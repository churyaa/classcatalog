from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

_CONTROL_RE = re.compile(r"^PTS_SELECT\$(\d+)$")
_SHADOW_RE = re.compile(r"^PTS_SELECT\$chk\$(\d+)$")
_SPACE_RE = re.compile(r"\s+")


class FacetParseError(ValueError):
    """Raised when PeopleSoft facet state cannot be interpreted safely."""


class SubjectFacetNotFound(FacetParseError):
    """Raised when an exact subject facet does not exist in the result page."""


class OpenClassesFacetNotFound(FacetParseError):
    """Raised when SDSU's open-only Class Status facet cannot be located."""


@dataclass(frozen=True, slots=True)
class SubjectFacet:
    subject: str
    label: str
    index: int
    control_name: str
    control_id: str | None
    checked: bool


@dataclass(frozen=True, slots=True)
class FacetChoice:
    group: str
    label: str
    index: int
    control_name: str
    control_id: str | None
    checked: bool


@dataclass(frozen=True, slots=True)
class PeopleSoftPost:
    action_url: str
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class SelectedFilterAction:
    label: str
    action_id: str


def normalize_space(value: str) -> str:
    return _SPACE_RE.sub(" ", unescape(value).replace("\xa0", " ")).strip()


def _subject_label_matches(label: str, subject: str) -> bool:
    normalized_label = normalize_space(label).casefold()
    normalized_subject = normalize_space(subject).casefold()
    return normalized_label == normalized_subject or normalized_label.startswith(
        f"{normalized_subject}/"
    )


def _form_with_state(soup: BeautifulSoup) -> Tag:
    forms = soup.find_all("form")
    if not forms:
        raise FacetParseError("No HTML form was found in the PeopleSoft result page.")

    for form in forms:
        if not isinstance(form, Tag):
            continue
        if form.get("id") == "win0" or form.get("name") == "win0":
            return form
        if form.find("input", attrs={"name": "ICStateNum"}) is not None:
            return form
    first = forms[0]
    if not isinstance(first, Tag):
        raise FacetParseError("The PeopleSoft form could not be read.")
    return first


def _labels_for_control(soup: BeautifulSoup, control: Tag) -> tuple[str, ...]:
    labels: list[str] = []
    control_id = control.get("id")
    if isinstance(control_id, str):
        for label in soup.find_all("label", attrs={"for": control_id}):
            labels.append(normalize_space(label.get_text(" ", strip=True)))

    for attribute in ("aria-label", "title", "data-label"):
        value = control.get(attribute)
        if isinstance(value, str) and value.strip():
            labels.append(normalize_space(value))

    parent = control.parent
    if isinstance(parent, Tag):
        labels.append(normalize_space(parent.get_text(" ", strip=True)))

    nearest = control.find_parent(["li", "tr"])
    if isinstance(nearest, Tag):
        labels.append(normalize_space(nearest.get_text(" ", strip=True)))

    return tuple(dict.fromkeys(value for value in labels if value))


def _all_facet_controls(soup: BeautifulSoup) -> tuple[tuple[Tag, int, tuple[str, ...]], ...]:
    found: list[tuple[Tag, int, tuple[str, ...]]] = []
    for control in soup.find_all("input"):
        if not isinstance(control, Tag):
            continue
        name = control.get("name")
        control_id = control.get("id")
        identifier = name if isinstance(name, str) else control_id
        if not isinstance(identifier, str):
            continue
        match = _CONTROL_RE.fullmatch(identifier)
        if match is None:
            continue
        found.append((control, int(match.group(1)), _labels_for_control(soup, control)))
    return tuple(found)


def _facet_group_fieldset(soup: BeautifulSoup, group: str) -> Tag | None:
    normalized_group = normalize_space(group).casefold()
    for fieldset in soup.find_all("fieldset"):
        if not isinstance(fieldset, Tag):
            continue
        legend = fieldset.find("legend")
        if not isinstance(legend, Tag):
            continue
        label = normalize_space(legend.get_text(" ", strip=True))
        if label.casefold() == normalized_group:
            return fieldset
    return None


