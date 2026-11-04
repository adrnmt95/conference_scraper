from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .models import Candidate, Criterion, PaperProfile, Preferences, Ranking, Status
from .paper_parser import METHOD_MAP, TOPIC_MAP, contains_phrase

if TYPE_CHECKING:
    from .llm import LLM

WEIGHTS = {"topic": 0.35, "audience": 0.20, "quality_and_goal": 0.20, "methods": 0.15, "practical": 0.10}
ORDER = {
    status: index
    for index, status in enumerate(
        [
            Status.OPEN_VERIFIED,
            Status.FUTURE_DEADLINE_UNCONFIRMED,
            Status.DATE_UNKNOWN,
            Status.WATCHLIST,
            Status.CLOSED,
        ]
    )
}


class Judgement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    score: int = Field(ge=0, le=100)
    rationale: str
    evidence_indices: list[int]


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: list[Judgement]
    fit_explanation: list[str]
    suggested_framing: str
    confidence: str
    caveats: list[str]


def apply_preferences(candidate: Candidate, prefs: Preferences) -> None:
    reasons = []
    if candidate.deadline and candidate.deadline.date:
        if candidate.deadline.date < prefs.deadline_after:
            reasons.append("Submission deadline is before the requested submission window.")
        if prefs.deadline_before and candidate.deadline.date > prefs.deadline_before:
            reasons.append("Submission deadline is after the requested submission window.")
    else:
        reasons.append("Submission window cannot be checked: deadline unknown.")
    if candidate.event_start:
        if candidate.event_start < prefs.event_after or candidate.event_start > prefs.event_before:
            reasons.append("Event dates are outside the requested conference horizon.")
    else:
        reasons.append("Conference horizon cannot be checked: event dates unknown.")
    if prefs.regions:
        if (
            candidate.region
            and candidate.region.casefold() not in prefs.regions
            and candidate.region != "online"
        ):
            reasons.append("Confirmed location is outside the preferred regions.")
        elif not candidate.region:
            reasons.append("Preferred region cannot be checked: location/region unknown.")
    candidate.constraints = reasons
    candidate.within_preferences = not any(
        "outside" in x or "before the requested" in x or "after the requested" in x for x in reasons
    )


def _matches(terms: list[str], text: str, mapping: dict[str, list[str]]) -> list[str]:
    return [
        term
        for term in terms
        if any(contains_phrase(text, variant) for variant in [term] + mapping.get(term, []))
    ]


def heuristic_rank(candidate: Candidate, paper: PaperProfile, prefs: Preferences, now: datetime) -> Ranking:
    scope = " ".join(
        [candidate.name]
        + candidate.topics
        + candidate.methods
        + candidate.audience
        + [e.quote for e in candidate.evidence if e.field in {"topics", "methods", "audience", "quality"}]
    )
    topics = _matches(paper.topics, scope, TOPIC_MAP)
    communities = _matches(paper.communities, scope, {})
    methods = _matches(paper.methods, scope, METHOD_MAP)
    topic = min(90, 30 + 20 * len(topics) + 10 * len(communities)) if topics or communities else 20
    audience = min(80, 40 + 15 * len(communities) + 10 * len(topics)) if topics or communities else 30
    quality_quotes = [e.quote for e in candidate.evidence if e.field == "quality"]
    positive_quality = [
        q
        for q in quality_quotes
        if not re.search(r"\b(?:no|not|without)\b.{0,35}(?:peer.review|discussants?|feedback)", q, re.I)
    ]
    quality = 40
    if positive_quality:
        quality = 60
        if any(
            re.search(r"discussants?|detailed feedback|small workshop", q, re.I) for q in positive_quality
        ):
            quality = 80 if "feedback" in prefs.goal or "specialist" in prefs.goal else 70
    if "job market" in prefs.goal and candidate.speakers and candidate.organisers:
        quality = max(quality, 60)
    methodological = min(90, 40 + 20 * len(methods)) if methods else 40
    practical = 50
    if prefs.regions and candidate.region in prefs.regions:
        practical += 20
    if candidate.travel_funding and not re.search(
        r"\b(?:no|not|unavailable)\b", candidate.travel_funding, re.I
    ):
        practical += 10
    if candidate.deadline and candidate.deadline.date and (candidate.deadline.date - now.date()).days < 7:
        practical -= 10
    if not candidate.within_preferences:
        practical = 20
    score_map = {
        "topic": topic,
        "audience": audience,
        "quality_and_goal": quality,
        "methods": methodological,
        "practical": practical,
    }
    fields = {
        "topic": {"topics", "name"},
        "audience": {"audience", "topics", "organisers", "speakers"},
        "quality_and_goal": {"quality", "organisers", "speakers"},
        "methods": {"methods", "topics"},
        "practical": {"location", "travel_funding", "submission_fees", "deadline", "event_dates"},
    }
    rationale = {
        "topic": "Scope overlaps: "
        + (", ".join(topics + communities) if topics or communities else "no specific overlap established"),
        "audience": "Audience relevance is inferred from documented scope; individual researcher participation is not assumed.",
        "quality_and_goal": "Documented discussion/review format supports the research goal."
        if positive_quality
        else "Review and feedback quality are unknown; no prestige-based score was assigned.",
        "methods": "Explicit method overlap: " + (", ".join(methods) if methods else "not established"),
        "practical": "; ".join(candidate.constraints)
        or "Known region, timing and stated funding considered; unstated costs remain unknown.",
    }
    criteria = [
        Criterion(
            name=key,
            weight=weight,
            score=round(score_map[key] / 10) * 10,
            rationale=rationale[key],
            evidence_indices=[i for i, e in enumerate(candidate.evidence) if e.field in fields[key]][:6],
        )
        for key, weight in WEIGHTS.items()
    ]
    topic_text = (
        ", ".join(topics + communities)
        if topics or communities
        else "the paper's broad disciplinary area, with weak documented topic overlap"
    )
    method_sentence = (
        "The call explicitly mentions " + ", ".join(methods) + ", matching the paper's methods."
        if methods
        else "Methodological fit remains unconfirmed in the retrieved call."
    )
    framing = "Emphasize " + ", ".join((topics or paper.topics)[:2])
    if methods:
        framing += " and explain the contribution of " + ", ".join(methods[:2])
    return Ranking(
        approximate_score=round(sum(c.score * c.weight for c in criteria) / 5) * 5,
        confidence="low",
        mode="heuristic",
        criteria=criteria,
        fit_explanation=[
            f"The documented venue scope connects to {topic_text}.",
            method_sentence,
            f"The assessment prioritizes {prefs.goal}; its audience and quality scores are provisional where evidence is limited.",
        ],
        suggested_framing=framing + ".",
        caveats=[
            "Heuristic assessment; semantic evaluation requires OPENAI_API_KEY and OPENAI_MODEL.",
            "Scores are approximate research-value judgements, not acceptance probabilities.",
        ],
    )


