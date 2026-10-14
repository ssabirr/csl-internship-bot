# Multi-Destination Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize posting from one hardcoded Discord bot-channel into a list of independently-tracked destinations (the existing GSU bot channel plus a new ICNA webhook), with **no behavior change** to GSU's existing posting pattern (hourly, oldest-first, no cap).

**Architecture:** `check_internships.py` fetches all sources once per run (unchanged), then loops over `DESTINATIONS`, running a self-contained `process_destination()` pipeline per destination against its own slice of a per-destination state tree in `posted.json`. A destination is either `type: "bot"` (REST API + bot token) or `type: "webhook"` (POST to a webhook URL secret); `post_embeds()` dispatches between them. A brand-new destination bootstraps itself (seeds its backlog without posting) automatically the first time it's seen, independent of any other destination's state.

**Tech Stack:** Python 3, `requests`, GitHub Actions (unchanged).

**Spec:** `docs/superpowers/specs/2026-09-18-internship-discord-bot-design.md` (see "Multi-Destination Delivery" section)

## Global Constraints

- **No change to GSU's existing posting behavior**: hourly cadence, oldest-first ordering, no daily cap, no prioritization. A daily-cap/prioritization/cadence redesign was discussed and even drafted into an earlier version of this plan, but was explicitly dropped before implementation — do not reintroduce `DAILY_POST_LIMIT`, notable-company matching, priority sorting, or a cron-schedule change as part of this work.
- `PER_RUN_LISTING_LIMIT = 150` (unchanged) — max candidates considered per destination per run.
- `CORRUPTION_GUARD_THRESHOLD = 1000` (unchanged) — hard abort per destination if exceeded.
- `MAX_LISTING_AGE_DAYS = 5` (unchanged) — freshness filter.
- Cadence stays `0 * * * *` (hourly) — unchanged.
- `DESTINATIONS = [{"name": "gsu", "type": "bot", "channel_id": "1291082188812582974"}, {"name": "icna", "type": "webhook", "secret_env": "DISCORD_ICNA_WEBHOOK_URL"}]` — exact values.
- Secrets: `DISCORD_BOT_TOKEN` (existing, for `type: "bot"` destinations) and `DISCORD_ICNA_WEBHOOK_URL` (new, for the `icna` destination specifically — each webhook-type destination's secret name comes from its own `secret_env` field).
- State file path and name are unchanged (`posted.json`), but its top-level shape changes to `{"destinations": {...}}` — see Task 1.
- Reuse, do not redefine: `SOURCES`, `fetch_all_sources`, `fetch_source_listings`, `find_new_listings`, `bootstrap_posted_ids`, `bootstrap_posted_keys`, `dedup_key`, `_is_postable`, `_is_recent`, `_sort_key`, `validate_listing`, `chunked`, `format_embed`, `escape_markdown`, `truncate`, `_status_code`. None of these need to change.
- Only `requests` as a third-party dependency (unchanged).
- Error logging never includes a raw exception string from a failed Discord request (unchanged policy — `_status_code` already enforces this; new code must keep using it, never `str(exc)`).

## Review Focus

- A destination's secret environment variable is set to an **empty string** (not merely unset) — must be treated exactly like "missing" (skip that destination, log it, don't crash attempting to use `""` as a bot token or webhook URL).
- One destination hitting its corruption guard, a validation failure, or an unresolved batch-post failure must **never** block, skip, or corrupt a different destination's processing in the same run — this is the central promise of the redesign, and a shared mutable `state` dict or an uncaught exception propagating out of `process_destination` could silently violate it.
- Migrating a pre-existing production `posted.json` (currently the flat `{"ids": [...], "keys": [...]}` format with ~4,800+ real tracked listings) into `destinations.gsu` must preserve every existing ID and key exactly.
- A brand-new destination (`icna`, the first time this runs) must bootstrap (seed its backlog, post nothing) in the *same* run that an already-initialized destination (`gsu`) continues posting normally — neither should block the other.
- `main()`'s overall exit code must be non-zero if *any* destination hit an unresolved error, but every destination must still be attempted even if an earlier one in the loop failed — a naive implementation might accidentally `sys.exit()` from inside the loop and never reach the remaining destinations.

---

