# PeopleSoft course-information follow-up

The Fluid detail flow is three HTTP GETs after the course-search result link:

1. Fetch the `SSR_CS_WRAP_FL` detail shell.
2. Parse `agGroupletList` and fetch the supplied `SSR_START_PAGE_FL` grouplet URL.
3. Parse the grouplet's `getDefaultURL(...)` call and fetch the supplied `SSR_CRSE_INFO_FL` URL.

The scraper deliberately follows the URLs emitted by PeopleSoft instead of constructing the
`CSDPRD_newwin`, `SSR_START_PAGE_FL`, or `SSR_CRSE_INFO_FL` paths itself.

With `--save-fixtures --detail-limit 1`, a CS 150 probe now saves files like:

- `detail-3213-shell.html`
- `detail-3213-grouplet.html`
- `detail-3213-course-info.html`

The `course-info` fixture is the next parser target for section-level fields such as instructors,
meeting patterns, enrollment, seats, units, grading, prerequisites, and class notes.