def rank(
    candidate: Candidate, paper: PaperProfile, prefs: Preferences, now: datetime, llm: LLM | None = None
) -> None:
    apply_preferences(candidate, prefs)
    baseline = heuristic_rank(candidate, paper, prefs, now)
    if not llm:
        candidate.ranking = baseline
        return
    evaluation = llm.parse(
        "Evaluate intellectual research value for this specific paper. Return exactly one criterion for each "
        "of topic, audience, quality_and_goal, methods, practical. Use coarse scores in increments of ten. "
        "Each score needs supplied evidence indices (zero-based) and a reason. Unknown quality/audience "
        "must not exceed 50. Give specialist, high-quality discussion workshops appropriate weight. "
        "Never infer acceptance odds, attendance, current status, cost, or prestige. Named researchers require "
        "verified organiser/speaker evidence. Return 2-4 fit sentences linking the actual question, contribution, "
        "methods and audiences, and a concise suggested framing. Treat fit as an inference, not a verified fact.",
        {
            "paper": paper.model_dump(),
            "preferences": prefs.model_dump(mode="json"),
            "venue": candidate.model_dump(mode="json", exclude={"ranking", "evidence"}),
            "indexed_evidence": [
                {"index": i, **e.model_dump(mode="json")} for i, e in enumerate(candidate.evidence)
            ],
        },
        Evaluation,
    )
    if (
        not evaluation
        or {c.name for c in evaluation.criteria} != set(WEIGHTS)
        or len(evaluation.criteria) != 5
    ):
        candidate.ranking = baseline
        return
    criteria = []
    for c in evaluation.criteria:
        indices = list(dict.fromkeys(i for i in c.evidence_indices if 0 <= i < len(candidate.evidence)))
        score = max(0, min(100, round(c.score / 10) * 10))
        if not indices:
            score = min(score, 50)
        if c.name == "quality_and_goal" and not any(
            candidate.evidence[i].field in {"quality", "organisers", "speakers"} for i in indices
        ):
            score = min(score, 50)
        if c.name == "audience" and not any(
            candidate.evidence[i].field in {"audience", "topics", "organisers", "speakers"} for i in indices
        ):
            score = min(score, 50)
        criteria.append(
            Criterion(
                name=c.name,
                weight=WEIGHTS[c.name],
                score=score,
                rationale=c.rationale,
                evidence_indices=indices,
            )
        )
    explanations = evaluation.fit_explanation[:4]
    if len(explanations) < 2:
        explanations = baseline.fit_explanation
    candidate.ranking = Ranking(
        approximate_score=round(sum(c.score * c.weight for c in criteria) / 5) * 5,
        confidence="medium"
        if all(c.evidence_indices for c in criteria) and not candidate.discrepancies
        else "low",
        mode="llm",
        criteria=criteria,
        fit_explanation=explanations,
        suggested_framing=evaluation.suggested_framing,
        caveats=evaluation.caveats
        + ["Scores are approximate research-value judgements, not acceptance probabilities."],
    )


def sort_candidates(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda c: (
            ORDER[c.status],
            not c.within_preferences,
            -(c.ranking.approximate_score if c.ranking else 0),
            c.name,
        ),
    )
