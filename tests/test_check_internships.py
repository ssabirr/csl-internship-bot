from check_internships import (
    DESCRIPTION_MAX_LEN,
    TITLE_MAX_LEN,
    bootstrap_posted_ids,
    bootstrap_posted_keys,
    chunked,
    dedup_key,
    escape_markdown,
    find_new_listings,
    format_embed,
    load_posted_ids,
    save_posted_ids,
    truncate,
    validate_listing,
)


def _listing(id_, active=True, date_posted=1700000000):
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


# --- is_visible filtering -------------------------------------------------


def test_is_visible_false_excluded_from_new_listings():
    listing = _listing("a")
    listing["is_visible"] = False
    assert find_new_listings([listing], []) == []


def test_is_visible_true_included_in_new_listings():
    listing = _listing("a")
    listing["is_visible"] = True
    assert [l["id"] for l in find_new_listings([listing], [])] == ["a"]


def test_missing_is_visible_treated_as_visible():
    listing = _listing("a")
    assert "is_visible" not in listing
    assert [l["id"] for l in find_new_listings([listing], [])] == ["a"]


def test_bootstrap_excludes_is_visible_false():
    a = _listing("a")
    b = _listing("b")
    b["is_visible"] = False
    c = _listing("c")
    c["is_visible"] = True
    assert set(bootstrap_posted_ids([a, b, c])) == {"a", "c"}


# --- cross-source dedup ----------------------------------------------------


def test_dedup_key_normalizes_case_and_whitespace():
    a = _listing("a")
    a["company_name"] = "  Acme Corp  "
    a["title"] = "Software Intern"
    a["url"] = "https://example.com/Job"
    b = _listing("b")
    b["company_name"] = "acme corp"
    b["title"] = "SOFTWARE INTERN"
    b["url"] = "HTTPS://EXAMPLE.COM/JOB"
    assert dedup_key(a) == dedup_key(b)


def test_dedup_key_differs_for_different_jobs():
    a = _listing("a")
    b = _listing("b")
    b["company_name"] = "Different Co"
    assert dedup_key(a) != dedup_key(b)


def test_find_new_listings_excludes_by_posted_key_even_with_new_id():
    listing = _listing("brand-new-id")
    key = dedup_key(listing)
    assert find_new_listings([listing], [], [key]) == []


def test_find_new_listings_posted_keys_defaults_to_empty():
    listing = _listing("a")
    assert [l["id"] for l in find_new_listings([listing], [])] == ["a"]


def test_bootstrap_posted_keys_returns_keys_for_active_visible_listings():
    a = _listing("a")
    b = _listing("b", active=False)
    keys = bootstrap_posted_keys([a, b])
    assert keys == [dedup_key(a)]


def test_bootstrap_posted_keys_dedupes_identical_jobs_across_sources():
    a = _listing("a")
    b = _listing("b")  # same company/title/url as "a", different id
    assert bootstrap_posted_keys([a, b]) == [dedup_key(a)]


# --- state load/save (legacy migration) -------------------------------------


def test_load_posted_ids_returns_none_when_missing(tmp_path):
    assert load_posted_ids(str(tmp_path / "missing.json")) is None


def test_load_posted_ids_migrates_legacy_flat_list(tmp_path):
    path = tmp_path / "posted.json"
    path.write_text('["a", "b", "c"]', encoding="utf-8")
    state = load_posted_ids(str(path))
    assert state == {"ids": ["a", "b", "c"], "keys": []}


def test_load_posted_ids_reads_new_dict_format(tmp_path):
    path = tmp_path / "posted.json"
    path.write_text('{"ids": ["a"], "keys": ["k1"]}', encoding="utf-8")
    state = load_posted_ids(str(path))
    assert state == {"ids": ["a"], "keys": ["k1"]}


def test_save_then_load_round_trips(tmp_path):
    path = str(tmp_path / "posted.json")
    save_posted_ids(path, {"ids": ["b", "a"], "keys": ["k2", "k1"]})
    assert load_posted_ids(path) == {"ids": ["a", "b"], "keys": ["k1", "k2"]}


# --- validation -----------------------------------------------------------


def test_validate_accepts_complete_listing():
    assert validate_listing(_listing("a")) is None


