from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "src" / "classcatalog" / "static"
SAMPLE_DATA = ROOT / "src" / "classcatalog" / "data" / "sample_sections.json"


def test_top_and_bottom_pagination_controls_are_present() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="top-pagination"' in html
    assert 'id="top-previous-page"' in html
    assert 'id="top-page-buttons"' in html
    assert 'id="top-next-page"' in html
    assert 'id="pagination"' in html
    assert 'id="previous-page"' in html
    assert 'id="next-page"' in html


def test_course_cards_render_seat_occupancy_and_description_tooltips() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function seatSummary(section)" in javascript
    assert "section.seat_capacity" in javascript
    assert "section.seats_enrolled" in javascript
    assert 'class="course-info"' in javascript
    assert 'class="course-tooltip"' in javascript
    assert "Course description is not available yet." in javascript


def test_sample_sections_include_occupancy_and_descriptions() -> None:
    sections = json.loads(SAMPLE_DATA.read_text(encoding="utf-8"))
    assert sections
    assert all(section.get("description") for section in sections)
    assert all(section.get("seat_capacity") is not None for section in sections)
    assert all(section.get("seats_enrolled") is not None for section in sections)

    by_code = {section["course_code"]: section for section in sections}
    assert by_code["BIOL 100"]["seat_status"] == "closed"
    assert by_code["BIOL 100"]["seats_enrolled"] == 40
    assert by_code["BIOL 100"]["seat_capacity"] == 40
    assert by_code["CS 150"]["description"].startswith("Computing methodology")


def test_time_filters_have_individual_visible_reset_controls() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert 'data-time-target="time-from"' in html
    assert 'data-time-target="time-to"' in html
    assert 'aria-label="Reset Starts after to any time"' in html
    assert 'aria-label="Reset Ends before to any time"' in html
    assert ".time-filter-label-row" in css
    assert "border: 1px solid var(--red);" in css
    assert 'function ensureTimeResetButtons()' in javascript
    assert 'button.textContent = "Reset"' in javascript
    assert 'function resetTimeFilter(targetId)' in javascript
    assert 'input.value = ""' in javascript


def test_filter_sidebar_stays_fixed_while_results_use_page_scroll() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "@media (min-width: 761px)" in css
    assert "position: sticky;" in css
    assert "top: calc(var(--toolbar-sticky-height) + 1rem);" in css
    assert "max-height: calc(100vh - var(--toolbar-sticky-height) - 2rem);" in css
    assert "overflow-y: auto;" in css
    assert ".results {" in css
    assert "overflow: visible;" in css
    assert 'window.matchMedia("(min-width: 761px)")' in javascript
    assert 'window.scrollTo({ top: Math.max(0, top), behavior: "smooth" })' in javascript


def test_empty_catalog_mappings_render_visible_disabled_filter_options() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "Program classification" in html
    assert "Requirements" in html
    assert "fallbackClassifications" in javascript
    assert "fallbackRequirements" in javascript
    assert "Requirement mappings are not loaded" in javascript
    assert "Program classification mappings are not loaded" in javascript


def test_catalog_profile_ui_and_live_filter_controls_are_present() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="catalog-status"' in html
    assert 'id="program-summary"' in html
    assert 'id="program-summary-source"' in html
    assert html.count("Planning aid only. Non-course conditions, grades, units, residency, and official degree progress still require SDSU degree evaluation and advisor review.") == 1
    assert "fetchJson(\"/api/catalog/status\")" in javascript
    assert "fetchJson(`/api/profile/summary?${params.toString()}`" in javascript
    assert "function syncClassificationAvailability()" in javascript
    assert 'id="major-only"' in html
    assert "Show only courses related to major" in html
    assert 'id="eligible-only"' not in html
    assert 'eligible_only' not in javascript
    assert 'params.set("major_only", String(document.querySelector("#major-only").checked))' in javascript
    assert "Computer Science, B.S." not in javascript
    assert ".catalog-status-loaded" in css
    assert ".program-summary" in css
    assert "styles.css?v=60" in html
    assert "app.js?v=62" in html
    assert "required courses completed:" in javascript
    assert "summary.completed_required_course_count" in javascript
    assert "summary.required_course_count" in javascript
    assert "mapped courses entered as complete" not in javascript
    assert "program-summary-note" not in html


