#!/usr/bin/env python3
"""
Send a manual FCM push notification and log it to push_history.csv.

Required env vars:
    FIREBASE_SERVICE_ACCOUNT  — Firebase service account JSON string

Optional env vars:
    PUSH_TITLE               — Notification title (required at runtime)
    PUSH_BODY                — Notification body (required at runtime)
    PUSH_TARGET              — FCM topic or token (default: topic:all_users)
    PUSH_INTERRUPTION_LEVEL  — time-sensitive | active | passive (default: active)
    PUSH_ARTICLE_ID          — Optional article ID for deep-link
    PUSH_NOTES               — Optional notes/context for the log

The script mirrors the APNs payload structure from launch_monitor.py:
  - passive → apns-priority 5, no sound, relevance 0.25
  - active  → apns-priority 10, sound "default", relevance 0.5
  - time-sensitive → apns-priority 10, sound "default", relevance 1.0
  - NO mutable-content (no NSE in the app, see PR #11)

Appends a row to push_history.csv in the repo root (creates with header if missing).
Stats columns (opens, unique_users, foreground, toggled) start blank and are filled
by refresh_push_stats.py.
"""

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FCM_URL_TEMPLATE = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "push_history.csv"
OSLO_TZ = ZoneInfo("Europe/Oslo")

CSV_COLUMNS = [
    "timestamp_utc",
    "timestamp_cest",
    "target",
    "interruption_level",
    "title",
    "body",
    "article_id",
    "message_id",
    "api",
    "opens",
    "unique_users",
    "foreground",
    "toggled",
    "notes",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("send_manual_push")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def get_fcm_credentials() -> tuple[str, str]:
    """Load Firebase service account from FIREBASE_SERVICE_ACCOUNT env var.
    Returns (access_token, project_id).
    """
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


# ---------------------------------------------------------------------------
# FCM send
# ---------------------------------------------------------------------------


def build_apns_payload(
    title: str,
    body: str,
    interruption_level: str,
    article_id: str | None,
) -> dict:
    """Build the APNs-specific payload, mirroring launch_monitor.py's send_fcm.

    Key design decisions (see launch_monitor.py comments and PR #11):
    - passive  → priority 5, no sound, relevance 0.25, 24h expiration
    - active   → priority 10, sound "default", relevance 0.5, 1h expiration
    - time-sensitive → priority 10, sound "default", relevance 1.0, 1h expiration
    - NO mutable-content (app has no NSE; setting it delays delivery on iOS)
    """
    sound = None if interruption_level == "passive" else "default"
    relevance = {
        "passive": 0.25,
        "active": 0.5,
        "time-sensitive": 1.0,
    }.get(interruption_level, 0.5)
    apns_priority = "5" if interruption_level == "passive" else "10"
    apns_expiration = str(
        int(time.time()) + (24 * 3600 if interruption_level == "passive" else 3600)
    )

    aps: dict = {
        "alert": {
            "title": title,
            "body": body,
        },
        "badge": 1,
        "interruption-level": interruption_level,
        "relevance-score": relevance,
    }
    if sound is not None:
        aps["sound"] = sound

    return {
        "headers": {
            "apns-push-type": "alert",
            "apns-priority": apns_priority,
            "apns-expiration": apns_expiration,
        },
        "payload": {"aps": aps},
    }


def build_fcm_message(
    target: str,
    title: str,
    body: str,
    interruption_level: str,
    article_id: str | None,
    sent_at: str,
) -> dict:
    """Build the full FCM v1 message payload."""
    data: dict[str, str] = {
        "event_type": "manual",
        "sent_at": sent_at,
    }
    if article_id:
        data["article_id"] = article_id

    message: dict = {
        "notification": {
            "title": title,
            "body": body,
        },
        "data": data,
        "apns": build_apns_payload(title, body, interruption_level, article_id),
        "android": {
            "notification": {
                "channel_id": "launches",
                "notification_priority": "PRIORITY_HIGH",
            },
        },
    }

    # target can be a topic (default) or a registration token
    if target.startswith("token:"):
        message["token"] = target[len("token:"):]
    else:
        # Strip optional "topic:" prefix for clarity
        topic = target.removeprefix("topic:")
        message["topic"] = topic

    return {"message": message}


def send_fcm(
    access_token: str,
    project_id: str,
    target: str,
    title: str,
    body: str,
    interruption_level: str,
    article_id: str | None,
) -> str:
    """Send FCM notification. Returns message_id on success, exits on failure."""
    sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = build_fcm_message(target, title, body, interruption_level, article_id, sent_at)

    log.info("FCM payload:\n%s", json.dumps(payload, ensure_ascii=False, indent=2))

    url = FCM_URL_TEMPLATE.format(project_id=project_id)
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )

    if resp.ok:
        message_id = resp.json().get("name", "")
        log.info("FCM sent OK: status=%d message_id=%s", resp.status_code, message_id)
        return message_id
    else:
        log.error("FCM FAILED: status=%d body=%s", resp.status_code, resp.text)
        sys.exit(1)


