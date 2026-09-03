from __future__ import annotations

from classcatalog.scraping.facet_parser import (
    build_facet_choice_post,
    find_facet_choice,
    find_facet_choices,
    is_facet_choice_selected,
)
from classcatalog.scraping.session import (
    RawSubjectPages,
    SdsuHttpConfig,
    SdsuPeopleSoftSession,
)

CURRENT_URL = "https://example.test/results?SEARCH_TEXT=BIOL&ES_STRM=2267"


def _facet_controls(*, career: str | None = None, units: str | None = None) -> str:
    graduate_checked = " checked" if career == "Graduate" else ""
    undergraduate_checked = " checked" if career == "Undergraduate" else ""
    units_1_3_checked = " checked" if units == "1 - 3 Units" else ""
    units_4_6_checked = " checked" if units == "4 - 6 Units" else ""
    graduate_shadow = "Y" if career == "Graduate" else "N"
    undergraduate_shadow = "Y" if career == "Undergraduate" else "N"
    units_1_3_shadow = "Y" if units == "1 - 3 Units" else "N"
    units_4_6_shadow = "Y" if units == "4 - 6 Units" else "N"
    return f"""
      <input type='hidden' name='PTS_SELECT$chk$0' value='N'>
      <input type='checkbox' id='PTS_SELECT$0' name='PTS_SELECT$0' value='Y'>
      <label for='PTS_SELECT$0'>Open Classes Only</label>
      <input type='hidden' name='PTS_SELECT$chk$6' value='Y'>
      <input type='checkbox' id='PTS_SELECT$6' name='PTS_SELECT$6' value='Y' checked>
      <label for='PTS_SELECT$6'>BIOL/Biology</label>
      <fieldset>
        <legend>Course Career</legend>
        <input type='hidden' name='PTS_SELECT$chk$3' value='{graduate_shadow}'>
        <input type='checkbox' id='PTS_SELECT$3' name='PTS_SELECT$3' value='Y'{graduate_checked}>
        <label for='PTS_SELECT$3'>Graduate</label>
        <input type='hidden' name='PTS_SELECT$chk$4' value='{undergraduate_shadow}'>
        <input type='checkbox' id='PTS_SELECT$4' name='PTS_SELECT$4'
               value='Y'{undergraduate_checked}>
        <label for='PTS_SELECT$4'>Undergraduate</label>
      </fieldset>
      <fieldset>
        <legend>Number of Units</legend>
        <input type='hidden' name='PTS_SELECT$chk$10' value='{units_1_3_shadow}'>
        <input type='checkbox' id='PTS_SELECT$10' name='PTS_SELECT$10' value='Y'{units_1_3_checked}>
        <label for='PTS_SELECT$10'>1 - 3 Units</label>
        <input type='hidden' name='PTS_SELECT$chk$11' value='{units_4_6_shadow}'>
        <input type='checkbox' id='PTS_SELECT$11' name='PTS_SELECT$11' value='Y'{units_4_6_checked}>
        <label for='PTS_SELECT$11'>4 - 6 Units</label>
      </fieldset>
    """


def _course_row(
    catalog: str,
    *,
    crse_id: str,
    career: str,
    class_number: str,
    sections: int = 1,
) -> str:
    return f"""
      <li class='ps_grid-row psc_rowact'>
        <p hidden>BIOL {catalog}</p>
        <h3 class='course-title'>Biology {catalog}</h3>
        <span>{sections} sections</span>
        <a href='/psc/CSDPRD/EMPLOYEE/SA/c/SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL
          ?Page=SSR_CS_WRAP_FL&amp;CRSE_ID={crse_id}&amp;CRSE_OFFER_NBR=1
          &amp;STRM=2267&amp;ACAD_CAREER={career}&amp;CLASS_NBR={class_number}'>View Details</a>
      </li>
    """


def _page(
    *,
    rows: tuple[str, ...],
    capped: bool,
    career: str | None = None,
    units: str | None = None,
) -> str:
    cap_text = "Search results have exceeded a limit set by your institution." if capped else ""
    return f"""
    <html><body>
      <form id='win0' name='win0' method='post' action='/results'>
        <input type='hidden' name='ICStateNum' value='12'>
        <input type='hidden' name='ICAction' value='None'>
        <input type='hidden' name='ICSID' value='state'>
        {_facet_controls(career=career, units=units)}
        <div>{cap_text}</div>
        <ul>{''.join(rows)}</ul>
      </form>
    </body></html>
    """


