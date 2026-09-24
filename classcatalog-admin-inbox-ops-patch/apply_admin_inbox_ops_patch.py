from __future__ import annotations

import argparse
from pathlib import Path

PATCH_ROOT = Path(__file__).resolve().parent
PAYLOAD = PATCH_ROOT / "payload"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _payload(name: str) -> str:
    return (PAYLOAD / name).read_text(encoding="utf-8")


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one matching block, found {count}.")
    return text.replace(old, new, 1)


def _patch_main(path: Path) -> bool:
    text = _read(path)
    if 'class AdminAnnouncementRequest(BaseModel):' in text:
        return False
    updated = text

    updated = _replace_once(
        updated,
        "from classcatalog.catalog.models import (",
        "from classcatalog.announcements import AnnouncementStore\nfrom classcatalog.catalog.models import (",
        label="announcement import",
    )
    updated = _replace_once(
        updated,
        'ADMIN_LOGIN_MAX_FAILURES = 5\n',
        'ADMIN_LOGIN_MAX_FAILURES = 5\nANNOUNCEMENTS_PATH_ENV = "CLASSCATALOG_ANNOUNCEMENTS_PATH"\n',
        label="announcement environment constant",
    )
    updated = _replace_once(
        updated,
        "class SeatInterestRequest(BaseModel):\n    schedule_numbers: list[str]\n",
        "class AdminAnnouncementRequest(BaseModel):\n"
        "    title: str = Field(min_length=1, max_length=120)\n"
        "    message: str = Field(min_length=1, max_length=2000)\n\n\n"
        "class SeatInterestRequest(BaseModel):\n    schedule_numbers: list[str]\n",
        label="announcement request model",
    )

    helper = _payload("main_helpers.inc").rstrip()
    updated = _replace_once(
        updated,
        "\ndef create_app(\n",
        "\n" + helper + "\n\n\ndef create_app(\n",
        label="inventory operations helper",
        )

    updated = _replace_once(
        updated,
        "    ratings_path: Path | None = None,\n    admin_password: str | None = None,",
        "    ratings_path: Path | None = None,\n    announcements_path: Path | None = None,\n    admin_password: str | None = None,",
        label="create_app announcements argument",
    )

    anchor = """    active_ratings_path = ratings_path if ratings_path is not None else ACTIVE_RATINGS_PATH
    ratings_editor = (
        RatingsCacheEditor(active_ratings_path)
        if active_ratings_path is not None
        else None
    )
"""
    replacement = anchor + """    configured_announcements_path = os.getenv(ANNOUNCEMENTS_PATH_ENV, "").strip()
    active_announcements_path = (
        announcements_path
        if announcements_path is not None
        else Path(configured_announcements_path).expanduser()
        if configured_announcements_path
        else active_data_path.with_name("announcements.json")
    )
    announcement_store = AnnouncementStore(active_announcements_path)
"""
    updated = _replace_once(updated, anchor, replacement, label="announcement store setup")

    updated = _replace_once(
        updated,
        '        "inventory": {\n            "course_section_listings": repository.total,',
        '        "operations": _inventory_operations_snapshot(repository, data_path=data_path),\n'
        '        "inventory": {\n            "course_section_listings": repository.total,',
        label="admin operations snapshot",
    )
    updated = _replace_once(
        updated,
        '            "ratings": _file_health(ratings_path),\n        },',
        '            "ratings": _file_health(ratings_path),\n'
        '            "announcements": _file_health(data_path.with_name("announcements.json")),\n'
        '        },',
        label="admin announcement file health",
    )

    public_anchor = """    @app.get("/api/admin/session")
    async def admin_session(request: Request, response: Response) -> dict[str, bool]:
"""
    public_block = """    @app.get("/api/announcements")
    async def announcements(response: Response) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return {"messages": announcement_store.list()}

""" + public_anchor
    updated = _replace_once(updated, public_anchor, public_block, label="public announcements endpoint")

    admin_anchor = """    @app.post("/api/admin/logout")
    async def admin_logout(request: Request, response: Response) -> dict[str, bool]:
"""
    admin_block = """    @app.post("/api/admin/announcements")
    async def admin_create_announcement(
        payload: AdminAnnouncementRequest,
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        _require_admin(request)
        response.headers["Cache-Control"] = "no-store"
        record = announcement_store.create(title=payload.title, message=payload.message)
        return {"sent": True, "announcement": record}

    @app.delete("/api/admin/announcements/{message_id}")
    async def admin_delete_announcement(
        message_id: str,
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        _require_admin(request)
        response.headers["Cache-Control"] = "no-store"
        if len(message_id) > 128 or not announcement_store.delete(message_id):
            raise HTTPException(status_code=404, detail="Announcement not found.")
        return {"deleted": True, "id": message_id}

""" + admin_anchor
    updated = _replace_once(updated, admin_anchor, admin_block, label="admin announcements endpoints")

    _write(path, updated)
    return True


