from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

CSS_MARKER = "/* Sticky primary header + schedule close scroll preservation ---------------- */"
TEST_MARKER = "test_sticky_header_keeps_navigation_available_and_schedule_close_preserves_scroll"
CACHE_MARKER = "scrollnav=1"


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

    old_sync = '''function syncStickyFilterOffset() {
  const toolbar = document.querySelector(".toolbar");
  if (!toolbar) return;
  const toolbarHeight = Math.ceil(toolbar.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--toolbar-sticky-height", `${toolbarHeight}px`);
}'''
    new_sync = '''function syncStickyFilterOffset() {
  const header = document.querySelector(".site-header");
  if (header) {
    const headerHeight = Math.ceil(header.getBoundingClientRect().height);
    document.documentElement.style.setProperty("--site-header-sticky-height", `${headerHeight}px`);
  }

  const toolbar = document.querySelector(".toolbar");
  if (!toolbar) return;
  const toolbarHeight = Math.ceil(toolbar.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--toolbar-sticky-height", `${toolbarHeight}px`);
}'''
    if old_sync in text:
        text = replace_once(text, old_sync, new_sync, label="sticky header height sync")
        changed = True
    elif 'document.documentElement.style.setProperty("--site-header-sticky-height"' not in text:
        raise RuntimeError("sticky header height sync: expected original function was not found.")

    old_focus = '  if (focusToggle) nav?.focus();'
    new_focus = '  if (focusToggle) nav?.focus({ preventScroll: true });'
    if old_focus in text:
        text = replace_once(text, old_focus, new_focus, label="schedule close focus behavior")
        changed = True
    elif 'nav?.focus({ preventScroll: true });' not in text:
        raise RuntimeError("schedule close focus behavior: expected original focus call was not found.")

    if changed:
        write(path, text)
    return changed


def patch_css(path: Path) -> bool:
    text = read(path)
    if CSS_MARKER in text:
        return False

    css = r'''
/* Sticky primary header + schedule close scroll preservation ---------------- */
/* Keep the navigation controls available while browsing long result lists. */
:root {
  --site-header-sticky-height: 4.5rem;
}

.site-header {
  position: sticky;
  top: 0;
  z-index: 65;
  border-bottom: 1px solid color-mix(in srgb, var(--line) 72%, transparent);
  background: color-mix(in srgb, var(--theme-background) 94%, transparent);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}

/* The search toolbar remains sticky immediately below the persistent header. */
.toolbar {
  top: var(--site-header-sticky-height);
}

@media (min-width: 761px) {
  .filters {
    top: calc(var(--site-header-sticky-height) + var(--toolbar-sticky-height) + 1rem);
    max-height: calc(100vh - var(--site-header-sticky-height) - var(--toolbar-sticky-height) - 2rem);
  }
}
'''.strip()

    if not text.endswith("\n"):
        text += "\n"
    text += "\n" + css + "\n"
    write(path, text)
    return True


def patch_index(path: Path) -> bool:
    text = read(path)
    if CACHE_MARKER in text:
        return False

    # Add a cache-busting marker to the main CSS and JS URLs without depending on
    # previous feature markers that may already exist in the working tree.
    css_pattern = re.compile(r'(/static/styles\.css\?[^"\n]+)(")')
    js_pattern = re.compile(r'(/static/app\.js\?[^"\n]+)(")')

    text, css_count = css_pattern.subn(lambda m: m.group(1) + "&amp;scrollnav=1" + m.group(2), text, count=1)
    text, js_count = js_pattern.subn(lambda m: m.group(1) + "&amp;scrollnav=1" + m.group(2), text, count=1)
    if css_count != 1 or js_count != 1:
        raise RuntimeError(
            f"cache key update: expected one styles.css and one app.js URL, found css={css_count} js={js_count}."
        )

    write(path, text)
    return True


def patch_tests(path: Path) -> bool:
    text = read(path)
    if TEST_MARKER in text:
        return False

    test = r'''


def test_sticky_header_keeps_navigation_available_and_schedule_close_preserves_scroll() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'nav?.focus({ preventScroll: true });' in javascript
    assert '--site-header-sticky-height' in javascript
    assert 'document.querySelector(".site-header")' in javascript
    assert "Sticky primary header + schedule close scroll preservation" in css
    assert ".site-header {" in css
    assert "position: sticky;" in css
    assert "z-index: 65;" in css
    assert ".toolbar {" in css
    assert "top: var(--site-header-sticky-height);" in css
    assert "top: calc(var(--site-header-sticky-height) + var(--toolbar-sticky-height) + 1rem);" in css
    assert "scrollnav=1" in html
'''
    if not text.endswith("\n"):
        text += "\n"
    text += test
    write(path, text)
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

    if patch_app(paths["app"]):
        changed.append("src/classcatalog/static/app.js")
    if patch_css(paths["css"]):
        changed.append("src/classcatalog/static/styles.css")
    if patch_index(paths["index"]):
        changed.append("src/classcatalog/static/index.html")
    if patch_tests(paths["tests"]):
        changed.append("tests/test_ui_features.py")

    protected_after = hashlib.sha256(paths["repository"].read_bytes()).hexdigest()
    if protected_before != protected_after:
        raise RuntimeError("Protected src/classcatalog/repository.py changed unexpectedly.")

    if changed:
        print("ClassCatalog sticky navigation / schedule scroll fix applied successfully.")
        print("Changed files:")
        for item in changed:
            print(f"  {item}")
    else:
        print("ClassCatalog sticky navigation / schedule scroll fix is already applied; no changes made.")
    print("Protected src/classcatalog/repository.py was not changed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the ClassCatalog sticky navigation and schedule-close scroll fix.")
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    apply(args.repo)


if __name__ == "__main__":
    main()
