# Build the production ClassCatalog dataset

The deep scraper writes one authoritative JSON file per SDSU subject. The dataset builder validates those files, compares their course inventory with an earlier search-only snapshot, counts physical class sections, and converts the scrape records into the JSON schema consumed by the FastAPI repository and browser UI.

## Inputs

A completed production scrape should have:

```text
results/
├── fall-2026-deep-course-stubs.json
└── fall-2026-deep/
    ├── checkpoint.json
    └── subjects/
        ├── a-e.json
        ├── acctg.json
        ├── art.json
        ├── biol.json
        └── ...
```

For exact inventory reconciliation, also keep the completed search-only snapshot, for example:

```text
results/fall-2026-course-stubs-complete-2026-08-20.json
```

The per-subject files are the publication source of truth. The aggregate deep-run file is checked for staleness, but a stale aggregate does not override newer complete subject files.

## Recommended command

Run this from the inner `classcatalog-starter` project directory:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.dataset.main `
  --checkpoint results\fall-2026-deep\checkpoint.json `
  --discovery-run results\fall-2026-course-stubs-complete-2026-08-20.json `
  --output-dir results\fall-2026-production `
  --install-api-data
```

The equivalent installed project command is:

```bash
uv run classcatalog-build-dataset \
  --checkpoint results/fall-2026-deep/checkpoint.json \
  --discovery-run results/fall-2026-course-stubs-complete-2026-08-20.json \
  --output-dir results/fall-2026-production \
  --install-api-data
```

If the discovery snapshot is missing, the build still validates and normalizes the deep data. Inventory comparison is marked `not_compared`; no discovery records are silently invented. If a discovery file is supplied, it must itself be a complete successful run. This prevents an overwritten one-subject test file or an older failed sweep from being treated as the authoritative 2,957-course snapshot.

## Outputs

A passing build writes:

```text
results/fall-2026-production/
├── courses.json
├── sections.json
├── inventory-diff.json
├── validation-report.json
├── validation-report.md
└── manifest.json
```

With `--install-api-data`, the newly built term is installed atomically into:

```text
src/classcatalog/data/sections.json
```

Restart the FastAPI run configuration after installation. `/api/health` should then report the normalized course-section listing count instead of the 13-row sample count.

## Multi-term active data

The production builder still validates one scrape term at a time, but `--install-api-data` is term-aware. Installing Spring 2027 removes any older Spring 2027 records from the active file, inserts the new Spring 2027 build, and preserves Fall 2026 or any other installed terms. Re-running a Spring 2027 scrape therefore replaces that term instead of appending a second copy. Duplicate section IDs and duplicate logical `(term, course_code, class_number)` listings are rejected before the active file is replaced.

Inspect installed terms with:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.dataset.terms list
```

When an old term should leave the active catalog, preview the retirement first:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.dataset.terms retire --term "Fall 2026"
```

The preview does not modify `sections.json`. Apply it only after checking the counts:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.dataset.terms retire --term "Fall 2026" --apply
```

The term manager refuses to remove the final active term by default. Browser favorites are not part of this process; they remain in each user's local storage until the user removes them.

## What is validated

The builder checks:

- every checkpoint subject has a readable per-subject output file;
- all subjects belong to one term, term code, and compatible run;
- subject searches are complete and have no unresolved capped partitions;
- deep detail targets match the complete saved subject inventory;
- every targeted course is complete and has Course Information plus assembled sections;
- option grids are complete;
- course and section unit ranges are valid;
- section course codes match their parent course;
- normalized section IDs do not conflict;
- the aggregate deep-run inventory agrees with the per-subject source of truth;
- the search-only and deep inventories are compared by logical course identity: subject,
  `CRSE_ID`, and academic career. `CRSE_OFFER_NBR` remains in production records but is not used to
  decide that a course was added or removed.

The report also measures coverage for section number, description, component, prerequisites, campus, location, instructor, meetings, timed meetings, seat counts, bookstore URL, textbook signal, and source URL.

Missing optional values are reported as coverage rather than fabricated. In particular, SDSU does not expose a section number for every class option, so the API record uses an empty section number and the UI displays an em dash. Variable-unit courses preserve `units_min`, `units_max`, and `units_text`; the legacy `units` field uses the minimum only as a filter-compatible representative value.

## Inventory reconciliation

`inventory-diff.json` records:

- true courses only in the earlier discovery snapshot;
- true courses only in the completed deep subject outputs;
- same-code/title courses whose PeopleSoft `CRSE_ID` was rekeyed;
- `CRSE_OFFER_NBR` changes for otherwise identical logical courses;
- title, representative class-number, and section-count changes.

The comparison identity intentionally excludes `CRSE_OFFER_NBR`. SDSU can change the offer number
for the same subject/`CRSE_ID`/career between snapshots, and treating that as a new course produced
large false-positive addition/removal lists. Offer-number drift remains visible in its own list.
Snapshot differences are reported rather than silently merged; the completed deep subject outputs
remain the production source because they contain the section details being published.

## Physical sections versus listings

The builder reports both:

- **course-section listings**: one API record for each course code/class-number combination;
- **unique physical sections**: unique `(term_code, class_number)` values.

If one physical class number is cross-listed under multiple course codes, the API retains each listing while the report counts the physical class once.

## Exit codes

- `0`: validation passed; production files were written and API data was installed when requested.
- `1`: validation found critical errors. Reports are written, but strict mode does not publish `courses.json` or `sections.json`.
- `2`: command/input failure, such as a missing checkpoint path or invalid CLI configuration.

`--allow-validation-errors` can write candidate files for debugging, but invalid data is never installed as the active API file by default.
