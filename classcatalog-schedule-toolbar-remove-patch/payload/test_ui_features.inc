

def test_schedule_drawer_omits_redundant_calendar_toolbar() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'id="schedule-calendar-view"' in html
    assert "Weekly calendar" not in html
    assert "Click a class for full details" not in html
    assert 'class="schedule-calendar-toolbar"' not in html
