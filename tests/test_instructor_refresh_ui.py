from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "classcatalog" / "static"


def test_admin_has_targeted_tba_professor_refresh_control() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'id="admin-instructor-refresh-tba"' in html
    assert 'id="admin-instructor-refresh-result"' in html
    assert "Refresh TBA Professors" in html
    assert "function refreshTbaProfessorsNow()" in javascript
    assert 'fetchJson("/api/admin/instructors/refresh-tba"' in javascript
    assert 'addEventListener("click", refreshTbaProfessorsNow)' in javascript