def _patch_app(path: Path) -> bool:
    text = _read(path)
    if 'const inboxReadStorageKey = "classcatalog_inbox_read_v1";' in text:
        return False
    updated = text
    updated = _replace_once(
        updated,
        "  scheduleDrawerOpen: false,\n  scheduleDetailKey: null,\n};",
        "  scheduleDrawerOpen: false,\n  scheduleDetailKey: null,\n  inboxDrawerOpen: false,\n  announcements: [],\n};",
        label="inbox state",
    )

    inbox_js = _payload("inbox.js.inc").rstrip()
    updated = _replace_once(
        updated,
        'const scheduleStorageKey = "classcatalog_schedule_v1";',
        inbox_js + '\n\nconst scheduleStorageKey = "classcatalog_schedule_v1";',
        label="inbox implementation",
        )

    open_schedule_anchor = """function openScheduleDrawer() {
  const drawer = document.querySelector("#schedule-drawer");
  const nav = document.querySelector("#schedule-nav");
  if (!drawer) return;
"""
    updated = _replace_once(
        updated,
        open_schedule_anchor,
        open_schedule_anchor + "  if (state.inboxDrawerOpen) closeInboxDrawer();\n",
        label="mutually exclusive drawers",
        )

    admin_ops = _payload("admin_ops.js.inc").rstrip()
    updated = _replace_once(
        updated,
        "function renderAdminHealth(data) {",
        admin_ops + "\n\nfunction renderAdminHealth(data) {",
        label="admin operations renderer",
        )
    health_start = updated.find("function renderAdminHealth(data)")
    if health_start == -1:
        raise RuntimeError(
            "admin operations render call: renderAdminHealth function not found."
        )

    health_end = updated.find("\nfunction ", health_start + 1)
    if health_end == -1:
        health_end = len(updated)

    seats_anchor = "  const seats = data?.seat_refresh || {};"
    health_block = updated[health_start:health_end]
    seats_count = health_block.count(seats_anchor)
    if seats_count != 1:
        raise RuntimeError(
            "admin operations render call: expected exactly one seat refresh anchor "
            f"inside renderAdminHealth, found {seats_count}."
        )

    seats_pos = health_start + health_block.index(seats_anchor)
    admin_render_calls = (
        "  renderAdminOperations(data);\n"
        "  renderAdminAnnouncementHistory();\n\n"
    )

    health_prefix = updated[health_start:seats_pos]
    if "renderAdminOperations(data);" not in health_prefix:
        updated = (
                updated[:seats_pos]
                + admin_render_calls
                + updated[seats_pos:]
        )

    boot_anchor = """  const adminProfessorOverrideForm = document.querySelector("#admin-professor-override-form");
  if (adminProfessorOverrideForm) adminProfessorOverrideForm.addEventListener("submit", saveAdminProfessorOverride);
  setupAdminProfessorPicker();
"""
    boot_replacement = boot_anchor + """  const adminAnnouncementForm = document.querySelector("#admin-announcement-form");
  if (adminAnnouncementForm) adminAnnouncementForm.addEventListener("submit", sendAdminAnnouncement);
"""
    updated = _replace_once(updated, boot_anchor, boot_replacement, label="admin announcement form wiring")
    updated = _replace_once(
        updated,
        "  populateOptions(options);\n  setupScheduleDrawer();",
        "  populateOptions(options);\n  setupScheduleDrawer();\n  setupInboxDrawer();\n  await loadAnnouncements();\n  window.setInterval(loadAnnouncements, 60000);",
        label="inbox boot",
    )

    _write(path, updated)
    return True


def _patch_index(path: Path) -> bool:
    text = _read(path)
    if 'id="inbox-nav"' in text:
        return False
    updated = text

    nav = _payload("inbox_nav.html.inc").rstrip()
    updated = _replace_once(
        updated,
        '        <button id="schedule-nav" class="header-nav-button schedule-nav-button"',
        nav + '\n        <button id="schedule-nav" class="header-nav-button schedule-nav-button"',
        label="inbox navigation button",
        )

    drawer = _payload("inbox_drawer.html.inc").rstrip()
    updated = _replace_once(
        updated,
        "\n  <main id=\"browse-page\">",
        drawer + "\n\n  <main id=\"browse-page\">",
        label="inbox drawer",
        )

    admin_sections = _payload("admin_sections.html.inc").rstrip()
    admin_anchor = """        <section class="admin-section" aria-labelledby="admin-seats-title">
"""
    updated = _replace_once(
        updated,
        admin_anchor,
        admin_sections + "\n\n" + admin_anchor,
        label="admin announcements and operations sections",
        )

    # Cache-bust while preserving the version token immediately after "?".
    def append_asset_cache_token(source: str, asset: str, token: str) -> str:
        marker = f"/static/{asset}?"
        start = source.find(marker)
        if start == -1:
            return source
        end = source.find('"', start)
        if end == -1:
            return source
        segment = source[start:end]
        if token in segment:
            return source
        return source[:end] + f"&amp;{token}" + source[end:]

    updated = append_asset_cache_token(updated, "styles.css", "inboxops=1")
    updated = append_asset_cache_token(updated, "app.js", "inboxops=1")

    _write(path, updated)
    return True


