from caseclerk_pipeline.dates import date_query_variants, find_dates


def test_find_dates_month_name_form() -> None:
    assert find_dates("The deposition on April 21, 2026 revealed a conflict.") == ["2026-04-21"]


def test_find_dates_abbreviated_month() -> None:
    assert find_dates("Filed Apr. 21, 2026 with the court.") == ["2026-04-21"]


def test_find_dates_numeric_slash_two_digit_year() -> None:
    assert find_dates("See the note dated 4/21/26.") == ["2026-04-21"]


def test_find_dates_numeric_slash_four_digit_year() -> None:
    assert find_dates("See the note dated 4/21/2026.") == ["2026-04-21"]


def test_find_dates_iso_form() -> None:
    assert find_dates("Entry logged 2026-04-21 at the courthouse.") == ["2026-04-21"]


def test_find_dates_deduplicates_and_preserves_order() -> None:
    text = "Deposition April 21, 2026. Follow-up 4/21/2026. Earlier: January 3, 2026."
    assert find_dates(text) == ["2026-04-21", "2026-01-03"]


def test_find_dates_ignores_invalid_calendar_dates() -> None:
    assert find_dates("Bogus date 13/45/2026 should not parse.") == []


def test_find_dates_no_dates_present() -> None:
    assert find_dates("No dates mentioned anywhere in this note.") == []


def test_date_query_variants_covers_written_forms() -> None:
    variants = date_query_variants("2026-04-21")
    assert "April 21, 2026" in variants
    assert "Apr 21, 2026" in variants
    assert "4/21/26" in variants
    assert "4/21/2026" in variants
    assert "2026-04-21" in variants


def test_date_query_variants_roundtrip_through_find_dates() -> None:
    for variant in date_query_variants("2026-04-21"):
        assert find_dates(f"See {variant} for details.") == ["2026-04-21"]
