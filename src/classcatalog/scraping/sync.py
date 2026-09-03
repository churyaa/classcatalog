from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Sequence

from classcatalog.models import CourseSection
from classcatalog.scraping.base import ScheduleSource, SubjectRequest
from classcatalog.subjects import SUBJECT_ABBREVIATIONS


@dataclass(frozen=True, slots=True)
class SubjectSyncError:
    request: SubjectRequest
    message: str


@dataclass(frozen=True, slots=True)
class SyncReport:
    requested: int
    completed: int
    sections: tuple[CourseSection, ...]
    errors: tuple[SubjectSyncError, ...]


class DemoScheduleSource:
    """Small deterministic source used to exercise the bounded queue."""

    async def fetch_subject(self, request: SubjectRequest) -> Sequence[CourseSection]:
        await asyncio.sleep(0)
        return ()


async def run_subject_queue(
    source: ScheduleSource,
    *,
    terms: Sequence[str],
    subjects: Sequence[str] = SUBJECT_ABBREVIATIONS,
    concurrency: int = 2,
) -> SyncReport:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    queue: asyncio.Queue[SubjectRequest | None] = asyncio.Queue()
    for term in terms:
        for subject in subjects:
            queue.put_nowait(SubjectRequest(term=term, subject=subject))

    collected: list[CourseSection] = []
    errors: list[SubjectSyncError] = []
    completed = 0
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal completed
        while True:
            request = await queue.get()
            try:
                if request is None:
                    return
                try:
                    sections = await source.fetch_subject(request)
                    async with lock:
                        collected.extend(sections)
                except Exception as exc:  # noqa: BLE001 - per-subject failure must be recorded
                    async with lock:
                        errors.append(SubjectSyncError(request=request, message=str(exc)))
                finally:
                    async with lock:
                        completed += 1
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await queue.join()
    for _ in workers:
        queue.put_nowait(None)
    await asyncio.gather(*workers)

    return SyncReport(
        requested=len(terms) * len(subjects),
        completed=completed,
        sections=tuple(collected),
        errors=tuple(errors),
    )


async def _run_demo(terms: Sequence[str], concurrency: int) -> None:
    report = await run_subject_queue(
        DemoScheduleSource(),
        terms=terms,
        concurrency=concurrency,
    )
    print(
        f"processed {report.completed}/{report.requested} subject searches; "
        f"sections={len(report.sections)} errors={len(report.errors)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise the typed subject-sync queue")
    parser.add_argument(
        "--term",
        action="append",
        dest="terms",
        default=[],
        help="Term name; may be supplied more than once",
    )
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    terms: list[str] = args.terms or ["Summer 2026", "Fall 2026", "Spring 2027"]
    asyncio.run(_run_demo(terms, args.concurrency))


if __name__ == "__main__":
    main()
