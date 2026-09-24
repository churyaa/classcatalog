ClassCatalog Inbox Bell Right-Alignment Patch
================================================

Purpose
-------
Moves the Inbox/bell button to the far-right side of the desktop header while
leaving the other navigation buttons grouped together on the left.

Files changed by apply.ps1
--------------------------
- src/classcatalog/static/styles.css
- src/classcatalog/static/index.html
- classcatalog-admin-inbox-ops-patch/payload/inbox_admin.css.inc
  (only if that payload file exists)

The patch is idempotent: running it again should make no additional changes.

Apply from the repository root
------------------------------
PowerShell:

  Expand-Archive -Path .\classcatalog-inbox-bell-right-patch.zip -DestinationPath .\classcatalog-inbox-bell-right-patch -Force

  powershell -ExecutionPolicy Bypass -File .\classcatalog-inbox-bell-right-patch\apply.ps1 -Repo .

Then review:

  git diff -- src/classcatalog/static/styles.css src/classcatalog/static/index.html classcatalog-admin-inbox-ops-patch/payload/inbox_admin.css.inc

Then test:

  .\.venv\Scripts\python.exe -m pytest -q

Finally hard-refresh the browser with Ctrl+Shift+R.
