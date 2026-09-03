# SDSU public catalog and degree-mapping ingestion

ClassCatalog treats schedule data and degree/catalog data as different sources. The mySDSU
schedule supplies the classes actually offered in a term. The public SDSU catalog supplies
program requirements, requirement categories, and the catalog year under which those mappings
apply.

SDSU's Acalog `catoid` is deployment metadata and must not be treated as a stable catalog-year
identifier. The collector opens the catalog landing page, resolves the active catoid from the
rendered 2026-2027 navigation, and automatically replaces a stale id.
The Computer Science department links both the official B.S. catalog entry and its roadmap from
its degree-program page. The catalog is browser-rendered and may present cookie or verification
UI, so this collector uses Playwright rather than `requests`.

## Privacy boundary

This collector reads only public catalog pages. It does **not** sign in to my.SDSU, collect SDSU
credentials, open a student's degree evaluation, or infer official graduation clearance. The
Student Profile summary in ClassCatalog is a local planning aid based on:

- a program selected by the user;
- a catalog year selected by the user;
- public program-to-course mappings;
- completed course codes manually entered by the user;
- classes present in the loaded schedule.

Grades, transfer work, residency, units, GPA, substitutions, waivers, and non-course conditions
still require the official SDSU degree evaluation and advisor review.

## One-time browser installation

From the inner `classcatalog-starter` directory:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

## Collect the 2026-2027 catalog overlay

Create an IntelliJ Python configuration with module:

```text
classcatalog.catalog.main
```

Program parameters:

```text
--catalog-year 2026-2027 --all-programs --headed --pause-for-human --install-api-data
```

Equivalent command:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.catalog.main `
  --catalog-year 2026-2027 `
  --all-programs `
  --headed `
  --pause-for-human `
  --install-api-data
```

The collector pauses proactively after opening the first catalog page. If SDSU only shows a
cookie banner, accept it; that is enough and there may be no separate verification page. If a
verification page does appear, complete it. Once the actual catalog content is visible, keep the
browser window open, return to the console, and press Enter. The collector keeps the visible page
instead of forcing the original URL again, then resolves the active catalog catoid from the
rendered navigation. The browser state is
saved at:

```text
results\sdsu-catalog-browser-state.json
```

Later catalog refreshes can usually run headlessly using that state:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.catalog.main `
  --catalog-year 2026-2027 `
  --all-programs `
  --install-api-data
```

The collector writes:

```text
results\sdsu-catalog-2026-2027\catalog-mappings.json
fixtures\sdsu\catalog\2026-2027\catalog-index.html
fixtures\sdsu\catalog\2026-2027\programs\...
fixtures\sdsu\catalog\2026-2027\requirements\...
src\classcatalog\data\catalog_mappings.json
```

The installed overlay activates:

- Major and catalog-year filtering;
- Major preparation, major course, and elective filtering;
- public GE/graduation requirement filtering;
- the Student Profile planning summary;
- catalog status and program-summary API endpoints.

## Targeted program collection

For a smaller first pass:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.catalog.main `
  --catalog-year 2026-2027 `
  `
  --program "Computer Science, B.S." `
  --headed `
  --pause-for-human `
  --install-api-data
```

Explicit `preview_program.php` URLs can be supplied with repeated `--program-url` arguments.
Explicit requirement pages can be supplied with repeated `--requirement-url` arguments when the
catalog navigation changes and automatic discovery cannot find them.

## Interpretation rules

Program courses are classified only when a nearby heading clearly identifies one of these
categories:

- `major_prep`: preparation for the major or equivalent lower-division preparation;
- `major_course`: required/core major coursework;
- `elective`: elective, option, or selected-course groups.

Ambiguous course links are retained in `unmapped_course_codes` rather than guessed.

Requirement mappings recognize named categories such as Oral Communication, Critical Thinking,
American Institutions, GWAR, and explicit GE/Area codes. The raw source HTML is saved so parser
changes can be regression-tested later.

## API integration

At startup, `CourseRepository` loads `src/classcatalog/data/catalog_mappings.json` when present.
Set a different overlay explicitly with:

```powershell
$env:CLASSCATALOG_CATALOG_PATH = "C:\path\to\catalog-mappings.json"
```

Useful endpoints:

```text
GET /api/catalog/status
GET /api/catalog/program?program=Computer%20Science%2C%20B.S.&catalog_year=2026-2027
GET /api/profile/summary?program=Computer%20Science%2C%20B.S.&catalog_year=2026-2027&completed_course=CS%20150
```


### Browser window closes during verification

If Playwright Chromium is closed or replaced during the first navigation, the collector retries
and reopens a usable tab automatically. The default allows two recoveries. To use Microsoft Edge
on Windows instead of bundled Chromium, run:

```powershell
.\.venv\Scripts\python.exe -m classcatalog.catalog.main `
  --catalog-year 2026-2027 `
  --all-programs `
  --headed `
  --pause-for-human `
  --browser-channel msedge `
  --install-api-data
```

Do not close the browser yourself. Press Enter in the console only after the actual catalog page
is visible. A cookie banner by itself is not a verification challenge.
