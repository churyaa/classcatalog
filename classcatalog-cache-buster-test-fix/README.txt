ClassCatalog Cache-Buster Test Fix
==================================

Why this patch exists
---------------------
The UI tests expect the asset URLs to retain these prefixes:

  /static/styles.css?v=...
  /static/app.js?v=...

Recent patches prepended cache-buster parameters such as "inboxops=1" or
"bellright=1" before the version token. The assets still worked in the browser,
but exact test assertions no longer found "styles.css?v=60" and "app.js?v=62".

This patch:
1. Reorders the existing query parameters in index.html so "v=..." is first.
2. Keeps all other query parameters, including inboxops/bellright.
3. Updates the admin Inbox patch script, when present, so future applications
   append inboxops=1 instead of prepending it.
4. Is safe to run more than once.

Apply
-----
From the ClassCatalog repository root:

  Expand-Archive `
    -Path .\classcatalog-cache-buster-test-fix.zip `
    -DestinationPath .\classcatalog-cache-buster-test-fix `
    -Force

  powershell -ExecutionPolicy Bypass `
    -File .\classcatalog-cache-buster-test-fix\apply.ps1 `
    -Repo .

Focused test:

  .\.venv\Scripts\python.exe -m pytest -q tests\test_ui_features.py

Full test suite:

  .\.venv\Scripts\python.exe -m pytest -q

Review:

  git diff -- src/classcatalog/static/index.html classcatalog-admin-inbox-ops-patch/apply_admin_inbox_ops_patch.py
