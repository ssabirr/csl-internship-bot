from datetime import datetime, timezone
import json
import os
import sys
import requests

SOURCES = [
    (
        "SimplifyJobs",
        "https://raw.githubusercontent.com/SimplifyJobs/"
        "Summer2027-Internships/dev/.github/scripts/listings.json",
    ),
    (
        "vanshb03",
        "https://raw.githubusercontent.com/vanshb03/"
        "Summer2027-Internships/main/.github/scripts/listings.json",
    ),
]
POSTED_IDS_PATH = "posted.json"

# Discord limits.
EMBEDS_PER_MESSAGE = 10
TITLE_MAX_LEN = 256
DESCRIPTION_MAX_LEN = 4096

# Guard against a corrupted/hand-edited posted.json causing a mass repost.
MAX_NEW_LISTINGS = 100

REQUIRED_FIELDS = ("id", "title", "url", "date_posted", "company_name")
MARKDOWN_SPECIAL_CHARS = "*_~`|>[]()"

# Sane epoch bounds for date_posted (year 2000 - year 2100). Rejects values
# datetime.fromtimestamp can't handle (e.g. upstream emitting milliseconds
# instead of seconds), which would otherwise raise unguarded in format_embed
# and permanently wedge that listing.
MIN_VALID_EPOCH = 946684800  # 2000-01-01T00:00:00Z
MAX_VALID_EPOCH = 4102444800  # 2100-01-01T00:00:00Z


def escape_markdown(text):
    """Escape Discord/Markdown-significant characters in free text."""
    out = []
    for char in str(text):
        if char == "\\" or char in MARKDOWN_SPECIAL_CHARS:
            out.append("\\")
        out.append(char)
    return "".join(out)


def truncate(text, limit):
    """Shorten text to at most `limit` characters, marking the cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def validate_listing(listing):
    """Return None if the listing can be posted, else a human-readable reason.

    Length problems are *not* rejections -- format_embed truncates instead.
    """
    if not isinstance(listing, dict):
        return "listing is not an object"
    for field in REQUIRED_FIELDS:
        value = listing.get(field)
        if value is None:
            return f"missing required field '{field}'"
        if isinstance(value, str) and not value.strip():
            return f"empty required field '{field}'"
    date_posted = listing.get("date_posted")
    if not isinstance(date_posted, (int, float)) or isinstance(date_posted, bool):
        return "field 'date_posted' is not a numeric timestamp"
    if not (MIN_VALID_EPOCH <= date_posted <= MAX_VALID_EPOCH):
        return "field 'date_posted' is outside the sane epoch range (2000-2100)"
    if not isinstance(listing.get("title"), str):
        return "field 'title' is not a string"
    if not isinstance(listing.get("url"), str):
        return "field 'url' is not a string"
    return None


def chunked(items, size):
    """Split a list into consecutive chunks of at most `size` items."""
    if size < 1:
        raise ValueError("size must be at least 1")
    return [items[i : i + size] for i in range(0, len(items), size)]


def _sort_key(listing):
    date_posted = listing.get("date_posted")
    if isinstance(date_posted, (int, float)) and not isinstance(date_posted, bool):
        return (0, date_posted)
    return (1, 0)


def _is_postable(listing):
    return (
        listing.get("active") is True
        and listing.get("is_visible") is not False
    )


def dedup_key(listing):
    """Normalized (company, title, url) signature for cross-source dedup.

    Two independent tracker repos assign different `id`s to the same real
    internship; this identifies "the same job" regardless of which repo it
    came from.
    """
    company = str(listing.get("company_name", "")).strip().lower()
    title = str(listing.get("title", "")).strip().lower()
    url = str(listing.get("url", "")).strip().lower()
    return f"{company}|{title}|{url}"


def find_new_listings(source_listings, posted_ids, posted_keys=()):
    posted_ids = set(posted_ids)
    posted_keys = set(posted_keys)
    new = [
        listing
        for listing in source_listings
        if _is_postable(listing)
        and listing.get("id") not in posted_ids
        and dedup_key(listing) not in posted_keys
    ]
    new.sort(key=_sort_key)
    return new


def bootstrap_posted_ids(source_listings):
    return [listing["id"] for listing in source_listings if _is_postable(listing)]


def bootstrap_posted_keys(source_listings):
    return list(
        {dedup_key(listing) for listing in source_listings if _is_postable(listing)}
    )


def format_embed(listing):
    locations = listing.get("locations") or []
    if not isinstance(locations, list):
        locations = []
    location_text = ", ".join(str(loc) for loc in locations)

    company = escape_markdown(listing.get("company_name", ""))
    description = f"**{company}**"
    if location_text:
        description += f"\n{escape_markdown(location_text)}"

    timestamp = datetime.fromtimestamp(
        listing["date_posted"], tz=timezone.utc
    ).isoformat()

    return {
        "title": truncate(listing.get("title", ""), TITLE_MAX_LEN),
        "url": listing.get("url", ""),
        "description": truncate(description, DESCRIPTION_MAX_LEN),
        "fields": [],
        "timestamp": timestamp,
    }


def fetch_source_listings(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_all_sources(sources):
    """Fetch every (name, url) source, tolerating individual failures.

    Returns (combined_listings, failed_source_names). A source that fails
    to fetch is skipped rather than failing the whole run -- one tracker
    repo being briefly down shouldn't block posting from the others.
    """
    combined = []
    failed = []
    for name, url in sources:
        try:
            combined.extend(fetch_source_listings(url))
        except (requests.RequestException, ValueError) as exc:
            print(f"Failed to fetch source '{name}': {type(exc).__name__}", file=sys.stderr)
            failed.append(name)
    return combined, failed


def load_posted_ids(path):
    """Load tracked state, migrating the legacy flat-ID-list format.

    Returns {"ids": [...], "keys": [...]}, or None if the file doesn't
    exist (first run). A pre-existing posted.json from before cross-source
    dedup was added is a plain JSON array of ID strings; that's treated as
    {"ids": <that array>, "keys": []} so nothing is lost or reposted.
    """
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"ids": data, "keys": []}
    return {"ids": data.get("ids", []), "keys": data.get("keys", [])}


def save_posted_ids(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"ids": sorted(state["ids"]), "keys": sorted(state["keys"])},
            f,
            indent=2,
        )
        f.write("\n")


# The channel PantherWatch posts to. Not a secret -- useless without the
# bot token, which is what actually authorizes posting.
DISCORD_CHANNEL_ID = "1291082188812582974"


def post_to_discord(bot_token, embeds):
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {"Authorization": f"Bot {bot_token}"}
    response = requests.post(url, headers=headers, json={"embeds": embeds}, timeout=30)
    response.raise_for_status()


def _status_code(exc):
    """Extract an HTTP status code from a requests exception, if any.

    Never returns or logs the exception string: exceptions from `requests`
    can echo request details, and this keeps the bot token out of logs
    unconditionally rather than relying on knowing exactly what leaks.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    return getattr(response, "status_code", None)


