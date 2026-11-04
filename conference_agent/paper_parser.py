from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from .models import PaperProfile

if TYPE_CHECKING:
    from .llm import LLM


# Broad enough to expose disciplinary neighbours without inventing a paper's methods.
TOPIC_MAP = {
    "political violence": ["political violence", "extremist violence", "terrorism", "conflict"],
    "institutional forbearance": ["forbearance", "non-enforcement", "nonenforcement"],
    "settlement expansion": ["settlement", "west bank", "palestine"],
    "political accountability": ["accountability", "elections", "voting"],
    "organized crime": ["organized crime", "organised crime", "mafia", "criminal infiltration"],
    "business ownership networks": ["ownership", "legal firms", "business networks", "firm networks"],
    "economic outcomes": ["economic outcomes", "productivity", "employment", "firm performance"],
    "institutions": ["institution", "state capacity", "governance"],
    "machine learning": ["machine learning", "neural network", "prediction", "predictive"],
}
METHOD_MAP = {
    "difference-in-differences": [
        "difference-in-differences",
        "difference in differences",
        "diff-in-diff",
        "did",
    ],
    "temporal graph neural networks": ["temporal graph neural", "dynamic graph neural"],
    "graph neural networks": ["graph neural", "gnn"],
    "network analysis": ["network", "graph"],
    "causal inference": ["causal", "event study", "difference-in-differences"],
    "prediction": ["prediction", "predictive", "forecast"],
    "theory": ["theoretical model", "game theory", "formal model"],
}


def contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.I))


class SemanticProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    abstract: str
    research_questions: list[str]
    topics: list[str]
    methods: list[str]
    contributions: list[str]
    fields: list[str]
    communities: list[str]


def read_paper(path: Path) -> str:
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("Paper exceeds the 30 MB input limit.")
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDF: provide an unencrypted paper or its abstract.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Could not read PDF; provide a text PDF, Markdown or TXT file.") from exc
        if len(text.strip()) < 80:
            raise ValueError(
                "PDF contains too little extractable text. OCR it locally or provide an abstract."
            )
        return text
    if path.suffix.lower() not in {".md", ".markdown", ".txt"}:
        raise ValueError("Supported paper formats: PDF, Markdown and TXT.")
    return path.read_text(encoding="utf-8-sig")


def parse_paper(
    *,
    path: Path | None = None,
    topic: str | None = None,
    fields: list[str] | None = None,
    llm: LLM | None = None,
) -> PaperProfile:
    raw = read_paper(path) if path else topic or ""
    if not raw.strip():
        raise ValueError("Provide a nonempty paper, title, abstract or topic.")
    lines = [line.strip().lstrip("# ") for line in raw.splitlines() if line.strip()]
    title = lines[0][:300]
    # Preserve abstract/conclusion context for full papers without silently sending unbounded input.
    excerpt = raw if len(raw) <= 65000 else raw[:50000] + "\n[Middle omitted]\n" + raw[-15000:]
    if llm:
        parsed = llm.parse(
            "Extract the paper's actual question, contribution, methods and relevant research communities. "
            "Do not infer methods or named researchers absent from the paper. Use conceptual communities, "
            "not invented conference editions. A topic input need not contain an abstract.",
            {"paper": excerpt, "preferred_fields": fields or []},
            SemanticProfile,
        )
        if parsed:
            data = parsed.model_dump()
            data["fields"] = list(dict.fromkeys((fields or []) + data["fields"]))
            return PaperProfile(**data, analysis_mode="llm")
    lowered = excerpt.casefold()
    topics = [
        key for key, variants in TOPIC_MAP.items() if any(contains_phrase(lowered, v) for v in variants)
    ]
    methods = [
        key for key, variants in METHOD_MAP.items() if any(contains_phrase(lowered, v) for v in variants)
    ]
    communities = []
    if any(
        t in topics
        for t in ["political violence", "institutional forbearance", "institutions", "settlement expansion"]
    ):
        communities += ["political economy", "conflict economics", "institutional economics"]
    if "organized crime" in topics:
        communities += ["economics of crime", "political economy", "law and economics"]
    if "business ownership networks" in topics or "network analysis" in methods:
        communities += ["economic networks", "computational social science"]
    if "machine learning" in topics:
        communities += ["machine learning in social sciences"]
    if not topics:
        topics = [re.sub(r"\s+", " ", title)[:140]]
    abstract = re.split(r"\babstract\b\s*[:\n]?", excerpt, maxsplit=1, flags=re.I)[-1].strip()[:12000]
    return PaperProfile(
        title=title,
        abstract=abstract,
        research_questions=[title],
        topics=topics,
        methods=methods,
        fields=fields or ["economics", "political science"],
        communities=list(dict.fromkeys(communities)),
    )
