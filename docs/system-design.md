# System design

## Product model

ClassCatalog separates a canonical **course** from a term-specific **section**.

- A course owns code, title, catalog description, units, grading methods, normalized prerequisite rules, and catalog attributes.
- A section owns term, schedule number, component, instructor assignments, meeting rows, instruction mode, campus, seat counts, and live status.
- Program classification is a mapping, not a course column: `(program, catalog_year, course_id) -> major_prep | major_course | elective`.
- Requirement fulfillment is also a mapping keyed by catalog year. A course can satisfy multiple requirements.
- Course-level difficulty is imported as a separate aggregate with provider, review count, and provenance.
- Professor signals are imported separately and joined through an instructor-match record with confidence and provenance.

## Ingestion flow

1. Discover the current term list and available subject values from the public SDSU interface.
2. Compare discovered subjects with the 127-item seed and record additions/removals.
3. Enqueue `(term, subject)` jobs with bounded concurrency.
4. Parse course groups, sections, meetings, instructors, seats, and detail-page fields.
5. Upsert by stable SDSU identifiers; retain source snapshots and checksums.
6. Refresh catalog and roadmap mappings on a slower cadence than seat data.
7. Match instructors to authorized rating records using normalized full name, department, and manual overrides.
8. Materialize searchable facets and invalidate API caches.

Every run should have a unique ID, start/end timestamps, source version, requested/completed counts, error rows, and a publish decision. Do not replace the current published dataset with a clearly incomplete run.

## Refresh strategy

- Full schedule/catalog reconciliation: periodic and after SDSU publishes a new term.
- Seat-only refresh: incremental, conservative, and more frequent during registration.
- Ratings: slow refresh with a long cache because the data changes less frequently.
- Source failures: exponential backoff, jitter, and a circuit breaker; no retry storms.

The exact frequency must be approved against each source's terms and capacity.

## API shape

`GET /api/classes` supports repeated query parameters. Examples:

```text
/api/classes?term=Fall%202026&seat_status=open&instruction_mode=in_person
/api/classes?classification=major_prep&program=Computer%20Science%2C%20B.S.&catalog_year=2026-2027
/api/classes?day=mon&day=wed&time_from=09:00&time_to=15:00
/api/classes?rating_min=4&difficulty_max=3&reviews_min=20&sort_by=professor_rating
/api/classes?sort_by=difficulty
/api/classes?sort_by=professor_difficulty_asc
```

Within a repeated group values are ORed; different groups are ANDed.

A production response should also return facet counts calculated against the current query while excluding each facet's own selection. That supports labels such as `Open (83)` and lets students see the effect before clicking.

## Prerequisite representation

Do not rely on free-text parsing at request time. Store both the original prerequisite text and a normalized expression tree. A practical initial representation is conjunctive normal form:

```json
{
  "all_of": [
    {"any_of": ["CS 160"]},
    {"any_of": ["MATH 150", "MATH 151"]}
  ],
  "manual_conditions": ["minimum grade C"]
}
```

The API may confidently evaluate the course-code groups, but it must surface manual conditions instead of presenting eligibility as guaranteed.

## Ratings matching

Never merge on last name alone. Use:

1. normalized full name;
2. department/subject compatibility;
3. exact external ID override when manually verified;
4. confidence score and an `unmatched` state.

Attendance and textbook fields should be aggregated at the professor-course level when the authorized source supports it. Show `unknown` and sample size rather than converting missing data to `false`. Keep course-level class difficulty separate from professor difficulty so the two sorts do not silently reuse the same number.

## Privacy boundary

The MVP should let students enter a major, catalog year, and completed courses locally. Store profiles only with clear consent. Do not collect SDSU credentials or scrape degree-evaluation pages. Any future SSO or student-record integration requires explicit university authorization and a FERPA-aware data review.

## Public catalog overlay

The schedule dataset remains immutable source data. Public SDSU catalog mappings are loaded as a
separate overlay at process startup and attached to matching normalized course codes in memory.
This preserves catalog-year semantics and avoids treating “major prep” as an intrinsic property of
a course.

The overlay file contains:

```text
CatalogMappings
├── catalog year and source metadata
├── programs
│   └── course code -> major_prep | major_course | elective
└── public requirements
    └── requirement label -> course codes
```

The Student Profile feature is deliberately not a degree audit. It reports public mapped courses
that are present in the loaded schedule and subtracts course codes entered locally by the user.
It does not collect credentials or student records, and it does not determine graduation
eligibility.

## Browser release validation

The API validator proves route/filter behavior over the entire normalized JSON dataset. The
Playwright frontend validator adds browser-level checks for rendering, pagination, course-prefix
search, sticky filter behavior, time resets, and the catalog/profile controls. Both validators
write machine-readable and Markdown reports for release evidence.
