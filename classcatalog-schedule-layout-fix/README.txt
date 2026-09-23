ClassCatalog schedule/professor layout follow-up

This follow-up is intended to be applied after the visual schedule builder patch.
It keeps the Add to Schedule control beside the professor area without squeezing
the RateMyProfessors panel, and stacks the action in narrow grouped-component
professor cells.

From the ClassCatalog repository root:

  .\.venv\Scripts\python.exe `
    .\classcatalog-schedule-layout-fix\apply_schedule_layout_fix.py `
    --repo .

Then run:

  .\.venv\Scripts\python.exe -m pytest -q tests\test_ui_features.py
  .\.venv\Scripts\python.exe -m pytest -q
  git diff --check
  git status --short

Do not commit the extracted classcatalog-schedule-layout-fix directory.