# ---------------------------------------------------------------------------
# CSV logging
# ---------------------------------------------------------------------------


def ensure_csv_header() -> None:
    """Create push_history.csv with header row if it doesn't exist."""
    if not CSV_PATH.exists():
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
        log.info("Created %s with header", CSV_PATH)


def append_push_row(
    *,
    timestamp_utc: str,
    timestamp_cest: str,
    target: str,
    interruption_level: str,
    title: str,
    body: str,
    article_id: str,
    message_id: str,
    notes: str,
) -> None:
    """Append a new push record to push_history.csv."""
    ensure_csv_header()
    row = {
        "timestamp_utc": timestamp_utc,
        "timestamp_cest": timestamp_cest,
        "target": target,
        "interruption_level": interruption_level,
        "title": title,
        "body": body,
        "article_id": article_id,
        "message_id": message_id,
        "api": "fcm_v1",
        "opens": "",
        "unique_users": "",
        "foreground": "",
        "toggled": "",
        "notes": notes,
    }
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writerow(row)
    log.info("Appended row to %s", CSV_PATH)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    title = os.environ.get("PUSH_TITLE", "").strip()
    body = os.environ.get("PUSH_BODY", "").strip()
    target = os.environ.get("PUSH_TARGET", "all_users").strip() or "all_users"
    interruption_level = (
        os.environ.get("PUSH_INTERRUPTION_LEVEL", "active").strip().lower() or "active"
    )
    article_id = os.environ.get("PUSH_ARTICLE_ID", "").strip()
    notes = os.environ.get("PUSH_NOTES", "").strip()

    if not title:
        log.error("PUSH_TITLE is required.")
        sys.exit(1)
    if not body:
        log.error("PUSH_BODY is required.")
        sys.exit(1)
    if interruption_level not in ("time-sensitive", "active", "passive"):
        log.error(
            "PUSH_INTERRUPTION_LEVEL must be one of: time-sensitive, active, passive. Got: %r",
            interruption_level,
        )
        sys.exit(1)

    access_token, project_id = get_fcm_credentials()

    message_id = send_fcm(
        access_token=access_token,
        project_id=project_id,
        target=target,
        title=title,
        body=body,
        interruption_level=interruption_level,
        article_id=article_id or None,
    )

    now_utc = datetime.now(timezone.utc)
    timestamp_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    timestamp_cest = now_utc.astimezone(OSLO_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    append_push_row(
        timestamp_utc=timestamp_utc,
        timestamp_cest=timestamp_cest,
        target=target,
        interruption_level=interruption_level,
        title=title,
        body=body,
        article_id=article_id,
        message_id=message_id,
        notes=notes,
    )

    print(f'Push sent ✓ message_id={message_id} — Title: {title}')


if __name__ == "__main__":
    main()
