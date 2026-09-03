from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from classcatalog.scraping.base import SubjectRequest

DEFAULT_PUBLIC_SCHEDULE_URL: Final[str] = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CLSRCH_MAIN_FL.GBL"
)


@dataclass(frozen=True, slots=True)
class BrowserCaptureConfig:
    base_url: str = DEFAULT_PUBLIC_SCHEDULE_URL
    headless: bool = True
    request_delay_seconds: float = 1.5
    output_directory: Path = Path("artifacts/sdsu-snapshots")


class SdsuPublicBrowserCapture:
    """Capture public result HTML; parsing and persistence remain separate concerns.

    PeopleSoft markup changes frequently. Accessible labels are used where possible, but
    selectors must be verified against the current public page before production use.
    """

    def __init__(self, config: BrowserCaptureConfig | None = None) -> None:
        self._config = config or BrowserCaptureConfig()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> SdsuPublicBrowserCapture:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._config.headless)
        self._context = await self._browser.new_context(
            user_agent=(
                "ClassCatalog research prototype; contact: replace-with-project-email@example.com"
            )
        )
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def capture(self, request: SubjectRequest) -> Path:
        if self._context is None:
            raise RuntimeError("Use SdsuPublicBrowserCapture as an async context manager")

        page = await self._context.new_page()
        try:
            await page.goto(self._config.base_url, wait_until="domcontentloaded")
            await self._choose_term(page, request.term)
            await self._open_additional_search(page)
            await self._choose_subject(page, request.subject)
            await self._submit_search(page)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(self._config.request_delay_seconds)

            safe_term = re.sub(r"[^a-z0-9]+", "-", request.term.casefold()).strip("-")
            safe_subject = re.sub(r"[^a-z0-9]+", "-", request.subject.casefold()).strip("-")
            output = self._config.output_directory / safe_term / f"{safe_subject}.html"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(await page.content(), encoding="utf-8")
            return output
        finally:
            await page.close()

    @staticmethod
    async def _choose_term(page: Page, term: str) -> None:
        term_choice = page.get_by_text(term, exact=True).first
        await term_choice.click()

    @staticmethod
    async def _open_additional_search(page: Page) -> None:
        control = page.get_by_text(re.compile(r"Additional Ways to Search", re.I)).first
        await control.click()

    @staticmethod
    async def _choose_subject(page: Page, subject: str) -> None:
        control = page.get_by_label(re.compile(r"Available Subjects", re.I)).first
        tag_name = await control.evaluate("element => element.tagName.toLowerCase()")
        if tag_name == "select":
            await control.select_option(label=subject)
        else:
            await control.fill(subject)
            await page.get_by_text(subject, exact=True).first.click()

    @staticmethod
    async def _submit_search(page: Page) -> None:
        await page.get_by_role("button", name=re.compile(r"^Search$", re.I)).first.click()
