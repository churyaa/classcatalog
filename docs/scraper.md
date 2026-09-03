# SDSU Fluid class-search scraper

## Current HTTP flow

The scraper follows the public PeopleSoft chain through the individual class-number POST:

1. Bootstrap one stateful `requests.Session` on the public landing page.
2. Construct the `SSR_CLSRCH_ES_FL` result URL for a subject keyword.
3. Locate the dynamic open-only Class Status `PTS_SELECT$N` facet. SDSU currently labels it `Open Classes` while PeopleSoft action/breadcrumb text calls the filter `Open Classes Only`. If SDSU has it selected, reconstruct the current PeopleSoft form and POST that checkbox action with the facet explicitly **off**.
4. Verify the returned page now reports the open-only Class Status checkbox as unselected.
5. Re-discover the Subject facet from that fresh all-statuses page, because PeopleSoft's numeric checkbox indexes are dynamic.
6. Recreate the PeopleSoft form state and POST the exact subject facet action while preserving the open-only Class Status checkbox as OFF.
7. If the exact-subject page reports PeopleSoft's 75-result institutional cap, recursively split
   it with dynamic facet values until every leaf is uncapped. Each sibling branch is replayed from
   fresh exact-subject state instead of reusing a stale `ICStateNum`/`ICSID`.
8. Merge/deduplicate all partition leaves into the complete typed course-stub set.
9. GET the selected course's `SSR_CS_WRAP_FL` master/detail shell.
10. Extract and GET the shell's `agGroupletList` / `SSR_START_PAGE_FL` URL.
11. Extract and GET the grouplet's `getDefaultURL(...)` / `SSR_CRSE_INFO_FL` URL.
12. Expand a truncated Course Information option grid (for example `1 - 50 of 55 options`) until
    all enrollment options are loaded.
13. Parse the fully loaded Course Information page into typed course and class-option records.
14. Discover every dynamic physical class-number action, including linked lecture/lab components.
15. Before each class click, refresh `SSR_CRSE_INFO_FL` to obtain fresh PeopleSoft form state and re-discover the action ID by class number.
16. POST the full form state with `ICAction` set to that class-number action.
17. Save the returned class-detail response for the next parser stage.

## 75-result subject-cap partitioning

The result cap applies after the exact Subject facet, so a `filtered_rows=75` page cannot be
treated as a complete subject. The scraper now uses the following fallback dimensions, in order,
only when the current branch remains capped:

1. Course Career
2. Number of Units
3. Class Status
4. Academic Session
5. Instruction Mode
6. Location

Course Career and Number of Units are preferred because they are course-level dimensions.
Later dimensions can overlap (the same course can have sections in more than one status, session,
mode, or location), so the final union is deduplicated by stable course identity. Every sibling
branch starts from a new exact-subject page and re-discovers the dynamic `PTS_SELECT$N` indexes.

`SubjectScrapeResult.complete` becomes true after a capped base page only when all resulting leaf
pages are uncapped. The result also reports `partitioned`, `partition_facets`,
`partition_leaf_count`, and `unresolved_partition_count`. If a leaf is still capped after all
known dimensions, the subject remains incomplete rather than silently dropping courses.

## Why the course page is refreshed before each class POST

PeopleSoft form state is not static. Fields such as `ICStateNum` and `ICSID` belong to the current server-side state, and action IDs can contain dynamic numeric fragments. Reusing a payload after another POST can therefore submit stale state.

`fetch_class_number_page(...)` deliberately performs a fresh GET immediately before every POST. It then finds the action from visible text such as `Lrg Lect - 3212`, rebuilds the form controls, replaces `ICAction`, and submits once. Stateful POST bodies are never blindly replayed. If a read-only class/tab POST receives a temporary 429/5xx response, the scraper cools down, reloads fresh course/class state, re-discovers the dynamic action IDs, and retries the logical navigation once.

## Rate limiting and transient recovery

