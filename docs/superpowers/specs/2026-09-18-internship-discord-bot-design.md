# Internship Discord Bot — Design Spec

## Purpose

Automatically post new internship listings from community-maintained
tracker repos —
[SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships)
and [vanshb03/Summer2027-Internships](https://github.com/vanshb03/Summer2027-Internships)
— into Discord, with no manual intervention. Other repos publishing the
same JSON schema can be added later; repos whose data lives behind a
private backend with no structured export (checked and rejected for
speedyapply and zapplyjobs — see Out of Scope) are not supported.

As of 2026-10-14 this posts to **two independent Discord servers**:
the original GSU CS club server (via the PantherWatch bot) and the
"Icna Technology Summer Internship" server (via an incoming webhook,
nicknamed InternWatch in that server's context — see Multi-Destination
Delivery below for why the mechanism differs between the two). Both
destinations run the exact same unthrottled behavior GSU always has —
hourly checks, oldest-first, no daily cap, no prioritization. A
daily-cap/prioritization/slower-cadence redesign was discussed and
even drafted into this spec at one point, but was explicitly dropped
before implementation (see Out of Scope) — nothing about GSU's posting
behavior changes as part of adding ICNA.

## Constraints & Decisions

- No native Discord integration exists on the source repos — we own the
  polling and delivery.
- Delivery mechanism is chosen per destination, not fixed to one:
  - **GSU** uses a real Discord **bot account authenticated via REST
    API with a bot token** (originally an incoming webhook; switched so
    the poster — PantherWatch — appears as a visible member of the
    server, matching how other utility bots like Dyno appear). This
    still needs no persistent Gateway connection: the bot only ever
    sends messages via one-off REST calls, never reads or responds to
    anything, so a short-lived scheduled script can authenticate as it
    without staying connected.
  - **ICNA** uses an **incoming webhook** instead, because inviting a
    bot into a server requires the "Manage Server" permission, which
    the operator of this bot does not have on the ICNA server, and
    "Manage Server" is a hard Discord platform restriction with no
    workaround — there is no code or configuration path around it. A
    webhook only needs "Manage Webhooks," which the operator does have
    there. This is an intentional mechanism split, not a design
    inconsistency: a bot invite is used wherever it's available
    (visible member, consistent identity), falling back to a webhook
    only where server permissions genuinely block the bot route.
- Runs on **GitHub Actions** (scheduled workflow) — free, no server to
  maintain.
- State (which listings have already been posted) is tracked by
  **committing a JSON file back to this repo** after each run, rather
  than GitHub Actions cache (which can be evicted) or reading Discord
  channel history (which would require a full bot). Each destination
  gets its own independent slice of this state (see
  Multi-Destination Delivery), so a problem with one destination can
  never block or corrupt the other's progress.

## Architecture

```
GitHub Actions cron (hourly)
        │
        ▼
check_internships.py
        │
        ├─ GET raw listings.json from each source repo ONCE per run,
        │  shared across all destinations (tolerating per-source failures)
        │
        └─ for each destination (gsu, icna, ...):
             ├─ load that destination's own state slice from posted.json
             │  (ids, keys)
             ├─ diff → new + fresh + undeduped listings (oldest first)
             ├─ corruption guard / per-run limit (per destination)
             ├─ validate + chunk into batches of ≤10 embeds
             ├─ POST each batch via that destination's delivery method
             │  (bot REST API + bot token, or webhook URL)
             └─ after each successful batch, write that destination's
                state slice — independent of every other destination
        │
        ▼
Workflow commits posted.json if changed (github-actions[bot]),
even if the script exited non-zero, so incremental progress persists
```

## Multi-Destination Delivery

Added 2026-10-14 when a second server (ICNA) needed the same feed. The
design generalizes "post to Discord" into a list of **destinations**,
each independently configured and tracked — otherwise behaving
identically to the original single-destination design (same ordering,
same guards, same cadence):

```python
DESTINATIONS = [
    {"name": "gsu", "type": "bot", "channel_id": "1291082188812582974"},
    {"name": "icna", "type": "webhook", "secret_env": "DISCORD_ICNA_WEBHOOK_URL"},
]
```

- `type: "bot"` posts via `POST /channels/{channel_id}/messages` with
  `Authorization: Bot $DISCORD_BOT_TOKEN` (unchanged from the original
  single-destination design).
- `type: "webhook"` posts directly to the URL stored in the named
  secret (no bot token involved).
- A single `post_embeds(destination, auth, embeds)` dispatches on
  `destination["type"]` to build the right request; everything
  upstream of it (filtering, batching) is identical regardless of
  destination type.
- If a destination's required secret is missing or empty, that
  destination is skipped (logged, not fatal) for this run — a
  misconfiguration on one destination (e.g. the ICNA webhook secret
  not yet added) must never block the other from posting normally.
- The run's overall exit code is non-zero if *any* destination hit an
  unresolved error (same per-destination error classification as
  before: 400 drops the batch, 401/403/404/429/5xx stay retryable) —
  but every destination is still attempted even if an earlier one
  failed. One destination's outage is visible (red X in Actions) without
  silently also stalling the other.

### State: per-destination, not global

```json
{
  "destinations": {
    "gsu":  {"ids": [...], "keys": [...]},
    "icna": {"ids": [...], "keys": [...]}
  }
}
```

Migration on load: the old flat `{"ids": [...], "keys": [...]}` format
(and the even older flat ID-array format before that) becomes
`destinations.gsu`'s state — GSU's existing tracked history (4,800+
listings) carries over untouched. Any destination present in
`DESTINATIONS` but absent from `destinations` in the loaded state
(e.g. `icna`, the first time this runs) is **bootstrapped
independently in the same run**: its current active+visible backlog is
seeded (ids + keys) without posting, exactly like the original
single-destination bootstrap, while any *other* already-initialized
destination proceeds with normal posting in that same run. Bootstrap
status is no longer an all-or-nothing gate on the whole script — it's
per destination, so adding a third destination later never disrupts
the first two.

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
  `active: true` are considered. Fetched once per run and shared across
  every destination.
- Loads `posted.json` — see Multi-Destination Delivery above for the
  current `{"destinations": {...}}` shape and its migration from the
  earlier flat `{"ids": [...], "keys": [...]}` format.
- **Cross-source dedup:** two independent tracker repos assign
  different IDs to the same real internship. Each listing also gets a
  `dedup_key` — a normalized `company_name|title|url` signature
  (lowercased, whitespace-trimmed). A listing already posted from one
  source (its key is in that destination's `keys`) is skipped even if a
  different source lists it under a new ID. Dedup is checked against
  previously-*posted* keys, not within a single run's batch — two
  sources introducing the exact same job in the same hour is a rare
  edge case that would produce one duplicate, self-correcting on the
  next run once the key is recorded. Dedup state (like everything else
  in `posted.json`) is per destination.
- **Bootstrap case:** per destination, independent of the others (see
  Multi-Destination Delivery) — a destination's current active+visible
  backlog across all sources is seeded (ids + keys) *without* posting,
  then that destination is done for this run while any other
  already-initialized destination still processes normally.
- Computes new listings per destination: active, visible
  (`is_visible is not False`) listings, combined across all sources,
  whose `id` is not in that destination's `ids`, whose `dedup_key` is
  not in its `keys`, and whose `date_posted` is within the last 5 days
  (`MAX_LISTING_AGE_DAYS` — see Data Flow below). A missing/malformed
  `date_posted` is not filtered here; `validate_listing` rejects those
  separately, keeping "is it fresh" and "is it well-formed" as distinct
  checks. Sorted oldest-first (`date_posted`) so the channel reads
  chronologically. If more than `CORRUPTION_GUARD_THRESHOLD` are found
  for a destination, that destination exits non-zero without posting
  (corruption guard — see Data Flow below); if more than
  `PER_RUN_LISTING_LIMIT` are found, only that many are considered this
  run and the rest wait for the next one.
- Validates each new listing and skips (logs + marks handled) any
  missing a required field; truncates an oversized title/description
  rather than rejecting it.
- Batches valid listings into groups of up to 10 and POSTs each batch
  as one Discord message, via that destination's delivery method (see
  Multi-Destination Delivery: bot REST API + bot token, or a webhook
  URL), containing embeds with: company name, role title, location,
  posted date, and the application link.
- After each batch posts successfully, immediately writes that
  destination's updated state (including that batch's IDs/keys) back
  to `posted.json` — incremental persistence, not a single write at
  the end.
