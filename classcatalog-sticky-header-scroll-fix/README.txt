ClassCatalog sticky navigation + schedule close scroll fix
==========================================================

Fixes two browse-page quality-of-life issues:

1. Closing the schedule drawer returns keyboard focus to the Schedule button
   without scrolling the document back to the top.
2. The primary ClassCatalog header remains sticky while scrolling, keeping Home,
   About, Subjects, Schedule, Favorites, and Admin controls available anywhere in
   a long class-results page. The sticky search toolbar is offset beneath it, and
   the desktop filter rail accounts for both sticky heights.

Apply from the repository root:

  .\.venv\Scripts\python.exe `
    .\classcatalog-sticky-header-scroll-fix\apply_sticky_header_scroll_fix.py `
    --repo .

Then run:

  .\.venv\Scripts\python.exe -m pytest -q tests\test_ui_features.py
  .\.venv\Scripts\python.exe -m pytest -q
  git diff --check
  git status --short

The installer is idempotent and does not modify repository.py.
