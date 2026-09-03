from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from classcatalog.catalog import browser as browser_module
from classcatalog.catalog.browser import (
    CatalogBrowser,
    CatalogBrowserConfig,
    CatalogPageSnapshot,
    _target_was_closed,
)
from classcatalog.catalog.main import _parser


class _FakeBrowser:
    def is_connected(self) -> bool:
        return True


class _FakeContext:
    def __init__(self, pages: list[Any]) -> None:
        self.pages = pages

    def new_page(self) -> Any:
        page = _FakePage("about:blank", self)
        self.pages.append(page)
        return page


class _FakePage:
    def __init__(self, url: str, context: _FakeContext | None = None) -> None:
        self.url = url
        self._closed = False
        self.context = context
        self.goto_callback: Any = None

    def set_default_timeout(self, value: int) -> None:
        del value

    def set_default_navigation_timeout(self, value: int) -> None:
        del value

    def is_closed(self) -> bool:
        return self._closed

    def goto(self, url: str, *, wait_until: str) -> Any:
        del wait_until
        self.url = url
        if self.goto_callback is not None:
            return self.goto_callback(url)
        return SimpleNamespace(status=200)


def test_target_closed_error_detection_covers_playwright_message() -> None:
    assert _target_was_closed(
        RuntimeError("Page.wait_for_timeout: Target page, context or browser has been closed")
    )
    assert not _target_was_closed(RuntimeError("ordinary navigation timeout"))


def test_navigation_adopts_replacement_page_without_page_bound_timer(monkeypatch: Any) -> None:
    monkeypatch.setattr(browser_module, "sleep", lambda _: None)
    context = _FakeContext([])
    original = _FakePage("about:blank", context)
    replacement = _FakePage("https://catalog.sdsu.edu/index.php?catoid=12", context)
    context.pages.append(original)

    def replace_page(url: str) -> Any:
        del url
        original._closed = True
        context.pages.append(replacement)
        return SimpleNamespace(status=403)

    original.goto_callback = replace_page
    catalog = CatalogBrowser(CatalogBrowserConfig(navigation_settle_ms=750))
    catalog._context = context  # type: ignore[assignment]
    catalog._browser = _FakeBrowser()  # type: ignore[assignment]
    catalog._page = original  # type: ignore[assignment]

    page, status = catalog._navigate_once("https://catalog.sdsu.edu/index.php?catoid=12")

    assert page is replacement
    assert status == 403


def test_pause_for_human_is_proactive_before_catalog_wait(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(
        CatalogBrowserConfig(headless=False, pause_for_human=True, navigation_settle_ms=0)
    )
    calls: list[tuple[str, object]] = []
    first_page = object()
    verified_page = object()

    def navigate(url: str, *, settle: bool = True) -> tuple[object, int]:
        calls.append(("navigate_settle", settle))
        return first_page, 403

    def pause(url: str) -> tuple[object, int]:
        calls.append(("pause", url))
        catalog._human_pause_completed = True
        return verified_page, 200

    monkeypatch.setattr(catalog, "_navigate_once", navigate)
    monkeypatch.setattr(catalog, "_pause_for_human_verification", pause)
    monkeypatch.setattr(
        catalog,
        "_wait_for_catalog_content",
        lambda **_: verified_page,
    )
    monkeypatch.setattr(catalog, "_challenge_present", lambda **_: False)
    monkeypatch.setattr(
        catalog,
        "_snapshot_current_page",
        lambda **_: CatalogPageSnapshot(
            title="SDSU Catalog",
            url="https://catalog.sdsu.edu/index.php?catoid=12",
            html="<html></html>",
        ),
    )
    monkeypatch.setattr(catalog, "_save_storage_state", lambda: None)

    snapshot = catalog.open("https://catalog.sdsu.edu/index.php?catoid=12")

    assert snapshot.title == "SDSU Catalog"
    assert calls[0] == ("navigate_settle", False)
    assert calls[1][0] == "pause"


def test_catalog_cli_accepts_edge_channel_and_recovery_count() -> None:
    args = _parser().parse_args(
        ["--browser-channel", "msedge", "--page-recovery-attempts", "4"]
    )
    assert args.browser_channel == "msedge"
    assert args.page_recovery_attempts == 4


def test_pause_for_human_keeps_visible_catalog_page_without_renavigation(monkeypatch: Any) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "")
    catalog = CatalogBrowser(
        CatalogBrowserConfig(headless=False, pause_for_human=True, navigation_settle_ms=0)
    )
    context = _FakeContext([])
    visible = _FakePage("https://catalog.sdsu.edu/index.php", context)
    context.pages.append(visible)
    catalog._context = context  # type: ignore[assignment]
    catalog._browser = _FakeBrowser()  # type: ignore[assignment]
    catalog._page = visible  # type: ignore[assignment]

    calls: list[str] = []

    def forbidden_navigation(url: str, *, settle: bool = True) -> tuple[object, int]:
        del settle
        calls.append(url)
        raise AssertionError("the verified/catalog page must not be forcibly reloaded")

    monkeypatch.setattr(catalog, "_navigate_once", forbidden_navigation)
    monkeypatch.setattr(catalog, "_challenge_present", lambda **_: False)
    monkeypatch.setattr(catalog, "_save_storage_state", lambda: None)

    page, status = catalog._pause_for_human_verification(
        "https://catalog.sdsu.edu/index.php?catoid=12"
    )

    assert page is visible
    assert status is None
    assert calls == []


