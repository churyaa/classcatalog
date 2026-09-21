from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

APP_PATH = Path("src/classcatalog/static/app.js")
CSS_PATH = Path("src/classcatalog/static/styles.css")
HTML_PATH = Path("src/classcatalog/static/index.html")
TEST_PATH = Path("tests/test_ui_features.py")
PROTECTED_PATH = Path("src/classcatalog/repository.py")

OLD_ACTIVE_FILTERS = '''function renderActiveFilters(params) {
  const container = document.querySelector("#active-filters");
  container.replaceChildren();
  const hiddenKeys = ["page", "page_size", "sort_by"];
  const entries = [...params.entries()].filter(([key]) => !hiddenKeys.includes(key));
  let completedCoursesChipAdded = false;

  entries.forEach(([key, value]) => {
    if (key === "completed_course") {
      if (completedCoursesChipAdded) return;
      completedCoursesChipAdded = true;
      const chip = document.createElement("span");
      chip.className = "filter-chip";
      chip.textContent = "completed courses";
      container.appendChild(chip);
      return;
    }

    const chip = document.createElement("span");
    chip.className = "filter-chip";
    const displayKey = key === "exclude_day" ? "exclude day" : key.replaceAll("_", " ");
    chip.textContent = `${displayKey}: ${labels[value] || dayLabels[value] || value}`;
    container.appendChild(chip);
  });
}
'''

PREVIOUS_ACTIVE_FILTERS = '''function removeActiveFilter(key, value) {
  const checkboxKeys = new Set([
    "campus",
    "requirement",
    "grading",
    "classification",
    "instruction_mode",
    "seat_status",
  ]);
  let profileChanged = false;

  if (key === "term") {
    state.selectedTerm = "";
    document.querySelectorAll("#term-buttons [data-term]").forEach((button) => {
      button.setAttribute("aria-pressed", "false");
    });
  } else if (checkboxKeys.has(key)) {
    document.querySelectorAll(`input[name="${key}"]`).forEach((input) => {
      if (input.value === value) input.checked = false;
    });
  } else if (key === "q") {
    document.querySelector("#query").value = "";
  } else if (key === "program") {
    document.querySelector("#program").value = "";
    persistMajorProgram("");
    syncClassificationAvailability();
    profileChanged = true;
  } else if (key === "catalog_year") {
    document.querySelector("#catalog-year").value = "";
    syncClassificationAvailability();
    profileChanged = true;
  } else if (key === "major_only") {
    document.querySelector("#major-only").checked = false;
  } else if (key === "completed_course") {
    setCompletedCourseValues([], { refresh: false });
    closeCompletedCourseSuggestions();
    profileChanged = true;
  } else if (key === "day" || key === "exclude_day") {
    const button = [...document.querySelectorAll("#days .day-toggle")]
      .find((item) => item.dataset.day === value);
    if (button) {
      button.dataset.state = "off";
      const label = button.textContent.trim();
      button.setAttribute("aria-label", `${label}: not filtered. Click to include.`);
    }
  } else {
    const targetIds = {
      units_min: "units-min",
      units_max: "units-max",
      time_from: "time-from",
      time_to: "time-to",
      rating_min: "rating-min",
      difficulty_max: "difficulty-max",
      would_take_again_min: "would-take-again-min",
      reviews_min: "reviews-min",
    };
    const targetId = targetIds[key];
    if (!targetId) return;
    const control = document.querySelector(`#${targetId}`);
    if (control) control.value = "";
  }

  if (profileChanged) scheduleProgramSummary();
  scheduleLoad();
}

function appendActiveFilterChip(container, label, key, value) {
  const chip = document.createElement("span");
  chip.className = "filter-chip";

  const text = document.createElement("span");
  text.className = "filter-chip-label";
  text.textContent = label;

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "filter-chip-remove";
  remove.setAttribute("aria-label", `Remove filter ${label}`);
  remove.title = `Remove ${label}`;
  remove.textContent = "×";
  remove.addEventListener("click", () => removeActiveFilter(key, value));

  chip.append(text, remove);
  container.appendChild(chip);
}

function renderActiveFilters(params) {
  const container = document.querySelector("#active-filters");
  container.replaceChildren();
  const hiddenKeys = ["page", "page_size", "sort_by"];
  const entries = [...params.entries()].filter(([key]) => !hiddenKeys.includes(key));
  let completedCoursesChipAdded = false;

  entries.forEach(([key, value]) => {
    // major_only=false is the non-restrictive state required by the API because its
    // server-side default is true. Keep sending it, but do not present it as an active filter.
    if (key === "major_only" && value === "false") return;

    if (key === "completed_course") {
      if (completedCoursesChipAdded) return;
      completedCoursesChipAdded = true;
      appendActiveFilterChip(container, "completed courses", key, value);
      return;
    }

    const displayKey = key === "exclude_day" ? "exclude day" : key.replaceAll("_", " ");
    const label = `${displayKey}: ${labels[value] || dayLabels[value] || value}`;
    appendActiveFilterChip(container, label, key, value);
  });
}
'''

