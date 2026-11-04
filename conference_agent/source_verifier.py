from __future__ import annotations

import hashlib
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from .deadline_extractor import deadline_state, explicit_dates, extract_deadlines
from .models import Candidate, Evidence, Source, Status
from .urls import domain_matches, hostname, series_key

if TYPE_CHECKING:
    from .llm import LLM

# Domain recognition is an authority check, never evidence of a current CFP.
ORGANISER_DOMAINS = {
    "cepr.org": ["cepr", "centre for economic policy research", "recipe"],
    "nber.org": ["nber", "national bureau of economic research"],
    "iza.org": ["iza", "institute of labor economics"],
    "cesifo.org": ["cesifo"],
    "ifo.de": ["ifo", "cesifo"],
    "ecpr.eu": ["ecpr", "european consortium for political research"],
    "epsanet.org": ["epsa", "european political science association"],
    "eeassoc.org": ["eea", "european economic association"],
    "princeton.edu": ["princeton", "esoc", "empirical studies of conflict"],
    "ox.ac.uk": ["oxford"],
    "cam.ac.uk": ["cambridge"],
}
EVENT_WORD = re.compile(
    r"\b(?:conference|workshop|meeting|symposium|congress|colloquium|summer institute)\b|\b(?:EEA|EPCS|ECPR|EPSA|ESOC|ReCIPE|HiCN)\s+(?:19|20)\d{2}\b",
    re.I,
)
ALLOWED_FIELDS = {
    "name",
    "edition_year",
    "organiser",
    "hosting_institution",
    "event_dates",
    "location",
    "region",
    "submission_type",
    "submission_url",
    "notification_date",
    "participation",
    "submission_fees",
    "travel_funding",
    "eligibility",
    "organisers",
    "speakers",
    "topics",
    "methods",
    "audience",
    "quality",
    "cfp",
    "open",
    "not_open",
    "closed",
    "recurring",
}


def compact(text: str) -> str:
    return " ".join(text.split()).casefold()


def quote_supported(quote: str, source: Source) -> bool:
    return len(quote.strip()) >= 5 and compact(quote) in compact(source.title + "\n" + source.text)


def supported_llm_claim(claim: Claim, source: Source) -> bool:
    if claim.field not in ALLOWED_FIELDS or not quote_supported(claim.quote, source):
        return False
    # Status-changing signals use deterministic checks, never an LLM's boolean assertion.
    if claim.field in {"open", "not_open", "closed", "cfp", "recurring", "participation"}:
        return False
    if claim.field == "submission_url":
        return any(link.url == claim.value for link in source.links)
    if claim.field in {"event_dates", "notification_date"}:
        dates = explicit_dates(claim.value)
        return bool(dates) and set(dates) <= set(explicit_dates(claim.quote))
    return compact(claim.value) in compact(claim.quote)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    value: str
    quote: str


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_academic_event: bool
    name: str | None
    claims: list[Claim]


def _labelled(source: Source, field: str, pattern: str) -> list[Claim]:
    result = []
    lines = source.text.splitlines()
    for i, line in enumerate(lines):
        match = re.search(pattern + r"(?=\s|[:–—-]|$)\s*[:–—-]?\s*(.*)", line, re.I)
        if not match:
            continue
        value = match[1].strip()
        quote = line
        if not value and i + 1 < len(lines):
            value = lines[i + 1].strip()
            quote += "\n" + value
        if value:
            result.append(Claim(field=field, value=value[:800], quote=quote[:1800]))
    return result


