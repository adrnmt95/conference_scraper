from datetime import date

import pytest

from conference_agent.models import Status
from conference_agent.pipeline import watchlist_for
from conference_agent.source_verifier import Claim, Extraction, SourceVerifier, deduplicate


@pytest.fixture
def verifier():
    return SourceVerifier(official_domains=("demo.example",))


def test_official_current_year_call_is_verified(make_source, verifier, now):
    candidate = verifier.extract(make_source(), now)
    assert candidate.status == Status.OPEN_VERIFIED
    assert candidate.edition_year == 2027
    assert candidate.submission_url.endswith("submit")
    assert candidate.evidence[0].retrieved_at == now


def test_old_cfp_not_rolled_forward(make_source, verifier, now):
    candidate = verifier.extract(
        make_source(name="Annual Political Economy Workshop 2026", deadline="2026-01-15", dates="2026-06-01"),
        now,
    )
    assert candidate.status == Status.CLOSED
    assert candidate.edition_year == 2026
    assert candidate.deadline.date == date(2026, 1, 15)
    watch = watchlist_for([candidate], now)[0]
    assert watch.status == Status.WATCHLIST
    assert watch.edition_year is None
    assert watch.deadline is None
    assert watch.event_start is None
    assert watch.last_observed_edition == 2026
    assert "2027" not in watch.name


def test_future_deadline_on_old_edition_does_not_verify(make_source, verifier, now):
    candidate = verifier.extract(make_source(name="Political Economy Workshop 2026", dates="2027-06-01"), now)
    assert candidate.status == Status.FUTURE_DEADLINE_UNCONFIRMED
    assert candidate.discrepancies


def test_unofficial_future_call_not_verified(make_source, verifier, now):
    candidate = verifier.extract(make_source(url="https://listings.example/cfp/"), now)
    assert candidate.status == Status.FUTURE_DEADLINE_UNCONFIRMED
    assert not candidate.official_source_urls
    assert not any(e.official for e in candidate.evidence)


@pytest.mark.parametrize(
    "open_text",
    ["Registration is now open.", "Submissions are not yet open.", "Call for papers will open next month."],
)
def test_open_signal_requires_current_paper_invitation(open_text, make_source, verifier, now):
    source = make_source(open_text=open_text)
    source.text = source.text.replace("Submit your paper\n", "")
    candidate = verifier.extract(source, now)
    assert candidate.status != Status.OPEN_VERIFIED


def test_closed_overrides_future_date(make_source, verifier, now):
    assert verifier.extract(make_source(extra="Submissions are now closed."), now).status == Status.CLOSED


def test_invite_only_is_not_an_open_opportunity(make_source, verifier, now):
    c = verifier.extract(make_source(extra="Participation is by invitation only."), now)
    assert c.participation == "invite-only"
    assert c.status == Status.FUTURE_DEADLINE_UNCONFIRMED


def test_unknown_deadline_not_inferred(make_source, verifier, now):
    c = verifier.extract(make_source(deadline="15 January"), now)
    assert c.status == Status.DATE_UNKNOWN
    assert c.deadline.date is None


def test_edition_year_required(make_source, verifier, now):
    c = verifier.extract(make_source(name="Political Economy Workshop", include_dates=False), now)
    assert c.edition_year is None
    assert c.status != Status.OPEN_VERIFIED


def test_deadline_after_event_blocks_verification(make_source, verifier, now):
    c = verifier.extract(make_source(dates="2027-01-01"), now)
    assert c.status != Status.OPEN_VERIFIED
    assert any("after the stated event" in s for s in c.discrepancies)


def test_same_day_without_timezone_unconfirmed(make_source, verifier, now):
    c = verifier.extract(make_source(deadline="2026-11-04 23:59"), now)
    assert c.status == Status.FUTURE_DEADLINE_UNCONFIRMED


def test_official_conflicting_sources_recorded(make_source, verifier, now):
    a = verifier.extract(make_source(), now)
    b = verifier.extract(make_source(url="https://research.demo.example/other/", deadline="2027-01-30"), now)
    merged = deduplicate([a, b], now)
    assert len(merged) == 1
    assert merged[0].status == Status.FUTURE_DEADLINE_UNCONFIRMED
    assert any("Official deadline conflict" in x for x in merged[0].discrepancies)
    assert len(merged[0].source_urls) == 2


