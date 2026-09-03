from __future__ import annotations

from classcatalog.catalog.browser import CatalogBrowserConfig, CatalogPageSnapshot
from classcatalog.catalog.parser import CatalogLink
from classcatalog.catalog.scraper import CatalogScrapeConfig, scrape_catalog
from pytest import MonkeyPatch


class _FragmentCatalogBrowser:
    opened_urls: list[str] = []

    def __init__(self, config: CatalogBrowserConfig) -> None:
        del config

    def __enter__(self) -> _FragmentCatalogBrowser:
        type(self).opened_urls = []
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def open(self, url: str) -> CatalogPageSnapshot:
        type(self).opened_urls.append(url)
        if "index.php" in url:
            return CatalogPageSnapshot(
                title="2026-2027 University Catalog",
                url="https://catalog.sdsu.edu/index.php?catoid=12",
                html="<a href='programs.php?catoid=12'>Programs</a>",
            )
        if url == "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884":
            return CatalogPageSnapshot(
                title="General Education Requirements",
                url=url,
                html="""
                <html><body><div class='block_content'>
                  <h2>1B. Critical Thinking</h2>
                  <li class='acalog-course'>
                    <a href='#' onclick="showCourse('12', '88247', this, 'payload');">
                      AFRAS 200 - Intermediate Expository Writing
                    </a>
                  </li>
                  <h2>1C. Oral Communication</h2>
                  <li class='acalog-course'>
                    <a href='#' onclick="showCourse('12', '88248', this, 'payload');">
                      COMM 103 - Oral Communication
                    </a>
                  </li>
                </div></body></html>
                """,
            )
        raise AssertionError(f"Unexpected browser open: {url}")

    def discover_programs(self, *, index_url: str, catoid: int) -> tuple[CatalogLink, ...]:
        del index_url, catoid
        return ()

    def discover_requirement_pages(
        self,
        *,
        index_url: str,
        catoid: int,
        additional_html: object = (),
    ) -> tuple[CatalogLink, ...]:
        del index_url, catoid, additional_html
        base = "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884"
        return (
            CatalogLink(title="1B. Critical Thinking", url=f"{base}#1bcriticalthinking"),
            CatalogLink(title="1C. Oral Communication", url=f"{base}#1coralcommunication"),
        )


def test_requirement_fragments_fetch_underlying_document_once(monkeypatch: MonkeyPatch) -> None:
    from classcatalog.catalog import scraper

    monkeypatch.setattr(scraper, "CatalogBrowser", _FragmentCatalogBrowser)

    mappings = scrape_catalog(
        CatalogScrapeConfig(
            catalog_year="2026-2027",
            catoid=12,
            index_url="https://catalog.sdsu.edu/index.php?catoid=12",
            all_undergraduate_programs=True,
        )
    )

    requirement_url = "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884"
    assert _FragmentCatalogBrowser.opened_urls.count(requirement_url) == 1
    assert {requirement.code for requirement in mappings.requirements} == {"GE 1B", "GE 1C"}


class _AliasCatalogBrowser(_FragmentCatalogBrowser):
    def open(self, url: str) -> CatalogPageSnapshot:
        type(self).opened_urls.append(url)
        if "index.php" in url:
            return CatalogPageSnapshot(
                title="2026-2027 University Catalog",
                url="https://catalog.sdsu.edu/index.php?catoid=12",
                html="<a href='programs.php?catoid=12'>Programs</a>",
            )
        if "poid=11884" in url:
            return CatalogPageSnapshot(
                title="General Education Requirements",
                url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884",
                html="""
                <html><body><div class='block_content'>
                  <h2>1B. Critical Thinking</h2>
                  <li class='acalog-course'>
                    <a href='#' onclick="showCourse('12', '88247', this, 'payload');">
                      AFRAS 200 - Intermediate Expository Writing
                    </a>
                  </li>
                </div></body></html>
                """,
            )
        raise AssertionError(f"Unexpected browser open: {url}")

    def discover_requirement_pages(
        self,
        *,
        index_url: str,
        catoid: int,
        additional_html: object = (),
    ) -> tuple[CatalogLink, ...]:
        del index_url, catoid, additional_html
        return ()


def test_requirement_returnto_aliases_fetch_same_acalog_document_once(
    monkeypatch: MonkeyPatch,
) -> None:
    from classcatalog.catalog import scraper

    monkeypatch.setattr(scraper, "CatalogBrowser", _AliasCatalogBrowser)

    base = "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884"
    mappings = scrape_catalog(
        CatalogScrapeConfig(
            catalog_year="2026-2027",
            catoid=12,
            index_url="https://catalog.sdsu.edu/index.php?catoid=12",
            all_undergraduate_programs=True,
            requirement_urls=(f"{base}&returnto=100", f"{base}&returnto=200"),
        )
    )

    opened_ge_urls = [url for url in _AliasCatalogBrowser.opened_urls if "poid=11884" in url]
    assert opened_ge_urls == [f"{base}&returnto=100"]
    assert {requirement.code for requirement in mappings.requirements} == {"GE 1B"}


class _RedirectAliasCatalogBrowser(_FragmentCatalogBrowser):
    def open(self, url: str) -> CatalogPageSnapshot:
        type(self).opened_urls.append(url)
        if "index.php" in url:
            return CatalogPageSnapshot(
                title="2026-2027 University Catalog",
                url="https://catalog.sdsu.edu/index.php?catoid=12",
                html="<a href='programs.php?catoid=12'>Programs</a>",
            )
        if "navoid=500" in url or "poid=11884" in url:
            return CatalogPageSnapshot(
                title="General Education Requirements",
                url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884",
                html="""
                <html><body><div class='block_content'>
                  <h2>1B. Critical Thinking</h2>
                  <li class='acalog-course'>
                    <a href='#' onclick="showCourse('12', '88247', this, 'payload');">
                      AFRAS 200 - Intermediate Expository Writing
                    </a>
                  </li>
                </div></body></html>
                """,
            )
        raise AssertionError(f"Unexpected browser open: {url}")

    def discover_requirement_pages(
        self,
        *,
        index_url: str,
        catoid: int,
        additional_html: object = (),
    ) -> tuple[CatalogLink, ...]:
        del index_url, catoid, additional_html
        return ()


def test_requirement_redirect_alias_is_reported_once(monkeypatch: MonkeyPatch) -> None:
    from classcatalog.catalog import scraper

    monkeypatch.setattr(scraper, "CatalogBrowser", _RedirectAliasCatalogBrowser)

    mappings = scrape_catalog(
        CatalogScrapeConfig(
            catalog_year="2026-2027",
            catoid=12,
            index_url="https://catalog.sdsu.edu/index.php?catoid=12",
            all_undergraduate_programs=True,
            requirement_urls=(
                "https://catalog.sdsu.edu/content.php?catoid=12&navoid=500",
                "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884",
            ),
        )
    )

    requirement_sources = [source for source in mappings.sources if source.kind.value == "requirement"]
    assert len(requirement_sources) == 1
    assert requirement_sources[0].url == (
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884"
    )
    assert {requirement.code for requirement in mappings.requirements} == {"GE 1B"}
