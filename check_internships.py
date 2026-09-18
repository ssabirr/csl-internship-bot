from datetime import datetime, timezone
import json
import os
import sys
import requests

SOURCE_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/"
    "Summer2027-Internships/dev/.github/scripts/listings.json"
)
POSTED_IDS_PATH = "posted.json"

# Discord limits.
EMBEDS_PER_MESSAGE = 10
TITLE_MAX_LEN = 256
DESCRIPTION_MAX_LEN = 4096

# Guard against a corrupted/hand-edited posted.json causing a mass repost.
MAX_NEW_LISTINGS = 100

REQUIRED_FIELDS = ("id", "title", "url", "date_posted", "company_name")
MARKDOWN_SPECIAL_CHARS = "*_~`|>[]()"


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
    if not isinstance(listing.get("date_posted"), (int, float)) or isinstance(
        listing.get("date_posted"), bool
    ):
        return "field 'date_posted' is not a numeric timestamp"
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


def find_new_listings(source_listings, posted_ids):
    posted = set(posted_ids)
    new = [
        listing
        for listing in source_listings
        if _is_postable(listing) and listing.get("id") not in posted
    ]
    new.sort(key=_sort_key)
    return new


def bootstrap_posted_ids(source_listings):
    return [listing["id"] for listing in source_listings if _is_postable(listing)]


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


def post_to_discord(webhook_url, embeds):
    response = requests.post(webhook_url, json={"embeds": embeds}, timeout=30)
    response.raise_for_status()


def _status_code(exc):
    """Extract an HTTP status code from a requests exception, if any.

    Never returns or logs the exception string: it embeds the webhook URL,
    which contains the secret token.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    return getattr(response, "status_code", None)


def main():
    webhook_url = os.environ["DISCORD_WEBHOOK_URL"]

    try:
        source_listings = fetch_source_listings()
    except (requests.RequestException, ValueError) as exc:
        print(f"Failed to fetch source listings: {type(exc).__name__}", file=sys.stderr)
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
    for listing in new_listings:
        reason = validate_listing(listing)
        if reason is None:
            postable.append(listing)
            continue
        listing_id = listing.get("id") if isinstance(listing, dict) else None
        print(f"Skipping listing {listing_id!r}: {reason}")
        if listing_id is not None:
            skipped_ids.append(listing_id)

    posted_ids = list(posted_ids)
    if skipped_ids:
        posted_ids.extend(skipped_ids)
        save_posted_ids(POSTED_IDS_PATH, posted_ids)

    posted_count = 0
    dropped_count = 0
    had_client_error = False

    for batch in chunked(postable, EMBEDS_PER_MESSAGE):
        batch_ids = [listing["id"] for listing in batch]
        try:
            post_to_discord(webhook_url, [format_embed(listing) for listing in batch])
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
                save_posted_ids(POSTED_IDS_PATH, posted_ids)
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
        save_posted_ids(POSTED_IDS_PATH, posted_ids)
        posted_count += len(batch_ids)

    print(
        f"Posted {posted_count} new listing(s); "
        f"skipped {len(skipped_ids)} invalid; dropped {dropped_count} rejected."
    )

    if had_client_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
