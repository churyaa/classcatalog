from __future__ import annotations

from pathlib import Path

from classcatalog.catalog.parser import (
    CatalogLink,
    extract_catalog_navigation_links,
    extract_program_links,
    likely_requirement_navigation_link,
    parse_program_page,
    parse_requirement_page,
)
from classcatalog.models import ProgramClassification

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"


def test_program_page_classifies_major_prep_core_and_electives() -> None:
    program = parse_program_page(
        (FIXTURES / "program-computer-science.html").read_text(encoding="utf-8"),
        program_name="Computer Science, B.S.",
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11846",
    )

    mapped = {(item.course_code, item.classification) for item in program.mappings}
    assert ("CS 150", ProgramClassification.MAJOR_PREP) in mapped
    assert ("MATH 150", ProgramClassification.MAJOR_PREP) in mapped
    assert ("CS 210", ProgramClassification.MAJOR_COURSE) in mapped
    assert ("CS 470", ProgramClassification.ELECTIVE) in mapped
    assert program.mapped_course_count == 7
    assert program.unmapped_course_codes == ("COMM 103",)


def test_requirement_page_extracts_ge_and_graduation_mappings() -> None:
    requirements = parse_requirement_page(
        (FIXTURES / "requirements-general-education.html").read_text(encoding="utf-8"),
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=999",
    )
    by_code = {requirement.code: requirement for requirement in requirements}

    assert by_code["GE A1"].course_codes == ("COMM 103",)
    assert by_code["GE A3"].course_codes == ("RWS 200",)
    assert by_code["GE 2"].course_codes == ("MATH 120", "MATH 150")
    assert by_code["AI"].course_codes == ("HIST 109",)


def test_current_acalog_show_course_markup_is_parsed() -> None:
    html = """
    <html><body><div class='block_content'>
      <div class='acalog-core'>
        <h2>Area 1. English Communication</h2>
      </div>
      <div class='acalog-core'>
        <h3>1B. Critical Thinking</h3>
        <ul>
          <li class='acalog-course'>
            <a href='#' onclick="showCourse('12', '88247', this, 'payload'); return false;">
              AFRAS 200 - Intermediate Expository Writing
            </a>
          </li>
        </ul>
      </div>
    </div></body></html>
    """

    requirements = parse_requirement_page(
        html,
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884",
    )

    assert len(requirements) == 1
    assert requirements[0].code == "GE 1B"
    assert requirements[0].course_codes == ("AFRAS 200",)


def test_current_acalog_program_show_course_markup_is_classified() -> None:
    html = """
    <html><body><div class='block_content'>
      <div class='acalog-core'>
        <h2>Preparation for the Major</h2>
        <ul>
          <li class='acalog-course'>
            <a href='#' onclick="showCourse('12', '90001', this, 'payload'); return false;">
              CS 150 - Introduction to Computer Programming
            </a>
          </li>
        </ul>
      </div>
      <div class='acalog-core'>
        <h2>Major Requirements</h2>
        <ul>
          <li class='acalog-course'>
            <a href='#' onclick="showCourse('12', '90002', this, 'payload'); return false;">
              CS 210 - Data Structures
            </a>
          </li>
        </ul>
      </div>
    </div></body></html>
    """

    program = parse_program_page(
        html,
        program_name="Computer Science, B.S.",
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=1",
    )

    mapped = {(item.course_code, item.classification) for item in program.mappings}
    assert ("CS 150", ProgramClassification.MAJOR_PREP) in mapped
    assert ("CS 210", ProgramClassification.MAJOR_COURSE) in mapped
    assert program.mappings[0].source_url.startswith(
        "https://catalog.sdsu.edu/preview_course_nopop.php?catoid=12&coid="
    )


def test_program_unmapped_codes_exclude_courses_classified_elsewhere() -> None:
    html = """
    <html><body><div class='block_content'>
      <h1>Aerospace Engineering, B.S.</h1>
      <p>
        Students must complete
        <a href='#' onclick="showCourse('12', '1', this, 'payload');">A E 301</a>.
      </p>
      <h2>Major</h2>
      <ul><li class='acalog-course'>
        <a href='#' onclick="showCourse('12', '1', this, 'payload');">A E 301</a>
      </li></ul>
    </div></body></html>
    """

    program = parse_program_page(
        html,
        program_name="Aerospace Engineering, B.S.",
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=1",
    )

    assert program.mapped_course_count == 1
    assert program.unmapped_course_codes == ()
    assert program.warnings == ()


