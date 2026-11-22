import io
import time
from pathlib import Path

import pytest

pytest.importorskip("flask")

from conference_agent.discover import main as discover_main  # noqa: E402
from conference_agent.web import (  # noqa: E402
    Job,
    Runner,
    build_command,
    configuration,
    create_app,
    date_range,
    safe_url,
    save_paper,
)

PAPER = Path(__file__).resolve().parents[1] / "examples" / "forbearance.md"


def demo_execute(job: Job, cwd: Path, register) -> tuple[int, str]:
    """Stand-in for the subprocess: run the offline demo in-process, no network or keys."""
    code = discover_main(["--demo", "A", f"--output={job.output}", f"--env-file={cwd / 'none.env'}"])
    return code, ""


def wait_for(runner: Runner, job_id: str, timeout: float = 30) -> Job:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = runner.get(job_id)
        if job and job.state in {"done", "failed"}:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, Runner(tmp_path, execute=demo_execute))
    app.testing = True
    return app, app.test_client()


def token(client) -> str:
    html = client.get("/").get_data(as_text=True)
    return html.split('name="token" value="', 1)[1].split('"', 1)[0]


def test_upload_runs_and_renders_results(client, tmp_path):
    app, c = client
    response = c.post(
        "/run",
        data={
            "token": token(c),
            "title": "Forbearance paper",
            "regions": ["europe", "mars"],
            "paper": (io.BytesIO(PAPER.read_bytes()), "my paper (2).md"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 303
    run_id = response.headers["Location"].rsplit("/", 1)[1]
    assert run_id.startswith("forbearance-paper-")
    assert (tmp_path / "papers" / "my_paper_2.md").read_bytes() == PAPER.read_bytes()

    job = wait_for(app.extensions["runner"], run_id)
    assert job.state == "done", job.error
    assert "--regions=europe" in job.command
    page = c.get(f"/runs/{run_id}").get_data(as_text=True)
    assert "Fictitious Annual Workshop on Conflict and Institutions" in page
    assert "Open calls" in page and "Offline demonstration" in page
    assert c.get(f"/runs/{run_id}/status").get_json()["state"] == "done"
    assert c.get(f"/runs/{run_id}/files/report.md").status_code == 200
    assert run_id in c.get("/").get_data(as_text=True)


def test_rejects_missing_token_bad_type_and_traversal(client):
    _, c = client
    paper = (io.BytesIO(b"x"), "paper.md")
    assert c.post("/run", data={"paper": paper}, content_type="multipart/form-data").status_code == 400
    bad = c.post(
        "/run",
        data={"token": token(c), "paper": (io.BytesIO(b"x"), "paper.exe")},
        content_type="multipart/form-data",
    )
    assert bad.status_code == 400 and "PDF, Markdown" in bad.get_data(as_text=True)
    assert c.get("/runs/..%2F.env").status_code == 404
    assert c.get("/runs/missing/files/run.log").status_code == 404


def test_failed_run_shows_error(tmp_path):
    def failing(job, cwd, register):
        return 2, "Configuration/input error: SEARCH_PROVIDER=tavily has no configured API key.\n"

    app = create_app(tmp_path, Runner(tmp_path, execute=failing))
    c = app.test_client()
    response = c.post(
        "/run",
        data={"token": token(c), "paper": (io.BytesIO(b"A paper"), "p.txt")},
        content_type="multipart/form-data",
    )
    run_id = response.headers["Location"].rsplit("/", 1)[1]
    assert wait_for(app.extensions["runner"], run_id).state == "failed"
    assert "no configured API key" in c.get(f"/runs/{run_id}").get_data(as_text=True)


def test_helpers(tmp_path, monkeypatch):
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("https://example.org/cfp") == "https://example.org/cfp"
    command = build_command(tmp_path / "p.pdf", tmp_path / "out", title="-x title", researchers=["A B"])
    assert "--title=-x title" in command and "--researcher=A B" in command
    first = save_paper(tmp_path, "p.pdf", b"one")
    assert save_paper(tmp_path, "p.pdf", b"one") == first
    assert save_paper(tmp_path, "p.pdf", b"two") != first

    from datetime import date

    assert date_range(date(2027, 6, 1), date(2027, 6, 3)) == "1–3 Jun 2027"

    for name in [
        "TAVILY_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "SEARCH_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    ]:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("SEARCH_PROVIDER=tavily\nTAVILY_API_KEY=secret\nOPENAI_API_KEY=k\n")
    config = configuration(tmp_path)
    assert config == {"search": "Tavily", "model": "", "has_openai_key": True}
    assert "secret" not in str(config)


def test_upload_size_limit(client, monkeypatch):
    _, c = client
    assert "up to 50 MB" in c.get("/").get_data(as_text=True)
    monkeypatch.setattr("conference_agent.web.MAX_PAPER_BYTES", 10)
    response = c.post(
        "/run",
        data={"token": token(c), "paper": (io.BytesIO(b"x" * 20), "big.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 413 and "larger than 50 MB" in response.get_data(as_text=True)
