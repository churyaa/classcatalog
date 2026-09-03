from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from classcatalog.catalog.models import (
    CatalogMappings,
    CatalogProgram,
    CatalogProgramDetail,
    CatalogProgramListItem,
    CatalogProgramListResponse,
    CatalogProgramRequirementGroup,
    CatalogProgramSummary,
    CatalogRequirementDetail,
    CatalogRequirementListResponse,
    CatalogRequirementSummary,
    CatalogStatus,
    StudentProfileSummary,
)
from classcatalog.models import CourseSection, ProgramClassification, ProgramTag
from classcatalog.requirements import (
    canonical_requirement_label,
    canonicalize_existing_requirement_tag,
    load_requirement_overlays,
    sort_requirement_labels,
)


def normalize_course_code(value: str) -> str:
    return " ".join(value.strip().upper().split())




# Some catalog groups enumerate many valid course choices even though students only
# complete a fixed number of selections. Keep these rules explicit and catalog-year
# scoped so the UI does not mistake every option for an individually required course.
_PROGRAM_ELECTIVE_SLOT_RULES: dict[tuple[str, str], dict[str, int]] = {
    ("Computer Science, B.S.", "2026-2027"): {
        "Upper Division Major Electives": 6,  # 18 units; listed options are 3-unit courses.
    },
}


def _program_requirement_progress(
    program: CatalogProgram,
    completed_courses: set[str],
) -> tuple[int, int]:
    rules = _PROGRAM_ELECTIVE_SLOT_RULES.get((program.name, program.catalog_year), {})
    if not rules:
        mapped = {normalize_course_code(mapping.course_code) for mapping in program.mappings}
        return len(mapped), len(mapped.intersection(completed_courses))

    fixed_courses = {
        normalize_course_code(mapping.course_code)
        for mapping in program.mappings
        if mapping.classification != ProgramClassification.ELECTIVE
    }
    required_count = len(fixed_courses)
    completed_count = len(fixed_courses.intersection(completed_courses))

    elective_groups: dict[str, set[str]] = defaultdict(set)
    for mapping in program.mappings:
        if mapping.classification != ProgramClassification.ELECTIVE:
            continue
        group = (mapping.group or mapping.classification.value).strip()
        elective_groups[group].add(normalize_course_code(mapping.course_code))

    for group, courses in elective_groups.items():
        # A course already counted as a fixed requirement should not also advance an
        # elective bucket merely because the catalog lists it as an allowed option.
        choices = courses.difference(fixed_courses)
        slots = rules.get(group)
        if slots is None:
            required_count += len(choices)
            completed_count += len(choices.intersection(completed_courses))
            continue
        effective_slots = min(slots, len(choices))
        required_count += effective_slots
        completed_count += min(
            effective_slots,
            len(choices.intersection(completed_courses)),
        )

    return required_count, completed_count


