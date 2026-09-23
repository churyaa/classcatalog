from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

PATCH_ROOT = Path(__file__).resolve().parent
PAYLOAD = PATCH_ROOT / "payload"
CSS_MARKER = "/* Visual schedule builder: preserve professor panel width ---------------- */"
TEST_MARKER = "test_schedule_button_does_not_compress_professor_panel"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _append_once(path: Path, payload_name: str, marker: str) -> bool:
    text = _read(path)
    if marker in text:
        return False
    payload = (PAYLOAD / payload_name).read_text(encoding="utf-8")
    separator = "" if text.endswith("\n") else "\n"
    _write(path, text + separator + payload)
    return True


def _patch_index(path: Path) -> bool:
    text = _read(path)
    if "schedulelayout=1" in text:
        return False

    lines = text.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if "/static/styles.css" in line and "schedule=1" in line]
    if len(matches) != 1:
        raise RuntimeError(
            "schedule CSS cache key: expected one styles.css line containing schedule=1, "
            f"found {len(matches)}. Apply the visual schedule builder patch first."
        )
    i = matches[0]
    line = lines[i]
    if "&amp;schedule=1" in line:
        lines[i] = line.replace("&amp;schedule=1", "&amp;schedule=1&amp;schedulelayout=1", 1)
    elif "&schedule=1" in line:
        lines[i] = line.replace("&schedule=1", "&schedule=1&schedulelayout=1", 1)
    else:
        raise RuntimeError("schedule CSS cache key was not in a recognized form.")
    _write(path, "".join(lines))
    return True


def apply(repo: Path) -> None:
    repo = repo.resolve()
    css = repo / "src/classcatalog/static/styles.css"
    index = repo / "src/classcatalog/static/index.html"
    tests = repo / "tests/test_ui_features.py"
    repository = repo / "src/classcatalog/repository.py"
    app = repo / "src/classcatalog/static/app.js"

    for path in (css, index, tests, repository, app):
        if not path.is_file():
            raise RuntimeError(f"Missing expected ClassCatalog file: {path}")

    if 'const scheduleStorageKey = "classcatalog_schedule_v1";' not in _read(app):
        raise RuntimeError("The visual schedule builder patch is not applied yet.")

    protected_before = hashlib.sha256(repository.read_bytes()).hexdigest()
    changed: list[str] = []

    if _append_once(css, "layout.css.inc", CSS_MARKER):
        changed.append("src/classcatalog/static/styles.css")
    if _patch_index(index):
        changed.append("src/classcatalog/static/index.html")
    if _append_once(tests, "test_ui_features.inc", TEST_MARKER):
        changed.append("tests/test_ui_features.py")

    protected_after = hashlib.sha256(repository.read_bytes()).hexdigest()
    if protected_before != protected_after:
        raise RuntimeError("Protected src/classcatalog/repository.py changed unexpectedly.")

    if changed:
        print("ClassCatalog schedule/professor layout fix applied successfully.")
        print("Changed files:")
        for item in changed:
            print(f"  {item}")
    else:
        print("ClassCatalog schedule/professor layout fix is already applied.")
    print("Protected src/classcatalog/repository.py was not changed.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    apply(args.repo)


if __name__ == "__main__":
    main()
