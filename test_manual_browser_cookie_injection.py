import asyncio
import json
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from core_helpers import parse_cookie
from patchright.async_api import async_playwright
from patchright_browser import (
    BrowserSessionConfig,
    PatchrightBrowser,
)
from patchright_cookie_codec import (
    has_primary_tiktok_auth_cookie,
    selenium_cookies_to_patchright,
    tiktok_auth_cookie_names,
)


class ManualBrowserCookieInjectionTests(unittest.TestCase):
    def test_past_expiry_json_refreshes_and_is_accepted_by_browser(self):
        # Sample realistic cookie json with historical expiry (August 2026, timestamp 1786267584)
        sample_json = json.dumps([
            {
                "name": "sessionid",
                "value": "mock_session_secret_123",
                "domain": ".tiktok.com",
                "path": "/",
                "expiry": 1786267584.0,
                "httpOnly": True,
                "secure": True,
            },
            {
                "name": "sessionid_ss",
                "value": "mock_session_secret_123",
                "domain": ".tiktok.com",
                "path": "/",
                "expiry": 1786267584.0,
            },
            {
                "name": "sid_tt",
                "value": "mock_sid_tt_val",
                "domain": ".tiktok.com",
                "path": "/",
                "expiry": 1786267584.0,
            },
            {
                "name": "msToken",
                "value": "mock_ms_token",
                "domain": ".tiktok.com",
                "path": "/",
            },
        ])

        now_ts = datetime.now().timestamp()
        parsed = parse_cookie(sample_json)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed), 4)

        # 1. Verify expiry was renewed past the current time
        for c in parsed:
            self.assertGreater(c["expiry"], now_ts)

        # 2. Verify conversion to Patchright format preserves renewed expiry
        converted = selenium_cookies_to_patchright(parsed)
        self.assertEqual(len(converted), 4)
        for c in converted:
            self.assertGreater(c["expires"], now_ts)

        # 3. Test with real Patchright Chromium context to ensure engine does not drop them
        async def verify_real_chromium():
            async with async_playwright() as p:
                b = await p.chromium.launch(headless=True)
                ctx = await b.new_context()
                await ctx.add_cookies(converted)
                retained = await ctx.cookies()
                auth_retained = [c["name"] for c in retained if c["name"] in ("sessionid", "sessionid_ss", "sid_tt")]
                await b.close()
                return len(retained), auth_retained

        total_retained, auth_retained = asyncio.run(verify_real_chromium())
        self.assertEqual(total_retained, 4)
        self.assertEqual(set(auth_retained), {"sessionid", "sessionid_ss", "sid_tt"})

    def test_chrome_extension_format_expiration_date_is_renewed(self):
        chrome_format = json.dumps([
            {
                "name": "sessionid",
                "value": "mock_val",
                "domain": ".tiktok.com",
                "path": "/",
                "expirationDate": 1700000000.0,
            }
        ])
        now_ts = time.time()
        parsed = parse_cookie(chrome_format)
        self.assertGreater(parsed[0]["expiry"], now_ts)

        converted = selenium_cookies_to_patchright(parsed)
        self.assertGreater(converted[0]["expires"], now_ts)

    def test_sqlite_fallback_invoked_when_cookie_str_empty(self):
        from tiktok_cookie_extractor import get_profile_tiktok_cookies

        mock_cfg = {
            "profile_name": "TestProfile",
            "cookie_str": "",
            "chrome_profile": "dummy_path",
        }
        with patch("tiktok_cookie_extractor.extract_cookies_from_profile_sqlite") as mock_extract:
            mock_extract.return_value = {
                "sessionid": "sqlite_session_xyz",
                "sid_tt": "sqlite_sid_xyz",
            }
            with patch("pathlib.Path.exists", return_value=True), patch("pathlib.Path.is_dir", return_value=True):
                extracted = get_profile_tiktok_cookies(mock_cfg)
                self.assertIn("sessionid", extracted)
                self.assertEqual(extracted["sessionid"], "sqlite_session_xyz")


if __name__ == "__main__":
    unittest.main()
