# Internship Discord Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Post new active internship listings from SimplifyJobs/Summer2027-Internships into a Discord channel once a day via a GitHub Actions scheduled workflow and a Discord webhook.

**Architecture:** A single Python script (`check_internships.py`) fetches the source repo's `listings.json`, diffs it against a locally-tracked `posted.json` of already-posted listing IDs, posts each new active listing as a Discord embed via webhook, and writes the updated ID list back. A GitHub Actions workflow runs the script on a daily cron and commits `posted.json` if it changed.

**Tech Stack:** Python 3, `requests` library, GitHub Actions, Discord incoming webhooks.

**Spec:** `docs/superpowers/specs/2026-09-18-internship-discord-bot-design.md`

## Global Constraints

- Source data: `https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json` — only entries with `active: true` are eligible to post.
- Delivery: Discord incoming webhook, URL read from environment variable `DISCORD_WEBHOOK_URL`. Never hardcode a webhook URL.
- State file: `posted.json`, a JSON array of listing ID strings (each listing's `id` field, a UUID string), committed back to this repo.
- First-run (bootstrap) behavior: if `posted.json` does not exist, seed it with every currently-active listing ID and exit **without posting anything**.
- On any error (fetch failure, malformed JSON, a webhook POST failure), the script must exit non-zero and must **not** write `posted.json` — the whole batch retries next run.
- New listings are sorted oldest-first by `date_posted` (a Unix timestamp) before posting.
- Only `requests` as a third-party dependency.

---

## File Structure

- `check_internships.py` — all script logic (fetch, diff, post, bootstrap, state write). Kept as one file since the logic is a single linear pipeline with no independent reusable parts.
- `tests/test_check_internships.py` — unit tests for the pure diff/bootstrap/sort logic, using fixture data (no real network calls).
- `requirements.txt` — pins `requests`.
- `.github/workflows/check.yml` — the scheduled workflow.
- `posted.json` — created at runtime (bootstrap task creates the initial empty-repo state; not committed by this plan directly, but the workflow will produce and commit it on first run).
- `.gitignore` — standard Python ignores (`__pycache__/`, `*.pyc`, `.venv/`).

---

### Task 1: Project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `tests/__init__.py` (empty)

**Interfaces:** None — no code yet.

- [ ] **Step 1: Create `requirements.txt`**

```
requests==2.32.3
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
```

- [ ] **Step 3: Create empty `tests/__init__.py`**

Create the file `tests/__init__.py` with no content (makes `tests/` an importable package).

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .gitignore tests/__init__.py
git commit -m "chore: project scaffolding"
```

---

### Task 2: Diff logic — find new active listings

**Files:**
- Create: `check_internships.py`
- Test: `tests/test_check_internships.py`

**Interfaces:**
- Produces: `find_new_listings(source_listings: list[dict], posted_ids: list[str]) -> list[dict]` — filters `source_listings` to those with `active is True` and `id` not in `posted_ids`, returns them sorted ascending by `date_posted`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_check_internships.py`:

```python
from check_internships import find_new_listings


def _listing(id_, active=True, date_posted=100):
    return {
        "id": id_,
        "active": active,
        "date_posted": date_posted,
        "company_name": "Acme",
        "title": "Intern",
        "locations": ["Remote"],
        "url": "https://example.com/job",
    }


def test_no_listings_returns_empty():
    assert find_new_listings([], []) == []


def test_all_already_posted_returns_empty():
    listings = [_listing("a"), _listing("b")]
    assert find_new_listings(listings, ["a", "b"]) == []


def test_inactive_listings_excluded():
    listings = [_listing("a", active=False)]
    assert find_new_listings(listings, []) == []


def test_new_active_listing_included():
    listings = [_listing("a")]
    result = find_new_listings(listings, [])
    assert len(result) == 1
    assert result[0]["id"] == "a"


def test_sorted_oldest_first_by_date_posted():
    listings = [
        _listing("newer", date_posted=200),
        _listing("older", date_posted=100),
    ]
    result = find_new_listings(listings, [])
    assert [l["id"] for l in result] == ["older", "newer"]


def test_mix_of_posted_inactive_and_new():
    listings = [
        _listing("already-posted", date_posted=50),
        _listing("inactive", active=False, date_posted=60),
        _listing("new-one", date_posted=70),
    ]
    result = find_new_listings(listings, ["already-posted"])
    assert [l["id"] for l in result] == ["new-one"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_check_internships.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'check_internships'` (file doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `check_internships.py`:

```python
def find_new_listings(source_listings, posted_ids):
    posted = set(posted_ids)
    new = [
        listing
        for listing in source_listings
        if listing.get("active") is True and listing["id"] not in posted
    ]
    new.sort(key=lambda listing: listing["date_posted"])
    return new
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_check_internships.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add check_internships.py tests/test_check_internships.py
git commit -m "feat: add diff logic for finding new active listings"
```

---

### Task 3: Bootstrap logic — seed state without posting on first run

**Files:**
- Modify: `check_internships.py`
- Test: `tests/test_check_internships.py`

**Interfaces:**
- Consumes: nothing new from Task 2.
- Produces: `bootstrap_posted_ids(source_listings: list[dict]) -> list[str]` — returns the `id` of every listing with `active is True`, for seeding `posted.json` on first run.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_check_internships.py`:

```python
from check_internships import bootstrap_posted_ids


def test_bootstrap_returns_only_active_ids():
    listings = [
        _listing("a", active=True),
        _listing("b", active=False),
        _listing("c", active=True),
    ]
    assert set(bootstrap_posted_ids(listings)) == {"a", "c"}


def test_bootstrap_empty_source_returns_empty():
    assert bootstrap_posted_ids([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_check_internships.py -v`
Expected: FAIL with `ImportError: cannot import name 'bootstrap_posted_ids'`

- [ ] **Step 3: Write minimal implementation**

Add to `check_internships.py`:

```python
def bootstrap_posted_ids(source_listings):
    return [listing["id"] for listing in source_listings if listing.get("active") is True]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_check_internships.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add check_internships.py tests/test_check_internships.py
git commit -m "feat: add bootstrap logic for first-run state seeding"
```

---

### Task 4: Discord embed formatting

**Files:**
- Modify: `check_internships.py`
- Test: `tests/test_check_internships.py`

**Interfaces:**
- Consumes: a single listing dict as produced by `find_new_listings` (has `company_name`, `title`, `locations`, `url`, `date_posted`).
- Produces: `format_embed(listing: dict) -> dict` — returns a Discord embed dict suitable for the `embeds` field of a webhook payload.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_check_internships.py`:

```python
from check_internships import format_embed


def test_format_embed_contains_key_fields():
    listing = _listing("a", date_posted=1700000000)
    listing["company_name"] = "Acme Corp"
    listing["title"] = "Software Intern"
    listing["locations"] = ["Atlanta, GA", "Remote"]
    listing["url"] = "https://example.com/apply"

    embed = format_embed(listing)

    assert embed["title"] == "Software Intern"
    assert embed["url"] == "https://example.com/apply"
    assert "Acme Corp" in embed["description"]
    assert "Atlanta, GA, Remote" in embed["description"]
    assert embed["fields"] == []
    assert isinstance(embed["timestamp"], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_check_internships.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_embed'`

- [ ] **Step 3: Write minimal implementation**

Add to the top of `check_internships.py`:

```python
from datetime import datetime, timezone
```

Add function:

```python
def format_embed(listing):
    locations = ", ".join(listing["locations"])
    description = f"**{listing['company_name']}**\n{locations}"
    timestamp = datetime.fromtimestamp(listing["date_posted"], tz=timezone.utc).isoformat()
    return {
        "title": listing["title"],
        "url": listing["url"],
        "description": description,
        "fields": [],
        "timestamp": timestamp,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_check_internships.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Commit**

```bash
git add check_internships.py tests/test_check_internships.py
git commit -m "feat: add Discord embed formatting for listings"
```

---

### Task 5: Fetch, post, and orchestrate main()

**Files:**
- Modify: `check_internships.py`

**Interfaces:**
- Consumes: `find_new_listings`, `bootstrap_posted_ids`, `format_embed` from Tasks 2-4.
- Produces: `fetch_source_listings() -> list[dict]`, `load_posted_ids(path: str) -> list[str] | None` (returns `None` if the file doesn't exist), `save_posted_ids(path: str, ids: list[str]) -> None`, `post_to_discord(webhook_url: str, embed: dict) -> None`, `main() -> None`. This is the final integration task — no later task depends on these names.

This task is integration logic (network calls, file I/O) rather than pure logic, so it is verified by a manual dry run (Step 6) instead of unit tests — the pure pieces are already covered by Tasks 2-4's tests.

- [ ] **Step 1: Add constants and fetch/load/save functions**

Add to the top of `check_internships.py` (below the existing import):

```python
import json
import os
import sys
import requests

SOURCE_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/"
    "Summer2027-Internships/dev/.github/scripts/listings.json"
)
POSTED_IDS_PATH = "posted.json"
```

Add functions:

```python
def fetch_source_listings():
    response = requests.get(SOURCE_URL, timeout=30)
    response.raise_for_status()
    return response.json()


def load_posted_ids(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_posted_ids(path, ids):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ids, f, indent=2)
        f.write("\n")
```

- [ ] **Step 2: Add `post_to_discord`**

```python
def post_to_discord(webhook_url, embed):
    response = requests.post(webhook_url, json={"embeds": [embed]}, timeout=30)
    response.raise_for_status()
```

- [ ] **Step 3: Add `main()`**

```python
def main():
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]

    try:
        source_listings = fetch_source_listings()
    except (requests.RequestException, ValueError) as exc:
        print(f"Failed to fetch source listings: {exc}", file=sys.stderr)
        sys.exit(1)

    posted_ids = load_posted_ids(POSTED_IDS_PATH)

    if posted_ids is None:
        seeded = bootstrap_posted_ids(source_listings)
        save_posted_ids(POSTED_IDS_PATH, seeded)
        print(f"Bootstrap: seeded {len(seeded)} active listing IDs, posted nothing.")
        return

    new_listings = find_new_listings(source_listings, posted_ids)

    if not new_listings:
        print("No new listings.")
        return

    try:
        for listing in new_listings:
            post_to_discord(webhook_url, format_embed(listing))
    except requests.RequestException as exc:
        print(f"Failed to post to Discord: {exc}", file=sys.stderr)
        sys.exit(1)

    updated_ids = posted_ids + [listing["id"] for listing in new_listings]
    save_posted_ids(POSTED_IDS_PATH, updated_ids)
    print(f"Posted {len(new_listings)} new listing(s).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add `requests` import check and run full test suite**

Run: `python -m pytest tests/test_check_internships.py -v`
Expected: PASS (all 9 tests still pass — this task adds no new pure-logic tests).

- [ ] **Step 5: Manual dry run against fixture data**

Run this from the project root to sanity-check `main()`'s wiring without hitting the network or Discord (temporarily, interactively — not committed):

```bash
python -c "
from check_internships import find_new_listings, bootstrap_posted_ids, format_embed
listings = [
    {'id': 'x', 'active': True, 'date_posted': 1700000000, 'company_name': 'Test Co',
     'title': 'Intern', 'locations': ['Remote'], 'url': 'https://example.com'},
]
print(bootstrap_posted_ids(listings))
print(find_new_listings(listings, []))
print(format_embed(listings[0]))
"
```

Expected output: a list containing `'x'`, a list containing the listing dict, and an embed dict with `title: 'Intern'`.

- [ ] **Step 6: Commit**

```bash
git add check_internships.py
git commit -m "feat: add fetch/post orchestration and main entrypoint"
```

---

### Task 6: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/check.yml`

**Interfaces:**
- Consumes: `check_internships.py`'s `main()` entrypoint (run as `python check_internships.py`), `DISCORD_WEBHOOK_URL` repo secret.
- Produces: nothing consumed by later tasks (final task).

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/check.yml`:

```yaml
name: Check for new internships

on:
  schedule:
    - cron: "0 13 * * *"
  workflow_dispatch: {}

permissions:
  contents: write

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install -r requirements.txt

      - name: Run check
        env:
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
        run: python check_internships.py

      - name: Commit updated posted.json
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add posted.json
          git diff --staged --quiet || git commit -m "chore: update posted listings"
          git push
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/check.yml
git commit -m "ci: add scheduled workflow to check and post internships"
```

- [ ] **Step 3: Push and verify**

```bash
git push -u origin master
```

Then, in the GitHub repo's Settings → Secrets and variables → Actions, add secret `DISCORD_WEBHOOK_URL` with the webhook URL from the target Discord channel. Trigger the workflow manually via the Actions tab ("Run workflow") to confirm: first run should bootstrap `posted.json` (no Discord messages), commit the file, and succeed. A second manual run should report "No new listings" (since nothing changed since bootstrap) and not error.

---

## Post-plan verification

After Task 6, confirm end-to-end by manually adding a fake new ID removal from `posted.json` for one real active listing (simulating "not yet posted") and re-running the workflow, to confirm a real Discord message renders correctly in the target channel before relying on the daily cron.
