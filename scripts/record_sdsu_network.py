from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Response, async_playwright

DEFAULT_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CLSRCH_MAIN_FL.GBL"
)
OUTPUT = Path("artifacts/sdsu-network")


def safe_name(url: str, counter: int) -> str:
    parsed = urlparse(url)
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", f"{parsed.netloc}{parsed.path}").strip("_")
    return f"{counter:04d}_{base[-120:]}"


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    counter = 0
    tasks: list[asyncio.Task[None]] = []

    async def save_response(response: Response) -> None:
        nonlocal counter
        resource_type = response.request.resource_type
        if resource_type not in {"xhr", "fetch"}:
            return
        counter += 1
        stem = safe_name(response.url, counter)
        metadata = {
            "url": response.url,
            "status": response.status,
            "resource_type": resource_type,
            "content_type": response.headers.get("content-type"),
        }
        (OUTPUT / f"{stem}.meta.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )
        try:
            body = await response.body()
        except Exception as exc:  # noqa: BLE001 - recorder should continue
            (OUTPUT / f"{stem}.error.txt").write_text(str(exc), encoding="utf-8")
            return
        suffix = ".json" if "json" in (metadata["content_type"] or "") else ".txt"
        (OUTPUT / f"{stem}{suffix}").write_bytes(body)
        print(response.status, resource_type, response.url)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        page.on("response", lambda response: tasks.append(asyncio.create_task(save_response(response))))
        await page.goto(os.getenv("SDSU_PUBLIC_SCHEDULE_URL", DEFAULT_URL))
        print("Perform one normal public term + subject search, then close the browser window.")
        await page.wait_for_event("close", timeout=0)
        if tasks:
            await asyncio.gather(*tasks)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
