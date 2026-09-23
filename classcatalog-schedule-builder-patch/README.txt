ClassCatalog visual schedule builder patch
==========================================

Adds a theme-aware right-side visual schedule drawer to the front page.

Behavior
--------
- Schedule icon/count in the primary header using the supplied calendar/pen SVG path.
- Schedule entries persist locally in classcatalog_schedule_v1.
- Schedule is scoped to the currently selected term; stored schedules for other terms are preserved.
- Single-section cards show Add to Schedule immediately left of the professor panel.
- Grouped options show Add to Schedule left of the first component professor. Adding a grouped option adds all linked components.
- Exact time/day conflicts (and overlapping meeting-date ranges when available) disable the button with `Overlaps with COURSE_CODE`.
- Back-to-back classes are allowed. TBA/asynchronous meetings do not block additions.
- Clicking a calendar block opens the same full course card details and a Remove from Schedule button.
- Timed meetings render on the interactive weekly calendar; asynchronous/TBA components are listed below it.
- Seat data for scheduled classes refreshes while the drawer is open.
- All drawer/calendar surfaces use ClassCatalog theme variables.
- repository.py and grouping/search semantics are not modified.

Apply
-----
From the classcatalog-starter repository root:

  .\.venv\Scripts\python.exe `
    .\classcatalog-schedule-builder-patch\apply_schedule_builder_patch.py `
    --repo .

Focused test:

  .\.venv\Scripts\python.exe -m pytest -q tests\test_ui_features.py

Full suite:

  .\.venv\Scripts\python.exe -m pytest -q

Then:

  git diff --check
  git status --short

The extracted classcatalog-schedule-builder-patch directory is only the installer and should not be committed.
