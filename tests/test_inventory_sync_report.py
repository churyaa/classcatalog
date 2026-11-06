"""Offline regressions for section-level drift and read-only rescrape targets."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.removal_review import RemovalReviewService
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService

URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&STRM=2273"
)


def _fixture(tmp_path: Path):
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    spring = [sample.model_copy(update={
        "id": f"inventory-cs250-{number}", "term": "Spring 2027",
        "term_code": "2273", "course_code": "CS 250", "subject": "CS",
        "catalog_number": "250", "schedule_number": number,
        "crse_id": "038524", "crse_offer_nbr": "1", "acad_career": "UGRD",
        "source_url": URL, "seat_updated_at": None,
        "seat_status": SeatStatus.OPEN,
    }) for number in ("8480", "11623")]
    other = sample.model_copy(update={
        "id": "other-fall", "term": "Fall 2026", "term_code": "2267",
        "course_code": "MATH 151", "subject": "MATH", "catalog_number": "151",
        "schedule_number": "33002", "source_url": None,
    })
    path = tmp_path / "sections.json"
    path.write_text(json.dumps([s.model_dump(mode="json") for s in (*spring, other)]), encoding="utf-8")
    repository = CourseRepository((*spring, other))
    service = SeatRefreshService(
        repository, cache_path=tmp_path / "seat_cache.json",
        instructor_cache_path=tmp_path / "instructor_cache.json",
        request_delay_seconds=0,
    )
    return spring, path, repository, service


def _apply_grid(service, *, numbers=("11623", "12262"), complete=True, term="2273"):
    parsed = SimpleNamespace(
        term_code=term, options_complete=complete,
        options=tuple(SimpleNamespace(
            class_number=n, instructor="Test Instructor",
            status=SeatStatus.OPEN, open_seats=9,
            seat_capacity=30, seats_enrolled=21,
        ) for n in numbers),
    )
    service._parse_expected_source_page = lambda *_: parsed
    client = SimpleNamespace(fetch_course_info_page=lambda url: SimpleNamespace(
        html="<html></html>", canonical_url=url, expansion_count=0,
    ))
    service._refresh_source(client, URL)


def test_complete_grid_lists_missing_and_new_but_never_hides(tmp_path: Path):
    _, path, repository, worker = _fixture(tmp_path)
    before = path.read_bytes()
    _apply_grid(worker)
    assert worker.missing_section_keys() == {"2273::8480"}
    drift = worker.admin_status()["inventory_drift"]
    assert drift["missing_section_candidates"] == 1
    assert drift["unlisted_class_numbers"] == 1
    assert "12262" in drift["unlisted_class_examples"][0]
    review = RemovalReviewService(repository, path, seat_service=worker)
    items = review.candidates(limit=None)
    flagged = next(i for i in items["items"] if i["class_number"] == "8480")
    assert flagged["missing_section_signal"] is True
    assert flagged["seat_age_hours"] is None
    assert len(items["rescrape_targets"]) == 1
    assert items["rescrape_targets"][0]["course_key"] == "CS|038524|1|UGRD"
    assert items["rescrape_targets"][0]["class_numbers"] == ["8480"]
    assert path.read_bytes() == before
    assert not (tmp_path / "hidden_classes.json").exists()



def test_new_number_only_does_not_mislabel_known_numbers(tmp_path: Path):
    _, path, repo, worker = _fixture(tmp_path)
    _apply_grid(worker, numbers=("8480", "11623", "12262"))
    assert worker.missing_section_keys() == set()
    review = RemovalReviewService(repo, path, seat_service=worker)
    targets = review.candidates(limit=None)["rescrape_targets"]
    assert len(targets) == 1
    assert targets[0]["course_key"] == "CS|038524|1|UGRD"
    assert targets[0]["signals"] == ["new_class_number"]
    assert targets[0]["class_numbers"] == []


def test_incomplete_or_wrong_term_cannot_flag_missing(tmp_path: Path):
    _, _, _, worker = _fixture(tmp_path)
    _apply_grid(worker, complete=False, numbers=("11623",))
    assert worker.missing_section_keys() == set()
    _apply_grid(worker, term="2267", numbers=("11623",))
    assert worker.missing_section_keys() == set()
    _apply_grid(worker, numbers=("8480", "11623"))
    assert worker.missing_section_keys() == set()


def test_persisted_evidence_survives_worker_restart(tmp_path: Path):
    _, _, repository, worker = _fixture(tmp_path)
    _apply_grid(worker)
    worker._flush_cache(force=True)
    assert (tmp_path / "seat_cache.json").exists()
    second = SeatRefreshService(
        repository, cache_path=tmp_path / "seat_cache.json",
        instructor_cache_path=tmp_path / "instructor_cache.json",
        request_delay_seconds=0,
    )
    assert second.missing_section_keys() == {"2273::8480"}
    _apply_grid(second, numbers=("8480", "11623"))
    assert second.missing_section_keys() == set()


def test_hidden_records_are_included_in_course_key_export(tmp_path: Path):
    sections, path, repository, worker = _fixture(tmp_path)
    review = RemovalReviewService(repository, path, seat_service=worker, probe=lambda _: True)
    target = sections[0]
    review._verify(f"2273::{target.schedule_number}", target)
    review.hide_confirmed(f"2273::{target.schedule_number}")
    targets = review.candidates(limit=None)["rescrape_targets"]
    assert len(targets) == 1
    assert targets[0]["course_key"] == "CS|038524|1|UGRD"
    assert targets[0]["signals"] == ["hidden"]
    assert targets[0]["class_numbers"] == ["8480"]
    assert (tmp_path / "hidden_classes.json").exists()
