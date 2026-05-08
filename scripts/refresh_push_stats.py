#!/usr/bin/env python3
"""
Refresh GA4 open-rate stats for recent manual push notifications.

Reads push_history.csv, queries the GA4 Data API for notification events
in a 3-day window after each push, and writes the stats back into the CSV.

Usage:
    python scripts/refresh_push_stats.py [--days N]

Options:
    --days N   Refresh rows where the push date is within the last N days
               (default: 14). Rows older than N days are left unchanged.

Authentication:
    The script first tries FIREBASE_SERVICE_ACCOUNT (same JSON used for FCM).
    If that service account has NOT been added to GA4 Property Access Management,
    the query will fail with a 403. In that case, set GA4_SERVICE_ACCOUNT to a
    separate JSON string for a service account that does have GA4 Viewer access.

    See README.md § "GA4 service account access" for setup instructions.

GA4 Property:
    Property ID: 534379147
    (configured via GA4_PROPERTY_ID env var or the hardcoded constant below)

Events tracked (mapped from GA4 eventName):
    notification_open       → opens
    notification_foreground → foreground
    notification_toggled    → toggled
    (totalUsers from notification_open → unique_users)
"""

import argparse
import csv
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("refresh_push_stats")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "push_history.csv"
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "534379147")

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

# ---------------------------------------------------------------------------
# GA4 credential loader
# ---------------------------------------------------------------------------


def get_ga4_credentials():
    """Return google.oauth2.service_account.Credentials for GA4 Data API.

    Tries GA4_SERVICE_ACCOUNT first (dedicated GA4 account), then falls back
    to FIREBASE_SERVICE_ACCOUNT. Both must be JSON strings.
    """
    from google.oauth2 import service_account

    scopes = ["https://www.googleapis.com/auth/analytics.readonly"]

    for env_var in ("GA4_SERVICE_ACCOUNT", "FIREBASE_SERVICE_ACCOUNT"):
        sa_json = os.environ.get(env_var)
        if sa_json:
            log.info("Using %s for GA4 authentication", env_var)
            sa_info = json.loads(sa_json)
            log.info(
                "Service account email: %s", sa_info.get("client_email", "unknown")
            )
            return service_account.Credentials.from_service_account_info(
                sa_info, scopes=scopes
            )

    log.error(
        "No GA4 credentials found. Set GA4_SERVICE_ACCOUNT or FIREBASE_SERVICE_ACCOUNT."
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# GA4 query
# ---------------------------------------------------------------------------


def query_ga4_notification_events(
    property_id: str,
    start_date: str,
    end_date: str,
    credentials,
) -> dict[str, dict]:
    """Query GA4 for notification events in a date range.

    Returns a dict mapping event names to {"event_count": int, "total_users": int}.

    Args:
        property_id: GA4 property ID (numeric string, e.g. "534379147")
        start_date:  ISO date string, e.g. "2024-05-01"
        end_date:    ISO date string, e.g. "2024-05-04"
        credentials: google.oauth2 Credentials object
    """
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            DimensionFilter,
            Filter,
            FilterExpression,
            Metric,
            RunReportRequest,
        )
    except ImportError:
        log.error(
            "google-analytics-data library not installed. "
            "Run: pip install google-analytics-data"
        )
        sys.exit(1)

    client = BetaAnalyticsDataClient(credentials=credentials)

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="eventName")],
        metrics=[
            Metric(name="eventCount"),
            Metric(name="totalUsers"),
        ],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="eventName",
                string_filter=Filter.StringFilter(
                    match_type=Filter.StringFilter.MatchType.CONTAINS,
                    value="notification",
                ),
            )
        ),
    )

    log.info(
        "Querying GA4 property %s for %s → %s", property_id, start_date, end_date
    )

    try:
        response = client.run_report(request)
    except Exception as exc:
        log.error("GA4 query failed: %s", exc)
        log.error(
            "If this is a 403, the service account has not been granted access to "
            "GA4 property %s. See README.md § 'GA4 service account access'.",
            property_id,
        )
        raise

    results: dict[str, dict] = {}
    for row in response.rows:
        event_name = row.dimension_values[0].value
        event_count = int(row.metric_values[0].value)
        total_users = int(row.metric_values[1].value)
        results[event_name] = {
            "event_count": event_count,
            "total_users": total_users,
        }
        log.info("  %s: count=%d users=%d", event_name, event_count, total_users)

    if not results:
        log.info("  No notification events found for this date range.")

    return results


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def parse_push_date(timestamp_utc: str) -> datetime | None:
    """Parse timestamp_utc field to a timezone-aware UTC datetime."""
    if not timestamp_utc:
        return None
    try:
        return datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def date_range_for_push(push_dt: datetime) -> tuple[str, str]:
    """Return (start_date, end_date) as 'YYYY-MM-DD' strings for a 3-day window."""
    start = push_dt.date()
    end = start + timedelta(days=3)
    return start.isoformat(), end.isoformat()