def _primary_control_label(scope: Tag, control: Tag) -> str | None:
    control_id = control.get("id")
    if isinstance(control_id, str):
        labels = [
            label
            for label in scope.find_all("label", attrs={"for": control_id})
            if isinstance(label, Tag)
        ]
        # Fluid checkboxes include an indicator label whose text is literally
        # ``Yes No`` followed by the human-readable ``ps-label``. Prefer the latter.
        for label in labels:
            classes = {str(value).casefold() for value in label.get("class", [])}
            if "ps-label" not in classes:
                continue
            value = normalize_space(label.get_text(" ", strip=True))
            if value:
                return value
        for label in labels:
            classes = {str(value).casefold() for value in label.get("class", [])}
            if "ps_indicator" in classes:
                continue
            value = normalize_space(label.get_text(" ", strip=True))
            if value:
                return value

    for attribute in ("aria-label", "title", "data-label"):
        value = control.get(attribute)
        if isinstance(value, str) and value.strip():
            return normalize_space(value)
    return None


def find_facet_choices(html: str, group: str) -> tuple[FacetChoice, ...]:
    """Return every dynamic ``PTS_SELECT$N`` value for one named facet group.

    PeopleSoft re-numbers facet controls after stateful POSTs, so callers must rediscover
    a choice from the current HTML instead of retaining a numeric checkbox index.
    """

    soup = BeautifulSoup(html, "html.parser")
    fieldset = _facet_group_fieldset(soup, group)
    if fieldset is None:
        return ()

    choices: list[FacetChoice] = []
    for control in fieldset.find_all("input"):
        if not isinstance(control, Tag):
            continue
        name = control.get("name")
        control_id = control.get("id")
        identifier = name if isinstance(name, str) else control_id
        if not isinstance(identifier, str):
            continue
        match = _CONTROL_RE.fullmatch(identifier)
        if match is None:
            continue
        index = int(match.group(1))
        label = _primary_control_label(fieldset, control)
        if label is None:
            continue
        control_name = name if isinstance(name, str) else f"PTS_SELECT${index}"
        choices.append(
            FacetChoice(
                group=normalize_space(group),
                label=label,
                index=index,
                control_name=control_name,
                control_id=control_id if isinstance(control_id, str) else None,
                checked=control.has_attr("checked")
                or str(control.get("aria-checked", "")).casefold() == "true",
            )
        )
    return tuple(choices)


