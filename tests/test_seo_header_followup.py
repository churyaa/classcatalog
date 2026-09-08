from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.models import CourseSection, GradingType, InstructionMode, SeatStatus
from classcatalog.repository import CourseRepository


def _client() -> TestClient:
    section = CourseSection(
        id="cs-210-header-test",
        term="Fall 2026",
        term_code="2267",
        course_code="CS 210",
        subject="CS",
        catalog_number="210",
        section_number="01",
        schedule_number="21001",
        component="Lecture",
        title="Data Structures",
        units=3.0,
        grading=GradingType.LETTER,
        instruction_mode=InstructionMode.IN_PERSON,
        seat_status=SeatStatus.OPEN,
        seats_available=5,
        seat_capacity=30,
        seats_enrolled=25,
        instructor="Example Professor",
        campus="San Diego Campus",
    )
    return TestClient(create_app(CourseRepository((section,)), seat_refresh_enabled=False))


def test_main_and_seo_headers_include_subjects_icon_navigation() -> None:
    client = _client()

    home = client.get("/")
    subjects = client.get("/subjects")

    assert home.status_code == 200
    assert subjects.status_code == 200
    assert 'id="subjects-nav"' in home.text
    assert 'href="/subjects"' in home.text
    assert 'aria-label="Subjects"' in home.text
    assert 'id="home-nav"' in subjects.text
    assert 'id="about-nav"' in subjects.text
    assert 'id="subjects-nav"' in subjects.text
    assert 'id="favorites-nav"' in subjects.text
    assert '<h1>classcatalog</h1>' in subjects.text
    assert 'seo-brand-title' not in subjects.text


def test_course_page_explains_missing_rmp_match() -> None:
    client = _client()

    response = client.get("/courses/cs-210")

    assert response.status_code == 200
    assert "Example Professor" in response.text
    assert "No RateMyProfessor match available" in response.text