def test_course_popovers_are_not_requirement_navigation_links() -> None:
    html = """
    <html><body>
      <a href='#area4-social-and-behavioral-sciences'>
        Area 4. Social and Behavioral Sciences
      </a>
      <a href='#tt5455'
         onclick="acalogPopup('preview_course.php?catoid=12&coid=88121&print', 'course');">
        AFRAS 101 - Introduction to Africana Studies: Social and Behavioral Sciences Units: 3
      </a>
    </body></html>
    """

    links = extract_catalog_navigation_links(
        html,
        base_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884",
        catoid=12,
    )

    requirement_links = [link for link in links if likely_requirement_navigation_link(link)]
    assert [link.title for link in requirement_links] == [
        "Area 4. Social and Behavioral Sciences"
    ]


def test_degree_and_roadmap_titles_are_not_requirement_navigation_links() -> None:
    links = (
        CatalogLink(
            title="Classics, Emphasis in Classical Humanities, B.A.",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11954",
        ),
        CatalogLink(
            title="Classics, Emphasis in Classical Humanities, B.A. - Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=12812",
        ),
        CatalogLink(
            title="General Education Requirements",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884",
        ),
    )

    assert [link.title for link in links if likely_requirement_navigation_link(link)] == [
        "General Education Requirements"
    ]


def test_program_title_humanities_does_not_create_ge_c2_requirement() -> None:
    html = """
    <html><body><div class='block_content'>
      <h1>Classics, Emphasis in Classical Humanities, B.A.</h1>
      <h2>Major</h2>
      <ul><li class='acalog-course'>
        <a href='#' onclick="showCourse('12', '1', this, 'payload');">CLASS 320</a>
      </li></ul>
    </div></body></html>
    """

    requirements = parse_requirement_page(
        html,
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11954",
    )

    assert requirements == ()


def test_program_link_extraction_stays_in_catalog_year() -> None:
    html = """
    <a href='preview_program.php?catoid=12&poid=1'>Computer Science, B.S.</a>
    <a href='preview_program.php?catoid=11&poid=2'>Old Computer Science, B.S.</a>
    """
    links = extract_program_links(
        html,
        base_url="https://catalog.sdsu.edu/programs.php?catoid=12",
        catoid=12,
    )
    assert [link.title for link in links] == ["Computer Science, B.S."]


def test_catalog_catoid_discovery_prefers_requested_catalog_year() -> None:
    from classcatalog.catalog.parser import discover_catalog_catoid

    html = """
    <html><body>
      <nav>
        <a href='index.php?catoid=10'>2025-2026 Catalog</a>
        <a href='index.php?catoid=11'>2026-2027 Catalog</a>
        <a href='programs.php?catoid=11'>Programs</a>
        <a href='content.php?catoid=11&navoid=100'>Academic Programs</a>
        <a href='index.php?catoid=9'>2024-2025 Catalog</a>
      </nav>
    </body></html>
    """

    assert (
        discover_catalog_catoid(
            html,
            base_url="https://catalog.sdsu.edu/index.php",
            catalog_year="2026-2027",
        )
        == 11
    )


def test_catalog_catoid_discovery_uses_navigation_frequency_without_year_label() -> None:
    from classcatalog.catalog.parser import discover_catalog_catoid

    html = """
    <a href='programs.php?catoid=11'>Programs</a>
    <a href='content.php?catoid=11&navoid=1'>Requirements</a>
    <a href='preview_program.php?catoid=11&poid=1'>Computer Science, B.S.</a>
    <a href='index.php?catoid=10'>Archived Catalog</a>
    """

    assert (
        discover_catalog_catoid(
            html,
            base_url="https://catalog.sdsu.edu/index.php",
            catalog_year="2026-2027",
        )
        == 11
    )


def test_resource_not_found_page_detection() -> None:
    from classcatalog.catalog.parser import is_resource_not_found_page

    html = """
    <main>
      <h1>Resource Not Found</h1>
      <p>We were unable to locate the resource you attempted to access.</p>
    </main>
    """
    assert is_resource_not_found_page(html, title="Resource Not Found")
    assert not is_resource_not_found_page("<main><h1>2026-2027 Catalog</h1></main>")


