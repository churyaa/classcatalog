ClassCatalog schedule/course layout polish patch
================================================

Fixes three UI issues:
1. Professor panels fill the full height of single-section course cards again.
2. Multi-component Add to Schedule is moved to the card heading and aligned with
   the single-section Add to Schedule button instead of sitting above the table.
3. The sticky search toolbar is narrowed by the same .25rem right inset used by
   the results column so its right border aligns with course-card borders.

Apply from the classcatalog-starter repository root:

  .\.venv\Scripts\python.exe `
    .\classcatalog-layout-polish-patch\apply_layout_polish_patch.py `
    --repo .

Then run:

  .\.venv\Scripts\python.exe -m pytest -q tests\test_ui_features.py
  .\.venv\Scripts\python.exe -m pytest -q
  git diff --check
  git status --short

This patch expects the previously supplied grouped schedule-button patch to be
applied. It does not modify repository.py, dataset grouping, or search semantics.
