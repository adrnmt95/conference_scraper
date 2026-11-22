"""Scraper for theeconomicmisfit.com conference listings."""

import re
import time
from bs4 import BeautifulSoup

BASE_URL = "https://theeconomicmisfit.com/category/conferences/"


def scrape(session, known_urls=None):
    """Scrape all conferences from theeconomicmisfit.com.

    Returns list of dicts with keys:
        title, conference_dates, location, url, source, page_text
    """
    known_urls = known_urls or set()
    links = _get_all_conference_links(session, known_urls)
    # Skip detail fetch for already-known URLs
    new_links = [l for l in links if l not in known_urls]
    if len(new_links) < len(links):
        print(f"  Skipping {len(links) - len(new_links)} already-known URLs")
    conferences = []
    for i, link in enumerate(new_links, 1):
        slug = link.split("/")[-2][:60]
        print(f"  [{i}/{len(new_links)}] {slug}")

        title, page_text = _fetch_page_text(session, link)
        if not title or not page_text:
            continue

        # Lightweight regex extraction for dedup (dates + location)
        dates = _extract_dates_from_text(page_text)
        location = _extract_location_from_text(page_text)

        conferences.append({
            "title": title,
            "conference_dates": dates,
            "location": location,
            "url": link,
            "source": "misfit",
            "page_text": page_text[:5000],
        })
        time.sleep(0.5)

    print(f"  Misfit: {len(conferences)} conferences found")
    return conferences


def _get_all_conference_links(session, known_urls=None):
    """Paginate through all listing pages and collect conference URLs."""
    known_urls = known_urls or set()
    all_links = []
    page = 1
    while True:
        url = BASE_URL if page == 1 else f"{BASE_URL}page/{page}/"
        print(f"  Fetching listing page {page}: {url}")
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
        except Exception as e:
            print(f"    Error on page {page}: {e}")
            if page > 1:
                time.sleep(5)
                try:
                    resp = session.get(url, timeout=90)
                    resp.raise_for_status()
                except Exception:
                    break
            else:
                break

        soup = BeautifulSoup(resp.text, "html.parser")
        links_found = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if re.match(
                r"https://theeconomicmisfit\.com/\d{4}/\d{2}/\d{2}/[\w-]+/?$", href
            ):
                links_found.add(href)

        if not links_found:
            break

        all_links.extend(links_found)
        print(f"    Found {len(links_found)} conference links on page {page}")

        # Early termination: if all links on this page are already known, stop
        if known_urls and all(link in known_urls for link in links_found):
            print(f"    All links on page {page} already known — stopping pagination")
            break

        page += 1
        time.sleep(1)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for link in all_links:
        if link not in seen:
            seen.add(link)
            unique.append(link)

    print(f"  Total unique misfit links: {len(unique)}")
    return unique


def _fetch_page_text(session, url):
    """Fetch a conference page and return (title, full_text)."""
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"    Error fetching {url}: {e}")
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("h1") or soup.find("h2", class_=re.compile("title"))
    title = title_tag.get_text(strip=True) if title_tag else ""

    content_div = (
        soup.find("div", class_=re.compile("entry-content"))
        or soup.find("div", class_=re.compile("post-content"))
        or soup.find("article")
    )

    if not content_div:
        return title, None

    full_text = content_div.get_text(separator="\n", strip=True)
    return title, full_text


def _extract_dates_from_text(text):
    """Lightweight regex to pull conference dates from page text."""
    # Look for patterns like "Date: September 4-5, 2026" or "September 4-5, 2026"
    patterns = [
        r"(?:Date|Conference date|When)[:\s]*(.+?\d{4})",
        r"(\w+ \d{1,2}[-–]\d{1,2},?\s*\d{4})",
        r"(\w+ \d{1,2},?\s*\d{4}\s*[-–]\s*\w+ \d{1,2},?\s*\d{4})",
        r"(\d{1,2}[-–]\d{1,2}\s+\w+\s+\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_location_from_text(text):
    """Lightweight regex to pull location from page text."""
    patterns = [
        r"(?:Location|Venue|Where|Place)[:\s]*(.+?)(?:\n|$)",
        r"(?:held (?:in|at))\s+(.+?)(?:\n|\.|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            loc = match.group(1).strip()
            # Clean up — take first line only, max 100 chars
            loc = loc.split("\n")[0][:100]
            return loc
    return ""
