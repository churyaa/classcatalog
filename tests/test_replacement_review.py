"""Tests for automatic verified replacement imports; no SDSU network needed."""
from __future__ import annotations

from datetime import time
import json
from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import Meeting, Weekday, CourseSection
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.replacement_review import ReplacementReviewService

OFFICIAL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_CLSRCH_FL.SSR_CRSE_INFO_FL.GBL?STRM=2273"
)


def _setup(tmp_path: Path):
    template = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None).sections[0]
    old = template.model_copy(update={
        "id": "sp27-latam-797-8182", "term": "Spring 2027", "term_code": "2273",
        "subject": "LATAM", "course_code": "LATAM 797", "catalog_number": "797",
        "crse_id": "042739", "acad_career": "GRAD", "schedule_number": "8182",
        "section_number": "01", "source_url": OFFICIAL,
        "meetings": (Meeting(days=(Weekday.MON,), start_time=time(10), end_time=time(11),
                             location="Old room", instructor="Old professor"),),
        "instructor": "Old professor", "location": "Old room",
        "option_group_ids": (), "option_primary_group_ids": (),
    })
    summer = template.model_copy(update={
        "id": "su27-latam-797-8182", "term": "Summer 2027", "term_code": "2275",
        "course_code": "LATAM 797", "subject": "LATAM", "catalog_number": "797",
        "schedule_number": "8182",
    })
    data = tmp_path / "sections.json"
    data.write_text(json.dumps([s.model_dump(mode="json") for s in (old, summer)]), encoding="utf-8")
    repo = CourseRepository((old, summer))
    new = old.model_copy(update={
        "id": "sp27-latam-797-5244", "schedule_number": "5244", "section_number": "02",
        "instructor": "New professor", "location": "New room", "professor": None,
        "meetings": (Meeting(days=(Weekday.TUE,), start_time=time(14), end_time=time(15),
                             location="New room", instructor="New professor"),),
    })
    return old, summer, new, data, repo


def _grid():
    option = SimpleNamespace(class_number="5244")
    return SimpleNamespace(course_code="LATAM 797", term_code="2273",
                           options_complete=True, options=[option])


def test_auto_add_verified_replacement_preserves_original_and_summer(tmp_path: Path, monkeypatch) -> None:
    old, summer, new, path, repo = _setup(tmp_path)
    seen = []
    service = ReplacementReviewService(
        repo, path, verify_replacement=lambda source, num, expected: (
            seen.append((source.schedule_number, num, expected)) or new),
    )
    monkeypatch.setattr(service, "_fresh_course_snapshot", lambda _s: ({"5244"}, {}))
    service.consider_grid(old, _grid())
    assert seen == [("8182", "5244", {"5244"})]
    assert [s.schedule_number for s in repo.sections] == ["8182", "8182", "5244"]
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert len(on_disk) == 3
    assert on_disk[1]["term"] == "Summer 2027"
    assert on_disk[2]["instructor"] == "New professor"
    assert on_disk[2]["meetings"][0]["days"] == ["tue"]
    assert service.history()[0]["status"] == "added"
    assert tuple((path.parent / "replacement-backups").glob("*.json"))
    # A second scan never installs the same class again.
    service.consider_grid(old, _grid())
    assert len(repo.sections) == 3


def test_ambiguous_replacement_is_skipped(tmp_path: Path, monkeypatch) -> None:
    old, _summer, new, path, repo = _setup(tmp_path)
    service = ReplacementReviewService(repo, path, verify_replacement=lambda *_args: new)
    monkeypatch.setattr(service, "_fresh_course_snapshot", lambda _s: ({"5244", "5245"}, {}))
    before = path.read_bytes()
    service.consider_grid(old, _grid())
    assert path.read_bytes() == before
    assert service.history()[0]["status"] == "skipped"


