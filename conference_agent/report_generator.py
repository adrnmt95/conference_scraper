from __future__ import annotations

import csv
import html
import json
from datetime import timezone
from pathlib import Path
from urllib.parse import quote

from .models import Candidate, RunResult, Status
from .ranking import sort_candidates


def md(value: object) -> str:
    return (
        html.escape(str(value or "unknown"))
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\n", " ")
    )


def link(label: str, url: str | None) -> str:
    if not url:
        return "unknown"
    return f"[{md(label)}]({quote(url, safe=':/?&=%#@+;,~_-')})"


def deadline_label(candidate: Candidate) -> str:
    d = candidate.deadline
    if not d or not d.date:
        return "unknown"
    text = d.date.isoformat()
    if d.time:
        text += " " + d.time
    text += " " + d.timezone if d.timezone else " (timezone not stated)"
    if not d.time:
        text += "; cutoff time not stated"
    return text


def _table(candidates: list[Candidate]) -> list[str]:
    result = [
        "| Venue | Approx. fit | Status | Deadline | Official call |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for c in candidates:
        score = f"~{c.ranking.approximate_score}/100 ({c.ranking.confidence})" if c.ranking else "unknown"
        result.append(
            f"| {md(c.name)} | {score} | {c.status.value} | {md(deadline_label(c))} | "
            + link("Official source", next(iter(c.official_source_urls), None))
            + " |"
        )
    return result


def _details(c: Candidate) -> list[str]:
    lines = [
        f"### {md(c.name)}",
        "",
        " ".join(md(s) for s in c.ranking.fit_explanation) if c.ranking else "Fit not evaluated.",
        "",
        f"- Status: **{c.status.value}**; edition: {c.edition_year or 'unknown'}; last observed edition: {c.last_observed_edition or 'n/a'}.",
        f"- Organiser: {md(c.organiser)}. Host: {md(c.hosting_institution)}.",
        f"- Event: {c.event_start or 'unknown'} to {c.event_end or 'unknown'}; {md(c.location)}; region: {md(c.region)}.",
        f"- Submission: {md(c.submission_type)}; **{md(deadline_label(c))}**.",
        f"- Submission link{' (verify against official call)' if not c.official_source_urls else ''}: {link('Submission portal', c.submission_url)}.",
        f"- Participation: {c.participation}. Eligibility: {md(c.eligibility)}.",
        f"- Submission fees: {md(c.submission_fees)}. Travel funding: {md(c.travel_funding)}.",
        f"- Notification: {c.notification_date or 'unknown'}; estimate: {c.notification_is_estimate if c.notification_is_estimate is not None else 'unknown'}.",
        f"- Verified organisers: {md('; '.join(c.organisers))}. Verified speakers: {md('; '.join(c.speakers))}.",
        f"- Verified at: {c.verified_at.isoformat()}.",
    ]
    if c.ranking:
        lines += [
            "",
            f"Suggested framing: {md(c.ranking.suggested_framing)}",
            "",
            "| Criterion | Weight | Approx. score | Reason and evidence |",
            "| --- | ---: | ---: | --- |",
        ]
        for criterion in c.ranking.criteria:
            refs = ", ".join(f"E{i + 1}" for i in criterion.evidence_indices)
            lines.append(
                f"| {criterion.name} | {criterion.weight:.0%} | {criterion.score} | {md(criterion.rationale)} {refs} |"
            )
        lines.append("")
    for label, notes in [
        ("Verification", c.verification_notes),
        ("Discrepancy", c.discrepancies),
        ("Constraint", c.constraints),
    ]:
        lines += [f"- {label}: {md(note)}" for note in notes]
    lines += ["", "Evidence (interpretive fit scores are distinct from source claims):", ""]
    for i, evidence in enumerate(c.evidence):
        lines.append(
            f"- E{i + 1} [{md(evidence.field)}; {'official' if evidence.official else 'authority unconfirmed'}] "
            f"{link('Source', evidence.source_url)}: “{md(evidence.quote)}” "
            f"Retrieved {evidence.retrieved_at.isoformat()}; extracted {evidence.extracted_at.isoformat()}; "
            f"published {md(evidence.published_at)}."
        )
    return lines + [""]


def strategy(result: RunResult) -> list[str]:
    lines = ["## Recommended submission strategy", ""]
    if result.mode == "offline_demo":
        lines += [
            "**Fictitious fixture exercise: these are examples of decision rules, not submission recommendations.**",
            "",
        ]
    eligible = [
        c
        for c in sort_candidates(result.candidates)
        if c.status == Status.OPEN_VERIFIED and c.within_preferences
    ]
    if not eligible:
        lines.append(
            "No verified open call within the requested preferences is available from this run. Resolve configuration or source uncertainties before choosing a submission."
        )
    for i, c in enumerate(eligible[:5], 1):
        lines.append(
            f"{i}. Prioritize **{md(c.name)}** (~{c.ranking.approximate_score if c.ranking else '?'}). "
            f"{md(c.ranking.suggested_framing if c.ranking else '')} "
            f"Deadline: {md(deadline_label(c))}; {link('submission', c.submission_url)}; "
            f"{link('official call', next(iter(c.official_source_urls), None))}."
        )
    imminent = [
        c
        for c in eligible
        if c.deadline and c.deadline.date and 0 <= (c.deadline.date - result.completed_at.date()).days <= 21
    ]
    if imminent:
        lines += ["", "Imminent verified deadlines (within 21 days):"]
        lines += [
            f"- {md(c.name)}: {md(deadline_label(c))}."
            for c in sorted(imminent, key=lambda c: c.deadline.date)
        ]
    unverified = sum(
        c.status in {Status.FUTURE_DEADLINE_UNCONFIRMED, Status.DATE_UNKNOWN} for c in result.candidates
    )
    if unverified:
        lines += [
            "",
            f"Check original calls for the {unverified} unconfirmed candidates before submitting. Fit scores do not resolve missing deadline or participation evidence.",
        ]
    if result.watchlist:
        lines += [
            "",
            "Revisit the recurring-series watchlist on a later run. Saved memory will flag a newly verified call or edition; historical dates are never projected forward.",
        ]
    return lines + ["", "No acceptance probabilities are estimated.", ""]


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _write_csv(path: Path, candidates: list[Candidate], mode: str) -> None:
    fields = [
        "id",
        "series_key",
        "name",
        "edition_year",
        "last_observed_edition",
        "status",
        "approximate_score",
        "confidence",
        "ranking_mode",
        "criteria_json",
        "organiser",
        "hosting_institution",
        "event_start",
        "event_end",
        "location",
        "region",
        "deadline_date",
        "deadline_time",
        "deadline_timezone",
        "deadline_utc",
        "submission_type",
        "submission_url",
        "participation",
        "submission_fees",
        "travel_funding",
        "eligibility",
        "notification_date",
        "notification_is_estimate",
        "source_urls",
        "official_source_urls",
        "organisers",
        "speakers",
        "fit_explanation",
        "suggested_framing",
        "within_preferences",
        "constraints",
        "discrepancies",
        "verification_notes",
        "evidence_json",
        "verified_at",
        "mode",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for c in candidates:
            d = c.deadline
            data = c.model_dump(mode="json")
            row = {field: data.get(field) for field in fields}
            row.update(
                approximate_score=c.ranking.approximate_score if c.ranking else None,
                confidence=c.ranking.confidence if c.ranking else None,
                ranking_mode=c.ranking.mode if c.ranking else None,
                criteria_json=json.dumps(
                    [criterion.model_dump() for criterion in c.ranking.criteria], ensure_ascii=False
                )
                if c.ranking
                else None,
                deadline_date=d.date.isoformat() if d and d.date else None,
                deadline_time=d.time if d else None,
                deadline_timezone=d.timezone if d else None,
                deadline_utc=d.at.astimezone(timezone.utc).isoformat() if d and d.at else None,
                fit_explanation=" ".join(c.ranking.fit_explanation) if c.ranking else None,
                suggested_framing=c.ranking.suggested_framing if c.ranking else None,
                evidence_json=json.dumps(data["evidence"], ensure_ascii=False),
                mode=mode,
            )
            for field, value in row.items():
                if isinstance(value, (dict, list)):
                    row[field] = json.dumps(value, ensure_ascii=False)
            writer.writerow({k: _csv_safe(v) for k, v in row.items()})


def generate_reports(result: RunResult, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Research conference shortlist",
        "",
        f"**Mode: {result.mode}; run status: {md(result.run_status)}.**",
        "",
        f"Paper/topic: {md(result.paper.title)}",
        "",
        f"Generated: {result.completed_at.isoformat()}. Conference horizon: {result.preferences.event_after} to {result.preferences.event_before}.",
        "",
        f"Research goal: {md(result.preferences.goal)}. Regions: {md(', '.join(result.preferences.regions) or 'any')}.",
        "",
        "Scores use 35% topic, 20% audience, 20% quality/research goal, 15% methods, 10% practical fit. Scores are approximate; confidence measures evidence coverage. Unknown does not mean free, funded, eligible, or open.",
        "",
    ]
    if result.mode == "offline_demo":
        lines += [
            "**OFFLINE DEMONSTRATION — every venue, person, source and deadline below is synthetic. No live search took place. The .example links are deliberately non-operational.**",
            "",
        ]
    if result.issues:
        lines += ["## Run limitations", ""] + ["- " + md(issue) for issue in result.issues] + [""]
    lines += strategy(result)
    sections = [
        (
            "Verified open calls",
            [c for c in result.candidates if c.status == Status.OPEN_VERIFIED and c.within_preferences],
        ),
        (
            "Future deadlines requiring verification",
            [
                c
                for c in result.candidates
                if c.status == Status.FUTURE_DEADLINE_UNCONFIRMED and c.within_preferences
            ],
        ),
        (
            "Calls with unknown deadlines",
            [c for c in result.candidates if c.status == Status.DATE_UNKNOWN and c.within_preferences],
        ),
        (
            "Outside requested preferences",
            [c for c in result.candidates if not c.within_preferences and c.status != Status.CLOSED],
        ),
        ("Closed or historical calls", [c for c in result.candidates if c.status == Status.CLOSED]),
    ]
    for title, candidates in sections:
        lines += [f"## {title}", ""]
        candidates = sort_candidates(candidates)
        lines += _table(candidates) + [""] if candidates else ["None established in this run.", ""]
        for candidate in candidates:
            lines += _details(candidate)
    lines += [
        "## Recurring-series watchlist",
        "",
        "See `watchlist.md` for recurring events without a confirmed current open call.",
        "",
        "## Researcher connections",
        "",
    ]
    for person in result.researchers:
        lines.append(
            f"- **{md(person.name)}**: {md(person.connection)}; {link('source', person.source_url) if person.source_url else 'supplied by user'}. "
            + (f"Evidence: “{md(person.quote)}”." if person.quote else "")
        )
    if not result.researchers:
        lines.append("No researcher connection was established from retrieved evidence.")
    lines += ["", "## Conference memory changes", ""]
    lines += ["- " + md(change["kind"]) + ": " + md(change["name"]) for change in result.memory_changes] or [
        "No new verified call or status/deadline change detected."
    ]
    lines += [
        "",
        "Query plan, machine-readable evidence, source snapshots and diagnostics are saved alongside this report.",
        "",
    ]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    watch = [
        "# Recurring conference watchlist",
        "",
        f"Mode: {result.mode}. Observed at: {result.completed_at.isoformat()}.",
        "",
        "These series have no confirmed current open call in this run. A historical edition supplies context only; no future deadline or edition is inferred.",
        "",
    ]
    if result.mode == "offline_demo":
        watch += ["**Synthetic demonstration data; no live events or working submission links.**", ""]
    for candidate in sort_candidates(result.watchlist):
        watch += _details(candidate)
    if not result.watchlist:
        watch.append("No recurring series established from the available evidence.")
    (output / "watchlist.md").write_text("\n".join(watch), encoding="utf-8")
    (output / "results.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (output / "queries.json").write_text(
        json.dumps([q.model_dump() for q in result.queries], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output / "researchers.json").write_text(
        json.dumps([p.model_dump() for p in result.researchers], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output / "sources.json").write_text(
        json.dumps(
            [s.model_dump(mode="json", exclude={"text", "links"}) for s in result.sources],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_csv(output / "candidates.csv", sort_candidates(result.candidates + result.watchlist), result.mode)
    _write_csv(output / "watchlist.csv", sort_candidates(result.watchlist), result.mode)
