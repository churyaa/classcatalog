# ClassCatalog — SDSU course-picker MVP

A runnable starter for a student-friendly SDSU class catalog. The demo includes a typed FastAPI backend, a responsive browser UI, live result counts, sorting, sample schedule/rating data, a 127-subject queue, and safe adapters for the real data sources.

## Important source boundaries

- SDSU's old Sunspot schedule is not the only source needed for the requested terms. SDSU routes Summer 2026, Fall 2026, and Spring 2027 through its newer public PeopleSoft Class Search.
- Treat the supplied 127 subject abbreviations as a seed list. At the start of every term sync, compare it with the current subject picker and report additions/removals.
- Do not scrape authenticated my.SDSU pages or student degree evaluations. Personalized requirement matching should use information the student enters or an explicitly authorized integration.
- RateMyProfessors' current terms prohibit automated scraping without permission. Implement the `RatingsSource` protocol with a licensed/authorized provider, an approved export, or omit ratings and link out.

## Run the demo

```bash
uv sync --dev
uv run uvicorn classcatalog.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Load more than the 13 demo sections

The application starts with `src/classcatalog/data/sample_sections.json`, which contains 13
handwritten demo sections. To load a real SDSU Schedule of Classes export:

1. Download the schedule spreadsheet for a term from SDSU.
2. Open it in Excel and save a copy as **CSV UTF-8 (Comma delimited)**.
3. Put the CSV in this project, for example `imports/fall-2026.csv`.
4. Convert it into the typed ClassCatalog JSON format:

```bash
uv run classcatalog-import-schedule imports/fall-2026.csv --term "Fall 2026"
```

The command writes `src/classcatalog/data/sections.json`. The backend automatically prefers
that file over the 13-row sample file the next time the server starts. Restart the Run
configuration and refresh the browser.

Import additional terms with `--append`:

```bash
uv run classcatalog-import-schedule imports/summer-2026.csv --term "Summer 2026"
uv run classcatalog-import-schedule imports/fall-2026.csv --term "Fall 2026" --append
uv run classcatalog-import-schedule imports/spring-2027.csv --term "Spring 2027" --append
```

The importer groups repeated spreadsheet rows by Class Number, deduplicates meeting rows,
normalizes meeting days and times, maps common instruction-mode codes, and recognizes common
Enrollment Capacity, Enrollment Total, Seats Available, and course-description headers. When
capacity and enrollment are present, cards display occupancy such as `Open 37/40` or
`Closed 40/40`. The schedule export may not contain prerequisites, grading basis, program
classifications, professor ratings, or course descriptions; those fields remain unknown until
their separate source adapters are implemented. The frontend displays at most 50 sections per
page and provides synchronized numbered **Previous** and **Next** pagination controls above and
below large result sets. The API also enforces a maximum page size of 50.

To return to the 13-row demo, stop the server and delete:

```text
src/classcatalog/data/sections.json
```

Quality checks:

```bash
uv run ruff check .
uv run ty check
uv run pytest
```

For Playwright-based source discovery:

```bash
uv run playwright install chromium
uv run python scripts/record_sdsu_network.py
```

The recorder opens the public SDSU Class Search and logs XHR/fetch responses while you perform one normal subject search. Prefer an authorized structured endpoint over brittle DOM scraping. Do not bypass authentication, access controls, rate limits, or anti-bot controls.

## What already works

- Semester buttons for All Terms, Summer 2026, Fall 2026, and Spring 2027.
- Result count updates after every filter change.
- Seat occupancy displays status plus enrollment/capacity when available.
- Course-code information buttons reveal catalog descriptions on hover or keyboard focus.
- Search by course, title, or instructor.
- Filters for requirement tags, units, grading, program classification, prerequisite eligibility, days, time window, format, seat status, rating, professor difficulty, would-take-again percentage, review count, attendance, and textbook status.
- Sort course A-Z or Z-A; sort professor rating, class difficulty, professor difficulty, review count, and would-take-again percentage in either direction.
- Completed-course input with simple AND/OR prerequisite evaluation.
- A typed asynchronous queue covering the supplied 127 subject abbreviations.

## Filter semantics

Filters are **ANDed across groups** and **ORed within a group**. For example, selecting `Online` or `Hybrid` plus `Open` means `(Online OR Hybrid) AND Open`.

- Days mean “the section has at least one meeting on any selected day.” A production version should also offer an “all meetings fit my availability” mode.
- A time window requires every scheduled timed meeting to fit inside the chosen window. Asynchronous/TBA sections are excluded when a time window is active.
- `Eligible only` evaluates normalized prerequisite groups against the completed-course list. Any non-course conditions remain marked for manual review.
- Attendance and textbook fields are review-reported signals, not authoritative course policies. Show the sample size and preserve `unknown`.

Metric sorts keep sections with missing data after sections with known values, regardless of
sort direction. `class_difficulty` is a separate optional course-level signal, while
`professor.difficulty` is the instructor-level signal. This prevents unknown values from appearing
as the lowest rating, easiest class, easiest professor, or smallest percentage.

## Recommended production architecture

```text
SDSU public class search ─┐
SDSU catalog/roadmaps ────┼─> ingestion workers -> PostgreSQL -> FastAPI -> web UI
Authorized ratings source ┘                         |           -> cached facets
                                                     -> audit/source timestamps
