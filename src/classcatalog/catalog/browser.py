from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from time import sleep
from types import TracebackType
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, Response, sync_playwright

from classcatalog.catalog.parser import (
    CatalogLink,
    extract_catalog_navigation_links,
    extract_program_links,
    is_resource_not_found_page,
    is_undergraduate_degree_program,
    likely_requirement_navigation_link,
)


class CatalogBrowserError(RuntimeError):
    pass


class CatalogChallengeError(CatalogBrowserError):
    pass


# SDSU occasionally publishes a roadmap without a canonical program link. Keep
# verified catalog-specific exceptions narrow and keyed by the roadmap identity so
# they cannot affect another catalog year. Values are (canonical title, program poid).
_VERIFIED_ROADMAP_PROGRAM_OVERRIDES: dict[tuple[int, str], tuple[str, str]] = {
    (12, "12827"): ("Humanities, B.A.", "12051"),
}


@dataclass(frozen=True, slots=True)
class CatalogBrowserConfig:
    headless: bool = True
    timeout_ms: int = 60_000
    pause_for_human: bool = False
    storage_state: Path | None = None
    browser_channel: str | None = None
    page_recovery_attempts: int = 2
    navigation_settle_ms: int = 750


@dataclass(frozen=True, slots=True)
class CatalogPageSnapshot:
    title: str
    url: str
    html: str


def _target_was_closed(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "target page, context or browser has been closed",
            "page has been closed",
            "browser has been closed",
            "context has been closed",
            "target closed",
        )
    )


