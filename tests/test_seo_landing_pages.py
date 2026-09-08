from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import time

from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.models import (
    CourseSection,
    GradingType,
    InstructionMode,
    Meeting,
    ProfessorMetrics,
    SeatStatus,
    Weekday,
)
from classcatalog.repository import CourseRepository
from classcatalog.seo import SeoCatalog, course_slug
from classcatalog.subjects import subject_display_name, subject_slug


def _section(
    *,
    identifier: str,
    course_code: str,
    subject: str,
    catalog_number: str,
    schedule_number: str,
    title: str,
    description: str | None = None,
    campus: str = "San Diego Campus",
    component: str = "Lecture",
    section_number: str = "01",
    option_number: int | None = None,
    source_course_key: str | None = None,
    option_group_indices: tuple[int, ...] = (),
    option_primary_group_indices: tuple[int, ...] = (),
    crse_id: str | None = None,
    crse_offer_nbr: str | None = None,
    units: float = 3.0,
    instructor: str | None = None,
    professor: ProfessorMetrics | None = None,
    meetings: tuple[Meeting, ...] = (),
    location: str | None = None,
    seat_status: SeatStatus = SeatStatus.OPEN,
    seats_available: int | None = 5,
    seat_capacity: int | None = 30,
    instruction_mode: InstructionMode = InstructionMode.IN_PERSON,
) -> CourseSection:
    return CourseSection(
        id=identifier,
        term="Fall 2026",
        term_code="2267",
        course_code=course_code,
        subject=subject,
        catalog_number=catalog_number,
        section_number=section_number,
        schedule_number=schedule_number,
        option_number=option_number,
        source_course_key=source_course_key,
        option_group_indices=option_group_indices,
        option_primary_group_indices=option_primary_group_indices,
        crse_id=crse_id,
        crse_offer_nbr=crse_offer_nbr,
        component=component,
        title=title,
        description=description,
        units=units,
        grading=GradingType.LETTER,
        instruction_mode=instruction_mode,
        seat_status=seat_status,
        seats_available=seats_available,
        seat_capacity=seat_capacity,
        seats_enrolled=(
            seat_capacity - seats_available
            if seat_capacity is not None and seats_available is not None
            else None
        ),
        campus=campus,
        location=location,
        instructor=instructor,
        meetings=meetings,
        professor=professor,
    )


def _client(*sections: CourseSection) -> TestClient:
    return TestClient(create_app(CourseRepository(sections), seat_refresh_enabled=False))


def test_slug_rules_cover_multiword_subjects_and_course_suffixes() -> None:
    assert subject_display_name("CS") == "Computer Science"
    assert subject_slug("CS") == "computer-science"
    assert subject_slug("MATH") == "mathematics"
    assert subject_slug("P A") == "public-administration"
    assert subject_slug("R A") == "recreation-administration"
    assert course_slug("CS 210") == "cs-210"
    assert course_slug("MATH 150A") == "math-150a"
    assert course_slug("P A 792") == "p-a-792"
    assert course_slug("R A 795") == "r-a-795"


def test_subject_and_course_pages_are_server_rendered_with_unique_metadata() -> None:
    cs_210 = _section(
        identifier="cs-210-1",
        course_code="CS 210",
        subject="CS",
        catalog_number="210",
        schedule_number="12345",
        title="Data Structures",
        description="Data structures and algorithms for computer science.",
    )
    client = _client(cs_210)

    subject_index = client.get("/subjects")
    subject = client.get("/subjects/computer-science")
    course = client.get("/courses/cs-210")

    assert subject_index.status_code == 200
    assert subject.status_code == 200
    assert course.status_code == 200
    assert "SDSU Subjects" in subject_index.text
    assert "SDSU Computer Science Classes" in subject.text
    assert 'href="/courses/cs-210"' in subject.text
    assert "SDSU CS 210 — Data Structures" in course.text
    assert "Data structures and algorithms for computer science." in course.text
    assert "Class #" in course.text
    assert "RateMyProfessors" in course.text
    assert "12345" in course.text
    assert '<link rel="canonical" href="https://classcatalog.cc/courses/cs-210">' in course.text
    assert (
        '<meta property="og:url" content="https://classcatalog.cc/courses/cs-210">'
        in course.text
    )
    assert '<script type="application/ld+json" id="classcatalog-breadcrumb-schema">' in course.text



def test_subject_index_uses_whole_card_links_with_only_course_counts() -> None:
    acctg = _section(
        identifier="acctg-201",
        course_code="ACCTG 201",
        subject="ACCTG",
        catalog_number="201",
        schedule_number="20101",
        title="Financial Accounting Fundamentals",
    )
    client = _client(acctg)

    response = client.get("/subjects")

    assert response.status_code == 200
    assert 'class="seo-subject-card" href="/subjects/accountancy"' in response.text
    assert "<strong>ACCTG</strong> - Accountancy" in response.text
    assert "1 course" in response.text
    assert "enrollment option" not in response.text


