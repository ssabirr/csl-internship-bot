# Internship Discord Bot — Design Spec

## Purpose

Automatically post new internship listings from community-maintained
tracker repos —
[SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships)
and [vanshb03/Summer2027-Internships](https://github.com/vanshb03/Summer2027-Internships)
— into a GSU CS club Discord channel, checked hourly, with no manual
intervention. Other repos publishing the same JSON schema can be added
later; repos whose data lives behind a private backend with no
structured export (checked and rejected for speedyapply and zapplyjobs
— see Out of Scope) are not supported.

## Constraints & Decisions

- No native Discord integration exists on the source repos — we own the
  polling and delivery.
- Delivery uses a real Discord **bot account authenticated via REST API
  with a bot token** (originally an incoming webhook; switched so the
  poster — PantherWatch — appears as a visible member of the server,
  matching how other utility bots like Dyno appear). This still needs
  no persistent Gateway connection: the bot only ever sends messages
  via one-off REST calls, never reads or responds to anything, so a
  short-lived scheduled script can authenticate as it without staying
  connected.
- Runs on **GitHub Actions** (scheduled workflow) — free, no server to
  maintain.
- State (which listings have already been posted) is tracked by
  **committing a JSON file back to this repo** after each run, rather
  than GitHub Actions cache (which can be evicted) or reading Discord
  channel history (which would require a full bot).

## Architecture

```
GitHub Actions cron (hourly)
        │
        ▼
check_internships.py
        │
        ├─ GET raw listings.json from each source repo (tolerating
        │  individual source failures)
        ├─ read posted.json (already-posted IDs + cross-source dedup keys)
        ├─ diff → new listings (oldest first), capped at 100/run
        ├─ validate + chunk into batches of ≤10 embeds
        ├─ POST each batch → Discord REST API (bot token, secret)
        └─ after each successful batch, write updated posted.json
        │
        ▼
Workflow commits posted.json if changed (github-actions[bot]),
even if the script exited non-zero, so incremental progress persists
```

## Components

### `check_internships.py`
- Fetches each configured source's `listings.json` (currently
  SimplifyJobs and vanshb03; both publish the same schema —
  `.github/scripts/listings.json` with `id`, `active`, `is_visible`,
  `date_posted`, `company_name`, `title`, `url`, `locations`). Sources
  are fetched independently; one source failing to fetch is logged and
  skipped rather than failing the whole run, unless *all* sources fail.
  Each source file contains every listing ever recorded (thousands of
  entries), most with `active: false` (expired). Only entries with
  `active: true` are considered.
- Loads `posted.json` — `{"ids": [...], "keys": [...]}`. `ids` are
  per-listing IDs already posted (a listing keeps the same ID only
  within its own source repo). `keys` are cross-source dedup
  signatures (see below). A pre-existing `posted.json` from before
  cross-source dedup was added — a plain JSON array of ID strings — is
  auto-migrated to `{"ids": <that array>, "keys": []}` on load.
