

def test_grouped_schedule_button_uses_component_header_area() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'class="component-group-toolbar"' in javascript
    assert '<div class="component-group-title">Class components</div>' in javascript
    assert '${scheduleActionButtonMarkup()}' in javascript
    assert 'component-professor-layout${index === 0 ? " with-schedule" : ""}' not in javascript
    assert '.component-group-toolbar {' in css
    assert '.component-group-toolbar > .schedule-add-button {' in css
    assert 'groupedschedule=1' in html