The default HTTP policy is intentionally conservative: a 2.0-second minimum gap plus up to 0.75 seconds of jitter between every PeopleSoft request. Safe GETs retry temporary 429/500/502/503/504 responses with 2, 5, 10, and 20 second backoff (also honoring a numeric `Retry-After` header). Stateful POSTs are not sent through that retry loop. A transient class/tab POST instead triggers a 20-second cooldown and a fresh-state logical recovery.

Use `--resume` to continue the checkpoint instead of starting the same command as a fresh run. Advanced tuning is available through `--delay`, `--jitter`, `--max-retries`, `--post-recovery-cooldown`, `--stateful-post-recovery-attempts`, `--in-run-state-recovery-attempts`, and `--state-circuit-breaker-threshold`. Setting `--delay 0` disables normal pacing for local fixture tests; it is not recommended for live SDSU runs.

PeopleSoft can also return a structurally invalid HTTP 200 page after a long-running session has gone
stale. Missing Subject facets, deferred grouplets, Course Information URLs, class-number actions, or
class options are treated as state-shape failures. The scraper discards the public cookie/component
state, rebuilds the current subject context, and retries the logical operation. The default is one
fresh-session retry. Three consecutive unrecovered state failures open a circuit breaker, immediately
ending the process while the checkpoint remains resumable and all unvisited subjects remain pending.

## Parsed course-information fields

`parse_course_info_page(...)` currently extracts these course-level fields:

- term and `STRM`
- subject, catalog number, course code
- title
- long description
- units
- normalized grading type plus original grading text
- course component
- course career

For each class option in the Course Information grid it extracts:

- option number
- open / closed / waitlist status when present
- session
- component label
- class number
- selected section number when the current URL proves it
- meeting dates
- raw days/times text
- normalized weekdays
- start and end time
- location
- instructor
- open seats
- capacity
- derived enrolled count (`capacity - open seats`)

The class-option table returned by PeopleSoft contains mildly malformed HTML. The parser identifies raw `<tr>` boundaries first and then parses each row fragment with BeautifulSoup.

## Class-number action discovery

The CS 150 page exposes class links such as:

```text
Lrg Lect - 3212
Lrg Lect - 3213
```

Their PeopleSoft action IDs look like:

```text
SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0
SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$1
```

The `$294` fragment is treated as dynamic. `class_post.py` matches the visible class number and reads the action ID from the current HTML instead of assuming a fixed value.

The POST payload is built from the page's successful form controls, including the current hidden PeopleSoft state. Only `ICAction` is deliberately replaced with the selected class action.

## IntelliJ Run configuration

The existing configuration stays the same:

```text
Run type:          Module name
Module name:       classcatalog.scraping.main
Parameters:        --term "Fall 2026" --term-code 2267 --subjects CS --save-fixtures --detail-limit 1
Working directory: C:\Users\Churb\IdeaProjects\classcatalog-backend\classcatalog-starter
Interpreter:       C:\Users\Churb\IdeaProjects\classcatalog-backend\classcatalog-starter\.venv\Scripts\python.exe
```

For CS 150, a successful run should now produce files resembling:

```text
fixtures/sdsu/live/fall-2026/cs/
├── initial.html
├── all-statuses.html
├── filtered.html
├── detail-3213-shell.html
├── detail-3213-grouplet.html
├── detail-3213-course-info.html
├── detail-3213-course-info.json
├── detail-3213-class-3212.html
├── detail-3213-class-3213.html
└── metadata.json
```

The `detail-3213-class-*.html` files are the next protocol fixtures. They should reveal whether the class click directly contains section number, instruction mode, enrollment requirements, waitlist details, class notes, or another deferred request.

## Checks

```powershell
uv run pytest
uv run ty check
uv run ruff check .
```

The automated tests cover dynamic class-action discovery, form-state reconstruction, fresh-state GET before POST, `Referer`/`Origin` handling, and protection against hardcoded dynamic action IDs.


