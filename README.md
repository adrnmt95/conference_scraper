# Conference Discovery

This repository contains the paper-driven discovery agent (`discover.py`): give it a
paper and it finds, verifies and ranks calls for papers worth submitting it to. The
original conference-to-Excel scraper has been retired to [`old/`](old/).

| Path | Contents |
| --- | --- |
| `discover.py`, `conference_agent/` | The discovery agent (CLI and package) |
| `papers/` | Your own papers to run the agent on; contents are not committed to git |
| `examples/` | Two sample paper descriptions for trying the agent |
| `demos/` | Saved output of the offline demonstration runs |
| `tests/` | Test suite |
| `old/` | The previous Excel scraper, kept for reference |

A reusable Python agent for economics, political science and computational social
science. It discovers academic calls, retrieves original sources, applies conservative
date and authority checks, and ranks the potential research value of presenting a
specific paper. Specialist feedback and relevant research audiences matter alongside
topic and methodological fit.

**Live search needs a configured Brave Search or Tavily API key.** Semantic paper
analysis, structured extraction, researcher discovery and evaluation additionally
need an OpenAI API key and a Responses API model supporting Structured Outputs.
Without search credentials, the CLI saves a query plan and an honest empty report,
then exits with code 2. It never substitutes invented conferences for search results.

## Setup

Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install '.[dev]'
cp -n .env.example .env
```

If `.env` already exists, add the missing settings to it. Edit `.env` locally:

```dotenv
SEARCH_PROVIDER=brave
BRAVE_SEARCH_API_KEY=your-search-key
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=your-supported-model-id
```

Alternatively set `SEARCH_PROVIDER=tavily` and `TAVILY_API_KEY`. `auto` uses every
provider with a configured key. A key's permissions and billing belong to that
provider account; this program cannot use the chat app's web-search capability or
subscription as an API credential. No specific model or current date is hardcoded.

The `.env` file is loaded from the current directory only, or from `--env-file`.
Existing environment values take precedence. Keys and API error bodies are excluded
from logs and cache keys. Paper excerpts and retrieved public text are sent to the
configured LLM when enabled; `--no-llm` disables those calls. Raw public sources,
extracted evidence, local reports and memory are stored locally.

## Run

Put your paper (PDF, Markdown or TXT) in `papers/` and point the agent at it. If you
have no paper file, describe it with `--topic` or `--abstract` instead.

```bash
python discover.py --paper papers/my_paper.pdf --output results/my_paper
python discover.py --paper examples/forbearance.md --output results
python discover.py --topic 'Political violence and government forbearance' --output results
python discover.py --abstract 'Your abstract here' --title 'Your paper title' --output results
python discover.py --paper papers/my_paper.pdf --regions europe,north-america --output results/my_paper
```

Titles are read heuristically from the first page of a PDF. If the title in the
report looks cut off, pass the full one with `--title`.

Files in `papers/` other than its README are ignored by git, so unpublished drafts
stay on your machine.

For a specific submission window, add ISO dates with `--deadline-after YYYY-MM-DD`
and `--deadline-before YYYY-MM-DD`. The default lower bound is the current local
date **at runtime**. Conference dates default to today through nine calendar months
ahead; use `--horizon-months`, `--event-after`, and `--event-before` to change this.
End-of-month horizons clamp to the destination month's last day. Submission and
conference windows are evaluated independently.

```bash
python discover.py --paper examples/crime_networks.md \
  --fields economics,computational-social-science \
  --regions europe,north-america --horizon-months 9 \
  --goal 'interdisciplinary networking' \
  --researcher 'A researcher you want to reach' --output results/crime
