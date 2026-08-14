import unittest

from patchright_cookie_codec import (
    has_primary_tiktok_auth_cookie,
    patchright_cookie_to_selenium,
    patchright_cookies_to_selenium,
    selenium_cookie_to_patchright,
    selenium_cookies_to_patchright,
    tiktok_auth_cookie_names,
)


class PatchrightCookieCodecTests(unittest.TestCase):
    def test_selenium_to_patchright_preserves_cookie_attributes(self):
        source = {
            "name": "sessionid",
            "value": "abc",
            "domain": ".tiktok.com",
            "path": "/upload",
            "expiry": 2_000_000_000,
            "sameSite": "none",
            "secure": True,
            "httpOnly": True,
        }
        converted = selenium_cookie_to_patchright(source)
        self.assertEqual(converted["domain"], ".tiktok.com")
        self.assertEqual(converted["expires"], 2_000_000_000.0)
        self.assertEqual(converted["sameSite"], "None")
        self.assertTrue(converted["secure"])
        self.assertTrue(converted["httpOnly"])
        self.assertNotIn("expiry", converted)

    def test_patchright_to_selenium_renames_expiration(self):
        converted = patchright_cookie_to_selenium({
            "name": "token", "value": "x", "domain": ".example.com",
            "path": "/", "expires": 1234.9, "sameSite": "Lax",
        })
        self.assertEqual(converted["expiry"], 1234)
        self.assertNotIn("expires", converted)
        self.assertEqual(converted["domain"], ".example.com")

    def test_session_cookies_remain_session_cookies(self):
        selenium_session = {"name": "a", "value": "b", "domain": "example.com"}
        patchright_session = {"name": "a", "value": "b", "domain": "example.com", "expires": -1}
        self.assertNotIn("expires", selenium_cookie_to_patchright(selenium_session))
        self.assertNotIn("expiry", patchright_cookie_to_selenium(patchright_session))

    def test_invalid_entries_are_rejected_without_mutating_input(self):
        valid = {"name": "ok", "value": "1", "domain": ".example.com", "expiry": 10}
        invalid = [
            None,
            {"name": "", "value": "x", "domain": "example.com"},
            {"name": "x", "value": "y", "domain": "bad domain"},
            {"name": "x", "value": "y", "domain": "example.com", "expiry": float("nan")},
            {"name": "x", "value": "y", "domain": "example.com", "secure": "yes"},
        ]
        source = [valid, *invalid]
        self.assertEqual(len(selenium_cookies_to_patchright(source)), 1)
        self.assertEqual(valid["domain"], ".example.com")
        self.assertIsNone(selenium_cookie_to_patchright(invalid[1]))
        self.assertEqual(patchright_cookies_to_selenium("not-a-list"), [])

    def test_auth_cookie_classification(self):
        from patchright_cookie_codec import TIKTOK_AUTH_COOKIES

        self.assertIn("sessionid", TIKTOK_AUTH_COOKIES)
        self.assertIn("sessionid_ss", TIKTOK_AUTH_COOKIES)
        self.assertIn("sid_guard", TIKTOK_AUTH_COOKIES)

        def cookie(name):
            return {"name": name, "value": "v", "domain": ".tiktok.com"}

        self.assertTrue(has_primary_tiktok_auth_cookie([cookie("sessionid")]))
        self.assertTrue(has_primary_tiktok_auth_cookie([cookie("sid_tt")]))
        self.assertTrue(has_primary_tiktok_auth_cookie([cookie("sessionid_ss"), cookie("sid_guard")]))
        self.assertFalse(has_primary_tiktok_auth_cookie([cookie("msToken"), cookie("ttwid")]))
        self.assertFalse(has_primary_tiktok_auth_cookie([]))

    def test_auth_cookie_names(self):
        self.assertEqual(
            tiktok_auth_cookie_names([
                {"name": "sessionid", "value": "v"},
                {"name": "msToken", "value": "x"},
            ]),
            ["sessionid"],
        )
        self.assertEqual(
            tiktok_auth_cookie_names([
                {"name": "sessionid_ss", "value": "v"},
                {"name": "ttwid", "value": "x"},
            ]),
            ["sessionid_ss"],
        )


if __name__ == "__main__":
    unittest.main()