def test_generic_facet_choice_post_is_exclusive_inside_group() -> None:
    html = _page(rows=(), capped=True)
    choices = find_facet_choices(html, "Course Career")
    assert [choice.label for choice in choices] == ["Graduate", "Undergraduate"]

    undergraduate = find_facet_choice(
        html,
        group="Course Career",
        label="Undergraduate",
    )
    post = build_facet_choice_post(
        html,
        current_url=CURRENT_URL,
        choice=undergraduate,
    )
    payload = dict(post.fields)

    assert payload["ICAction"] == "PTS_SELECT$4"
    assert payload["PTS_SELECT$chk$3"] == "N"
    assert "PTS_SELECT$3" not in payload
    assert payload["PTS_SELECT$chk$4"] == "Y"
    assert payload["PTS_SELECT$4"] == "Y"
    assert payload["PTS_SELECT$chk$6"] == "Y"
    assert payload["PTS_SELECT$6"] == "Y"
    assert payload["PTS_SELECT$chk$0"] == "N"
    assert "PTS_SELECT$0" not in payload


def test_facet_choice_ignores_people_soft_yes_no_indicator_label() -> None:
    html = """
    <form id='win0'>
      <fieldset>
        <legend>Course Career</legend>
        <input type='checkbox' id='PTS_SELECT$3' value='Y' title='Graduate'>
        <label class='ps_indicator' for='PTS_SELECT$3'>Yes No</label>
        <label class='ps-label' for='PTS_SELECT$3'>Graduate</label>
      </fieldset>
    </form>
    """
    choices = find_facet_choices(html, "Course Career")
    assert len(choices) == 1
    assert choices[0].label == "Graduate"


def test_selected_facet_can_be_verified_from_people_soft_breadcrumb() -> None:
    html = """
    <form id='win0'>
      <fieldset>
        <legend>Class Status</legend>
        <input type='checkbox' id='PTS_SELECT$0' value='Y'>
        <label class='ps-label' for='PTS_SELECT$0'>Open Classes</label>
        <input type='checkbox' id='PTS_SELECT$1' value='Y'>
        <label class='ps-label' for='PTS_SELECT$1'>Wait List Classes</label>
      </fieldset>
      <div id='win0divPTS_SRCH_PTS_BREADCRUMB_GB'>
        <table title='Selected Filters'>
          <tr><td>
            <a aria-label='Remove Closed Classes Only filter'>
              <span class='ps-text'>Closed Classes</span>
            </a>
          </td></tr>
        </table>
      </div>
    </form>
    """

    assert [choice.label for choice in find_facet_choices(html, "Class Status")] == [
        "Open Classes",
        "Wait List Classes",
    ]
    assert is_facet_choice_selected(
        html,
        group="Class Status",
        label="Closed Classes",
    )
    assert not is_facet_choice_selected(
        html,
        group="Class Status",
        label="Open Classes",
    )


def test_selected_facet_prefers_checked_control_when_value_remains_visible() -> None:
    html = """
    <form id='win0'>
      <fieldset>
        <legend>Course Career</legend>
        <input type='checkbox' id='PTS_SELECT$3' value='Y' checked>
        <label class='ps-label' for='PTS_SELECT$3'>Graduate</label>
        <input type='checkbox' id='PTS_SELECT$4' value='Y'>
        <label class='ps-label' for='PTS_SELECT$4'>Undergraduate</label>
      </fieldset>
    </form>
    """

    assert is_facet_choice_selected(html, group="Course Career", label="Graduate")
    assert not is_facet_choice_selected(
        html,
        group="Course Career",
        label="Undergraduate",
    )


class _PartitionHarness(SdsuPeopleSoftSession):
    def __init__(self, mapping: dict[tuple[tuple[str, str], ...], str]) -> None:
        super().__init__(SdsuHttpConfig(delay_seconds=0, max_retries=0))
        self.mapping = mapping
        self.requested_paths: list[tuple[tuple[str, str], ...]] = []

    def _fetch_subject_partition_path(
        self,
        *,
        term: str,
        term_code: str,
        subject: str,
        filters: tuple[tuple[str, str], ...],
    ) -> tuple[str, str]:
        del term, term_code, subject
        self.requested_paths.append(filters)
        return self.mapping[filters], CURRENT_URL


