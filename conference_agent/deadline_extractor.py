"""Explicit dates only: never fill in a missing year from today's date."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Deadline

MONTHS = {
    name: i
    for i, names in enumerate(
        [
            (),
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for name in names
}
MONTH = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
YEAR = r"(?:19|20)\d{2}"
DEADLINE_LABEL = re.compile(
    r"(?:submission\s+deadline|(?:paper|abstract|extended abstract|full paper)s?\s+(?:submission\s+)?deadline"
    r"|deadline\s+for\s+(?:paper|abstract|submission)s?|(?:paper|abstract)s?\s+due"
    r"|submit\s+(?:papers?\s+)?(?:by|before)|(?:extended|new|final)\s+deadline|deadline)",
    re.I,
)
OTHER_MILESTONE = re.compile(
    r"\b(?:notification|acceptance notification|conference dates?|event dates?|registration deadline"
    r"|camera.ready|early.bird|workshop dates?)\b",
    re.I,
)


def explicit_dates(text: str) -> list[date]:
    """English date formats and ISO; ambiguous numeric dates are not guessed."""
    found: list[tuple[int, date]] = []
    covered: list[tuple[int, int]] = []

    def add(match: re.Match, year: str, month: str | int, days: list[str]) -> None:
        try:
            m = (
                MONTHS[str(month).lower().rstrip(".")]
                if isinstance(month, str) and not month.isdigit()
                else int(month)
            )
            values = [date(int(year), m, int(d)) for d in days]
        except (ValueError, KeyError):
            return
        if any(a <= match.start() < b for a, b in covered):
            return
        found.extend((match.start(), d) for d in values)
        covered.append(match.span())

    patterns = [
        (rf"\b(?P<d>\d{{1,2}})\s*[–—-]\s*(?P<e>\d{{1,2}})\s+(?P<m>{MONTH})\s*,?\s*(?P<y>{YEAR})\b", True),
        (rf"\b(?P<m>{MONTH})\s+(?P<d>\d{{1,2}})\s*[–—-]\s*(?P<e>\d{{1,2}}),?\s+(?P<y>{YEAR})\b", True),
        (rf"\b(?P<y>{YEAR})-(?P<m>\d{{1,2}})-(?P<d>\d{{1,2}})\b", False),
        (rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{MONTH})\s*,?\s*(?P<y>{YEAR})\b", False),
        (rf"\b(?P<m>{MONTH})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>{YEAR})\b", False),
    ]
    for pattern, is_range in patterns:
        for match in re.finditer(pattern, text, re.I):
            add(match, match["y"], match["m"], [match["d"], match["e"]] if is_range else [match["d"]])
    for match in re.finditer(rf"\b(?P<a>\d{{1,2}})/(?P<b>\d{{1,2}})/(?P<y>{YEAR})\b", text):
        a, b = int(match["a"]), int(match["b"])
        if a > 12 >= b:
            add(match, match["y"], b, [str(a)])
        elif b > 12 >= a:
            add(match, match["y"], a, [str(b)])
    return [d for _, d in sorted(found, key=lambda item: item[0])]


def _zone(text: str):
    fixed = {"UTC": 0, "GMT": 0, "CET": 1, "CEST": 2, "EST": -5, "EDT": -4, "PST": -8, "PDT": -7, "AOE": -12}
    match = re.search(r"\b(?:UTC|GMT)\s*([+-])(\d{1,2})(?::(\d{2}))?\b", text, re.I)
    if match:
        hours, minutes = int(match[2]), int(match[3] or 0)
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            return None, None
        offset = timedelta(hours=hours, minutes=minutes) * (1 if match[1] == "+" else -1)
        return match[0], timezone(offset)
    match = re.search(r"\b[A-Za-z_]+/[A-Za-z_]+(?:/[A-Za-z_]+)?\b", text)
    if match:
        try:
            return match[0], ZoneInfo(match[0])
        except ZoneInfoNotFoundError:
            pass
    match = re.search(r"\b(UTC|GMT|CET|CEST|EST|EDT|PST|PDT|AoE|ET|PT|CST|IST|BST)\b", text, re.I)
    if match:
        label = match[0].upper()
        if label in {"ET", "PT"}:
            return label, ZoneInfo("America/New_York" if label == "ET" else "America/Los_Angeles")
        if label in fixed:
            return label, timezone(timedelta(hours=fixed[label]))
        return label, None  # CST/IST/BST are geographically ambiguous.
    if re.search(r"anywhere on earth", text, re.I):
        return "AoE", timezone(timedelta(hours=-12))
    return None, None


def parse_deadline(text: str, source_url: str, kind: str = "submission") -> Deadline:
    dates = explicit_dates(text)
    warnings = []
    if re.search(
        r"\b(?:expected|estimated|tentative|provisional|tbc)\b|to be confirmed|subject to change", text, re.I
    ):
        warnings.append("Deadline is provisional in the source; a confirmed cutoff is not established.")
    if not dates:
        warnings.append("Missing year, unrecognised date, or ambiguous numeric date; no year was inferred.")
    elif len(set(dates)) > 1 and not re.search(r"extended|extension|new deadline", text, re.I):
        warnings.append("Multiple dates in deadline evidence; milestone association is uncertain.")
    selected = (
        max(dates)
        if dates and re.search(r"extended|extension|new deadline", text, re.I)
        else (dates[0] if dates else None)
    )
    if len(set(dates)) > 1 and any("Multiple dates" in warning for warning in warnings):
        selected = None
    tz_label, tz = _zone(text)
    clock_text = re.sub(r"\b(?:UTC|GMT)\s*[+-]\d{1,2}(?::\d{2})?", "", text, flags=re.I)
    match = re.search(r"\b(\d{1,2}):(\d{2})(?:\s*(am|pm))?\b", clock_text, re.I)
    if not match:
        match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", clock_text, re.I)
        groups = (int(match[1]), 0, match[2].lower()) if match else None
    else:
        groups = (int(match[1]), int(match[2]), (match[3] or "").lower())
    clock = None
    if groups:
        h, m, ampm = groups
        if ampm and not 1 <= h <= 12:
            warnings.append("Invalid twelve-hour deadline time.")
        else:
            h = (h % 12 + (12 if ampm == "pm" else 0)) if ampm else h
            try:
                clock = time(h, m)
            except ValueError:
                warnings.append("Invalid deadline time.")
    if tz_label and not tz:
        warnings.append("Timezone abbreviation is ambiguous; no UTC time was inferred.")
    at = datetime.combine(selected, clock, tzinfo=tz) if selected and clock and tz else None
    return Deadline(
        date=selected,
        time=clock.isoformat(timespec="minutes") if clock else None,
        timezone=tz_label,
        at=at,
        raw=text,
        source_url=source_url,
        kind=kind,
        warnings=warnings,
    )


def extract_deadlines(text: str, source_url: str) -> list[Deadline]:
    found = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        matches = list(DEADLINE_LABEL.finditer(line))
        for pos, match in enumerate(matches):
            prefix = line[max(0, match.start() - 40) : match.start()]
            if re.search(r"registration|camera.ready|early.bird|notification", prefix, re.I):
                continue
            end = matches[pos + 1].start() if pos + 1 < len(matches) else len(line)
            segment = line[match.start() : end]
            other = OTHER_MILESTONE.search(segment)
            if other:
                segment = segment[: other.start()]
            # HTML often places a label and its value on neighbouring lines.
            if not explicit_dates(segment) and index + 1 < len(lines):
                following = lines[index + 1]
                if not OTHER_MILESTONE.search(following) and not DEADLINE_LABEL.search(following):
                    segment += " " + following
            if re.search(r"registration|camera.ready|early.bird", segment[:40], re.I):
                continue
            found.append(parse_deadline(segment.strip(" :;–-"), source_url))
    unique = {(x.date, x.at, x.raw): x for x in found}
    return list(unique.values())


def deadline_state(deadline: Deadline, now: datetime) -> str:
    """'future' means definitely future, including when timezone/time is unspecified."""
    if now.tzinfo is None:
        raise ValueError("Verification time must have a timezone.")
    if deadline.date is None:
        return "unknown"
    if deadline.at:
        return "future" if deadline.at > now else "past"
    _, tz = _zone(deadline.raw)
    earliest = datetime.combine(deadline.date, time.min, tzinfo=tz or timezone(timedelta(hours=14)))
    latest = datetime.combine(
        deadline.date + timedelta(days=1), time.min, tzinfo=tz or timezone(timedelta(hours=-12))
    )
    if now < earliest:
        return "future"
    if now >= latest:
        return "past"
    return "uncertain"