def heuristic_claims(source: Source) -> list[Claim]:
    claims = []
    name = source.title
    if not EVENT_WORD.search(name):
        name = next((line for line in source.text.splitlines()[:15] if EVENT_WORD.search(line)), name)
    if name:
        claims.append(Claim(field="name", value=name, quote=name))
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", name))
    if len(years) == 1:
        claims.append(Claim(field="edition_year", value=next(iter(years)), quote=name))
    patterns = {
        "organiser": r"^(?:organis(?:er|ed by)|organiz(?:er|ed by)|organising institution|organizing institution)",
        "hosting_institution": r"^(?:hosted by|hosting institution|host institution|host)",
        "event_dates": r"^(?:conference dates?|workshop dates?|event dates?|meeting dates?|dates?)",
        "location": r"^(?:location|venue)",
        "region": r"^region",
        "submission_type": r"^(?:submission type|submission format|submit a|submissions require)",
        "notification_date": r"^(?:acceptance notification|notification(?: of acceptance)?(?: date)?)",
        "submission_fees": r"^(?:submission fees?|paper submission fees?)",
        "travel_funding": r"^(?:travel funding|travel support|funding)",
        "eligibility": r"^(?:eligibility|eligible applicants|restrictions)",
        "organisers": r"^(?:organis(?:ing committee|ers)|organiz(?:ing committee|ers)|scientific committee)",
        "speakers": r"^(?:keynote speakers?|invited speakers?|speakers)",
        "topics": r"^(?:topics|scope|themes|research topics)",
        "methods": r"^(?:methods|methodological focus)",
        "audience": r"^(?:audience|participants)",
    }
    for field, pattern in patterns.items():
        claims.extend(_labelled(source, field, pattern))
    for line in source.text.splitlines():
        if re.search(r"call for (?:papers|abstracts)|we invite (?:papers|submissions|abstracts)", line, re.I):
            claims.append(Claim(field="cfp", value="true", quote=line))
        if re.search(
            r"submissions? (?:are )?(?:now )?open|we invite (?:papers|submissions|abstracts)"
            r"|papers are invited|submit your (?:paper|abstract)",
            line,
            re.I,
        ):
            if not re.search(r"not yet|will open|opens on|closed|no longer", line, re.I):
                claims.append(Claim(field="open", value="true", quote=line))
        if re.search(
            r"submissions? (?:are )?(?:now )?closed|no longer accept(?:ing)? (?:papers|submissions)"
            r"|call for papers (?:is )?closed|submission deadline (?:has )?passed",
            line,
            re.I,
        ):
            claims.append(Claim(field="closed", value="true", quote=line))
        if re.search(
            r"submissions? (?:are )?(?:not yet open|will open|opens? on)|call for papers (?:will open|is not (?:yet )?open)|not (?:yet )?open for submissions",
            line,
            re.I,
        ):
            claims.append(Claim(field="not_open", value="true", quote=line))
        if re.search(r"invite.only|invitation.only|by invitation only", line, re.I):
            claims.append(Claim(field="participation", value="invite-only", quote=line))
        elif re.search(
            r"open to (?:all )?(?:researchers|scholars|academics)|any researcher may submit", line, re.I
        ):
            claims.append(Claim(field="participation", value="open", quote=line))
        if re.search(
            r"\bannual\b|\bbiennial\b|previous editions|recurring|\b(?:[2-9]|\d{2,})(?:nd|rd|th)\b",
            line,
            re.I,
        ):
            claims.append(Claim(field="recurring", value="true", quote=line))
        if re.search(
            r"peer.review|discussants?|small workshop|detailed feedback|scientific committee", line, re.I
        ):
            claims.append(Claim(field="quality", value=line, quote=line))
    for link in source.links:
        if re.search(
            r"submit (?:your )?(?:paper|abstract)|submission (?:portal|form)|paper submission",
            link.text,
            re.I,
        ):
            claims.append(Claim(field="submission_url", value=link.url, quote=link.text))
    return [claim for claim in claims if quote_supported(claim.quote, source)]


def _infer_region(location: str | None) -> str | None:
    if not location:
        return None
    if re.search(r"\bonline\b|\bvirtual\b", location, re.I):
        return "online"
    europe = [
        "europe",
        "united kingdom",
        "germany",
        "france",
        "italy",
        "switzerland",
        "netherlands",
        "belgium",
        "spain",
        "portugal",
        "austria",
        "sweden",
        "denmark",
        "norway",
        "finland",
        "poland",
        "greece",
        "ireland",
        "czech",
        "london",
        "paris",
        "berlin",
        "zurich",
        "zürich",
        "rome",
        "milan",
        "florence",
        "munich",
        "oxford",
        "bonn",
        "vienna",
        "amsterdam",
    ]
    north_america = [
        "north america",
        "united states",
        "usa",
        "canada",
        "mexico",
        "washington, dc",
        "new york",
        "boston",
        "chicago",
        "toronto",
        "montreal",
        "princeton",
    ]
    for region, names in [("europe", europe), ("north-america", north_america)]:
        if any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", location, re.I) for name in names):
            return region
    return None