```

Use separate adapters for:

1. **Schedule source:** sections, instructors, meetings, modes, seats, notes, and term-specific identifiers.
2. **Catalog source:** canonical course descriptions, units, grading, prerequisites, GE attributes, and program/catalog-year mappings.
3. **Ratings source:** professor metrics only from an authorized source.

The production database should keep `source_url`, `source_updated_at`, `fetched_at`, and a payload checksum on every imported record. Never silently merge instructors solely by last name; normalize names, compare department, and retain a match-confidence score.

## Live SDSU Fluid scraper

The project now includes a stateful `requests.Session` scraper for the modern PeopleSoft Fluid
results component. It constructs one subject-keyword GET, dynamically locates SDSU's open-only Class Status checkbox (`Open Classes` in the live UI; `Open Classes Only` in PeopleSoft action text)
`PTS_SELECT$N` facet and explicitly turns it **off**, then discovers the exact subject facet,
reconstructs the PeopleSoft form state, POSTs that facet action, and parses typed course stubs
plus course-detail identifiers. This keeps open, waitlisted, and closed class options available
for ClassCatalog's own seat-status filters. Detail probes now follow the Fluid shell, grouplet, and `SSR_CRSE_INFO_FL` course-information request, parse that page into typed course and class-option records, then refresh the course page and POST every dynamic class-number action using fresh PeopleSoft form state.

Install the new scraper dependencies after applying this update:

```bash
uv sync --dev
```

Verify currently exposed term codes:

```bash
uv run classcatalog-scrape-sdsu --list-terms
```

Run the smallest Fall 2026 test and save regression fixtures:

```bash
uv run classcatalog-scrape-sdsu \
  --term "Fall 2026" \
  --term-code 2267 \
  --subjects CS \
  --save-fixtures \
  --detail-limit 1
```

The safe default is one subject (`CS`). Use `--all-subjects` only after the first run succeeds.
The aggregate course-stub file remains `results/fall-2026-course-stubs.json`. A separate atomic
checkpoint is written to `results/fall-2026/checkpoint.json`, and every subject gets an incremental
JSON file under `results/fall-2026/subjects/` (for example `cs.json`). The checkpoint is updated
after subject discovery and before/after every course-detail attempt. Each subject JSON contains the
search result, selected detail targets, parsed course information, assembled section records, fixture
paths, attempts, warnings, and completion state.

Each probed course also produces a `detail-*-course-info.json` fixture containing parsed description, units, grading, class options, instructors, meetings, and seat counts. Fixed-unit courses retain `units` plus matching `units_min`/`units_max`; variable-unit courses such as `1.00 - 3.00` use `units=null` with the range preserved in `units_min`, `units_max`, and `units_text`. Every clickable class number is then POSTed and saved as `detail-*-class-<class-number>.html` for the next section-specific parser stage. Near-final `detail-*-sections.json` records use `course_source_url` for the course-entry URL. They intentionally do not pretend that this is a direct class URL: individual Class Information modals are opened by stateful PeopleSoft POST actions and may not have a stable per-class GET URL. The atomic run JSON and completion logs also report `detail_courses_attempted`, `detail_courses_complete`, `detail_courses_partial`, and `detail_warnings`, so partial detail failures are visible even when the subject search succeeds. A result marked `complete=false` means either the search remained incomplete or one of the selected detail targets still needs to be retried.

Resume an interrupted run with the same default checkpoint by using only the term and `--resume`:

```bash
uv run classcatalog-scrape-sdsu \
  --term "Fall 2026" \
  --term-code 2267 \
  --resume
