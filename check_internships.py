from datetime import datetime, timezone


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