def test_subject_page_is_compact_course_table_without_course_descriptions() -> None:
    aerospace = _section(
        identifier="ae-123",
        course_code="A E 123",
        subject="A E",
        catalog_number="123",
        schedule_number="12301",
        title="The Aerospace Engineer",
        description=(
            "This description belongs on the individual course page, not the subject table."
        ),
        units=1.0,
    )
    client = _client(aerospace)

    response = client.get("/subjects/aerospace-engineering")

    assert response.status_code == 200
    assert 'class="seo-course-table"' in response.text
    assert "<th scope=\"col\">Course</th>" in response.text
    assert "<th scope=\"col\">Title</th>" in response.text
    assert "<th scope=\"col\">Units</th>" in response.text
    assert "<th scope=\"col\">Options</th>" in response.text
    assert "A E 123" in response.text
    assert "The Aerospace Engineer" in response.text
    assert "This description belongs" not in response.text


def test_course_options_are_compact_and_include_rmp_metrics() -> None:
    professor = ProfessorMetrics(
        provider="ratemyprofessors",
        external_id="1234",
        rating=4.6,
        difficulty=2.3,
        would_take_again_percent=91.0,
        num_reviews=42,
        profile_url="https://www.ratemyprofessors.com/professor/1234",
    )
    meeting = Meeting(
        days=(Weekday.MON, Weekday.WED),
        start_time=time(10, 0),
        end_time=time(10, 50),
        location="GMCS 301",
    )
    aerospace = _section(
        identifier="ae-123",
        course_code="A E 123",
        subject="A E",
        catalog_number="123",
        schedule_number="12345",
        title="The Aerospace Engineer",
        instructor="Ada Lovelace",
        professor=professor,
        meetings=(meeting,),
        location="GMCS 301",
        seats_available=7,
        seat_capacity=30,
    )
    client = _client(aerospace)

    response = client.get("/courses/a-e-123")

    assert response.status_code == 200
    assert 'class="seo-option-table"' in response.text
    for heading in (
        "Class #",
        "Format",
        "Status",
        "Seats",
        "Time",
        "Location",
        "Professor",
        "RateMyProfessors",
    ):
        assert heading in response.text
    assert "12345" in response.text
    assert "In person" in response.text
    assert ">Open<" in response.text
    assert "23 / 30" in response.text
    assert "7 / 30" not in response.text
    assert "Mon/Wed 10:00 AM–10:50 AM" in response.text
    assert "GMCS 301" in response.text
    assert "Ada Lovelace" in response.text
    assert "4.6/5" in response.text
    assert "42 reviews" in response.text
    assert "Difficulty 2.3" in response.text
    assert "91% take again" in response.text
    assert "https://www.ratemyprofessors.com/professor/1234" in response.text


def test_course_lookup_is_exact_and_does_not_include_prefix_matches() -> None:
    cs_210 = _section(
        identifier="cs-210",
        course_code="CS 210",
        subject="CS",
        catalog_number="210",
        schedule_number="21000",
        title="Data Structures",
    )
    cs_210a = _section(
        identifier="cs-210a",
        course_code="CS 210A",
        subject="CS",
        catalog_number="210A",
        schedule_number="21001",
        title="Special Data Structures",
    )
    client = _client(cs_210, cs_210a)

    response = client.get("/courses/cs-210")

    assert response.status_code == 200
    assert "Data Structures" in response.text
    assert "Special Data Structures" not in response.text
    assert "21000" in response.text
    assert "21001" not in response.text


def test_one_course_page_aggregates_multiple_offerings_and_campuses() -> None:
    first = _section(
        identifier="cs-210-sd",
        course_code="CS 210",
        subject="CS",
        catalog_number="210",
        schedule_number="22001",
        title="Data Structures",
        crse_id="012345",
        crse_offer_nbr="1",
        campus="San Diego Campus",
    )
    second = _section(
        identifier="cs-210-iv",
        course_code="CS 210",
        subject="CS",
        catalog_number="210",
        schedule_number="22002",
        title="Data Structures",
        crse_id="098765",
        crse_offer_nbr="2",
        campus="Imperial Valley Campus",
    )
    client = _client(first, second)

    response = client.get("/courses/cs-210")

    assert response.status_code == 200
    assert response.text.count('rel="canonical"') == 1
    assert "22001" in response.text
    assert "22002" in response.text
    assert "San Diego Campus" not in response.text
    assert "Imperial Valley Campus" not in response.text