def test_capped_subject_recursively_partitions_and_merges_courses() -> None:
    base = _page(
        rows=(
            _course_row("100", crse_id="100", career="UGRD", class_number="1000"),
            _course_row("200", crse_id="200", career="UGRD", class_number="2000"),
        ),
        capped=True,
    )
    undergraduate_capped = _page(
        rows=(
            _course_row("100", crse_id="100", career="UGRD", class_number="1000"),
            _course_row("200", crse_id="200", career="UGRD", class_number="2000"),
        ),
        capped=True,
        career="Undergraduate",
    )
    graduate = _page(
        rows=(_course_row("600", crse_id="600", career="GRAD", class_number="6000"),),
        capped=False,
        career="Graduate",
    )
    undergraduate_1_3 = _page(
        rows=(_course_row("100", crse_id="100", career="UGRD", class_number="1000"),),
        capped=False,
        career="Undergraduate",
        units="1 - 3 Units",
    )
    undergraduate_4_6 = _page(
        rows=(_course_row("200", crse_id="200", career="UGRD", class_number="2000"),),
        capped=False,
        career="Undergraduate",
        units="4 - 6 Units",
    )

    mapping = {
        (("Course Career", "Graduate"),): graduate,
        (("Course Career", "Undergraduate"),): undergraduate_capped,
        (
            ("Course Career", "Undergraduate"),
            ("Number of Units", "1 - 3 Units"),
        ): undergraduate_1_3,
        (
            ("Course Career", "Undergraduate"),
            ("Number of Units", "4 - 6 Units"),
        ): undergraduate_4_6,
    }
    client = _PartitionHarness(mapping)
    pages = RawSubjectPages(
        subject="BIOL",
        term="Fall 2026",
        term_code="2267",
        search_url=CURRENT_URL,
        initial_html=base,
        filtered_html=base,
        exact_facet_applied=True,
    )

    partition_pages = client._partition_capped_subject_pages(pages)
    partitioned_pages = RawSubjectPages(
        subject=pages.subject,
        term=pages.term,
        term_code=pages.term_code,
        search_url=pages.search_url,
        initial_html=pages.initial_html,
        filtered_html=pages.filtered_html,
        exact_facet_applied=True,
        partition_pages=partition_pages,
    )
    result = client.parse_subject_pages(partitioned_pages)

    assert len(partition_pages) == 3
    assert all(not leaf.capped for leaf in partition_pages)
    assert result.complete is True
    assert result.partitioned is True
    assert result.partition_leaf_count == 3
    assert result.unresolved_partition_count == 0
    assert result.partition_facets == ("Course Career", "Number of Units")
    assert [course.course_code for course in result.courses] == [
        "BIOL 100",
        "BIOL 200",
        "BIOL 600",
    ]


def test_overlapping_partition_courses_are_deduplicated() -> None:
    base = _page(rows=(), capped=True)
    first = _page(
        rows=(_course_row("100", crse_id="100", career="UGRD", class_number="1000", sections=1),),
        capped=False,
        career="Undergraduate",
    )
    second = _page(
        rows=(_course_row("100", crse_id="100", career="UGRD", class_number="1000", sections=3),),
        capped=False,
        career="Graduate",
    )
    client = _PartitionHarness({})
    from classcatalog.scraping.session import RawSubjectPartitionPage

    pages = RawSubjectPages(
        subject="BIOL",
        term="Fall 2026",
        term_code="2267",
        search_url=CURRENT_URL,
        initial_html=base,
        filtered_html=base,
        exact_facet_applied=True,
        partition_pages=(
            RawSubjectPartitionPage(
                filters=(("Course Career", "Undergraduate"),),
                url=CURRENT_URL,
                html=first,
                result_count=1,
                capped=False,
            ),
            RawSubjectPartitionPage(
                filters=(("Course Career", "Graduate"),),
                url=CURRENT_URL,
                html=second,
                result_count=1,
                capped=False,
            ),
        ),
    )

    result = client.parse_subject_pages(pages)

    assert len(result.courses) == 1
    assert result.courses[0].course_code == "BIOL 100"
    assert result.courses[0].section_count == 3


