# SpaceX Tracker — Content Repo

## Launch Monitor

A GitHub Actions workflow (`.github/workflows/launch-monitor.yml`) runs every 10 minutes and monitors SpaceX launches for schedule changes and outcomes.

### How it works

1. Polls the [Launch Library 2](https://thespacedevs.com/llapi) API for upcoming and recent SpaceX launches
2. Compares against saved state in `state/launch_state.json`
3. Detects **reschedules** (NET moved by >= 5 minutes for launches within ±48h) and **outcomes** (Success / Failure / Partial Failure)
4. Sends FCM push notifications (in Norwegian) to the `all` topic
5. Commits updated state back to the repo

### Bootstrap mode (first run)

When `state/launch_state.json` is empty or has no launches, the script enters **bootstrap mode**:

- Fetches all current launches (upcoming + recent previous) from LL2
- Populates the state file with these launches as the baseline
- **Does NOT send any notifications** — avoids flooding users with stale outcomes
- Logs: `[BOOTSTRAP] Initialized state with N launches. No notifications sent.`
- Subsequent runs will diff against this baseline and only notify on real changes

Dry-run mode also shows bootstrap behavior (without committing state).

### Notification cap

As a safety measure, if more than `MAX_NOTIFICATIONS_PER_RUN` events (default: **5**) are detected in a single run, only the **3 most recent** (by launch NET) are sent. The rest are skipped and logged as a warning. This prevents accidental flooding if state gets corrupted or reset.

Override via environment variable:

```
MAX_NOTIFICATIONS_PER_RUN=10
```

### Notifications

All push notifications are in Norwegian (Bokmål) and sent to the FCM topic `all`.

| Event | Example title | Example body |
|-------|--------------|--------------|
| Reschedule | Oppskytning flyttet | Falcon 9 Block 5 \| Starlink Group 17-16 er flyttet til 27. apr kl 18:30 |
| Success | Suksess | Falcon 9 Block 5 \| Starlink Group 17-16 ble vellykket skutt opp |
| Failure | Feil | Falcon 9 Block 5 \| Starlink Group 17-16: oppskytning mislyktes |
| Partial Failure | Delvis vellykket | Falcon 9 Block 5 \| Starlink Group 17-16: delvis vellykket oppskytning |

### Secrets

| Secret | Description |
|--------|-------------|
| `FIREBASE_SERVICE_ACCOUNT` | Firebase service account JSON (must have `firebase.messaging` scope). The `project_id` field is used to target the correct FCM project. |

### Manual testing

Trigger the workflow manually via `workflow_dispatch` with dry-run mode:

```
gh workflow run launch-monitor.yml -f dry_run=true
```

In dry-run mode the script logs what notifications would be sent without actually calling FCM or committing state changes.

Content delivery for the SpaceX Tracker iOS app. Articles and images are served via GitHub Pages and fetched at runtime by the app.

## Structure

```
updates/
├── manifest.json          # Production manifest (live to all users)
├── manifest-draft.json    # Draft manifest (DEBUG preview mode only)
├── articles/              # Markdown article bodies
│   ├── welcome-to-mission-control.md
│   ├── starship-flight-test-overview.md
│   └── mission-control-premium-guide.md
└── images/                # Hero images (1200x675 JPEG)
    ├── hero_welcome.jpg
    ├── hero_starship.jpg
    └── hero_premium.jpg
```

## Publishing workflow

1. Edit or add an article in `updates/articles/`
2. Drop the hero image in `updates/images/` (1200x675 JPEG, ~85% quality)
3. Update `manifest-draft.json` first — verify it renders via DEBUG preview mode in the app
4. When ready, promote the entry to `manifest.json` and commit
5. Optional: send a FCM push with `article_id` in the data payload to deep-link

## Manifest entry fields

| Field        | Required | Notes                                        |
|--------------|----------|----------------------------------------------|
| `id`         | yes      | URL-safe; must match the `.md` filename      |
| `title`      | yes      |                                              |
| `subtitle`   | no       | Shown on the card                            |
| `date`       | yes      | ISO 8601 UTC                                 |
| `category`   | yes      | `all`, `starship`, `falcon9`, `dragon`, etc. |
| `premium`    | yes      | `true` = paywall teaser for free users       |
| `hero_image` | no       | Full URL to 1200x675 JPEG                    |
| `summary`    | yes      | ~2 sentences; shown on card + detail view    |
| `author`     | no       |                                              |

## Markdown extensions

- `@youtube(URL_OR_ID)` — inline embed on its own line
- `@gallery(url1, url2, url3, ...)` — swipeable gallery on its own line

Standard Markdown (headings, bold, italic, lists, links, images) is fully supported.

## GitHub Pages

Pages is served from `main` branch, root folder. URL:

```
https://grandmasterjc.github.io/spacex-tracker-config/
```

## Merch

Products are listed in `merch/index.json` and follow the same CMS pattern as the news feed. The app fetches this file at launch and displays product cards on the Dashboard.

### Why no prices?

Prices are intentionally omitted — they vary by Etsy currency and change without notice. The app links directly to each Etsy listing where the buyer sees the current local price.

### Adding a product

Push a new entry to the `products` array in `merch/index.json` — no app update required. Each product entry needs:

| Field       | Required | Notes                                         |
|-------------|----------|-----------------------------------------------|
| `id`        | yes      | URL-safe slug, unique across all products     |
| `title`     | yes      |                                               |
| `subtitle`  | no       | Short descriptor shown on the card            |
| `category`  | yes      | e.g. `apparel`, `poster`, `accessory`         |
| `image_url` | yes      | Direct image URL (e.g. etsystatic.com CDN)    |
| `url`       | yes      | Full Etsy listing URL                         |

The `store_url` field at the top level can be set to the Etsy shop landing page URL once one is available (currently `null`).

## Push Analytics

### Sending a manual push

1. Go to **Actions → Manual Push Notification** in the GitHub repository
2. Click **Run workflow** and fill in the inputs:

| Input | Required | Description |
|-------|----------|-------------|
| `title` | yes | Notification title |
| `body` | yes | Notification body |
| `target` | no | FCM topic or token (default: `all_users`) |
| `interruption_level` | no | `active` (default), `time-sensitive`, or `passive` |
| `article_id` | no | Article ID for in-app deep-link |
| `notes` | no | Free-text notes stored in the log |

3. The workflow sends the notification via FCM v1 and commits a new row to `push_history.csv`.

### Interruption levels

| Level | Sound | Priority | Use case |
|-------|-------|----------|----------|
| `active` | ✓ | 10 | Default — plays sound, Focus modes may defer |
| `time-sensitive` | ✓ | 10 | Breaks through Focus/DND/Sleep — use sparingly |
| `passive` | ✗ | 5 | Silent, appears only in Notification Center |

### Refreshing open-rate stats

Stats are pulled from GA4 (property **534379147**) automatically every **Monday at 09:00 CEST** via the **Refresh Push Stats** workflow. You can also trigger it manually:

1. Go to **Actions → Refresh Push Stats**
2. Click **Run workflow** (optionally set `days` to change the lookback window, default 14)

The script queries GA4 for notification events in a 3-day window after each push and writes the counts back to `push_history.csv`.

### push_history.csv columns

| Column | Description |
|--------|-------------|
| `timestamp_utc` | UTC timestamp of when the push was sent (`YYYY-MM-DDTHH:MM:SSZ`) |
| `timestamp_cest` | Same timestamp in Europe/Oslo timezone |
| `target` | FCM topic or token the push was sent to |
| `interruption_level` | `active`, `time-sensitive`, or `passive` |
| `title` | Notification title |
| `body` | Notification body |
| `article_id` | Article deep-link ID (empty if not set) |
| `message_id` | FCM v1 message ID returned by the API (`projects/.../messages/...`) |
| `api` | Always `fcm_v1` |
| `opens` | `notification_open` event count (GA4, 3-day window) |
| `unique_users` | Unique users who opened the notification (GA4 `totalUsers`) |
| `foreground` | `notification_foreground` event count |
| `toggled` | `notification_toggled` event count |
| `notes` | Free-text notes entered at send time |

Stats columns (`opens`, `unique_users`, `foreground`, `toggled`) are blank when a push is first logged and filled in by the refresh script.

### GA4 service account access

> **Important:** Service accounts do not automatically have access to GA4 data. The refresh script will fail with a 403 error until this is configured.

To grant access:
1. Find the service account email — it looks like `firebase-adminsdk-XXXX@spacex-tracker-97b32.iam.gserviceaccount.com`
   - Check the `FIREBASE_SERVICE_ACCOUNT` secret JSON, field `client_email`
   - Or: Firebase Console → Project Settings → Service Accounts
2. Open [Google Analytics](https://analytics.google.com) → Admin → Property Access Management (for property **534379147**)
3. Add the service account email as a **Viewer**

Alternatively, create a dedicated GA4 service account and store its JSON in the `GA4_SERVICE_ACCOUNT` secret. The refresh script checks `GA4_SERVICE_ACCOUNT` first, then falls back to `FIREBASE_SERVICE_ACCOUNT`.
