

def test_schedule_button_does_not_compress_professor_panel() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "Visual schedule builder: preserve professor panel width" in css
    assert "#course-list .course-card:not(.course-card-grouped)" in css
    assert "minmax(360px, .95fr)" in css
    assert "grid-template-columns: minmax(7.6rem, 8.4rem) minmax(13.5rem, 1fr);" in css
    assert ".component-professor-layout.with-schedule {" in css
    assert "grid-template-columns: 1fr;" in css
    assert "schedulelayout=1" in html