def find_facet_choice(html: str, *, group: str, label: str) -> FacetChoice:
    normalized_label = normalize_space(label).casefold()
    matches = [
        choice
        for choice in find_facet_choices(html, group)
        if normalize_space(choice.label).casefold() == normalized_label
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        indexes = ", ".join(str(choice.index) for choice in matches)
        raise FacetParseError(
            f"More than one {group!r} facet value matched {label!r}; indexes: {indexes}."
        )
    available = "; ".join(choice.label for choice in find_facet_choices(html, group))
    raise FacetParseError(
        f"Could not locate facet value {label!r} in group {group!r}. "
        f"Available values: {available or 'none'}."
    )


def is_facet_choice_selected(html: str, *, group: str, label: str) -> bool:
    """Return whether PeopleSoft currently represents one facet value as selected.

    Fluid search results are inconsistent after a stateful facet POST. Some facet groups
    keep the selected checkbox in the available-value list with ``checked`` set; others
    remove the chosen value from that list and represent it only in the ``Selected
    Filters`` breadcrumb. Class Status does the latter for values such as ``Closed
    Classes`` on some SDSU result sets.

    The breadcrumb is therefore an authoritative fallback when the value is no longer
    present in its fieldset. The group argument is retained for the normal checkbox
    lookup and for a safe API that mirrors ``find_facet_choice``.
    """

    normalized_label = normalize_space(label).casefold()
    matching_choices = [
        choice
        for choice in find_facet_choices(html, group)
        if normalize_space(choice.label).casefold() == normalized_label
    ]
    if any(choice.checked for choice in matching_choices):
        return True

    soup = BeautifulSoup(html, "html.parser")

    # SDSU's Fluid page renders selected facets in a grid titled ``Selected Filters``.
    # Prefer its visible breadcrumb text because aria labels can add words such as
    # ``Only`` (for example, "Remove Closed Classes Only filter").
    breadcrumb_scopes: list[Tag] = []
    for tag in soup.find_all(["table", "div"]):
        if not isinstance(tag, Tag):
            continue
        title = tag.get("title")
        tag_id = tag.get("id")
        if (
            isinstance(title, str)
            and normalize_space(title).casefold() == "selected filters"
        ) or (
            isinstance(tag_id, str)
            and "breadcrumb" in tag_id.casefold()
        ):
            breadcrumb_scopes.append(tag)

    for scope in breadcrumb_scopes:
        for candidate in scope.find_all(["a", "span", "button"]):
            if not isinstance(candidate, Tag):
                continue
            visible = normalize_space(candidate.get_text(" ", strip=True))
            if visible and visible.casefold() == normalized_label:
                return True

            # Fallback for breadcrumb controls whose visible text is unavailable.
            for attribute in ("aria-label", "title", "alt"):
                raw = candidate.get(attribute)
                if not isinstance(raw, str):
                    continue
                normalized = normalize_space(raw).casefold()
                if not normalized.startswith("remove ") or not normalized.endswith(" filter"):
                    continue
                middle = normalized[len("remove ") : -len(" filter")].strip()
                if middle == normalized_label or middle == f"{normalized_label} only":
                    return True

    return False


def find_selected_filter_remove_action(
    html: str,
    *,
    labels: Iterable[str],
) -> SelectedFilterAction:
    """Find a stateful PeopleSoft Selected Filters removal action by visible label.

    SDSU can hide a selected facet value from the available checkbox list while keeping
    only its breadcrumb/removal control. This is especially important for sparse result
    sets where ``Open Classes`` is selected but the Class Status fieldset disappears.
    """

    accepted = {normalize_space(label).casefold() for label in labels}
    soup = BeautifulSoup(html, "html.parser")
    matches: dict[str, SelectedFilterAction] = {}

    for scope in soup.find_all(["table", "div"]):
        if not isinstance(scope, Tag):
            continue
        title = scope.get("title")
        scope_id = scope.get("id")
        is_selected_filters = (
            isinstance(title, str)
            and normalize_space(title).casefold() == "selected filters"
        ) or (isinstance(scope_id, str) and "breadcrumb" in scope_id.casefold())
        if not is_selected_filters:
            continue

        for candidate in scope.find_all(["a", "button"]):
            if not isinstance(candidate, Tag):
                continue
            action_id = candidate.get("id")
            if not isinstance(action_id, str) or not action_id:
                continue

            visible = normalize_space(candidate.get_text(" ", strip=True))
            normalized_visible = visible.casefold()
            aria = candidate.get("aria-label")
            normalized_aria = normalize_space(aria).casefold() if isinstance(aria, str) else ""

            matched_label: str | None = None
            for raw_label in labels:
                normalized_label = normalize_space(raw_label).casefold()
                if normalized_visible == normalized_label:
                    matched_label = normalize_space(raw_label)
                    break
                if normalized_aria in {
                    f"remove {normalized_label} filter",
                    f"remove {normalized_label} only filter",
                }:
                    matched_label = normalize_space(raw_label)
                    break
            if matched_label is None or matched_label.casefold() not in accepted:
                continue
            matches[action_id] = SelectedFilterAction(
                label=matched_label,
                action_id=action_id,
            )

    if len(matches) == 1:
        return next(iter(matches.values()))
    if len(matches) > 1:
        raise FacetParseError(
            "More than one matching Selected Filters removal action was detected: "
            + ", ".join(sorted(matches))
        )
    raise FacetParseError(
        "Could not locate a matching Selected Filters removal action for: "
        + ", ".join(labels)
    )


def build_selected_filter_remove_post(
    html: str,
    *,
    current_url: str,
    labels: Iterable[str],
) -> PeopleSoftPost:
    """Build a stateful POST that clicks a Selected Filters breadcrumb removal control."""

    soup = BeautifulSoup(html, "html.parser")
    form = _form_with_state(soup)
    action = find_selected_filter_remove_action(html, labels=labels)
    fields = list(_extract_successful_controls(form))
    _replace_field(fields, "ICAction", action.action_id)
    form_action = form.get("action")
    action_url = (
        urljoin(current_url, form_action)
        if isinstance(form_action, str) and form_action
        else current_url
    )
    return PeopleSoftPost(action_url=action_url, fields=tuple(fields))


def find_subject_facet(html: str, subject: str) -> SubjectFacet:
    soup = BeautifulSoup(html, "html.parser")
    matches: dict[int, SubjectFacet] = {}
    available_labels: list[str] = []

    for control, index, labels in _all_facet_controls(soup):
        available_labels.extend(labels)
        matching_label = next(
            (label for label in labels if _subject_label_matches(label, subject)),
            None,
        )
        if matching_label is None:
            continue

        name = control.get("name")
        control_id = control.get("id")
        control_name = name if isinstance(name, str) else f"PTS_SELECT${index}"
        matches[index] = SubjectFacet(
            subject=subject,
            label=matching_label,
            index=index,
            control_name=control_name,
            control_id=control_id if isinstance(control_id, str) else None,
            checked=control.has_attr("checked")
            or str(control.get("aria-checked", "")).casefold() == "true",
        )

    if len(matches) == 1:
        return next(iter(matches.values()))
    if len(matches) > 1:
        indexes = ", ".join(str(index) for index in sorted(matches))
        raise FacetParseError(
            f"More than one exact facet matched {subject!r}; checkbox indexes: {indexes}."
        )

    concise_labels = tuple(dict.fromkeys(label for label in available_labels if "/" in label))
    preview = "; ".join(concise_labels[:12]) or "none detected"
    raise SubjectFacetNotFound(
        f"No exact subject facet matched {subject!r}. Facet labels detected: {preview}"
    )



def find_open_classes_only_facet(html: str) -> SubjectFacet:
    """Locate SDSU's dynamic open-only Class Status checkbox.

    The live Fall 2026 page labels the control ``Open Classes`` even though PeopleSoft's
    action/breadcrumb text calls the underlying filter ``Open Classes Only``. Older
    fixtures and other PeopleSoft renderings can use the longer label directly. Accept
    both forms and continue discovering the dynamic ``PTS_SELECT$N`` index rather than
    hardcoding it.
    """

    accepted_labels = {"open classes", "open classes only"}
    soup = BeautifulSoup(html, "html.parser")
    matches: dict[int, SubjectFacet] = {}
    for control, index, labels in _all_facet_controls(soup):
        matching_label = next(
            (
                label
                for label in labels
                if normalize_space(label).casefold() in accepted_labels
            ),
            None,
        )
        if matching_label is None:
            continue
        name = control.get("name")
        control_id = control.get("id")
        control_name = name if isinstance(name, str) else f"PTS_SELECT${index}"
        matches[index] = SubjectFacet(
            subject="Open Classes Only",
            label=matching_label,
            index=index,
            control_name=control_name,
            control_id=control_id if isinstance(control_id, str) else None,
            checked=control.has_attr("checked")
            or str(control.get("aria-checked", "")).casefold() == "true",
        )

    if len(matches) == 1:
        return next(iter(matches.values()))
    if len(matches) > 1:
        indexes = ", ".join(str(index) for index in sorted(matches))
        raise FacetParseError(
            "More than one open-only Class Status facet was detected; "
            f"checkbox indexes: {indexes}."
        )
    raise OpenClassesFacetNotFound(
        "Could not locate SDSU's Open Classes/Open Classes Only Class Status facet."
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


def _extract_successful_controls(form: Tag) -> tuple[tuple[str, str], ...]:
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


def _replace_field(
    fields: list[tuple[str, str]], name: str, value: str, *, add_if_missing: bool = True
) -> None:
    first_index: int | None = None
    updated: list[tuple[str, str]] = []
    for field_name, field_value in fields:
        if field_name == name:
            if first_index is None:
                first_index = len(updated)
                updated.append((name, value))
            continue
        updated.append((field_name, field_value))
    if first_index is None and add_if_missing:
        updated.append((name, value))
    fields[:] = updated


def _remove_fields(fields: list[tuple[str, str]], names: Iterable[str]) -> None:
    rejected = set(names)
    fields[:] = [(name, value) for name, value in fields if name not in rejected]


def build_subject_facet_post(
    html: str,
    *,
    current_url: str,
    facet: SubjectFacet,
) -> PeopleSoftPost:
    soup = BeautifulSoup(html, "html.parser")
    form = _form_with_state(soup)
    fields = list(_extract_successful_controls(form))

    controls = _all_facet_controls(soup)
    checked_indexes = {
        index
        for control, index, _labels in controls
        if control.has_attr("checked")
        or str(control.get("aria-checked", "")).casefold() == "true"
    }
    checked_indexes.add(facet.index)
    all_indexes = {index for _control, index, _labels in controls}
    all_indexes.add(facet.index)

    direct_names = [name for name, _value in fields if _CONTROL_RE.fullmatch(name)]
    shadow_names = [name for name, _value in fields if _SHADOW_RE.fullmatch(name)]
    _remove_fields(fields, (*direct_names, *shadow_names))

    for index in sorted(all_indexes):
        selected = index in checked_indexes
        fields.append((f"PTS_SELECT$chk${index}", "Y" if selected else "N"))
        if selected:
            fields.append((f"PTS_SELECT${index}", "Y"))

    _replace_field(fields, "ICAction", f"PTS_SELECT${facet.index}")

    action = form.get("action")
    action_url = urljoin(current_url, action) if isinstance(action, str) and action else current_url
    return PeopleSoftPost(action_url=action_url, fields=tuple(fields))


def build_facet_choice_post(
    html: str,
    *,
    current_url: str,
    choice: FacetChoice,
) -> PeopleSoftPost:
    """Select exactly one value inside a facet group while preserving other facets.

    The result-cap splitter uses this to create disjoint/exhaustive branches such as
    ``Course Career=Undergraduate``.  Existing selections in other groups (notably the
    exact Subject facet) are retained.  Any sibling value in the target group is cleared
    so the branch represents one value, not PeopleSoft's multi-select OR behavior.
    """

    soup = BeautifulSoup(html, "html.parser")
    form = _form_with_state(soup)
    refreshed_choice = find_facet_choice(
        html,
        group=choice.group,
        label=choice.label,
    )
    group_choices = find_facet_choices(html, choice.group)
    group_indexes = {item.index for item in group_choices}

    fields = list(_extract_successful_controls(form))
    controls = _all_facet_controls(soup)
    checked_indexes = {
        index
        for control, index, _labels in controls
        if control.has_attr("checked")
        or str(control.get("aria-checked", "")).casefold() == "true"
    }
    checked_indexes.difference_update(group_indexes)
    checked_indexes.add(refreshed_choice.index)

    all_indexes = {index for _control, index, _labels in controls}
    all_indexes.update(group_indexes)
    all_indexes.add(refreshed_choice.index)

    direct_names = [name for name, _value in fields if _CONTROL_RE.fullmatch(name)]
    shadow_names = [name for name, _value in fields if _SHADOW_RE.fullmatch(name)]
    _remove_fields(fields, (*direct_names, *shadow_names))

    for index in sorted(all_indexes):
        selected = index in checked_indexes
        fields.append((f"PTS_SELECT$chk${index}", "Y" if selected else "N"))
        if selected:
            fields.append((f"PTS_SELECT${index}", "Y"))

    _replace_field(fields, "ICAction", f"PTS_SELECT${refreshed_choice.index}")

    action = form.get("action")
    action_url = urljoin(current_url, action) if isinstance(action, str) and action else current_url
    return PeopleSoftPost(action_url=action_url, fields=tuple(fields))

def build_open_classes_only_post(
    html: str,
    *,
    current_url: str,
    enabled: bool,
) -> PeopleSoftPost:
    """Build the stateful POST that explicitly toggles ``Open Classes Only``.

    We intentionally submit the Open Classes checkbox as its own PeopleSoft action before
    applying the exact subject facet. This prevents the subsequent subject action from
    inheriting SDSU's default open-only state and silently excluding full/waitlisted classes.
    """

    soup = BeautifulSoup(html, "html.parser")
    form = _form_with_state(soup)
    facet = find_open_classes_only_facet(html)
    fields = list(_extract_successful_controls(form))

    controls = _all_facet_controls(soup)
    checked_indexes = {
        index
        for control, index, _labels in controls
        if control.has_attr("checked")
        or str(control.get("aria-checked", "")).casefold() == "true"
    }
    if enabled:
        checked_indexes.add(facet.index)
    else:
        checked_indexes.discard(facet.index)

    all_indexes = {index for _control, index, _labels in controls}
    all_indexes.add(facet.index)
    direct_names = [name for name, _value in fields if _CONTROL_RE.fullmatch(name)]
    shadow_names = [name for name, _value in fields if _SHADOW_RE.fullmatch(name)]
    _remove_fields(fields, (*direct_names, *shadow_names))

    for index in sorted(all_indexes):
        selected = index in checked_indexes
        fields.append((f"PTS_SELECT$chk${index}", "Y" if selected else "N"))
        if selected:
            fields.append((f"PTS_SELECT${index}", "Y"))

    _replace_field(fields, "ICAction", f"PTS_SELECT${facet.index}")

    action = form.get("action")
    action_url = urljoin(current_url, action) if isinstance(action, str) and action else current_url
    return PeopleSoftPost(action_url=action_url, fields=tuple(fields))