### Task 1: Per-destination state format + migration

**Files:**
- Modify: `check_internships.py` (replace `load_posted_ids`/`save_posted_ids`/`POSTED_IDS_PATH`)
- Test: `tests/test_check_internships.py`

**Interfaces:**
- Produces: `STATE_PATH` (str constant, replaces `POSTED_IDS_PATH`, same value `"posted.json"`), `load_state(path) -> dict | None`, `save_state(path, state) -> None`. `load_state` returns `{"destinations": {name: {"ids": [...], "keys": [...]}, ...}}`, or `None` if the file doesn't exist.

- [ ] **Step 1: Write the failing tests**

Replace the existing `load_posted_ids`/`save_posted_ids`/`POSTED_IDS_PATH` import and its four existing tests (`test_load_posted_ids_returns_none_when_missing`, `test_load_posted_ids_migrates_legacy_flat_list`, `test_load_posted_ids_reads_new_dict_format`, `test_save_then_load_round_trips`) with the block below. Change the import line at the top of the test file from `load_posted_ids,` / `save_posted_ids,` to `load_state,` / `save_state,`.

```python
def test_load_state_returns_none_when_missing(tmp_path):
    assert load_state(str(tmp_path / "missing.json")) is None


def test_load_state_migrates_legacy_flat_id_list(tmp_path):
    path = tmp_path / "posted.json"
    path.write_text('["a", "b", "c"]', encoding="utf-8")
    state = load_state(str(path))
    assert state == {"destinations": {"gsu": {"ids": ["a", "b", "c"], "keys": []}}}


def test_load_state_migrates_legacy_ids_keys_dict(tmp_path):
    path = tmp_path / "posted.json"
    path.write_text('{"ids": ["a"], "keys": ["k1"]}', encoding="utf-8")
    state = load_state(str(path))
    assert state == {"destinations": {"gsu": {"ids": ["a"], "keys": ["k1"]}}}


def test_load_state_reads_destinations_format_as_is(tmp_path):
    path = tmp_path / "posted.json"
    path.write_text(
        '{"destinations": {"gsu": {"ids": ["a"], "keys": []}}}',
        encoding="utf-8",
    )
    state = load_state(str(path))
    assert state == {"destinations": {"gsu": {"ids": ["a"], "keys": []}}}


def test_save_then_load_round_trips_multiple_destinations(tmp_path):
    path = str(tmp_path / "posted.json")
    state = {
        "destinations": {
            "gsu": {"ids": ["b", "a"], "keys": ["k2", "k1"]},
            "icna": {"ids": [], "keys": []},
        }
    }
    save_state(path, state)
    assert load_state(path) == {
        "destinations": {
            "gsu": {"ids": ["a", "b"], "keys": ["k1", "k2"]},
            "icna": {"ids": [], "keys": []},
        }
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_check_internships.py -k "state" -v`
Expected: FAIL with `ImportError: cannot import name 'load_state'` (and similarly for `save_state`).

- [ ] **Step 3: Replace the implementation**

In `check_internships.py`, replace the line `POSTED_IDS_PATH = "posted.json"` with `STATE_PATH = "posted.json"`, and replace the entire `load_posted_ids`/`save_posted_ids` function pair with:

```python
def load_state(path):
    """Load per-destination state, migrating older formats.

    Returns {"destinations": {name: {"ids": [...], "keys": [...]}, ...}},
    or None if the file doesn't exist (first run ever). Pre-existing
    posted.json files from before this format existed are migrated into
    destinations.gsu: either a plain JSON array of ID strings (the oldest
    format), or the flat {"ids": [...], "keys": [...]} format (used
    before multi-destination support). Neither loses any tracked data.
    """
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"destinations": {"gsu": {"ids": data, "keys": []}}}
    if "destinations" in data:
        return data
    return {
        "destinations": {
            "gsu": {"ids": data.get("ids", []), "keys": data.get("keys", [])}
        }
    }


def save_state(path, state):
    serializable = {"destinations": {}}
    for name, dest_state in state["destinations"].items():
        serializable["destinations"][name] = {
            "ids": sorted(dest_state["ids"]),
            "keys": sorted(dest_state["keys"]),
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)
        f.write("\n")
```

