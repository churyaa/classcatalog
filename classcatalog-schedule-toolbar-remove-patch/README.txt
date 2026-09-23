ClassCatalog schedule calendar toolbar removal

Removes the redundant "Weekly calendar / Click a class for full details" strip from the visual schedule drawer.

Apply from the repository root:

  .\.venv\Scripts\python.exe .\classcatalog-schedule-toolbar-remove-patch\apply_schedule_toolbar_remove_patch.py --repo .

Then run:

  .\.venv\Scripts\python.exe -m pytest -q tests\test_ui_features.py
  .\.venv\Scripts\python.exe -m pytest -q
