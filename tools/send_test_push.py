#!/usr/bin/env python3
"""
Send a single test FCM push notification to the 'all' topic.

Uses the same message builder as launch_monitor.py so you can verify
that pushes display on the iOS lock screen without triggering a full
launch-monitor run.

Usage:
    # Requires FIREBASE_SERVICE_ACCOUNT env var (JSON service account key)
    export FIREBASE_SERVICE_ACCOUNT='{"project_id": "...", ...}'
    python tools/send_test_push.py

    # Custom title/body:
    python tools/send_test_push.py --title "Test" --body "Hei fra serveren"

    # Dry run (prints payload, does not send):
    python tools/send_test_push.py --dry-run
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest

FCM_URL_TEMPLATE = (
    "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("test_push")


def get_fcm_credentials() -> tuple[str, str]:
    """Load Firebase service-account credentials from env."""
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not sa_json:
        log.error("FIREBASE_SERVICE_ACCOUNT env var is not set.")
        sys.exit(1)

    from google.oauth2 import service_account

    sa_info = json.loads(sa_json)
    project_id = sa_info["project_id"]

    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/firebase.messaging"],
    )
    credentials.refresh(GoogleAuthRequest())
    return credentials.token, project_id


def build_message(title: str, body: str, topic: str = "all") -> dict:
    """Build an FCM message dict with full APNs alert payload.

    This mirrors the production builder in launch_monitor.py. Any change
    there should be reflected here so test pushes are representative.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "message": {
            "topic": topic,
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {
                "event_type": "test",
                "sent_at": now_str,
            },
            "apns": {
                "headers": {
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                },
                "payload": {
                    "aps": {
                        "alert": {
                            "title": title,
                            "body": body,
                        },
                        "sound": "default",
                        "badge": 1,
                        "mutable-content": 1,
                    },
                },
            },
            "android": {
                "notification": {
                    "channel_id": "launches",
                    "priority": "HIGH",
                },
            },
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a test FCM push notification to the 'all' topic",
    )
    parser.add_argument(
        "--title",
        default="Testmelding",
        help="Notification title (default: 'Testmelding')",
    )
    parser.add_argument(
        "--body",
        default="Hvis du ser dette på låst skjerm, fungerer push-varsler riktig!",
        help="Notification body",
    )
    parser.add_argument(
        "--topic",
        default="all",
        help="FCM topic to send to (default: 'all')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the payload without sending",
    )
    args = parser.parse_args()

    message = build_message(args.title, args.body, args.topic)
    log.info("Message payload:\n%s", json.dumps(message, ensure_ascii=False, indent=2))

    if args.dry_run:
        log.info("[DRY RUN] Not sending. Payload printed above.")
        return

    access_token, project_id = get_fcm_credentials()
    url = FCM_URL_TEMPLATE.format(project_id=project_id)

    log.info("Sending to %s (topic=%r)", url, args.topic)
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=message,
        timeout=15,
    )

    if resp.ok:
        log.info("SUCCESS: status=%d response=%s", resp.status_code, resp.text)
    else:
        log.error("FAILED: status=%d body=%s", resp.status_code, resp.text)
        sys.exit(1)


if __name__ == "__main__":
    main()
