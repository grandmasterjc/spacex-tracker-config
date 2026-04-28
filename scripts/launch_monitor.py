#!/usr/bin/env python3
"""
Launch Monitor for SpaceX Tracker.

Polls Launch Library 2 API, detects reschedules and outcomes for SpaceX launches,
and sends FCM push notifications (in Norwegian) to the `all` topic.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LL2_UPCOMING_URL = (
    "https://ll.thespacedevs.com/2.2.0/launch/upcoming/"
    "?limit=20&hide_recent_previous=false&mode=detailed"
)
LL2_PREVIOUS_URL = (
    "https://ll.thespacedevs.com/2.2.0/launch/previous/?limit=10&mode=detailed"
)
FCM_URL_TEMPLATE = (
    "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
)
STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "launch_state.json"
OSLO_TZ = ZoneInfo("Europe/Oslo")

RESCHEDULE_THRESHOLD_SECONDS = 5 * 60  # 5 minutes
UPCOMING_WINDOW_HOURS = 48
PRUNE_DAYS = 30

# Outcome status IDs
OUTCOME_STATUSES = {3: "Success", 4: "Failure", 7: "Partial Failure"}

# Norwegian outcome labels
OUTCOME_TITLES = {
    3: "Suksess",
    4: "Feil",
    7: "Delvis vellykket",
}

# Norwegian outcome body templates (format with launch_name)
OUTCOME_BODIES = {
    3: "{launch_name} ble vellykket skutt opp",
    4: "{launch_name}: oppskytning mislyktes",
    7: "{launch_name}: delvis vellykket oppskytning",
}

# Notification flood cap — configurable via env var
MAX_NOTIFICATIONS_PER_RUN = int(os.environ.get("MAX_NOTIFICATIONS_PER_RUN", "5"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("launch_monitor")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_iso(dt_str: str) -> datetime:
    """Parse an ISO-8601 string to a timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


def format_norwegian_time(dt: datetime) -> str:
    """Format datetime as '27. apr kl 18:30' in Europe/Oslo timezone."""
    oslo_dt = dt.astimezone(OSLO_TZ)
    months_no = [
        "jan", "feb", "mar", "apr", "mai", "jun",
        "jul", "aug", "sep", "okt", "nov", "des",
    ]
    month_str = months_no[oslo_dt.month - 1]
    return f"{oslo_dt.day}. {month_str} kl {oslo_dt.strftime('%H:%M')}"


