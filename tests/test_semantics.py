from datetime import date
from types import SimpleNamespace

from pydantic import BaseModel

from conference_agent.cache import Cache
from conference_agent.config import Settings
from conference_agent.demo import TOPICS
from conference_agent.llm import LLM
from conference_agent.memory import ConferenceMemory
from conference_agent.models import Preferences, SearchHit, SearchQuery, Source, SourceLink, Status
from conference_agent.paper_parser import parse_paper
from conference_agent.pipeline import Budgets, run_discovery
from conference_agent.ranking import WEIGHTS, Evaluation, Judgement, rank
from conference_agent.search_provider import SearchManager
from conference_agent.source_verifier import (
    AliasGroup,
    Claim,
    Extraction,
    SemanticAliases,
    SourceVerifier,
    deduplicate,
)


def test_semantic_aliases_require_same_edition_and_corroboration(make_source, now):
    verifier = SourceVerifier(official_domains=("demo.example",))
    a = verifier.extract(
        make_source(name="EPCS Annual Meeting 2027", organiser="European Public Choice Society"), now
    )
    b = verifier.extract(
        make_source(
            name="European Public Choice Society Congress 2027",
            organiser="European Public Choice Society",
            url="https://research.demo.example/congress/",
        ),
        now,
    )

    class FakeLLM:
        def parse(self, task, payload, schema):
            assert schema == SemanticAliases
            return SemanticAliases(
                groups=[
                    AliasGroup(
                        candidate_ids=[a.id, b.id],
                        reason="Acronym and full name, same organiser/date/edition.",
                    )
                ]
            )

    assert len(deduplicate([a, b], now, FakeLLM())) == 1
    b.edition_year = 2028
    assert len(deduplicate([a, b], now, FakeLLM())) == 2


def test_semantic_alias_cannot_merge_conflicting_locations(make_source, now):
    verifier = SourceVerifier(official_domains=("demo.example",))
    a = verifier.extract(make_source(name="EPCS Annual Meeting 2027"), now)
    b = verifier.extract(
        make_source(name="Public Choice Congress 2027", url="https://other.demo.example/congress/"), now
    )
    b.location = "New York, USA"

    class FakeLLM:
        def parse(self, *args):
            return SemanticAliases(
                groups=[AliasGroup(candidate_ids=[a.id, b.id], reason="Proposed but unsupported alias")]
            )

    assert len(deduplicate([a, b], now, FakeLLM())) == 2


def test_structured_output_sdk_and_cache(monkeypatch, tmp_path):
    class Answer(BaseModel):
        topic: str

    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 2
            self.responses = self

        def parse(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["text_format"] == Answer
            assert kwargs["store"] is False
            assert "untrusted DATA" in kwargs["instructions"]
            return SimpleNamespace(output_parsed=Answer(topic="political economy"))

        def close(self):
            pass

    monkeypatch.setattr("conference_agent.llm.OpenAI", FakeClient)
    cache = Cache(tmp_path)
    llm = LLM(Settings(openai_key="fake", openai_model="configured-model"), cache)
    first = llm.parse("Extract the topic.", {"paper": "Institutions"}, Answer)
    second = llm.parse("Extract the topic.", {"paper": "Institutions"}, Answer)
    assert first == second
    assert len(calls) == 1
    cache.close()
    llm.close()


def test_llm_unavailable_returns_explicit_failure(monkeypatch, tmp_path):
    class Answer(BaseModel):
        topic: str

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = self

        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=None)

        def close(self):
            pass

    monkeypatch.setattr("conference_agent.llm.OpenAI", FakeClient)
    cache = Cache(tmp_path)
    llm = LLM(Settings(openai_key="fake", openai_model="configured-model"), cache)
    assert llm.parse("Extract.", {}, Answer) is None
    assert "fallback" in llm.failures[0]
    cache.close()
    llm.close()


def test_llm_date_value_is_bound_to_its_quote(make_source, now):
    source = make_source()
    quote = "Event dates: 2027-06-01\nTopics: political violence; institutions; political economy; organized crime; economic networks"

    class FakeLLM:
        def parse(self, *args):
            return Extraction(
                is_academic_event=True,
                name=source.title,
                claims=[
                    Claim(field="event_dates", value="2027-06-01", quote=quote),
                    Claim(field="event_dates", value="2028-06-01", quote=quote),
                ],
            )

    candidate = SourceVerifier(official_domains=("demo.example",), llm=FakeLLM()).extract(source, now)
    assert candidate.event_start == candidate.event_end == date(2027, 6, 1)