- Exits non-zero (after attempting every destination) if fetching *all*
  sources failed, or if any destination hit the corruption guard or an
  unresolved batch-post failure — in the batch-failure case, that
  destination's prior successful batches are already saved, so the
  next run resumes rather than reposting everything, and other
  destinations are unaffected.

### `posted.json`
- `{"destinations": {"<name>": {"ids": [...], "keys": [...]}, ...}}`
  — see Multi-Destination Delivery for the full shape and migration
  from earlier formats. Committed to the repo, giving a versioned
  audit trail of what's been posted, when, and to which destination.

### `.github/workflows/check.yml`
- Scheduled trigger (hourly cron, `0 * * * *`).
- Also supports `workflow_dispatch` for manual runs (e.g. testing, or
  re-running after a failure).
- A `concurrency` group (with `cancel-in-progress: false`) so an
  overlapping manual run queues behind the scheduled run instead of
  racing it against the same `posted.json`.
- A `timeout-minutes` cap on the job.
- Steps: checkout, set up Python, install `requests`, run the script
  with `DISCORD_BOT_TOKEN` and `DISCORD_ICNA_WEBHOOK_URL` from repo
  secrets, then commit `posted.json` if it changed — this commit step
  runs even if the script exited non-zero, so incrementally-saved
  progress from a partial batch failure survives instead of being lost
  with the runner.

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
- **Incremental state persistence:** a destination's state is updated
  after each of its successful batches, not only after its processing
  finishes. If batch N fails for a destination, batches 1..N-1 are
  already durably recorded as posted for it, and that destination's
  processing stops (the run overall exits non-zero, but other
  destinations are still attempted) — the next run resumes that
  destination from batch N instead of reposting everything already
  sent. This still guarantees a listing is never silently dropped (it's
  retried until it succeeds) while eliminating the duplicate-storm
  failure mode of the original all-or-nothing rule.
