from __future__ import annotations

import asyncio
from collections.abc import Sequence

from classcatalog.models import CourseSection
from classcatalog.scraping.base import SubjectRequest
from classcatalog.scraping.sync import run_subject_queue


class CountingSource:
    def __init__(self) -> None:
        self.requests: list[SubjectRequest] = []

    async def fetch_subject(self, request: SubjectRequest) -> Sequence[CourseSection]:
        self.requests.append(request)
        await asyncio.sleep(0)
        return ()


def test_queue_visits_every_term_subject_pair() -> None:
    source = CountingSource()
    report = asyncio.run(
        run_subject_queue(
            source,
            terms=("Summer 2026", "Fall 2026"),
            subjects=("CS", "MATH", "CIV E"),
            concurrency=2,
        )
    )
    assert report.requested == 6
    assert report.completed == 6
    assert len(source.requests) == 6
    assert not report.errors