NEW_ACTIVE_FILTERS = '''function removeActiveFilter(key, value) {
  const checkboxKeys = new Set([
    "campus",
    "requirement",
    "grading",
    "classification",
    "instruction_mode",
    "seat_status",
  ]);
  let profileChanged = false;

  if (checkboxKeys.has(key)) {
    document.querySelectorAll(`input[name="${key}"]`).forEach((input) => {
      if (input.value === value) input.checked = false;
    });
  } else if (key === "q") {
    document.querySelector("#query").value = "";
  } else if (key === "program") {
    document.querySelector("#program").value = "";
    persistMajorProgram("");
    syncClassificationAvailability();
    profileChanged = true;
  } else if (key === "catalog_year") {
    document.querySelector("#catalog-year").value = "";
    syncClassificationAvailability();
    profileChanged = true;
  } else if (key === "major_only") {
    document.querySelector("#major-only").checked = false;
  } else if (key === "completed_course") {
    setCompletedCourseValues([], { refresh: false });
    closeCompletedCourseSuggestions();
    profileChanged = true;
  } else if (key === "day" || key === "exclude_day") {
    const button = [...document.querySelectorAll("#days .day-toggle")]
      .find((item) => item.dataset.day === value);
    if (button) {
      button.dataset.state = "off";
      const label = button.textContent.trim();
      button.setAttribute("aria-label", `${label}: not filtered. Click to include.`);
    }
  } else {
    const targetIds = {
      units_min: "units-min",
      units_max: "units-max",
      time_from: "time-from",
      time_to: "time-to",
      rating_min: "rating-min",
      difficulty_max: "difficulty-max",
      would_take_again_min: "would-take-again-min",
      reviews_min: "reviews-min",
    };
    const targetId = targetIds[key];
    if (!targetId) return;
    const control = document.querySelector(`#${targetId}`);
    if (control) control.value = "";
  }

  if (profileChanged) scheduleProgramSummary();
  scheduleLoad();
}

function appendActiveFilterChip(container, label, key, value, { removable = true } = {}) {
  const chip = document.createElement("span");
  chip.className = "filter-chip";

  const text = document.createElement("span");
  text.className = "filter-chip-label";
  text.textContent = label;
  chip.appendChild(text);

  if (removable) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "filter-chip-remove";
    remove.setAttribute("aria-label", `Remove filter ${label}`);
    remove.title = `Remove ${label}`;
    remove.textContent = "×";
    remove.addEventListener("click", () => removeActiveFilter(key, value));
    chip.appendChild(remove);
  }

  container.appendChild(chip);
}

function renderActiveFilters(params) {
  const container = document.querySelector("#active-filters");
  container.replaceChildren();
  const hiddenKeys = ["page", "page_size", "sort_by"];
  const entries = [...params.entries()].filter(([key]) => !hiddenKeys.includes(key));
  let completedCoursesChipAdded = false;

  entries.forEach(([key, value]) => {
    // major_only=false is the non-restrictive state required by the API because its
    // server-side default is true. Keep sending it, but do not present it as an active filter.
    if (key === "major_only" && value === "false") return;

    // The semester is a required single-choice state. It may be changed with the
    // term buttons, but it cannot be cleared from the active-filter summary.
    if (key === "term") {
      appendActiveFilterChip(container, `term: ${value}`, key, value, { removable: false });
      return;
    }

    if (key === "completed_course") {
      if (completedCoursesChipAdded) return;
      completedCoursesChipAdded = true;
      appendActiveFilterChip(container, "completed courses", key, value);
      return;
    }

    const displayKey = key === "exclude_day" ? "exclude day" : key.replaceAll("_", " ");
    const label = `${displayKey}: ${labels[value] || dayLabels[value] || value}`;
    appendActiveFilterChip(container, label, key, value);
  });
}
'''

