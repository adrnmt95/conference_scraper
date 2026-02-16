"""
Multi-source conference scraper with deduplication.

Scrapes conferences from multiple sources, deduplicates them,
classifies new ones via OpenAI, and writes to Excel.

Usage:
  python run.py
  python run.py --include "applied econ, political economy" --exclude "finance"
"""

import argparse
import importlib
import pkgutil
import re
import warnings
import time
from datetime import date

import requests

import scrapers
from dedup import deduplicate, normalize_title
from classify import extract_with_openai, check_relevance
from excel_writer import (
    load_existing_xlsx,
    parse_deadline_date,
    write_to_excel,
)

warnings.filterwarnings("ignore", category=DeprecationWarning)

TODAY = date.today()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape conferences from multiple sources"
    )
    parser.add_argument(
        "--include",
        type=str,
        default=None,
        help='Comma-separated topics to actively seek (e.g. "applied econ, political economy")',
    )
    parser.add_argument(
        "--exclude",
        type=str,
        default=None,
        help='Comma-separated topics to actively exclude (e.g. "macro-finance, economic theory")',
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print detailed reasoning for include/exclude decisions",
    )
    parser.add_argument(
        "--scrapers",
        type=str,
        default=None,
        help='Comma-separated scraper names to run (e.g. "inomics,misfit"). Default: all',
    )
    return parser.parse_args()


def _make_session():
    """Create a requests session with retries and a browser-like user agent."""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        max_retries=requests.adapters.Retry(
            total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504]
        )
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })
    return session


