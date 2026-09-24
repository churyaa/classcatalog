ClassCatalog Admin Inventory + Inbox patch
==========================================

Adds:
- Public Inbox icon/count and theme-aware right-side announcement drawer.
- Browser-local unread state; opening Inbox marks currently visible announcements read.
- Admin announcement composer and deletion controls.
- JSON-backed announcements stored beside sections.json by default in production.
- Admin operations overview: latest published scrape timestamp, incomplete-subject status,
  term coverage, section-count change, new/removed subjects, stale seat records, and
  professor matching failures.
- Backend and UI regression tests.

Apply from the repository root:

  .\.venv\Scripts\python.exe `
    .\classcatalog-admin-inbox-ops-patch\apply_admin_inbox_ops_patch.py `
    --repo .

Focused tests:

  .\.venv\Scripts\python.exe -m pytest -q `
    tests\test_announcements.py `
    tests\test_admin_dashboard.py `
    tests\test_ui_features.py

Then run the complete suite.