def test_validate_rejects_each_missing_required_field():
    for field in ("id", "title", "url", "date_posted", "company_name"):
        listing = _listing("a")
        del listing[field]
        reason = validate_listing(listing)
        assert reason is not None, f"{field} should be required"
        assert field in reason


def test_validate_rejects_empty_string_field():
    listing = _listing("a")
    listing["title"] = "   "
    assert "title" in validate_listing(listing)


def test_validate_rejects_non_numeric_date_posted():
    listing = _listing("a")
    listing["date_posted"] = "yesterday"
    assert "date_posted" in validate_listing(listing)


def test_validate_rejects_out_of_range_date_posted():
    listing = _listing("a")
    listing["date_posted"] = 1700000000000  # milliseconds, not seconds
    assert "date_posted" in validate_listing(listing)


def test_validate_rejects_negative_date_posted():
    listing = _listing("a")
    listing["date_posted"] = -1
    assert "date_posted" in validate_listing(listing)


def test_validate_accepts_date_posted_at_range_boundaries():
    listing = _listing("a")
    listing["date_posted"] = 946684800  # 2000-01-01
    assert validate_listing(listing) is None
    listing["date_posted"] = 4102444800  # 2100-01-01
    assert validate_listing(listing) is None


def test_validate_rejects_non_dict():
    assert validate_listing("not a listing") is not None


def test_validate_accepts_oversized_title_and_description():
    # Length alone is never a rejection -- format_embed truncates.
    listing = _listing("a")
    listing["title"] = "x" * (TITLE_MAX_LEN + 50)
    listing["company_name"] = "y" * (DESCRIPTION_MAX_LEN + 50)
    assert validate_listing(listing) is None


def test_validate_allows_missing_locations():
    listing = _listing("a")
    del listing["locations"]
    assert validate_listing(listing) is None


# --- truncation / formatting fallbacks ------------------------------------


def test_truncate_leaves_short_text_alone():
    assert truncate("hello", 10) == "hello"


def test_truncate_shortens_long_text_to_limit():
    assert len(truncate("x" * 500, 256)) == 256


def test_format_embed_truncates_oversized_title():
    listing = _listing("a")
    listing["title"] = "x" * (TITLE_MAX_LEN + 50)
    assert len(format_embed(listing)["title"]) == TITLE_MAX_LEN


def test_format_embed_truncates_oversized_description():
    listing = _listing("a")
    listing["company_name"] = "y" * (DESCRIPTION_MAX_LEN + 500)
    assert len(format_embed(listing)["description"]) == DESCRIPTION_MAX_LEN


def test_format_embed_handles_missing_locations():
    listing = _listing("a")
    del listing["locations"]
    embed = format_embed(listing)
    assert "Acme" in embed["description"]


def test_format_embed_handles_non_list_locations():
    listing = _listing("a")
    listing["locations"] = None
    assert "Acme" in format_embed(listing)["description"]


# --- markdown escaping ----------------------------------------------------


def test_escape_markdown_escapes_special_characters():
    assert escape_markdown("[x](y)") == "\\[x\\]\\(y\\)"
    assert escape_markdown("a*b_c~d`e|f>g") == "a\\*b\\_c\\~d\\`e\\|f\\>g"


def test_escape_markdown_escapes_backslash():
    assert escape_markdown("a\\b") == "a\\\\b"


def test_escape_markdown_leaves_plain_text_alone():
    assert escape_markdown("Acme Corp, Inc.") == "Acme Corp, Inc."


def test_format_embed_escapes_company_name():
    listing = _listing("a")
    listing["company_name"] = "[Evil](https://phish.example)"
    description = format_embed(listing)["description"]
    assert "[Evil](https://phish.example)" not in description
    assert "\\[Evil\\]" in description


# --- batching -------------------------------------------------------------


def test_chunked_splits_25_into_10_10_5():
    batches = chunked(list(range(25)), 10)
    assert [len(b) for b in batches] == [10, 10, 5]


def test_chunked_exact_multiple():
    assert [len(b) for b in chunked(list(range(20)), 10)] == [10, 10]


def test_chunked_fewer_than_size():
    assert chunked([1, 2, 3], 10) == [[1, 2, 3]]


def test_chunked_empty():
    assert chunked([], 10) == []


def test_chunked_preserves_order_and_contents():
    items = list(range(25))
    assert [x for batch in chunked(items, 10) for x in batch] == items
