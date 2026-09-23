

def test_visual_schedule_builder_drawer_and_overlap_controls_are_present() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="schedule-nav"' in html
    assert 'id="schedule-count" class="header-nav-count"' in html
    assert 'id="schedule-drawer"' in html
    assert 'id="schedule-calendar"' in html
    assert 'id="schedule-detail-view"' in html
    assert 'aria-controls="schedule-drawer"' in html
    assert 'M10 21H6.2C5.0799' in html
    assert 'const scheduleStorageKey = "classcatalog_schedule_v1";' in javascript
    assert 'window.localStorage.setItem(scheduleStorageKey' in javascript
    assert 'function meetingsOverlap(left, right)' in javascript
    assert 'function meetingDateRangesOverlap(left, right)' in javascript
    assert 'leftStart < rightEnd && rightStart < leftEnd' in javascript
    assert 'button.textContent = `Overlaps with ${conflict.course_code}`;' in javascript
    assert 'button.disabled = true;' in javascript
    assert 'function renderScheduleCalendar(sections)' in javascript
    assert 'function renderScheduleDetail(section, key)' in javascript
    assert 'remove.textContent = "Remove from Schedule";' in javascript
    assert 'function refreshScheduleSeats()' in javascript
    assert 'component-professor-layout${index === 0 ? " with-schedule" : ""}' in javascript
    assert '.schedule-drawer {' in css
    assert 'width: min(820px, 72vw);' in css
    assert 'background: color-mix(in srgb, var(--theme-accent) var(--schedule-tint), var(--surface-raised));' in css
    assert '.course-side-panel {' in css
    assert 'schedule=1' in html
