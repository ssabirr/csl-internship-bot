# Internship Discord Bot — Design Spec

## Purpose

Automatically post new internship listings from the community-maintained
[SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships)
repo into a GSU CS club Discord channel, once per day, with no manual
intervention.

## Constraints & Decisions

- No native Discord webhook exists on the source repo — we own the
  polling and delivery.
- Delivery uses a Discord **incoming webhook** (not a full bot with a
  gateway connection), since we only ever push messages and never need
  to read/respond in the channel.
- Runs on **GitHub Actions** (scheduled workflow) — free, no server to
  maintain.
- State (which listings have already been posted) is tracked by
  **committing a JSON file back to this repo** after each run, rather
  than GitHub Actions cache (which can be evicted) or reading Discord
  channel history (which would require a full bot).

## Architecture

```
GitHub Actions cron (daily)
        │
        ▼
check_internships.py
        │
        ├─ GET raw listings.json from SimplifyJobs/Summer2027-Internships
        ├─ read posted.json (already-posted listing IDs) from this repo
        ├─ diff → new listings (oldest first)
        ├─ POST one Discord embed per new listing → webhook URL (secret)
        └─ write updated posted.json
        │
        ▼
Workflow commits posted.json if changed (github-actions[bot])
```

## Components

### `check_internships.py`
- Fetches `https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json`.
  The source file contains every listing ever recorded (17,000+
  entries as of this writing), most with `active: false` (expired).
  Only entries with `active: true` are considered.
- Loads `posted.json` (a JSON array of listing IDs already posted).
- **Bootstrap case:** if `posted.json` doesn't exist yet (first run),
  write it with the IDs of every currently-active listing *without*
  posting any of them to Discord, then exit. This avoids dumping
  thousands of backlog listings into the channel at once. From the
  next run onward, only genuinely new active listings get posted.
- Computes new listings: active listings in the source whose `id` is
  not in `posted.json`. Sorted oldest-first (`date_posted`) so the
  channel reads chronologically.
- For each new listing, POSTs a Discord embed to
  `$DISCORD_WEBHOOK_URL` containing: company name, role title, location,
  posted date, and the application link.
- On success for all listings, writes the full updated ID list back to
  `posted.json`.
- Exits non-zero on fetch failure or malformed JSON, without touching
  `posted.json` — so a failed run doesn't lose or corrupt state, and
  simply retries in full on the next scheduled run.

### `posted.json`
- Single JSON array of listing IDs (strings). Committed to the repo,
  giving a versioned audit trail of what's been posted and when.

### `.github/workflows/check.yml`
- Scheduled trigger (daily cron, e.g. `0 13 * * *` — 9am ET).
- Also supports `workflow_dispatch` for manual runs (e.g. testing, or
  re-running after a failure).
- Steps: checkout, set up Python, install `requests`, run the script
  with `DISCORD_WEBHOOK_URL` from repo secrets, commit `posted.json` if
  it changed.

### `requirements.txt`
- `requests` only.

## Data Flow / Error Handling

- Source repo is public — no auth needed to fetch `listings.json`.
- Discord webhook rate limit is 30 messages/minute; expected volume
  (a handful of new listings/day) never approaches this, so no
  throttling logic is needed.
- If the Discord POST for one listing fails mid-batch, the script exits
  non-zero without updating `posted.json` for *any* listing in that
  run — the whole day's batch is retried next run. This risks
  duplicate posts for listings that succeeded before the failure, but
  never risks silently dropping a listing. Given expected low daily
  volume, occasional duplicates are an acceptable trade-off against the
  complexity of partial-batch tracking.

## Setup (manual, one-time)

1. Create a Discord incoming webhook in the target channel (Server
   Settings → Integrations → Webhooks → New Webhook → copy URL).
2. Add the URL as a GitHub Actions secret named `DISCORD_WEBHOOK_URL`
   on this repo.

## Testing

- Unit-testable pure function for the diff logic (`new listings from
  source + posted`) with a handful of fixture cases (empty posted.json,
  no new listings, several new listings, malformed source JSON).
- Manual end-to-end test via `workflow_dispatch` against a test Discord
  channel before pointing at the real club channel.

## Out of Scope

- Any bot commands, moderation, or other club-bot features — this repo
  is scoped to internship posting only.
- Filtering/tailoring listings (e.g. by role keyword) — posts
  everything in the source list. Can be added later as a follow-up if
  wanted.
