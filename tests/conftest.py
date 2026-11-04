from datetime import datetime, timezone

import pytest

from conference_agent.models import Source, SourceLink


@pytest.fixture
def now():
    return datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def make_source(now):
    def make(
        *,
        name="Annual Political Economy Workshop 2027",
        url="https://research.demo.example/cfp/",
        deadline="2027-01-15 23:59 UTC",
        dates="2027-06-01",
        open_text="We invite submissions. Submissions are now open.",
        extra="",
        organiser="Research Institute",
        include_dates=True,
    ):
        text = (
            f"{name}\nOrganiser: {organiser}\nHosted by: {organiser}\nLocation: London, United Kingdom\n"
            "Call for papers\n"
            + open_text
            + "\n"
            + (f"Submission deadline: {deadline}\n" if deadline is not None else "")
            + (f"Event dates: {dates}\n" if include_dates else "")
            + "Topics: political violence; institutions; political economy; organized crime; economic networks\n"
            "Methods: difference-in-differences; temporal graph neural networks\n"
            "Submission type: full paper\nSubmit your paper\n" + extra
        )
        return Source(
            url=url,
            final_url=url,
            title=name,
            text=text,
            links=[SourceLink(url=url + "submit", text="Submit your paper")],
            retrieved_at=now,
            extracted_at=now,
        )

    return make