def test_llm_ranking_caps_unsupported_quality_and_audience(make_source, now):
    candidate = SourceVerifier(official_domains=("demo.example",)).extract(make_source(), now)

    class FakeLLM:
        def parse(self, *args):
            return Evaluation(
                criteria=[
                    Judgement(name=name, score=100, rationale="Uncorroborated proposal", evidence_indices=[])
                    for name in WEIGHTS
                ],
                fit_explanation=["Related question.", "Potential audience."],
                suggested_framing="Explain the institutional mechanism.",
                confidence="high",
                caveats=[],
            )

    paper = parse_paper(topic=TOPICS["A"])
    prefs = Preferences(deadline_after=now.date(), event_after=now.date(), event_before=date(2027, 8, 4))
    rank(candidate, paper, prefs, now, FakeLLM())
    assert candidate.ranking.approximate_score == 50
    assert candidate.ranking.confidence == "low"
    assert all(c.score <= 50 for c in candidate.ranking.criteria)


def test_researcher_connection_discovers_indirect_workshop(tmp_path, now, make_source):
    profile_url = "https://research.demo.example/researcher/"
    profile = Source(
        url=profile_url,
        final_url=profile_url,
        title="Academic profile",
        text="Researchers: Ada Mentor\nResearch: institutional forbearance and political violence",
        retrieved_at=now,
        extracted_at=now,
    )
    workshop = make_source(name="Institutional Economics Workshop 2027", deadline=None)
    original = make_source(name=workshop.title, url="https://original-cfp.example/call/")
    workshop.links.append(SourceLink(url=original.final_url, text="Original call for papers"))
    workshop.text += "\nOriginal call for papers\n"
    sources = {profile_url: profile, workshop.final_url: workshop, original.final_url: original}

    class CommunitySearch:
        name = "test"

        def search(self, query, count=5):
            source = workshop if '"Ada Mentor"' in query else profile
            return [SearchHit(title=source.title, url=source.final_url, provider=self.name, query=query)]

    class Fetcher:
        def fetch(self, url):
            return sources[url]

    cache, memory = Cache(tmp_path), ConferenceMemory(tmp_path)
    paper = parse_paper(topic=TOPICS["A"])
    prefs = Preferences(deadline_after=now.date(), event_after=now.date(), event_before=date(2027, 8, 4))
    result = run_discovery(
        paper=paper,
        preferences=prefs,
        now=now,
        queries=[SearchQuery(family="researcher_discovery", query="institutions researchers")],
        search=SearchManager([CommunitySearch()], cache, 0),
        fetcher=Fetcher(),
        verifier=SourceVerifier(official_domains=("demo.example",)),
        memory=memory,
        budgets=Budgets(max_queries=1, max_sources=6, researcher_queries=1),
    )
    assert result.researchers[0].name == "Ada Mentor"
    assert result.researchers[0].quote
    assert result.candidates[0].name == "Institutional Economics Workshop 2027"
    assert result.candidates[0].status == Status.OPEN_VERIFIED
    assert result.candidates[0].deadline.source_url == original.final_url
    assert original.final_url in result.candidates[0].official_source_urls
    assert any(
        e.field == "authority" and e.source_url == workshop.final_url for e in result.candidates[0].evidence
    )
    assert any(q.family == "researcher_network" for q in result.queries)
    cache.close()
    memory.close()


def test_original_cfp_already_retrieved_can_be_endorsed(tmp_path, now, make_source):
    parent = make_source(deadline=None)
    original = make_source(url="https://original-cfp.example/call/")
    parent.links.append(SourceLink(url=original.final_url, text="Original call for papers"))
    parent.text += "\nOriginal call for papers\n"

    class Search:
        name = "test"

        def search(self, query, count=5):
            # Stronger snippet puts the untrusted original ahead of the official parent.
            return [
                SearchHit(
                    title=s.title,
                    url=s.final_url,
                    provider=self.name,
                    query=query,
                    snippet="call for papers workshop conference submissions" if s == original else "",
                )
                for s in [original, parent]
            ]

    class Fetcher:
        def fetch(self, url):
            return {parent.final_url: parent, original.final_url: original}[url]

    cache, memory = Cache(tmp_path), ConferenceMemory(tmp_path)
    paper = parse_paper(topic=TOPICS["A"])
    prefs = Preferences(deadline_after=now.date(), event_after=now.date(), event_before=date(2027, 8, 4))
    result = run_discovery(
        paper=paper,
        preferences=prefs,
        now=now,
        queries=[SearchQuery(family="test", query="workshop")],
        search=SearchManager([Search()], cache, 0),
        fetcher=Fetcher(),
        verifier=SourceVerifier(official_domains=("demo.example",)),
        memory=memory,
        budgets=Budgets(max_queries=1, max_sources=6),
    )
    assert result.candidates[0].status == Status.OPEN_VERIFIED
    assert original.final_url in result.candidates[0].official_source_urls
    assert result.statistics["source_attempts"] == 2
    cache.close()
    memory.close()