def test_requirement_filters_use_compact_order_without_duplicate_ge_umbrellas() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    expected = [
        "GWAR",
        "American Institutions",
        "Ethnic Studies",
        "Cultural Diversity",
        "1A English Composition",
        "1B Critical Thinking",
        "1C Oral Communication",
        "2 Mathematical/Quantitative Reasoning",
        "3A Arts",
        "3B Humanities",
        "4 Social and Behavioral Sciences",
        "5A Physical Science",
        "5B Biological Science",
        "5C Laboratory",
        "EXPLORATIONS - PHYS, BIO, MATH/QUANT",
        "EXPLORATIONS - ARTS & HUMANITIES",
        "EXPLORATIONS - SOCIAL & BEHAVIORAL SCIENCES",
    ]
    positions = [javascript.index(f'"{label}"') for label in expected]
    assert positions == sorted(positions)
    assert "GE 4: Social and Behavioral Sciences (6 units)" not in javascript
    assert "GE 2: or 5." not in javascript
    assert "GE 3: Arts and Humanities" not in javascript


def test_location_filter_and_component_layout_are_present() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert html.index('<summary>Location</summary>') < html.index('<summary>Seat availability</summary>')
    assert 'id="campuses"' in html
    assert '{ checked: value === "San Diego Campus" }' in javascript
    assert 'addRepeated(params, "campus", checkedValues("campus"))' in javascript
    assert 'function meetingLocationText(meetings, fallbackLocation = null)' in javascript
    assert 'function componentRows(section)' in javascript
    assert 'Class #' in javascript
    assert '.component-table' in css
    assert '.component-row' in css
    assert 'Class components' in javascript
    assert '.shared-course-info' in css
    assert 'Shared course information' in javascript
    grouped_start = javascript.index('const groupedSharedInfo = `')
    grouped_end = javascript.index('const singleGrid = `', grouped_start)
    grouped_shared = javascript[grouped_start:grouped_end]
    assert 'Program classification' not in grouped_shared
    assert 'Requirements' not in grouped_shared
    assert 'function componentProfessorSummary(component)' in javascript
    assert 'No RateMyProfessors match' in javascript
    assert '<div role="columnheader">Professor</div>' in javascript
    assert 'grouped-badges' in javascript

def test_major_and_completed_courses_persist_in_first_party_cookies() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'const majorProgramCookieName = "classcatalog_major_program";' in javascript
    assert "const majorProgramCookieMaxAgeSeconds = 60 * 60 * 24 * 365;" in javascript
    assert 'const completedCoursesCookieName = "classcatalog_completed_courses";' in javascript
    assert "const completedCoursesCookieMaxAgeSeconds = 60 * 60 * 24 * 365;" in javascript
    assert "document.cookie" in javascript
    assert "SameSite=Lax" in javascript
    assert "Path=/" in javascript
    assert 'window.location.protocol === "https:"' in javascript
    assert 'cookie += "; Secure"' in javascript
    assert "savedMajorProgram()" in javascript
    assert "persistMajorProgram(selectedProgram);" in javascript
    assert 'persistMajorProgram("");' in javascript
    assert "restoreCompletedCourses();" in javascript
    assert "persistCompletedCourses(hidden.value);" in javascript
    assert 'persistCompletedCourses("");' in javascript
    assert "clearLegacyProfileCookies" not in javascript
    assert "app.js?v=62" in html


def test_theme_persists_in_first_party_cookie() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'const themeCookieName = "classcatalog_theme";' in javascript
    assert "const themeCookieMaxAgeSeconds = 60 * 60 * 24 * 365;" in javascript
    assert "readCookie(themeCookieName)" in javascript
    assert "writeCookie(themeCookieName, normalizeTheme(themeId), themeCookieMaxAgeSeconds);" in javascript
    assert 'window.localStorage.setItem(legacyThemeStorageKey' not in javascript
    assert 'const cookieName = "classcatalog_theme";' in html
    assert "SameSite=Lax" in html
    assert "Path=/" in html
    assert 'window.location.protocol === "https:"' in html
    assert 'cookie += "; Secure"' in html
    assert "themecookie=1" in html


def test_header_comparison_tagline_is_removed() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "Compare sections, schedules, seats, and review-reported professor signals." not in html