OLD_FILTER_CSS = '.filter-chip { padding: .35rem .55rem; border: 1px solid #d5dade; border-radius: 999px; background: rgba(255,255,255,.75); color: #46525d; font-size: .72rem; }'

NEW_FILTER_CSS = '''.filter-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  padding: .35rem .55rem;
  border: 1px solid #d5dade;
  border-radius: 999px;
  background: rgba(255,255,255,.75);
  color: #46525d;
  font-size: .72rem;
}
.filter-chip-remove {
  position: absolute;
  top: -.38rem;
  right: -.38rem;
  display: grid;
  place-items: center;
  width: 1rem;
  height: 1rem;
  padding: 0;
  border: 1px solid rgba(255,255,255,.92);
  border-radius: 999px;
  background: #c62828;
  color: #fff;
  cursor: pointer;
  font: inherit;
  font-size: .78rem;
  font-weight: 900;
  line-height: 1;
  opacity: 0;
  pointer-events: none;
  transform: scale(.82);
  box-shadow: 0 2px 5px rgba(22,32,42,.24);
  transition: opacity .12s ease, transform .12s ease, background .12s ease;
}
.filter-chip:hover .filter-chip-remove,
.filter-chip:focus-within .filter-chip-remove {
  opacity: 1;
  pointer-events: auto;
  transform: scale(1);
}
.filter-chip-remove:hover,
.filter-chip-remove:focus-visible {
  background: #a71919;
  outline: 2px solid color-mix(in srgb, #c62828 35%, transparent);
  outline-offset: 1px;
}
@media (hover: none) {
  .filter-chip-remove {
    opacity: 1;
    pointer-events: auto;
    transform: scale(1);
  }
}'''

OLD_ACTIVE_FILTER_TEST = '''def test_active_filter_summary_collapses_completed_courses_to_one_chip() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'if (key === "completed_course")' in javascript
    assert 'chip.textContent = "completed courses";' in javascript
    assert "completedCoursesChipAdded" in javascript
'''

PREVIOUS_ACTIVE_FILTER_TEST = '''def test_active_filter_summary_collapses_completed_courses_to_one_chip() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'if (key === "completed_course")' in javascript
    assert 'appendActiveFilterChip(container, "completed courses", key, value);' in javascript
    assert "completedCoursesChipAdded" in javascript


def test_active_filter_chips_can_remove_their_filters() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "function removeActiveFilter(key, value)" in javascript
    assert "function appendActiveFilterChip(container, label, key, value)" in javascript
    assert 'remove.className = "filter-chip-remove";' in javascript
    assert 'remove.addEventListener("click", () => removeActiveFilter(key, value));' in javascript
    assert 'if (key === "major_only" && value === "false") return;' in javascript
    assert 'setCompletedCourseValues([], { refresh: false });' in javascript
    assert 'state.selectedTerm = "";' in javascript
    assert '.filter-chip:hover .filter-chip-remove' in css
    assert '.filter-chip:focus-within .filter-chip-remove' in css
    assert "background: #c62828;" in css
    assert "filters=5" in html
    assert "filters=3" in html
'''

NEW_ACTIVE_FILTER_TEST = '''def test_active_filter_summary_collapses_completed_courses_to_one_chip() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'if (key === "completed_course")' in javascript
    assert 'appendActiveFilterChip(container, "completed courses", key, value);' in javascript
    assert "completedCoursesChipAdded" in javascript


def test_active_filter_chips_can_remove_their_filters_except_term() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "function removeActiveFilter(key, value)" in javascript
    assert "function appendActiveFilterChip(container, label, key, value, { removable = true } = {})" in javascript
    assert 'remove.className = "filter-chip-remove";' in javascript
    assert 'remove.addEventListener("click", () => removeActiveFilter(key, value));' in javascript
    assert 'if (key === "major_only" && value === "false") return;' in javascript
    assert 'setCompletedCourseValues([], { refresh: false });' in javascript
    assert 'if (key === "term") {' in javascript
    assert 'appendActiveFilterChip(container, `term: ${value}`, key, value, { removable: false });' in javascript
    assert 'state.selectedTerm = "";' not in javascript
    assert '.filter-chip:hover .filter-chip-remove' in css
    assert '.filter-chip:focus-within .filter-chip-remove' in css
    assert "background: #c62828;" in css
    assert "filters=5" in html
    assert "filters=4" in html
'''


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _replace_once(text: str, old: str, new: str, *, label: str) -> tuple[str, bool]:
    if new in text:
        return text, False
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one matching block, found {count}.")
    return text.replace(old, new, 1), True


