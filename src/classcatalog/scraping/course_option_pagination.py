from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from classcatalog.scraping.class_post import (
    PeopleSoftActionPost,
    build_people_soft_action_post,
)
from classcatalog.scraping.facet_parser import normalize_space

_DISPLAY_MORE_RE = re.compile(r"^Display\s+(?P<count>\d+)\s+More$", re.I)
_SUBMIT_ACTION_RE = re.compile(
    r"submitAction_[^(]*\([^,]+,\s*['\"](?P<action>[^'\"]+)['\"]",
    re.I,
)


class CourseOptionPaginationError(ValueError):
    """Raised when the Course Information option-grid pager is ambiguous."""


@dataclass(frozen=True, slots=True)
class CourseOptionExpandAction:
    """One PeopleSoft action that reveals more enrollment-option rows."""

    action_id: str
    display_count: int
    label: str


def _control_label(control: Tag) -> str:
    if control.name.casefold() == "input":
        value = control.get("value")
        return normalize_space(value) if isinstance(value, str) else ""
    return normalize_space(control.get_text(" ", strip=True))


def _action_id(control: Tag) -> str | None:
    href = control.get("href")
    if isinstance(href, str):
        match = _SUBMIT_ACTION_RE.search(href)
        if match is not None:
            return match.group("action")

    onclick = control.get("onclick")
    if isinstance(onclick, str):
        match = _SUBMIT_ACTION_RE.search(onclick)
        if match is not None:
            return match.group("action")

    control_id = control.get("id")
    if isinstance(control_id, str) and control_id:
        return control_id
    name = control.get("name")
    if isinstance(name, str) and name:
        return name
    return None


def find_course_option_expand_action(html: str) -> CourseOptionExpandAction | None:
    """Find PeopleSoft's dynamic ``Display N More`` option-grid action.

    SDSU's Course Information grid does not use a conventional next-page link. When
    more than the currently rendered options exist, PeopleSoft emits a stateful
    ``Display N More`` control (currently ``SSR_CLSRCH_F_WK_SSR_CHANGE_BTN``). The
    visible label is the stable contract; the action ID is discovered from the page.
    """

    soup = BeautifulSoup(html, "html.parser")
    discovered: list[CourseOptionExpandAction] = []
    for control in soup.find_all(["a", "button", "input"]):
        if not isinstance(control, Tag):
            continue
        label = _control_label(control)
        match = _DISPLAY_MORE_RE.fullmatch(label)
        if match is None:
            continue
        action_id = _action_id(control)
        if action_id is None:
            raise CourseOptionPaginationError(
                f"Course option pager {label!r} did not expose a PeopleSoft action ID."
            )
        discovered.append(
            CourseOptionExpandAction(
                action_id=action_id,
                display_count=int(match.group("count")),
                label=label,
            )
        )

    unique = {(item.action_id, item.display_count): item for item in discovered}
    if not unique:
        return None
    if len(unique) > 1:
        rendered = ", ".join(
            f"{item.label!r} -> {item.action_id!r}" for item in unique.values()
        )
        raise CourseOptionPaginationError(
            f"Course option grid exposed more than one Display More action: {rendered}."
        )
    return next(iter(unique.values()))


def build_course_option_expand_post(
    html: str,
    *,
    current_url: str,
    action: CourseOptionExpandAction,
) -> PeopleSoftActionPost:
    """Rebuild fresh PeopleSoft form state for one ``Display N More`` click."""

    return build_people_soft_action_post(
        html,
        current_url=current_url,
        action_id=action.action_id,
    )
