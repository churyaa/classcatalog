from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import RLock

from classcatalog.catalog.repository import CatalogRepository
from classcatalog.filters import SearchFilters, matches, normalize_campus, sort_sections
from classcatalog.models import (
    CourseComponent,
    CourseLookupOption,
    CourseSection,
    GradingType,
    InstructionMode,
    ProfessorMetrics,
    ProgramClassification,
    SearchOptions,
    SearchResponse,
    SeatStatus,
    SectionCoverageAudit,
)
from classcatalog.ratings import (
    apply_cached_ratings,
    load_ratings_metrics,
    normalize_person_name,
)
from classcatalog.requirements import sort_requirement_labels


def _term_sort_key(term: str) -> tuple[int, int, str]:
    season_order = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}
    parts = term.rsplit(" ", maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        return (9999, 99, term)
    season, year_text = parts
    return (int(year_text), season_order.get(season, 98), term)


class CourseRepository:
    def __init__(
        self,
        sections: Sequence[CourseSection],
        *,
        catalog: CatalogRepository | None = None,
        ratings_record_count: int = 0,
        ratings_load_error: str | None = None,
        catalog_load_error: str | None = None,
        embedded_professors: Mapping[str, ProfessorMetrics | None] | None = None,
    ) -> None:
        self._lock = RLock()
        self._catalog = catalog or CatalogRepository.empty()
        self._ratings_record_count = ratings_record_count
        self._ratings_load_error = ratings_load_error
        self._catalog_load_error = catalog_load_error
        self._sections = self._catalog.enrich_sections(sections)
        self._embedded_professors = (
            dict(embedded_professors)
            if embedded_professors is not None
            else {section.id: section.professor for section in self._sections}
        )
        self._course_total = len(
            {
                (
                    section.term_code or section.term,
                    section.subject,
                    section.crse_id or section.course_code,
                    section.crse_offer_nbr or "",
                    section.acad_career or "",
                )
                for section in self._sections
            }
        )
        self._physical_section_total = len(
            {
                (section.term_code or section.term, section.schedule_number)
                for section in self._sections
            }
        )
        self._subject_total = len({section.subject for section in self._sections})
        self._options = self._build_options()

    @classmethod
    def from_json(
        cls,
        path: Path,
        *,
        catalog_path: Path | None = None,
        ratings_path: Path | None = None,
    ) -> CourseRepository:
        raw = json.loads(path.read_text(encoding="utf-8"))
        sections = tuple(CourseSection.model_validate(item) for item in raw)
        embedded_professors = {section.id: section.professor for section in sections}
        effective_ratings_path = ratings_path if ratings_path is not None else resolve_ratings_path()
        ratings_load_error = None
        try:
            sections, ratings_record_count = apply_cached_ratings(sections, effective_ratings_path)
        except Exception as exc:
            # Ratings are supplemental. A malformed/unreadable cache must not take
            # down class search; the app records this issue for the Admin dashboard.
            ratings_record_count = 0
            ratings_load_error = f"{type(exc).__name__}: {exc}"

        catalog = CatalogRepository.empty()
        catalog_load_error = None
        if catalog_path is not None and catalog_path.is_file():
            try:
                catalog = CatalogRepository.from_json(catalog_path)
            except Exception as exc:
                # Catalog planning filters are supplemental to the class inventory.
                catalog_load_error = f"{type(exc).__name__}: {exc}"
        return cls(
            sections,
            catalog=catalog,
            ratings_record_count=ratings_record_count,
            ratings_load_error=ratings_load_error,
            catalog_load_error=catalog_load_error,
            embedded_professors=embedded_professors,
        )

    @property
    def sections(self) -> tuple[CourseSection, ...]:
        return self._sections

    @property
    def catalog(self) -> CatalogRepository:
        return self._catalog

    @property
    def scheduled_course_codes(self) -> tuple[str, ...]:
        return tuple(sorted({section.course_code for section in self._sections}))

    @property
    def total(self) -> int:
        return len(self._sections)

    @property
    def course_total(self) -> int:
        return self._course_total

    @property
    def physical_section_total(self) -> int:
        return self._physical_section_total

    @property
    def subject_total(self) -> int:
        return self._subject_total

    @property
    def ratings_record_count(self) -> int:
        return self._ratings_record_count

    @property
    def ratings_load_error(self) -> str | None:
        return self._ratings_load_error

    @property
    def catalog_load_error(self) -> str | None:
        return self._catalog_load_error

    def reload_ratings(self, path: Path | None) -> int:
        """Reload the complete ratings cache after an Admin override edit."""

        metrics_by_name, record_count = load_ratings_metrics(path)
        changed = 0
        with self._lock:
            replaced: list[CourseSection] = []
            for section in self._sections:
                metrics = metrics_by_name.get(
                    normalize_person_name(section.instructor or ""),
                    self._embedded_professors.get(section.id),
                )
                candidate = section.model_copy(update={"professor": metrics})
                if candidate.professor != section.professor:
                    changed += 1
                replaced.append(candidate)
            self._sections = tuple(replaced)
            self._ratings_record_count = record_count
            self._ratings_load_error = None
        return changed


    def apply_seat_updates(
        self,
        updates: Mapping[tuple[str, str], Mapping[str, object]],
    ) -> int:
        """Overlay volatile seat fields onto every matching physical section.

        The production dataset is immutable on disk.  The rolling SDSU seat refresher
        updates only the in-memory section snapshot, keyed by ``(term, class number)``.
        Duplicate/cross-listed listings for one physical class therefore stay in sync.
        """

        if not updates:
            return 0
        changed_physical: set[tuple[str, str]] = set()
        allowed = {
            "seat_status",
            "seats_available",
            "seat_capacity",
            "seats_enrolled",
            "seat_updated_at",
        }
        with self._lock:
            replaced: list[CourseSection] = []
            for section in self._sections:
                key = self._physical_key(section)
                raw = updates.get(key)
                if raw is None:
                    replaced.append(section)
                    continue
                values = {name: value for name, value in raw.items() if name in allowed}
                if not values:
                    replaced.append(section)
                    continue
                candidate = section.model_copy(update=values)
                if candidate != section:
                    changed_physical.add(key)
                replaced.append(candidate)
            self._sections = tuple(replaced)
            if changed_physical:
                self._options = self._build_options()
        return len(changed_physical)

    def seat_snapshot(
        self,
        schedule_numbers: Sequence[str] = (),
    ) -> dict[tuple[str, str], CourseSection]:
        """Return one current record for each requested physical class number."""

        wanted = {str(value).strip() for value in schedule_numbers if str(value).strip()}
        snapshot: dict[tuple[str, str], CourseSection] = {}
        with self._lock:
            for section in self._sections:
                if wanted and section.schedule_number not in wanted:
                    continue
                snapshot.setdefault(self._physical_key(section), section)
        return snapshot

    def _build_options(self) -> SearchOptions:
        classifications = {
            tag.classification for item in self._sections for tag in item.program_tags
        }
        gradings = {item.grading for item in self._sections}
        instruction_modes = {item.instruction_mode for item in self._sections}
        seat_statuses = {item.seat_status for item in self._sections}
        programs = {tag.program for item in self._sections for tag in item.program_tags}
        programs.update(self._catalog.program_names)
        catalog_years = {
            tag.catalog_year for item in self._sections for tag in item.program_tags
        }
        catalog_years.update(self._catalog.catalog_years)
        requirements = {tag for item in self._sections for tag in item.requirement_tags}
        requirements.update(self._catalog.requirement_labels)
        classifications.update(self._catalog.classifications)
        campuses = ("San Diego Campus", "Imperial Valley Campus")
        course_titles: dict[str, str] = {}
        for item in self._sections:
            code = (item.course_code or "").strip().upper()
            if not code:
                continue
            title = (item.title or "").strip()
            if code not in course_titles or (not course_titles[code] and title):
                course_titles[code] = title
        for code in self._catalog.mapped_course_codes:
            normalized = (code or "").strip().upper()
            if normalized:
                course_titles.setdefault(normalized, "")
        courses = tuple(
            CourseLookupOption(course_code=code, title=course_titles[code])
            for code in sorted(course_titles)
        )
        return SearchOptions(
            terms=tuple(sorted({item.term for item in self._sections}, key=_term_sort_key)),
            courses=courses,
            campuses=campuses,
            requirements=sort_requirement_labels(requirements),
            programs=tuple(sorted(programs)),
            catalog_years=tuple(sorted(catalog_years, reverse=True)),
            classifications=tuple(
                value for value in ProgramClassification if value in classifications
            ),
            gradings=tuple(value for value in GradingType if value in gradings),
            instruction_modes=tuple(
                value for value in InstructionMode if value in instruction_modes
            ),
            seat_statuses=tuple(value for value in SeatStatus if value in seat_statuses),
        )

    def options(self) -> SearchOptions:
        return self._options

    @staticmethod
    def _course_offering_key(section: CourseSection) -> tuple[object, ...]:
        return (
            section.term_code or section.term,
            section.subject,
            section.crse_id or section.course_code,
            section.crse_offer_nbr or "",
            section.acad_career or "",
        )

    @staticmethod
    def _course_term_key(section: CourseSection) -> tuple[object, ...]:
        """Stable course identity for legacy orphan-component suppression.

        SDSU can emit secondary component rows with a different/missing CRSE_ID or
        offering number from the numbered option rows they belong to.  Course code +
        term is the reliable common identity for deciding whether a zero-unit orphan
        Activity/Lab/Tech Acts row should be shown as a standalone result.
        """
        return (
            (section.term or "").strip().casefold(),
            (section.course_code or "").strip().upper(),
        )

    @staticmethod
    def _is_secondary_component(section: CourseSection) -> bool:
        component = (section.component or "").strip().casefold()
        return any(
            token in component
            for token in ("activity", "laboratory", "lab", "tech acts", "tech act", "clinical")
        )

    @staticmethod
    def _option_group_keys(section: CourseSection) -> tuple[tuple[object, ...], ...]:
        """Return every enrollment-option group a physical section belongs to.

        PeopleSoft may reuse one physical Discussion/Lecture in several enrollment
        options, each paired with a different Activity/Lab.  ``option_group_indices``
        preserves those row-level memberships from the Class Selection table.
        """

        if section.option_group_ids:
            return tuple(
                (
                    "option-signature",
                    (section.term or section.term_code or "").strip().casefold(),
                    (section.course_code or "").strip().upper(),
                    group_id,
                )
                for group_id in section.option_group_ids
            )
        if section.source_course_key and section.option_group_indices:
            return tuple(
                (
                    "option-row",
                    (section.term or section.term_code or "").strip().casefold(),
                    section.source_course_key,
                    group_index,
                )
                for group_index in section.option_group_indices
            )
        if section.option_number is None:
            return (("section", section.id),)
        # Backward-compatible fallback for datasets built before row-level option
        # membership was preserved. Keep the PeopleSoft offering identity here so
        # repeated visible option numbers do not collapse unrelated sections.
        return (("legacy-option", *CourseRepository._course_offering_key(section), section.option_number),)

    @staticmethod
    def _display_option_number(key: tuple[object, ...], primary: CourseSection) -> int | None:
        if key and key[0] == "option-row":
            value = key[-1]
            return value if isinstance(value, int) else primary.option_number
        return primary.option_number

    @staticmethod
    def _is_primary_for_group(
        section: CourseSection,
        key: tuple[object, ...],
    ) -> bool:
        if key and key[0] == "option-signature":
            group_id = key[-1]
            return isinstance(group_id, str) and group_id in section.option_primary_group_ids
        if key and key[0] == "option-row":
            group_index = key[-1]
            return (
                isinstance(group_index, int)
                and group_index in section.option_primary_group_indices
            )
        # Older datasets do not know row roles.  Keep the previous component-priority
        # behavior as a compatibility fallback until they are rebuilt.
        return not CourseRepository._is_secondary_component(section)

    @staticmethod
    def _component_priority(section: CourseSection) -> tuple[int, str, str]:
        component = (section.component or "").casefold()
        order = (
            "lecture",
            "discussion",
            "seminar",
            "activity",
            "laboratory",
            "lab",
            "tech",
        )
        rank = next((index for index, token in enumerate(order) if token in component), 99)
        return (rank, section.section_number, section.schedule_number)

    @staticmethod
    def _linked_component(section: CourseSection) -> CourseComponent:
        return CourseComponent(
            id=section.id,
            section_number=section.section_number,
            schedule_number=section.schedule_number,
            option_number=section.option_number,
            component=section.component,
            instruction_mode=section.instruction_mode,
            instruction_mode_text=section.instruction_mode_text,
            seat_status=section.seat_status,
            seats_available=section.seats_available,
            seat_capacity=section.seat_capacity,
            seats_enrolled=section.seats_enrolled,
            seat_updated_at=section.seat_updated_at,
            waitlist_available=section.waitlist_available,
            campus=normalize_campus(section.campus),
            location=section.location,
            instructor=section.instructor,
            meetings=section.meetings,
            professor=section.professor,
        )

    def _grouped_sections(
        self,
        matched_sections: Sequence[CourseSection] | None = None,
    ) -> list[CourseSection]:
        all_groups: dict[tuple[object, ...], list[CourseSection]] = {}
        for section in self._sections:
            for key in self._option_group_keys(section):
                members = all_groups.setdefault(key, [])
                if section not in members:
                    members.append(section)

        # Legacy deep records can split a linked secondary component into a different
        # source course key from the row's primary class.  Reconcile only when the
        # relationship is unambiguous: same displayed term, course code, and row-group
        # index, with exactly one candidate group that contains an authoritative primary.
        aliases: dict[tuple[object, ...], tuple[object, ...]] = {}
        primary_groups: dict[tuple[tuple[object, ...], int], list[tuple[object, ...]]] = {}
        for key, members in all_groups.items():
            if not key or key[0] != "option-row":
                continue
            group_index = key[-1]
            if not isinstance(group_index, int) or not members:
                continue
            if not any(self._is_primary_for_group(member, key) for member in members):
                continue
            identity = (self._course_term_key(members[0]), group_index)
            primary_groups.setdefault(identity, []).append(key)

        for key, members in list(all_groups.items()):
            if not key or key[0] != "option-row" or not members:
                continue
            if any(self._is_primary_for_group(member, key) for member in members):
                continue
            group_index = key[-1]
            if not isinstance(group_index, int):
                continue
            identity = (self._course_term_key(members[0]), group_index)
            candidates = [candidate for candidate in primary_groups.get(identity, ()) if candidate != key]
            if len(candidates) != 1:
                continue
            target = candidates[0]
            target_members = all_groups[target]
            for member in members:
                if member not in target_members:
                    target_members.append(member)
            aliases[key] = target
            del all_groups[key]

        if matched_sections is None:
            selected_keys = set(all_groups)
        else:
            selected_keys = {
                aliases.get(key, key)
                for section in matched_sections
                for key in self._option_group_keys(section)
                if aliases.get(key, key) in all_groups
            }

        grouped: list[CourseSection] = []
        for key in selected_keys:
            members = sorted(all_groups[key], key=self._component_priority)
            known_primaries = [
                member for member in members if self._is_primary_for_group(member, key)
            ]
            primary = (
                sorted(known_primaries, key=self._component_priority)[0]
                if known_primaries
                else members[0]
            )
            if not known_primaries and all(
                self._is_secondary_component(member) for member in members
            ):
                # The rebuilt dataset can distinguish a legitimate Activity/Lab/Clinical
                # primary from a linked secondary component.  If row-role metadata is
                # still missing, keep the old conservative suppression and let the
                # coverage audit report the unresolved physical section.
                continue
            if (
                key and key[0] == "section"
                and primary.option_number is None
                and len(members) == 1
                and self._is_secondary_component(primary)
                and primary.units <= 0
            ):
                continue
            linked = tuple(self._linked_component(member) for member in members) if len(members) > 1 else ()
            grouped.append(
                primary.model_copy(
                    update={
                        "campus": normalize_campus(primary.campus),
                        "option_number": self._display_option_number(key, primary),
                        "linked_components": linked,
                    }
                )
            )
        return grouped

    @staticmethod
    def _physical_key(section: CourseSection) -> tuple[str, str]:
        return (section.term_code or section.term, section.schedule_number)

    def coverage_audit(self) -> SectionCoverageAudit:
        """Account for every physical SDSU section in the displayed option model."""

        grouped = self._grouped_sections()
        sections_by_id = {section.id: section for section in self._sections}
        all_physical = {self._physical_key(section) for section in self._sections}
        represented_listing_ids: set[str] = set()
        represented_physical: set[tuple[str, str]] = set()
        standalone_physical: set[tuple[str, str]] = set()
        grouped_physical: set[tuple[str, str]] = set()
        physical_option_memberships = 0

        for option in grouped:
            listing_ids = {option.id, *(component.id for component in option.linked_components)}
            represented_listing_ids.update(listing_ids)
            option_physical: set[tuple[str, str]] = set()
            for listing_id in listing_ids:
                source = sections_by_id.get(listing_id)
                if source is None:
                    continue
                option_physical.add(self._physical_key(source))
            represented_physical.update(option_physical)
            physical_option_memberships += len(option_physical)
            if option.linked_components:
                grouped_physical.update(option_physical)
            else:
                standalone_physical.update(option_physical)

        unaccounted_physical = all_physical - represented_physical
        unaccounted_listings = [
            section for section in self._sections if section.id not in represented_listing_ids
        ]
        examples: list[str] = []
        seen_examples: set[tuple[str, str]] = set()
        for section in unaccounted_listings:
            key = self._physical_key(section)
            if key not in unaccounted_physical or key in seen_examples:
                continue
            seen_examples.add(key)
            component = (section.component or "unknown").strip() or "unknown"
            examples.append(
                f"{section.course_code} schedule #{section.schedule_number} ({component})"
            )
            if len(examples) >= 20:
                break

        accounted = len(represented_physical)
        return SectionCoverageAudit(
            status="passed" if not unaccounted_physical else "failed",
            course_section_listings=len(self._sections),
            physical_sections=len(all_physical),
            displayed_options=len(grouped),
            standalone_physical_sections=len(standalone_physical),
            grouped_component_physical_sections=len(grouped_physical),
            physical_option_memberships=physical_option_memberships,
            duplicate_option_memberships=max(0, physical_option_memberships - accounted),
            accounted_physical_sections=accounted,
            unaccounted_physical_sections=len(unaccounted_physical),
            represented_course_section_listings=len(represented_listing_ids),
            unaccounted_course_section_listings=len(unaccounted_listings),
            unaccounted_examples=tuple(examples),
        )

    def search(self, filters: SearchFilters) -> SearchResponse:
        matched = [section for section in self._sections if matches(section, filters)]
        grouped = self._grouped_sections(matched)
        ordered = sort_sections(grouped, filters.sort_by)
        unfiltered_total = len(self._grouped_sections())

        page_size = min(max(filters.page_size, 1), 50)
        total_pages = max(1, math.ceil(len(ordered) / page_size))
        page = min(max(filters.page, 1), total_pages)
        start = (page - 1) * page_size
        end = start + page_size

        return SearchResponse(
            filtered_total=len(ordered),
            unfiltered_total=unfiltered_total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            items=tuple(ordered[start:end]),
        )


DATA_DIRECTORY = Path(__file__).parent / "data"
SAMPLE_DATA_PATH = DATA_DIRECTORY / "sample_sections.json"
IMPORTED_DATA_PATH = DATA_DIRECTORY / "sections.json"
CATALOG_DATA_PATH = DATA_DIRECTORY / "catalog_mappings.json"
RATINGS_DATA_PATH = DATA_DIRECTORY / "professor_ratings.json"


def resolve_data_path() -> Path:
    configured = os.getenv("CLASSCATALOG_DATA_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_file():
            raise FileNotFoundError(
                f"CLASSCATALOG_DATA_PATH does not point to a file: {configured_path}"
            )
        return configured_path
    if IMPORTED_DATA_PATH.is_file():
        return IMPORTED_DATA_PATH
    return SAMPLE_DATA_PATH


ACTIVE_DATA_PATH = resolve_data_path()


def resolve_catalog_path() -> Path | None:
    configured = os.getenv("CLASSCATALOG_CATALOG_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_file():
            raise FileNotFoundError(
                "CLASSCATALOG_CATALOG_PATH does not point to a file: "
                f"{configured_path}"
            )
        return configured_path
    if CATALOG_DATA_PATH.is_file():
        return CATALOG_DATA_PATH
    return None




def resolve_ratings_path() -> Path | None:
    configured = os.getenv("CLASSCATALOG_RATINGS_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_file():
            raise FileNotFoundError(
                "CLASSCATALOG_RATINGS_PATH does not point to a file: "
                f"{configured_path}"
            )
        return configured_path
    if RATINGS_DATA_PATH.is_file():
        return RATINGS_DATA_PATH
    return None


ACTIVE_CATALOG_PATH = resolve_catalog_path()
ACTIVE_RATINGS_PATH = resolve_ratings_path()
repository = CourseRepository.from_json(
    ACTIVE_DATA_PATH,
    catalog_path=ACTIVE_CATALOG_PATH,
    ratings_path=ACTIVE_RATINGS_PATH,
)
