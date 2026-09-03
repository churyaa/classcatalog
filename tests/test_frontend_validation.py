from __future__ import annotations

from pathlib import Path

from classcatalog.frontend_validation.validator import (
    _active_subject_count,
    _logical_course_count,
    _physical_section_count,
    _pick_program,
    _pick_requirement,
    _load_catalog,
    _load_sections,
)

ROOT = Path(__file__).parents[1]


def test_frontend_validator_counts_production_concepts() -> None:
    sections = _load_sections(ROOT / "src/classcatalog/data/sample_sections.json")
    assert _logical_course_count(sections) == 13
    assert _physical_section_count(sections) == 13
    assert _active_subject_count(sections) > 0


def test_frontend_validator_selects_mapped_program_and_requirement(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        """
        {
          "catalog_year": "2026-2027",
          "generated_at": "2026-08-23T00:00:00+00:00",
          "source_index_url": "https://catalog.sdsu.edu/index.php?catoid=12",
          "programs": [{
            "name": "Computer Science, B.S.",
            "catalog_year": "2026-2027",
            "source_url": "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=1",
            "mappings": [{"course_code": "CS 150", "classification": "major_prep"}]
          }],
          "requirements": [{
            "code": "GE A1",
            "name": "Oral Communication",
            "catalog_year": "2026-2027",
            "source_url": "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1",
            "course_codes": ["COMM 103"]
          }]
        }
        """,
        encoding="utf-8",
    )
    catalog = _load_catalog(catalog_path)
    assert catalog is not None
    scheduled = {"CS 150", "COMM 103"}
    assert _pick_program(catalog, scheduled) == (
        "Computer Science, B.S.",
        "2026-2027",
        "major_prep",
    )
    assert _pick_requirement(catalog, scheduled) == "GE A1: Oral Communication"
