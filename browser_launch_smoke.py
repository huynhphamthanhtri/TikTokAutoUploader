"""Headless browser launch smoke for CI and local verification.

Launches a real persistent Chromium context using the bundled ``Browser``
resource executable (or a path from ``BROWSER_SMOKE_EXECUTABLE``), loads a
local data: URL, verifies the page title, then closes the context cleanly.

Does not touch TikTok, proxies, cookies or user profiles.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import browser_patchright_glue as glue


def _resolve_executable():
    override = os.environ.get("BROWSER_SMOKE_EXECUTABLE", "").strip()
    if override and Path(override).is_file() and Path(override).stat().st_size > 0:
        return str(override)
    return glue.resolve_browser_executable()


async def main():
    executable = _resolve_executable()
    if not executable:
        print("SMOKE_FAIL: no browser executable resolved")
        return 1
    print("EXECUTABLE:", executable)

    from patchright.async_api import async_playwright

    with tempfile.TemporaryDirectory() as tmp:
        profile = str(Path(tmp) / "profile")
        async with async_playwright() as playwright:
            ctx = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile,
                headless=True,
                executable_path=executable,
                args=["--no-first-run", "--log-level=3"],
            )
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto("data:text/html,<html><title>smoke</title></html>")
                title = await page.title()
                print("PAGE_TITLE:", title)
                if title != "smoke":
                    raise RuntimeError("title mismatch: %r" % title)
            finally:
                await ctx.close()
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