def _patch_css(path: Path) -> bool:
    text = _read(path)
    marker = "/* Inbox and admin inventory intelligence"
    if marker in text:
        return False
    css = _payload("inbox_admin.css.inc").rstrip()
    _write(path, text.rstrip() + "\n" + css + "\n")
    return True


def _patch_env(path: Path) -> bool:
    text = _read(path)
    if "CLASSCATALOG_ANNOUNCEMENTS_PATH=" in text:
        return False
    _write(path, text.rstrip() + "\nCLASSCATALOG_ANNOUNCEMENTS_PATH=\n")
    return True


def _patch_tests_ui(path: Path) -> bool:
    text = _read(path)
    marker = "test_inbox_drawer_and_admin_announcement_ui_are_present"
    if marker in text:
        return False
    addition = r'''


def test_inbox_drawer_and_admin_announcement_ui_are_present() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert 'id="inbox-nav"' in html
    assert 'id="inbox-count" class="header-nav-count"' in html
    assert 'id="inbox-drawer"' in html
    assert 'id="admin-announcement-form"' in html
    assert 'id="admin-operations-metrics"' in html
    assert 'id="admin-term-coverage"' in html
    assert 'id="admin-professor-match-failures"' in html
    assert 'const inboxReadStorageKey = "classcatalog_inbox_read_v1";' in javascript
    assert 'function openInboxDrawer()' in javascript
    assert 'function sendAdminAnnouncement(event)' in javascript
    assert 'function renderAdminOperations(data)' in javascript
    assert 'nav?.focus({ preventScroll: true });' in javascript
    assert '.inbox-drawer {' in css
    assert '.admin-ops-detail-grid {' in css
'''
    _write(path, text.rstrip() + addition + "\n")
    return True


def _write_backend_tests(path: Path) -> bool:
    if path.exists():
        return False
    content = r'''from pathlib import Path

from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


ADMIN_PASSWORD = "announcement-test-password"


def _client(tmp_path: Path) -> TestClient:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    return TestClient(
        create_app(
            repository,
            data_path=SAMPLE_DATA_PATH,
            announcements_path=tmp_path / "announcements.json",
            admin_password=ADMIN_PASSWORD,
            seat_refresh_enabled=False,
        )
    )


def test_announcements_are_public_but_writes_require_admin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/announcements").json() == {"messages": []}

    denied = client.post(
        "/api/admin/announcements",
        json={"title": "Hello", "message": "Registration opens soon."},
    )
    assert denied.status_code == 401

    assert client.post("/api/admin/login", json={"password": ADMIN_PASSWORD}).status_code == 200
    sent = client.post(
        "/api/admin/announcements",
        json={"title": "Hello", "message": "Registration opens soon."},
    )
    assert sent.status_code == 200
    record = sent.json()["announcement"]
    assert record["title"] == "Hello"

    public = client.get("/api/announcements").json()["messages"]
    assert [item["id"] for item in public] == [record["id"]]
    assert public[0]["message"] == "Registration opens soon."

    deleted = client.delete(f"/api/admin/announcements/{record['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/announcements").json() == {"messages": []}


def test_announcement_payload_is_bounded(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/admin/login", json={"password": ADMIN_PASSWORD}).status_code == 200
    response = client.post(
        "/api/admin/announcements",
        json={"title": "x" * 121, "message": "ok"},
    )
    assert response.status_code == 422
'''
    _write(path, content)
    return True


def apply(repo: Path) -> None:
    repo = repo.resolve()
    paths = {
        "main": repo / "src/classcatalog/main.py",
        "app": repo / "src/classcatalog/static/app.js",
        "index": repo / "src/classcatalog/static/index.html",
        "css": repo / "src/classcatalog/static/styles.css",
        "env": repo / ".env.example",
        "ui_tests": repo / "tests/test_ui_features.py",
        "backend_tests": repo / "tests/test_announcements.py",
        "announcements": repo / "src/classcatalog/announcements.py",
    }
    required = [paths[key] for key in ("main", "app", "index", "css", "env", "ui_tests")]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("Missing expected ClassCatalog files: " + ", ".join(missing))

    changed: list[str] = []
    if not paths["announcements"].exists():
        _write(paths["announcements"], _payload("announcements.py"))
        changed.append("src/classcatalog/announcements.py")
    if _patch_main(paths["main"]): changed.append("src/classcatalog/main.py")
    if _patch_app(paths["app"]): changed.append("src/classcatalog/static/app.js")
    if _patch_index(paths["index"]): changed.append("src/classcatalog/static/index.html")
    if _patch_css(paths["css"]): changed.append("src/classcatalog/static/styles.css")
    if _patch_env(paths["env"]): changed.append(".env.example")
    if _patch_tests_ui(paths["ui_tests"]): changed.append("tests/test_ui_features.py")
    if _write_backend_tests(paths["backend_tests"]): changed.append("tests/test_announcements.py")

    if changed:
        print("ClassCatalog admin inventory + Inbox patch applied successfully.")
        print("Changed files:")
        for item in changed:
            print(f"  {item}")
    else:
        print("ClassCatalog admin inventory + Inbox patch is already applied; no changes made.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the ClassCatalog admin inventory and Inbox patch.")
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    apply(args.repo)


if __name__ == "__main__":
    main()
