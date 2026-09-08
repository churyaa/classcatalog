# ClassCatalog compact SEO landing-page follow-up

This package is designed to be applied **after** the previously merged ClassCatalog SEO landing-page implementation.
It changes presentation only. It does not modify the dataset builder, reconciliation logic, grouping logic, seat refresh service, filters, sitemap identity rules, or canonical URL rules.

## What changes

### `/subjects`

- Simplified subject directory.
- Each subject is a single whole-card link.
- Card text is intentionally compact, for example `ACCTG - Accountancy`.
- Only the current course count is shown; enrollment-option counts are removed from this directory.

### `/subjects/{slug}`

- Replaces large course cards with a compact table similar to the supplied reference image.
- Columns: **Course**, **Title**, **Units**, **Options**.
- Course descriptions, campuses, and instruction-mode summaries are removed from the subject listing.
- Course pages remain normal crawlable links.

### `/courses/{slug}`

- Keeps the course heading/description but compresses the enrollment section into a table.
- Columns: **Class #**, **Format**, **Status**, **Seats**, **Time**, **Location**, **Professor**, **RateMyProfessors**.
- Seat count is shown as `available / capacity` when both values exist.
- Status is shown separately as Open, Waitlisted, Closed, or Unknown.
- RMP cell includes rating, review count, difficulty, take-again percentage, and the RMP profile link when one exists.
- Linked lecture/lab/activity components are rendered as compact rows inside the same enrollment-option group. No component or physical class is discarded.
- Course requirements become a compact expandable disclosure when present.

The stylesheet URL is bumped to `/static/seo.css?v=2` so browsers do not retain the previous SEO layout CSS.

## Apply

From the ClassCatalog repository root:

```powershell
cd C:\Users\Churb\IdeaProjects\classcatalog-backend\classcatalog-starter

# Extract this ZIP somewhere first, for example:
Remove-Item C:\Temp\classcatalog-seo-compact-pages -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path "$env:USERPROFILE\Downloads\classcatalog-seo-compact-pages.zip" -DestinationPath C:\Temp\classcatalog-seo-compact-pages -Force

& C:\Temp\classcatalog-seo-compact-pages\apply.ps1
```

The apply script verifies that the original SEO implementation exists, replaces only the SEO renderer/templates/CSS/test file, and runs `git diff --check`.

## Test

```powershell
& C:\Temp\classcatalog-seo-compact-pages\test.ps1
```

Or manually:

```powershell
cd C:\Users\Churb\IdeaProjects\classcatalog-backend\classcatalog-starter

.\.venv\Scripts\python.exe -m pytest -q tests\test_seo.py tests\test_seo_landing_pages.py tests\test_cookie_consent.py tests\test_filters.py tests\test_seat_refresh.py tests\test_seat_refresh_api.py tests\test_ui_features.py

uv run ruff check .
uv run ty check
.\.venv\Scripts\python.exe -m pytest -q
```

## Manual browser checks

After starting the site, check representative pages:

```text
http://127.0.0.1:8000/subjects
http://127.0.0.1:8000/subjects/aerospace-engineering
http://127.0.0.1:8000/subjects/computer-science
http://127.0.0.1:8000/courses/a-e-123
http://127.0.0.1:8000/courses/cs-210
```

For a course with linked components, confirm all expected class numbers still appear and remain grouped together. For a professor with cached RateMyProfessors data, confirm the rating/difficulty/take-again/review values appear in the same row as that class.
