from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from .models import PaperProfile, Researcher, Source
from .source_verifier import compact, quote_supported

if TYPE_CHECKING:
    from .llm import LLM


class ResearcherClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")
    researchers: list[Researcher]


def discover_researchers(source: Source, paper: PaperProfile, llm: LLM | None = None) -> list[Researcher]:
    if llm:
        extracted = llm.parse(
            "Identify at most 4 named researchers who demonstrably work on the paper's question/methods, "
            "or organised/presented at a related research workshop in this source. A connection needs a "
            "verbatim quote containing the person's name and topic/workshop context. Do not use memory "
            "to invent researchers, attendance, or affiliations. supplied_by_user must be false. "
            "Set source_url to the supplied URL.",
            {"paper": paper.model_dump(), "url": source.final_url, "text": source.text[:35000]},
            ResearcherClaims,
        )
        if extracted:
            result = []
            for person in extracted.researchers:
                if (
                    person.quote
                    and quote_supported(person.quote, source)
                    and compact(person.name) in compact(person.quote)
                ):
                    person.source_url = source.final_url
                    person.supplied_by_user = False
                    result.append(person)
            return result[:4]
    result = []
    for line in source.text.splitlines():
        match = re.match(
            r"(?:Relevant researchers?|Researchers?|Organis(?:ers|ing committee)|Organiz(?:ers|ing committee))\s*:\s*(.+)",
            line,
            re.I,
        )
        if not match:
            continue
        context = source.text.casefold()
        if not any(term.casefold() in context for term in paper.topics + paper.communities + paper.methods):
            continue
        for part in re.split(r";|\s+and\s+", match[1]):
            name = re.sub(r"\s*\([^)]*\)", "", part).strip()
            if not re.fullmatch(r"[A-ZÀ-ÖØ-Þ][\w'’.-]+(?:\s+[A-ZÀ-ÖØ-Þ][\w'’.-]+){1,3}", name):
                continue
            result.append(
                Researcher(
                    name=name,
                    connection="Named researcher/organiser on a page with related research scope; connection is provisional.",
                    source_url=source.final_url,
                    quote=line,
                )
            )
    return result[:4]