def test_untrusted_or_still_present_class_never_imports(tmp_path: Path, monkeypatch) -> None:
    old, _summer, new, path, repo = _setup(tmp_path)
    service = ReplacementReviewService(repo, path, verify_replacement=lambda *_args: new)
    monkeypatch.setattr(service, "_fresh_course_snapshot", lambda _s: ({"5244"}, {}))
    initial = path.read_bytes()
    source = old.model_copy(update={"source_url": "https://example.invalid/SSR_CRSE_INFO_FL.GBL"})
    service.consider_grid(source, _grid())
    service.consider_grid(old, SimpleNamespace(course_code="LATAM 797", term_code="2273",
                                               options_complete=True, options=[SimpleNamespace(class_number="8182")]))
    assert path.read_bytes() == initial


def test_refuses_crosslisted_and_linked_replacements(tmp_path: Path, monkeypatch) -> None:
    old, summer, new, path, _repo = _setup(tmp_path)
    another = old.model_copy(update={"id": "crosslisted", "course_code": "LATAM 798"})
    repo = CourseRepository((old, another, summer))
    service = ReplacementReviewService(repo, path, verify_replacement=lambda *_args: new)
    monkeypatch.setattr(service, "_fresh_course_snapshot", lambda _s: ({"5244"}, {}))
    before = path.read_bytes()
    service.consider_grid(old, _grid())
    assert path.read_bytes() == before
    assert service.history()[0]["status"] == "skipped"


def test_full_class_details_populate_new_schedule_professor_and_seats(tmp_path: Path) -> None:
    from classcatalog.models import SeatStatus, InstructionMode
    old, _summer, _new, _path, _repo = _setup(tmp_path)
    old = old.model_copy(update={"option_group_ids": ("8182",), "option_primary_group_ids": ("8182",)})
    option = SimpleNamespace(
        class_number="5244", section_number="02", option_number=1,
        option_group_index=2, instructor="New professor", location="New room",
        days=(Weekday.TUE,), start_time=time(14), end_time=time(15),
        meeting_dates="2027-01-20 - 2027-05-10", status=SeatStatus.OPEN,
        open_seats=5, seat_capacity=20, seats_enrolled=15,
    )
    row = SimpleNamespace(
        days=(Weekday.TUE,), start_time=time(14), end_time=time(15),
        room="New room", meeting_dates="2027-01-20 - 2027-05-10",
        instructor="New professor",
    )
    details = SimpleNamespace(
        meetings=(row,), location="New room", instruction_mode_text="In Person",
        instruction_mode=InstructionMode.IN_PERSON, section_number="02",
        status=SeatStatus.OPEN, seats_available=5, seat_capacity=20,
        seats_enrolled=15, waitlist_capacity=0, waitlist_total=0,
        waitlist_available=0, bookstore_url=None, materials_description=None,
        textbook_required=None, class_notes=(), enrollment_requirements=(),
    )
    candidate = ReplacementReviewService._build_candidate(
        old, "5244", None, option, details, OFFICIAL,
    )
    assert candidate.schedule_number == "5244"
    assert candidate.instructor == "New professor"
    assert candidate.professor is None
    assert candidate.location == "New room"
    assert candidate.seats_available == 5
    assert candidate.seat_capacity == 20
    assert candidate.meetings[0].days == (Weekday.TUE,)
    assert candidate.meetings[0].start_time == time(14)
    assert candidate.option_group_ids == ("5244",)
    assert candidate.option_primary_group_ids == ("5244",)


def test_replacement_grid_without_term_code_is_compatible(tmp_path: Path) -> None:
    """Grid doubles may omit the optional STRM; this must not abort the
    existing modified-class scan, which processes the same source page.
    """
    old, _summer, _new, path, repo = _setup(tmp_path)
    service = ReplacementReviewService(repo, path)
    grid = SimpleNamespace(
        course_code=old.course_code,
        options_complete=True,
        options=(SimpleNamespace(class_number=old.schedule_number),),
    )
    service.consider_grid(old, grid)
    assert service.history() == []
