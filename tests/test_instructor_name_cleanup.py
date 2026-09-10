from __future__ import annotations

import json
from pathlib import Path

from classcatalog.instructors import clean_live_instructor_name, load_instructor_cache
from classcatalog.ratings import is_placeholder_instructor


def test_people_soft_mixed_tba_and_real_name_keeps_only_real_name() -> None:
    assert clean_live_instructor_name("To Be Announced Vijayanka Nair") == "Vijayanka Nair"
    assert clean_live_instructor_name("TBA - Ada Lovelace") == "Ada Lovelace"
    assert clean_live_instructor_name("Grace Hopper - To Be Announced") == "Grace Hopper"


def test_people_soft_duplicate_values_collapse_without_turning_tba_into_a_name() -> None:
    assert clean_live_instructor_name("Vijayanka Nair Vijayanka Nair") == "Vijayanka Nair"
    duplicate_tba = clean_live_instructor_name("To Be Announced To Be Announced")
    assert is_placeholder_instructor(duplicate_tba)


def test_existing_bad_instructor_cache_is_cleaned_on_load(tmp_path: Path) -> None:
    cache = tmp_path / "instructor_cache.json"
    cache.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sections": {
                    "2267::5217": {
                        "term": "2267",
                        "schedule_number": "5217",
                        "instructor": "To Be Announced Vijayanka Nair",
                    },
                    "2267::9473": {
                        "term": "2267",
                        "schedule_number": "9473",
                        "instructor": "To Be Announced To Be Announced",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    records = load_instructor_cache(cache)

    assert records[("2267", "5217")]["instructor"] == "Vijayanka Nair"
    assert ("2267", "9473") not in records