Do not modify `main()` yet — it still references the old names and will be fixed in Task 3. It's fine for the module to be temporarily inconsistent between this task and Task 3 landing; the tests for this task only exercise `load_state`/`save_state` directly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_check_internships.py -k "state" -v`
Expected: PASS (all 5 new tests)

- [ ] **Step 5: Commit**

```bash
git add check_internships.py tests/test_check_internships.py
git commit -m "feat: per-destination state format with legacy migration"
```

---

### Task 2: Multi-destination posting dispatch

**Files:**
- Modify: `check_internships.py` (replaces `DISCORD_CHANNEL_ID` and the old single-destination `post_to_discord`)
- Test: `tests/test_check_internships.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `DESTINATIONS` (list of dicts, see Global Constraints for exact value), `_resolve_auth(destination) -> str | None`, `post_embeds(destination, auth, embeds) -> None` (raises `requests.RequestException` on failure, same contract the old `post_to_discord` had). `_resolve_auth` returns `None` if the destination's required env var is unset *or empty* — never raises, never returns an empty string.

This task's `post_embeds` makes real network calls and is integration code, same as the old `post_to_discord` was (never unit-tested with a live request) — but `_resolve_auth` is pure and fully testable without touching the network, so it gets real tests. `post_embeds` itself is verified later by the manual dry run in Task 3.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_check_internships.py` (add `_resolve_auth` to the import block, and add `import pytest` near the top of the file if it isn't already imported — check first):

```python
def test_resolve_auth_bot_type_reads_bot_token(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake-token")
    destination = {"name": "gsu", "type": "bot", "channel_id": "123"}
    assert _resolve_auth(destination) == "fake-token"


def test_resolve_auth_webhook_type_reads_its_own_secret_env(monkeypatch):
    monkeypatch.setenv("DISCORD_ICNA_WEBHOOK_URL", "https://discord.com/api/webhooks/fake")
    destination = {"name": "icna", "type": "webhook", "secret_env": "DISCORD_ICNA_WEBHOOK_URL"}
    assert _resolve_auth(destination) == "https://discord.com/api/webhooks/fake"


def test_resolve_auth_missing_env_var_returns_none(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    destination = {"name": "gsu", "type": "bot", "channel_id": "123"}
    assert _resolve_auth(destination) is None


def test_resolve_auth_empty_string_env_var_returns_none(monkeypatch):
    monkeypatch.setenv("DISCORD_ICNA_WEBHOOK_URL", "")
    destination = {"name": "icna", "type": "webhook", "secret_env": "DISCORD_ICNA_WEBHOOK_URL"}
    assert _resolve_auth(destination) is None


def test_resolve_auth_unknown_type_raises():
    destination = {"name": "bad", "type": "carrier_pigeon"}
    with pytest.raises(ValueError):
        _resolve_auth(destination)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_check_internships.py -k "resolve_auth" -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_auth'`

- [ ] **Step 3: Write the implementation**

In `check_internships.py`, replace the existing `DISCORD_CHANNEL_ID` constant and the existing `post_to_discord` function entirely with:

```python
DESTINATIONS = [
    {"name": "gsu", "type": "bot", "channel_id": "1291082188812582974"},
    {"name": "icna", "type": "webhook", "secret_env": "DISCORD_ICNA_WEBHOOK_URL"},
]


def _resolve_auth(destination):
    """Returns the auth value (bot token or webhook URL) for a destination,
    or None if its required secret/env var is missing or empty -- callers
    must treat None as "skip this destination," never as a 'try anyway'.
    """
    if destination["type"] == "bot":
        return os.environ.get("DISCORD_BOT_TOKEN") or None
    if destination["type"] == "webhook":
        return os.environ.get(destination["secret_env"]) or None
    raise ValueError(f"Unknown destination type: {destination['type']!r}")


def post_embeds(destination, auth, embeds):
    """Posts a batch of embeds to one destination.

    `auth` is the bot token (type: "bot") or the webhook URL itself
    (type: "webhook") -- whatever _resolve_auth returned for this
    destination. Raises requests.RequestException on failure, same
    contract the single-destination post_to_discord had.
    """
    if destination["type"] == "bot":
        url = f"https://discord.com/api/v10/channels/{destination['channel_id']}/messages"
        response = requests.post(
            url, headers={"Authorization": f"Bot {auth}"}, json={"embeds": embeds}, timeout=30
        )
    elif destination["type"] == "webhook":
        response = requests.post(auth, json={"embeds": embeds}, timeout=30)
    else:
        raise ValueError(f"Unknown destination type: {destination['type']!r}")
    response.raise_for_status()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_check_internships.py -k "resolve_auth" -v`
Expected: PASS (all 5 new tests)

- [ ] **Step 5: Commit**

```bash
git add check_internships.py tests/test_check_internships.py
git commit -m "feat: multi-destination posting dispatch (bot + webhook)"
```

---

### Task 3: Per-destination main() restructuring

**Files:**
- Modify: `check_internships.py` (replaces the entire body of `main()`, adds `process_destination`)

**Interfaces:**
- Consumes: `load_state`/`save_state`/`STATE_PATH` (Task 1), `DESTINATIONS`/`_resolve_auth`/`post_embeds` (Task 2), plus the unchanged `fetch_all_sources`, `SOURCES`, `find_new_listings`, `bootstrap_posted_ids`, `bootstrap_posted_keys`, `dedup_key`, `validate_listing`, `chunked`, `format_embed`, `_status_code`, `EMBEDS_PER_MESSAGE`, `PER_RUN_LISTING_LIMIT`, `CORRUPTION_GUARD_THRESHOLD`.
- Produces: `process_destination(name, destination, source_listings, state, save_callback, now=None) -> bool` (`True` = finished without an unresolved error, even if nothing was posted; `False` = hit a stop condition). `main()` itself produces nothing further tasks consume — it's the entrypoint.

This task is integration logic (orchestration, network calls, file I/O), consistent with how `main()` has always been handled in this project: verified by a manual dry run, not unit tests, since the pure logic it calls is already tested in Tasks 1-2 and in the pre-existing suite. This task intentionally keeps `find_new_listings`'s existing oldest-first result as the final posting order — no re-sorting, no prioritization, no daily-quota slicing: GSU's behavior must come out identical to what it is today, just restructured to loop over destinations.

- [ ] **Step 1: Replace `main()` and add `process_destination`**

In `check_internships.py`, delete the entire existing `main()` function and replace it with:

```python
def process_destination(name, destination, source_listings, state, save_callback, now=None):
    """Processes one destination's share of a run.

    `state` is the full {"destinations": {...}} tree; this destination's
    slice lives at state["destinations"][name], created fresh here if
    absent (bootstrap case). Mutates `state` in place and calls
    save_callback() after every change that should be persisted
    immediately -- never waits until the very end. Returns True if this
    destination finished without an unresolved error (even if it posted
    nothing), False if it hit a stop condition (missing secret,
    corruption guard, an unresolved batch failure) that should make the
    overall run exit non-zero. A problem here must never raise out to
    the caller -- the caller still needs to attempt every other
    destination regardless of what happens here.
    """
    auth = _resolve_auth(destination)
    if auth is None:
        print(f"[{name}] Missing required secret; skipping this destination.", file=sys.stderr)
        return False

    if name not in state["destinations"]:
        seeded_ids = bootstrap_posted_ids(source_listings)
        seeded_keys = bootstrap_posted_keys(source_listings)
        state["destinations"][name] = {"ids": seeded_ids, "keys": seeded_keys}
        save_callback()
        print(f"[{name}] Bootstrap: seeded {len(seeded_ids)} active listing IDs, posted nothing.")
        return True

    dest_state = state["destinations"][name]
    new_listings = find_new_listings(source_listings, dest_state["ids"], dest_state["keys"], now=now)

    if not new_listings:
        print(f"[{name}] No new listings.")
        return True

    if len(new_listings) > CORRUPTION_GUARD_THRESHOLD:
        print(
            f"[{name}] Refusing to post: {len(new_listings)} new listings exceeds the "
            f"corruption guard of {CORRUPTION_GUARD_THRESHOLD}. posted.json may be "
            "corrupted or truncated. Nothing was posted and no state was written for "
            "this destination; investigate before rerunning.",
            file=sys.stderr,
        )
        return False

    if len(new_listings) > PER_RUN_LISTING_LIMIT:
        print(
            f"[{name}] Found {len(new_listings)} new listings; processing the oldest "
            f"{PER_RUN_LISTING_LIMIT} this run and leaving the rest for future runs."
        )
        new_listings = new_listings[:PER_RUN_LISTING_LIMIT]

    posted_ids = list(dest_state["ids"])
    posted_keys = list(dest_state["keys"])

    postable = []
    skipped_ids = []
    skipped_keys = []
    for listing in new_listings:
        reason = validate_listing(listing)
        if reason is None:
            postable.append(listing)
            continue
        if isinstance(listing, dict):
            listing_id = listing.get("id")
            print(f"[{name}] Skipping listing {listing_id!r}: {reason}")
            if listing_id is not None:
                skipped_ids.append(listing_id)
                skipped_keys.append(dedup_key(listing))
        else:
            print(f"[{name}] Skipping listing: {reason}")

    if skipped_ids:
        posted_ids.extend(skipped_ids)
        posted_keys.extend(skipped_keys)
        dest_state["ids"] = posted_ids
        dest_state["keys"] = posted_keys
        save_callback()

    posted_count = 0
    dropped_count = 0
    had_client_error = False

    for batch in chunked(postable, EMBEDS_PER_MESSAGE):
        batch_ids = [listing["id"] for listing in batch]
        batch_keys = [dedup_key(listing) for listing in batch]
        try:
            post_embeds(destination, auth, [format_embed(listing) for listing in batch])
        except requests.RequestException as exc:
            status = _status_code(exc)
            if status == 400:
                print(
                    f"[{name}] Discord rejected a batch with HTTP {status}; dropping "
                    f"these listings without retry: {batch_ids}",
                    file=sys.stderr,
                )
                posted_ids.extend(batch_ids)
                posted_keys.extend(batch_keys)
                dest_state["ids"] = posted_ids
                dest_state["keys"] = posted_keys
                save_callback()
                dropped_count += len(batch_ids)
                had_client_error = True
                continue
            status_text = status if status is not None else "no response"
            print(
                f"[{name}] Failed to post batch to Discord (HTTP {status_text}); "
                f"listing IDs in failed batch: {batch_ids}. Stopping; these will be "
                f"retried on the next run. {posted_count} listing(s) posted and saved "
                "so far.",
                file=sys.stderr,
            )
            return False

        posted_ids.extend(batch_ids)
        posted_keys.extend(batch_keys)
        dest_state["ids"] = posted_ids
        dest_state["keys"] = posted_keys
        save_callback()
        posted_count += len(batch_ids)

    print(
        f"[{name}] Posted {posted_count} new listing(s); "
        f"skipped {len(skipped_ids)} invalid; dropped {dropped_count} rejected."
    )

    return not had_client_error


def main():
    source_listings, failed_sources = fetch_all_sources(SOURCES)
    if len(failed_sources) == len(SOURCES):
        print("Failed to fetch any source; nothing to do.", file=sys.stderr)
        sys.exit(1)
    if failed_sources:
        print(f"Warning: continuing without source(s): {failed_sources}", file=sys.stderr)

    state = load_state(STATE_PATH)
    if state is None:
        state = {"destinations": {}}

    def save_callback():
        save_state(STATE_PATH, state)

    all_ok = True
    for destination in DESTINATIONS:
        ok = process_destination(destination["name"], destination, source_listings, state, save_callback)
        all_ok = all_ok and ok

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
```

Make sure the trailing `if __name__ == "__main__": main()` block that already exists at the end of the file isn't duplicated — there should be exactly one, at the very end of the file (the one shown above replaces the old one; delete any other copy).

- [ ] **Step 2: Run the full existing suite to confirm nothing broke**

Run: `python -m pytest tests/test_check_internships.py -v`
Expected: PASS — every test from Tasks 1-2 plus all pre-existing tests (the pure functions they call are unchanged).

- [ ] **Step 3: Manual dry run against fixture data**

Run this from the project root (no network, no real secrets) to sanity-check the wiring:

```bash
python -c "
import os
os.environ['DISCORD_BOT_TOKEN'] = 'fake-token-for-dry-run'
from check_internships import process_destination, DESTINATIONS

source_listings = [
    {
        'id': 'x', 'active': True, 'is_visible': True, 'date_posted': __import__('time').time() - 3600,
        'company_name': 'Acme', 'title': 'Intern', 'locations': ['Remote'],
        'url': 'https://example.com/job',
    },
]
state = {'destinations': {}}
calls = []
ok = process_destination('gsu', DESTINATIONS[0], source_listings, state, lambda: calls.append(dict(state)))
print('bootstrap ok:', ok)
print('state after bootstrap:', state)
print('save_callback called', len(calls), 'time(s)')
"
```

Expected output: `bootstrap ok: True`, `state after bootstrap:` showing `gsu` seeded with `ids: ['x']`, and the save callback called once. This confirms a brand-new destination bootstraps without attempting a real network post (bootstrap always returns before reaching `post_embeds`).

- [ ] **Step 4: Commit**

```bash
git add check_internships.py
git commit -m "feat: restructure main() into per-destination processing"
```

---

### Task 4: Wire up the ICNA webhook secret in the workflow

**Files:**
- Modify: `.github/workflows/check.yml`

**Interfaces:** None — this task only changes the workflow YAML, no Python interfaces involved. The cron schedule is **not** touched by this task (stays `0 * * * *`, per Global Constraints).

**Important operational note for whoever runs this task:** pushing a change to any file under `.github/workflows/` through this repo's `gh`/git CLI setup has previously been rejected with `refusing to allow an OAuth App to create or update workflow ... without 'workflow' scope`. If that happens again: commit the change locally, but the push itself may need to go through GitHub's web file editor instead (`github.com/ssabirr/csl-internship-bot/edit/master/.github/workflows/check.yml`) rather than `git push`. This is a known, already-encountered limitation of this project's push credentials, not a bug in this task's instructions.

- [ ] **Step 1: Add the new secret to the `Run check` step's env**

In `.github/workflows/check.yml`, change:
```yaml
      - name: Run check
        env:
          DISCORD_BOT_TOKEN: ${{ secrets.DISCORD_BOT_TOKEN }}
        run: python check_internships.py
```
to:
```yaml
      - name: Run check
        env:
          DISCORD_BOT_TOKEN: ${{ secrets.DISCORD_BOT_TOKEN }}
          DISCORD_ICNA_WEBHOOK_URL: ${{ secrets.DISCORD_ICNA_WEBHOOK_URL }}
        run: python check_internships.py
```

- [ ] **Step 2: Confirm the `DISCORD_ICNA_WEBHOOK_URL` secret already exists on the repo**

Run: `gh api repos/ssabirr/csl-internship-bot/actions/secrets --jq '.secrets[].name'`
Expected: output includes `DISCORD_ICNA_WEBHOOK_URL` (it should already have been added per the spec's Setup section, before this task runs — if it's missing, stop and get it added first; the workflow will otherwise run with that destination silently skipped every time, per Task 2/3's "missing secret" handling).

- [ ] **Step 3: Commit and push**

```bash
git add .github/workflows/check.yml
git commit -m "ci: wire up ICNA webhook secret"
git push
```

If the push is rejected for the OAuth workflow-scope reason described above, apply this same one-line diff manually via the GitHub web editor instead, and skip the local push for this one file.

---

## Post-plan verification

After all tasks land and are live (not just the repo's local test suite), trigger the workflow manually (`gh workflow run check.yml --repo ssabirr/csl-internship-bot`) and confirm from its logs:

1. `[icna]` bootstraps (seeds its backlog, posts nothing) on its first real run — confirms the per-destination bootstrap doesn't flood the new server.
2. `[gsu]` continues processing normally in the *same* run (not blocked by `icna`'s bootstrap, and vice versa) — confirms destination independence.
3. `[gsu]`'s behavior (cadence, ordering, volume) is unchanged from before this plan — no daily cap, no prioritization, still hourly, still oldest-first.
4. Check Discord directly: the ICNA server's `#sabir-bot` channel shows the webhook's configured name (set when the webhook was created, per the spec's Setup section) — no code-level username override is needed for the webhook destination, unlike the bot destination's earlier `WEBHOOK_USERNAME` mechanism (which no longer exists, since GSU posts as a real bot account now).
