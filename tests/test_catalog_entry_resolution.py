from __future__ import annotations

from classcatalog.catalog.browser import CatalogBrowserConfig, CatalogPageSnapshot
from classcatalog.catalog.scraper import CatalogScrapeConfig, _resolve_catalog_entry


class _FakeCatalogBrowser:
    def __init__(self, snapshots: dict[str, CatalogPageSnapshot]) -> None:
        self.snapshots = snapshots
        self.opened: list[str] = []

    def open(self, url: str) -> CatalogPageSnapshot:
        self.opened.append(url)
        return self.snapshots[url]


def test_resolver_replaces_stale_cli_catoid_from_catalog_home_navigation() -> None:
    home = "https://catalog.sdsu.edu/index.php"
    browser = _FakeCatalogBrowser(
        {
            home: CatalogPageSnapshot(
                title="2026-2027 SDSU Catalog",
                url=home,
                html="""
                    <a href='index.php?catoid=11'>2026-2027 Catalog</a>
                    <a href='programs.php?catoid=11'>Programs</a>
                    <a href='content.php?catoid=11&navoid=100'>Requirements</a>
                """,
            )
        }
    )
    config = CatalogScrapeConfig(
        catalog_year="2026-2027",
        catoid=12,
        index_url=home,
        browser=CatalogBrowserConfig(),
    )

    snapshot, catoid, notes = _resolve_catalog_entry(browser, config)  # type: ignore[arg-type]

    assert snapshot.url == home
    assert catoid == 11
    assert any("Requested catoid 12" in note for note in notes)


def test_resolver_falls_back_to_home_when_explicit_catoid_is_not_found() -> None:
    bad = "https://catalog.sdsu.edu/index.php?catoid=12"
    home = "https://catalog.sdsu.edu/index.php"
    browser = _FakeCatalogBrowser(
        {
            bad: CatalogPageSnapshot(
                title="Resource Not Found",
                url=bad,
                html=(
                    "<h1>Resource Not Found</h1>"
                    "<p>We were unable to locate the resource you attempted to access.</p>"
                ),
            ),
            home: CatalogPageSnapshot(
                title="SDSU Catalog",
                url=home,
                html="""
                    <a href='index.php?catoid=11'>2026-2027 Catalog</a>
                    <a href='programs.php?catoid=11'>Programs</a>
                """,
            ),
        }
    )
    config = CatalogScrapeConfig(
        catalog_year="2026-2027",
        catoid=12,
        index_url=bad,
        browser=CatalogBrowserConfig(),
    )

    snapshot, catoid, notes = _resolve_catalog_entry(browser, config)  # type: ignore[arg-type]

    assert snapshot.url == home
    assert catoid == 11
    assert browser.opened == [bad, home]
    assert any("Resource Not Found" in note for note in notes)
