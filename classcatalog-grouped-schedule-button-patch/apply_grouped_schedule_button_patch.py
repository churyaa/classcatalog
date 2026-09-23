from __future__ import annotations

import argparse
from pathlib import Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one matching block, found {count}.")
    return text.replace(old, new, 1)


def patch_app(path: Path) -> bool:
    text = _read(path)
    if 'class="component-group-toolbar"' in text:
        return False

    old = '''function componentRows(section) {
  const components = section.linked_components || [];
  if (components.length < 2) return "";
  const rows = components.map((component, index) => {
    const seats = componentSeatSummary(component);
    const sectionText = component.section_number ? `Section ${component.section_number}` : "";
    return `
      <div class="component-row" role="row">
        <div class="component-class" role="cell">
          <strong class="component-name">${escapeHtml(component.component || "Class")}</strong>
          <span class="component-class-number">Class #${escapeHtml(component.schedule_number)}</span>
          ${sectionText ? `<span class="component-subvalue">${escapeHtml(sectionText)}</span>` : ""}
        </div>
        <div class="component-cell" role="cell">${escapeHtml(meetingText(component.meetings || []))}</div>
        <div class="component-cell" role="cell">${escapeHtml(meetingLocationText(component.meetings || [], component.location))}</div>
        <div class="component-cell component-professor" role="cell">
          <div class="component-professor-layout${index === 0 ? " with-schedule" : ""}">
            ${index === 0 ? scheduleActionButtonMarkup() : ""}
            <div class="component-professor-copy">${componentProfessorSummary(component)}</div>
          </div>
        </div>
        <div class="component-cell component-seats" role="cell">
          <span class="component-seat ${escapeHtml(seats.status)}">${escapeHtml(seats.primary)}</span>
          ${seats.secondary ? `<span class="component-subvalue">${escapeHtml(seats.secondary)}</span>` : ""}
          ${seatFreshnessMarkup(component, "component-seat-freshness")}
        </div>
      </div>`;
  }).join("");
  return `
    <div class="component-group" aria-label="Class option components">
      <div class="component-group-title">Class components</div>
      <div class="component-table" role="table" aria-label="Classes included in this option">
        <div class="component-header" role="row">
          <div role="columnheader">Class</div>
          <div role="columnheader">Meetings</div>
          <div role="columnheader">Location</div>
          <div role="columnheader">Professor</div>
          <div role="columnheader">Seats</div>
        </div>
        ${rows}
      </div>
    </div>`;
}'''

    new = '''function componentRows(section) {
  const components = section.linked_components || [];
  if (components.length < 2) return "";
  const rows = components.map((component) => {
    const seats = componentSeatSummary(component);
    const sectionText = component.section_number ? `Section ${component.section_number}` : "";
    return `
      <div class="component-row" role="row">
        <div class="component-class" role="cell">
          <strong class="component-name">${escapeHtml(component.component || "Class")}</strong>
          <span class="component-class-number">Class #${escapeHtml(component.schedule_number)}</span>
          ${sectionText ? `<span class="component-subvalue">${escapeHtml(sectionText)}</span>` : ""}
        </div>
        <div class="component-cell" role="cell">${escapeHtml(meetingText(component.meetings || []))}</div>
        <div class="component-cell" role="cell">${escapeHtml(meetingLocationText(component.meetings || [], component.location))}</div>
        <div class="component-cell component-professor" role="cell">
          <div class="component-professor-copy">${componentProfessorSummary(component)}</div>
        </div>
        <div class="component-cell component-seats" role="cell">
          <span class="component-seat ${escapeHtml(seats.status)}">${escapeHtml(seats.primary)}</span>
          ${seats.secondary ? `<span class="component-subvalue">${escapeHtml(seats.secondary)}</span>` : ""}
          ${seatFreshnessMarkup(component, "component-seat-freshness")}
        </div>
      </div>`;
  }).join("");
  return `
    <div class="component-group" aria-label="Class option components">
      <div class="component-group-toolbar">
        <div class="component-group-title">Class components</div>
        ${scheduleActionButtonMarkup()}
      </div>
      <div class="component-table" role="table" aria-label="Classes included in this option">
        <div class="component-header" role="row">
          <div role="columnheader">Class</div>
          <div role="columnheader">Meetings</div>
          <div role="columnheader">Location</div>
          <div role="columnheader">Professor</div>
          <div role="columnheader">Seats</div>
        </div>
        ${rows}
      </div>
    </div>`;
}'''

    _write(path, _replace_once(text, old, new, label="grouped schedule button placement"))
    return True