def test_rate_my_professors_profile_links_and_metrics_are_rendered() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "function professorProfileLink(instructor, metrics" in javascript
    assert 'target="_blank"' in javascript
    assert 'rel="noopener noreferrer"' in javascript
    assert "RateMyProfessors" in javascript
    assert "Quality" in javascript
    assert "Reviews" in javascript
    assert "Professor Difficulty" in javascript
    assert "Take Again %" in javascript
    assert "metrics.profile_url" in javascript or "metrics?.profile_url" in javascript
    assert "rmp-component-metrics" in javascript
    assert ".professor-profile-link" in css
    assert ".professor-metrics" in css
    assert ".rmp-stat-row" in css
    assert javascript.index("<span>Reviews</span>") < javascript.index("<span>Quality</span>") < javascript.index("<span>Professor Difficulty</span>") < javascript.index("<span>Take Again %</span>")
    assert 'rmpMetricTone(metrics.rating, "quality")' in javascript
    assert 'rmpMetricTone(metrics.difficulty, "difficulty")' in javascript
    assert 'rmpMetricTone(metrics.would_take_again_percent, "take-again")' in javascript
    assert 'if (numeric >= 4) return "good";' in javascript
    assert 'if (numeric >= 2.5) return "mixed";' in javascript
    assert 'if (numeric <= 2) return "good";' in javascript
    assert 'if (numeric <= 3.5) return "mixed";' in javascript
    assert 'if (numeric >= 80) return "good";' in javascript
    assert 'if (numeric >= 50) return "mixed";' in javascript
    assert ".rmp-score.good" in css
    assert ".rmp-score.mixed" in css
    assert ".rmp-score.poor" in css
    assert "styles.css?v=60" in html
    assert "app.js?v=62" in html


def test_class_difficulty_is_not_rendered_in_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "src/classcatalog/static/index.html").read_text(encoding="utf-8")
    javascript = (root / "src/classcatalog/static/app.js").read_text(encoding="utf-8")
    assert "Class Difficulty" not in html
    assert "Class difficulty" not in javascript
    assert "classDifficulty" not in javascript


def test_professor_names_use_larger_typography() -> None:
    root = Path(__file__).resolve().parents[1]
    css = (root / "src/classcatalog/static/styles.css").read_text(encoding="utf-8")
    assert ".professor-name { margin: 0; font-size: 1.08rem;" in css
    assert ".component-professor-name { display: inline-flex; color: #1f2933; font-size: .84rem;" in css


def test_active_filter_summary_collapses_completed_courses_to_one_chip() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'if (key === "completed_course")' in javascript
    assert 'chip.textContent = "completed courses";' in javascript
    assert "completedCoursesChipAdded" in javascript


def test_completed_courses_use_autocomplete_picker() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="completed-course-input"' in html
    assert 'role="combobox"' in html
    assert 'aria-controls="completed-course-suggestions"' in html
    assert 'id="completed-course-suggestions"' in html
    assert 'id="completed-course-chips"' in html
    assert 'id="completed-courses" type="hidden"' in html
    assert "function matchingCompletedCourseOptions(query)" in javascript
    assert "function addCompletedCourse(courseCode)" in javascript
    assert "function removeCompletedCourse(courseCode)" in javascript
    assert "function setupCompletedCoursePicker()" in javascript
    assert "course.course_code" in javascript
    assert "course.title" in javascript
    assert ".completed-course-suggestion" in css
    assert ".completed-course-chip" in css


def test_professor_rating_sort_label_is_professor_quality() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "Professor Quality (Low to High)" in html
    assert "Professor Quality (High to Low)" in html
    assert "Professor Rating (Low to High)" not in html
    assert "Professor Rating (High to Low)" not in html


