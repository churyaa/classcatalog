from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

PATCH_ROOT = Path(__file__).resolve().parent
PAYLOAD = PATCH_ROOT / "payload"


def _payload(name: str) -> str:
    return (PAYLOAD / name).read_text(encoding="utf-8")


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one matching block, found {count}.")
    return text.replace(old, new, 1)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _patch_app(path: Path) -> bool:
    text = _read(path)
    if 'const scheduleStorageKey = "classcatalog_schedule_v1";' in text:
        return False

    schedule_js = _payload("schedule.js.inc").rstrip()
    updated = text
    updated = _replace_once(
        updated,
        "  serviceMessages: {},\n};",
        "  serviceMessages: {},\n  scheduleDrawerOpen: false,\n  scheduleDetailKey: null,\n};",
        label="schedule state",
    )

    seat_anchor = """function applySeatRecordsToFavorite(section, records) {
  const primary = applySeatRecord(section, records[seatRecordKey(section)]);
  const linked = (section.linked_components || []).map((component) =>
    applySeatRecord(component, records[seatRecordKey({ ...component, term: section.term })])
  );
  return { ...primary, linked_components: linked };
}

async function refreshFavoritesSeats() {"""
    seat_replacement = seat_anchor.replace(
        "\n\nasync function refreshFavoritesSeats() {",
        "\n\n" + schedule_js + "\n\nasync function refreshFavoritesSeats() {",
    )
    updated = _replace_once(
        updated,
        seat_anchor,
        seat_replacement,
        label="schedule implementation insertion",
    )

    old_poll = """async function pollSeatData() {
  await loadSeatRefreshStatus();
  if (window.location.hash === "#favorites") {
    await refreshFavoritesSeats();
    return;
  }
  if (!window.location.hash || window.location.hash === "#") {
    await loadCourses({ page: state.page, quiet: true });
  }
}"""
    new_poll = """async function pollSeatData() {
  await loadSeatRefreshStatus();
  if (state.scheduleDrawerOpen) await refreshScheduleSeats();
  if (window.location.hash === "#favorites") {
    await refreshFavoritesSeats();
    return;
  }
  if (!window.location.hash || window.location.hash === "#") {
    await loadCourses({ page: state.page, quiet: true });
  }
}"""
    updated = _replace_once(updated, old_poll, new_poll, label="schedule seat polling")

    old_term = """    button.addEventListener("click", () => {
      state.selectedTerm = term;
      [...termContainer.children].forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.term === term)));
      scheduleLoad();
    });"""
    new_term = """    button.addEventListener("click", () => {
      state.selectedTerm = term;
      [...termContainer.children].forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.term === term)));
      state.scheduleDetailKey = null;
      updateScheduleCount();
      syncScheduleButtons();
      if (state.scheduleDrawerOpen) renderScheduleDrawer();
      scheduleLoad();
    });"""
    updated = _replace_once(updated, old_term, new_term, label="term-aware schedule refresh")

    updated = _replace_once(
        updated,
        "  const rows = components.map((component) => {",
        "  const rows = components.map((component, index) => {",
        label="grouped schedule row index",
    )
    updated = _replace_once(
        updated,
        '        <div class="component-cell component-professor" role="cell">${componentProfessorSummary(component)}</div>',
        """        <div class="component-cell component-professor" role="cell">
          <div class="component-professor-layout${index === 0 ? " with-schedule" : ""}">
            ${index === 0 ? scheduleActionButtonMarkup() : ""}
            <div class="component-professor-copy">${componentProfessorSummary(component)}</div>
          </div>
        </div>""",
        label="grouped schedule action",
    )

    updated = _replace_once(
        updated,
        "function courseCard(section) {",
        'function courseCard(section, { tooltipPrefix = "course-description" } = {}) {',
        label="course card detail prefix",
    )
    updated = _replace_once(
        updated,
        '  const tooltipId = `course-description-${String(section.id).replace(/[^a-zA-Z0-9_-]/g, "-")}`;',
        '  const tooltipId = `${tooltipPrefix}-${String(section.id).replace(/[^a-zA-Z0-9_-]/g, "-")}`;',
        label="schedule detail tooltip ids",
    )
    updated = _replace_once(
        updated,
        '    ${grouped ? "" : professorPanel(section)}',
        '    ${grouped ? "" : `<div class="course-side-panel">${scheduleActionButtonMarkup()}${professorPanel(section)}</div>`}',
        label="single-section schedule action",
    )

    old_favorite_end = """  if (favoriteButton) {
    favoriteButton.dataset.favoriteKey = sectionFavoriteKey(section);
    setFavoriteButtonState(favoriteButton, section);
    favoriteButton.addEventListener("click", () => toggleFavorite(section));
  }
  return card;"""
    new_favorite_end = """  if (favoriteButton) {
    favoriteButton.dataset.favoriteKey = sectionFavoriteKey(section);
    setFavoriteButtonState(favoriteButton, section);
    favoriteButton.addEventListener("click", () => toggleFavorite(section));
  }
  wireScheduleButtons(card, section);
  return card;"""
    updated = _replace_once(
        updated,
        old_favorite_end,
        new_favorite_end,
        label="schedule button wiring",
    )

    updated = _replace_once(
        updated,
        "  populateOptions(options);\n  restoreCompletedCourses();",
        "  populateOptions(options);\n  setupScheduleDrawer();\n  restoreCompletedCourses();",
        label="schedule drawer boot",
    )

    _write(path, updated)
    return True