def main():
    args = parse_args()
    include_topics = args.include
    exclude_topics = args.exclude
    debug = args.debug
    filtering = include_topics or exclude_topics

    print("=" * 60)
    print("Conference Scraper - Multi-source")
    print(f"Today's date: {TODAY}")
    if include_topics:
        print(f"Include: {include_topics}")
    if exclude_topics:
        print(f"Exclude: {exclude_topics}")
    if not filtering:
        print("No filters — all conferences will be included")
    print("=" * 60)

    # --- Step 0: Load existing conferences from Excel ---
    known_titles, existing_active, existing_past, known_urls = load_existing_xlsx()
    print(f"\nAlready in Excel: {len(existing_active)} active, {len(existing_past)} past")

    # Move past-deadline conferences from active to past
    still_active = []
    newly_past = []
    for conf in existing_active:
        dl = conf.get("deadline_date")
        if dl and not isinstance(dl, str) and dl < TODAY:
            print(f"  Deadline passed: {conf['title'][:60]}")
            newly_past.append(conf)
        else:
            still_active.append(conf)

    if newly_past:
        print(f"  Moved {len(newly_past)} conferences to Past Conferences")

    excluded_reasons = []

    # --- Step 1: Scrape all sources (auto-discovered from scrapers/) ---
    selected = None
    if args.scrapers:
        selected = {s.strip().lower() for s in args.scrapers.split(",")}
    print("\n[1/5] Scraping conferences from all sources...")
    session = _make_session()

    all_scraped = []
    for finder, name, _ in pkgutil.iter_modules(scrapers.__path__):
        if selected and name.lower() not in selected:
            continue
        mod = importlib.import_module(f"scrapers.{name}")
        if not hasattr(mod, "scrape"):
            continue
        print(f"\n--- {name} ---")
        confs = mod.scrape(session, known_urls=known_urls)
        print(f"  {name}: {len(confs)} conferences")
        all_scraped.extend(confs)

    print(f"\nTotal scraped: {len(all_scraped)}")

    # --- Step 2: Deduplicate ---
    print("\n[2/5] Deduplicating...")
    unique_confs = deduplicate(all_scraped, existing_titles=known_titles)

    # --- Step 3: Classify via OpenAI ---
    print(f"\n[3/5] Classifying {len(unique_confs)} new conferences via OpenAI...")
    new_conferences = []

    for i, conf in enumerate(unique_confs, 1):
        title = conf["title"]
        page_text = conf.get("page_text", "")
        print(f"  [{i}/{len(unique_confs)}] {title[:60]}")

        if not page_text:
            print(f"    -> No page text, skipping")
            continue

        # If filtering, check relevance first (cheap call)
        if filtering:
            print(f"    -> Checking relevance...")
            relevant, reason, detected_topics = check_relevance(
                title, page_text, include_topics, exclude_topics
            )
            if debug:
                decision = "INCLUDE" if relevant else "EXCLUDE"
                print(f"    [DEBUG] {decision}: {reason}")
                print(f"    [DEBUG] Detected topics: {detected_topics}")
            if not relevant:
                print(f"    -> Not relevant, skipping")
                excluded_reasons.append((title, f"not relevant: {reason}"))
                continue

        # Extract structured fields
        print(f"    -> Extracting details...")
        extracted = extract_with_openai(title, page_text)

        # Post-process: skip conferences with expired/closed deadlines
        sub_dl = extracted.get("submission_deadline", "")
        dl_date = extracted.get("deadline_date", "")
        _expired = re.compile(r"expired|passed|closed", re.IGNORECASE)
        if _expired.search(sub_dl) or _expired.search(dl_date):
            print(f"    -> Deadline expired/closed, skipping")
            excluded_reasons.append((title, "deadline expired/closed"))
            continue

        # Clear non-date placeholders but keep the conference (user checks manually)
        _placeholder = re.compile(r"tba|to be announced|n/a", re.IGNORECASE)
        if _placeholder.search(sub_dl):
            sub_dl = ""
        if _placeholder.search(dl_date):
            dl_date = ""

        deadline_date = parse_deadline_date(dl_date)

        new_conf = {
            "title": title,
            "url": conf["url"],
            "submission_deadline": sub_dl,
            "deadline_date": deadline_date,
            "conference_dates": extracted.get("conference_dates", ""),
            "location": extracted.get("location", ""),
            "keynote_speakers": extracted.get("keynote_speakers", ""),
            "description": extracted.get("description", ""),
            "topics": extracted.get("topics", ""),
        }
        new_conferences.append(new_conf)
        time.sleep(0.5)

    print(f"\n  Classified: {len(new_conferences)} new conferences")

    # --- Step 4: Filter by deadline ---
    print("\n[4/5] Filtering by deadline...")
    filtered_new = []

    for conf in new_conferences:
        dl = conf.get("deadline_date")
        if dl and not isinstance(dl, str) and dl < TODAY:
            excluded_reasons.append((conf["title"], "deadline passed"))
            newly_past.append(conf)
            continue
        filtered_new.append(conf)

    # Combine existing active + filtered new
    all_active = still_active + filtered_new

    # Final title-based dedup (in case OpenAI returned slightly different titles)
    deduped = []
    for conf in all_active:
        norm = normalize_title(conf["title"])
        is_dup = False
        for j, existing_conf in enumerate(deduped):
            existing_norm = normalize_title(existing_conf["title"])
            shorter = min(len(norm), len(existing_norm))
            if shorter >= 15 and (
                norm.startswith(existing_norm[:shorter])
                or existing_norm.startswith(norm[:shorter])
            ):
                is_dup = True
                dl_new = conf.get("deadline_date")
                dl_old = existing_conf.get("deadline_date")
                if dl_new and not dl_old:
                    deduped[j] = conf
                elif len(conf.get("description", "")) > len(
                    existing_conf.get("description", "")
                ):
                    deduped[j] = conf
                break
        if not is_dup:
            deduped.append(conf)

    all_active = deduped

    # Sort: conferences with known deadlines first (by date), then those without
    with_deadline = [
        c for c in all_active
        if c.get("deadline_date") and not isinstance(c["deadline_date"], str)
    ]
    without_deadline = [
        c for c in all_active
        if not c.get("deadline_date") or isinstance(c["deadline_date"], str)
    ]
    with_deadline.sort(key=lambda c: c["deadline_date"])
    final_active = with_deadline + without_deadline

    # Rebuild past list
    all_past = existing_past + newly_past
    all_past_with_dl = [
        c for c in all_past
        if c.get("deadline_date") and not isinstance(c["deadline_date"], str)
    ]
    all_past_no_dl = [
        c for c in all_past
        if not c.get("deadline_date") or isinstance(c["deadline_date"], str)
    ]
    all_past_with_dl.sort(key=lambda c: c["deadline_date"], reverse=True)
    seen_past = set()
    unique_past = []
    for c in all_past_with_dl + all_past_no_dl:
        tn = normalize_title(c["title"])
        if tn not in seen_past:
            seen_past.add(tn)
            unique_past.append(c)
    final_past = unique_past[:10]

    print(f"  Active: {len(final_active)} conferences")
    print(f"  Past (kept): {len(final_past)} conferences")
    print(f"  Excluded: {len(excluded_reasons)} conferences")
    if excluded_reasons:
        print("\n  Excluded conferences:")
        for title, reason in excluded_reasons:
            print(f"    - {title[:70]} [{reason}]")

    # --- Step 5: Write to Excel ---
    print(f"\n[5/5] Writing {len(final_active)} active + {len(final_past)} past conferences...")
    write_to_excel(final_active, final_past)

    print("\nDone!")
    print(f"  Active:  {len(final_active)} conferences")
    print(f"  Past:    {len(final_past)} conferences (10 most recent)")


if __name__ == "__main__":
    main()