## Seat-status completeness

SDSU's result page defaults to an **open-only Class Status** filter. The live checkbox label is `Open Classes`, while PeopleSoft action text calls it `Open Classes Only`. ClassCatalog must not inherit that
source-side filter because the website itself needs to offer Open, Waitlist, and Closed filters.
The HTTP scraper therefore treats disabling that facet as a required protocol step.

When the checkbox is selected, the scraper sends one dedicated stateful POST with the dynamic
Open Classes action as `ICAction`, `PTS_SELECT$chk$N=N`, and no checked `PTS_SELECT$N` value.
It then verifies the response shows that checkbox as unselected before applying the exact Subject
facet. If the Open Classes facet cannot be found or remains selected, the subject scrape fails
explicitly rather than silently returning an incomplete open-only catalog.

With `--save-fixtures`, the response immediately after disabling the source-side filter is saved
as `all-statuses.html`. This is the first fixture to inspect when a full/waitlisted class seems to
vanish before Course Information.

## Checkpointing, resume, and per-subject output

Every normal scraper run now creates three output layers:

```text
results/
├── fall-2026-course-stubs.json          # aggregate search/run summary
└── fall-2026/
    ├── checkpoint.json                  # small atomic resume index
    └── subjects/
        ├── cs.json                      # complete CS search/detail state
        ├── math.json
        └── ...
```

The checkpoint is written atomically after subject discovery and before and after every selected
course-detail attempt. The full detail payload is stored in the per-subject file rather than copied
into the checkpoint. A subject file contains:

- the complete `SubjectScrapeResult` and course stubs;
- the stable keys of the courses selected by `--detail-limit`;
- each course's `in_progress`, `complete`, `partial`, or `failed` state;
- attempt count and warning count;
- parsed `CourseInfoRecord` data;
- assembled `SdsuCourseSectionRecord` records;
- paths to the raw/parsed fixtures used for that course;
- subject-level detail totals and completion state.

An interrupted course is left as `in_progress`. On resume it is retried from fresh PeopleSoft state.
A completed course is never requested again. Partial and failed courses are retried and replace their
prior course record after the new attempt finishes.

Resume also recalculates completion from the persisted search inventory and detail target keys. Older
subject files with `courses > 0` but no detail targets are never skipped as complete: their subject
search is rebuilt and the corrected target list is checkpointed. Completed course records whose stable
keys still match are retained.

When a capped subject was assembled from multiple partition leaves, detail scraping first resets to a
fresh exact-subject context and canonicalizes every course URL from its stable PeopleSoft identifiers.
This avoids inheriting the final partition leaf's server-side state, including an empty `Closed Classes`
branch that can otherwise produce Course Information pages with zero options.

Start a new detail run normally, for example:

```text
--term "Fall 2026" --term-code 2267 --subjects CS MATH --save-fixtures --detail-limit 100
```

Resume the default checkpoint without repeating the subject list or detail limit:

```text
--term "Fall 2026" --term-code 2267 --resume
```

Custom locations are supported:

```text
--checkpoint results/fall-2026/deep-scrape-checkpoint.json
--subject-output-dir results/fall-2026/deep-subjects
```

Resume validates the term, term code, selected subjects, detail limit, aggregate output path, and
per-subject output directory. A mismatch exits with code 2 instead of mixing two runs. A fresh run
replaces the checkpoint at the selected path. `--resume-from` is still a manual new-run slice and is
mutually exclusive with `--resume`.

A completed checkpoint can be resumed safely; every subject is loaded from disk and skipped, so the
command exits without repeating SDSU searches. If a checkpoint points to a missing or invalid subject
file, that subject is marked pending and scraped again rather than being silently treated as complete.

For a detail run, the process now exits nonzero while any selected course detail remains partial,
failed, or interrupted. This makes the checkpoint state suitable for IntelliJ, scripts, and CI.