def apply(repo: Path) -> None:
    repo = repo.resolve()
    protected = repo / PROTECTED_PATH
    protected_before = _sha256(protected)

    changed: list[Path] = []

    app_path = repo / APP_PATH
    app = _read(app_path)
    if NEW_ACTIVE_FILTERS in app:
        did_change = False
    elif PREVIOUS_ACTIVE_FILTERS in app:
        app = app.replace(PREVIOUS_ACTIVE_FILTERS, NEW_ACTIVE_FILTERS, 1)
        did_change = True
    else:
        app, did_change = _replace_once(
            app,
            OLD_ACTIVE_FILTERS,
            NEW_ACTIVE_FILTERS,
            label="active filter chip behavior",
        )
    if did_change:
        _write(app_path, app)
        changed.append(APP_PATH)

    css_path = repo / CSS_PATH
    css = _read(css_path)
    css, did_change = _replace_once(
        css,
        OLD_FILTER_CSS,
        NEW_FILTER_CSS,
        label="active filter chip styling",
    )
    if did_change:
        _write(css_path, css)
        changed.append(CSS_PATH)

    html_path = repo / HTML_PATH
    html = _read(html_path)
    html_changed = False
    if "filters=5" not in html:
        old = 'styles.css?v=60&amp;header=3&amp;filters=4&amp;themes=10'
        new = 'styles.css?v=60&amp;header=3&amp;filters=5&amp;themes=10'
        if old not in html:
            raise RuntimeError("stylesheet cache key: expected filters=4 marker was not found.")
        html = html.replace(old, new, 1)
        html_changed = True
    if "app.js?v=62&amp;filters=4&amp;themes=8" not in html:
        previous = 'app.js?v=62&amp;filters=3&amp;themes=8'
        original = 'app.js?v=62&amp;filters=2&amp;themes=8'
        latest = 'app.js?v=62&amp;filters=4&amp;themes=8'
        if previous in html:
            html = html.replace(previous, latest, 1)
        elif original in html:
            html = html.replace(original, latest, 1)
        else:
            raise RuntimeError("JavaScript cache key: expected filters=2 or filters=3 marker was not found.")
        html_changed = True
    if html_changed:
        _write(html_path, html)
        changed.append(HTML_PATH)

    test_path = repo / TEST_PATH
    tests = _read(test_path)
    if NEW_ACTIVE_FILTER_TEST in tests:
        did_change = False
    elif PREVIOUS_ACTIVE_FILTER_TEST in tests:
        tests = tests.replace(PREVIOUS_ACTIVE_FILTER_TEST, NEW_ACTIVE_FILTER_TEST, 1)
        did_change = True
    else:
        tests, did_change = _replace_once(
            tests,
            OLD_ACTIVE_FILTER_TEST,
            NEW_ACTIVE_FILTER_TEST,
            label="active filter tests",
        )
    if did_change:
        _write(test_path, tests)
        changed.append(TEST_PATH)

    protected_after = _sha256(protected)
    if protected_before != protected_after:
        raise RuntimeError(f"Protected {PROTECTED_PATH} changed unexpectedly.")

    if changed:
        print("ClassCatalog removable active-filter chips patch applied successfully.")
        print(f"Protected {PROTECTED_PATH} was not changed.")
        print("Changed files:")
        for path in changed:
            print(f"  {path.as_posix()}")
    else:
        print("ClassCatalog removable active-filter chips patch is already applied; no files changed.")
        print(f"Protected {PROTECTED_PATH} was not changed.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    apply(args.repo)


if __name__ == "__main__":
    main()
