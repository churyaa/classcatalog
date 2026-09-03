# PeopleSoft detail grouplet follow-up

`SSR_CS_WRAP_FL` returns a Fluid master/detail shell whose main target is initially empty.
The shell exposes the lazy-loaded master-list request in JavaScript as `agGroupletList`.

The scraper now handles a detail probe as:

1. GET the course `SSR_CS_WRAP_FL` URL.
2. Parse the exact `SSR_START_PAGE_FL.GBL?...&ICGrouplet=1...` URL from `agGroupletList`.
3. GET that supplied URL with the shell URL as the `Referer`.
4. Save both responses when `--save-fixtures --detail-limit N` is active.

For class number `3213`, fixtures are named:

- `detail-3213-shell.html`
- `detail-3213-grouplet.html`

The parser deliberately does not construct or hard-code the `CSDPRD_newwin` URL. It follows the URL emitted by the live PeopleSoft shell.

## Course-information follow-up

The grouplet itself can defer the real target again with `getDefaultURL(...)`. The scraper now extracts that supplied `SSR_CRSE_INFO_FL` URL and performs a third GET using the same session. See `docs/course-info-follow.md`.