```

When `--subjects`/`--all-subjects` and `--detail-limit` are omitted during resume, their values are
loaded from the checkpoint. Completed subjects are skipped without another search. Within a partial
subject, completed course details are skipped and only partial, failed, or interrupted courses are
retried. Resume validation rejects a different term, term code, subject list, detail limit, aggregate
output path, or per-subject output directory. `--resume-from` remains available as a manual starting
point for a new run; it cannot be combined with `--resume`. Custom paths are available through
`--checkpoint` and `--subject-output-dir`.

Deep-run resume also repairs checkpoints written by older builds that incorrectly marked a subject
complete despite having scheduled courses and no detail targets. Those subjects are reset to pending,
their search is rebuilt, and any valid completed course details already stored in the subject file are
retained. A subject with courses can no longer be complete unless the configured number of course
details was actually targeted and completed.

Long-running public PeopleSoft sessions can become structurally stale while still returning HTTP 200.
The scraper now recognizes missing Subject facets, missing deferred grouplets/course-information URLs,
missing class actions, and zero-option Course Information pages as recoverable state failures. It
creates a fresh public session, rebuilds the current subject context, and retries the logical operation
once by default. If three consecutive operations still fail after recovery, a circuit breaker stops the
run and leaves the remaining subjects pending for `--resume` instead of generating a cascade of false
failures or false completions. Tune those safeguards with:

```text
--in-run-state-recovery-attempts 1
--state-circuit-breaker-threshold 3
```

For partitioned subjects, detail scraping now starts from a fresh exact-subject context rather than the
last partition leaf, and every merged course detail URL is rebuilt from stable PeopleSoft identifiers.
This prevents an empty final partition (for example, `Closed Classes`) from yielding zero-option detail
pages for an otherwise valid subject.

If an exact-subject page hits PeopleSoft's 75-result institutional cap, the scraper now
automatically partitions that subject through dynamic facet values until every leaf is uncapped,
then merges/deduplicates the course stubs. The original capped-page metadata is preserved, while
`partitioned`, `partition_facets`, `partition_leaf_count`, and `unresolved_partition_count`
describe the recovery. A result is `complete=false` only when the automatic splitter exhausts
its known facet dimensions and at least one leaf is still capped.

The implementation lives under `classcatalog.scraping`:

- `constants.py` contains the confirmed components, institution, result query, and Fall 2026 code.
- `session.py` performs landing GET, result GET, an explicit open-only Class Status = OFF POST, exact-subject facet POST, automatic fresh-state partitioning for capped subjects, canonical partition-detail rehydration, deferred detail GETs, stateful class-number/tab POSTs, conservative pacing with jitter, GET backoff for transient 429/5xx responses, in-run fresh-session recovery, a state-failure circuit breaker, and login checks.
- `facet_parser.py` dynamically discovers the open-only Class Status, exact-subject, and generic partition-facet checkbox indexes and rebuilds their PeopleSoft form payloads without hardcoding `PTS_SELECT$N` values.
- `class_post.py` discovers class-number actions and rebuilds the stateful PeopleSoft POST payload without hardcoding dynamic action IDs.
- `parser.py` handles multi-word subjects, result rows, detail URLs, term discovery, AJAX XML, and the fully loaded `SSR_CRSE_INFO_FL` course-information grid.
- `models.py` contains typed course-stub, course-information, class-option, per-subject detail, and checkpoint records.
- `progress.py` owns atomic model writes, stable course-detail keys, detail-target selection, and checkpoint state updates.
- `main.py` provides `--term`, `--term-code`, `--subjects`, `--all-subjects`, `--resume`,
  `--resume-from`, `--checkpoint`, `--subject-output-dir`, `--save-fixtures`, `--detail-limit`,
  `--in-run-state-recovery-attempts`, `--state-circuit-breaker-threshold`, and conservative HTTP
  pacing/recovery controls.

Detailed run instructions and the next class-detail parsing milestone are in
[`docs/scraper.md`](docs/scraper.md). The older Playwright recorder remains available only as a
source-discovery fallback; it is not used by this result-list path.

## Validate and publish a completed deep scrape

After all per-subject deep outputs are complete, run the production dataset builder. It validates
checkpoint completeness, reconciles the deep course inventory against the earlier search-only
snapshot, counts unique physical class numbers, produces canonical course records, and writes the
`CourseSection` JSON consumed by the FastAPI repository. Snapshot reconciliation identifies a
logical course by subject, `CRSE_ID`, and academic career; `CRSE_OFFER_NBR` changes are reported as
metadata drift instead of false additions/removals.

From the inner project directory on Windows:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.dataset.main `
  --checkpoint results\fall-2026-deep\checkpoint.json `
  --discovery-run results\fall-2026-course-stubs-complete-2026-08-20.json `
  --output-dir results\fall-2026-production `
  --install-api-data
```

