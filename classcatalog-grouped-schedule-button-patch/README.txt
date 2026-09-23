ClassCatalog grouped schedule button placement patch
====================================================

Moves the Add to Schedule action for multi-component class options out of the
first professor cell and into the blank header area above the component table.
Single-section cards are unchanged.

Apply from the repository root:

  .\.venv\Scripts\python.exe `
    .\classcatalog-grouped-schedule-button-patch\apply_grouped_schedule_button_patch.py `
    --repo .

Then run:

  .\.venv\Scripts\python.exe -m pytest -q tests\test_ui_features.py
  .\.venv\Scripts\python.exe -m pytest -q
  git diff --check
  git status --short

Do not commit the extracted patch directory.
