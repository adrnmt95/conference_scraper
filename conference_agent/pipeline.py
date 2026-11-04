from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

import httpx

from .memory import ConferenceMemory
from .models import (
    Candidate,
    Evidence,
    PaperProfile,
    Preferences,
    Researcher,
    RunResult,
    SearchHit,
    SearchQuery,
    Source,
    Status,
    utcnow,
)
from .network import FetchError
from .query_generator import researcher_queries
from .ranking import rank, sort_candidates
from .researchers import discover_researchers
from .search_provider import SearchManager
from .source_verifier import SourceVerifier, classify, compact, deduplicate
from .urls import hostname, normalize_url

if TYPE_CHECKING:
    from .llm import LLM

logger = logging.getLogger(__name__)


class Fetcher(Protocol):
    def fetch(self, url: str) -> Source: ...


@dataclass
class Budgets:
    max_queries: int = 36
    results_per_query: int = 5
    max_sources: int = 40
    followup_queries: int = 8
    researcher_queries: int = 6


def watchlist_for(candidates: list[Candidate], now: datetime) -> list[Candidate]:
    grouped: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        grouped.setdefault(ConferenceMemory.identity(candidate), []).append(candidate)
    watchlist = []
    for group in grouped.values():
        if not any(c.recurring for c in group):
            continue
        if any(
            c.official_source_urls and c.active_cfp and c.edition_year and c.status != Status.CLOSED
            for c in group
        ):
            continue
        template = max(group, key=lambda c: c.edition_year or 0).model_copy(deep=True)
        template.id = hashlib.sha256(
            (ConferenceMemory.identity(template) + "|watchlist").encode()
        ).hexdigest()[:16]
        template.last_observed_edition = template.edition_year
        template.name = re.sub(r"\s*\b(?:19|20)\d{2}\b", "", template.name).strip()
        template.edition_year = None
        template.event_start = template.event_end = None
        template.deadline = None
        template.notification_date = None
        template.notification_is_estimate = None
        template.submission_url = None
        template.submission_type = None
        template.active_cfp = template.open_evidence = template.explicitly_closed = False
        template.status = Status.WATCHLIST
        template.ranking = None
        template.verified_at = now
        template.verification_notes.append(
            f"Series watchlist only. Last observed edition: {template.last_observed_edition or 'unknown'}. "
            "No current open CFP established; historical dates were cleared, not projected forward."
        )
        watchlist.append(template)
    return watchlist


def _priority(hit: SearchHit, paper: PaperProfile) -> float:
    text = (hit.title + " " + hit.snippet).casefold()
    score = sum(3 for term in ["call for papers", "workshop", "conference", "submission"] if term in text)
    score += sum(2 for term in paper.topics + paper.communities if term.casefold() in text)
    if any(x in hostname(hit.url) for x in [".edu", ".ac.", "cepr.org", "nber.org", "ecpr.eu", "iza.org"]):
        score += 2
    return score


