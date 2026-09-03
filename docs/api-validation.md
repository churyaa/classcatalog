# Validate the production ClassCatalog API dataset

The production API validator loads the installed `sections.json` directly, creates an in-process
FastAPI application around that exact repository, and exercises the same endpoints and query
parameters used by the browser. No SDSU network requests are made and Uvicorn does not need to be
running.

## Fall 2026 command

Run from the inner project directory:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.api_validation.main `
  --data src\classcatalog\data\sections.json `
  --output-dir results\fall-2026-api-validation `
  --expected-sections 7035 `
  --expected-courses 2955 `
  --expected-physical-sections 7024 `
  --expected-subjects 127
```

The expected values make the validation fail if the wrong or truncated data file is active.
Omitting them still runs all behavioral checks.

## Checks

The validator verifies:

- the JSON file loads into unique normalized section IDs;
- health counts match the injected repository;
- the filter-options endpoint matches values actually present in the data;
- the default page size is 50 and the page count is correct;
- every unfiltered page can be traversed with no omissions or duplicate IDs;
- out-of-range pages clamp to the last page and invalid page sizes return HTTP 422;
- an impossible query returns the stable zero-result pagination contract;
- text search matches course code, title, description, and instructor semantics;
- semester, grading, instruction-mode, seat-status, unit-range, day, and time filters work;
- program, catalog-year, and classification filters work independently when program tags exist;
- combined filters agree with direct repository filtering;
- Course A-Z and Z-A are naturally ordered and all professor-related sort values are accepted;
- a broad filtered result set paginates without omissions or duplicates;
- the browser shell and JavaScript expose search plus top and bottom pagination controls.

The current production data does not yet contain normalized program requirement tags, so the
program-filter production check is recorded as skipped rather than failed. Unit tests continue to
cover that behavior.

## Outputs

```text
results/fall-2026-api-validation/
├── api-validation-report.json
└── api-validation-report.md
```

The report includes per-check durations and request latency totals, median, p95, and maximum.
Correctness failures return exit code `1`; missing/invalid command inputs return `2`.

Use `--skip-full-pagination` only for a quick development check. The production verification should
traverse all pages.


## Catalog overlay validation

Pass `--catalog-data src\classcatalog\data\catalog_mappings.json` after collecting the
public SDSU catalog overlay. The validator then exercises the catalog status, program summary,
Student Profile, program classification, and public requirement filters in addition to the
schedule-only checks.