def test_program_discovery_follows_program_listing_pagination(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/index.php": CatalogPageSnapshot(
            title="Catalog",
            url="https://catalog.sdsu.edu/index.php",
            html="<a href='/content.php?catoid=12&navoid=1153'>Programs by Campus Location</a>",
        ),
        "https://catalog.sdsu.edu/programs.php?catoid=12": CatalogPageSnapshot(
            title="Programs",
            url="https://catalog.sdsu.edu/programs.php?catoid=12",
            html="""
              <a href='preview_program.php?catoid=12&poid=1'>Computer Science, B.S.</a>
              <a href='programs.php?catoid=12&page=2'>Next</a>
            """,
        ),
        "https://catalog.sdsu.edu/programs.php?catoid=12&page=2": CatalogPageSnapshot(
            title="Programs page 2",
            url="https://catalog.sdsu.edu/programs.php?catoid=12&page=2",
            html="<a href='preview_program.php?catoid=12&poid=2'>Mathematics, B.S.</a>",
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1153": CatalogPageSnapshot(
            title="Programs by Campus Location",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1153",
            html="<a href='preview_program.php?catoid=12&poid=3'>Nursing, B.S.</a>",
        ),
    }

    def open_page(url: str) -> CatalogPageSnapshot:
        return pages[url]

    monkeypatch.setattr(catalog, "open", open_page)
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [link.title for link in links] == [
        "Computer Science, B.S.",
        "Mathematics, B.S.",
        "Nursing, B.S.",
    ]


def test_program_discovery_follows_sdsu_curricula_navigation(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/index.php": CatalogPageSnapshot(
            title="Catalog",
            url="https://catalog.sdsu.edu/index.php",
            html="""
              <a href='/content.php?catoid=12&navoid=1120'>Summary of Curricula Offered</a>
              <a href='/content.php?catoid=12&navoid=1122'>Curricula by Department</a>
              <a href='/content.php?catoid=12&navoid=1153'>Programs by Campus Location</a>
            """,
        ),
        "https://catalog.sdsu.edu/programs.php?catoid=12": CatalogPageSnapshot(
            title="Programs",
            url="https://catalog.sdsu.edu/programs.php?catoid=12",
            html="",
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1120": CatalogPageSnapshot(
            title="Summary of Curricula Offered",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1120",
            html="<a href='preview_program.php?catoid=12&poid=10'>Biology, B.S.</a>",
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1122": CatalogPageSnapshot(
            title="Curricula by Department",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1122",
            html="<a href='preview_program.php?catoid=12&poid=11'>Computer Science, B.S.</a>",
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1153": CatalogPageSnapshot(
            title="Programs by Campus Location",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1153",
            html="<a href='preview_program.php?catoid=12&poid=12'>Nursing, B.S.</a>",
        ),
    }

    monkeypatch.setattr(catalog, "open", lambda url: pages[url])
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [link.title for link in links] == ["Biology, B.S."]