class SourceVerifier:
    def __init__(self, *, official_domains: tuple[str, ...] = (), llm: LLM | None = None):
        self.official_domains, self.llm = official_domains, llm

    def authority(self, source: Source, claims: list[Claim]) -> tuple[bool, str]:
        host = hostname(source.final_url)
        if any(domain_matches(host, domain) for domain in self.official_domains):
            return True, "Organiser domain explicitly trusted in configuration."
        identities = " ".join(
            c.value for c in claims if c.field in {"organiser", "hosting_institution", "name"}
        )
        for domain, aliases in ORGANISER_DOMAINS.items():
            if domain_matches(host, domain) and any(alias in identities.casefold() for alias in aliases):
                return True, "Recognised organiser domain and matching organiser/event identity in page text."
        # A university domain alone is insufficient: a matching host/organiser claim is also required.
        labels = [
            x
            for x in re.findall(r"[a-z]+", host)
            if len(x) >= 4
            and x not in {"www", "university", "events", "econ", "economics", "dept", "department"}
        ]
        host_claims = [c for c in claims if c.field in {"organiser", "hosting_institution"}]
        academic_domain = bool(re.search(r"\.edu$|\.ac\.[a-z]{2,3}$|(?:^|\.)(?:uni-|univ-)", host))
        if academic_domain and any(label in compact(c.value) for c in host_claims for label in labels):
            return True, "Academic domain matches an explicit host/organiser claim."
        return False, "Official organiser authority could not be established from the retrieved page."

    def extract(self, source: Source, now: datetime) -> Candidate | None:
        claims = heuristic_claims(source)
        if self.llm:
            extracted = self.llm.parse(
                "Extract ONE main academic conference/workshop from this page. A directory, research paper, "
                "news index, or researcher CV is not itself an event. Return is_academic_event=false for such pages. "
                "Every claim needs a verbatim quote from the page; keep missing fields absent. Allowed fields: "
                + ", ".join(sorted(ALLOWED_FIELDS))
                + ". Use ISO dates only when explicitly supported, "
                "never fill in a missing year. open requires an actual invitation or explicit open submissions; "
                "registration is not paper submission. recurring requires evidence. List only verified people. "
                "An event year must describe this edition, not a copyright, historical link, or submission year. "
                "Do not label a page official: authority is checked independently. submission_url must be a supplied link.",
                {
                    "url": source.final_url,
                    "title": source.title,
                    "text": source.text[:55000],
                    "links": [link.model_dump() for link in source.links[:120]],
                },
                Extraction,
            )
            if extracted:
                if not extracted.is_academic_event:
                    return None
                claims.extend(c for c in extracted.claims if supported_llm_claim(c, source))
                if extracted.name and quote_supported(extracted.name, source):
                    claims.insert(0, Claim(field="name", value=extracted.name, quote=extracted.name))
        name_claim = next(
            (
                c
                for c in claims
                if c.field == "name"
                and EVENT_WORD.search(c.value)
                and compact(c.value) in compact(source.title + "\n" + source.text)
            ),
            None,
        )
        if not name_claim:
            return None
        name = name_claim.value[:350]
        if re.search(
            r"\b(?:conference directory|all conferences|upcoming events|events calendar|curriculum vitae)\b",
            name,
            re.I,
        ):
            return None
        official, authority_note = self.authority(source, claims)
        evidence = [
            Evidence(
                field=c.field,
                value=c.value,
                quote=c.quote,
                source_url=source.final_url,
                retrieved_at=source.retrieved_at,
                extracted_at=source.extracted_at,
                published_at=source.published_at,
                official=official,
            )
            for c in claims
        ]

        def values(field: str) -> list[str]:
            return list(dict.fromkeys(c.value for c in claims if c.field == field))

        def first(field: str) -> str | None:
            return next(iter(values(field)), None)

        years = set()
        for c in claims:
            if c.field == "edition_year" and c.value.isdigit() and re.search(rf"\b{c.value}\b", c.quote):
                if EVENT_WORD.search(c.quote) or c.quote.strip() == name:
                    years.add(int(c.value))
        edition_year = next(iter(years)) if len(years) == 1 else None
        dates = []
        for c in claims:
            if c.field == "event_dates":
                supported_dates = explicit_dates(c.value)
                if set(supported_dates) <= set(explicit_dates(c.quote)):
                    dates.extend(supported_dates)
        event_start, event_end = (min(dates), max(dates)) if dates else (None, None)
        if not edition_year and event_start and event_start.year == event_end.year:
            edition_year = event_start.year
        deadlines = extract_deadlines(source.text, source.final_url)
        unique_dates = {(d.date, d.at) for d in deadlines if d.date}
        # An explicitly extended deadline supersedes an old date on that same page.
        extended = [
            d for d in deadlines if d.date and re.search(r"extended|new deadline|extension", d.raw, re.I)
        ]
        selected = (
            max(extended, key=lambda d: d.date) if extended else next((d for d in deadlines if d.date), None)
        )
        if len(unique_dates) > 1 and not extended:
            future = [d for d in deadlines if deadline_state(d, now) in {"future", "uncertain"}]
            selected = min(future, key=lambda d: d.date) if future else selected
        if not selected and deadlines:
            selected = deadlines[0]
        for deadline in deadlines:
            evidence.append(
                Evidence(
                    field="deadline",
                    value=deadline.date.isoformat() if deadline.date else "unknown",
                    quote=deadline.raw,
                    source_url=source.final_url,
                    official=official,
                    retrieved_at=source.retrieved_at,
                    extracted_at=source.extracted_at,
                    published_at=source.published_at,
                )
            )
        organiser = first("organiser")
        key = series_key(name, organiser)
        candidate = Candidate(
            id=hashlib.sha256(f"{key}|{edition_year}|{source.final_url}".encode()).hexdigest()[:16],
            series_key=key,
            name=name,
            edition_year=edition_year,
            organiser=organiser,
            hosting_institution=first("hosting_institution"),
            event_start=event_start,
            event_end=event_end,
            location=first("location"),
            region=first("region").casefold().replace(" ", "-").replace("_", "-")
            if first("region")
            else _infer_region(first("location")),
            deadline=selected,
            submission_type=first("submission_type"),
            participation="invite-only"
            if "invite-only" in values("participation")
            else ("open" if "open" in values("participation") else "unclear"),
            submission_fees=first("submission_fees"),
            travel_funding=first("travel_funding"),
            eligibility=first("eligibility"),
            organisers=[
                v
                for v in values("organisers")
                if not re.search(r"\b(?:tba|tbc|unknown|pending|to be announced|none)\b", v, re.I)
            ]
            if official
            else [],
            speakers=[
                v
                for v in values("speakers")
                if not re.search(r"\b(?:tba|tbc|unknown|pending|to be announced|none)\b", v, re.I)
            ]
            if official
            else [],
            topics=values("topics"),
            methods=values("methods"),
            audience=values("audience"),
            recurring=bool(values("recurring")),
            active_cfp=bool(values("cfp")),
            open_evidence=official and bool(values("open")),
            explicitly_closed=official and bool(values("closed")),
            source_urls=[source.final_url],
            official_source_urls=[source.final_url] if official else [],
            evidence=evidence,
            verified_at=now,
            verification_notes=[authority_note],
        )
        for c in claims:
            if c.field == "submission_url" and any(link.url == c.value for link in source.links):
                candidate.submission_url = c.value
                break
        notification = next((c for c in claims if c.field == "notification_date"), None)
        if notification:
            n_dates = explicit_dates(notification.value)
            candidate.notification_date = n_dates[0] if len(set(n_dates)) == 1 else None
            candidate.notification_is_estimate = bool(
                re.search(r"expected|estimate|tentative|approximately", notification.quote, re.I)
            )
        if len(unique_dates) > 1 and not extended:
            candidate.discrepancies.append(
                "Official deadline conflict: multiple submission deadlines or tracks require checking."
                if official
                else "Multiple reported submission deadlines require checking."
            )
        if len(years) > 1:
            candidate.discrepancies.append(
                "Multiple edition years in evidence; current call identity is ambiguous."
            )
        if edition_year and event_start and edition_year != event_start.year:
            candidate.discrepancies.append("Edition year conflicts with stated conference dates.")
        if selected and selected.date and event_start and selected.date > event_start:
            candidate.discrepancies.append("Submission deadline is after the stated event start.")
        if selected:
            candidate.verification_notes.extend(selected.warnings)
        if source.from_cache:
            candidate.verification_notes.append(
                f"Source cached; original retrieval: {source.retrieved_at.isoformat()}."
            )
        if source.publication_date_kind:
            candidate.verification_notes.append(
                source.publication_date_kind + ": " + str(source.published_at)
            )
        candidate.status = classify(candidate, now)
        return candidate


