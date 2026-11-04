from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .models import Candidate, Status, utcnow
from .urls import hostname, series_key


class ConferenceMemory:
    """Edition-specific observations; historical deadlines are never rolled forward."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(directory / "conferences.sqlite3")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS editions (
                series_id TEXT NOT NULL, edition TEXT NOT NULL, name TEXT NOT NULL,
                status TEXT NOT NULL, url TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                snapshot TEXT NOT NULL, PRIMARY KEY(series_id, edition)
            );
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY, series_id TEXT NOT NULL, edition TEXT NOT NULL,
                observed_at TEXT NOT NULL, status TEXT NOT NULL, snapshot TEXT NOT NULL
            );
        """)

    @staticmethod
    def identity(candidate: Candidate) -> str:
        organisation = candidate.organiser or candidate.hosting_institution
        if organisation:
            context = series_key(organisation)
        else:
            context = hostname(next(iter(candidate.official_source_urls or candidate.source_urls), ""))
        return hashlib.sha256(f"{candidate.series_key}|{context}".encode()).hexdigest()[:20]

    def record(self, candidates: list[Candidate]) -> list[dict[str, str | None]]:
        changes = []
        now = utcnow().isoformat()
        with self.connection:
            for candidate in candidates:
                series_id = self.identity(candidate)
                edition = str(candidate.edition_year) if candidate.edition_year else "series"
                previous = self.connection.execute(
                    "SELECT status, snapshot FROM editions WHERE series_id=? AND edition=?",
                    (series_id, edition),
                ).fetchone()
                older = self.connection.execute(
                    "SELECT edition FROM editions WHERE series_id=? AND edition!=?", (series_id, edition)
                ).fetchall()
                if candidate.status == Status.OPEN_VERIFIED and (
                    not previous or previous[0] != Status.OPEN_VERIFIED
                ):
                    changes.append(
                        {
                            "kind": "new_verified_edition" if older and not previous else "new_verified_call",
                            "name": candidate.name,
                            "edition": str(candidate.edition_year),
                            "previous_status": previous[0] if previous else None,
                            "source_url": next(iter(candidate.official_source_urls), None),
                        }
                    )
                elif previous:
                    old_candidate = Candidate.model_validate_json(previous[1])
                    old_date = old_candidate.deadline.date if old_candidate.deadline else None
                    new_date = candidate.deadline.date if candidate.deadline else None
                    if old_date != new_date or previous[0] != candidate.status:
                        changes.append(
                            {
                                "kind": "deadline_or_status_changed",
                                "name": candidate.name,
                                "edition": edition,
                                "previous_status": previous[0],
                                "source_url": next(
                                    iter(candidate.official_source_urls or candidate.source_urls), None
                                ),
                            }
                        )
                snapshot = candidate.model_dump_json()
                url = next(iter(candidate.official_source_urls or candidate.source_urls), None)
                self.connection.execute(
                    "INSERT INTO editions VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(series_id,edition) "
                    "DO UPDATE SET name=excluded.name,status=excluded.status,url=excluded.url,"
                    "last_seen=excluded.last_seen,snapshot=excluded.snapshot",
                    (series_id, edition, candidate.name, candidate.status.value, url, now, now, snapshot),
                )
                self.connection.execute(
                    "INSERT INTO observations(series_id,edition,observed_at,status,snapshot) VALUES(?,?,?,?,?)",
                    (series_id, edition, now, candidate.status.value, snapshot),
                )
        return changes

    def seeds(self, limit: int = 10) -> list[dict[str, str]]:
        rows = self.connection.execute(
            "SELECT name,url FROM (SELECT name,url,last_seen,ROW_NUMBER() OVER "
            "(PARTITION BY series_id ORDER BY last_seen DESC,edition DESC) AS rn FROM editions WHERE url IS NOT NULL) "
            "WHERE rn=1 ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"name": series_key(name), "url": url} for name, url in rows]

    def export(self, path: Path) -> None:
        rows = self.connection.execute(
            "SELECT series_id, edition, first_seen, last_seen, snapshot FROM editions ORDER BY last_seen DESC"
        ).fetchall()
        path.write_text(
            json.dumps(
                [
                    {
                        "series_id": row[0],
                        "edition": row[1],
                        "first_seen": row[2],
                        "last_seen": row[3],
                        "event": json.loads(row[4]),
                    }
                    for row in rows
                ],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def close(self) -> None:
        self.connection.close()
