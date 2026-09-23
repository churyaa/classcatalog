

def test_schedule_layout_polish_keeps_professor_full_height_and_aligns_grouped_action() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'class="course-card-heading-row${grouped ? " is-grouped" : ""}"' in javascript
    assert 'class="grouped-schedule-side"' in javascript
    assert '<div class="component-group-toolbar">' in javascript
    assert '<div class="component-group-title">Class components</div>' in javascript
    assert '.course-side-panel > .professor-panel {' in css
    assert 'height: 100%;' in css
    assert '.course-card-heading-row.is-grouped {' in css
    assert 'grid-template-columns: minmax(0, 1fr) minmax(360px, .95fr);' in css
    assert '.grouped-schedule-side > .schedule-add-button {' in css
    assert 'width: calc(100% - .25rem);' in css
    assert 'layoutpolish=1' in html