def test_sdsu_terse_major_and_required_lower_division_headings_are_classified() -> None:
    html = """
    <html><body><div class='block_content'>
      <div class='acalog-core'>
        <h2>Required Lower Division Nursing Courses</h2>
        <ul><li class='acalog-course'>
          <a href='#' onclick="showCourse('12', '1', this, 'payload');">
            NURS 202 - Client Assessment
          </a><strong>Units:</strong> 3
        </li></ul>
      </div>
      <div class='acalog-core'>
        <h2>Major</h2>
        <ul><li class='acalog-course'>
          <a href='#' onclick="showCourse('12', '2', this, 'payload');">
            NURS 300 - Nursing Care
          </a><strong>Units:</strong> 8
        </li></ul>
      </div>
    </div></body></html>
    """

    program = parse_program_page(
        html,
        program_name="Nursing, B.S. - Second Bachelor’s Degree",
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=1",
    )

    by_code = {mapping.course_code: mapping for mapping in program.mappings}
    assert by_code["NURS 202"].classification == ProgramClassification.MAJOR_PREP
    assert by_code["NURS 202"].group == "Required Lower Division Nursing Courses"
    assert by_code["NURS 300"].classification == ProgramClassification.MAJOR_COURSE
    assert by_code["NURS 300"].group == "Major"
    assert program.unmapped_course_codes == ()


def test_undergraduate_degree_detection_accepts_sdsu_degree_spelling_variants() -> None:
    from classcatalog.catalog.parser import is_undergraduate_degree_program

    accepted = (
        "Biology, B.S.",
        "Biology, BS",
        "Art, BFA",
        "Nursing, BSN",
        "Social Work, BSW",
        "Architecture, B.Arch.",
        "Music, Bachelor of Music",
        "Engineering, B.S./M.S.",
        "Mathematics, B.S. - Single Subject Teaching Credential",
    )
    rejected = (
        "Biology Minor",
        "Data Science Certificate",
        "Biology, M.S.",
        "Education, Ed.D.",
    )

    assert all(is_undergraduate_degree_program(title) for title in accepted)
    assert not any(is_undergraduate_degree_program(title) for title in rejected)


def test_sdsu_named_degree_requirements_are_classified_without_guessing() -> None:
    html = """
    <html><body><div class='block_content'>
      <h1>Urban Studies, Urban Sustainability Specialization, B.A.</h1>

      <h2>Graduation Writing Assessment Requirement</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','1',this,'x');">RWS 305W</a></li>

      <h2>Additional Lower Division Courses</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','2',this,'x');">P H 233</a></li>

      <h2>Teacher Credential Program Prerequisites</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','3',this,'x');">ED 451</a></li>

      <h2>Credential Requirements</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','4',this,'x');">SPED 526</a></li>

      <h2>Additional Requirements</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','5',this,'x');">MUSIC 105</a></li>

      <h2>Requirements for Specialization</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','6',this,'x');">POL S 430</a></li>

      <h2>Breadth</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','7',this,'x');">PSY 340</a></li>

      <h2>Auxiliary Area</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','8',this,'x');">CHEM 200</a></li>

      <h2>Capstone Requirement</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','9',this,'x');">POL S 495</a></li>

      <h2>International Experience</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','10',this,'x');">GEN S 450</a></li>

      <h2>Urban Sustainability</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','11',this,'x');">SUSTN 334</a></li>
    </div></body></html>
    """

    program = parse_program_page(
        html,
        program_name="Urban Studies, Urban Sustainability Specialization, B.A.",
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=1",
    )

    by_code = {mapping.course_code: mapping.classification for mapping in program.mappings}
    assert by_code == {
        "RWS 305W": ProgramClassification.ELECTIVE,
        "P H 233": ProgramClassification.MAJOR_PREP,
        "ED 451": ProgramClassification.MAJOR_PREP,
        "SPED 526": ProgramClassification.MAJOR_COURSE,
        "MUSIC 105": ProgramClassification.MAJOR_COURSE,
        "POL S 430": ProgramClassification.ELECTIVE,
        "PSY 340": ProgramClassification.ELECTIVE,
        "CHEM 200": ProgramClassification.ELECTIVE,
        "POL S 495": ProgramClassification.ELECTIVE,
        "GEN S 450": ProgramClassification.ELECTIVE,
        "SUSTN 334": ProgramClassification.ELECTIVE,
    }
    assert program.unmapped_course_codes == ()
    assert program.warnings == ()


