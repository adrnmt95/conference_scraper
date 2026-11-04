from __future__ import annotations

from datetime import date, datetime, timezone
from datetime import date as DateValue
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Status(StrEnum):
    OPEN_VERIFIED = "OPEN_VERIFIED"
    FUTURE_DEADLINE_UNCONFIRMED = "FUTURE_DEADLINE_UNCONFIRMED"
    DATE_UNKNOWN = "DATE_UNKNOWN"
    WATCHLIST = "WATCHLIST"
    CLOSED = "CLOSED"


class PaperProfile(Model):
    title: str
    abstract: str
    research_questions: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    contributions: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    communities: list[str] = Field(default_factory=list)
    analysis_mode: Literal["llm", "heuristic"] = "heuristic"


class Preferences(Model):
    deadline_after: date
    deadline_before: date | None = None
    event_after: date
    event_before: date
    regions: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    goal: str = "specialist feedback"
    researchers: list[str] = Field(default_factory=list)


class SearchQuery(Model):
    family: str
    query: str


class SearchHit(Model):
    title: str
    url: str
    raw_url: str | None = None
    snippet: str = ""
    provider: str
    query: str
    retrieved_at: datetime = Field(default_factory=utcnow)
    published_at: str | None = None


class SourceLink(Model):
    url: str
    text: str
    raw_url: str | None = None


class Source(Model):
    url: str
    final_url: str
    raw_url: str | None = None
    title: str
    text: str
    links: list[SourceLink] = Field(default_factory=list)
    retrieved_at: datetime = Field(default_factory=utcnow)
    extracted_at: datetime = Field(default_factory=utcnow)
    published_at: str | None = None
    publication_date_kind: str | None = None
    content_type: str = "text/html"
    sha256: str = ""
    from_cache: bool = False
    raw_path: str | None = None


class Evidence(Model):
    field: str
    value: str
    quote: str
    source_url: str
    retrieved_at: datetime
    extracted_at: datetime
    published_at: str | None = None
    official: bool = False


class Deadline(Model):
    date: DateValue | None = None
    time: str | None = None
    timezone: str | None = None
    at: datetime | None = None
    raw: str
    source_url: str
    kind: str = "submission"
    warnings: list[str] = Field(default_factory=list)


class Criterion(Model):
    name: str
    weight: float
    score: int = Field(ge=0, le=100)
    rationale: str
    evidence_indices: list[int] = Field(default_factory=list)


class Ranking(Model):
    approximate_score: int = Field(ge=0, le=100)
    confidence: Literal["low", "medium", "high"]
    mode: Literal["heuristic", "llm"]
    criteria: list[Criterion]
    fit_explanation: list[str]
    suggested_framing: str
    caveats: list[str] = Field(default_factory=list)


class Candidate(Model):
    id: str
    series_key: str
    name: str
    edition_year: int | None = None
    organiser: str | None = None
    hosting_institution: str | None = None
    event_start: date | None = None
    event_end: date | None = None
    location: str | None = None
    region: str | None = None
    deadline: Deadline | None = None
    notification_date: date | None = None
    notification_is_estimate: bool | None = None
    submission_type: str | None = None
    submission_url: str | None = None
    participation: Literal["open", "invite-only", "unclear"] = "unclear"
    submission_fees: str | None = None
    travel_funding: str | None = None
    eligibility: str | None = None
    organisers: list[str] = Field(default_factory=list)
    speakers: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)
    recurring: bool = False
    active_cfp: bool = False
    open_evidence: bool = False
    explicitly_closed: bool = False
    official_source_urls: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    status: Status = Status.DATE_UNKNOWN
    verification_notes: list[str] = Field(default_factory=list)
    discrepancies: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    within_preferences: bool = True
    ranking: Ranking | None = None
    verified_at: datetime = Field(default_factory=utcnow)
    last_observed_edition: int | None = None


class Researcher(Model):
    name: str
    connection: str
    source_url: str | None = None
    quote: str | None = None
    supplied_by_user: bool = False


class RunResult(Model):
    schema_version: str = "1.0"
    run_id: str
    started_at: datetime
    completed_at: datetime = Field(default_factory=utcnow)
    mode: Literal["live", "offline_demo", "configuration_required", "plan_only"]
    run_status: str = "complete"
    paper: PaperProfile
    preferences: Preferences
    providers: list[str] = Field(default_factory=list)
    queries: list[SearchQuery] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    watchlist: list[Candidate] = Field(default_factory=list)
    researchers: list[Researcher] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    memory_changes: list[dict[str, str | None]] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    statistics: dict[str, int] = Field(default_factory=dict)