def test_program_discovery_follows_department_children_inside_curricula_hub(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/index.php": CatalogPageSnapshot(
            title="Catalog",
            url="https://catalog.sdsu.edu/index.php",
            html="<a href='/content.php?catoid=12&navoid=1122'>Curricula by Department</a>",
        ),
        "https://catalog.sdsu.edu/programs.php?catoid=12": CatalogPageSnapshot(
            title="Programs",
            url="https://catalog.sdsu.edu/programs.php?catoid=12",
            html="",
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1122": CatalogPageSnapshot(
            title="Curricula by Department",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1122",
            html="""
              <a href='/content.php?catoid=12&navoid=1200'>Biology</a>
              <a href='/content.php?catoid=12&navoid=1201'>Computer Science</a>
            """,
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1200": CatalogPageSnapshot(
            title="Biology",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1200",
            html="<a href='preview_program.php?catoid=12&poid=20'>Biology, B.S.</a>",
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1201": CatalogPageSnapshot(
            title="Computer Science",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1201",
            html="<a href='preview_program.php?catoid=12&poid=21'>Computer Science, B.S.</a>",
        ),
    }

    monkeypatch.setattr(catalog, "open", lambda url: pages[url])
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [link.title for link in links] == ["Biology, B.S.", "Computer Science, B.S."]


def test_program_discovery_follows_preview_entity_department_pages(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/index.php": CatalogPageSnapshot(
            title="Catalog",
            url="https://catalog.sdsu.edu/index.php",
            html="<a href='/content.php?catoid=12&navoid=1122'>Curricula by Department</a>",
        ),
        "https://catalog.sdsu.edu/programs.php?catoid=12": CatalogPageSnapshot(
            title="Programs",
            url="https://catalog.sdsu.edu/programs.php?catoid=12",
            html="",
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1122": CatalogPageSnapshot(
            title="Curricula by Department",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1122",
            html="""
              <div class='block_content'>
                <a href='/preview_entity.php?catoid=12&ent_oid=55'>Biology</a>
              </div>
              <div class='block_n2'>
                <a href='/content.php?catoid=12&navoid=9999'>Unrelated Global Navigation</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_entity.php?catoid=12&ent_oid=55": CatalogPageSnapshot(
            title="Biology",
            url="https://catalog.sdsu.edu/preview_entity.php?catoid=12&ent_oid=55",
            html="""<div class='block_content'>
              <a href='preview_program.php?catoid=12&poid=30'>Biology, BS</a>
            </div>""",
        ),
    }

    monkeypatch.setattr(catalog, "open", lambda url: pages[url])
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [link.title for link in links] == ["Biology, BS"]


def test_program_discovery_prefers_roadmap_index_and_resolves_canonical_programs(
    monkeypatch: Any,
) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184": CatalogPageSnapshot(
            title="Undergraduate Academic Roadmaps",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=900'>
                  Aerospace Engineering, B.S. - 4 Year Roadmap
                </a>
                <a href='preview_program.php?catoid=12&poid=901'>
                  Computer Science, B.S. Roadmap
                </a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900": CatalogPageSnapshot(
            title="Aerospace Engineering Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900",
            html="""
              <div class='block_content'>
                <p><a href='preview_program.php?catoid=12&poid=10'>Aerospace Engineering, B.S.</a></p>
                <a href='preview_program.php?catoid=12&poid=999'>General Education Requirements</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=901": CatalogPageSnapshot(
            title="Computer Science Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=901",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=11'>Computer Science, B.S.</a>
              </div>
            """,
        ),
    }

    monkeypatch.setattr(catalog, "open", lambda url: pages[url])
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        (
            "Aerospace Engineering, B.S.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=10",
        ),
        (
            "Computer Science, B.S.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11",
        ),
    ]


def test_roadmap_discovery_uses_single_generic_program_requirements_link(
    monkeypatch: Any,
) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184": CatalogPageSnapshot(
            title="Undergraduate Academic Roadmaps",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=900&returnto=1184'>
                  Humanities, B.A. - Roadmap
                </a>
              </div>
            """,
        ),
        (
            "https://catalog.sdsu.edu/preview_program.php?"
            "catoid=12&poid=900&returnto=1184"
        ): CatalogPageSnapshot(
            title="Humanities Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900&returnto=1184",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=50'>Program Requirements</a>
                <a href='preview_program.php?catoid=12&poid=11884'>
                  General Education Requirements
                </a>
              </div>
            """,
        ),
    }

    monkeypatch.setattr(catalog, "open", lambda url: pages[url])
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        (
            "Humanities, B.A.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=50",
        )
    ]


def test_roadmap_discovery_filters_print_and_undeclared_links(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    opened: list[str] = []
    pages = {
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184": CatalogPageSnapshot(
            title="Undergraduate Academic Roadmaps",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184",
            html="""
              <div class='block_content'>
                <a href='content.php?catoid=12&navoid=1184&print'>Print (opens a new window)</a>
                <a href='preview_program.php?catoid=12&poid=999'>Undeclared - Roadmap</a>
                <a href='preview_program.php?catoid=12&poid=900'>Computer Science, B.S. - Roadmap</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900": CatalogPageSnapshot(
            title="Computer Science Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=10'>Computer Science, B.S.</a>
              </div>
            """,
        ),
    }

    def open_page(url: str) -> CatalogPageSnapshot:
        opened.append(url)
        return pages[url]

    monkeypatch.setattr(catalog, "open", open_page)
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        (
            "Computer Science, B.S.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=10",
        )
    ]
    assert not any("print" in url or "poid=999" in url for url in opened)


def test_roadmap_discovery_does_not_filter_painting_and_printmaking(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184": CatalogPageSnapshot(
            title="Undergraduate Academic Roadmaps",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=900'>
                  Art, Emphasis in Painting and Printmaking, B.A. - Roadmap
                </a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900": CatalogPageSnapshot(
            title="Painting and Printmaking Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=50'>
                  Art, Emphasis in Painting and Printmaking, B.A.
                </a>
              </div>
            """,
        ),
    }

    monkeypatch.setattr(catalog, "open", lambda url: pages[url])
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        (
            "Art, Emphasis in Painting and Printmaking, B.A.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=50",
        )
    ]


