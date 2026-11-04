"""Synthetic end-to-end examples. No network; no assertions about real conferences."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import SearchHit, Source, SourceLink

TOPICS = {
    "A": "Political violence, institutional forbearance, settlement expansion, difference-in-differences, West Bank.",
    "B": "Organized crime infiltration in legal firms, temporal graph neural networks, business ownership networks, prediction, economic outcomes.",
}


def demo_sources(topic: str, now: datetime) -> dict[str, Source]:
    is_a = topic == "A"
    scope = (
        "political violence; institutional forbearance; settlement expansion; political economy; institutions"
        if is_a
        else "organized crime; business ownership networks; economic outcomes; economics of crime; computational social science"
    )
    methods = (
        "difference-in-differences; causal inference"
        if is_a
        else "temporal graph neural networks; network analysis; prediction"
    )
    subject = "Conflict and Institutions" if is_a else "Crime and Economic Networks"
    deadline = now.date() + timedelta(days=45)
    event = now.date() + timedelta(days=120)
    past_year = now.year - 1
    pages = [
        (
            "open",
            f"Fictitious Annual Workshop on {subject} {event.year}",
            True,
            f"Call for papers\nWe invite submissions. Submissions are now open.\nSubmission deadline: {deadline} 23:59 UTC\n"
            f"Event dates: {event}\nSubmission type: full paper\n"
            "This small workshop uses discussants and detailed feedback.\nParticipation is open to all researchers.\n"
            "Travel funding: limited travel support available for doctoral researchers\nSubmission fee: none\nEligibility: all researchers\n"
            "Organisers: Ada Mentor; Bruno Scholar\n",
            event.year,
        ),
        (
            "listing",
            f"Fictitious {subject} Conference {event.year}",
            False,
            f"Call for papers\nSubmission deadline: {deadline + timedelta(days=15)}\nEvent dates: {event + timedelta(days=14)}\n"
            "Submission type: extended abstract\n",
            event.year,
        ),
        (
            "unknown",
            f"Fictitious {subject} Methods Workshop {event.year}",
            True,
            f"Call for papers\nWe invite submissions.\nEvent dates: {event + timedelta(days=30)}\nSubmission deadline: to be announced\n",
            event.year,
        ),
        (
            "archive",
            f"Fictitious Annual {subject} Research Meeting {past_year}",
            True,
            f"Call for papers\nSubmissions are closed.\nSubmission deadline: {past_year}-04-01\nEvent dates: {past_year}-06-15\n"
            "Previous editions are available in our annual meeting archive.\n",
            past_year,
        ),
    ]
    result = {}
    for slug, name, official, body, year in pages:
        host = "institute.demo.example" if official else "listing.example"
        url = f"https://{host}/{topic.lower()}/{slug}/{year}/"
        text = (
            f"{name}\nOrganiser: Fictitious Research Institute\nHosted by: Fictitious Research Institute\n"
            f"Location: London, United Kingdom\nTopics: {scope}\nMethods: {methods}\n"
            f"Audience: economics and political science researchers working on {subject.lower()}\n" + body
        )
        links = [SourceLink(url=url + "submit", text="Submit your paper")] if slug == "open" else []
        if links:
            text += "Submit your paper\n"
        result[url] = Source(
            url=url, final_url=url, title=name, text=text, links=links, retrieved_at=now, extracted_at=now
        )
    return result


class DemoSearch:
    name = "synthetic_fixture"

    def __init__(self, sources: dict[str, Source]):
        self.sources = sources

    def search(self, query: str, count: int = 5) -> list[SearchHit]:
        return [
            SearchHit(
                title=s.title,
                url=url,
                snippet="Synthetic fixture, not internet search.",
                provider=self.name,
                query=query,
            )
            for url, s in list(self.sources.items())[:count]
        ]


class DemoFetcher:
    def __init__(self, sources: dict[str, Source]):
        self.sources = sources

    def fetch(self, url: str) -> Source:
        return self.sources[url].model_copy(deep=True)
