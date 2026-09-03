# Frontend validation against production-sized data

The frontend validator uses Playwright to exercise the real FastAPI routes and browser UI. It can
start a temporary Uvicorn process automatically, so the normal API run configuration does not
need to be running.

## Run it

Create an IntelliJ Python configuration with module:

```text
classcatalog.frontend_validation.main
```

Program parameters for the Fall 2026 production data:

```text
--data src\classcatalog\data\sections.json --catalog-data src\classcatalog\data\catalog_mappings.json --output-dir results\fall-2026-frontend-validation --expected-sections 7035 --expected-courses 2955 --expected-physical-sections 7024 --expected-active-subjects 121
```

Equivalent command:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.frontend_validation.main `
  --data src\classcatalog\data\sections.json `
  --catalog-data src\classcatalog\data\catalog_mappings.json `
  --output-dir results\fall-2026-frontend-validation `
  --expected-sections 7035 `
  --expected-courses 2955 `
  --expected-physical-sections 7024 `
  --expected-active-subjects 121
```

Add `--headed` to watch the browser.

When Playwright's managed Chromium is unavailable but Chromium is already installed, pass its
executable explicitly, for example:

```text
--chromium-executable C:\Program Files\Google\Chrome\Application\chrome.exe
```


## What it verifies

- health counts match the normalized dataset;
- the first page renders at most 50 cards;
- `CS 15` behaves as a course-code prefix without unrelated description matches;
- Starts after and Ends before Reset controls are visible and functional;
- the desktop filter panel remains sticky and independently scrollable;
- top and bottom pagination stay synchronized;
- catalog status, program, year, classification, requirement, and profile controls work when a
  catalog overlay is installed;
- browser console and uncaught page errors remain empty.

## Output

```text
results\fall-2026-frontend-validation\
├── frontend-validation-report.json
├── frontend-validation-report.md
└── classcatalog-production.png
```

A missing catalog overlay does not make the base schedule UI fail; catalog-specific checks are
reported as skipped. For production release validation, install the overlay first so those checks
must pass.

The validator can target an already-running deployment with:

```text
--base-url http://127.0.0.1:8000
```