```

`--researcher` may be repeated. Known names seed community searches; independently
discovered names require a quote from a retrieved page. The connection is recorded
and researcher/workshop follow-up queries are executed. A person's appearance on
one public page does not imply attendance at a different event or future edition.

Use `python discover.py --help` for all options. Installed entry points are
`conference-discover` and `python -m conference_agent`.

## Local website

A small website does the same thing without the command line: upload a paper, wait,
and browse the ranked results.

```bash
python -m pip install '.[web]'     # once: installs Flask
.venv/bin/python serve.py           # opens http://127.0.0.1:8765 in your browser
```

Start it from the project folder so it finds `.env`. The page shows whether a search
key and OpenAI model are configured, but never their values. Uploaded papers are saved
to `papers/` and each run's output goes to `results/<title>-<timestamp>/`, the same
files the command line writes. Runs started from the command line also appear in the
list of previous runs. Runs go one at a time, because they share the conference memory.

By default the site only accepts connections from this computer. `--port` changes
the port and `--no-browser` stops it opening a tab. Stop it with Ctrl+C.

## Old Excel scraper

The previous spreadsheet scraper (INOMICS and The Economic Misfit → `conferences.xlsx`)
now lives in [`old/`](old/) and is no longer developed. It still runs from that folder:

```bash
python -m pip install '.[legacy]'
cd old && python run.py --include 'political economy, development' --exclude 'macro-finance'
```

It reads `OPENAI_API_KEY` from the root `.env` and its model from `old/config.json`.
See [old/legacy_scraper.md](old/legacy_scraper.md) for its full documentation.

## Outputs

| File | Contents |
| --- | --- |
| `report.md` | Ranked shortlist, 2–4 fit sentences per venue, evidence, constraints and submission strategy |
| `candidates.csv` | One row per candidate or watchlist series; evidence and scoring criteria included as JSON columns |
| `results.json` | Pydantic-validated run, candidates, source text, quotes, publication/retrieval/extraction times, issues and scores |
| `watchlist.md`, `watchlist.csv` | Recurring series without a confirmed current call, kept separate from open opportunities |
| `queries.json` | Queries planned/executed and their discovery families |
| `researchers.json` | Cited researcher connections and user-supplied names |
| `sources.json` | Retrieval manifest, content hashes, cache flags, publication metadata and raw-snapshot paths |
| `conference_memory.json` | Export of persistent edition-specific conference memory |
| `run.log` | Structured JSON-lines diagnostics, excluding credentials and API response bodies |

Open verified calls come first. Unconfirmed deadlines, unknown dates, preferences
mismatches and closed editions have separate sections. Unknown geography/dates
remain visible with explicit constraints; confirmed mismatches are excluded from
the recommended submission strategy. Unstated fees, funding and eligibility remain
unknown. Publication metadata is labelled as page metadata; it is never replaced
with a guessed publication date. Report evidence references are local `E1`, `E2`,
etc., each with a direct source link and retrieval time.

The main CSV includes watchlist rows once, distinguished by `status=WATCHLIST`.
`watchlist.csv` is a convenience subset. CSV text that could execute as a spreadsheet
formula is escaped. Scores are rounded to five-point increments and criterion
scores to ten-point increments; the JSON records the weights and supporting evidence.

## Verification rules

| Status | Required evidence |
| --- | --- |
| `OPEN_VERIFIED` | Retrieved official source, explicit edition identity, an actual paper invitation/open signal, and a definitely future submission cutoff; no unresolved official date conflict or invite-only restriction |
| `FUTURE_DEADLINE_UNCONFIRMED` | A future reported cutoff, or a same-day cutoff whose time/timezone cannot be established, with some required confirmation missing |
| `DATE_UNKNOWN` | A retrieved candidate/CFP whose submission date cannot be established |
| `WATCHLIST` | Evidence of a recurring relevant series without a confirmed current call; no projected future edition/deadline |
| `CLOSED` | Observed edition has occurred, submission cutoff has definitely passed, or retrieved call explicitly closes submissions |

Search snippets are leads only and cannot verify a call. A 2026 call never creates
a 2027 call. An explicitly dated next-year call is valid even when its deadline is
in the preceding year. Missing years and ambiguous numeric dates are not inferred;
unrecognised formats stay unknown. Notification and registration milestones cannot
supply submission deadlines. Notification dates are marked as estimates only when
their source says so; no typical review interval is projected forward.

Precise cutoffs require a stated time and an unambiguous timezone. UTC/GMT offsets,
IANA zones, common explicit CET/CEST/EST/EDT/PST/PDT offsets, ET/PT date-aware zones
and Anywhere on Earth are supported. CST/IST/BST remain ambiguous. Date-only
deadlines use conservative timezone bounds: a same-day call stays unconfirmed
until a definitely future cutoff can be established. Explicit extensions supersede
the old date on that page; unresolved multiple tracks/deadlines block verification.

Official authority uses recognised organiser domains with matching page identity,
academic domains with a matching explicit organiser/host, or a same-edition original
CFP linked by an already verified organiser. A university domain alone is
insufficient. Unknown custom event domains remain unconfirmed. After checking a
domain yourself, add it to `OFFICIAL_DOMAINS` or `--official-domains`. This is an
explicit trust override and should be restricted to organisers you have reviewed.
An LLM cannot grant official authority or override deterministic status checks.
Named organisers/speakers in the structured candidate fields require official
evidence. Unconfirmed named claims remain in the evidence ledger.

Conflicting source claims are retained. An official deadline takes precedence
over an aggregator. Conflicting official deadlines downgrade verification. Original
and final source URLs, exact supporting text, publication metadata, extraction and
retrieval times are preserved. Source caching defaults to one hour and is capped
at six hours; cached retrieval timestamps are never rewritten. Use `--refresh`
for a new search and source retrieval. Search caching defaults to 12 hours.

## Research-value ranking

- 35% intellectual/topic fit
- 20% audience relevance, using verified scope and people where available
- 20% academic quality and alignment with the research goal
- 15% methodological fit
- 10% location, stated cost/funding and timing

The LLM judges only supplied paper information and verified/reported evidence,
with indexed citations. Evidence-free audience/quality scores are capped. Documented
discussants, review format and specialist feedback matter; prestige does not supply
acceptance probabilities. Fit explanations and proposed framings are explicitly
interpretive judgements, not source claims. No acceptance probability is estimated.

Without LLM configuration, conservative labelled-field extraction and an explicit
concept dictionary provide a usable fallback. Its scores have **low confidence**
and natural-language CFPs can leave more fields unknown. The configured LLM handles
semantic interpretation, aliases and research communities. This is especially
useful for relevant institutional workshops whose titles do not repeat the paper's
keywords. Semantic aliases still need a consistent edition and corroborating
organiser/date/location information before merging.

## Persistent conference memory

Default state lives in `.conference-agent/`; reuse that directory on subsequent
runs, or set `CONFERENCE_STATE_DIR` / `--state-dir`. SQLite stores edition-specific
snapshots and an observation history. Previously observed sources are rechecked,
and series names seed new current-horizon searches. `memory_changes` distinguishes
newly verified calls, newly verified editions, and deadline/status changes. A
historical observation can produce a series watchlist entry, with its historical
edition labelled and dates cleared. Stored deadlines are never rolled forward.

No recurring task is installed. Re-run on demand; weekly scheduling can be added
after checking the source quality for your research communities. Demo memory is
isolated under each demo output and cannot seed live discovery accidentally.

## Search scope, access and cost controls

Queries vary the central research question, topic, methods, contribution,
neighbouring disciplines, specialist workshops, universities, researcher networks
and recurring series. Community queries cover CEPR, EEA, EPCS, ECPR, EPSA, ESOC,
ReCIPE, NBER, CESifo, IZA and HiCN. Names here are search targets, not asserted
current open calls. Prior memory supplements those searches.

Defaults: 36 initial queries, up to 8 official-source resolution queries and 6
researcher queries, 5 hits per provider/query, and at most 40 source-retrieval
attempts including failed pages and original-CFP follow-ups. `auto` can use two
providers per query. The LLM makes additional paper/query/extraction/evaluation
calls within that source budget. Reduce these limits for faster, cheaper runs.
Coverage is bounded and is never presented as exhaustive.

The crawler identifies itself, checks `robots.txt` for each origin/redirect,
honours crawl delays and Retry-After, throttles per host, and retries transient
failures with bounded backoff. It defers long rate-limit waits to a later run.
Missing robots files (404/410) permit access; unreadable/restricted robots files
leave a source unverified. Site-specific terms also apply: review them for your
sources and use `BLOCKED_DOMAINS` / `--blocked-domains` for prohibited domains.
Explicit automated-access prohibitions on retrieved pages are respected.

The agent never logs into sites, solves CAPTCHAs, renders around anti-bot gates or
bypasses a 401/403. Login pages, scanned PDFs, JavaScript-only sources and restricted
CFPs remain unverified. Public source downloads are capped at 6 MB, paper inputs
at 50 MB. Original public HTML/PDF/text is content-addressed under `sources/` in
the state directory. Local/private network URLs and nonstandard ports are rejected.

Exit codes: `0` completed (possibly partial; check `run_status` and `issues`),
`2` input/configuration required, `3` every search request failed. Partial failures
are reported alongside usable results. A result-free search does not establish
that no suitable conference exists.

## Offline demonstrations and tests

```bash
python discover.py --demo A --regions europe,north-america --output demos/topic_a
python discover.py --demo B --regions europe,north-america --output demos/topic_b
python discover.py --paper examples/forbearance.md --plan-only --output demos/query_plan
python -m pytest -q
ruff check conference_agent tests discover.py
ruff format --check conference_agent tests discover.py
```

`--demo` executes the complete search → retrieval → extraction → verification →
ranking → memory → report pipeline through synthetic providers. All venues,
people, URLs and deadlines are fictitious and prominently labelled. The `.example`
links do not work. Fixture dates are generated relative to runtime so examples
remain useful. The demos exercise open, unconfirmed, unknown, closed and watchlist
outcomes without claiming internet access. Live provider and LLM integration is
unit-tested with mocked HTTP/SDK responses; real authenticated calls require keys.

Tests cover outdated editions, date/timezone boundaries, registration/notification
separation, conflicting official/third-party sources, prompt/claim grounding,
deduplication, conference memory, public-source caching, robots/redirect restrictions,
provider failures, paper/PDF parsing, no-credential behavior and report round-trips.

## Architecture

| Module | Responsibility |
| --- | --- |
| `paper_parser.py` | PDF/Markdown/TXT input and semantic/heuristic research profile |
| `query_generator.py` | Varied query families and community/researcher follow-ups |
| `search_provider.py` | Brave/Tavily adapters, provider protocol and cached searches |
| `source_fetcher.py`, `network.py` | Respectful public retrieval, retries, throttling, robots and raw snapshots |
| `source_verifier.py` | Grounded claims, organiser authority, edition/status checks and deduplication |
| `deadline_extractor.py` | Explicit dates, milestone binding and conservative timezone comparisons |
| `ranking.py` | Five weighted criteria, citations, framing and preferences |
| `researchers.py` | Cited researcher connections and relevant community discovery |
| `memory.py` | Persistent series/edition observations and new-call detection |
| `report_generator.py` | Markdown/CSV/JSON shortlist, strategy and separate watchlist |
| `cache.py`, `models.py`, `config.py`, `llm.py` | TTL storage, validated schemas, configuration and structured LLM calls |
| `pipeline.py`, `discover.py` | Budgeted orchestration and CLI |

Add another search service by implementing `SearchProvider.search(query, count)`
and returning `SearchHit` objects, then register it in `configured_providers`.
Search adapters are deliberately separate from source verification: a provider's
answer or snippet can never directly create an `OPEN_VERIFIED` result.

Official API references used for the integrations:
[Brave Web Search](https://api-dashboard.search.brave.com/app/documentation/web-search),
[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search),
and [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
