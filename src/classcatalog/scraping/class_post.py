from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from classcatalog.scraping.facet_parser import normalize_space

_CLASS_ACTION_ID_RE = re.compile(
    r"^SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_(?P<component_index>\d+)"
    r"(?:\$[^$]*)*\$\$(?P<row>\d+)$",
    re.I,
)
_CLASS_TEXT_RE = re.compile(
    r"^(?P<component>.*?)\s*[-–—]\s*(?P<class_number>\d+)\s*$",
)


class ClassPostParseError(ValueError):
    """Raised when a PeopleSoft class-number action cannot be parsed safely."""


class ClassNumberActionNotFound(ClassPostParseError):
    """Raised when a requested class number has no clickable PeopleSoft action."""


@dataclass(frozen=True, slots=True)
class ClassNumberAction:
    class_number: str
    action_id: str
    component: str | None
    source_row_index: int
    component_index: int = 1


@dataclass(frozen=True, slots=True)
class PeopleSoftActionPost:
    action_url: str
    fields: tuple[tuple[str, str], ...]


def _form_with_state(soup: BeautifulSoup) -> Tag:
    for form in soup.find_all("form"):
        if not isinstance(form, Tag):
            continue
        if form.find("input", attrs={"name": "ICStateNum"}) is not None:
            return form
    raise ClassPostParseError(
        "No stateful PeopleSoft form containing ICStateNum was found on the course page."
    )


def _selected_option_value(select: Tag) -> str:
    selected = select.find("option", selected=True)
    if selected is None:
        selected = select.find("option")
    if not isinstance(selected, Tag):
        return ""
    value = selected.get("value")
    if isinstance(value, str):
        return value
    return normalize_space(selected.get_text(" ", strip=True))


def _successful_controls(form: Tag) -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = []
    for control in form.find_all(["input", "select", "textarea"]):
        if not isinstance(control, Tag) or control.has_attr("disabled"):
            continue
        name = control.get("name")
        if not isinstance(name, str) or not name:
            continue

        tag_name = control.name.casefold()
        if tag_name == "select":
            fields.append((name, _selected_option_value(control)))
            continue
        if tag_name == "textarea":
            fields.append((name, control.get_text()))
            continue

        input_type = str(control.get("type", "text")).casefold()
        if input_type in {"submit", "button", "image", "file", "reset"}:
            continue
        if input_type in {"checkbox", "radio"} and not control.has_attr("checked"):
            continue
        value = control.get("value")
        fields.append((name, value if isinstance(value, str) else ""))
    return tuple(fields)


def _replace_field(fields: list[tuple[str, str]], name: str, value: str) -> None:
    replaced = False
    output: list[tuple[str, str]] = []
    for field_name, field_value in fields:
        if field_name == name:
            if not replaced:
                output.append((name, value))
                replaced = True
            continue
        output.append((field_name, field_value))
    if not replaced:
        output.append((name, value))
    fields[:] = output


def find_class_number_actions(html: str) -> tuple[ClassNumberAction, ...]:
    """Find the dynamic PeopleSoft actions behind each clickable class number.

    The numeric fragments inside the action ID are implementation details and may
    change between page loads.  Callers should locate actions by class number on
    every fresh course-information page rather than persisting an action ID.
    """

    soup = BeautifulSoup(html, "html.parser")
    actions: list[ClassNumberAction] = []
    seen: set[str] = set()

    for link in soup.find_all("a"):
        if not isinstance(link, Tag):
            continue
        action_id = link.get("id")
        if not isinstance(action_id, str):
            continue
        id_match = _CLASS_ACTION_ID_RE.fullmatch(action_id)
        if id_match is None:
            continue

        text = normalize_space(link.get_text(" ", strip=True))
        text_match = _CLASS_TEXT_RE.fullmatch(text)
        if text_match is None:
            continue
        class_number = text_match.group("class_number")
        component = normalize_space(text_match.group("component")) or None

        # A single physical class can legitimately appear in many enrollment options.
        # CHEM 100, for example, repeats the same lecture class next to dozens of
        # different laboratory choices, giving that lecture a different PeopleSoft
        # action ID in every row.  The action still opens the same class modal, so use
        # the first occurrence of each physical class number instead of treating the
        # repeated dynamic action IDs as an ambiguity.
        if class_number in seen:
            continue
        seen.add(class_number)
        actions.append(
            ClassNumberAction(
                class_number=class_number,
                action_id=action_id,
                component=component,
                source_row_index=int(id_match.group("row")),
                component_index=int(id_match.group("component_index")),
            )
        )

    return tuple(
        sorted(
            actions,
            key=lambda action: (action.source_row_index, action.component_index),
        )
    )


def find_class_number_action(html: str, class_number: str) -> ClassNumberAction:
    normalized = class_number.strip()
    for action in find_class_number_actions(html):
        if action.class_number == normalized:
            return action
    available = ", ".join(action.class_number for action in find_class_number_actions(html))
    raise ClassNumberActionNotFound(
        f"No clickable PeopleSoft action matched class {normalized!r}. "
        f"Available class numbers: {available or 'none'}."
    )


def build_people_soft_action_post(
    html: str,
    *,
    current_url: str,
    action_id: str,
) -> PeopleSoftActionPost:
    """Rebuild the current PeopleSoft form state for an arbitrary action ID."""

    soup = BeautifulSoup(html, "html.parser")
    form = _form_with_state(soup)
    fields = list(_successful_controls(form))
    _replace_field(fields, "ICAction", action_id)

    form_action = form.get("action")
    action_url = (
        urljoin(current_url, form_action)
        if isinstance(form_action, str) and form_action
        else current_url
    )
    return PeopleSoftActionPost(action_url=action_url, fields=tuple(fields))


def build_class_number_post(
    html: str,
    *,
    current_url: str,
    action: ClassNumberAction,
) -> PeopleSoftActionPost:
    """Rebuild the PeopleSoft form state for one class-number click."""

    return build_people_soft_action_post(
        html,
        current_url=current_url,
        action_id=action.action_id,
    )