def load_state() -> dict:
    """Load the current launch state from disk."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"launches": {}, "updated_at": None}


def save_state(state: dict) -> None:
    """Save launch state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def prune_state(state: dict) -> None:
    """Remove launches older than PRUNE_DAYS from state."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)
    to_remove = []
    for lid, data in state["launches"].items():
        try:
            net = parse_iso(data["net"])
            if net < cutoff:
                to_remove.append(lid)
        except (KeyError, ValueError):
            to_remove.append(lid)
    for lid in to_remove:
        del state["launches"][lid]
        log.info("Pruned old launch %s from state", lid)


# ---------------------------------------------------------------------------
# LL2 API
# ---------------------------------------------------------------------------


def fetch_ll2(url: str) -> list[dict]:
    """Fetch launches from LL2 API, filtering for SpaceX only."""
    log.info("Fetching %s", url)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    spacex = [
        l for l in results
        if l.get("launch_service_provider", {}).get("name") == "SpaceX"
    ]
    log.info("Got %d SpaceX launches (of %d total)", len(spacex), len(results))
    return spacex


# ---------------------------------------------------------------------------
# FCM
# ---------------------------------------------------------------------------


def get_fcm_credentials() -> tuple[str, str]:
    """
    Load Firebase service account from FIREBASE_SERVICE_ACCOUNT env var,
    return (access_token, project_id).
    """
    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not sa_json:
        log.error("FIREBASE_SERVICE_ACCOUNT secret is not set.")
        sys.exit(1)

    from google.oauth2 import service_account

    sa_info = json.loads(sa_json)
    project_id = sa_info["project_id"]

    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/firebase.messaging"],
    )
    credentials.refresh(GoogleAuthRequest())
    access_token = credentials.token
    return access_token, project_id


def send_fcm(
    access_token: str,
    project_id: str,
    title: str,
    body: str,
    data: dict[str, str],
    dry_run: bool = False,
) -> bool:
    """Send an FCM push notification to the `all` topic. Returns True on success."""
    message = {
        "message": {
            "topic": "all",
            "notification": {
                "title": title,
                "body": body,
            },
            "data": data,
            "apns": {
                "headers": {
                    "apns-priority": "10",
                    "apns-push-type": "alert",
                },
            },
        }
    }

    if dry_run:
        log.info("[DRY RUN] Would send FCM: %s", json.dumps(message, ensure_ascii=False, indent=2))
        return True

    url = FCM_URL_TEMPLATE.format(project_id=project_id)
    log.info("FCM URL: %s", url)
    log.info("FCM request body: %s", json.dumps(message, ensure_ascii=False))
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
        log.info("FCM sent successfully: %s – %s", title, body)
        log.info("FCM response: status=%d body=%s", resp.status_code, resp.text)
        return True
    else:
        log.error("FCM failed status=%d body=%s headers=%s", resp.status_code, resp.text, dict(resp.headers))
        return False
    if False:
        log.error("FCM failed (%d): %s", resp.status_code, resp.text)
        return False


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------


def detect_changes(
    upcoming: list[dict],
    previous: list[dict],
    state: dict,
) -> list[dict]:
    """
    Compare fetched launches against state, return list of change events.
    Each event: {type, launch_id, launch_name, ...}
    """
    now = datetime.now(timezone.utc)
    events: list[dict] = []

    # --- Reschedule detection (upcoming launches within ±48h) ---
    for launch in upcoming:
        lid = launch.get("id", "")
        name = launch.get("name", "Unknown")
        net_str = launch.get("net")
        status_id = launch.get("status", {}).get("id")
        status_name = launch.get("status", {}).get("name", "")

        if not net_str or not lid:
            continue

        try:
            net = parse_iso(net_str)
        except ValueError:
            continue

        # Only consider launches within ±48h for reschedule
        if abs((net - now).total_seconds()) > UPCOMING_WINDOW_HOURS * 3600:
            continue

        prev = state["launches"].get(lid)
        if prev and prev.get("last_notified_net"):
            try:
                last_net = parse_iso(prev["last_notified_net"])
            except ValueError:
                last_net = None

            if last_net:
                delta = abs((net - last_net).total_seconds())
                if delta >= RESCHEDULE_THRESHOLD_SECONDS:
                    events.append({
                        "type": "reschedule",
                        "launch_id": lid,
                        "launch_name": name,
                        "new_net": net_str,
                        "new_net_formatted": format_norwegian_time(net),
                        "status_id": status_id,
                        "status_name": status_name,
                    })

        # Update state for this launch
        state["launches"][lid] = {
            "net": net_str,
            "status_id": status_id,
            "status_name": status_name,
            "name": name,
            "last_notified_net": state["launches"].get(lid, {}).get("last_notified_net", net_str),
            "last_notified_status_id": state["launches"].get(lid, {}).get("last_notified_status_id", status_id),
        }

    # --- Outcome detection (upcoming + previous) ---
    all_launches = upcoming + previous
    for launch in all_launches:
        lid = launch.get("id", "")
        name = launch.get("name", "Unknown")
        net_str = launch.get("net")
        status_id = launch.get("status", {}).get("id")
        status_name = launch.get("status", {}).get("name", "")

        if not lid:
            continue

        if status_id not in OUTCOME_STATUSES:
            continue

        prev = state["launches"].get(lid)
        last_status = prev.get("last_notified_status_id") if prev else None

        if last_status != status_id:
            events.append({
                "type": "outcome",
                "launch_id": lid,
                "launch_name": name,
                "status_id": status_id,
                "status_name": status_name,
                "outcome": OUTCOME_STATUSES[status_id],
                "net": net_str,
            })

        # Update/insert state
        state["launches"][lid] = {
            "net": net_str or (prev["net"] if prev else ""),
            "status_id": status_id,
            "status_name": status_name,
            "name": name,
            "last_notified_net": net_str or (prev.get("last_notified_net", "") if prev else ""),
            "last_notified_status_id": state["launches"].get(lid, {}).get("last_notified_status_id", status_id),
        }

    return events


def update_notified(state: dict, events: list[dict]) -> None:
    """After successful notifications, update last_notified fields."""
    for event in events:
        lid = event["launch_id"]
        if lid not in state["launches"]:
            continue
        if event["type"] == "reschedule":
            state["launches"][lid]["last_notified_net"] = event["new_net"]
        elif event["type"] == "outcome":
            state["launches"][lid]["last_notified_status_id"] = event["status_id"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="SpaceX Launch Monitor")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be sent without actually sending or committing state",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    if dry_run:
        log.info("=== DRY RUN MODE ===")

    # Load credentials (skip in dry-run if secret not set)
    access_token = None
    project_id = None
    if not dry_run:
        access_token, project_id = get_fcm_credentials()
    else:
        sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if sa_json:
            sa_info = json.loads(sa_json)
            project_id = sa_info.get("project_id", "dry-run-project")
        else:
            log.info("[DRY RUN] No FIREBASE_SERVICE_ACCOUNT set, using placeholder project ID")
            project_id = "dry-run-project"

    # Load state
    state = load_state()
    is_bootstrap = (
        not state.get("launches")
        or not isinstance(state["launches"], dict)
        or len(state["launches"]) == 0
    )
    log.info("Loaded state with %d tracked launches", len(state.get("launches", {})))

    # Fetch from LL2
    try:
        upcoming = fetch_ll2(LL2_UPCOMING_URL)
        previous = fetch_ll2(LL2_PREVIOUS_URL)
    except requests.RequestException as e:
        log.error("LL2 API request failed: %s", e)
        sys.exit(1)

    # --- Bootstrap mode: populate state without sending notifications ---
    if is_bootstrap:
        state.setdefault("launches", {})
        all_launches = upcoming + previous
        for launch in all_launches:
            lid = launch.get("id", "")
            if not lid:
                continue
            name = launch.get("name", "Unknown")
            net_str = launch.get("net", "")
            status_id = launch.get("status", {}).get("id")
            status_name = launch.get("status", {}).get("name", "")
            state["launches"][lid] = {
                "net": net_str,
                "status_id": status_id,
                "status_name": status_name,
                "name": name,
                "last_notified_net": net_str,
                "last_notified_status_id": status_id,
            }
        n = len(state["launches"])
        log.info("[BOOTSTRAP] Initialized state with %d launches. No notifications sent.", n)
        if not dry_run:
            save_state(state)
            log.info("State saved to %s", STATE_FILE)
        else:
            log.info("[DRY RUN] [BOOTSTRAP] Would initialize state with %d launches. State NOT saved.", n)
        return

    # Detect changes
    events = detect_changes(upcoming, previous, state)
    log.info("Detected %d events", len(events))

    # --- Defensive cap: limit notifications per run ---
    if len(events) > MAX_NOTIFICATIONS_PER_RUN:
        log.warning(
            "Notification cap triggered: %d events exceed limit of %d. "
            "Sending only the 3 most recent.",
            len(events),
            MAX_NOTIFICATIONS_PER_RUN,
        )
        # Sort by last_updated proxy: prefer events with a net timestamp, most recent first
        def _sort_key(ev: dict) -> str:
            return ev.get("new_net") or ev.get("net") or ""
        events.sort(key=_sort_key, reverse=True)
        events = events[:3]

    # Send notifications
    sent_events: list[dict] = []
    for event in events:
        if event["type"] == "reschedule":
            title = "Oppskytning flyttet"
            body = f"{event['launch_name']} er flyttet til {event['new_net_formatted']}"
            data = {
                "event_type": "reschedule",
                "launch_id": event["launch_id"],
                "launch_name": event["launch_name"],
                "new_net": event["new_net"],
            }
        elif event["type"] == "outcome":
            title = OUTCOME_TITLES.get(event["status_id"], "Oppdatering")
            body_template = OUTCOME_BODIES.get(event["status_id"])
            if body_template:
                body = body_template.format(launch_name=event["launch_name"])
            else:
                body = f"{event['launch_name']}: {title}"
            data = {
                "event_type": "outcome",
                "launch_id": event["launch_id"],
                "launch_name": event["launch_name"],
                "outcome": event["outcome"],
            }
        else:
            continue

        success = send_fcm(access_token, project_id, title, body, data, dry_run=dry_run)
        if success:
            sent_events.append(event)
        else:
            log.error("Failed to send notification for %s", event["launch_id"])

    # Update notified state
    update_notified(state, sent_events)

    # Prune old entries
    prune_state(state)

    # Save state
    if not dry_run:
        save_state(state)
        log.info("State saved to %s", STATE_FILE)
    else:
        log.info("[DRY RUN] State NOT saved. Would have %d launches tracked.", len(state["launches"]))

    log.info("Done. %d notifications sent.", len(sent_events))


if __name__ == "__main__":
    main()
