"""Deduplication logic for conferences from multiple sources.

Matches conferences by normalized date+location, with fuzzy title fallback.
No OpenAI calls — pure string matching.
"""

import re
from datetime import datetime


def deduplicate(conferences, existing_titles=None):
    """Remove duplicate conferences from the list.

    Args:
        conferences: list of raw conference dicts (from scrapers)
        existing_titles: set of normalized titles already in Excel (to skip)

    Returns:
        list of unique conference dicts
    """
    if existing_titles is None:
        existing_titles = set()

    # First pass: remove conferences already in Excel
    new_confs = []
    for conf in conferences:
        norm_title = normalize_title(conf["title"])
        if norm_title in existing_titles:
            print(f"    -> Already in Excel: {conf['title'][:60]}")
            continue
        new_confs.append(conf)

    # Second pass: deduplicate among scraped conferences
    # Group by (date_key, location_key) for exact matches
    groups = {}
    ungrouped = []

    for conf in new_confs:
        date_key = _normalize_dates(conf.get("conference_dates", ""))
        loc_key = _normalize_location(conf.get("location", ""))

        if date_key and loc_key:
            key = (date_key, loc_key)
            groups.setdefault(key, []).append(conf)
        else:
            ungrouped.append(conf)

    # Within each date+location group, pick the best entry
    deduped = []
    for key, group in groups.items():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            # Multiple conferences at same date+location — check title similarity
            merged = _pick_best(group)
            deduped.append(merged)

    # For ungrouped conferences, check title similarity against everything
    for conf in ungrouped:
        if not _is_title_duplicate(conf, deduped):
            deduped.append(conf)

    print(f"  Dedup: {len(conferences)} raw -> {len(deduped)} unique (skipped {len(conferences) - len(deduped) - (len(conferences) - len(new_confs))} dupes, {len(conferences) - len(new_confs)} already in Excel)")
    return deduped


def normalize_title(title):
    """Normalize a title for comparison (lowercase, strip non-alphanumeric)."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _normalize_dates(date_str):
    """Normalize date string to a comparable key.

    Tries to extract start month+day for grouping.
    Returns a tuple like ('2026-05-15',) or None if unparseable.
    """
    if not date_str:
        return None

    date_str = date_str.strip()

    # Try ISO format: "2026-05-15"
    iso_match = re.search(r"(\d{4}-\d{2}-\d{2})", date_str)
    if iso_match:
        return iso_match.group(1)

    # Try "15 May" or "May 15" patterns (with optional year)
    month_names = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
        "nov": "11", "dec": "12",
    }

    # "May 15-16, 2026" or "May 15, 2026" or "15 May 2026"
    for pattern in [
        r"(\w+)\s+(\d{1,2})(?:\s*[-–]\s*\d{1,2})?,?\s*(\d{4})?",
        r"(\d{1,2})\s+(\w+)\s*(\d{4})?",
    ]:
        match = re.search(pattern, date_str, re.IGNORECASE)
        if match:
            g = match.groups()
            if g[0].isdigit():
                day, month_name, year = g[0], g[1], g[2]
            else:
                month_name, day, year = g[0], g[1], g[2]

            month_num = month_names.get(month_name.lower())
            if month_num:
                year = year or "2026"
                return f"{year}-{month_num}-{int(day):02d}"

    return None


def _normalize_location(location):
    """Normalize location string to a comparable key.

    Extracts city+country, lowercased and stripped.
    """
    if not location:
        return None

    loc = location.lower().strip()
    # Remove institution names, keep city/country
    # Common pattern: "City, Country" or "Institution, City, Country"
    parts = [p.strip() for p in loc.split(",")]
    if len(parts) >= 2:
        # Take last two parts (likely city, country)
        return ",".join(parts[-2:]).strip()
    return loc if len(loc) > 2 else None


def _pick_best(group):
    """From a group of similar conferences, pick the one with richest info."""
    best = group[0]
    for conf in group[1:]:
        # Prefer longer page_text (more detail)
        if len(conf.get("page_text", "")) > len(best.get("page_text", "")):
            best = conf
    return best


def _is_title_duplicate(conf, existing):
    """Check if conf's title is similar to any in existing list."""
    norm = normalize_title(conf["title"])
    if len(norm) < 10:
        return False

    for other in existing:
        other_norm = normalize_title(other["title"])
        # Check prefix overlap
        shorter = min(len(norm), len(other_norm))
        if shorter >= 15:
            if norm.startswith(other_norm[:shorter]) or other_norm.startswith(norm[:shorter]):
                return True
        # Check token overlap
        if _token_overlap(norm, other_norm) > 0.8:
            return True

    return False


def _token_overlap(a, b):
    """Compute token-level Jaccard similarity between two normalized strings."""
    # Re-tokenize (split on runs of letters/digits)
    tokens_a = set(re.findall(r"[a-z0-9]+", a))
    tokens_b = set(re.findall(r"[a-z0-9]+", b))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)
