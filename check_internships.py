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


def find_new_listings(source_listings, posted_ids):
    posted = set(posted_ids)
    new = [
        listing
        for listing in source_listings
        if listing.get("active") is True and listing["id"] not in posted
    ]
    new.sort(key=lambda listing: listing["date_posted"])
    return new


def bootstrap_posted_ids(source_listings):
    return [listing["id"] for listing in source_listings if listing.get("active") is True]


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


def post_to_discord(webhook_url, embed):
    response = requests.post(webhook_url, json={"embeds": [embed]}, timeout=30)
    response.raise_for_status()


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
