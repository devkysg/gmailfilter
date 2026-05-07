import hashlib
import json
import logging
import os
from typing import Optional

import anthropic

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


class AIJudge:
    def __init__(self, config: dict, redis_client=None):
        self.config = config.get("ai", {})
        self.model = self.config.get("model", "claude-haiku-4-5-20251001")
        self.max_tokens = self.config.get("max_tokens", 256)
        self.redis = redis_client
        self.ttl = self.config.get("cache_ttl_days", 7) * 86400
        self.key_prefix = config.get("redis", {}).get("key_prefix", "gmail:")
        self.client = anthropic.Anthropic()

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

    def judge(self, msg) -> dict:
        cached = self._get_cache(msg.message_id)
        if cached:
            return cached

        user_content = f"""件名: {msg.subject}
送信者: {msg.sender}
本文（抜粋）: {msg.snippet[:500]}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # プロンプトキャッシュ
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )

        raw = response.content[0].text.strip()
        # コードブロック(```json ... ```)を除去
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("AI returned non-JSON: %s", raw)
            result = {
                "importance": "medium",
                "category": "other",
                "summary": msg.snippet[:30],
                "notify": False,
                "reason": "判定失敗",
            }

        logger.info(
            "AI judge: [%s] %s -> importance=%s notify=%s",
            msg.gmail_id,
            msg.subject[:40],
            result.get("importance"),
            result.get("notify"),
        )
        self._set_cache(msg.message_id, result)
        return result