def test_course_slug_is_stable_across_internal_course_rekeys() -> None:
    old = _section(
        identifier="old",
        course_code="R A 795",
        subject="R A",
        catalog_number="795",
        schedule_number="30001",
        title="Capstone Development I",
        crse_id="038812",
        crse_offer_nbr="1",
    )
    new = old.model_copy(update={"id": "new", "crse_id": "042739", "crse_offer_nbr": "9"})

    old_catalog = SeoCatalog.from_repository(CourseRepository((old,)))
    new_catalog = SeoCatalog.from_repository(CourseRepository((new,)))

    old_course = old_catalog.course_by_code("R A 795")
    new_course = new_catalog.course_by_code("R A 795")
    assert old_course is not None
    assert new_course is not None
    assert old_course.slug == "r-a-795"
    assert new_course.slug == "r-a-795"


def test_seo_projection_reuses_shared_primary_grouping() -> None:
    discussion = _section(
        identifier="math-disc",
        course_code="MATH 150",
        subject="MATH",
        catalog_number="150",
        schedule_number="6528",
        title="Calculus I",
        component="Discussion",
        option_number=1,
        source_course_key="MATH|150|UGRD",
        option_group_indices=(1, 2),
        option_primary_group_indices=(1, 2),
        units=4.0,
    )
    activity_one = _section(
        identifier="math-act-1",
        course_code="MATH 150",
        subject="MATH",
        catalog_number="150",
        schedule_number="6533",
        title="Calculus I",
        component="Activity",
        option_number=1,
        source_course_key="MATH|150|UGRD",
        option_group_indices=(1,),
        units=0.0,
    )
    activity_two = activity_one.model_copy(
        update={"id": "math-act-2", "schedule_number": "6534", "option_group_indices": (2,)}
    )
    repository = CourseRepository((discussion, activity_one, activity_two))

    displayed = repository.displayed_options(term="Fall 2026", course_code="MATH 150")

    assert len(displayed) == 2
    assert [
        [component.schedule_number for component in item.linked_components]
        for item in displayed
    ] == [
        ["6528", "6533"],
        ["6528", "6534"],
    ]
    catalog = SeoCatalog.from_repository(repository)
    course = catalog.course_by_code("MATH 150")
    assert course is not None
    assert course.option_count == 2


def test_suppressed_orphan_component_does_not_create_indexable_course_page() -> None:
    orphan = _section(
        identifier="orphan",
        course_code="MATH 150",
        subject="MATH",
        catalog_number="150",
        schedule_number="6532",
        title="Calculus I",
        component="Activity",
        units=0.0,
    )
    client = _client(orphan)

    assert client.get("/courses/math-150").status_code == 404
    sitemap = client.get("/sitemap.xml")
    assert "https://classcatalog.cc/courses/math-150" not in sitemap.text


def test_unknown_and_noncanonical_slugs_are_handled_cleanly() -> None:
    cs_210 = _section(
        identifier="cs-210",
        course_code="CS 210",
        subject="CS",
        catalog_number="210",
        schedule_number="21000",
        title="Data Structures",
    )
    client = _client(cs_210)

    assert client.get("/courses/does-not-exist").status_code == 404
    assert client.get("/subjects/does-not-exist").status_code == 404

    redirect = client.get("/courses/CS-210", follow_redirects=False)
    assert redirect.status_code == 308
    assert redirect.headers["location"] == "/courses/cs-210"


def test_dynamic_sitemap_contains_only_unique_clean_canonical_urls() -> None:
    cs_210 = _section(
        identifier="cs-210",
        course_code="CS 210",
        subject="CS",
        catalog_number="210",
        schedule_number="21000",
        title="Data Structures",
    )
    math_150 = _section(
        identifier="math-150",
        course_code="MATH 150",
        subject="MATH",
        catalog_number="150",
        schedule_number="15000",
        title="Calculus I",
    )
    client = _client(cs_210, math_150)

    sitemap = client.get("/sitemap.xml")
    root = ET.fromstring(sitemap.text)
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [node.text for node in root.findall("sm:url/sm:loc", namespace)]

    assert sitemap.status_code == 200
    assert "https://classcatalog.cc/subjects/computer-science" in urls
    assert "https://classcatalog.cc/subjects/mathematics" in urls
    assert "https://classcatalog.cc/courses/cs-210" in urls
    assert "https://classcatalog.cc/courses/math-150" in urls
    assert len(urls) == len(set(urls))
    assert all(url is not None and "?" not in url and "#" not in url for url in urls)


def test_landing_pages_preserve_consent_gated_analytics() -> None:
    cs_210 = _section(
        identifier="cs-210",
        course_code="CS 210",
        subject="CS",
        catalog_number="210",
        schedule_number="21000",
        title="Data Structures",
    )
    client = _client(cs_210)

    for path in ("/subjects", "/subjects/computer-science", "/courses/cs-210"):
        html = client.get(path).text
        assert '<meta name="classcatalog-google-analytics-id" content="G-Y9SSE53RE6">' in html
        assert '/static/cookie-consent.css?v=1' in html
        assert '/static/cookie-consent.js?v=1' in html
        assert "https://www.googletagmanager.com/gtag/js?id=" not in html