def main():
    bot_token = os.environ["DISCORD_BOT_TOKEN"]

    source_listings, failed_sources = fetch_all_sources(SOURCES)
    if len(failed_sources) == len(SOURCES):
        print("Failed to fetch any source; nothing to do.", file=sys.stderr)
        sys.exit(1)
    if failed_sources:
        print(f"Warning: continuing without source(s): {failed_sources}", file=sys.stderr)

    state = load_posted_ids(POSTED_IDS_PATH)

    if state is None:
        seeded_ids = bootstrap_posted_ids(source_listings)
        seeded_keys = bootstrap_posted_keys(source_listings)
        save_posted_ids(POSTED_IDS_PATH, {"ids": seeded_ids, "keys": seeded_keys})
        print(f"Bootstrap: seeded {len(seeded_ids)} active listing IDs, posted nothing.")
        return

    posted_ids = list(state["ids"])
    posted_keys = list(state["keys"])

    new_listings = find_new_listings(source_listings, posted_ids, posted_keys)

    if not new_listings:
        print("No new listings.")
        return

    if len(new_listings) > MAX_NEW_LISTINGS:
        print(
            f"Refusing to post: {len(new_listings)} new listings exceeds the sanity "
            f"cap of {MAX_NEW_LISTINGS}. posted.json may be corrupted or truncated. "
            "Nothing was posted and no state was written; investigate before rerunning.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate first: a bad listing is skipped, logged, and still marked handled
    # so it never wedges the pipeline behind it.
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
            print(f"Skipping listing {listing_id!r}: {reason}")
            if listing_id is not None:
                skipped_ids.append(listing_id)
                skipped_keys.append(dedup_key(listing))
        else:
            print(f"Skipping listing: {reason}")

    if skipped_ids:
        posted_ids.extend(skipped_ids)
        posted_keys.extend(skipped_keys)
        save_posted_ids(POSTED_IDS_PATH, {"ids": posted_ids, "keys": posted_keys})

    posted_count = 0
    dropped_count = 0
    had_client_error = False

    for batch in chunked(postable, EMBEDS_PER_MESSAGE):
        batch_ids = [listing["id"] for listing in batch]
        batch_keys = [dedup_key(listing) for listing in batch]
        try:
            post_to_discord(bot_token, [format_embed(listing) for listing in batch])
        except requests.RequestException as exc:
            status = _status_code(exc)
            if status is not None and 400 <= status < 500 and status != 429:
                # Client error: Discord will reject this payload again on every
                # retry. Mark handled so it doesn't wedge the queue forever.
                print(
                    f"Discord rejected a batch with HTTP {status}; dropping these "
                    f"listings without retry: {batch_ids}",
                    file=sys.stderr,
                )
                posted_ids.extend(batch_ids)
                posted_keys.extend(batch_keys)
                save_posted_ids(POSTED_IDS_PATH, {"ids": posted_ids, "keys": posted_keys})
                dropped_count += len(batch_ids)
                had_client_error = True
                continue
            status_text = status if status is not None else "no response"
            print(
                f"Failed to post batch to Discord (HTTP {status_text}); listing IDs "
                f"in failed batch: {batch_ids}. Stopping; these will be retried on "
                f"the next run. {posted_count} listing(s) posted and saved so far.",
                file=sys.stderr,
            )
            sys.exit(1)

        posted_ids.extend(batch_ids)
        posted_keys.extend(batch_keys)
        save_posted_ids(POSTED_IDS_PATH, {"ids": posted_ids, "keys": posted_keys})
        posted_count += len(batch_ids)

    print(
        f"Posted {posted_count} new listing(s); "
        f"skipped {len(skipped_ids)} invalid; dropped {dropped_count} rejected."
    )

    if had_client_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