def classify(candidate: Candidate, now: datetime) -> Status:
    closed_claim = any(
        e.field == "closed" and (e.official or not candidate.official_source_urls) for e in candidate.evidence
    )
    if closed_claim or candidate.explicitly_closed:
        candidate.verification_notes.append(
            "Retrieved call explicitly closes submissions"
            + (" (official source)." if candidate.official_source_urls else " (authority unconfirmed).")
        )
        return Status.CLOSED
    if candidate.event_end and candidate.event_end < now.date():
        candidate.verification_notes.append(
            "The observed edition has already taken place; it was not rolled forward."
        )
        return Status.CLOSED
    state = deadline_state(candidate.deadline, now) if candidate.deadline else "unknown"
    if state == "past":
        return Status.CLOSED
    if not candidate.active_cfp and candidate.recurring:
        return Status.WATCHLIST
    if state in {"future", "uncertain"}:
        blockers = any(
            token in note.casefold()
            for note in candidate.discrepancies
            for token in [
                "official deadline conflict",
                "edition year",
                "multiple edition",
                "after the stated event",
            ]
        )
        official_deadline = bool(
            candidate.deadline and candidate.deadline.source_url in candidate.official_source_urls
        )
        open_claim = any(e.field == "open" and e.official for e in candidate.evidence)
        not_open_claim = any(
            e.field == "not_open" and (e.official or not candidate.official_source_urls)
            for e in candidate.evidence
        )
        provisional = bool(
            candidate.deadline
            and any("provisional" in warning.lower() for warning in candidate.deadline.warnings)
        )
        dated_edition = candidate.edition_year is not None
        if (
            state == "future"
            and official_deadline
            and open_claim
            and not not_open_claim
            and not provisional
            and candidate.active_cfp
            and dated_edition
            and not blockers
            and candidate.participation != "invite-only"
        ):
            return Status.OPEN_VERIFIED
        if state == "uncertain":
            candidate.verification_notes.append(
                "Same-day/timezone uncertainty: a future cutoff is not established."
            )
        if not dated_edition:
            candidate.verification_notes.append("No explicit, consistent edition year was established.")
        if candidate.participation == "invite-only":
            candidate.verification_notes.append(
                "Participation is invite-only; not an open submission opportunity."
            )
        if not_open_claim:
            candidate.verification_notes.append(
                "The call says submissions are not yet open; a static submit link does not override that restriction."
            )
        return Status.FUTURE_DEADLINE_UNCONFIRMED
    return Status.DATE_UNKNOWN if candidate.active_cfp or not candidate.recurring else Status.WATCHLIST


