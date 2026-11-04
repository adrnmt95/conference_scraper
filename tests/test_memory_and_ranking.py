from datetime import date

from conference_agent.cache import Cache
from conference_agent.memory import ConferenceMemory
from conference_agent.models import Preferences, Status
from conference_agent.paper_parser import parse_paper
from conference_agent.pipeline import watchlist_for
from conference_agent.query_generator import COMMUNITIES, generate_queries
from conference_agent.ranking import WEIGHTS, apply_preferences, rank
from conference_agent.source_verifier import SourceVerifier


def prefs(now):
    return Preferences(
        deadline_after=now.date(), event_after=now.date(), event_before=date(2027, 8, 4), regions=["europe"]
    )


def test_query_families_and_all_research_communities(now):
    paper = parse_paper(
        topic="Political violence, institutional forbearance, settlement expansion, difference-in-differences, West Bank"
    )
    queries = generate_queries(paper, prefs(now), now.date())
    families = {q.family for q in queries}
    assert {
        "research_question",
        "discipline",
        "methods",
        "theory",
        "university",
        "specialist_workshop",
        "recurring_series",
        "researcher_discovery",
    } <= families
    for name, _ in COMMUNITIES:
        assert any(name in q.query for q in queries)
    assert any("2027" in q.query for q in queries)
    assert len(queries) == len({q.query for q in queries})


def test_topic_b_methods_are_not_dropped():
    paper = parse_paper(
        topic="Organized crime infiltration in legal firms, temporal graph neural networks, business ownership networks, prediction, economic outcomes"
    )
    assert "temporal graph neural networks" in paper.methods
    assert "economics of crime" in paper.communities
    assert "computational social science" in paper.communities


def test_memory_detects_new_edition_without_inventing_one(tmp_path, make_source, now):
    verifier = SourceVerifier(official_domains=("demo.example",))
    old = verifier.extract(
        make_source(name="Annual Political Economy Workshop 2026", deadline="2026-01-15", dates="2026-06-01"),
        now,
    )
    memory = ConferenceMemory(tmp_path)
    assert memory.record([old]) == []
    assert "2026" not in memory.seeds()[0]["name"]
    current = verifier.extract(make_source(), now)
    changes = memory.record([current])
    assert changes[0]["kind"] == "new_verified_edition"
    assert changes[0]["edition"] == "2027"
    assert memory.record([current]) == []
    memory.close()


def test_memory_reopened_call_detected(tmp_path, make_source, now):
    verifier = SourceVerifier(official_domains=("demo.example",))
    c = verifier.extract(make_source(extra="Submissions are closed."), now)
    memory = ConferenceMemory(tmp_path)
    memory.record([c])
    reopened = verifier.extract(make_source(), now)
    assert memory.record([reopened])[0]["kind"] == "new_verified_call"
    memory.close()


def test_current_official_cfp_does_not_duplicate_watchlist(make_source, now):
    c = SourceVerifier(official_domains=("demo.example",)).extract(make_source(), now)
    assert watchlist_for([c], now) == []


def test_ranking_weights_evidence_and_coarse_precision(make_source, now):
    c = SourceVerifier(official_domains=("demo.example",)).extract(
        make_source(extra="This small workshop provides discussants and detailed feedback."), now
    )
    paper = parse_paper(topic="Political violence, institutions, difference-in-differences")
    rank(c, paper, prefs(now), now)
    assert sum(WEIGHTS.values()) == 1
    assert c.ranking.approximate_score % 5 == 0
    assert c.ranking.confidence == "low"
    assert 2 <= len(c.ranking.fit_explanation) <= 4
    assert all(
        0 <= index < len(c.evidence)
        for criterion in c.ranking.criteria
        for index in criterion.evidence_indices
    )
    assert next(x for x in c.ranking.criteria if x.name == "quality_and_goal").score >= 70


def test_known_preference_mismatches_excluded_unknowns_preserved(make_source, now):
    c = SourceVerifier(official_domains=("demo.example",)).extract(make_source(), now)
    pref = prefs(now)
    pref.regions = ["north-america"]
    apply_preferences(c, pref)
    assert c.within_preferences is False
    assert c.status == Status.OPEN_VERIFIED
    c.region = None
    apply_preferences(c, pref)
    assert c.within_preferences is True
    assert any("unknown" in note for note in c.constraints)


def test_cache_ttl_and_persistence(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("conference_agent.cache.time.time", lambda: clock[0])
    cache = Cache(tmp_path)
    key = Cache.key("search", {"query": "CFP"})
    cache.put(key, {"value": "test"})
    cache.close()
    cache = Cache(tmp_path)
    assert cache.get(key, 10)["value"] == "test"
    assert cache.get(key, 0) is None
    clock[0] += 11
    assert cache.get(key, 10) is None
    cache.close()
