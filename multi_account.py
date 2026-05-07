import os
import json
import logging
from pathlib import Path

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
]

logger = logging.getLogger(__name__)


def get_gmail_service(account: dict, base_dir: Path):
    cred_path = base_dir / account["credentials"]
    token_path = base_dir / account["token"]

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            logger.info("Token refreshed for %s", account["name"])
        else:
            if not cred_path.exists():
                raise FileNotFoundError(
                    f"credentials not found: {cred_path}\n"
                    f"Download from Google Cloud Console and save as {cred_path}"
                )
            flow = Flow.from_client_secrets_file(
                str(cred_path), scopes=SCOPES, redirect_uri="http://localhost"
            )
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
            print(f"\n以下のURLをブラウザで開いてください:\n\n{auth_url}\n")
            print("許可後、ブラウザのアドレスバーに表示された URL を丸ごとコピーして貼り付けてください")
            print("(http://localhost/?code=... で始まるURL)")
            redirect_response = input("URL: ").strip()
            flow.fetch_token(authorization_response=redirect_response)
            creds = flow.credentials
            logger.info("New token obtained for %s", account["name"])

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def test_auth(account: dict, base_dir: Path):
    service = get_gmail_service(account, base_dir)
    profile = service.users().getProfile(userId="me").execute()
    print(f"[{account['name']}] authenticated as: {profile['emailAddress']}")
    return service


if __name__ == "__main__":
    import sys
    import yaml

    logging.basicConfig(level=logging.INFO)
    config = yaml.safe_load(Path("config.yaml").read_text())
    base = Path(".")

    for acc in config["accounts"]:
        try:
            test_auth(acc, base)
        except Exception as e:
            print(f"[{acc['name']}] ERROR: {e}", file=sys.stderr)
