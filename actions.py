import logging
import requests

logger = logging.getLogger(__name__)


class GmailActions:
    def __init__(self, service, account_name: str):
        self.service = service
        self.account = account_name
        self._label_cache: dict[str, str] = {}

    def _get_or_create_label(self, name: str) -> str:
        if name in self._label_cache:
            return self._label_cache[name]

        labels = self.service.users().labels().list(userId="me").execute()
        for lbl in labels.get("labels", []):
            if lbl["name"].lower() == name.lower():
                self._label_cache[name] = lbl["id"]
                return lbl["id"]

        new_label = (
            self.service.users()
            .labels()
            .create(userId="me", body={"name": name, "labelListVisibility": "labelShow"})
            .execute()
        )
        self._label_cache[name] = new_label["id"]
        logger.info("Created label '%s'", name)
        return new_label["id"]

    def apply_label(self, gmail_id: str, label_name: str):
        label_id = self._get_or_create_label(label_name)
        self.service.users().messages().modify(
            userId="me",
            id=gmail_id,
            body={"addLabelIds": [label_id]},
        ).execute()
        logger.debug("Label '%s' applied to %s", label_name, gmail_id)

    def mark_read(self, gmail_id: str):
        self.service.users().messages().modify(
            userId="me",
            id=gmail_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()

    def archive(self, gmail_id: str):
        self.service.users().messages().modify(
            userId="me",
            id=gmail_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()

    def execute_action(self, gmail_id: str, action: dict):
        if label := action.get("label"):
            self.apply_label(gmail_id, label)
        if action.get("mark_read"):
            self.mark_read(gmail_id)
        if action.get("skip_inbox"):
            self.archive(gmail_id)


class Notifier:
    def __init__(self, config: dict):
        self.discord_cfg = config.get("notify", {}).get("discord", {})
        self.gchat_cfg = config.get("notify", {}).get("google_chat", {})

    def send(self, title: str, body: str, account: str = ""):
        prefix = f"[{account}] " if account else ""
        text = f"{prefix}**{title}**\n{body}"

        if self.discord_cfg.get("enabled") and self.discord_cfg.get("webhook_url"):
            self._discord(text)

        if self.gchat_cfg.get("enabled") and self.gchat_cfg.get("webhook_url"):
            self._google_chat(f"{prefix}{title}\n{body}")

    def _discord(self, text: str):
        try:
            resp = requests.post(
                self.discord_cfg["webhook_url"],
                json={"content": text[:2000]},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Discord notify failed: %s", e)

    def _google_chat(self, text: str):
        try:
            resp = requests.post(
                self.gchat_cfg["webhook_url"],
                json={"text": text[:4096]},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Google Chat notify failed: %s", e)
