from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from .models import PaperProfile, Preferences, SearchQuery

if TYPE_CHECKING:
    from .llm import LLM

COMMUNITIES = [
    ("CEPR", "cepr.org"),
    ("EEA", "eeassoc.org"),
    ("EPCS", None),
    ("ECPR", "ecpr.eu"),
    ("EPSA", "epsanet.org"),
    ("ESOC Empirical Studies of Conflict", None),
    ("ReCIPE", "cepr.org"),
    ("NBER", "nber.org"),
    ("CESifo", "cesifo.org"),
    ("IZA", "iza.org"),
    ("HiCN Households in Conflict Network", None),
]


class ExtraQueries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str]


def generate_queries(
    paper: PaperProfile, prefs: Preferences, today: date, llm: LLM | None = None
) -> list[SearchQuery]:
    years = " ".join(str(y) for y in range(today.year, prefs.event_before.year + 1))
    questions = " ".join(paper.topics[:2]) or paper.title[:100]
    result: list[SearchQuery] = []

    def add(family: str, value: str) -> None:
        value = " ".join(value.split())[:590]
        if value and value.casefold() not in {q.query.casefold() for q in result}:
            result.append(SearchQuery(family=family, query=value))

    # Round-robin core families before expanding the individual communities.
    add("research_question", f'{questions} "call for papers" {years}')
    add("discipline", f"{' '.join(paper.communities[:2] or paper.fields[:2])} conference submissions {years}")
    add("specialist_workshop", f"{questions} specialist research workshop call papers {years}")
    add("university", f'{questions} workshop "call for papers" university {years}')
    add("researcher_discovery", f"{questions} researchers workshop organisers programme")
    add("recurring_series", f"{questions} annual workshop conference previous editions")
    add("methods", f"{' '.join(paper.methods[:2]) or questions} social science workshop CFP {years}")
    add(
        "theory",
        f"{' '.join(paper.contributions[:1] or paper.topics[-2:])} institutions political economy workshop {years}",
    )
    for name, domain in COMMUNITIES:
        target = f"site:{domain} {name}" if domain else name
        add(
            "research_community",
            f"{target} {paper.communities[0] if paper.communities else questions} call papers {years}",
        )
    for topic in paper.topics[1:5]:
        add("substantive_topic", f"{topic} workshop paper submission {years}")
    for method in paper.methods[:3]:
        add("methods", f"{method} economics political science workshop call papers {years}")
    for community in paper.communities[:5]:
        add("related_discipline", f"{community} specialist workshop call papers {years}")
    for researcher in prefs.researchers:
        add("researcher_network", f'"{researcher}" workshop conference programme call papers {years}')
    for region in prefs.regions:
        add("geography", f"{questions} research workshop {region.replace('-', ' ')} {years}")
    if llm:
        extra = llm.parse(
            "Generate at most 8 semantically distinct academic CFP search queries. Include theoretical "
            "contributions, neighbouring disciplines, substantive questions, and specialist communities. "
            "Do not assert that a named event or future call exists. Avoid commercial conference directories.",
            {"paper": paper.model_dump(), "years": years, "existing_queries": [q.query for q in result]},
            ExtraQueries,
        )
        for query in extra.queries[:8] if extra else []:
            add("semantic_expansion", query)
    semantic = [q for q in result if q.family == "semantic_expansion"]
    return result[:8] + semantic + [q for q in result[8:] if q.family != "semantic_expansion"]


def researcher_queries(names: list[str], years: str) -> list[SearchQuery]:
    return [
        SearchQuery(family="researcher_network", query=f'"{name}" workshop conference call papers {years}')
        for name in names
    ]