class CatalogBrowser:
    def __init__(self, config: CatalogBrowserConfig) -> None:
        self._config = config
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._human_pause_completed = False

    def __enter__(self) -> CatalogBrowser:
        self._playwright = sync_playwright().start()
        self._start_runtime()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self._close_runtime(save_state=True)
        if self._playwright is not None:
            with suppress(Exception):
                self._playwright.stop()
        self._playwright = None

    def _context_args(self) -> dict[str, object]:
        context_args: dict[str, object] = {
            "viewport": {"width": 1440, "height": 1000},
            "locale": "en-US",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            ),
        }
        if self._config.storage_state is not None and self._config.storage_state.is_file():
            context_args["storage_state"] = str(self._config.storage_state)
        return context_args

    def _start_runtime(self) -> None:
        if self._playwright is None:
            raise CatalogBrowserError("Playwright is not running.")
        launch_args: dict[str, object] = {"headless": self._config.headless}
        if self._config.browser_channel:
            launch_args["channel"] = self._config.browser_channel
        try:
            self._browser = self._playwright.chromium.launch(**launch_args)
            self._context = self._browser.new_context(**self._context_args())
            self._context.on("page", self._on_context_page)
            self._page = self._context.new_page()
            self._configure_page(self._page)
        except Exception as exc:
            self._close_runtime(save_state=False)
            channel = self._config.browser_channel or "bundled Chromium"
            raise CatalogBrowserError(
                f"Could not start the catalog browser using {channel!r}: {exc}"
            ) from exc

    def _close_runtime(self, *, save_state: bool) -> None:
        if save_state:
            self._save_storage_state()
        if self._context is not None:
            with suppress(Exception):
                self._context.close()
        if self._browser is not None:
            with suppress(Exception):
                self._browser.close()
        self._page = None
        self._context = None
        self._browser = None

    def _restart_runtime(self) -> None:
        self._close_runtime(save_state=False)
        self._start_runtime()

    def _save_storage_state(self) -> None:
        if self._context is None or self._config.storage_state is None:
            return
        with suppress(Exception):
            self._config.storage_state.parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(self._config.storage_state))

    def _configure_page(self, page: Page) -> None:
        page.set_default_timeout(self._config.timeout_ms)
        page.set_default_navigation_timeout(self._config.timeout_ms)

    def _on_context_page(self, page: Page) -> None:
        self._configure_page(page)
        self._page = page

    def _live_pages(self) -> tuple[Page, ...]:
        if self._context is None:
            return ()
        try:
            pages = tuple(self._context.pages)
        except Exception:
            return ()
        live: list[Page] = []
        for page in pages:
            with suppress(Exception):
                if not page.is_closed():
                    live.append(page)
        return tuple(live)

    def _adopt_live_page(self, *, preferred_url: str | None = None) -> Page | None:
        pages = self._live_pages()
        if not pages:
            return None

        preferred_host = urlparse(preferred_url).netloc.casefold() if preferred_url else ""
        ranked: list[tuple[int, int, Page]] = []
        for index, page in enumerate(pages):
            try:
                url = page.url
            except Exception:
                url = ""
            host = urlparse(url).netloc.casefold()
            score = 0
            if url and url != "about:blank":
                score += 1
            if preferred_host and host == preferred_host:
                score += 4
            if host.endswith("sdsu.edu"):
                score += 2
            ranked.append((score, index, page))
        selected = max(ranked, key=lambda item: (item[0], item[1]))[2]
        self._configure_page(selected)
        self._page = selected
        return selected

    def _ensure_page(self, *, preferred_url: str | None = None) -> Page:
        page = self._adopt_live_page(preferred_url=preferred_url)
        if page is not None:
            return page
        if self._context is None or self._browser is None or not self._browser.is_connected():
            self._restart_runtime()
        if self._context is None:
            raise CatalogBrowserError("Catalog browser context is unavailable.")
        try:
            page = self._context.new_page()
        except Exception:
            self._restart_runtime()
            if self._context is None:
                raise CatalogBrowserError("Catalog browser context could not be restored.")
            page = self._context.new_page()
        self._configure_page(page)
        self._page = page
        return page

    @property
    def page(self) -> Page:
        return self._ensure_page()

    def _challenge_present(self, *, response_status: int | None = None) -> bool:
        if response_status in {401, 403, 429, 503}:
            return True
        page = self._adopt_live_page()
        if page is None:
            return False
        try:
            text = page.locator("body").inner_text(timeout=min(self._config.timeout_ms, 10_000))
        except Exception:
            return False
        folded = text.casefold()
        return any(
            marker in folded
            for marker in (
                "verify that you're not a robot",
                "verify you are human",
                "verify you are a human",
                "checking your browser",
                "enable javascript and then reload",
                "security check",
                "just a moment",
                "pardon our interruption",
                "press and hold",
                "verification required",
                "automated access",
                "access denied",
                "request unsuccessful",
            )
        )

    def _wait_for_catalog_content(self, *, preferred_url: str | None = None) -> Page:
        selectors = (
            "#acalog-content",
            "#acalog-page-content",
            ".acalog-core",
            "main",
            "body",
        )
        last_error: BaseException | None = None
        for selector in selectors:
            page = self._ensure_page(preferred_url=preferred_url)
            try:
                page.locator(selector).first.wait_for(state="attached", timeout=5_000)
                return page
            except Exception as exc:  # Playwright uses several timeout subclasses here.
                last_error = exc
                if _target_was_closed(exc):
                    replacement = self._adopt_live_page(preferred_url=preferred_url)
                    if replacement is not None:
                        continue
        if last_error is not None and _target_was_closed(last_error):
            raise last_error
        return self._ensure_page(preferred_url=preferred_url)

    def _navigate_once(
        self,
        url: str,
        *,
        settle: bool = True,
    ) -> tuple[Page, int | None]:
        page = self._ensure_page(preferred_url=url)
        response: Response | None = None
        try:
            response = page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            if not _target_was_closed(exc):
                raise
            # Verification providers sometimes replace the original tab. Give the new page
            # a moment to register before deciding that navigation really failed.
            sleep(0.25)
            replacement = self._adopt_live_page(preferred_url=url)
            if replacement is None:
                raise
            page = replacement

        if settle and self._config.navigation_settle_ms > 0:
            # Do not use Page.wait_for_timeout here. Acalog's verification flow can close the
            # original tab and open a replacement, which would make a page-bound timer fail.
            sleep(self._config.navigation_settle_ms / 1_000)
        replacement = self._adopt_live_page(preferred_url=url)
        if replacement is None:
            raise CatalogBrowserError(
                "The SDSU catalog closed its browser tab before the page could be read."
            )
        status = response.status if response is not None else None
        return replacement, status

    def _pause_for_human_verification(self, url: str) -> tuple[Page, int | None]:
        prompt_attempts = max(2, self._config.page_recovery_attempts + 1)
        last_error: BaseException | None = None
        for attempt in range(prompt_attempts):
            try:
                input(
                    "A browser window is open. If SDSU only shows a cookie banner, accept the "
                    "cookies; no separate verification is required. If a verification page "
                    "does appear, complete it. Once actual catalog content is visible, press "
                    "Enter here and keep the browser window open..."
                )
            except EOFError as exc:
                raise CatalogChallengeError(
                    "Human verification was requested, but this console cannot read input. "
                    "Run the command from IntelliJ or a normal terminal with --headed "
                    "--pause-for-human."
                ) from exc

            # Verification/cookie flows can redirect to the catalog landing page. Do not
            # immediately force the originally requested URL again: an old Acalog catoid can
            # turn a perfectly usable verified session into a Resource Not Found page. Adopt
            # the page the user is actually looking at and let the scraper resolve the active
            # catalog id from its navigation.
            page = self._adopt_live_page(preferred_url=url)
            if page is None:
                try:
                    self._restart_runtime()
                    page, _ = self._navigate_once(
                        "https://catalog.sdsu.edu/index.php", settle=False
                    )
                except Exception as exc:
                    last_error = exc
                    continue
                print(
                    "The catalog browser had closed, so it was reopened at the SDSU catalog "
                    "home page. Accept cookies or complete any verification, then press Enter "
                    "again."
                )
                continue

            if self._challenge_present(response_status=None):
                if attempt + 1 < prompt_attempts:
                    print(
                        "The SDSU verification page still appears to be open. Complete it, or "
                        "if you only see a cookie banner accept it, then press Enter again."
                    )
                    continue
                raise CatalogChallengeError(
                    "The SDSU catalog verification challenge is still present after the pause."
                )

            self._human_pause_completed = True
            self._save_storage_state()
            print(f"catalog_browser_ready url={page.url!r}")
            return page, None

        detail = str(last_error) if last_error is not None else "the browser window kept closing"
        raise CatalogChallengeError(
            "The SDSU verification browser could not be kept open long enough to continue: "
            f"{detail}. Try the same command with --browser-channel msedge."
        ) from last_error

    def _snapshot_current_page(self, *, preferred_url: str) -> CatalogPageSnapshot:
        page = self._ensure_page(preferred_url=preferred_url)
        try:
            return CatalogPageSnapshot(
                title=page.title(),
                url=page.url,
                html=page.content(),
            )
        except Exception as exc:
            if not _target_was_closed(exc):
                raise
            replacement = self._adopt_live_page(preferred_url=preferred_url)
            if replacement is None:
                raise
            return CatalogPageSnapshot(
                title=replacement.title(),
                url=replacement.url,
                html=replacement.content(),
            )

    def open(self, url: str) -> CatalogPageSnapshot:
        recovery_attempts = max(0, self._config.page_recovery_attempts)
        last_error: BaseException | None = None
        for attempt in range(recovery_attempts + 1):
            try:
                proactive_human_pause = (
                    not self._config.headless
                    and self._config.pause_for_human
                    and not self._human_pause_completed
                )
                page, response_status = self._navigate_once(
                    url,
                    settle=not proactive_human_pause,
                )

                # --pause-for-human is intentionally proactive on the first page. Some browser
                # verification pages replace/close the initial tab before their body text can be
                # inspected, so waiting for marker detection is too late.
                if proactive_human_pause:
                    page, response_status = self._pause_for_human_verification(url)

                page = self._wait_for_catalog_content(preferred_url=url)
                if self._config.navigation_settle_ms > 0:
                    sleep(self._config.navigation_settle_ms / 1_000)
                    page = self._ensure_page(preferred_url=url)

                if self._challenge_present(response_status=response_status):
                    if self._config.headless or not self._config.pause_for_human:
                        raise CatalogChallengeError(
                            "The SDSU catalog presented a browser verification challenge. "
                            "Re-run with --headed --pause-for-human, complete the challenge "
                            "once, and keep the storage-state file for later runs."
                        )
                    page, response_status = self._pause_for_human_verification(url)
                    page = self._wait_for_catalog_content(preferred_url=url)
                    if self._challenge_present(response_status=response_status):
                        raise CatalogChallengeError(
                            "The SDSU catalog verification challenge is still present after "
                            "the pause. Leave the browser open, complete the verification, "
                            "and press Enter only after the actual catalog page is visible."
                        )

                snapshot = self._snapshot_current_page(preferred_url=url)
                self._save_storage_state()
                return snapshot
            except CatalogChallengeError:
                raise
            except Exception as exc:
                last_error = exc
                recoverable = _target_was_closed(exc) or "closed its browser tab" in str(exc)
                if not recoverable or attempt >= recovery_attempts:
                    break
                # Keep a surviving context when only the original tab was closed. Restart the
                # whole runtime only when the browser/context itself is gone.
                if self._adopt_live_page(preferred_url=url) is None:
                    self._restart_runtime()

        detail = str(last_error) if last_error is not None else "unknown browser failure"
        if _target_was_closed(last_error or RuntimeError(detail)):
            detail = (
                f"{detail}. The initial catalog tab was closed before it could be read. "
                "The collector now recovers replacement tabs automatically; if this repeats, "
                "keep the Playwright browser open and try --browser-channel msedge."
            )
        raise CatalogBrowserError(f"Could not open SDSU catalog URL {url!r}: {detail}") from last_error

    @staticmethod
    def _roadmap_link_score(roadmap_title: str, candidate: CatalogLink) -> tuple[int, str]:
        """Rank a roadmap page's links by likelihood of being its canonical program page."""

        def words(value: str) -> set[str]:
            cleaned = value.casefold()
            for phrase in (
                "four year",
                "four-year",
                "4 year",
                "4-year",
                "two year",
                "two-year",
                "2 year",
                "2-year",
                "academic roadmap",
                "roadmap",
                "my map",
                "mymap",
            ):
                cleaned = cleaned.replace(phrase, " ")
            return {token for token in cleaned.replace(",", " ").replace("-", " ").split() if len(token) > 1}

        title_folded = candidate.title.casefold()
        score = 0
        if is_undergraduate_degree_program(candidate.title):
            score += 100
        if any(marker in title_folded for marker in ("roadmap", "road map", "mymap")):
            score -= 100
        roadmap_words = words(roadmap_title)
        candidate_words = words(candidate.title)
        if roadmap_words and candidate_words:
            overlap = len(roadmap_words & candidate_words)
            score += overlap * 10
            if roadmap_words <= candidate_words or candidate_words <= roadmap_words:
                score += 25

        # Subject identity matters more than a coincidental shared word. For example,
        # a Humanities roadmap must prefer "Humanities, B.A. ..." over "Classics,
        # Emphasis in Classical Humanities, B.A.".
        roadmap_base = CatalogBrowser._roadmap_base_title(roadmap_title).casefold()
        candidate_base = candidate.title.casefold()
        roadmap_subject = roadmap_base.split(",", 1)[0].strip()
        candidate_subject = candidate_base.split(",", 1)[0].strip()
        if roadmap_subject and candidate_subject == roadmap_subject:
            score += 60
        elif roadmap_subject and candidate_base.startswith(roadmap_subject + ","):
            score += 50
        return score, candidate.title.casefold()

    _EMBEDDED_PROGRAM_URL_RE = re.compile(
        r"(?P<url>(?:https?://[^\s'\"<>]+/)?preview_program\.php\?[^\s'\"<>,)]+)",
        re.IGNORECASE,
    )

    @staticmethod
    def _is_print_utility_link(link: CatalogLink) -> bool:
        """Identify Acalog's Print control without rejecting academic names.

        A substring check incorrectly filtered the legitimate "Painting and
        Printmaking" roadmap.  The utility is identifiable by its label or a standalone
        ``print`` query flag.
        """

        title = " ".join(link.title.split()).casefold()
        query = urlparse(link.url).query.casefold()
        return title == "print" or title.startswith("print (") or bool(
            re.search(r"(?:^|&)print(?:=|&|$)", query)
        )

    @staticmethod
    def _program_page_identity(url: str) -> tuple[str, str | None, str | None]:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        return (
            parsed.path.casefold(),
            query.get("catoid", [None])[0],
            query.get("poid", [None])[0],
        )

    @staticmethod
    def _verified_roadmap_program_override(
        *,
        roadmap_url: str,
        index_url: str,
        catoid: int,
    ) -> CatalogLink | None:
        _, roadmap_catoid, roadmap_poid = CatalogBrowser._program_page_identity(roadmap_url)
        if roadmap_poid is None or roadmap_catoid not in (None, str(catoid)):
            return None
        override = _VERIFIED_ROADMAP_PROGRAM_OVERRIDES.get((catoid, roadmap_poid))
        if override is None:
            return None
        title, program_poid = override
        url = urljoin(index_url, f"preview_program.php?catoid={catoid}&poid={program_poid}")
        return CatalogLink(title=title, url=url)

    @staticmethod
    def _is_roadmap_title(title: str) -> bool:
        folded = title.casefold()
        return any(marker in folded for marker in ("roadmap", "road map", "mymap"))

    @staticmethod
    def _roadmap_base_title(title: str) -> str:
        cleaned = title
        for pattern in (
            r"\s*[-–—:]?\s*(?:four|4)[ -]?year\s+(?:academic\s+)?roadmap\s*$",
            r"\s*[-–—:]?\s*(?:two|2)[ -]?year\s+(?:academic\s+)?roadmap\s*$",
            r"\s*[-–—:]?\s*(?:academic\s+)?roadmap\s*$",
            r"\s*[-–—:]?\s*mymap\s*$",
        ):
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        return " ".join(cleaned.split()).strip(" -–—:")

    @staticmethod
    def _degree_title_from_snapshot(snapshot: CatalogPageSnapshot) -> str | None:
        """Read a canonical degree title from an opened Acalog program page."""

        soup = BeautifulSoup(snapshot.html, "html.parser")
        seen: set[str] = set()
        for tag in soup.select("h1, h2, h3, .acalog-program-name, .program-name"):
            text = " ".join(tag.get_text(" ", strip=True).split())
            folded = text.casefold()
            if not text or folded in seen:
                continue
            seen.add(folded)
            if (
                is_undergraduate_degree_program(text)
                and not CatalogBrowser._is_roadmap_title(text)
            ):
                return text

        # Some Acalog themes put the program name only in the document title.  Test
        # each title segment rather than accepting the full institutional suffix.
        for part in re.split(r"\s+(?:[-–—|])\s+", snapshot.title):
            text = " ".join(part.split())
            if (
                is_undergraduate_degree_program(text)
                and not CatalogBrowser._is_roadmap_title(text)
            ):
                return text
        return None

    def _recover_roadmap_programs_from_catalog_navigation(
        self,
        *,
        index_url: str,
        catoid: int,
        unresolved: list[CatalogLink],
        max_listing_pages: int = 80,
    ) -> dict[str, CatalogLink]:
        """Recover roadmap programs whose roadmap page has no canonical link.

        SDSU occasionally publishes a roadmap whose degree label is rendered as plain
        text (for example ``<span class="acalog-permalink-inactive">``) rather than
        a link to the canonical requirements page.  In that case, search the catalog's
        program/curricula navigation and require a strong title match before accepting a
        replacement.  This keeps the recovery generic across catalog years and avoids
        parsing the roadmap itself as if it contained the full degree requirements.
        """

        if not unresolved:
            return {}

        try:
            index = self.open(index_url)
        except CatalogChallengeError:
            raise
        except Exception:
            return {}

        targets = {item.url: item for item in unresolved}
        best: dict[str, tuple[int, CatalogLink]] = {}
        candidates: deque[tuple[str, int]] = deque()

        # Prefer the catalog's named program/curricula hubs.  They are much smaller and
        # safer than crawling every content page in the catalog.
        for link in extract_catalog_navigation_links(
            index.html,
            base_url=index.url,
            catoid=catoid,
        ):
            title = link.title.casefold()
            if any(keyword in title for keyword in ("program", "degree", "curricula", "curriculum")):
                candidates.append((link.url, 4))

        # Some catalog themes do not expose programs.php on the index, but it is cheap
        # and safe to probe as an additional program registry page.
        candidates.appendleft((urljoin(index_url, f"programs.php?catoid={catoid}"), 0))

        attempted: set[str] = set()
        while candidates and len(attempted) < max(1, max_listing_pages):
            url, content_depth = candidates.popleft()
            if url in attempted:
                continue
            attempted.add(url)
            try:
                snapshot = self.open(url)
            except CatalogChallengeError:
                raise
            except Exception:
                continue

            for program in extract_program_links(
                snapshot.html,
                base_url=snapshot.url,
                catoid=catoid,
            ):
                if self._is_roadmap_title(program.title):
                    continue
                for roadmap_url, roadmap in targets.items():
                    score, _ = self._roadmap_link_score(roadmap.title, program)
                    current = best.get(roadmap_url)
                    if score >= 125 and (current is None or score > current[0]):
                        best[roadmap_url] = (score, program)

            # Stop early once every unresolved roadmap has a strong match.
            if len(best) == len(targets):
                break

            for link in extract_catalog_navigation_links(
                snapshot.html,
                base_url=snapshot.url,
                catoid=catoid,
                main_content_only=True,
            ):
                parsed = urlparse(link.url)
                path = parsed.path.casefold()
                title = link.title.casefold()
                if path.endswith("/programs.php") or path == "programs.php":
                    candidates.append((link.url, content_depth))
                    continue

                is_content_page = path.endswith("/content.php") or path == "content.php"
                is_entity_page = (
                    path.endswith("/preview_entity.php") or path == "preview_entity.php"
                )
                if not (is_content_page or is_entity_page):
                    continue

                named_hub = any(
                    keyword in title for keyword in ("program", "degree", "curricula", "curriculum")
                )
                if named_hub:
                    candidates.append((link.url, max(content_depth, 4)))
                elif content_depth > 0:
                    candidates.append((link.url, content_depth - 1))

        recovered = {roadmap_url: value[1] for roadmap_url, value in best.items()}
        print(
            "catalog_roadmap_recovery "
            f"strategy='catalog_navigation' unresolved={len(unresolved)} "
            f"listing_pages={len(attempted)} recovered={len(recovered)}"
        )
        return recovered

    def _discover_programs_from_roadmaps(
        self,
        *,
        index_url: str,
        catoid: int,
        roadmap_navoid: int = 1184,
        max_roadmap_pages: int = 260,
    ) -> tuple[CatalogLink, ...]:
        """Resolve canonical program pages through SDSU's undergraduate roadmap index.

        The roadmap index is a much stronger source of truth than generic Acalog
        navigation: each roadmap represents an undergraduate academic plan, and the
        roadmap page links back to the canonical program requirements page near the top.
        """

        parsed_index = urlparse(index_url)
        scheme = parsed_index.scheme or "https"
        host = parsed_index.netloc or "catalog.sdsu.edu"
        roadmap_index_url = (
            f"{scheme}://{host}/content.php?catoid={catoid}&navoid={roadmap_navoid}"
        )
        try:
            roadmap_index = self.open(roadmap_index_url)
        except CatalogChallengeError:
            raise
        except Exception:
            return ()

        if is_resource_not_found_page(roadmap_index.html, title=roadmap_index.title):
            return ()

        index_links = extract_catalog_navigation_links(
            roadmap_index.html,
            base_url=roadmap_index.url,
            catoid=catoid,
            main_content_only=True,
        )
        roadmap_links: list[CatalogLink] = []
        seen_roadmaps: set[str] = set()
        for link in index_links:
            parsed = urlparse(link.url)
            path = parsed.path.casefold()
            title_folded = link.title.casefold()
            # The roadmap index includes utility and non-plan links alongside actual
            # roadmaps.  Do not spend a browser navigation on Print or Undeclared.
            if self._is_print_utility_link(link):
                continue
            if "undeclared" in title_folded:
                continue
            if not any(
                path.endswith(suffix)
                for suffix in ("/preview_program.php", "/content.php", "/preview_entity.php")
            ) and path not in {"preview_program.php", "content.php", "preview_entity.php"}:
                continue
            if link.url == roadmap_index.url or link.url in seen_roadmaps:
                continue
            seen_roadmaps.add(link.url)
            roadmap_links.append(link)

        discovered: dict[str, CatalogLink] = {}
        opened = 0
        unresolved = 0
        unresolved_links: list[CatalogLink] = []
        unresolved_examples: list[tuple[str, str]] = []
        for roadmap_link in roadmap_links[: max(1, max_roadmap_pages)]:
            try:
                roadmap = self.open(roadmap_link.url)
            except CatalogChallengeError:
                raise
            except Exception:
                unresolved += 1
                continue
            opened += 1

            # Some SDSU catalog releases contain a verified roadmap-to-program mismatch
            # that cannot be inferred from the roadmap HTML itself. Apply only an exact
            # catoid + roadmap-poid override, then leave all other roadmaps on the generic
            # discovery path. Humanities 2026-2027 is one such case: roadmap poid 12827
            # has no program link, while the full requirements live at program poid 12051.
            verified_override = self._verified_roadmap_program_override(
                roadmap_url=roadmap.url,
                index_url=index_url,
                catoid=catoid,
            )
            if verified_override is not None:
                discovered[verified_override.url] = verified_override
                print(
                    "catalog_roadmap_recovery "
                    "strategy='verified_override' "
                    f"roadmap={roadmap_link.title!r} program={verified_override.title!r} "
                    f"url={verified_override.url!r}"
                )
                continue

            def collect_candidates(*, main_content_only: bool) -> list[CatalogLink]:
                result: list[CatalogLink] = []
                roadmap_identity = self._program_page_identity(roadmap.url)
                for candidate in extract_catalog_navigation_links(
                    roadmap.html,
                    base_url=roadmap.url,
                    catoid=catoid,
                    main_content_only=main_content_only,
                ):
                    parsed = urlparse(candidate.url)
                    path = parsed.path.casefold()
                    if not (
                        path.endswith("/preview_program.php")
                        or path == "preview_program.php"
                    ):
                        continue
                    if self._program_page_identity(candidate.url) == roadmap_identity:
                        continue
                    result.append(candidate)

                # Some SDSU roadmap pages keep the canonical program URL inside an
                # onclick/script string rather than a navigable href.  Recover those
                # URLs and let the later target-page inspection determine the real
                # degree title. This is especially useful for the Humanities roadmap.
                for match in self._EMBEDDED_PROGRAM_URL_RE.finditer(roadmap.html):
                    raw_url = unescape(match.group("url"))
                    candidate_url = urljoin(roadmap.url, raw_url)
                    if self._program_page_identity(candidate_url) == roadmap_identity:
                        continue
                    parsed_candidate = urlparse(candidate_url)
                    candidate_catoid = parse_qs(parsed_candidate.query).get("catoid", [None])[0]
                    if candidate_catoid not in (None, str(catoid)):
                        continue
                    result.append(
                        CatalogLink(title="Program Requirements", url=candidate_url)
                    )

                by_url: dict[str, CatalogLink] = {}
                for item in result:
                    # Normal anchors carry a meaningful label and are appended before
                    # raw script-string fallbacks. Preserve that richer title when the
                    # same URL appears in both forms.
                    by_url.setdefault(item.url, item)
                return list(by_url.values())

            candidates = collect_candidates(main_content_only=True)

            def best_positive_candidate(items: list[CatalogLink]) -> CatalogLink | None:
                if not items:
                    return None
                ranked = sorted(
                    items,
                    key=lambda item: self._roadmap_link_score(roadmap_link.title, item),
                    reverse=True,
                )
                for item in ranked:
                    score, _ = self._roadmap_link_score(roadmap_link.title, item)
                    if score > 0 and not self._is_roadmap_title(item.title):
                        return item
                return None

            candidate = best_positive_candidate(candidates)
            if candidate is None:
                # A small number of SDSU roadmap pages place the program link outside
                # the normal main-content wrapper. Search the complete document only
                # after the safer main-content pass fails.
                all_candidates = collect_candidates(main_content_only=False)
                by_url = {item.url: item for item in [*candidates, *all_candidates]}
                candidates = list(by_url.values())
                candidate = best_positive_candidate(candidates)

            if candidate is not None:
                discovered[candidate.url] = candidate
                continue

            # Some roadmaps label the sole canonical link generically (for example,
            # "Program Requirements") rather than repeating the degree title. If there
            # is exactly one non-roadmap, non-requirement program link, use the roadmap's
            # own degree label for that URL instead of discarding the plan.
            plausible = [
                item
                for item in candidates
                if not self._is_roadmap_title(item.title)
                and not likely_requirement_navigation_link(item)
            ]
            if len(plausible) == 1:
                base_title = self._roadmap_base_title(roadmap_link.title)
                title = (
                    base_title
                    if is_undergraduate_degree_program(base_title)
                    else plausible[0].title
                )
                discovered[plausible[0].url] = CatalogLink(title=title, url=plausible[0].url)
                continue

            # A few roadmaps expose several generically labelled program links.  Open
            # those candidates only after normal title matching fails and inspect the
            # target page's real degree heading.  This resolves pages such as Humanities
            # without hard-coding a catalog-year-specific poid.
            resolved_candidate: CatalogLink | None = None
            resolved_score = 100  # Degree detection alone is worth 100; require title overlap.
            for item in sorted(
                plausible,
                key=lambda value: self._roadmap_link_score(roadmap_link.title, value),
                reverse=True,
            )[:12]:
                try:
                    target = self.open(item.url)
                except CatalogChallengeError:
                    raise
                except Exception:
                    continue
                actual_title = self._degree_title_from_snapshot(target)
                if actual_title is None:
                    continue
                actual = CatalogLink(title=actual_title, url=target.url)
                score, _ = self._roadmap_link_score(roadmap_link.title, actual)
                if score > resolved_score:
                    resolved_candidate = actual
                    resolved_score = score
            if resolved_candidate is not None:
                discovered[resolved_candidate.url] = resolved_candidate
                continue

            # Some catalog years may list the canonical program directly rather than
            # wrapping it in a separate roadmap page. Preserve that as a safe fallback.
            if (
                is_undergraduate_degree_program(roadmap_link.title)
                and not self._is_roadmap_title(roadmap_link.title)
            ):
                discovered[roadmap_link.url] = roadmap_link
            else:
                unresolved += 1
                unresolved_links.append(roadmap_link)
                if len(unresolved_examples) < 10:
                    unresolved_examples.append((roadmap_link.title, roadmap_link.url))

        if unresolved_links:
            recovered = self._recover_roadmap_programs_from_catalog_navigation(
                index_url=index_url,
                catoid=catoid,
                unresolved=unresolved_links,
            )
            if recovered:
                for roadmap_link in unresolved_links:
                    candidate = recovered.get(roadmap_link.url)
                    if candidate is not None:
                        discovered[candidate.url] = candidate
                unresolved_links = [
                    item for item in unresolved_links if item.url not in recovered
                ]
                unresolved = len(unresolved_links)
                unresolved_examples = [
                    (item.title, item.url) for item in unresolved_links[:10]
                ]

        print(
            "catalog_roadmap_discovery "
            f"roadmap_index={roadmap_index_url!r} roadmap_links={len(roadmap_links)} "
            f"roadmap_pages_opened={opened} canonical_programs={len(discovered)} "
            f"unresolved_roadmaps={unresolved} unresolved_examples={unresolved_examples!r}"
        )
        return tuple(sorted(discovered.values(), key=lambda item: item.title.casefold()))

    def _discover_programs_from_catalog_navigation(
        self,
        *,
        index_url: str,
        catoid: int,
        max_listing_pages: int = 8,
    ) -> tuple[CatalogLink, ...]:
        """Discover standalone bachelor programs from SDSU's curricula summary.

        The roadmap index is not exhaustive.  SDSU's catalog index, however, links to a
        dedicated "Summary of Curricula Offered" page, which is also the target behind
        Acalog ``returnto`` values such as the Humanities B.A. page.  Prefer that single
        authoritative page instead of recursively crawling catalog navigation.

        A small bounded fallback remains for catalog years where the summary link is absent
        or empty.  This fallback is deliberately capped so discovery cannot explode into
        hundreds of roadmap, service, and combined-degree pages again.
        """

        combined_degree_re = re.compile(
            r"\b(?:B\.?A\.?|B\.?S\.?|BFA|B\.?M\.?|BSN|BBA|BAS|BARCH|BMUS)"
            r"\s*/\s*(?:M\.?A\.?|M\.?S\.?|MFA|MPH|MBA|MPA|MENG|M\.?ENG\.?)\b",
            re.I,
        )

        def is_listing_program(title: str) -> bool:
            folded = title.casefold()
            if "roadmap" in folded:
                return False
            if "4+1" in folded or "4 + 1" in folded:
                return False
            if combined_degree_re.search(title):
                return False
            if any(
                phrase in folded
                for phrase in (
                    "combined degree",
                    "accelerated master's",
                    "accelerated masters",
                )
            ):
                return False
            return is_undergraduate_degree_program(title)

        def identity(link: CatalogLink) -> tuple[str, str | None, str | None] | tuple[str]:
            key = self._program_page_identity(link.url)
            if key[2] is not None:
                return key
            return (link.url,)

        def canonicalize(link: CatalogLink, *, base_url: str) -> CatalogLink:
            path, link_catoid, poid = self._program_page_identity(link.url)
            if path.endswith("preview_program.php") and poid is not None:
                return CatalogLink(
                    title=link.title,
                    url=urljoin(
                        base_url,
                        f"preview_program.php?catoid={link_catoid or catoid}&poid={poid}",
                    ),
                )
            return link

        discovered: dict[tuple[object, ...], CatalogLink] = {}

        def add_programs(snapshot: CatalogPageSnapshot) -> None:
            for raw_link in extract_program_links(
                snapshot.html,
                base_url=snapshot.url,
                catoid=catoid,
            ):
                if not is_listing_program(raw_link.title):
                    continue
                link = canonicalize(raw_link, base_url=snapshot.url)
                discovered.setdefault(identity(link), link)

        try:
            index = self.open(index_url)
        except CatalogChallengeError:
            raise
        except Exception:
            return ()

        navigation = extract_catalog_navigation_links(
            index.html,
            base_url=index.url,
            catoid=catoid,
        )
        summaries = [
            link
            for link in navigation
            if "summary of curricula offered" in link.title.casefold()
        ]

        attempted = 0
        for link in summaries[:1]:
            try:
                snapshot = self.open(link.url)
            except CatalogChallengeError:
                raise
            except CatalogBrowserError:
                continue
            attempted += 1
            add_programs(snapshot)

        if discovered:
            print(
                "catalog_program_listing_discovery "
                "strategy='curricula_summary' "
                f"listing_pages={attempted} undergraduate_programs={len(discovered)}"
            )
            return tuple(sorted(discovered.values(), key=lambda item: item.title.casefold()))

        # Bounded compatibility fallback for catalog years/themes where the summary page
        # is unavailable.  Only program registries and curricula/program hubs are followed.
        queue: deque[tuple[str, int]] = deque()
        queue.append((urljoin(index_url, f"programs.php?catoid={catoid}"), 1))
        for link in navigation:
            folded = link.title.casefold()
            if any(
                phrase in folded
                for phrase in (
                    "curricula by department",
                    "programs by campus location",
                )
            ):
                queue.append((link.url, 1))

        visited: set[str] = set()
        while queue and len(visited) < max(1, max_listing_pages):
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            try:
                snapshot = self.open(url)
            except CatalogChallengeError:
                raise
            except Exception:
                continue
            add_programs(snapshot)
            if depth <= 0:
                continue
            for child in extract_catalog_navigation_links(
                snapshot.html,
                base_url=snapshot.url,
                catoid=catoid,
                main_content_only=True,
            ):
                parsed = urlparse(child.url)
                child_path = parsed.path.casefold()
                if (
                    child_path.endswith("/programs.php")
                    or child_path == "programs.php"
                    or child_path.endswith("/content.php")
                    or child_path == "content.php"
                    or child_path.endswith("/preview_entity.php")
                    or child_path == "preview_entity.php"
                ):
                    queue.append((child.url, depth - 1))

        print(
            "catalog_program_listing_discovery "
            "strategy='bounded_fallback' "
            f"listing_pages={len(visited)} undergraduate_programs={len(discovered)}"
        )
        return tuple(sorted(discovered.values(), key=lambda item: item.title.casefold()))

    def discover_programs(
        self,
        *,
        index_url: str,
        catoid: int,
        max_listing_pages: int = 160,
    ) -> tuple[CatalogLink, ...]:
        """Discover the union of roadmap programs and the catalog program registry.

        SDSU's roadmap index is excellent for canonical program resolution, but it is not
        exhaustive: some current emphases have catalog requirement pages without a roadmap.
        Merge both sources by canonical Acalog program identity so a missing roadmap cannot
        silently remove a valid degree from ClassCatalog.
        """

        roadmap_programs = self._discover_programs_from_roadmaps(
            index_url=index_url,
            catoid=catoid,
        )
        listing_programs = self._discover_programs_from_catalog_navigation(
            index_url=index_url,
            catoid=catoid,
            max_listing_pages=max_listing_pages,
        )

        def identity(link: CatalogLink) -> tuple[str, str | None, str | None] | tuple[str]:
            key = self._program_page_identity(link.url)
            if key[2] is not None:
                return key
            return (link.url,)

        merged: dict[tuple[object, ...], CatalogLink] = {}
        for link in roadmap_programs:
            merged[identity(link)] = link
        roadmap_identities = set(merged)
        for link in listing_programs:
            merged.setdefault(identity(link), link)

        if roadmap_programs:
            added = sum(1 for key in merged if key not in roadmap_identities)
            print(
                "catalog_program_discovery "
                "strategy='roadmaps+catalog_listing' "
                f"roadmap_programs={len(roadmap_programs)} "
                f"catalog_listing_programs={len(listing_programs)} "
                f"added_from_catalog_listing={added} discovered_programs={len(merged)}"
            )
        else:
            print(
                "catalog_program_discovery "
                "strategy='catalog_navigation' "
                f"discovered_programs={len(merged)}"
            )

        return tuple(sorted(merged.values(), key=lambda item: item.title.casefold()))

    def discover_requirement_pages(
        self,
        *,
        index_url: str,
        catoid: int,
        additional_html: Iterable[CatalogPageSnapshot] = (),
    ) -> tuple[CatalogLink, ...]:
        snapshots = [self.open(index_url), *additional_html]
        links: dict[str, CatalogLink] = {}
        for position, snapshot in enumerate(snapshots):
            for link in extract_catalog_navigation_links(
                snapshot.html,
                base_url=snapshot.url,
                catoid=catoid,
                main_content_only=position > 0,
            ):
                if likely_requirement_navigation_link(link):
                    links[link.url] = link
        return tuple(sorted(links.values(), key=lambda item: item.title.casefold()))
