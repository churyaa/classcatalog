from __future__ import annotations

import json
from pathlib import Path

from classcatalog.ratings import (
    InstructorTarget,
    RmpCandidate,
    canonical_instructor_name,
    normalize_person_name,
    select_candidate,
    sync_rate_my_professors,
)
from classcatalog.repository import CourseRepository


ROOT = Path(__file__).parents[1]
SAMPLE_DATA = ROOT / "src" / "classcatalog" / "data" / "sample_sections.json"


def candidate(
    name: str,
    *,
    legacy_id: int,
    node_id: str,
    rating: float = 4.4,
    difficulty: float = 2.7,
    reviews: int = 31,
    take_again: float = 91.0,
) -> RmpCandidate:
    return RmpCandidate(
        node_id=node_id,
        legacy_id=legacy_id,
        name=name,
        department="Mathematics",
        rating=rating,
        difficulty=difficulty,
        num_reviews=reviews,
        would_take_again_percent=take_again,
    )


def test_instructor_name_normalization_handles_last_first_format() -> None:
    assert canonical_instructor_name("Thompson, Renee") == "Renee Thompson"
    assert normalize_person_name("Jesús Ayala-Candia") == "jesus ayala candia"


def test_exact_rmp_name_match_is_preferred() -> None:
    target = InstructorTarget(
        name="Renee Thompson",
        normalized_name="renee thompson",
        course_codes=("MATH150",),
    )
    chosen, confidence, reason = select_candidate(
        target,
        (
            candidate("Renee Thompson", legacy_id=1, node_id="one"),
            candidate("Rene Thompson", legacy_id=2, node_id="two"),
        ),
    )
    assert chosen is not None
    assert chosen.legacy_id == 1
    assert confidence == 1.0
    assert reason == "matched"




def test_parenthetical_preferred_name_matches_same_surname() -> None:
    target = InstructorTarget(
        name="Mark Dunster",
        normalized_name="mark dunster",
        course_codes=("MATH150",),
    )
    chosen, confidence, reason = select_candidate(
        target,
        (candidate("Timothy (Mark) Dunster", legacy_id=10, node_id="mark-dunster"),),
    )
    assert chosen is not None
    assert chosen.name == "Timothy (Mark) Dunster"
    assert confidence is not None and confidence >= 0.98
    assert reason == "matched"


def test_shortened_first_name_matches_same_surname() -> None:
    target = InstructorTarget(
        name="Satchithanandam Venkataraman",
        normalized_name="satchithanandam venkataraman",
        course_codes=("MATH150",),
    )
    chosen, confidence, reason = select_candidate(
        target,
        (candidate("Satchi Venkataraman", legacy_id=11, node_id="satchi"),),
    )
    assert chosen is not None
    assert chosen.name == "Satchi Venkataraman"
    assert confidence is not None and confidence >= 0.94
    assert reason == "matched"


def test_nickname_matching_still_requires_same_surname() -> None:
    target = InstructorTarget(
        name="Mark Dunster",
        normalized_name="mark dunster",
        course_codes=("MATH150",),
    )
    chosen, confidence, reason = select_candidate(
        target,
        (candidate("Timothy (Mark) Smith", legacy_id=12, node_id="wrong-person"),),
    )
    assert chosen is None
    assert confidence is None
    assert reason == "no_name_match"


def test_ambiguous_same_name_can_be_resolved_by_course_history() -> None:
    target = InstructorTarget(
        name="Alex Kim",
        normalized_name="alex kim",
        course_codes=("NURS202",),
    )
    first = candidate("Alex Kim", legacy_id=1, node_id="first")
    second = candidate("Alex Kim", legacy_id=2, node_id="second")
    courses = {
        "first": {"MATH150"},
        "second": {"NURS202", "NURS300"},
    }
    chosen, confidence, reason = select_candidate(
        target,
        (first, second),
        course_lookup=lambda node_id: courses[node_id],
    )
    assert chosen == second
    assert confidence == 1.0
    assert reason == "matched_by_course"


