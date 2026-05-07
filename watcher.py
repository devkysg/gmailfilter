#!/usr/bin/env python3
"""Gmail filter watcher - 5-minute polling service."""

import base64
import email
import logging
import os
import signal
import sys
import time
from pathlib import Path

import yaml

from actions import GmailActions, Notifier
from ai_judge import AIJudge
from multi_account import get_gmail_service
from rule_engine import MailMessage, evaluate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/gmailfilter.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("watcher")

BASE_DIR = Path(__file__).parent
PROCESSED_IDS_FILE = BASE_DIR / "processed_ids.txt"


def load_config():
    return yaml.safe_load((BASE_DIR / "config.yaml").read_text())


def load_processed_ids() -> set:
    if PROCESSED_IDS_FILE.exists():
        return set(PROCESSED_IDS_FILE.read_text().splitlines())
    return set()


def save_processed_id(gmail_id: str, processed: set):
    processed.add(gmail_id)
    # ファイルが大きくなりすぎないよう末尾10000件のみ保持
    ids = list(processed)[-10000:]
    PROCESSED_IDS_FILE.write_text("\n".join(ids))


def extract_message(raw_msg: dict) -> MailMessage:
    payload = raw_msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    subject = headers.get("subject", "(件名なし)")
    sender = headers.get("from", "")
    msg_id = headers.get("message-id", raw_msg["id"])
    has_unsubscribe = "list-unsubscribe" in headers

    snippet = raw_msg.get("snippet", "")

    body_text = ""
    parts = payload.get("parts", [payload])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                body_text = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            break

    return MailMessage(
        message_id=msg_id,
        gmail_id=raw_msg["id"],
        subject=subject,
        sender=sender,
        snippet=snippet,
        body_text=body_text,
        has_list_unsubscribe=has_unsubscribe,
        label_ids=raw_msg.get("labelIds", []),
    )


def process_account(account: dict, config: dict, ai_judge: AIJudge, notifier: Notifier, processed: set):
    service = get_gmail_service(account, BASE_DIR)
    gmail_actions = GmailActions(service, account["name"])
    poll_cfg = config.get("polling", {})
    max_results = poll_cfg.get("max_results", 50)

    results = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX", "UNREAD"], maxResults=max_results)
        .execute()
    )

    messages = results.get("messages", [])
    if not messages:
        return

    for msg_ref in messages:
        gmail_id = msg_ref["id"]
        if gmail_id in processed:
            continue

        try:
            raw = service.users().messages().get(
                userId="me", id=gmail_id, format="full"
            ).execute()
            msg = extract_message(raw)

            rule_match = evaluate(msg, config["rules"])

            if rule_match:
                gmail_actions.execute_action(gmail_id, rule_match.action)
                if rule_match.action.get("notify"):
                    notifier.send(
                        title=f"[{rule_match.rule_name}] {msg.subject}",
                        body=f"From: {msg.sender}\n{msg.snippet[:200]}",
                        account=account["name"],
                    )
                logger.info("[%s] Rule '%s': %s", account["name"], rule_match.rule_name, msg.subject[:60])
            elif config.get("ai", {}).get("enabled"):
                result = ai_judge.judge(msg)
                action = {}
                if result.get("importance") == "low":
                    action = {"label": "filter/AI-low", "mark_read": True}
                elif result.get("importance") == "high":
                    action = {"label": "filter/AI-high"}
                    if result.get("notify") and config.get("ai", {}).get("notify_on_important"):
                        notifier.send(
                            title=f"[重要] {msg.subject}",
                            body=f"From: {msg.sender}\n{result.get('summary', msg.snippet[:100])}\n理由: {result.get('reason', '')}",
                            account=account["name"],
                        )
                if action:
                    gmail_actions.execute_action(gmail_id, action)

            save_processed_id(gmail_id, processed)

        except Exception as e:
            logger.error("[%s] Failed to process %s: %s", account["name"], gmail_id, e)


def run():
    logger.info("Gmail filter watcher starting...")
    config = load_config()
    processed = load_processed_ids()

    redis_client = None
    if config.get("redis", {}).get("enabled"):
        import redis as redis_lib
        redis_client = redis_lib.Redis(
            host=config["redis"].get("host", "localhost"),
            port=config["redis"].get("port", 6379),
            db=config["redis"].get("db", 1),
        )
        logger.info("Redis connected")

    ai_judge = AIJudge(config, redis_client)
    notifier = Notifier(config)
    interval = config.get("polling", {}).get("interval_seconds", 300)

    running = True

    def _stop(sig, frame):
        nonlocal running
        logger.info("Shutdown signal received")
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while running:
        for account in config["accounts"]:
            try:
                process_account(account, config, ai_judge, notifier, processed)
            except Exception as e:
                logger.error("Account %s error: %s", account["name"], e)

        if running:
            time.sleep(interval)

    logger.info("Gmail filter watcher stopped.")


if __name__ == "__main__":
    run()