def test_roadmap_discovery_recovers_program_url_from_onclick(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184": CatalogPageSnapshot(
            title="Undergraduate Academic Roadmaps",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=900'>Humanities, B.A. - Roadmap</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900": CatalogPageSnapshot(
            title="Humanities Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900",
            html="""
              <div class='block_content'>
                <a href='#' onclick="openProgram('preview_program.php?catoid=12&amp;poid=50')">
                  Program Requirements
                </a>
              </div>
            """,
        ),
    }

    monkeypatch.setattr(catalog, "open", lambda url: pages[url])
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        (
            "Humanities, B.A.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=50",
        )
    ]


def test_roadmap_discovery_inspects_multiple_generic_candidate_targets(
    monkeypatch: Any,
) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184": CatalogPageSnapshot(
            title="Undergraduate Academic Roadmaps",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=900'>Humanities, B.A. - Roadmap</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900": CatalogPageSnapshot(
            title="Humanities Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=50'>Program Requirements</a>
                <a href='preview_program.php?catoid=12&poid=51'>Related Program</a>
                <a href='preview_program.php?catoid=12&poid=11884'>General Education Requirements</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=50": CatalogPageSnapshot(
            title="San Diego State University",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=50",
            html="<div class='block_content'><h1>Humanities, B.A.</h1></div>",
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=51": CatalogPageSnapshot(
            title="San Diego State University",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=51",
            html="<div class='block_content'><h1>Religious Studies, B.A.</h1></div>",
        ),
    }

    monkeypatch.setattr(catalog, "open", lambda url: pages[url])
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        (
            "Humanities, B.A.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=50",
        )
    ]


def test_roadmap_discovery_uses_verified_humanities_requirements_override(
    monkeypatch: Any,
) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184": CatalogPageSnapshot(
            title="Undergraduate Academic Roadmaps",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=12827&returnto=1184'>
                  Humanities, B.A. - Roadmap
                </a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=12827&returnto=1184": CatalogPageSnapshot(
            title="Humanities, B.A. - Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=12827&returnto=1184",
            html="""
              <div class='block_content'>
                <p><span class='acalog-permalink-inactive'>Humanities, B.A.</span></p>
              </div>
            """,
        ),
    }

    def open_page(url: str) -> CatalogPageSnapshot:
        return pages[url]

    monkeypatch.setattr(catalog, "open", open_page)
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        (
            "Humanities, B.A.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=12051",
        )
    ]