def test_program_parser_ignores_impaction_notes_recommendations_and_general_education() -> None:
    html = """
    <html><body><div class='block_content'>
      <h1>International Business, Spanish - Latin America Emphasis, B.A.</h1>
      <h2>Impacted Program</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','1',this,'x');">COMM 103</a></li>
      <h2>Recommended</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','2',this,'x');">ECON 101</a></li>
      <h2>Note</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','3',this,'x');">CHEM 417</a></li>
      <h2>General Education</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','4',this,'x');">PHIL 330</a></li>
      <h2>Major</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','5',this,'x');">MGT 405</a></li>
    </div></body></html>
    """

    program = parse_program_page(
        html,
        program_name="International Business, Spanish - Latin America Emphasis, B.A.",
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=1",
    )

    assert [(mapping.course_code, mapping.classification) for mapping in program.mappings] == [
        ("MGT 405", ProgramClassification.MAJOR_COURSE)
    ]
    assert program.unmapped_course_codes == ()
    assert program.warnings == ()


def test_program_parser_handles_sdsu_typo_and_plural_impaction_heading() -> None:
    html = """
    <html><body><div class='block_content'>
      <h1>Biology, B.S. in Preparation for the Single Subject Teaching Credential</h1>
      <h2>Addtional Requirements</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','1',this,'x');">TE 462</a></li>
      <h2>Impacted Programs</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','2',this,'x');">SPAN 282</a></li>
    </div></body></html>
    """

    program = parse_program_page(
        html,
        program_name=(
            "Biology, B.S. in Preparation for the Single Subject Teaching Credential"
        ),
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=1",
    )

    assert [(mapping.course_code, mapping.classification) for mapping in program.mappings] == [
        ("TE 462", ProgramClassification.MAJOR_COURSE)
    ]
    assert program.unmapped_course_codes == ()
    assert program.warnings == ()


def test_standard_ecl_program_handles_honors_option_and_selection_prose() -> None:
    html = """
    <html><body><div class='block_content'>
      <h1>English and Comparative Literature, B.A.</h1>
      <h2>Selection of Courses</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','1',this,'x');">RWS 100</a></li>
      <h2>English Honors Variation</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','2',this,'x');">ECL 498</a></li>
    </div></body></html>
    """

    program = parse_program_page(
        html,
        program_name="English and Comparative Literature, B.A.",
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11989",
    )

    observed = [
        (mapping.course_code, mapping.classification, mapping.group)
        for mapping in program.mappings
    ]
    assert observed == [
        ("ECL 498", ProgramClassification.ELECTIVE, "English Honors Variation")
    ]
    assert program.unmapped_course_codes == ()
    assert program.warnings == ()


def test_degree_type_is_found_before_credential_or_certificate_suffix() -> None:
    html = """
    <html><body><div class='block_content'>
      <h2>Major</h2>
      <li class='acalog-course'><a href='#' onclick="showCourse('12','1',this,'x');">BIOL 350</a></li>
    </div></body></html>
    """

    credential = parse_program_page(
        html,
        program_name=(
            "Biology, B.S. in Preparation for the Single Subject Teaching Credential "
            "in Science/Biological Sciences"
        ),
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=1",
    )
    certificate = parse_program_page(
        html,
        program_name="Chemistry, B.S. and Certificate of the American Chemical Society",
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=2",
    )

    assert credential.degree_type == "B.S."
    assert certificate.degree_type == "B.S."


def test_roadmap_only_program_preserves_explicit_major_courses_with_partial_warning() -> None:
    html = """
    <html><head><title>Program: Humanities, B.A. - Roadmap - SDSU</title></head>
    <body><div class='block_content'>
      <h1>Humanities, B.A. - Roadmap</h1>
      <ul>
        <li><p>HUM 101 - Introduction to Humanities Units: 3</p><ul><li><p>Major Prep</p></li></ul></li>
        <li><p>HUM 390W - Writing in the Humanities Units: 3</p><ul><li><p>GWAR: Upper Division Writing Course / Major</p></li></ul></li>
        <li><p>HUM 400 - Senior Seminar Units: 3</p><ul><li><p>Major</p></li></ul></li>
        <li><p>Elective Units: 3</p><ul><li><p>Recommended: HUM 201</p></li></ul></li>
      </ul>
    </div></body></html>
    """
    parsed = parse_program_page(
        html,
        program_name="Humanities, B.A.",
        catalog_year="2026-2027",
        source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=12827",
    )
    assert {(m.course_code, m.classification.value) for m in parsed.mappings} == {
        ("HUM 101", "major_prep"),
        ("HUM 390W", "major_course"),
        ("HUM 400", "major_course"),
    }
    assert "HUM 201" not in {m.course_code for m in parsed.mappings}
    assert any("Roadmap-only program source" in warning for warning in parsed.warnings)
