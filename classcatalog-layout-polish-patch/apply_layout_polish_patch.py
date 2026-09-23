from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

CSS_MARKER = "/* Schedule/course layout polish ------------------------------------------- */"
TEST_MARKER = "test_schedule_layout_polish_keeps_professor_full_height_and_aligns_grouped_action"
CACHE_MARKER = "layoutpolish=1"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one matching block, found {count}.")
    return text.replace(old, new, 1)


def patch_app(path: Path) -> bool:
    text = read(path)
    changed = False

    # The previous grouped-button patch placed the action in the component-group
    # toolbar. Keep that toolbar/title for layout compatibility, but move the
    # actual action to the course heading row.
    old_toolbar = '''      <div class="component-group-toolbar">
        <div class="component-group-title">Class components</div>
        ${scheduleActionButtonMarkup()}
      </div>'''
    new_toolbar = '''      <div class="component-group-toolbar">
        <div class="component-group-title">Class components</div>
      </div>'''
    if old_toolbar in text:
        text = replace_once(text, old_toolbar, new_toolbar, label="remove grouped component-table schedule action")
        changed = True
    elif new_toolbar not in text:
        raise RuntimeError(
            "grouped schedule toolbar not found. Apply the grouped schedule button patch first."
        )

    old_card = '''  card.innerHTML = `
    <div>
      <div class="course-top">
        <div class="course-code-wrap">
          <p class="course-code">${escapeHtml(section.course_code)}</p>
          <button class="course-info" type="button" aria-label="Description for ${escapeHtml(section.course_code)}" aria-describedby="${tooltipId}">
            <span aria-hidden="true">i</span>
            <span id="${tooltipId}" class="course-tooltip" role="tooltip">${escapeHtml(description)}</span>
          </button>
          <button class="course-favorite" type="button" aria-label="Add ${escapeHtml(section.course_code)} ${escapeHtml(section.title)} to favorites" aria-pressed="false">
            <svg class="favorite-star-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M12 2.75l2.77 5.61 6.19.9-4.48 4.37 1.06 6.17L12 16.88 6.46 19.8l1.06-6.17-4.48-4.37 6.19-.9L12 2.75z"></path>
            </svg>
          </button>
        </div>
        <div>
          <h3>${escapeHtml(section.title)}</h3>
          <p class="course-meta">${groupedMeta}</p>
        </div>
      </div>
      ${grouped ? `<div class="badges grouped-badges">${groupedTags.join("")}</div>${componentRows(section)}` : ""}
      ${grouped ? groupedSharedInfo : `<div class="badges">${singleTags.join("")}</div>${singleGrid}`}
    </div>
    ${grouped ? "" : `<div class="course-side-panel">${scheduleActionButtonMarkup()}${professorPanel(section)}</div>`}
  `;'''

    new_card = '''  card.innerHTML = `
    <div>
      <div class="course-card-heading-row${grouped ? " is-grouped" : ""}">
        <div class="course-top">
          <div class="course-code-wrap">
            <p class="course-code">${escapeHtml(section.course_code)}</p>
            <button class="course-info" type="button" aria-label="Description for ${escapeHtml(section.course_code)}" aria-describedby="${tooltipId}">
              <span aria-hidden="true">i</span>
              <span id="${tooltipId}" class="course-tooltip" role="tooltip">${escapeHtml(description)}</span>
            </button>
            <button class="course-favorite" type="button" aria-label="Add ${escapeHtml(section.course_code)} ${escapeHtml(section.title)} to favorites" aria-pressed="false">
              <svg class="favorite-star-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="M12 2.75l2.77 5.61 6.19.9-4.48 4.37 1.06 6.17L12 16.88 6.46 19.8l1.06-6.17-4.48-4.37 6.19-.9L12 2.75z"></path>
              </svg>
            </button>
          </div>
          <div>
            <h3>${escapeHtml(section.title)}</h3>
            <p class="course-meta">${groupedMeta}</p>
          </div>
        </div>
        ${grouped ? `<div class="grouped-schedule-side">${scheduleActionButtonMarkup()}</div>` : ""}
      </div>
      ${grouped ? `<div class="badges grouped-badges">${groupedTags.join("")}</div>${componentRows(section)}` : ""}
      ${grouped ? groupedSharedInfo : `<div class="badges">${singleTags.join("")}</div>${singleGrid}`}
    </div>
    ${grouped ? "" : `<div class="course-side-panel">${scheduleActionButtonMarkup()}${professorPanel(section)}</div>`}
  `;'''

    if old_card in text:
        text = replace_once(text, old_card, new_card, label="grouped schedule heading alignment")
        changed = True
    elif 'class="course-card-heading-row${grouped ? " is-grouped" : ""}"' not in text:
        raise RuntimeError("course card heading markup was not in the expected ClassCatalog form.")

    if changed:
        write(path, text)
    return changed


def patch_css(path: Path, payload: Path) -> bool:
    text = read(path)
    if CSS_MARKER in text:
        return False
    addition = (payload / "layout_polish.css.inc").read_text(encoding="utf-8")
    write(path, text.rstrip() + addition + "\n")
    return True


def patch_index(path: Path) -> bool:
    text = read(path)
    if CACHE_MARKER in text:
        return False

    css_pattern = re.compile(r'(/static/styles\.css\?[^"\n]+)(")')
    js_pattern = re.compile(r'(/static/app\.js\?[^"\n]+)(")')
    text, css_count = css_pattern.subn(lambda m: m.group(1) + "&amp;layoutpolish=1" + m.group(2), text, count=1)
    text, js_count = js_pattern.subn(lambda m: m.group(1) + "&amp;layoutpolish=1" + m.group(2), text, count=1)
    if css_count != 1 or js_count != 1:
        raise RuntimeError(
            f"cache key update: expected one styles.css and one app.js URL, found css={css_count} js={js_count}."
        )
    write(path, text)
    return True


def patch_tests(path: Path, payload: Path) -> bool:
    text = read(path)
    if TEST_MARKER in text:
        return False
    addition = (payload / "test_ui_features.inc").read_text(encoding="utf-8")
    write(path, text.rstrip() + addition + "\n")
    return True


def apply(repo: Path, patch_root: Path) -> None:
    repo = repo.resolve()
    payload = patch_root / "payload"
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

    if patch_app(paths["app"]):
        changed.append("src/classcatalog/static/app.js")
    if patch_css(paths["css"], payload):
        changed.append("src/classcatalog/static/styles.css")
    if patch_index(paths["index"]):
        changed.append("src/classcatalog/static/index.html")
    if patch_tests(paths["tests"], payload):
        changed.append("tests/test_ui_features.py")

    protected_after = hashlib.sha256(paths["repository"].read_bytes()).hexdigest()
    if protected_before != protected_after:
        raise RuntimeError("Protected src/classcatalog/repository.py changed unexpectedly.")

    if changed:
        print("ClassCatalog schedule/course layout polish patch applied successfully.")
        print("Changed files:")
        for item in changed:
            print(f"  {item}")
    else:
        print("ClassCatalog schedule/course layout polish patch is already applied; no changes made.")
    print("Protected src/classcatalog/repository.py was not changed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Polish ClassCatalog professor, grouped schedule, and toolbar layout.")
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    apply(args.repo, Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