def test_favorites_navigation_star_and_persistence_are_present() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="favorites-nav"' in html
    assert 'href="#favorites"' in html
    assert 'id="home-nav"' in html
    assert '>Home<' in html.replace("\n", "") or 'Home' in html
    assert 'Back to Classes' not in html
    assert 'id="favorites-page"' in html
    assert 'id="favorites-list"' in html
    assert 'id="favorites-count"' in html
    assert 'class="course-favorite"' in javascript
    assert '<svg class="favorite-star-icon"' in javascript
    assert 'viewBox="0 0 24 24"' in javascript
    assert 'const favoritesStorageKey = "classcatalog_favorites_v1";' in javascript
    assert 'window.localStorage.setItem' in javascript
    assert 'window.localStorage.getItem' in javascript
    assert 'function sectionFavoriteKey(section)' in javascript
    assert 'section.linked_components.map((component) => component.schedule_number)' in javascript
    assert 'function renderFavoritesPage()' in javascript
    assert 'window.addEventListener("hashchange", syncPageFromHash);' in javascript
    assert '.course-favorite.is-favorite' in css
    assert 'color: #e0ac27;' in css
    assert '.course-favorite.is-favorite .favorite-star-icon path' in css
    assert 'fill: #f4c542;' in css
    assert '.header-nav-button' in css


def test_admin_data_health_dashboard_is_private_and_has_login_controls() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="admin-nav" class="header-nav-button" href="#admin" hidden' in html
    assert 'id="admin-page"' in html
    assert 'id="admin-login-form"' in html
    assert 'id="admin-password"' in html
    assert 'id="admin-logout"' in html
    assert 'id="admin-refresh"' in html
    assert 'id="admin-coverage-metrics"' in html
    assert 'id="admin-rmp-metrics"' in html
    assert 'id="admin-completeness-metrics"' in html
    assert 'id="admin-files"' in html
    assert 'id="admin-recent-errors"' in html
    assert 'id="admin-recent-error-count"' in html
    assert 'fetchJson("/api/admin/session", { cache: "no-store" })' in javascript
    assert 'fetchJson("/api/admin/login", {' in javascript
    assert 'fetchJson("/api/admin/logout", { method: "POST", cache: "no-store" })' in javascript
    assert '"/api/admin/health"' in javascript
    assert 'window.location.hash === "#admin"' in javascript
    assert 'function updateAdminAccessUi()' in javascript
    assert 'function renderAdminHealth(data)' in javascript
    assert 'function loadAdminHealth()' in javascript
    assert '.admin-login-card' in css
    assert '.admin-metric-grid' in css
    assert '.admin-status-card' in css
    assert '.admin-error-row' in css
    assert 'styles.css?v=60' in html
    assert 'app.js?v=62' in html


def test_admin_can_manage_manual_professor_matches() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="admin-professor-overrides-title"' in html
    assert 'id="admin-professor-override-form"' in html
    assert 'id="admin-professor-instructor-input"' in html
    assert 'aria-controls="admin-professor-instructor-suggestions"' in html
    assert 'id="admin-professor-instructor-suggestions"' in html
    assert 'id="admin-professor-profile-input"' in html
    assert 'id="admin-professor-override-save"' in html
    assert 'id="admin-professor-overrides"' in html
    assert 'id="admin-professor-override-result"' in html
    assert "function matchingAdminProfessorOptions(query)" in javascript
    assert "function renderAdminProfessorSuggestions()" in javascript
    assert "function handleAdminProfessorKeydown(event)" in javascript
    assert "function saveAdminProfessorOverride(event)" in javascript
    assert "function deleteAdminProfessorOverride(instructorName, button)" in javascript
    assert 'fetchJson("/api/admin/professors/overrides", {' in javascript
    assert 'method: "DELETE"' in javascript
    assert "setupAdminProfessorPicker();" in javascript
    assert ".admin-professor-override-form" in css
    assert ".admin-professor-override-table" in css