def run_discovery(
    *,
    paper: PaperProfile,
    preferences: Preferences,
    now: datetime,
    queries: list[SearchQuery],
    search: SearchManager,
    fetcher: Fetcher,
    verifier: SourceVerifier,
    memory: ConferenceMemory,
    budgets: Budgets,
    mode: str = "live",
    llm: LLM | None = None,
) -> RunResult:
    result = RunResult(
        run_id=hashlib.sha256(now.isoformat().encode()).hexdigest()[:12],
        started_at=now,
        mode=mode,
        paper=paper,
        preferences=preferences,
        providers=[p.name for p in search.providers],
    )
    result.researchers = [
        Researcher(name=name, connection="Researcher supplied by the user.", supplied_by_user=True)
        for name in preferences.researchers
    ]
    all_hits: dict[str, SearchHit] = {}
    fetched: dict[str, Source] = {}
    attempted: set[str] = set()
    extracted: list[Candidate] = []
    follow_links: list[tuple[Source, Candidate, str, str]] = []
    source_candidates: dict[str, Candidate] = {}
    followed_links = 0

    def do_search(query: SearchQuery) -> None:
        if query.query not in {q.query for q in result.queries}:
            result.queries.append(query)
            for hit in search.search(query.query, budgets.results_per_query):
                all_hits.setdefault(hit.url, hit)

    seeds = memory.seeds()
    planned = (
        queries[:8]
        + [
            SearchQuery(
                family="conference_memory",
                query=f"{seed['name']} official call papers {now.year} {preferences.event_before.year}",
            )
            for seed in seeds[:5]
        ]
        + queries[8:]
    )
    for query in planned[: budgets.max_queries]:
        do_search(query)
    for seed in seeds:
        all_hits.setdefault(
            seed["url"],
            SearchHit(
                title=seed["name"],
                url=seed["url"],
                provider="conference_memory",
                query="Recheck previous public source; no current status assumed.",
            ),
        )

    def fetch_url(url: str) -> Candidate | None:
        if len(attempted) >= budgets.max_sources:
            return None
        try:
            url = normalize_url(url)
        except ValueError:
            return None
        if url in attempted:
            return None
        attempted.add(url)
        try:
            source = fetcher.fetch(url)
            hit = all_hits.get(url)
            source.raw_url = hit.raw_url if hit and hit.raw_url else source.raw_url or url
            fetched[source.final_url] = source
            candidate = verifier.extract(source, now)
            if candidate:
                extracted.append(candidate)
                source_candidates[url] = source_candidates[source.final_url] = candidate
                for child in source.links:
                    if re.search(
                        r"call for papers|\bCFP\b|original call|(?:official(?: conference| event| workshop)?|conference|event|workshop) website",
                        child.text,
                        re.I,
                    ):
                        if child.url != source.final_url:
                            follow_links.append((source, candidate, child.url, child.text))
            people = discover_researchers(source, paper, llm) if len(result.researchers) < 12 else []
            for person in people:
                if compact(person.name) not in {compact(p.name) for p in result.researchers}:
                    result.researchers.append(person)
            logger.info("source_processed", extra={"host": hostname(url), "candidate": bool(candidate)})
            return candidate
        except (FetchError, httpx.HTTPError, ValueError, KeyError) as exc:
            reason = str(exc) if isinstance(exc, FetchError) else type(exc).__name__
            result.issues.append(f"Source unverified: {url} — {reason}")
            logger.info("source_unverified", extra={"host": hostname(url), "error_type": type(exc).__name__})
            return None

    def follow_originals() -> None:
        nonlocal followed_links
        while follow_links and followed_links < 12:
            parent_source, parent, url, anchor = follow_links.pop(0)
            followed_links += 1
            child = source_candidates.get(url) or fetch_url(url)
            if (
                child
                and parent.official_source_urls
                and child.edition_year == parent.edition_year
                and child.series_key == parent.series_key
            ):
                child.official_source_urls = list(
                    dict.fromkeys(child.official_source_urls + child.source_urls)
                )
                child.evidence = [e.model_copy(update={"official": True}) for e in child.evidence]
                child.evidence.append(
                    Evidence(
                        field="authority",
                        value="Original CFP linked by official organiser",
                        quote=anchor,
                        source_url=parent_source.final_url,
                        official=True,
                        retrieved_at=parent_source.retrieved_at,
                        extracted_at=parent_source.extracted_at,
                        published_at=parent_source.published_at,
                    )
                )
                for attr in ["organisers", "speakers"]:
                    setattr(
                        child,
                        attr,
                        list(
                            dict.fromkeys(
                                e.value
                                for e in child.evidence
                                if e.field == attr
                                and not re.search(
                                    r"\b(?:tba|tbc|unknown|pending|to be announced|none)\b", e.value, re.I
                                )
                            )
                        ),
                    )
                child.verification_notes.append(
                    "Original CFP linked by the verified organiser for this same edition."
                )
                child.status = classify(child, now)

    initial_limit = budgets.max_sources - min(12, budgets.max_sources // 3)
    for hit in sorted(all_hits.values(), key=lambda h: _priority(h, paper), reverse=True):
        if len(attempted) >= initial_limit:
            break
        fetch_url(hit.url)
        follow_originals()

    unresolved = [c for c in extracted if not c.official_source_urls and c.status != Status.CLOSED]
    for candidate in unresolved[: budgets.followup_queries]:
        do_search(
            SearchQuery(
                family="official_source_resolution",
                query=f'"{candidate.name[:180]}" official call for papers',
            )
        )
    years = f"{now.year} {preferences.event_before.year}"
    network_queries = researcher_queries([p.name for p in result.researchers], years)
    for query in network_queries[: budgets.researcher_queries]:
        do_search(query)
    remaining = [hit for hit in all_hits.values() if hit.url not in attempted]
    for hit in sorted(remaining, key=lambda h: _priority(h, paper), reverse=True):
        fetch_url(hit.url)
        follow_originals()
        if len(attempted) >= budgets.max_sources:
            break
    candidates = deduplicate(extracted, now, llm)
    watchlist = watchlist_for(candidates, now)
    candidates = [c for c in candidates if c.status != Status.WATCHLIST]
    for candidate in candidates + watchlist:
        rank(candidate, paper, preferences, now, llm)
    result.candidates = sort_candidates(candidates)
    result.watchlist = sort_candidates(watchlist)
    result.memory_changes = memory.record(candidates + watchlist)
    result.sources = list(fetched.values())
    result.issues.extend(search.issues)
    if llm:
        result.issues.extend(llm.failures)
    else:
        result.issues.append(
            "Semantic LLM evaluation was unavailable/disabled; extraction and ranking use conservative heuristics with low score confidence."
        )
    result.issues.append(
        "Search and retrieval budgets limit coverage; absence from this report does not establish that a series has no open call."
    )
    if search.requests and not search.successes:
        result.run_status = "search_failed"
    elif search.issues or any(issue.startswith("Source unverified") for issue in result.issues):
        result.run_status = "partial"
    result.issues = list(dict.fromkeys(result.issues))
    result.statistics = {
        "queries": len(result.queries),
        "search_requests": search.requests,
        "search_successes": search.successes,
        "unique_search_urls": len(all_hits),
        "source_attempts": len(attempted),
        "sources_retrieved": len(fetched),
        "candidates": len(candidates),
        "watchlist": len(watchlist),
        "open_verified": sum(c.status == Status.OPEN_VERIFIED for c in candidates),
    }
    result.completed_at = utcnow()
    return result
