from __future__ import annotations

import argparse
import calendar
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from .cache import Cache
from .config import ConfigurationError, Settings, split_csv
from .demo import TOPICS, DemoFetcher, DemoSearch, demo_sources
from .llm import LLM
from .memory import ConferenceMemory
from .models import Preferences, RunResult, utcnow
from .network import HTTPTransport
from .paper_parser import parse_paper
from .pipeline import Budgets, run_discovery
from .query_generator import generate_queries
from .report_generator import generate_reports
from .search_provider import SearchManager, configured_providers
from .source_fetcher import SourceFetcher
from .source_verifier import SourceVerifier


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in ["host", "provider", "count", "attempt", "candidate", "error_type"]:
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, ensure_ascii=False)


def add_months(day: date, months: int) -> date:
    month_index = day.year * 12 + day.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Discover and verify research conferences; never extrapolate a CFP from a prior edition."
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument("--paper", type=Path, help="PDF, Markdown or TXT paper/abstract")
    group.add_argument("--topic", help="Research topic or paper title")
    group.add_argument("--abstract", help="Abstract supplied directly")
    p.add_argument("--title", help="Paper title, or title override for an abstract/paper")
    p.add_argument("--output", type=Path, default=Path("results"))
    p.add_argument("--fields", default="", help="Comma-separated preferred academic fields")
    p.add_argument("--regions", default="", help="Comma-separated regions, e.g. europe,north-america")
    p.add_argument("--deadline-after", type=date.fromisoformat)
    p.add_argument("--deadline-before", type=date.fromisoformat)
    p.add_argument("--event-after", type=date.fromisoformat)
    p.add_argument("--event-before", type=date.fromisoformat)
    p.add_argument("--horizon-months", type=int, default=9)
    p.add_argument("--goal", default="specialist feedback")
    p.add_argument("--researcher", action="append", default=[], help="Known researcher; may be repeated")
    p.add_argument("--provider", choices=["auto", "brave", "tavily"])
    p.add_argument("--env-file", type=Path)
    p.add_argument("--state-dir", type=Path)
    p.add_argument(
        "--official-domains", default="", help="Additional reviewed organiser domains, comma separated"
    )
    p.add_argument(
        "--blocked-domains", default="", help="Domains to exclude for site-specific terms/access restrictions"
    )
    p.add_argument(
        "--max-queries", type=int, default=36, help="Initial query budget; follow-up budgets are separate"
    )
    p.add_argument("--results-per-query", type=int, default=5)
    p.add_argument(
        "--max-sources", type=int, default=40, help="Total retrieval-attempt budget, including original CFPs"
    )
    p.add_argument("--followup-queries", type=int, default=8)
    p.add_argument("--researcher-queries", type=int, default=6)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--refresh", action="store_true", help="Bypass search/source caches for this run")
    p.add_argument(
        "--plan-only", action="store_true", help="Save the query plan without searching or calling an LLM"
    )
    p.add_argument(
        "--demo", choices=["A", "B"], help="Synthetic offline pipeline demonstration; no network calls"
    )
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    if not any([args.paper, args.topic, args.abstract, args.title, args.demo]):
        p.error("provide --paper, --topic, --abstract, --title, or --demo A/B")
    if args.demo and any([args.paper, args.topic, args.abstract]):
        p.error("--demo supplies its own topic; do not combine it with paper/topic/abstract")
    if not 1 <= args.horizon_months <= 60:
        p.error("--horizon-months must be between 1 and 60")
    if (
        min(args.max_queries, args.results_per_query, args.max_sources) < 1
        or min(args.followup_queries, args.researcher_queries) < 0
    ):
        p.error("query/results/source budgets must be positive; follow-up budgets cannot be negative")
    now = datetime.now().astimezone()
    cache = transport = memory = llm = None
    handler = None
    try:
        settings = Settings.load(args.env_file)
        if args.provider:
            settings.search_provider = args.provider
        if args.state_dir:
            settings.state_dir = args.state_dir
        elif args.demo:
            settings.state_dir = args.output / ".demo-state"
        settings.official_domains += tuple(split_csv(args.official_domains))
        settings.blocked_domains += tuple(split_csv(args.blocked_domains))
        if args.refresh or args.demo:
            settings.source_cache_seconds = settings.search_cache_seconds = 0
        if settings.source_cache_seconds > 6 * 3600:
            settings.source_cache_seconds = 6 * 3600
        regions = [r.lower().replace(" ", "-").replace("_", "-") for r in split_csv(args.regions)]
        prefs = Preferences(
            deadline_after=args.deadline_after or now.date(),
            deadline_before=args.deadline_before,
            event_after=args.event_after or now.date(),
            event_before=args.event_before or add_months(now.date(), args.horizon_months),
            regions=regions,
            fields=split_csv(args.fields),
            goal=args.goal,
            researchers=list(dict.fromkeys(args.researcher)),
        )
        if prefs.event_before < prefs.event_after or (
            prefs.deadline_before and prefs.deadline_before < prefs.deadline_after
        ):
            raise ValueError("Date window end must not precede its start.")
        args.output.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(args.output / "run.log", encoding="utf-8")
        handler.setFormatter(JSONFormatter())
        logging.getLogger("conference_agent").addHandler(handler)
        logging.getLogger("conference_agent").setLevel(logging.INFO)
        if args.verbose:
            logging.getLogger("conference_agent").addHandler(logging.StreamHandler(sys.stderr))
        cache = Cache(settings.state_dir)
        memory = ConferenceMemory(settings.state_dir)
        if (
            settings.openai_key
            and settings.openai_model
            and not any([args.no_llm, args.demo, args.plan_only])
        ):
            llm = LLM(settings, cache)
        paper = parse_paper(
            path=args.paper,
            topic=TOPICS[args.demo] if args.demo else args.topic or args.abstract or args.title,
            fields=prefs.fields,
            llm=llm,
        )
        if args.title:
            paper.title = args.title
        queries = generate_queries(paper, prefs, now.date(), llm)
        transport = HTTPTransport(settings)
        if args.demo:
            fixtures = demo_sources(args.demo, now)
            providers = [DemoSearch(fixtures)]
            fetcher = DemoFetcher(fixtures)
            official_domains = ("demo.example",)
        else:
            providers = [] if args.plan_only else configured_providers(settings, transport)
            fetcher = SourceFetcher(settings, transport, cache)
            official_domains = settings.official_domains
        if not providers or args.plan_only:
            mode = "plan_only" if args.plan_only else "configuration_required"
            result = RunResult(
                run_id=now.strftime("%Y%m%dT%H%M%S"),
                started_at=now,
                mode=mode,
                run_status=mode,
                paper=paper,
                preferences=prefs,
                queries=queries,
            )
            if not args.plan_only:
                result.issues.append(
                    "No live search provider configured. Set BRAVE_SEARCH_API_KEY or TAVILY_API_KEY in .env. No internet discovery was performed and no conference results were invented."
                )
            if not llm:
                result.issues.append(
                    "Set both OPENAI_API_KEY and OPENAI_MODEL to enable semantic paper analysis, structured extraction and evaluation."
                )
            result.completed_at = utcnow()
        else:
            result = run_discovery(
                paper=paper,
                preferences=prefs,
                now=now,
                queries=queries,
                search=SearchManager(providers, cache, settings.search_cache_seconds),
                fetcher=fetcher,
                verifier=SourceVerifier(official_domains=official_domains, llm=llm),
                memory=memory,
                budgets=Budgets(
                    args.max_queries,
                    args.results_per_query,
                    args.max_sources,
                    args.followup_queries,
                    args.researcher_queries,
                ),
                mode="offline_demo" if args.demo else "live",
                llm=llm,
            )
        if settings.openai_key and not settings.openai_model:
            result.issues.append(
                "OPENAI_API_KEY is present but OPENAI_MODEL is unset; LLM calls were disabled."
            )
        generate_reports(result, args.output)
        memory.export(args.output / "conference_memory.json")
        print(
            json.dumps(
                {
                    "mode": result.mode,
                    "run_status": result.run_status,
                    "report": str((args.output / "report.md").resolve()),
                    **result.statistics,
                },
                indent=2,
            )
        )
        return (
            2
            if result.mode == "configuration_required"
            else (3 if result.run_status == "search_failed" else 0)
        )
    except (ConfigurationError, ValueError, OSError) as exc:
        print(f"Configuration/input error: {exc}", file=sys.stderr)
        return 2
    finally:
        for resource in [llm, transport, memory, cache]:
            if resource:
                resource.close()
        if handler:
            logging.getLogger("conference_agent").removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
