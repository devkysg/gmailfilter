import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたはメール分類AIです。受け取ったメール情報を分析し、以下のJSON形式のみで回答してください。

{
  "importance": "high" | "medium" | "low",
  "category": "business" | "personal" | "shopping" | "notification" | "spam" | "other",
  "summary": "メールの要約（日本語30文字以内）",
  "notify": true | false,
  "reason": "判定理由（日本語50文字以内）"
}

判定基準:
- importance=high: 返信や対応が必要な業務・重要連絡
- importance=medium: 情報として把握しておくべき内容
- importance=low: ニュースレター、通知、広告など
- notify=true: importance=high かつ即時対応が必要な場合のみ"""

FALLBACK_RESULT = {
    "importance": "medium",
    "category": "other",
    "notify": False,
    "reason": "判定失敗",
}


def _strip_code_block(text: str) -> str:
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


class AIJudge:
    def __init__(self, config: dict, redis_client=None):
        ai_cfg = config.get("ai", {})
        self.provider = ai_cfg.get("provider", "anthropic").lower()
        self.model = ai_cfg.get("model", "claude-haiku-4-5-20251001")
        self.max_tokens = ai_cfg.get("max_tokens", 256)
        self.redis = redis_client
        self.ttl = ai_cfg.get("cache_ttl_days", 7) * 86400
        self.key_prefix = config.get("redis", {}).get("key_prefix", "gmail:")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if self.provider == "openai":
                from openai import OpenAI
                self._client = OpenAI()
            else:
                import anthropic
                self._client = anthropic.Anthropic()
        return self._client

    def _cache_key(self, message_id: str) -> str:
        h = hashlib.sha256(message_id.encode()).hexdigest()[:16]
        return f"{self.key_prefix}ai:{h}"

    def _get_cache(self, message_id: str) -> Optional[dict]:
        if not self.redis:
            return None
        try:
            val = self.redis.get(self._cache_key(message_id))
            if val:
                logger.debug("Cache hit for %s", message_id)
                return json.loads(val)
        except Exception as e:
            logger.warning("Redis get error: %s", e)
        return None

    def _set_cache(self, message_id: str, result: dict):
        if not self.redis:
            return
        try:
            self.redis.setex(self._cache_key(message_id), self.ttl, json.dumps(result))
        except Exception as e:
            logger.warning("Redis set error: %s", e)

    def _call_anthropic(self, user_content: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
        return response.content[0].text.strip()

    def _call_openai(self, user_content: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
        )
        return response.choices[0].message.content.strip()

    def judge(self, msg) -> dict:
        cached = self._get_cache(msg.message_id)
        if cached:
            return cached

        user_content = f"件名: {msg.subject}\n送信者: {msg.sender}\n本文（抜粋）: {msg.snippet[:500]}"

        try:
            if self.provider == "openai":
                raw = self._call_openai(user_content)
            else:
                raw = self._call_anthropic(user_content)
        except Exception as e:
            logger.error("AI API error (%s): %s", self.provider, e)
            return {**FALLBACK_RESULT, "summary": msg.snippet[:30]}

        raw = _strip_code_block(raw)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("AI returned non-JSON: %s", raw)
            result = {**FALLBACK_RESULT, "summary": msg.snippet[:30]}

        logger.info(
            "AI judge [%s]: [%s] %s -> importance=%s notify=%s",
            self.provider,
            msg.gmail_id,
            msg.subject[:40],
            result.get("importance"),
            result.get("notify"),
        )
        self._set_cache(msg.message_id, result)
        return result
