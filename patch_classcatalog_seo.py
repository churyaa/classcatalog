from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

BASELINE_COMMIT = "3a4d86efb65df71677b19d0dabc0ae24bd983d75"
MARKER = "ClassCatalog SEO landing pages"

SUBJECT_SEO_BLOCK = r'''

# Stable public labels/slugs used by crawlable subject landing pages. Keep these
# independent from transient PeopleSoft facet indexes. Unknown/new subject codes
# intentionally fall back to the normalized abbreviation until a verified label
# is added here.
SUBJECT_SEO_NAMES: Final[dict[str, str]] = {
    "A E": "Aerospace Engineering",
    "AAS": "Asian American Studies",
    "ACCTG": "Accountancy",
    "AFRAS": "Africana Studies",
    "AMIND": "American Indian Studies",
    "ANTH": "Anthropology",
    "ARAB": "Arabic",
    "ARP": "Administration, Rehabilitation and Postsecondary Education",
    "ART": "Art",
    "ASIAN": "Asian Studies",
    "ASL": "American Sign Language",
    "ASTR": "Astronomy",
    "AUD": "Audiology",
    "B A": "Business Administration",
    "BDA": "Big Data Analytics",
    "BIOL": "Biology",
    "BQS": "Business Quantitative Skills",
    "BRAZ": "Brazilian Studies",
    "CCS": "Chicana and Chicano Studies",
    "CFD": "Child and Family Development",
    "CHEM": "Chemistry",
    "CHIN": "Chinese",
    "CIV E": "Civil Engineering",
    "CJ": "Criminal Justice",
    "CLASS": "Classics",
    "COMM": "Communication",
    "COMP": "Comparative Literature",
    "COMPE": "Computer Engineering",
    "CON E": "Construction Engineering",
    "CON M": "Construction Management",
    "CS": "Computer Science",
    "CSP": "Counseling and School Psychology",
    "DANCE": "Dance",
    "DLE": "Dual Language and English Learner Education",
    "DPT": "Doctor of Physical Therapy",
    "E E": "Electrical Engineering",
    "ECON": "Economics",
    "ED": "Education",
    "EDL": "Educational Leadership",
    "ENGR": "Engineering",
    "ENS": "Exercise and Nutritional Sciences",
    "ENV E": "Environmental Engineering",
    "ENV S": "Environmental Sciences",
    "EUROP": "European Studies",
    "FILIP": "Filipino",
    "FIN": "Finance",
    "FN": "Foods and Nutrition",
    "FRENC": "French",
    "GEN S": "General Studies",
    "GEOG": "Geography",
    "GEOL": "Geological Sciences",
    "GERMN": "German",
    "GERO": "Gerontology",
    "H SEC": "Homeland Security",
    "HEBRW": "Hebrew",
    "HHS": "Health and Human Services",
    "HIST": "History",
    "HONOR": "Honors",
    "HTM": "Hospitality and Tourism Management",
    "HUM": "Humanities",
    "I B": "International Business",
    "INT S": "International Studies",
    "ISCOR": "International Security and Conflict Resolution",
    "ITAL": "Italian",
    "JAPAN": "Japanese",
    "JMS": "Journalism and Media Studies",
    "JS": "Jewish Studies",
    "KOR": "Korean",
    "LATAM": "Latin American Studies",
    "LDT": "Learning Design and Technology",
    "LGBT": "Lesbian, Gay, Bisexual and Transgender Studies",
    "LIB S": "Liberal Studies",
    "LING": "Linguistics",
    "M E": "Mechanical Engineering",
    "M S E": "Materials Science and Engineering",
    "MATH": "Mathematics",
    "MGT": "Management",
    "MIL S": "Military Science",
    "MIS": "Management Information Systems",
    "MKTG": "Marketing",
    "MTHED": "Mathematics Education",
    "MUSIC": "Music",
    "NAV S": "Naval Science",
    "NURS": "Nursing",
    "OCEAN": "Oceanography",
    "P A": "Public Administration",
    "P H": "Public Health",
    "PERS": "Persian",
    "PHIL": "Philosophy",
    "PHYS": "Physics",
    "POL S": "Political Science",
    "PORT": "Portuguese",
    "PSY": "Psychology",
    "R A": "Recreation Administration",
    "REL S": "Religious Studies",
    "RTM": "Recreation and Tourism Management",
    "RUSSN": "Russian",
    "RWS": "Rhetoric and Writing Studies",
    "SLHS": "Speech, Language and Hearing Sciences",
    "SOC": "Sociology",
    "SOCSI": "Social Science",
    "SPAN": "Spanish",
    "SPED": "Special Education",
    "STAT": "Statistics",
    "STS": "Science, Technology and Society",
    "SUSTN": "Sustainability",
    "SWORK": "Social Work",
    "TE": "Teacher Education",
    "TFM": "Television, Film and New Media",
    "THEA": "Theatre",
    "WGSS": "Women's, Gender and Sexuality Studies",
}

_SUBJECT_SEO_SLUG_RE = re.compile(r"[^a-z0-9]+")


def subject_display_name(subject: str) -> str:
    """Return a verified long name when available, otherwise the stable SDSU code."""
    normalized = " ".join(normalize_subject_input(subject).split())
    return SUBJECT_SEO_NAMES.get(normalized, normalized)


def subject_slug(subject: str) -> str:
    """Return the stable canonical subject slug used by public SEO routes."""
    normalized = " ".join(normalize_subject_input(subject).split())
    label = SUBJECT_SEO_NAMES.get(normalized, normalized)
    return _SUBJECT_SEO_SLUG_RE.sub("-", label.casefold()).strip("-")
'''.lstrip("\n")

