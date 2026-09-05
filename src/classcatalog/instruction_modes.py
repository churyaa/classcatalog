from __future__ import annotations

import re

from classcatalog.models import InstructionMode


def classify_sdsu_instruction_mode(
        value: str | None,
        *,
        has_timed_meeting: bool | None = None,
        fallback: InstructionMode | None = None,
) -> InstructionMode | None:
    if value is None:
        return fallback

    normalized = re.sub(
        r"[^a-z0-9]+",
        " ",
        value.casefold(),
    ).strip()

    # Check these before "in person" because both strings
    # can also contain the words "in person".
    if "hybrid" in normalized:
        return InstructionMode.HYBRID

    if "online with in person exams" in normalized:
        return InstructionMode.ONLINE_WITH_IN_PERSON_EXAMS

    if "asynchronous" in normalized:
        return InstructionMode.ONLINE_ASYNCHRONOUS

    if "synchronous" in normalized:
        return InstructionMode.ONLINE_SYNCHRONOUS

    if "online" in normalized:
        if has_timed_meeting is None:
            return fallback or InstructionMode.OTHER

        return (
            InstructionMode.ONLINE_SYNCHRONOUS
            if has_timed_meeting
            else InstructionMode.ONLINE_ASYNCHRONOUS
        )

    if "in person" in normalized or "face to face" in normalized:
        return InstructionMode.IN_PERSON

    return fallback or InstructionMode.OTHER