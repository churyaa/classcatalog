ClassCatalog removable active-filter chips patch
================================================

This patch makes the active-filter chips above the results removable, except for the term chip.

Behavior:
- Hover a removable filter chip to reveal a small red X in its upper-right corner.
- Keyboard focus also reveals the X.
- Touch devices keep the X visible because they do not have hover.
- Clicking the X clears only that filter and refreshes the results.
- The collapsed "completed courses" chip clears all completed courses.
- The term chip is NOT removable. Exactly one semester remains selected at all times; change semesters only with the term buttons.
- "major only: false" is no longer shown as an active filter because false is the non-restrictive state.
- Removing a major or catalog year also clears now-invalid classification selections.
- Browser asset cache keys are bumped so the new JS/CSS is fetched after deployment.

The installer supports both states:
- the original ClassCatalog files before the removable-chip patch; or
- a repository where the earlier removable-chip patch was already applied.

Apply from the repository root:

  .\.venv\Scripts\python.exe `
    .\classcatalog-filter-chip-remove-patch\apply_filter_chip_remove_patch.py `
    --repo .

Focused tests:

  .\.venv\Scripts\python.exe -m pytest -q tests\test_ui_features.py

Then run the full suite:

  .\.venv\Scripts\python.exe -m pytest -q

Finally:

  git diff --check
  git status --short

Expected project files changed:
  src/classcatalog/static/app.js
  src/classcatalog/static/styles.css
  src/classcatalog/static/index.html
  tests/test_ui_features.py

The patch does not modify src/classcatalog/repository.py or dataset/search grouping logic.
