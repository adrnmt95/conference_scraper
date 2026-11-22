"""Local website: upload a paper, run the discovery agent on it, browse the results.

Start it from the project directory (where `.env` lives): `python serve.py`.
Runs execute one at a time in a subprocess, exactly as the CLI would run them, so
the website and `discover.py` produce identical outputs under `results/`.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import ipaddress
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from .models import Candidate, RunResult, Status
from .paper_parser import MAX_PAPER_BYTES
from .ranking import sort_candidates
from .urls import hostname

ALLOWED_SUFFIXES = {".pdf", ".md", ".txt"}
DOWNLOADS = ("report.md", "candidates.csv", "results.json")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,120}$")
MAX_UPLOAD_MB = MAX_PAPER_BYTES // 1048576
# The request also carries form fields and multipart framing; the file itself is checked below.
MAX_REQUEST_BYTES = MAX_PAPER_BYTES + 1024 * 1024
REGIONS = {"europe": "Europe", "north-america": "North America"}
MAX_SOURCES = 40

STATUS_LABELS = {
    Status.OPEN_VERIFIED: ("Open · verified", "open"),
    Status.FUTURE_DEADLINE_UNCONFIRMED: ("Deadline to confirm", "confirm"),
    Status.DATE_UNKNOWN: ("Deadline unknown", "unknown"),
    Status.WATCHLIST: ("Watchlist", "watch"),
    Status.CLOSED: ("Closed", "closed"),
}
CRITERION_LABELS = {
    "topic": "Topic",
    "audience": "Audience",
    "quality_and_goal": "Quality & goal",
    "methods": "Methods",
    "practical": "Practicalities",
}


@dataclass
class Job:
    id: str
    title: str
    command: list[str]
    output: Path
    state: str = "queued"  # queued | running | done | failed
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None


def _run_subprocess(
    job: Job, cwd: Path, register: Callable[[subprocess.Popen | None], None]
) -> tuple[int, str]:
    proc = subprocess.Popen(job.command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    register(proc)
    try:
        _, err = proc.communicate()
    finally:
        register(None)
    return proc.returncode, err


class Runner:
    """Runs jobs one at a time: runs share the SQLite conference memory and caches."""

    def __init__(self, cwd: Path, execute: Callable[..., tuple[int, str]] = _run_subprocess):
        self.cwd = cwd
        self.execute = execute
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.queue: queue.Queue[Job] = queue.Queue()
        self.current: subprocess.Popen | None = None
        threading.Thread(target=self._work, daemon=True).start()
        atexit.register(self.stop)

    def submit(self, job: Job) -> None:
        with self.lock:
            self.jobs[job.id] = job
        self.queue.put(job)

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def pending(self) -> list[Job]:
        with self.lock:
            return [j for j in self.jobs.values() if j.state in {"queued", "running", "failed"}]

    def position(self, job: Job) -> int:
        with self.lock:
            waiting = [j for j in self.jobs.values() if j.state in {"queued", "running"}]
        return sorted(waiting, key=lambda j: j.created).index(job) if job in waiting else 0

    def _register(self, proc: subprocess.Popen | None) -> None:
        self.current = proc

    def _work(self) -> None:
        while True:
            job = self.queue.get()
            job.state, job.started = "running", time.time()
            try:
                code, err = self.execute(job, self.cwd, self._register)
                # 0 = complete, 2 = configuration required, 3 = all searches failed. The last two
                # still write a results.json explaining what happened; anything else is an error.
                if code in (0, 2, 3) and (job.output / "results.json").exists():
                    job.state = "done"
                else:
                    job.state = "failed"
                    job.error = "\n".join(err.strip().splitlines()[-12:]) or f"Exited with code {code}."
            except Exception as exc:  # noqa: BLE001 - surface any launch failure on the page
                job.state, job.error = "failed", f"{type(exc).__name__}: {exc}"
            finally:
                job.finished = time.time()
                self.queue.task_done()

    def stop(self) -> None:
        if self.current and self.current.poll() is None:
            self.current.terminate()


# ---------------------------------------------------------------- helpers


def slugify(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug[:limit].rstrip("-") or "paper"


def save_paper(directory: Path, filename: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if target.exists() and target.read_bytes() != data:
        target = directory / f"{target.stem}-{hashlib.sha256(data).hexdigest()[:8]}{target.suffix}"
    if not target.exists():
        target.write_bytes(data)
    return target


def build_command(
    paper: Path,
    output: Path,
    *,
    title: str = "",
    regions: list[str] | None = None,
    fields: str = "",
    horizon: int = 9,
    goal: str = "",
    researchers: list[str] | None = None,
) -> list[str]:
    # `--name=value` keeps user text that starts with "-" from being read as an option.
    command = [sys.executable, "-m", "conference_agent", f"--paper={paper}", f"--output={output}"]
    command.append(f"--horizon-months={horizon}")
    if title:
        command.append(f"--title={title}")
    if regions:
        command.append(f"--regions={','.join(regions)}")
    if fields:
        command.append(f"--fields={fields}")
    if goal:
        command.append(f"--goal={goal}")
    command += [f"--researcher={name}" for name in researchers or []]
    return command


def read_progress(output: Path) -> dict[str, int]:
    searches = pages = 0
    try:
        with (output / "run.log").open(encoding="utf-8") as fh:
            for line in fh:
                if '"logger": "conference_agent.search_provider"' in line:
                    searches += 1
                elif '"event": "source_processed"' in line or '"event": "source_unverified"' in line:
                    pages += 1
    except OSError:
        pass
    return {"searches": searches, "pages": pages, "max_pages": MAX_SOURCES}


def configuration(root: Path) -> dict[str, str | bool]:
    """What is configured, as booleans and the model name only; never key values."""
    file_values = {k: v or "" for k, v in dotenv_values(root / ".env").items()}

    def value(name: str) -> str:
        return (os.environ.get(name) or file_values.get(name) or "").strip()

    providers = [
        name for name, key in [("Tavily", "TAVILY_API_KEY"), ("Brave", "BRAVE_SEARCH_API_KEY")] if value(key)
    ]
    chosen = value("SEARCH_PROVIDER").lower() or "auto"
    if chosen in {"tavily", "brave"}:
        providers = [p for p in providers if p.lower() == chosen]
    model = value("OPENAI_MODEL")
    return {
        "search": " + ".join(providers),
        "model": model if value("OPENAI_API_KEY") and model else "",
        "has_openai_key": bool(value("OPENAI_API_KEY")),
    }


def list_runs(results: Path) -> list[dict]:
    runs = []
    if not results.is_dir():
        return runs
    for folder in results.iterdir():
        path = folder / "results.json"
        if not (folder.is_dir() and RUN_ID.match(folder.name) and path.is_file()):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            completed = datetime.fromisoformat(data["completed_at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        runs.append(
            {
                "id": folder.name,
                "title": data.get("paper", {}).get("title") or folder.name,
                "completed": completed,
                "mode": data.get("mode"),
                "open": sum(c.get("status") == "OPEN_VERIFIED" for c in data.get("candidates", [])),
                "candidates": len(data.get("candidates", [])),
            }
        )
    return sorted(runs, key=lambda r: r["completed"], reverse=True)


def safe_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else None


def nice_date(value: date | None) -> str:
    return f"{value.day} {value:%b %Y}" if value else ""


def date_range(start: date | None, end: date | None) -> str:
    if not start:
        return ""
    if not end or end == start:
        return nice_date(start)
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {end:%b %Y}"
    if start.year == end.year:
        return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"
    return f"{nice_date(start)} – {nice_date(end)}"


def deadline_info(candidate: Candidate, today: date) -> dict[str, str] | None:
    d = candidate.deadline
    if not d or not d.date:
        return None
    detail = " ".join(x for x in [d.time or "", d.timezone or ""] if x) or "time not stated"
    days = (d.date - today).days
    when = "today" if days == 0 else f"in {days} days" if days > 0 else f"{-days} days ago"
    return {"date": nice_date(d.date), "detail": detail, "when": when}


def sections(result: RunResult) -> list[dict]:
    c = result.candidates
    groups = [
        ("Open calls", "Verified on an official page, with a future deadline.", True,
         [x for x in c if x.status == Status.OPEN_VERIFIED and x.within_preferences]),
        ("Deadline to confirm", "A future deadline was reported, but not every check passed. Open the call before submitting.", True,
         [x for x in c if x.status == Status.FUTURE_DEADLINE_UNCONFIRMED and x.within_preferences]),
        ("Deadline unknown", "Relevant calls whose submission date could not be established.", True,
         [x for x in c if x.status == Status.DATE_UNKNOWN and x.within_preferences]),
        ("Outside your preferences", "Matches the paper but not your region or date window.", False,
         [x for x in c if not x.within_preferences and x.status != Status.CLOSED]),
        ("Watchlist", "Recurring series without a confirmed current call. Worth checking again later.", False,
         list(result.watchlist)),
        ("Closed", "Deadline passed or submissions closed.", False,
         [x for x in c if x.status == Status.CLOSED]),
    ]  # fmt: skip
    return [
        {"title": t, "note": n, "open": o, "venues": sort_candidates(items)}
        for t, n, o, items in groups
        if items
    ]


# ---------------------------------------------------------------- app


def create_app(root: Path, runner: Runner | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES, TOKEN=secrets.token_urlsafe(24))
    papers, results = root / "papers", root / "results"
    runner = runner or Runner(root)
    app.extensions["runner"] = runner

    app.jinja_env.filters.update(safe_url=safe_url, host=lambda u: hostname(u) if u else "")
    app.jinja_env.globals.update(
        STATUS_LABELS=STATUS_LABELS,
        CRITERION_LABELS=CRITERION_LABELS,
        date_range=date_range,
        nice_date=nice_date,
        deadline_info=deadline_info,
    )

    def index_page(error: str = "", status: int = 200):
        pending = sorted(runner.pending(), key=lambda j: j.created, reverse=True)
        return (
            render_template(
                "index.html",
                max_mb=MAX_UPLOAD_MB,
                token=app.config["TOKEN"],
                config=configuration(root),
                regions=REGIONS,
                pending=pending,
                runs=list_runs(results),
                error=error,
            ),
            status,
        )

    @app.get("/")
    def index():
        return index_page()

    @app.errorhandler(413)
    def too_large(_):
        return index_page(f"That file is larger than {MAX_UPLOAD_MB} MB.", 413)

    @app.post("/run")
    def start_run():
        if not secrets.compare_digest(request.form.get("token", ""), app.config["TOKEN"]):
            abort(400, "This form has expired. Reload the page and try again.")
        upload = request.files.get("paper")
        if not upload or not upload.filename:
            return index_page("Choose a paper to upload.", 400)
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            return index_page("Upload a PDF, Markdown (.md) or text (.txt) file.", 400)
        data = upload.read()
        if len(data) > MAX_PAPER_BYTES:
            return index_page(f"That file is larger than {MAX_UPLOAD_MB} MB.", 413)
        if not data:
            return index_page("That file is empty.", 400)
        filename = secure_filename(upload.filename) or f"paper{suffix}"
        if not filename.lower().endswith(suffix):
            filename += suffix
        paper = save_paper(papers, filename, data)

        title = " ".join(request.form.get("title", "").split())[:300]
        try:
            horizon = min(max(int(request.form.get("horizon") or 9), 1), 60)
        except ValueError:
            horizon = 9
        researchers = [
            " ".join(line.split())[:120]
            for line in request.form.get("researchers", "").splitlines()
            if line.strip()
        ][:10]
        run_id = f"{slugify(title or Path(filename).stem)}-{datetime.now():%Y%m%d-%H%M%S}"
        output = results / run_id
        command = build_command(
            paper,
            output,
            title=title,
            regions=[r for r in request.form.getlist("regions") if r in REGIONS],
            fields=" ".join(request.form.get("fields", "").split())[:300],
            horizon=horizon,
            goal=" ".join(request.form.get("goal", "").split())[:200],
            researchers=researchers,
        )
        runner.submit(Job(id=run_id, title=title or Path(filename).stem, command=command, output=output))
        return redirect(url_for("run_page", run_id=run_id), code=303)

    def run_folder(run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            abort(404)
        return results / run_id

    @app.get("/runs/<run_id>")
    def run_page(run_id: str):
        folder = run_folder(run_id)
        job = runner.get(run_id)
        if job and job.state in {"queued", "running", "failed"}:
            return render_template(
                "progress.html",
                job=job,
                ahead=runner.position(job),
                progress=read_progress(folder),
                elapsed=int(time.time() - (job.started or job.created)),
            )
        path = folder / "results.json"
        if not path.is_file():
            abort(404)
        result = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
        unverified = [i for i in result.issues if i.startswith("Source unverified")]
        return render_template(
            "results.html",
            run_id=run_id,
            result=result,
            sections=sections(result),
            notes=[i for i in result.issues if not i.startswith("Source unverified")],
            unverified=unverified,
            downloads=[name for name in DOWNLOADS if (folder / name).is_file()],
            run_date=result.completed_at.astimezone().date(),
            today=date.today(),
        )

    @app.get("/runs/<run_id>/status")
    def run_status(run_id: str):
        folder = run_folder(run_id)
        job = runner.get(run_id)
        if job:
            return jsonify(state=job.state, ahead=runner.position(job), **read_progress(folder))
        if (folder / "results.json").is_file():
            return jsonify(state="done")
        abort(404)

    @app.get("/runs/<run_id>/files/<name>")
    def download(run_id: str, name: str):
        folder = run_folder(run_id)
        if name not in DOWNLOADS or not (folder / name).is_file():
            abort(404)
        return send_from_directory(folder, name, as_attachment=True)

    return app


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Local website for the conference discovery agent")
    p.add_argument("--host", default="127.0.0.1", help="Default: only this computer can connect")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="Do not open a browser tab on start")
    args = p.parse_args(argv)
    try:
        local = args.host == "localhost" or ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        local = False
    if not local:
        print(
            "Warning: listening beyond this computer. Anyone who can reach it can start runs on your API keys.",
            file=sys.stderr,
        )
    app = create_app(Path.cwd())
    url = f"http://{'127.0.0.1' if args.host in {'0.0.0.0', '::'} else args.host}:{args.port}/"
    print(f"Conference finder running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