A passing build writes `courses.json`, `sections.json`, `inventory-diff.json`, JSON/Markdown
validation reports, and a SHA-256 manifest under `results/fall-2026-production/`. With
`--install-api-data`, the sections file is atomically installed at
`src/classcatalog/data/sections.json`; restart the API afterward. Optional missing values remain
unknown rather than being fabricated, and a failing strict validation does not replace the active
API dataset.

See [`docs/dataset-build.md`](docs/dataset-build.md) for the complete schema, validation rules,
physical-section counting semantics, and exit codes.

## Validate the production-sized API and website contract

After installing `sections.json`, exercise the real FastAPI endpoints, all 50-record pages, dynamic
filters, sort modes, and the browser pagination contract without starting Uvicorn:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.api_validation.main `
  --data src\classcatalog\data\sections.json `
  --catalog-data src\classcatalog\data\catalog_mappings.json `
  --output-dir results\fall-2026-api-validation `
  --expected-sections 7035 `
  --expected-courses 2955 `
  --expected-physical-sections 7024 `
  --expected-subjects 127
```

The validator traverses every unfiltered page and a broad filtered result set, verifies that no
normalized section IDs are omitted or duplicated, tests search/unit/day/time/format/seat/grading
filters, confirms both alphabetical directions plus every professor sort value, and checks that the
HTML/JavaScript expose the top and bottom pagination controls. It writes JSON and Markdown reports.
See [`docs/api-validation.md`](docs/api-validation.md).

## Suggested next database tables

- `terms`
- `courses`
- `sections`
- `meetings`
- `instructors`
- `professor_metrics`
- `requirement_definitions`
- `course_requirement_mappings`
- `program_course_classifications`
- `prerequisite_rules`
- `source_snapshots`
- `sync_runs` and `sync_errors`

Program requirement mappings must be keyed by at least `(program, catalog_year, course_code)`. “Major prep” is not a universal property of a course.

### Class-information tab probes

A `--detail-limit` probe now continues past the class-number modal. The scraper parses the default Enrollment Information response and then safely reopens the class before following each additional tab (`CD`, `MI`, `CA`, and `TI`). Dynamic PeopleSoft radio IDs are discovered on every fresh modal instead of being hardcoded.

For a seed detail such as class 3213, the fixture directory now also receives files like:

```text
detail-3213-class-3212-enrollment.json
detail-3213-class-3212-class-details.html
detail-3213-class-3212-meeting-information.html
detail-3213-class-3212-class-availability.html
detail-3213-class-3212-textbook-other-materials.html
```

The same set is produced for the other class options. These tab fixtures are the regression inputs for the next parser stage that will merge section number, instruction mode, detailed availability/waitlist, textbook signals, and any additional meeting/class attributes into final `CourseSection` records.

## Public SDSU degree and requirement mappings

The schedule scrape does not contain degree-program classifications or public GE/graduation
requirement lists. ClassCatalog now loads those from a separate catalog overlay so mappings remain
catalog-year-specific.

Install the Playwright browser once:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

Then collect all discovered 2026-2027 undergraduate bachelor's programs and public requirement
pages:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.catalog.main `
  --catalog-year 2026-2027 `
  --all-programs `
  --headed `
  --pause-for-human `
  --install-api-data
```

The SDSU catalog can show a cookie banner and may sometimes show browser verification. Accept
the cookie banner; if no separate verification appears, none is required. Press Enter only once
the actual catalog content is visible. The installed overlay is written to:

```text
src\classcatalog\data\catalog_mappings.json
```

After restarting the API, Program Classification, Requirements, and the Student Profile planning
summary are active. See `docs/catalog-data.md` for source, privacy, fixture, and parser details.

New catalog endpoints:

```text
GET /api/catalog/status
GET /api/catalog/program
GET /api/profile/summary
```

## Production frontend validation

After installing the catalog overlay, validate the real browser UI against the 7,035 normalized
Fall 2026 listings:

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

The validator starts a temporary local API, drives Chromium, tests production-sized UI behavior,
and writes JSON, Markdown, and screenshot evidence. See `docs/frontend-validation.md`.