- **Per-listing validation:** a listing missing a required field, or
  with an oversized title/description, is skipped and logged rather
  than aborting the whole run — one bad listing from the
  community-maintained source must not wedge every other listing
  behind it indefinitely. Skipped listings are still recorded in
  `posted.json` (as "handled") so they aren't retried forever.
- **Freshness filter:** only listings whose `date_posted` is within the
  last `MAX_LISTING_AGE_DAYS` (5) days are eligible to post. Without
  this, a listing a source repo only just added to its feed — but that
  was itself posted weeks or months ago (backfilled data, or a listing
  this bot simply hadn't ingested until now) — would show up in the
  channel looking like a brand-new opportunity. The cutoff is
  club-observed practice, not tied to any upstream API's own notion of
  "active."
- **Discord batch-rejection handling:** if Discord rejects a posted
  batch with HTTP 400 (Bad Request), that specific payload is
  permanently malformed and would fail identically on every retry, so
  the batch is dropped and marked handled without retrying. Any other
  status — 401/403/404 (bad token, missing permissions, wrong channel
  — an environment/config problem, not the listing's fault), 429
  (rate limited), or 5xx — is treated as retryable: the run stops
  without marking that batch handled, so it posts normally on the next
  run once the underlying issue is fixed. (Found via a real 403 during
  bot-token setup, when the batch would otherwise have been
  permanently dropped over what was just a missing channel permission.)
- **Per-run processing limit (self-healing throttle):** at most
  `PER_RUN_LISTING_LIMIT` (150) new listings are considered per
  destination per run, oldest first; any excess is simply left
  untouched for the next run — nothing is marked handled, so it's
  picked up normally next time. This paces a real high-volume burst
  across several runs without any manual intervention. (Originally
  this was a hard abort at 100 with no processing at all; a real
  high-volume week — 295 genuinely fresh listings in one run — tripped
  it, and because a hard abort writes zero state, the identical growing
  backlog re-tripped it on every subsequent run for 4 days straight
  with no self-recovery, appearing as "the bot keeps failing" when the
  actual cause was safe-guard design, not rejected/invalid listings.)
- **Corruption guard:** if the number of new listings found for a
  single destination in one run exceeds `CORRUPTION_GUARD_THRESHOLD`
  (1000), that destination exits non-zero without posting or writing
  anything for itself, forcing investigation before retrying — other
  destinations are still attempted normally. This is reserved for
  actual corruption (e.g. a wiped or badly truncated `posted.json`) —
  the 5-day freshness filter already bounds even a fully-empty
  destination's worst case to a few hundred (measured: 421 across both
  sources at time of writing), so 1000 sits comfortably above any
  volume that's ever legitimate while staying well below what real
  corruption looks like.
- **Concurrency guard:** the workflow uses a concurrency group so an
  overlapping manual (`workflow_dispatch`) run can never race the
  scheduled run against the same `posted.json`.
- **Error message hygiene:** error logging never includes the raw
  exception string from a failed Discord API request, on the
  conservative assumption that request/response details could echo
  something sensitive. Only the HTTP status code and the listing ID
  are logged.

## Setup (manual, one-time)

### GSU destination (bot)

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
   into the server. Requires "Manage Server" permission on the target
   server — see ICNA destination below for the fallback when that's
   not available.
4. Set that destination's `channel_id` in `DESTINATIONS` to the target
   channel's ID (Discord → enable Developer Mode → right-click channel
   → Copy Channel ID). Not a secret.

