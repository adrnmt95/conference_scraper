from datetime import date, datetime, timezone

import pytest

from conference_agent.deadline_extractor import (
    deadline_state,
    explicit_dates,
    extract_deadlines,
    parse_deadline,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("15 January 2027", [date(2027, 1, 15)]),
        ("January 15th, 2027", [date(2027, 1, 15)]),
        ("2027-01-15", [date(2027, 1, 15)]),
        ("15/01/2027", [date(2027, 1, 15)]),
        ("01/15/2027", [date(2027, 1, 15)]),
        ("1–3 June 2027", [date(2027, 6, 1), date(2027, 6, 3)]),
        ("June 1-3, 2027", [date(2027, 6, 1), date(2027, 6, 3)]),
        ("04/05/2027", []),
        ("January 15", []),
        ("2027-02-29", []),
        ("31 February 2027", []),
    ],
)
def test_explicit_dates(text, expected):
    assert explicit_dates(text) == expected


def test_submission_and_notification_are_separate():
    text = (
        "Submission deadline: 15 January 2027; Notification: 1 March 2027\nRegistration deadline: 5 May 2027"
    )
    deadlines = extract_deadlines(text, "https://example.edu/cfp")
    assert len(deadlines) == 1
    assert deadlines[0].date == date(2027, 1, 15)


def test_html_neighbouring_lines():
    assert extract_deadlines("Submission deadline:\n15 January 2027", "https://example.edu")[0].date == date(
        2027, 1, 15
    )


def test_missing_year_stays_unknown():
    result = parse_deadline("Submission deadline: January 15", "https://example.edu")
    assert result.date is None
    assert result.at is None
    assert result.warnings


def test_same_day_requires_precise_cutoff(now):
    unknown = parse_deadline("Submission deadline: 2026-11-04", "https://example.edu")
    precise = parse_deadline("Submission deadline: 2026-11-04 23:59 UTC", "https://example.edu")
    passed = parse_deadline("Submission deadline: 2026-11-04 09:00 UTC", "https://example.edu")
    assert deadline_state(unknown, now) == "uncertain"
    assert deadline_state(precise, now) == "future"
    assert deadline_state(passed, now) == "past"


def test_ambiguous_timezone_not_made_precise(now):
    deadline = parse_deadline("Submission deadline: 2026-11-04 23:59 CST", "https://example.edu")
    assert deadline.timezone == "CST"
    assert deadline.at is None
    assert deadline_state(deadline, now) == "uncertain"


@pytest.mark.parametrize(
    "text, hour",
    [
        ("2027-01-15 23:00 CET", 22),
        ("2027-07-15 23:00 ET", 3),
        ("2027-01-15 11 pm UTC", 23),
        ("2027-01-15 12:00 am UTC", 0),
        ("2027-01-15 23:00 Europe/Zurich", 22),
        ("2027-01-15 23:00 UTC+02:00", 21),
    ],
)
def test_explicit_timezones(text, hour):
    deadline = parse_deadline(text, "https://example.edu")
    assert deadline.at.astimezone(timezone.utc).hour == hour


def test_anywhere_on_earth_is_explicit():
    deadline = parse_deadline("2026-11-04 23:59 AoE", "https://example.edu")
    assert deadline.at.astimezone(timezone.utc).date() == date(2026, 11, 5)


def test_timezone_uncertainty_can_span_next_utc_day():
    deadline = parse_deadline("2026-11-04", "https://example.edu")
    assert deadline_state(deadline, datetime(2026, 11, 5, 2, tzinfo=timezone.utc)) == "uncertain"
    assert deadline_state(deadline, datetime(2026, 11, 6, tzinfo=timezone.utc)) == "past"


def test_extension_does_not_mix_notification():
    deadline = extract_deadlines(
        "Submission deadline extended from January 1, 2027 to January 15, 2027; Notification: February 1, 2027",
        "https://example.edu",
    )[0]
    assert deadline.date == date(2027, 1, 15)


def test_distinct_dates_without_extension_are_uncertain():
    deadline = parse_deadline("January 1, 2027 or January 15, 2027", "https://example.edu")
    assert deadline.date is None


def test_timezone_offset_does_not_become_a_cutoff_time():
    deadline = parse_deadline("Submission deadline: 2027-01-15 UTC+02:00", "https://example.edu")
    assert deadline.time is None
    assert deadline.at is None