REPOSITORY_METHOD = r'''
    def displayed_options(
        self,
        *,
        term: str | None = None,
        subject: str | None = None,
        course_code: str | None = None,
    ) -> tuple[CourseSection, ...]:
        """Return exact, read-only enrollment options using the authoritative grouping model.

        This is intentionally a projection of ``_grouped_sections`` rather than a second
        grouping implementation. SEO pages and other read-only surfaces can therefore
        consume the same linked-component and orphan-suppression behavior as class search.
        """
        normalized_term = term.strip() if term is not None else None
        normalized_subject = (
            " ".join(subject.strip().upper().split()) if subject is not None else None
        )
        normalized_course_code = (
            " ".join(course_code.strip().upper().split())
            if course_code is not None
            else None
        )
        matched = [
            section
            for section in self._sections
            if (normalized_term is None or section.term == normalized_term)
            and (
                normalized_subject is None
                or " ".join(section.subject.strip().upper().split()) == normalized_subject
            )
            and (
                normalized_course_code is None
                or " ".join(section.course_code.strip().upper().split())
                == normalized_course_code
            )
        ]
        grouped = self._grouped_sections(matched)
        return tuple(sort_sections(grouped, SearchFilters().sort_by))

'''

MAIN_IMPORT = "from classcatalog.seo import SeoCatalog, SeoRenderer, build_sitemap_xml\n"
MAIN_INIT = "    seo_catalog = SeoCatalog.from_repository(active_repository)\n    seo_renderer = SeoRenderer()\n"