class CatalogRepository:
    def __init__(
        self,
        mappings: CatalogMappings | None = None,
        *,
        data_path: Path | None = None,
    ) -> None:
        self._mappings = mappings
        self._data_path = data_path
        self._programs_by_key: dict[tuple[str, str], CatalogProgram] = {}
        self._program_tags: dict[str, set[ProgramTag]] = defaultdict(set)
        self._requirement_tags: dict[str, set[str]] = defaultdict(set)
        self._requirement_labels: set[str] = set()

        if mappings is None:
            return

        for program in mappings.programs:
            self._programs_by_key[(program.name, program.catalog_year)] = program
            for mapping in program.mappings:
                code = normalize_course_code(mapping.course_code)
                self._program_tags[code].add(
                    ProgramTag(
                        program=program.name,
                        catalog_year=program.catalog_year,
                        classification=mapping.classification,
                    )
                )

        for requirement in mappings.requirements:
            label = canonical_requirement_label(requirement.code, requirement.name)
            if label is None:
                continue
            self._requirement_labels.add(label)
            for course_code in requirement.course_codes:
                self._requirement_tags[normalize_course_code(course_code)].add(label)

        for label, course_codes in load_requirement_overlays(mappings.catalog_year).items():
            self._requirement_labels.add(label)
            for course_code in course_codes:
                self._requirement_tags[normalize_course_code(course_code)].add(label)

    @classmethod
    def empty(cls) -> CatalogRepository:
        return cls()

    @classmethod
    def from_json(cls, path: Path) -> CatalogRepository:
        raw = json.loads(path.read_text(encoding="utf-8"))
        mappings = CatalogMappings.model_validate(raw)
        return cls(mappings, data_path=path)

    @property
    def loaded(self) -> bool:
        return self._mappings is not None

    @property
    def mappings(self) -> CatalogMappings | None:
        return self._mappings

    @property
    def data_path(self) -> Path | None:
        return self._data_path

    @property
    def programs(self) -> tuple[CatalogProgram, ...]:
        if self._mappings is None:
            return ()
        return self._mappings.programs

    @property
    def program_names(self) -> tuple[str, ...]:
        return tuple(sorted({program.name for program in self.programs}))

    @property
    def catalog_years(self) -> tuple[str, ...]:
        return tuple(sorted({program.catalog_year for program in self.programs}, reverse=True))

    @property
    def classifications(self) -> tuple[ProgramClassification, ...]:
        present = {
            mapping.classification
            for program in self.programs
            for mapping in program.mappings
        }
        return tuple(value for value in ProgramClassification if value in present)

    @property
    def requirement_labels(self) -> tuple[str, ...]:
        return sort_requirement_labels(self._requirement_labels)

    @property
    def mapped_course_codes(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._program_tags) | set(self._requirement_tags)))

    def status(self) -> CatalogStatus:
        mappings = self._mappings
        return CatalogStatus(
            loaded=mappings is not None,
            institution=mappings.institution if mappings is not None else None,
            catalog_years=self.catalog_years,
            programs=len(self.programs),
            requirements=len(mappings.requirements) if mappings is not None else 0,
            mapped_course_codes=len(self.mapped_course_codes),
            generated_at=mappings.generated_at if mappings is not None else None,
            source_index_url=mappings.source_index_url if mappings is not None else None,
            data_file=str(self._data_path) if self._data_path is not None else None,
            warnings=mappings.warnings if mappings is not None else (),
        )

    def enrich_section(self, section: CourseSection) -> CourseSection:
        code = normalize_course_code(section.course_code)
        requirement_tags = {
            canonical
            for raw_tag in section.requirement_tags
            if (canonical := canonicalize_existing_requirement_tag(raw_tag)) is not None
        }
        requirement_tags.update(self._requirement_tags.get(code, ()))
        program_tags = set(section.program_tags)
        program_tags.update(self._program_tags.get(code, ()))
        if (
            requirement_tags == set(section.requirement_tags)
            and program_tags == set(section.program_tags)
        ):
            return section
        return section.model_copy(
            update={
                "requirement_tags": sort_requirement_labels(requirement_tags),
                "program_tags": tuple(
                    sorted(
                        program_tags,
                        key=lambda tag: (
                            tag.program,
                            tag.catalog_year,
                            tag.classification.value,
                        ),
                    )
                ),
            }
        )

    def enrich_sections(self, sections: Sequence[CourseSection]) -> tuple[CourseSection, ...]:
        return tuple(self.enrich_section(section) for section in sections)

    def get_program(self, name: str, catalog_year: str) -> CatalogProgram | None:
        return self._programs_by_key.get((name, catalog_year))

    def program_summary(
        self,
        name: str,
        catalog_year: str,
        *,
        scheduled_course_codes: Iterable[str] = (),
    ) -> CatalogProgramSummary | None:
        program = self.get_program(name, catalog_year)
        if program is None:
            return None
        mapped_by_classification: dict[ProgramClassification, set[str]] = defaultdict(set)
        for mapping in program.mappings:
            mapped_by_classification[mapping.classification].add(
                normalize_course_code(mapping.course_code)
            )
        scheduled = {normalize_course_code(code) for code in scheduled_course_codes}
        mapped = (
            set().union(*mapped_by_classification.values())
            if mapped_by_classification
            else set()
        )
        return CatalogProgramSummary(
            name=program.name,
            catalog_year=program.catalog_year,
            source_url=program.source_url,
            degree_type=program.degree_type,
            mapped_course_count=len(mapped),
            major_prep_count=len(
                mapped_by_classification.get(ProgramClassification.MAJOR_PREP, set())
            ),
            major_course_count=len(
                mapped_by_classification.get(ProgramClassification.MAJOR_COURSE, set())
            ),
            elective_count=len(
                mapped_by_classification.get(ProgramClassification.ELECTIVE, set())
            ),
            scheduled_course_count=len(mapped.intersection(scheduled)),
        )


    def list_programs(
        self,
        *,
        catalog_year: str | None = None,
        query: str | None = None,
        degree_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> CatalogProgramListResponse:
        normalized_query = " ".join((query or "").casefold().split())
        normalized_degree = (degree_type or "").casefold().strip()
        programs = []
        for program in self.programs:
            if catalog_year and program.catalog_year != catalog_year:
                continue
            if normalized_query and normalized_query not in program.name.casefold():
                continue
            if normalized_degree and (program.degree_type or "").casefold() != normalized_degree:
                continue
            programs.append(program)

        programs.sort(key=lambda item: (item.name.casefold(), item.catalog_year), reverse=False)
        total = len(programs)
        page_size = min(max(page_size, 1), 100)
        total_pages = max(1, math.ceil(total / page_size))
        page = min(max(page, 1), total_pages)
        start = (page - 1) * page_size
        end = start + page_size

        items = tuple(self._program_list_item(program) for program in programs[start:end])
        return CatalogProgramListResponse(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            items=items,
        )

    def _program_list_item(self, program: CatalogProgram) -> CatalogProgramListItem:
        by_classification: dict[ProgramClassification, set[str]] = defaultdict(set)
        for mapping in program.mappings:
            by_classification[mapping.classification].add(
                normalize_course_code(mapping.course_code)
            )
        mapped = set().union(*by_classification.values()) if by_classification else set()
        return CatalogProgramListItem(
            name=program.name,
            catalog_year=program.catalog_year,
            source_url=program.source_url,
            degree_type=program.degree_type,
            mapped_course_count=len(mapped),
            major_prep_count=len(
                by_classification.get(ProgramClassification.MAJOR_PREP, set())
            ),
            major_course_count=len(
                by_classification.get(ProgramClassification.MAJOR_COURSE, set())
            ),
            elective_count=len(
                by_classification.get(ProgramClassification.ELECTIVE, set())
            ),
            warning_count=len(program.warnings),
        )

    def program_detail(self, name: str, catalog_year: str) -> CatalogProgramDetail | None:
        program = self.get_program(name, catalog_year)
        if program is None:
            return None

        grouped: dict[tuple[ProgramClassification, str], dict[str, set[str]]] = {}
        for mapping in program.mappings:
            group_name = (mapping.group or mapping.classification.value).strip()
            key = (mapping.classification, group_name)
            bucket = grouped.setdefault(key, {"courses": set(), "notes": set()})
            bucket["courses"].add(normalize_course_code(mapping.course_code))
            bucket["notes"].update(note.strip() for note in mapping.notes if note.strip())

        classification_order = {
            ProgramClassification.MAJOR_PREP: 0,
            ProgramClassification.MAJOR_COURSE: 1,
            ProgramClassification.ELECTIVE: 2,
        }
        groups = tuple(
            CatalogProgramRequirementGroup(
                classification=classification,
                group=group_name,
                course_codes=tuple(sorted(values["courses"])),
                notes=tuple(sorted(values["notes"])),
            )
            for (classification, group_name), values in sorted(
                grouped.items(),
                key=lambda item: (
                    classification_order.get(item[0][0], 99),
                    item[0][1].casefold(),
                ),
            )
        )
        mapped = {normalize_course_code(mapping.course_code) for mapping in program.mappings}
        return CatalogProgramDetail(
            name=program.name,
            catalog_year=program.catalog_year,
            source_url=program.source_url,
            department_url=program.department_url,
            degree_type=program.degree_type,
            mapped_course_count=len(mapped),
            groups=groups,
            unmapped_course_codes=tuple(
                sorted(normalize_course_code(code) for code in program.unmapped_course_codes)
            ),
            warnings=program.warnings,
        )

    def list_requirements(
        self,
        *,
        catalog_year: str | None = None,
    ) -> CatalogRequirementListResponse:
        if self._mappings is None:
            return CatalogRequirementListResponse(total=0, items=())
        requirements = [
            requirement
            for requirement in self._mappings.requirements
            if catalog_year is None or requirement.catalog_year == catalog_year
        ]
        requirements.sort(key=lambda item: (item.code.casefold(), item.name.casefold()))
        items = tuple(
            CatalogRequirementSummary(
                code=requirement.code,
                name=requirement.name,
                display_name=requirement.display_name,
                catalog_year=requirement.catalog_year,
                source_url=requirement.source_url,
                course_count=len({normalize_course_code(code) for code in requirement.course_codes}),
            )
            for requirement in requirements
        )
        return CatalogRequirementListResponse(total=len(items), items=items)

    def get_requirement(
        self,
        code: str,
        catalog_year: str,
    ) -> CatalogRequirementDetail | None:
        if self._mappings is None:
            return None
        code_key = code.casefold().strip()
        for requirement in self._mappings.requirements:
            if (
                requirement.catalog_year == catalog_year
                and requirement.code.casefold().strip() == code_key
            ):
                return CatalogRequirementDetail(
                    code=requirement.code,
                    name=requirement.name,
                    display_name=requirement.display_name,
                    catalog_year=requirement.catalog_year,
                    source_url=requirement.source_url,
                    course_codes=tuple(
                        sorted({normalize_course_code(value) for value in requirement.course_codes})
                    ),
                    notes=requirement.notes,
                )
        return None

    def student_profile_summary(
        self,
        name: str,
        catalog_year: str,
        *,
        scheduled_course_codes: Iterable[str],
        completed_courses: Iterable[str] = (),
    ) -> StudentProfileSummary | None:
        program = self.get_program(name, catalog_year)
        if program is None:
            return None

        by_classification: dict[ProgramClassification, set[str]] = defaultdict(set)
        for mapping in program.mappings:
            by_classification[mapping.classification].add(
                normalize_course_code(mapping.course_code)
            )
        mapped = set().union(*by_classification.values()) if by_classification else set()
        scheduled = {normalize_course_code(code) for code in scheduled_course_codes}
        completed = {normalize_course_code(code) for code in completed_courses}
        remaining_scheduled = sorted(mapped.intersection(scheduled).difference(completed))
        required_course_count, completed_required_course_count = _program_requirement_progress(
            program, completed
        )

        return StudentProfileSummary(
            program=program.name,
            catalog_year=program.catalog_year,
            source_url=program.source_url,
            completed_courses=tuple(sorted(completed)),
            mapped_course_count=len(mapped),
            scheduled_course_count=len(mapped.intersection(scheduled)),
            completed_mapped_course_count=len(mapped.intersection(completed)),
            required_course_count=required_course_count,
            completed_required_course_count=completed_required_course_count,
            remaining_scheduled_course_count=len(remaining_scheduled),
            major_prep_courses=tuple(
                sorted(by_classification.get(ProgramClassification.MAJOR_PREP, set()))
            ),
            major_course_courses=tuple(
                sorted(by_classification.get(ProgramClassification.MAJOR_COURSE, set()))
            ),
            elective_courses=tuple(
                sorted(by_classification.get(ProgramClassification.ELECTIVE, set()))
            ),
            remaining_scheduled_courses=tuple(remaining_scheduled),
        )
