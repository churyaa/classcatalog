"""Prevent stale waitlist-label regressions and require frontend cache bust."""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "classcatalog" / "static"


def test_waitlist_uses_verified_waitlist_places_not_classroom_size():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    begin = source.index("function seatSummary(section) {")
    end = source.index("function seatFreshnessDetails(", begin)
    renderer = source[begin:end]
    assert 'const primary = status === "waitlist"' in renderer
    assert '${statusLabel} ${section.waitlist_available}/${section.waitlist_capacity}' in renderer
    assert 'section.waitlist_capacity > 0' in renderer
    assert 'section.waitlist_available <= section.waitlist_capacity' in renderer
    assert '&& section.waitlist_available > 0' in renderer


def test_frontend_loads_fresh_waitlist_renderer_version():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'app.js?v=79&amp;' in html
    assert 'app.js?v=78&amp;' not in html