MAIN_ROUTES = r'''
    @app.get("/subjects", include_in_schema=False)
    async def subjects_page() -> Response:
        return Response(
            content=seo_renderer.render_subject_index(seo_catalog),
            media_type="text/html",
        )

    @app.get("/subjects/{slug}", include_in_schema=False)
    async def subject_page(slug: str) -> Response:
        subject = seo_catalog.subject_by_slug(slug.casefold())
        if subject is None:
            raise HTTPException(status_code=404, detail="Subject not found.")
        if slug != subject.slug:
            return Response(
                status_code=308,
                headers={"Location": f"/subjects/{subject.slug}"},
            )
        return Response(
            content=seo_renderer.render_subject(seo_catalog, subject),
            media_type="text/html",
        )

    @app.get("/courses/{slug}", include_in_schema=False)
    async def course_page(slug: str) -> Response:
        course = seo_catalog.course_by_slug(slug.casefold())
        if course is None or seo_catalog.primary_term is None:
            raise HTTPException(status_code=404, detail="Course not found.")
        if slug != course.slug:
            return Response(
                status_code=308,
                headers={"Location": f"/courses/{course.slug}"},
            )

        options = active_repository.displayed_options(
            term=seo_catalog.primary_term,
            course_code=course.course_code,
        )
        if not options:
            raise HTTPException(status_code=404, detail="Course not found.")

        if active_seat_service is not None:
            schedules: list[str] = []
            for option in options:
                schedules.append(option.schedule_number)
                schedules.extend(component.schedule_number for component in option.linked_components)
            active_seat_service.register_interest(tuple(schedules))

        return Response(
            content=seo_renderer.render_course(seo_catalog, course, options),
            media_type="text/html",
        )

'''


