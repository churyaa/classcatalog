ClassCatalog Inbox Drawer UX Patch
==================================

Changes
-------
1. Clicking outside the Inbox drawer closes it.
2. Clicking inside the drawer does not close it.
3. Clicking the Inbox bell does not cause an immediate outside-click close.
4. The drawer is reduced to a maximum width of 340px.
5. The original admin Inbox patch payloads are updated too, when present.
6. CSS and JS URLs receive an appended inboxdrawer=1 cache-buster without
   moving the existing v= version token.

Apply from the ClassCatalog repository root
-------------------------------------------
After downloading the ZIP:

  Copy-Item `
    "$HOME\Downloads\classcatalog-inbox-drawer-ux-patch.zip" `
    ".\classcatalog-inbox-drawer-ux-patch.zip"

  Remove-Item `
    .\classcatalog-inbox-drawer-ux-patch `
    -Recurse `
    -Force `
    -ErrorAction SilentlyContinue

  Expand-Archive `
    -Path .\classcatalog-inbox-drawer-ux-patch.zip `
    -DestinationPath .\classcatalog-inbox-drawer-ux-patch `
    -Force

  powershell -ExecutionPolicy Bypass `
    -File .\classcatalog-inbox-drawer-ux-patch\apply.ps1 `
    -Repo .

Review
------
  git diff -- `
    src/classcatalog/static/app.js `
    src/classcatalog/static/styles.css `
    src/classcatalog/static/index.html `
    classcatalog-admin-inbox-ops-patch/payload/inbox.js.inc `
    classcatalog-admin-inbox-ops-patch/payload/inbox_admin.css.inc

Test
----
Focused UI tests:

  .\.venv\Scripts\python.exe -m pytest -q tests\test_ui_features.py

Full suite:

  .\.venv\Scripts\python.exe -m pytest -q

Then hard-refresh the browser with Ctrl+Shift+R.