def write_csv_atomic(rows: list[dict]) -> None:
    """Write updated rows back to push_history.csv atomically via a temp file."""
    tmp_path = CSV_PATH.with_suffix(".csv.tmp")
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(CSV_PATH)
    log.info("Wrote %d rows to %s", len(rows), CSV_PATH)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh GA4 notification stats for recent manual pushes"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Refresh rows where push date is within the last N days (default: 14)",
    )
    args = parser.parse_args()

    if not CSV_PATH.exists():
        log.warning("%s does not exist. Nothing to refresh.", CSV_PATH)
        return

    # Load CSV
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        log.info("push_history.csv is empty. Nothing to refresh.")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    credentials = get_ga4_credentials()

    refreshed = 0
    total_opens = 0
    total_unique = 0
    errors = 0

    for row in rows:
        push_dt = parse_push_date(row.get("timestamp_utc", ""))
        if push_dt is None:
            log.warning("Cannot parse timestamp_utc=%r, skipping.", row.get("timestamp_utc"))
            continue

        if push_dt < cutoff:
            log.debug("Row %s is older than %d days, skipping.", row.get("timestamp_utc"), args.days)
            continue

        start_date, end_date = date_range_for_push(push_dt)

        log.info(
            "Refreshing push '%s' sent at %s (window: %s → %s)",
            row.get("title", ""),
            row.get("timestamp_utc", ""),
            start_date,
            end_date,
        )

        try:
            events = query_ga4_notification_events(
                property_id=GA4_PROPERTY_ID,
                start_date=start_date,
                end_date=end_date,
                credentials=credentials,
            )
        except Exception:
            log.error("Failed to query GA4 for push '%s'. Leaving stats unchanged.", row.get("title", ""))
            errors += 1
            continue

        # Map event names to CSV columns
        open_data = events.get("notification_open", {})
        opens = open_data.get("event_count", 0)
        unique_users = open_data.get("total_users", 0)
        foreground = events.get("notification_foreground", {}).get("event_count", 0)
        toggled = events.get("notification_toggled", {}).get("event_count", 0)

        row["opens"] = str(opens) if opens else ""
        row["unique_users"] = str(unique_users) if unique_users else ""
        row["foreground"] = str(foreground) if foreground else ""
        row["toggled"] = str(toggled) if toggled else ""

        refreshed += 1
        total_opens += opens
        total_unique += unique_users

    if refreshed == 0 and errors == 0:
        log.info("No rows needed refreshing within the last %d days.", args.days)
        return

    # Write back atomically
    write_csv_atomic(rows)

    avg_opens = round(total_opens / refreshed) if refreshed else 0
    avg_unique = round(total_unique / refreshed) if refreshed else 0

    summary = (
        f"Refreshed {refreshed} push{'es' if refreshed != 1 else ''}"
        f" — avg {avg_opens} opens, {avg_unique} unique users"
    )
    if errors:
        summary += f" ({errors} error{'s' if errors != 1 else ''}, check logs)"
    print(summary)


if __name__ == "__main__":
    main()