- **Cross-source dedup:** two independent tracker repos assign
  different IDs to the same real internship. Each listing also gets a
  `dedup_key` — a normalized `company_name|title|url` signature
  (lowercased, whitespace-trimmed). A listing already posted from one
  source (its key is in `posted.json`'s `keys`) is skipped even if a
  different source lists it under a new ID. Dedup is checked against
  previously-*posted* keys, not within a single run's batch — two
  sources introducing the exact same job in the same hour is a rare
  edge case that would produce one duplicate, self-correcting on the
  next run once the key is recorded.
- **Bootstrap case:** if `posted.json` doesn't exist yet (first run),
  write it with the IDs and dedup keys of every currently-active
  listing across all sources *without* posting any of them to Discord,
  then exit. This avoids dumping thousands of backlog listings into
  the channel at once. From the next run onward, only genuinely new
  active listings get posted. (Adding a *new* source to an
  already-running bot needs the same backlog-seeding treatment, done
  once manually at deploy time — otherwise that source's entire
  current backlog would look "new" in one run and trip the sanity
  cap.)
- Computes new listings: active, visible (`is_visible is not False`)
  listings, combined across all sources, whose `id` is not in
  `posted.json`'s `ids` and whose `dedup_key` is not in its `keys`.
  Sorted oldest-first (`date_posted`) so the channel reads
  chronologically. If more than 100 are found, exits non-zero without
  posting (sanity cap — see Data Flow below).
- Validates each new listing and skips (logs + marks handled) any
  missing a required field; truncates an oversized title/description
  rather than rejecting it.
- Batches valid listings into groups of up to 10 and POSTs each batch
  as one Discord message, via `POST /channels/{channel_id}/messages`
  authenticated with `Authorization: Bot $DISCORD_BOT_TOKEN`, containing
  embeds with: company name, role title, location, posted date, and the
  application link. The channel ID is a constant in the script (not a
  secret — useless without the bot token).
- After each batch posts successfully, immediately writes the updated
  ID list (including that batch's IDs) back to `posted.json` —
  incremental persistence, not a single write at the end.
- Exits non-zero on fetch failure, malformed JSON, the sanity cap, or a
  batch POST failure — in the batch-failure case, prior successful
  batches' IDs are already saved, so the next run resumes rather than
  reposting everything.

### `posted.json`
- `{"ids": [...], "keys": [...]}` — per-listing IDs and cross-source
  dedup keys. Committed to the repo, giving a versioned audit trail of
  what's been posted and when.

### `.github/workflows/check.yml`
- Scheduled trigger (hourly cron, `0 * * * *`).
- Also supports `workflow_dispatch` for manual runs (e.g. testing, or
  re-running after a failure).
- A `concurrency` group (with `cancel-in-progress: false`) so an
  overlapping manual run queues behind the scheduled run instead of
  racing it against the same `posted.json`.
- A `timeout-minutes` cap on the job.
- Steps: checkout, set up Python, install `requests`, run the script
  with `DISCORD_BOT_TOKEN` from repo secrets, then commit `posted.json`
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
  Discord's per-channel message rate limit (roughly 30/minute,
  regardless of whether posting via webhook or bot token). Posting one
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
  exception string from a failed Discord API request, on the
  conservative assumption that request/response details could echo
  something sensitive. Only the HTTP status code and the listing ID
  are logged.

## Setup (manual, one-time)

1. Create a Discord Application + Bot in the
   [Developer Portal](https://discord.com/developers/applications); set
   its username/avatar there (this is the bot's real identity, not a
   per-message override). Leave all Privileged Gateway Intents off —
   the bot only sends messages, never reads.
2. Add the bot token as a GitHub Actions secret named
   `DISCORD_BOT_TOKEN` on this repo.
3. Generate an OAuth2 invite URL (Developer Portal → OAuth2 → URL
   Generator) with scope `bot` and permissions limited to **View
   Channel**, **Send Messages**, **Embed Links** — no broader
   permissions than the bot actually needs. Open it and authorize it
   into the server.
4. Set `DISCORD_CHANNEL_ID` in `check_internships.py` to the target
   channel's ID (Discord → enable Developer Mode → right-click channel
   → Copy Channel ID). Not a secret.

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
- **speedyapply/2027-SWE-College-Jobs, speedyapply/2027-AI-College-Jobs,
  zapplyjobs/Internships-2027** — investigated and rejected as sources.
  None publish a structured JSON export; their real data lives behind
  a private backend (Supabase for speedyapply, Zapply's own platform),
  and the repos only contain human-readable markdown tables generated
  from it. Supporting these would mean scraping/parsing markdown
  tables instead of reading structured JSON — meaningfully more
  fragile (breaks silently on reformatting) and a separate adapter,
  not a one-line addition. Revisit only if there's a strong reason to
  take on that maintenance cost.
- **Instagram Stories monitoring** — considered and rejected. Stories
  require an authenticated session to view even on public accounts,
  and no official API exposes a third party's Stories regardless of
  account visibility or follower count. The only automation path is
  unofficial scraping via a logged-in account against Instagram's
  private API, which violates Instagram's Terms of Service and risks
  that account being banned. Recommended alternative if revisited:
  ask the content creator to forward posts directly (e.g. to a
  webhook) rather than build a scraper.