def _read(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Required repository file not found: {path}")
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _replace_once(path: Path, old: str, new: str, *, already: str | None = None) -> None:
    text = _read(path)
    if already is not None and already in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one patch anchor in {path}; found {count}: {old[:80]!r}")
    _write(path, text.replace(old, new, 1))


def _append_once(path: Path, marker: str, block: str) -> None:
    text = _read(path)
    if marker in text:
        return
    if not text.endswith("\n"):
        text += "\n"
    _write(path, text + "\n" + block.rstrip() + "\n")


def _copy_tree(package_root: Path, repo: Path) -> None:
    source = package_root / "files"
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def _git_head(repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def apply(repo: Path, package_root: Path) -> None:
    repo = repo.resolve()
    pyproject = repo / "pyproject.toml"
    main = repo / "src" / "classcatalog" / "main.py"
    repository = repo / "src" / "classcatalog" / "repository.py"
    subjects = repo / "src" / "classcatalog" / "subjects.py"
    test_seo = repo / "tests" / "test_seo.py"

    for path in (pyproject, main, repository, subjects, test_seo):
        _read(path)

    head = _git_head(repo)
    if head and head != BASELINE_COMMIT:
        print(
            f"NOTE: repository HEAD is {head[:12]}, while this patch was prepared from "
            f"{BASELINE_COMMIT[:12]}. Anchor checks will protect against incompatible edits."
        )

    _replace_once(
        pyproject,
        '  "httpx>=0.28,<1",\n',
        '  "httpx>=0.28,<1",\n  "jinja2>=3.1,<4",\n',
        already='"jinja2>=3.1,<4"',
    )

    _replace_once(
        subjects,
        "from __future__ import annotations\n\nfrom typing import Final\n",
        "from __future__ import annotations\n\nimport re\nfrom typing import Final\n",
        already="import re\nfrom typing import Final",
    )
    _append_once(subjects, "SUBJECT_SEO_NAMES:", SUBJECT_SEO_BLOCK)

    _replace_once(
        repository,
        "    @staticmethod\n    def _physical_key(section: CourseSection) -> tuple[str, str]:\n",
        REPOSITORY_METHOD
        + "    @staticmethod\n    def _physical_key(section: CourseSection) -> tuple[str, str]:\n",
        already="    def displayed_options(\n",
    )

    _replace_once(
        main,
        "from classcatalog.seats import SeatRefreshService, seat_refresh_env_enabled\n",
        MAIN_IMPORT + "from classcatalog.seats import SeatRefreshService, seat_refresh_env_enabled\n",
        already=MAIN_IMPORT.strip(),
    )
    _replace_once(
        main,
        "    active_repository = course_repository or default_repository\n",
        "    active_repository = course_repository or default_repository\n" + MAIN_INIT,
        already="    seo_catalog = SeoCatalog.from_repository(active_repository)\n",
    )
    _replace_once(
        main,
        '    @app.get("/sitemap.xml", include_in_schema=False)\n'
        '    async def sitemap_xml() -> FileResponse:\n'
        '        return FileResponse(STATIC_DIR / "sitemap.xml", media_type="application/xml")\n\n',
        '    @app.get("/sitemap.xml", include_in_schema=False)\n'
        '    async def sitemap_xml() -> Response:\n'
        '        return Response(content=build_sitemap_xml(seo_catalog), media_type="application/xml")\n\n',
        already="return Response(content=build_sitemap_xml(seo_catalog)",
    )
    dynamic_sitemap_anchor = (
        '    @app.get("/sitemap.xml", include_in_schema=False)\n'
        '    async def sitemap_xml() -> Response:\n'
        '        return Response(content=build_sitemap_xml(seo_catalog), media_type="application/xml")\n\n'
        '    @app.get("/", include_in_schema=False)\n'
        '    async def index() -> FileResponse:\n'
    )
    _replace_once(
        main,
        dynamic_sitemap_anchor,
        dynamic_sitemap_anchor.replace(
            '    @app.get("/", include_in_schema=False)\n    async def index() -> FileResponse:\n',
            MAIN_ROUTES + '    @app.get("/", include_in_schema=False)\n    async def index() -> FileResponse:\n',
        ),
        already='    @app.get("/subjects", include_in_schema=False)',
    )

    sitemap = repo / "src" / "classcatalog" / "static" / "sitemap.xml"
    if sitemap.exists():
        sitemap.unlink()

    footer_edits = {
        repo / "src" / "classcatalog" / "static" / "index.html": (
            '      <a href="#about">About</a>\n      <a href="/privacy">Privacy</a>',
            '      <a href="#about">About</a>\n      <a href="/subjects">Browse Subjects</a>\n      <a href="/privacy">Privacy</a>',
        ),
        repo / "src" / "classcatalog" / "static" / "privacy.html": (
            '      <a href="/">Home</a>\n      <a href="/privacy" aria-current="page">Privacy</a>',
            '      <a href="/">Home</a>\n      <a href="/subjects">Browse Subjects</a>\n      <a href="/privacy" aria-current="page">Privacy</a>',
        ),
        repo / "src" / "classcatalog" / "static" / "terms.html": (
            '      <a href="/">Home</a>\n      <a href="/privacy">Privacy</a>',
            '      <a href="/">Home</a>\n      <a href="/subjects">Browse Subjects</a>\n      <a href="/privacy">Privacy</a>',
        ),
    }
    for path, (old, new) in footer_edits.items():
        _replace_once(path, old, new, already='href="/subjects">Browse Subjects</a>')

    old_urls_assert = '''    assert urls == [\n        "https://classcatalog.cc/",\n        "https://classcatalog.cc/privacy",\n        "https://classcatalog.cc/terms",\n    ]\n'''
    new_urls_assert = '''    assert urls[:4] == [\n        "https://classcatalog.cc/",\n        "https://classcatalog.cc/privacy",\n        "https://classcatalog.cc/terms",\n        "https://classcatalog.cc/subjects",\n    ]\n    assert any(url.startswith("https://classcatalog.cc/subjects/") for url in urls)\n    assert any(url.startswith("https://classcatalog.cc/courses/") for url in urls)\n    assert len(urls) == len(set(urls))\n    assert all("?" not in url and "#" not in url for url in urls)\n'''
    _replace_once(
        test_seo,
        old_urls_assert,
        new_urls_assert,
        already='assert urls[:4] == [',
    )

    _copy_tree(package_root, repo)
    print(f"{MARKER} applied successfully to {repo}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the ClassCatalog SEO landing-page patch.")
    parser.add_argument("--repo", type=Path, required=True, help="Path to the classcatalog repo root")
    args = parser.parse_args()
    apply(args.repo, Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