def patch_css(path: Path, payload_dir: Path) -> bool:
    text = _read(path)
    marker = "/* Grouped schedule action: use the empty area above the component table. */"
    if marker in text:
        return False
    addition = (payload_dir / "grouped_schedule.css.inc").read_text(encoding="utf-8")
    _write(path, text.rstrip() + addition + "\n")
    return True


def patch_index(path: Path) -> bool:
    text = _read(path)
    if "groupedschedule=1" in text:
        return False
    updated = text.replace("&amp;schedulelayout=1", "&amp;schedulelayout=1&amp;groupedschedule=1", 1)
    updated = updated.replace("&amp;schedule=1\" defer", "&amp;schedule=1&amp;groupedschedule=1\" defer", 1)
    if updated == text:
        raise RuntimeError("cache key update: expected schedule cache keys were not found")
    _write(path, updated)
    return True


def patch_tests(path: Path, payload_dir: Path) -> bool:
    text = _read(path)
    changed = False

    old_visual = '    assert \'component-professor-layout${index === 0 ? " with-schedule" : ""}\' in javascript\n'
    new_visual = '    assert \'class="component-group-toolbar"\' in javascript\n'
    if old_visual in text:
        text = text.replace(old_visual, new_visual, 1)
        changed = True

    old_layout = '''    assert ".component-professor-layout.with-schedule {" in css
    assert "grid-template-columns: 1fr;" in css
    assert "schedulelayout=1" in html
'''
    new_layout = '''    assert ".component-group-toolbar {" in css
    assert ".component-group-toolbar > .schedule-add-button {" in css
    assert "schedulelayout=1" in html
'''
    if old_layout in text:
        text = text.replace(old_layout, new_layout, 1)
        changed = True

    marker = "test_grouped_schedule_button_uses_component_header_area"
    if marker not in text:
        addition = (payload_dir / "test_ui_features.inc").read_text(encoding="utf-8")
        text = text.rstrip() + addition + "\n"
        changed = True

    if changed:
        _write(path, text)
    return changed


def apply(repo: Path, patch_root: Path) -> None:
    repo = repo.resolve()
    payload = patch_root / "payload"
    paths = {
        "app": repo / "src/classcatalog/static/app.js",
        "css": repo / "src/classcatalog/static/styles.css",
        "index": repo / "src/classcatalog/static/index.html",
        "tests": repo / "tests/test_ui_features.py",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise RuntimeError("Missing expected ClassCatalog files: " + ", ".join(missing))

    changed = []
    if patch_app(paths["app"]):
        changed.append("src/classcatalog/static/app.js")
    if patch_css(paths["css"], payload):
        changed.append("src/classcatalog/static/styles.css")
    if patch_index(paths["index"]):
        changed.append("src/classcatalog/static/index.html")
    if patch_tests(paths["tests"], payload):
        changed.append("tests/test_ui_features.py")

    if changed:
        print("ClassCatalog grouped schedule button patch applied successfully.")
        print("Changed files:")
        for item in changed:
            print(f"  {item}")
    else:
        print("ClassCatalog grouped schedule button patch is already applied; no changes made.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Move grouped schedule buttons above the component table.")
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    apply(args.repo, Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