def test_live_seat_refresh_ui_and_admin_controls_are_present() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="seat-freshness"' in html
    assert 'id="admin-seat-refresh-now"' in html
    assert 'id="admin-seat-refresh-form"' in html
    assert 'id="admin-seat-course-input"' in html
    assert 'aria-controls="admin-seat-course-suggestions"' in html
    assert 'id="admin-seat-course-suggestions"' in html
    assert 'id="admin-seat-refresh-course"' in html
    assert 'id="admin-seat-refresh-result"' in html
    assert 'id="admin-seat-metrics"' in html
    assert 'id="admin-seat-failures-title"' in html
    assert 'id="admin-seat-failure-count"' in html
    assert 'id="admin-seat-failures"' in html
    assert 'id="admin-seat-failure-result"' in html
    assert "Latest failure" in html
    assert "Occurrences" in html
    assert "function loadSeatRefreshStatus()" in javascript
    assert "function pollSeatData()" in javascript
    assert "function refreshFavoritesSeats()" in javascript
    assert "function seatFreshnessDetails(updatedAt)" in javascript
    assert "function seatFreshnessMarkup(section" in javascript
    assert 'fetchJson("/api/seats/status"' in javascript
    assert 'fetchJson("/api/admin/seats/refresh"' in javascript
    assert 'fetchJson("/api/admin/seats/refresh/course"' in javascript
    assert "function refreshSpecificCourseSeats(event)" in javascript
    assert "function matchingAdminSeatCourseOptions(query)" in javascript
    assert "function renderAdminSeatCourseSuggestions()" in javascript
    assert "function handleAdminSeatCourseKeydown(event)" in javascript
    assert "setupAdminSeatCoursePicker();" in javascript
    assert "function retrySeatFailureCourse(courseCode, button)" in javascript
    assert 'data-retry-course="${escapeHtml(courseCode)}"' in javascript
    assert 'item.resolved ? "Recovered" : "Unresolved"' in javascript
    assert "Seats updated just now" in javascript
    assert "Stale · updated" in javascript
    assert "Live update pending" in javascript
    assert 'if (available != null && available > 0) status = "open";' in javascript
    assert 'seat_updated_at: record.updated_at ?? target.seat_updated_at' in javascript
    assert 'adminMetric("Browser poll"' in javascript
    assert 'adminMetric("Priority cadence"' in javascript
    assert 'adminMetric("Stale warning"' in javascript
    assert ".seat-freshness.is-fresh" in css
    assert ".seat-freshness.is-stale" in css
    assert ".seat-card-freshness" in css
    assert ".component-seat-freshness" in css
    assert ".admin-seat-refresh-form" in css
    assert ".admin-seat-failure-table" in css
    assert ".admin-seat-failure-status.is-unresolved" in css
    assert ".admin-seat-failure-status.is-recovered" in css
    assert "styles.css?v=60" in html
    assert "app.js?v=62" in html


def test_global_api_failures_have_clean_user_messages_and_nonblocking_alerts() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="service-alerts"' in html
    assert "class ApiError extends Error" in javascript
    assert "async function fetchJson(" in javascript
    assert "Seat data is temporarily unavailable." in javascript
    assert "Professor ratings could not be loaded." in javascript
    assert "Class results are temporarily unavailable. Please try again." in javascript
    assert 'fetchJson("/api/ratings/status"' in javascript
    assert 'window.addEventListener("unhandledrejection"' in javascript
    assert ".service-alert" in css


def test_about_page_explains_classcatalog_and_links_to_legal_pages() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'id="about-nav"' in html
    assert 'href="#about"' in html
    assert 'id="about-page"' in html
    assert 'id="about-title"' in html

    about = html[
        html.index('id="about-page"'):
        html.index('id="admin-page"')
    ]

    assert "About ClassCatalog" in about
    assert "What ClassCatalog does" in about
    assert "Where the information comes from" in about
    assert "Before enrolling" in about
    assert "Privacy and terms" in about

    assert "independent planning aid" in about
    assert "not an official SDSU enrollment system" in about
    assert "SDSU public sources" in about
    assert "Rate My Professors" in about
    assert "official SDSU systems" in about

    assert 'href="/privacy">Privacy Policy</a>' in about
    assert 'href="/terms">Terms of Service</a>' in about

    # Privacy-specific details now belong on the separate /privacy page.
    assert "Selected major" not in about
    assert "Completed courses" not in about

    # The redesigned About page is intentionally a simple document,
    # without the previous cards or decorative symbols.
    assert "about-card" not in about
    assert "about-icon" not in about


def test_term_selector_defaults_to_current_semester_without_all_terms() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "function academicTermKey(term)" in javascript
    assert "function currentAcademicTerm(terms, now = new Date())" in javascript
    assert 'month <= 5 ? "Spring" : month <= 7 ? "Summer" : "Fall"' in javascript
    assert 'const currentTerm = `${season} ${year}`;' in javascript
    assert "state.selectedTerm = currentAcademicTerm(terms);" in javascript
    assert 'const allTerms = ["", ...options.terms];' not in javascript
    assert 'button.textContent = term || "All terms";' not in javascript
    assert 'button.textContent = term;' in javascript
    assert "app.js?v=62" in html

