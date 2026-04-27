# SpaceX Tracker — Content Repo

## Launch Monitor

A GitHub Actions workflow (`.github/workflows/launch-monitor.yml`) runs every 10 minutes and monitors SpaceX launches for schedule changes and outcomes.

### How it works

1. Polls the [Launch Library 2](https://thespacedevs.com/llapi) API for upcoming and recent SpaceX launches
2. Compares against saved state in `state/launch_state.json`
3. Detects **reschedules** (NET moved by >= 5 minutes for launches within ±48h) and **outcomes** (Success / Failure / Partial Failure)
4. Sends FCM push notifications (in Norwegian) to the `all` topic
5. Commits updated state back to the repo

### Notifications

All push notifications are in Norwegian (Bokmål) and sent to the FCM topic `all`.

| Event | Example title | Example body |
|-------|--------------|--------------|
| Reschedule | Oppskytning flyttet | Falcon 9 Block 5 \| Starlink Group 17-16 er flyttet til 27. apr kl 18:30 |
| Success | Suksess | Falcon 9 Block 5 \| Starlink Group 17-16: Success |
| Failure | Feil | Falcon 9 Block 5 \| Starlink Group 17-16: Failure |

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