def test_partition_open_status_allows_missing_open_choice_when_not_selected() -> None:
    html = """
    <form id='win0'>
      <fieldset>
        <legend>Class Status</legend>
        <input type='checkbox' id='PTS_SELECT$1' value='Y'>
        <label class='ps-label' for='PTS_SELECT$1'>Wait List Classes</label>
        <input type='checkbox' id='PTS_SELECT$2' value='Y'>
        <label class='ps-label' for='PTS_SELECT$2'>Closed Classes</label>
      </fieldset>
    </form>
    """

    SdsuPeopleSoftSession._verify_partition_open_status(
        html,
        intentionally_open_only=False,
    )


def test_partition_open_status_recognizes_selected_open_breadcrumb_without_checkbox() -> None:
    html = """
    <form id='win0'>
      <fieldset>
        <legend>Class Status</legend>
        <input type='checkbox' id='PTS_SELECT$1' value='Y'>
        <label class='ps-label' for='PTS_SELECT$1'>Wait List Classes</label>
      </fieldset>
      <div id='win0divPTS_SRCH_PTS_BREADCRUMB_GB'>
        <table title='Selected Filters'>
          <tr><td>
            <a aria-label='Remove Open Classes Only filter'>
              <span class='ps-text'>Open Classes</span>
            </a>
          </td></tr>
        </table>
      </div>
    </form>
    """

    SdsuPeopleSoftSession._verify_partition_open_status(
        html,
        intentionally_open_only=True,
    )


def _component_partition_page(*, rows: tuple[str, ...], capped: bool, selected: str | None = None) -> str:
    cap_text = "Search results have exceeded a limit set by your institution." if capped else ""
    lecture_checked = " checked" if selected == "Lecture" else ""
    activity_checked = " checked" if selected == "Activity" else ""
    return f"""
    <html><body>
      <form id='win0' name='win0' method='post' action='/results'>
        <input type='hidden' name='ICStateNum' value='12'>
        <input type='hidden' name='ICAction' value='None'>
        <input type='hidden' name='ICSID' value='state'>
        <input type='hidden' name='PTS_SELECT$chk$0' value='N'>
        <input type='checkbox' id='PTS_SELECT$0' name='PTS_SELECT$0' value='Y'>
        <label for='PTS_SELECT$0'>Open Classes Only</label>
        <input type='hidden' name='PTS_SELECT$chk$6' value='Y'>
        <input type='checkbox' id='PTS_SELECT$6' name='PTS_SELECT$6' value='Y' checked>
        <label for='PTS_SELECT$6'>BIOL/Biology</label>
        <fieldset>
          <legend>Class Component</legend>
          <input type='checkbox' id='PTS_SELECT$20' value='Y'{lecture_checked}>
          <label for='PTS_SELECT$20'>Lecture</label>
          <input type='checkbox' id='PTS_SELECT$21' value='Y'{activity_checked}>
          <label for='PTS_SELECT$21'>Activity</label>
        </fieldset>
        <div>{cap_text}</div>
        <ul>{''.join(rows)}</ul>
      </form>
    </body></html>
    """


def test_capped_subject_can_fall_back_to_class_component_after_primary_facets() -> None:
    base = _component_partition_page(rows=(), capped=True)
    lecture = _component_partition_page(
        rows=(_course_row("101", crse_id="101", career="UGRD", class_number="1010"),),
        capped=False,
        selected="Lecture",
    )
    activity = _component_partition_page(
        rows=(_course_row("102", crse_id="102", career="UGRD", class_number="1020"),),
        capped=False,
        selected="Activity",
    )
    mapping = {
        (("Class Component", "Lecture"),): lecture,
        (("Class Component", "Activity"),): activity,
    }
    client = _PartitionHarness(mapping)
    pages = RawSubjectPages(
        subject="BIOL",
        term="Fall 2026",
        term_code="2267",
        search_url=CURRENT_URL,
        initial_html=base,
        filtered_html=base,
        exact_facet_applied=True,
    )

    partition_pages = client._partition_capped_subject_pages(pages)

    assert len(partition_pages) == 2
    assert all(not leaf.capped for leaf in partition_pages)
    assert {leaf.filters for leaf in partition_pages} == {
        (("Class Component", "Lecture"),),
        (("Class Component", "Activity"),),
    }
