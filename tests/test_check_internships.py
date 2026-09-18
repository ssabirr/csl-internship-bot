from check_internships import find_new_listings, bootstrap_posted_ids, format_embed


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


def test_bootstrap_returns_only_active_ids():
    listings = [
        _listing("a", active=True),
        _listing("b", active=False),
        _listing("c", active=True),
    ]
    assert set(bootstrap_posted_ids(listings)) == {"a", "c"}


def test_bootstrap_empty_source_returns_empty():
    assert bootstrap_posted_ids([]) == []


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
