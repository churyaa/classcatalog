from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

TEST_MARKER = "test_schedule_drawer_omits_redundant_calendar_toolbar"
OLD_TOOLBAR = '''        <div class="schedule-calendar-toolbar">\n          <strong>Weekly calendar</strong>\n          <span>Click a class for full details</span>\n        </div>\n'''


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def apply(repo: Path) -> None:
    repo = repo.resolve()
    index = repo / "src/classcatalog/static/index.html"
    tests = repo / "tests/test_ui_features.py"
    app = repo / "src/classcatalog/static/app.js"
    repository = repo / "src/classcatalog/repository.py"

    for path in (index, tests, app, repository):
        if not path.is_file():
            raise RuntimeError(f"Missing expected ClassCatalog file: {path}")

    if 'id="schedule-drawer"' not in _read(index) or 'const scheduleStorageKey = "classcatalog_schedule_v1";' not in _read(app):
        raise RuntimeError("The visual schedule builder patch is not applied yet.")

    protected_before = hashlib.sha256(repository.read_bytes()).hexdigest()
    changed: list[str] = []

    html = _read(index)
    if OLD_TOOLBAR in html:
        _write(index, html.replace(OLD_TOOLBAR, "", 1))
        changed.append("src/classcatalog/static/index.html")
    elif "Weekly calendar" in html or "Click a class for full details" in html or 'class="schedule-calendar-toolbar"' in html:
        raise RuntimeError("Schedule calendar toolbar exists but does not match the expected block; refusing an unsafe edit.")

    test_text = _read(tests)
    if TEST_MARKER not in test_text:
        payload = (Path(__file__).resolve().parent / "payload" / "test_ui_features.inc").read_text(encoding="utf-8")
        _write(tests, test_text + ("" if test_text.endswith("\n") else "\n") + payload)
        changed.append("tests/test_ui_features.py")

    protected_after = hashlib.sha256(repository.read_bytes()).hexdigest()
    if protected_before != protected_after:
        raise RuntimeError("Protected src/classcatalog/repository.py changed unexpectedly.")

    if changed:
        print("ClassCatalog schedule calendar toolbar removed successfully.")
        print("Changed files:")
        for item in changed:
            print(f"  {item}")
    else:
        print("ClassCatalog schedule calendar toolbar is already removed.")
    print("Protected src/classcatalog/repository.py was not changed.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    apply(args.repo)


if __name__ == "__main__":
    main()