def _patch_index(path: Path) -> bool:
    text = _read(path)
    updated = text
    changed = False

    if 'id="schedule-nav"' not in updated:
        old_nav = '        </a>        <a id="favorites-nav" class="header-nav-button" href="#favorites" aria-label="Favorites" title="Favorites">'
        nav = _payload("schedule_nav.html.inc").rstrip("\n")
        updated = _replace_once(updated, old_nav, nav, label="schedule header button")
        changed = True

    if 'id="schedule-drawer"' not in updated:
        old_drawer = '  <div id="service-alerts" class="service-alerts" role="status" aria-live="polite" hidden></div>\n\n  <main id="browse-page">'
        drawer = _payload("schedule_drawer.html.inc").rstrip("\n")
        updated = _replace_once(updated, old_drawer, drawer, label="schedule drawer markup")
        changed = True

    if "&amp;schedule=1" not in updated:
        updated = _replace_once(
            updated,
            "/static/styles.css?v=60&amp;header=3&amp;filters=5&amp;themes=10&amp;brandalign=2&amp;admintheme=1&amp;themrail=1&amp;library=3&amp;filterpopout=1&amp;daystates=1&amp;brandlogo=1",
            "/static/styles.css?v=60&amp;header=3&amp;filters=5&amp;themes=10&amp;brandalign=2&amp;admintheme=1&amp;themrail=1&amp;library=3&amp;filterpopout=1&amp;daystates=1&amp;brandlogo=1&amp;schedule=1",
            label="schedule CSS cache key",
        )
        updated = _replace_once(
            updated,
            "/static/app.js?v=62&amp;filters=4&amp;themes=8&amp;library=4&amp;filterpopout=1&amp;daystates=1&amp;themecookie=1&amp;branding=1&amp;branding=1&amp;instructorrefresh=1",
            "/static/app.js?v=62&amp;filters=4&amp;themes=8&amp;library=4&amp;filterpopout=1&amp;daystates=1&amp;themecookie=1&amp;branding=1&amp;branding=1&amp;instructorrefresh=1&amp;schedule=1",
            label="schedule JS cache key",
        )
        changed = True

    if changed:
        _write(path, updated)
    return changed


def _patch_css(path: Path) -> bool:
    text = _read(path)
    marker = "/* Visual schedule builder ------------------------------------------------- */"
    if marker in text:
        return False
    css = _payload("schedule.css.inc").rstrip()
    separator = "" if text.endswith("\n") else "\n"
    _write(path, text + separator + "\n" + css + "\n")
    return True


def _patch_tests(path: Path) -> bool:
    text = _read(path)
    marker = "test_visual_schedule_builder_drawer_and_overlap_controls_are_present"
    if marker in text:
        return False
    test = _payload("test_ui_features.inc")
    separator = "" if text.endswith("\n") else "\n"
    _write(path, text + separator + test)
    return True


def apply(repo: Path) -> None:
    repo = repo.resolve()
    paths = {
        "app": repo / "src/classcatalog/static/app.js",
        "css": repo / "src/classcatalog/static/styles.css",
        "index": repo / "src/classcatalog/static/index.html",
        "tests": repo / "tests/test_ui_features.py",
        "repository": repo / "src/classcatalog/repository.py",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise RuntimeError("Missing expected ClassCatalog files: " + ", ".join(missing))

    protected_before = hashlib.sha256(paths["repository"].read_bytes()).hexdigest()
    changed: list[str] = []
    if _patch_app(paths["app"]):
        changed.append("src/classcatalog/static/app.js")
    if _patch_css(paths["css"]):
        changed.append("src/classcatalog/static/styles.css")
    if _patch_index(paths["index"]):
        changed.append("src/classcatalog/static/index.html")
    if _patch_tests(paths["tests"]):
        changed.append("tests/test_ui_features.py")

    protected_after = hashlib.sha256(paths["repository"].read_bytes()).hexdigest()
    if protected_before != protected_after:
        raise RuntimeError("Protected src/classcatalog/repository.py changed unexpectedly.")

    if changed:
        print("ClassCatalog visual schedule builder patch applied successfully.")
        print("Changed files:")
        for item in changed:
            print(f"  {item}")
    else:
        print("ClassCatalog visual schedule builder patch is already applied; no changes made.")
    print("Protected src/classcatalog/repository.py was not changed.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply the ClassCatalog visual schedule builder patch."
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    apply(args.repo)


if __name__ == "__main__":
    main()
