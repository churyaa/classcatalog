from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from classcatalog.models import CourseSection


@dataclass(frozen=True, slots=True)
class SubjectRequest:
    term: str
    subject: str


class ScheduleSource(Protocol):
    async def fetch_subject(self, request: SubjectRequest) -> Sequence[CourseSection]: ...