def test_roadmap_discovery_recovers_inactive_humanities_via_catalog_navigation(
    monkeypatch: Any,
) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184": CatalogPageSnapshot(
            title="Undergraduate Academic Roadmaps",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=12999&returnto=1184'>
                  Humanities, B.A. - Roadmap
                </a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=12999&returnto=1184": CatalogPageSnapshot(
            title="Humanities, B.A. - Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=12999&returnto=1184",
            html="""
              <div class='block_content'>
                <p><span class='acalog-permalink-inactive'>Humanities, B.A.</span></p>
                <p>The roadmap outlines a recommended sequence of courses.</p>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/index.php": CatalogPageSnapshot(
            title="San Diego State University",
            url="https://catalog.sdsu.edu/index.php",
            html="""
              <div class='block_content'>
                <a href='content.php?catoid=12&navoid=1122'>Curricula by Department</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1122": CatalogPageSnapshot(
            title="Curricula by Department",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1122",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=14000'>
                  Humanities, B.A. in Liberal Arts and Sciences
                </a>
                <a href='preview_program.php?catoid=12&poid=11954'>
                  Classics, Emphasis in Classical Humanities, B.A.
                </a>
              </div>
            """,
        ),
    }

    def open_page(url: str) -> CatalogPageSnapshot:
        if url.endswith("programs.php?catoid=12"):
            raise RuntimeError("not found")
        return pages[url]

    monkeypatch.setattr(catalog, "open", open_page)
    links = catalog._discover_programs_from_roadmaps(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        (
            "Humanities, B.A. in Liberal Arts and Sciences",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=14000",
        )
    ]


def test_program_discovery_unions_roadmaps_with_curricula_summary(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184": CatalogPageSnapshot(
            title="Undergraduate Academic Roadmaps",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1184",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=900'>Computer Science, B.S. - Roadmap</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900": CatalogPageSnapshot(
            title="Computer Science Roadmap",
            url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=900",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=10'>Computer Science, B.S.</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/index.php": CatalogPageSnapshot(
            title="San Diego State University",
            url="https://catalog.sdsu.edu/index.php",
            html="""
              <div class='block_content'>
                <a href='content.php?catoid=12&navoid=1120'>Summary of Curricula Offered</a>
                <a href='content.php?catoid=12&navoid=9999'>Special Programs and Services</a>
              </div>
            """,
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1120": CatalogPageSnapshot(
            title="Summary of Curricula Offered",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1120",
            html="""
              <div class='block_content'>
                <a href='preview_program.php?catoid=12&poid=10&returnto=1120'>Computer Science, B.S.</a>
                <a href='preview_program.php?catoid=12&poid=77&returnto=1120'>Geological Sciences, Emphasis in Engineering Geology, B.S.</a>
                <a href='preview_program.php?catoid=12&poid=900&returnto=1120'>Computer Science, B.S. - Roadmap</a>
                <a href='preview_program.php?catoid=12&poid=91&returnto=1120'>Mechanical Engineering, BS/MS 4+1 Degree</a>
                <a href='preview_program.php?catoid=12&poid=92&returnto=1120'>Engineering, B.S./M.S.</a>
                <a href='preview_program.php?catoid=12&poid=88'>Graduate Example, M.S.</a>
              </div>
            """,
        ),
    }

    opened: list[str] = []

    def open_page(url: str) -> CatalogPageSnapshot:
        opened.append(url)
        return pages[url]

    monkeypatch.setattr(catalog, "open", open_page)
    links = catalog.discover_programs(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        (
            "Computer Science, B.S.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=10",
        ),
        (
            "Geological Sciences, Emphasis in Engineering Geology, B.S.",
            "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=77",
        ),
    ]
    assert "https://catalog.sdsu.edu/content.php?catoid=12&navoid=9999" not in opened


def test_curricula_summary_excludes_roadmaps_and_combined_degree_pages(monkeypatch: Any) -> None:
    catalog = CatalogBrowser(CatalogBrowserConfig())
    pages = {
        "https://catalog.sdsu.edu/index.php": CatalogPageSnapshot(
            title="San Diego State University",
            url="https://catalog.sdsu.edu/index.php",
            html="<a href='content.php?catoid=12&navoid=1120'>Summary of Curricula Offered</a>",
        ),
        "https://catalog.sdsu.edu/content.php?catoid=12&navoid=1120": CatalogPageSnapshot(
            title="Summary of Curricula Offered",
            url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1120",
            html="""
              <a href='preview_program.php?catoid=12&poid=1'>Art History, B.A.</a>
              <a href='preview_program.php?catoid=12&poid=2'>Art History, B.A. - Roadmap</a>
              <a href='preview_program.php?catoid=12&poid=3'>Mechanical Engineering, BS/MS 4+1 Degree</a>
              <a href='preview_program.php?catoid=12&poid=4'>Chemistry, B.S./M.S.</a>
            """,
        ),
    }
    monkeypatch.setattr(catalog, "open", lambda url: pages[url])

    links = catalog._discover_programs_from_catalog_navigation(
        index_url="https://catalog.sdsu.edu/index.php",
        catoid=12,
    )

    assert [(link.title, link.url) for link in links] == [
        ("Art History, B.A.", "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=1")
    ]