def test_official_beats_third_party_deadline(make_source, verifier, now):
    official = verifier.extract(make_source(), now)
    listing = verifier.extract(make_source(url="https://listings.example/cfp/", deadline="2027-01-30"), now)
    merged = deduplicate([listing, official], now)[0]
    assert merged.status == Status.OPEN_VERIFIED
    assert merged.deadline.date == date(2027, 1, 15)
    assert merged.discrepancies


def test_different_editions_not_deduplicated(make_source, verifier, now):
    a = verifier.extract(make_source(), now)
    b = verifier.extract(
        make_source(name="Annual Political Economy Workshop 2028", dates="2028-06-01", deadline="2028-01-15"),
        now,
    )
    assert len(deduplicate([a, b], now)) == 2


def test_generic_titles_at_different_institutions_not_merged(make_source, verifier, now):
    a = verifier.extract(make_source(), now)
    b = verifier.extract(
        make_source(
            url="https://other.demo.example/cfp/", organiser="Different University", dates="2027-06-03"
        ),
        now,
    )
    assert len(deduplicate([a, b], now)) == 2


def test_university_domain_is_not_sufficient_authority(make_source, now):
    c = SourceVerifier().extract(
        make_source(url="https://other.edu/cfp/", organiser="Research Institute"), now
    )
    assert c.status != Status.OPEN_VERIFIED


def test_lookalike_domain_not_trusted(make_source, now):
    c = SourceVerifier().extract(make_source(url="https://cepr.org.evil.example/cfp/", organiser="CEPR"), now)
    assert not c.official_source_urls


def test_notification_date_is_stated_estimate_only(make_source, verifier, now):
    c = verifier.extract(make_source(extra="Notification: expected March 1, 2027"), now)
    assert c.notification_date == date(2027, 3, 1)
    assert c.notification_is_estimate is True
    assert c.deadline.date == date(2027, 1, 15)


def test_llm_cannot_invent_open_signal_or_organiser(make_source, now):
    class FakeLLM:
        def parse(self, *args):
            return Extraction(
                is_academic_event=True,
                name="Political Economy Workshop 2027",
                claims=[
                    Claim(field="open", value="true", quote="Registration is now open."),
                    Claim(field="organiser", value="CEPR", quote="Organiser: Research Institute"),
                ],
            )

    source = make_source(url="https://cepr.org/cfp/", open_text="Registration is now open.")
    source.text = source.text.replace("Submit your paper\n", "")
    c = SourceVerifier(llm=FakeLLM()).extract(source, now)
    assert not c.official_source_urls
    assert c.status != Status.OPEN_VERIFIED


def test_static_submission_link_does_not_override_not_yet_open(make_source, verifier, now):
    c = verifier.extract(make_source(open_text="Submissions are not yet open."), now)
    assert c.status == Status.FUTURE_DEADLINE_UNCONFIRMED


def test_provisional_deadline_cannot_verify(make_source, verifier, now):
    c = verifier.extract(make_source(deadline="expected January 15, 2027"), now)
    assert c.status == Status.FUTURE_DEADLINE_UNCONFIRMED
    assert c.deadline.date == date(2027, 1, 15)


def test_conflicting_official_cutoff_times_block_verification(make_source, verifier, now):
    a = verifier.extract(make_source(deadline="2027-01-15 23:59 UTC"), now)
    b = verifier.extract(
        make_source(url="https://research.demo.example/second/", deadline="2027-01-15 23:59 CET"), now
    )
    merged = deduplicate([a, b], now)[0]
    assert merged.status == Status.FUTURE_DEADLINE_UNCONFIRMED
    assert merged.discrepancies


def test_remaining_track_not_marked_closed_when_another_has_passed(make_source, verifier, now):
    c = verifier.extract(make_source(deadline="2026-10-01", extra="Full paper deadline: 2027-01-15"), now)
    assert c.status == Status.FUTURE_DEADLINE_UNCONFIRMED
    assert c.deadline.date == date(2027, 1, 15)


def test_unverified_speakers_not_presented_as_verified(make_source, verifier, now):
    c = verifier.extract(
        make_source(url="https://listings.example/event/", extra="Speakers: Ada Mentor"), now
    )
    assert c.speakers == []
    assert any(e.field == "speakers" and not e.official for e in c.evidence)
