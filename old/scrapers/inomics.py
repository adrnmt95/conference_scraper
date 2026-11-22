"""Scraper for inomics.com conference listings."""

import re
import time
from bs4 import BeautifulSoup

BASE_URL = "https://inomics.com/top/conferences"


def scrape(session, known_urls=None):
    """Scrape all conferences from inomics.com.

    Returns list of dicts with keys:
        title, conference_dates, location, url, source, page_text
    """
    known_urls = known_urls or set()
    entries = _get_listing_entries(session, known_urls)
    # Only fetch detail pages for new entries
    new_entries = [e for e in entries if e["url"] not in known_urls]
    if len(new_entries) < len(entries):
        print(f"  Skipping {len(entries) - len(new_entries)} already-known URLs")
    conferences = []

    for i, entry in enumerate(new_entries, 1):
        print(f"  [{i}/{len(new_entries)}] {entry['title'][:60]}")

        page_text = _fetch_detail_page(session, entry["url"])
        if page_text is None:
            page_text = ""

        conferences.append({
            "title": entry["title"],
            "conference_dates": entry.get("conference_dates", ""),
            "location": entry.get("location", ""),
            "url": entry["url"],
            "source": "inomics",
            "page_text": page_text[:5000],
        })
        time.sleep(0.5)

    print(f"  Inomics: {len(conferences)} conferences found")
    return conferences


def _get_listing_entries(session, known_urls=None):
    """Paginate through listing pages and extract conference entries."""
    known_urls = known_urls or set()
    all_entries = []
    seen_urls = set()
    page = 0

    while True:
        url = f"{BASE_URL}?page={page}"
        print(f"  Fetching inomics page {page}: {url}")
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
        except Exception as e:
            print(f"    Error on page {page}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        entries_on_page = _parse_listing_page(soup)

        if not entries_on_page:
            break

        new_count = 0
        for entry in entries_on_page:
            if entry["url"] not in seen_urls:
                seen_urls.add(entry["url"])
                all_entries.append(entry)
                new_count += 1

        print(f"    Found {new_count} new entries on page {page}")

        if new_count == 0:
            break

        # Early termination: if all entries on this page are already known, stop
        page_urls = [e["url"] for e in entries_on_page]
        if known_urls and all(u in known_urls for u in page_urls):
            print(f"    All entries on page {page} already known — stopping pagination")
            break

        page += 1
        time.sleep(1)

    print(f"  Total unique inomics entries: {len(all_entries)}")
    return all_entries


def _parse_listing_page(soup):
    """Extract conference entries from a single listing page."""
    entries = []

    # Find all conference links (both featured and regular listings)
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        if not re.match(r"/conference/[\w-]+-\d+$", href):
            continue

        # Skip if this is a tiny navigation link (not a listing entry)
        h2 = a_tag.find("h2")
        if not h2:
            continue

        title = h2.get_text(strip=True)
        full_url = f"https://inomics.com{href}"

        # Try to extract dates and location from the listing entry
        dates = ""
        location = ""

        info_span = a_tag.find("span", class_="informations")
        if info_span:
            # Dates: "Between <bold>15 May</bold> and <bold>16 May</bold>"
            info_text = info_span.get_text(separator=" ", strip=True)
            date_match = re.search(
                r"Between\s+(.+?)\s+and\s+(.+?)(?:\s+in\s+|$)", info_text
            )
            if date_match:
                dates = f"{date_match.group(1).strip()} - {date_match.group(2).strip()}"

            # Location: <span class="location bold">Barcelona, Spain</span>
            loc_span = info_span.find("span", class_="location")
            if loc_span:
                location = loc_span.get_text(strip=True)

        entries.append({
            "title": title,
            "conference_dates": dates,
            "location": location,
            "url": full_url,
        })

    return entries


def _fetch_detail_page(session, url):
    """Fetch an inomics detail page and return its text content."""
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"    Error fetching {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Get structured fields from post-details
    details = {}
    detail_div = soup.find("div", class_="post-details")
    if detail_div:
        for div in detail_div.find_all("div", recursive=False):
            label_span = div.find("span", class_=["detail-title", "detail-attendance"])
            h4 = div.find("h4")
            if label_span and h4:
                key = label_span.get_text(strip=True)
                val = h4.get_text(strip=True)
                details[key] = val

    # Get the main body content
    post_body = soup.find("div", class_=lambda c: c and "post-body" in " ".join(
        c if isinstance(c, list) else [c]
    ))
    body_text = ""
    if post_body:
        body_text = post_body.get_text(separator="\n", strip=True)

    # Combine all structured details + body text
    parts = []
    for key, val in details.items():
        parts.append(f"{key}: {val}")
    if body_text:
        parts.append(body_text)

    return "\n".join(parts) if parts else None