### ICNA destination (webhook)

Used instead of a bot invite because the operator lacks "Manage
Server" permission on this server — a hard Discord platform
restriction with no workaround; a webhook only needs "Manage
Webhooks," which the operator does have there.

1. In the target channel: **Edit Channel → Integrations → Webhooks →
   New Webhook**. Name/avatar it as desired (nickname "InternWatch" for
   this server's context — this is the webhook's own configured name,
   shown automatically on every message it posts; no code-level
   override is needed, unlike the earlier bot-side `WEBHOOK_USERNAME`
   mechanism from before GSU switched to a full bot account).
2. Copy the webhook URL immediately into a GitHub Actions secret named
   `DISCORD_ICNA_WEBHOOK_URL` — **never paste a webhook URL anywhere
   other than directly into the GitHub secret field.** It's a bearer
   credential with no separate auth step; anyone who has the URL can
   post as it. (One was accidentally pasted in plaintext chat during
   this feature's setup — that one was deleted and regenerated before
   use, never stored in code or committed anywhere.)
3. No invite flow, no permissions dialog, no channel ID needed — the
   webhook URL alone fully identifies both the destination and the
   credential.

### Adding a future destination

Add an entry to `DESTINATIONS` (bot type needs "Manage Server" on that
server; webhook type needs only "Manage Webhooks"), add whatever
secret it requires, and it bootstraps independently on its first run —
no changes needed to any other destination's config or state.

## Testing

- Unit-testable pure functions for the diff logic (`new listings from
  source + posted`) and state migration (legacy flat format →
  per-destination format) — fixture cases following the existing
  suite's pattern.
- Manual end-to-end test via `workflow_dispatch` against a test Discord
  channel before pointing at a real club channel. For the ICNA
  destination specifically: verify its independent bootstrap doesn't
  post anything on first run, and that a GSU-side failure (simulated)
  doesn't block ICNA's posting in the same run, and vice versa.

## Out of Scope

- Any bot commands, moderation, or other club-bot features — this repo
  is scoped to internship posting only.
- Filtering/tailoring listings by role keyword, company reputation, or
  any other criterion — posts everything that passes the
  freshness/validity/dedup checks, regardless of role or company.
- **Daily volume cap + notable-company prioritization + slower
  cadence** — a redesign to reduce posting volume/frequency and
  prioritize "better" listings when over a cap was discussed in detail
  and drafted into an earlier version of this spec, but was explicitly
  dropped before implementation once it became clear it had never
  actually been deployed to GSU either. Both destinations run the
  original unthrottled, unprioritized behavior. Revisit as its own
  separate change if wanted later — it should not be silently
  reintroduced as a side effect of unrelated work.
- Per-destination divergence in any future per-destination setting —
  not applicable today since there are no destination-specific tuning
  values at all (both destinations share identical behavior by
  construction, not by configuration). Would become relevant again if
  a cap/prioritization feature (see above) is revisited.
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
