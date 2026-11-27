"""Offline regressions: class numbers may be reused across SDSU terms.

No real SDSU requests or production datasets are used in these tests.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService

BASE = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)


def make_service(tmp_path: Path):
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    specs = (
        ("Fall 2026", "2267", "ASTR", "ASTR 101", "3991", "072001"),
        ("Spring 2027", "2273", "CHEM", "CHEM 201", "3991", "032312"),
        ("Spring 2027", "2273", "CHEM", "CHEM 201", "3984", "032312"),
    )
    sections = []
    urls = {}
    for term, code, subject, course, number, crse_id in specs:
        url = f"{BASE}&STRM={code}&CLASS_NBR={3991 if subject == 'ASTR' else 11659}"
        urls[term] = url
        sections.append(sample.model_copy(update={
            "id": f"term-qualified-{term}-{number}",
            "term": term, "term_code": code, "subject": subject,
            "course_code": course, "catalog_number": course.split()[-1],
            "schedule_number": number, "source_url": url,
            "crse_id": crse_id, "crse_offer_nbr": "1", "acad_career": "UGRD",
            "seat_status": SeatStatus.WAITLIST,
            "seats_available": 0, "seat_capacity": 32,
            "seats_enrolled": 32, "waitlist_available": 994,
            "waitlist_capacity": 999, "waitlist_total": 5,
            "seat_updated_at": None, "linked_components": (),
        }))
    repository = CourseRepository(tuple(sections))
    service = SeatRefreshService(
        repository,
        cache_path=tmp_path / "term-seat-cache.json",
        instructor_cache_path=tmp_path / "term-instructors.json",
        request_delay_seconds=0,
        preferred_term="Spring 2027",
    )
    return service, urls


def test_viewed_spring_3991_never_prioritizes_fall_3991(tmp_path):
    service, urls = make_service(tmp_path)
    assert service.register_interest((), section_keys=("Spring 2027::3991",)) == 1
    assert set(service._interests) == {urls["Spring 2027"]}
    assert (urls["Spring 2027"], "3991") in service._viewed_class_expirations
    assert (urls["Fall 2026"], "3991") not in service._viewed_class_expirations
    assert service._next_source(0) == urls["Spring 2027"]


def test_legacy_number_only_client_remains_compatible(tmp_path):
    service, urls = make_service(tmp_path)
    assert service.register_interest(("3991",)) == 2
    assert set(service._interests) == set(urls.values())
    assert (urls["Fall 2026"], "3991") in service._viewed_class_expirations
    assert (urls["Spring 2027"], "3991") in service._viewed_class_expirations
    assert set(service.seat_records(("3991",))) == {
        "Fall 2026::3991", "Spring 2027::3991",
    }


def test_qualified_lookup_returns_only_requested_term(tmp_path):
    service, _ = make_service(tmp_path)
    assert set(service.seat_records((), section_keys=("Spring 2027::3991",))) == {
        "Spring 2027::3991",
    }
    assert service.seat_records((), section_keys=("Summer 2026::3991",)) == {}
    # Mixed old/new requests retain the union rather than dropping older clients.
    assert set(service.seat_records(("3991",), section_keys=("Spring 2027::3984",))) == {
        "Fall 2026::3991", "Spring 2027::3991", "Spring 2027::3984",
    }


def test_api_get_uses_exact_term_for_interest_and_response(tmp_path):
    service, urls = make_service(tmp_path)
    client = TestClient(create_app(
        service.repository, data_path=SAMPLE_DATA_PATH, seat_refresh_service=service,
    ))
    resp = client.get("/api/seats", params=[("section_key", "Spring 2027::3991")])
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert set(resp.json()["records"]) == {"Spring 2027::3991"}
    assert set(service._interests) == {urls["Spring 2027"]}
    assert client.get("/api/seats", params={"section_key": "Spring 2027::ABC"}).status_code == 400
    assert client.get("/api/seats", params={"section_key": "Invalid"}).status_code == 400


def test_api_viewed_post_accepts_term_qualified_keys(tmp_path):
    service, urls = make_service(tmp_path)
    client = TestClient(create_app(
        service.repository, data_path=SAMPLE_DATA_PATH, seat_refresh_service=service,
    ))
    response = client.post("/api/seats/interest", json={
        "schedule_numbers": [], "section_keys": ["Spring 2027::3991"], "kind": "viewed",
    })
    assert response.status_code == 200
    assert response.json() == {"priority_sources": 1}
    assert set(service._interests) == {urls["Spring 2027"]}


def test_public_classes_browse_only_marks_the_displayed_term(tmp_path):
    service, urls = make_service(tmp_path)
    client = TestClient(create_app(
        service.repository, data_path=SAMPLE_DATA_PATH, seat_refresh_service=service,
    ))
    response = client.get("/api/classes", params={
        "term": "Spring 2027", "q": "CHEM 201", "page_size": 50,
    })
    assert response.status_code == 200, response.text
    assert response.json()["items"]
    assert set(service._interests) == {urls["Spring 2027"]}


def test_frontend_favorites_and_schedule_send_exact_class_keys():
    js = (Path(__file__).resolve().parents[1] / "src/classcatalog/static/app.js").read_text(encoding="utf-8")
    for name in ("refreshScheduleSeats", "refreshFavoritesSeats"):
        start = js.index(f"async function {name}() {{")
        end = js.index("  try {", start)
        chunk = js[start:end]
        assert 'params.append("section_key", key)' in chunk
        assert 'params.append("schedule_number"' not in chunk
    assert "function seatSectionKeysFor(sections)" in js
    assert "`${term}::${number}`" in js
    html = (Path(__file__).resolve().parents[1] / "src/classcatalog/static/index.html").read_text(encoding="utf-8")
    assert "app.js?v=79&amp;termseats=1" in html