def _same_event(a: Candidate, b: Candidate) -> bool:
    if a.edition_year != b.edition_year:
        return False
    if a.event_start and b.event_start and a.event_start != b.event_start:
        return False
    common_url = bool(set(a.source_urls) & set(b.source_urls))
    same_org = bool(a.organiser and b.organiser and compact(a.organiser) == compact(b.organiser))
    same_host = bool(
        {hostname(u) for u in a.official_source_urls} & {hostname(u) for u in b.official_source_urls}
    )
    same_date_place = bool(
        a.event_start
        and a.event_start == b.event_start
        and a.location
        and b.location
        and compact(a.location) == compact(b.location)
    )
    names = SequenceMatcher(None, a.series_key, b.series_key).ratio()
    tokens_a, tokens_b = set(a.series_key.split()), set(b.series_key.split())
    semantic = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))
    return common_url or ((same_org or same_host or same_date_place) and (names >= 0.85 or semantic >= 0.72))


class AliasGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ids: list[str]
    reason: str


class SemanticAliases(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[AliasGroup]


def _semantic_anchor(a: Candidate, b: Candidate) -> bool:
    if a.edition_year is None or a.edition_year != b.edition_year:
        return False
    if a.event_start and b.event_start and a.event_start != b.event_start:
        return False
    if a.location and b.location and compact(a.location) != compact(b.location):
        return False
    return bool(
        (a.organiser and b.organiser and compact(a.organiser) == compact(b.organiser))
        or (a.event_start and a.event_start == b.event_start and a.location and b.location)
    )


def deduplicate(candidates: list[Candidate], now: datetime, llm: LLM | None = None) -> list[Candidate]:
    aliases: set[frozenset[str]] = set()
    if llm and len(candidates) > 1:
        proposal = llm.parse(
            "Find duplicate listings of the SAME conference EDITION, including acronym/full-name aliases. "
            "Do not group related workshops, different years, or generic titles at different institutions. "
            "Return groups of supplied candidate_ids only and an evidence-based reason. Leave uncertain pairs separate.",
            {
                "candidates": [
                    c.model_dump(
                        mode="json",
                        include={
                            "id",
                            "name",
                            "edition_year",
                            "organiser",
                            "hosting_institution",
                            "event_start",
                            "event_end",
                            "location",
                            "source_urls",
                        },
                    )
                    for c in candidates
                ]
            },
            SemanticAliases,
        )
        by_id = {c.id: c for c in candidates}
        for group in proposal.groups if proposal else []:
            members = [by_id[key] for key in dict.fromkeys(group.candidate_ids) if key in by_id]
            for i, a in enumerate(members):
                for b in members[i + 1 :]:
                    if _semantic_anchor(a, b):
                        aliases.add(frozenset((a.id, b.id)))
    result: list[Candidate] = []
    for item in candidates:
        existing_index = next(
            (
                i
                for i, c in enumerate(result)
                if _same_event(c, item) or frozenset((c.id, item.id)) in aliases
            ),
            None,
        )
        if existing_index is None:
            result.append(item)
            continue
        old = result[existing_index]
        # Official evidence wins over aggregators; retain all evidence and discrepancies.
        if bool(item.official_source_urls) > bool(old.official_source_urls):
            primary, secondary = item, old
        else:
            primary, secondary = old, item
        primary = primary.model_copy(deep=True)
        different_deadline = bool(
            primary.deadline
            and secondary.deadline
            and (
                primary.deadline.date != secondary.deadline.date
                or (
                    primary.deadline.at
                    and secondary.deadline.at
                    and primary.deadline.at != secondary.deadline.at
                )
                or (
                    primary.deadline.time
                    and secondary.deadline.time
                    and primary.deadline.timezone == secondary.deadline.timezone
                    and primary.deadline.time != secondary.deadline.time
                )
            )
        )
        if different_deadline:
            both_official = bool(primary.official_source_urls and secondary.official_source_urls)
            label = (
                "Official deadline conflict"
                if both_official
                else "Reported deadline discrepancy (official source takes precedence)"
            )
            primary.discrepancies.append(
                f"{label}: {primary.deadline.raw} at {primary.deadline.source_url}; "
                f"{secondary.deadline.raw} at {secondary.deadline.source_url}."
            )
        for attr in [
            "organiser",
            "hosting_institution",
            "event_start",
            "event_end",
            "location",
            "region",
            "deadline",
            "submission_type",
            "submission_url",
            "notification_date",
            "submission_fees",
            "travel_funding",
            "eligibility",
        ]:
            if getattr(primary, attr) is None:
                setattr(primary, attr, getattr(secondary, attr))
        for attr in [
            "source_urls",
            "official_source_urls",
            "organisers",
            "speakers",
            "topics",
            "methods",
            "audience",
            "verification_notes",
            "discrepancies",
        ]:
            setattr(primary, attr, list(dict.fromkeys(getattr(primary, attr) + getattr(secondary, attr))))
        primary.evidence.extend(e for e in secondary.evidence if e not in primary.evidence)
        primary.recurring |= secondary.recurring
        primary.active_cfp |= secondary.active_cfp
        primary.open_evidence |= secondary.open_evidence
        primary.explicitly_closed |= secondary.explicitly_closed
        primary.status = classify(primary, now)
        result[existing_index] = primary
    return result
