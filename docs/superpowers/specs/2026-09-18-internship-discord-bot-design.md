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
        ├─ diff → new listings (oldest first), capped at 100/run
        ├─ validate + chunk into batches of ≤10 embeds
        ├─ POST each batch → webhook URL (secret)
        └─ after each successful batch, write updated posted.json
        │
        ▼
Workflow commits posted.json if changed (github-actions[bot]),
even if the script exited non-zero, so incremental progress persists
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
- Computes new listings: active, visible (`is_visible is not False`)
  listings in the source whose `id` is not in `posted.json`. Sorted
  oldest-first (`date_posted`) so the channel reads chronologically. If
  more than 100 are found, exits non-zero without posting (sanity cap —
  see Data Flow below).
- Validates each new listing and skips (logs + marks handled) any
  missing a required field; truncates an oversized title/description
  rather than rejecting it.
- Batches valid listings into groups of up to 10 and POSTs each batch
  as one Discord message (`$DISCORD_WEBHOOK_URL`) containing embeds
  with: company name, role title, location, posted date, and the
  application link.
- After each batch posts successfully, immediately writes the updated
  ID list (including that batch's IDs) back to `posted.json` —
  incremental persistence, not a single write at the end.
- Exits non-zero on fetch failure, malformed JSON, the sanity cap, or a
  batch POST failure — in the batch-failure case, prior successful
  batches' IDs are already saved, so the next run resumes rather than
  reposting everything.

### `posted.json`
- Single JSON array of listing IDs (strings). Committed to the repo,
  giving a versioned audit trail of what's been posted and when.

### `.github/workflows/check.yml`
- Scheduled trigger (daily cron, e.g. `0 13 * * *` — 9am ET).
- Also supports `workflow_dispatch` for manual runs (e.g. testing, or
  re-running after a failure).
- A `concurrency` group (with `cancel-in-progress: false`) so an
  overlapping manual run queues behind the scheduled run instead of
  racing it against the same `posted.json`.
- A `timeout-minutes` cap on the job.
- Steps: checkout, set up Python, install `requests`, run the script
  with `DISCORD_WEBHOOK_URL` from repo secrets, then commit `posted.json`
  if it changed — this commit step runs even if the script exited
  non-zero, so incrementally-saved progress from a partial batch
  failure survives instead of being lost with the runner.

### `requirements.txt`
- `requests` only.

## Data Flow / Error Handling

- Source repo is public — no auth needed to fetch `listings.json`.
- **Real daily volume is much higher than originally assumed.** Live
  data (checked 2026-09-18) shows 4,321 currently-active listings and
  60-300+ newly-active listings per day during internship season (e.g.
  225, 208, 316 on recent days) — one to two orders of magnitude above
  Discord's webhook rate limit of ~30 messages/minute. Posting one
  message per listing with no batching or throttling will hit the
  limit, fail, and (under the original all-or-nothing state rule)
  retry the same doomed batch forever. The design below replaces that
  assumption.
- **Batching:** listings are posted in batches of up to 10 embeds per
  Discord message (Discord's per-message embed limit), not one message
  per listing. This drops a 200-listing day from 200 requests to ~20,
  comfortably under the rate limit.
- **Incremental state persistence:** `posted.json` is updated after
  each successful batch, not only after the entire run succeeds. If
  batch N fails, batches 1..N-1 are already durably recorded as
  posted, and the run exits non-zero — the next run resumes from batch
  N instead of reposting everything already sent. This still guarantees
  a listing is never silently dropped (it's retried until it succeeds)
  while eliminating the duplicate-storm failure mode of the original
  all-or-nothing rule.
- **Per-listing validation:** a listing missing a required field, or
  with an oversized title/description, is skipped and logged rather
  than aborting the whole run — one bad listing from the
  community-maintained source must not wedge every other listing
  behind it indefinitely. Skipped listings are still recorded in
  `posted.json` (as "handled") so they aren't retried forever.
- **Sanity cap:** if the number of new listings in a single run exceeds
  a large threshold (100), the run exits non-zero without posting
  anything. This is a guard against a corrupted or hand-edited
  `posted.json` causing a mass-repost of the entire active listing set.
- **Concurrency guard:** the workflow uses a concurrency group so an
  overlapping manual (`workflow_dispatch`) run can never race the
  scheduled run against the same `posted.json`.
- **Error message hygiene:** error logging never includes the raw
  exception string from a failed webhook request, since that string
  can contain the webhook URL (including its secret token). Only the
  HTTP status code and the listing ID are logged.

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
