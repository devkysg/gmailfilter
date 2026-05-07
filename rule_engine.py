import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MailMessage:
    message_id: str
    gmail_id: str
    subject: str
    sender: str
    snippet: str
    body_text: str = ""
    has_list_unsubscribe: bool = False
    label_ids: list = field(default_factory=list)


@dataclass
class RuleMatch:
    rule_name: str
    action: dict
    skip_ai: bool = False


def _match_from(sender: str, patterns: list[str]) -> bool:
    sender_lower = sender.lower()
    return any(p.lower() in sender_lower for p in patterns)


def _match_subject(subject: str, keywords: list[str]) -> bool:
    subject_lower = subject.lower()
    return any(k.lower() in subject_lower for k in keywords)


def _has_active_conditions(match: dict) -> bool:
    """少なくとも1つの有効な条件があるか確認（空リストは無効）"""
    if match.get("from"):
        return True
    if match.get("subject_contains"):
        return True
    if match.get("header_list_unsubscribe"):
        return True
    return False


def evaluate(msg: MailMessage, rules: list[dict]) -> Optional[RuleMatch]:
    for rule in rules:
        match = rule.get("match", {})

        # 有効な条件が一つもないルールはスキップ
        if not _has_active_conditions(match):
            continue

        if match.get("from") and not _match_from(msg.sender, match["from"]):
            continue

        if match.get("subject_contains") and not _match_subject(msg.subject, match["subject_contains"]):
            continue

        if match.get("header_list_unsubscribe") and not msg.has_list_unsubscribe:
            continue

        logger.debug("Rule matched: %s for message %s", rule["name"], msg.gmail_id)
        return RuleMatch(
            rule_name=rule["name"],
            action=rule.get("action", {}),
            skip_ai=rule.get("skip_ai", False),
        )

    return None


def dry_run(msg: MailMessage, rules: list[dict]):
    result = evaluate(msg, rules)
    if result:
        print(f"MATCH: [{result.rule_name}] -> {result.action}")
    else:
        print(f"NO MATCH -> will go to AI judge")
    return result


if __name__ == "__main__":
    import yaml
    import sys
    from pathlib import Path

    config = yaml.safe_load(Path("config.yaml").read_text())
    sample = MailMessage(
        message_id="<test@example.com>",
        gmail_id="test123",
        subject=sys.argv[1] if len(sys.argv) > 1 else "テストメール",
        sender=sys.argv[2] if len(sys.argv) > 2 else "test@example.com",
        snippet="テスト本文",
    )
    dry_run(sample, config["rules"])
