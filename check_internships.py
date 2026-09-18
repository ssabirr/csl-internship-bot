def find_new_listings(source_listings, posted_ids):
    posted = set(posted_ids)
    new = [
        listing
        for listing in source_listings
        if listing.get("active") is True and listing["id"] not in posted
    ]
    new.sort(key=lambda listing: listing["date_posted"])
    return new