def test_repository_overlays_cached_rmp_metrics(tmp_path: Path) -> None:
    ratings_path = tmp_path / "professor_ratings.json"
    ratings_path.write_text(
        json.dumps(
            {
                "provider": "RateMyProfessors",
                "records": [
                    {
                        "name": "Maya Chen",
                        "normalized_name": "maya chen",
                        "metrics": {
                            "provider": "RateMyProfessors",
                            "external_id": "123456",
                            "rating": 4.9,
                            "difficulty": 2.1,
                            "would_take_again_percent": 98.0,
                            "num_reviews": 77,
                            "profile_url": "https://www.ratemyprofessors.com/professor/123456",
                            "match_confidence": 1.0,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repository = CourseRepository.from_json(SAMPLE_DATA, ratings_path=ratings_path)
    section = next(item for item in repository.sections if item.instructor == "Maya Chen")
    assert repository.ratings_record_count == 1
    assert section.professor is not None
    assert section.professor.provider == "RateMyProfessors"
    assert section.professor.rating == 4.9
    assert section.professor.difficulty == 2.1
    assert section.professor.would_take_again_percent == 98.0
    assert section.professor.num_reviews == 77
    assert section.professor.profile_url == "https://www.ratemyprofessors.com/professor/123456"


class FakeRmpClient:
    def search(self, name: str) -> tuple[RmpCandidate, ...]:
        return (
            candidate(
                name,
                legacy_id=987654,
                node_id="teacher-node",
                rating=4.6,
                difficulty=2.5,
                reviews=42,
                take_again=93.0,
            ),
        )

    def course_codes(self, node_id: str) -> frozenset[str]:
        assert node_id == "teacher-node"
        return frozenset({"CS150"})


def test_rmp_sync_writes_resumable_cache_document(tmp_path: Path) -> None:
    output = tmp_path / "professor_ratings.json"
    exit_code = sync_rate_my_professors(
        sections_path=SAMPLE_DATA,
        output_path=output,
        delay_seconds=0,
        limit=1,
        client=FakeRmpClient(),
    )
    assert exit_code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["provider"] == "RateMyProfessors"
    assert document["school"]["legacy_id"] == 877
    assert document["summary"]["matched"] == 1
    metrics = document["records"][0]["metrics"]
    assert metrics["rating"] == 4.6
    assert metrics["difficulty"] == 2.5
    assert metrics["would_take_again_percent"] == 93.0
    assert metrics["num_reviews"] == 42
    assert metrics["profile_url"] == "https://www.ratemyprofessors.com/professor/987654"


class DirectAliasRmpClient:
    def __init__(self) -> None:
        self.lookups: list[int] = []
        self.searches: list[str] = []

    def lookup_legacy_id(self, legacy_id: int) -> RmpCandidate | None:
        self.lookups.append(legacy_id)
        if legacy_id == 1681:
            return candidate(
                "Timothy (Mark) Dunster",
                legacy_id=1681,
                node_id="direct-mark",
                rating=2.2,
                difficulty=4.1,
                reviews=213,
                take_again=23.0,
            )
        if legacy_id == 444442:
            return candidate(
                "Satchi Venkataraman",
                legacy_id=444442,
                node_id="direct-satchi",
                rating=3.3,
                difficulty=3.5,
                reviews=58,
                take_again=39.0,
            )
        return None

    def search(self, name: str) -> tuple[RmpCandidate, ...]:
        self.searches.append(name)
        return ()

    def course_codes(self, node_id: str) -> frozenset[str]:
        return frozenset({"MATH150", "AE310", "AE621"})


def test_known_profile_id_fallback_matches_mark_when_rmp_search_returns_nothing(tmp_path: Path) -> None:
    sections = json.loads(SAMPLE_DATA.read_text(encoding="utf-8"))
    sections[0]["instructor"] = "Mark Dunster"
    sections[0]["meetings"] = []
    sections_path = tmp_path / "sections.json"
    sections_path.write_text(json.dumps([sections[0]]), encoding="utf-8")
    output = tmp_path / "professor_ratings.json"

    client = DirectAliasRmpClient()
    exit_code = sync_rate_my_professors(
        sections_path=sections_path,
        output_path=output,
        delay_seconds=0,
        names=("Mark Dunster",),
        client=client,
    )

    assert exit_code == 0
    assert client.lookups == [1681]
    document = json.loads(output.read_text(encoding="utf-8"))
    record = document["records"][0]
    assert record["normalized_name"] == "mark dunster"
    assert record["rmp_name"] == "Timothy (Mark) Dunster"
    assert record["metrics"]["external_id"] == "1681"
    assert record["metrics"]["num_reviews"] == 213


def test_known_profile_id_fallback_matches_satchi_when_rmp_search_returns_nothing(tmp_path: Path) -> None:
    sections = json.loads(SAMPLE_DATA.read_text(encoding="utf-8"))
    sections[0]["course_code"] = "A E 310"
    sections[0]["instructor"] = "Satchithanandam Venkataraman"
    sections[0]["meetings"] = []
    sections_path = tmp_path / "sections.json"
    sections_path.write_text(json.dumps([sections[0]]), encoding="utf-8")
    output = tmp_path / "professor_ratings.json"

    client = DirectAliasRmpClient()
    exit_code = sync_rate_my_professors(
        sections_path=sections_path,
        output_path=output,
        delay_seconds=0,
        names=("Satchithanandam Venkataraman",),
        client=client,
    )

    assert exit_code == 0
    assert client.lookups == [444442]
    document = json.loads(output.read_text(encoding="utf-8"))
    record = document["records"][0]
    assert record["normalized_name"] == "satchithanandam venkataraman"
    assert record["rmp_name"] == "Satchi Venkataraman"
    assert record["metrics"]["external_id"] == "444442"
    assert record["metrics"]["num_reviews"] == 58


class AliasOnlyRmpClient:
    def __init__(self) -> None:
        self.searches: list[str] = []

    def search(self, name: str) -> tuple[RmpCandidate, ...]:
        self.searches.append(name)
        if name == "Timothy (Mark) Dunster":
            return (candidate("Timothy (Mark) Dunster", legacy_id=444, node_id="alias-node"),)
        return ()

    def course_codes(self, node_id: str) -> frozenset[str]:
        assert node_id == "alias-node"
        return frozenset({"CS150"})


def test_sync_can_target_known_alias_without_overwriting_other_cached_records(tmp_path: Path) -> None:
    sections = json.loads(SAMPLE_DATA.read_text(encoding="utf-8"))
    sections[0]["instructor"] = "Mark Dunster"
    sections[0]["meetings"] = []
    sections_path = tmp_path / "sections.json"
    sections_path.write_text(json.dumps([sections[0]]), encoding="utf-8")

    output = tmp_path / "professor_ratings.json"
    output.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "name": "Someone Else",
                        "normalized_name": "someone else",
                        "metrics": {
                            "provider": "RateMyProfessors",
                            "external_id": "99",
                            "rating": 4.0,
                            "difficulty": 2.0,
                            "would_take_again_percent": 80.0,
                            "num_reviews": 10,
                            "profile_url": "https://www.ratemyprofessors.com/professor/99",
                            "match_confidence": 1.0,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    client = AliasOnlyRmpClient()
    exit_code = sync_rate_my_professors(
        sections_path=sections_path,
        output_path=output,
        delay_seconds=0,
        names=("Mark Dunster",),
        client=client,
    )
    assert exit_code == 0
    assert client.searches == ["Mark Dunster", "Timothy (Mark) Dunster"]
    document = json.loads(output.read_text(encoding="utf-8"))
    records = {record["normalized_name"]: record for record in document["records"]}
    assert "mark dunster" in records
    assert records["mark dunster"]["rmp_name"] == "Timothy (Mark) Dunster"
    assert records["mark dunster"]["metrics"]["external_id"] == "444"
