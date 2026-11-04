import csv
import json
from datetime import date

import pytest

from conference_agent.cache import Cache
from conference_agent.demo import TOPICS, DemoFetcher, DemoSearch, demo_sources
from conference_agent.discover import add_months, main
from conference_agent.memory import ConferenceMemory
from conference_agent.models import Preferences, RunResult, SearchHit, Status
from conference_agent.network import FetchError
from conference_agent.paper_parser import parse_paper, read_paper
from conference_agent.pipeline import Budgets, run_discovery
from conference_agent.query_generator import generate_queries
from conference_agent.report_generator import generate_reports
from conference_agent.search_provider import SearchManager
from conference_agent.source_verifier import SourceVerifier


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    for key in [
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "BRAVE_SEARCH_API_KEY",
        "TAVILY_API_KEY",
        "SEARCH_PROVIDER",
        "CONFERENCE_STATE_DIR",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "conference_agent.network.HTTPTransport.request",
        lambda *args, **kwargs: pytest.fail("Unexpected internet request"),
    )


@pytest.mark.parametrize("topic", ["A", "B"])
def test_offline_demo_runs_whole_pipeline_without_network(tmp_path, monkeypatch, topic):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / topic
    assert main(["--demo", topic, "--output", str(output), "--max-queries", "8"]) == 0
    result = RunResult.model_validate_json((output / "results.json").read_text())
    assert result.mode == "offline_demo"
    assert {c.status for c in result.candidates} == {
        Status.OPEN_VERIFIED,
        Status.FUTURE_DEADLINE_UNCONFIRMED,
        Status.DATE_UNKNOWN,
        Status.CLOSED,
    }
    assert result.watchlist[0].deadline is None
    assert result.researchers
    assert any(q.family == "researcher_network" for q in result.queries)
    report = (output / "report.md").read_text()
    assert "OFFLINE DEMONSTRATION" in report
    assert report.index("## Verified open calls") < report.index("## Future deadlines")
    with (output / "candidates.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(result.candidates) + len(result.watchlist)
    assert all(row["mode"] == "offline_demo" for row in rows)
    assert not (tmp_path / ".conference-agent").exists()


def test_no_credentials_writes_honest_empty_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["--topic", TOPICS["A"], "--output", "results"]) == 2
    result = json.loads((tmp_path / "results/results.json").read_text())
    assert result["mode"] == "configuration_required"
    assert result["candidates"] == result["watchlist"] == []
    assert result["queries"]
    assert "No live search provider configured" in " ".join(result["issues"])


def test_plan_only_and_current_date_at_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["--topic", TOPICS["B"], "--plan-only", "--output", "plan"]) == 0
    result = json.loads((tmp_path / "plan/results.json").read_text())
    assert result["mode"] == "plan_only"
    assert result["preferences"]["deadline_after"] == date.today().isoformat()
    assert result["preferences"]["event_before"] == add_months(date.today(), 9).isoformat()


def test_month_arithmetic_clamps_day():
    assert add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_invalid_date_range_has_actionable_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert (
        main(["--topic", "institutions", "--deadline-after", "2027-06-01", "--deadline-before", "2027-01-01"])
        == 2
    )
    assert "must not precede" in capsys.readouterr().err


def test_markdown_and_txt_papers(tmp_path):
    paper = tmp_path / "paper.md"
    paper.write_text(
        "# Government Forbearance\n\nAbstract\nPolitical violence in the West Bank. We use difference-in-differences."
    )
    profile = parse_paper(path=paper)
    assert profile.title == "Government Forbearance"
    assert "political violence" in profile.topics
    assert "difference-in-differences" in profile.methods
    txt = tmp_path / "paper.txt"
    txt.write_text("Crime and Firms\nOrganized crime and ownership networks.")
    assert parse_paper(path=txt).title == "Crime and Firms"


def test_real_pdf_text_extraction_and_scanned_pdf_error(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 40 700 Td (Political violence and institutional forbearance. This paper studies settlement expansion and accountability.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    pdf = tmp_path / "paper.pdf"
    writer.write(pdf)
    assert "institutional forbearance" in read_paper(pdf)
    blank = PdfWriter()
    blank.add_blank_page(width=612, height=792)
    blank.write(tmp_path / "scan.pdf")
    with pytest.raises(ValueError, match="OCR"):
        read_paper(tmp_path / "scan.pdf")


def test_snippets_cannot_verify_when_source_is_inaccessible(tmp_path, now):
    class SnippetSearch:
        name = "test"

        def search(self, query, count=5):
            return [
                SearchHit(
                    title="Annual Workshop 2027",
                    url="https://example.edu/cfp",
                    snippet="Submissions open. Deadline January 1, 2027",
                    provider=self.name,
                    query=query,
                )
            ]

    class DeniedFetcher:
        def fetch(self, url):
            raise FetchError("Access restricted (HTTP 403)")

    paper = parse_paper(topic=TOPICS["A"])
    prefs = Preferences(deadline_after=now.date(), event_after=now.date(), event_before=date(2027, 8, 4))
    cache, memory = Cache(tmp_path), ConferenceMemory(tmp_path)
    result = run_discovery(
        paper=paper,
        preferences=prefs,
        now=now,
        queries=generate_queries(paper, prefs, now.date()),
        search=SearchManager([SnippetSearch()], cache, 0),
        fetcher=DeniedFetcher(),
        verifier=SourceVerifier(),
        memory=memory,
        budgets=Budgets(max_queries=2, max_sources=2),
    )
    assert result.candidates == []
    assert result.run_status == "partial"
    assert any("Access restricted" in issue for issue in result.issues)
    cache.close()
    memory.close()


def test_csv_escapes_formula_and_results_roundtrip(tmp_path, now):
    sources = demo_sources("A", now)
    paper = parse_paper(topic=TOPICS["A"])
    prefs = Preferences(deadline_after=now.date(), event_after=now.date(), event_before=date(2027, 8, 4))
    cache, memory = Cache(tmp_path), ConferenceMemory(tmp_path)
    result = run_discovery(
        paper=paper,
        preferences=prefs,
        now=now,
        queries=generate_queries(paper, prefs, now.date()),
        search=SearchManager([DemoSearch(sources)], cache, 0),
        fetcher=DemoFetcher(sources),
        verifier=SourceVerifier(official_domains=("demo.example",)),
        memory=memory,
        budgets=Budgets(max_queries=2),
        mode="offline_demo",
    )
    result.candidates[0].name = '=HYPERLINK("evil")'
    generate_reports(result, tmp_path / "output")
    reread = RunResult.model_validate_json((tmp_path / "output/results.json").read_text())
    assert reread.sources
    with (tmp_path / "output/candidates.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["name"].startswith("'=")
    cache.close()
    memory.close()
