import unittest

from cookie_live_check import (
    CookieCheckResult,
    CookieCheckState,
    CookieSource,
    build_summary,
    classify_login_state,
    mask_detail,
    primary_auth_cookie_names,
)


class CookieCheckModelTests(unittest.TestCase):
    def test_primary_auth_cookie_names_detects_sessionid(self):
        cookies = [
            {"name": "sessionid", "value": "abc"},
            {"name": "theme", "value": "dark"},
        ]
        self.assertEqual(primary_auth_cookie_names(cookies), ("sessionid",))

    def test_primary_auth_cookie_names_empty(self):
        self.assertEqual(primary_auth_cookie_names([{"name": "theme"}]), ())

    def test_classify_authenticated_is_live(self):
        self.assertEqual(
            classify_login_state("authenticated", True),
            CookieCheckState.LIVE,
        )

    def test_classify_login_required_is_dead(self):
        self.assertEqual(
            classify_login_state("login_required", True),
            CookieCheckState.DEAD,
        )

    def test_classify_indeterminate_is_unknown_never_dead(self):
        self.assertEqual(
            classify_login_state("indeterminate", True),
            CookieCheckState.UNKNOWN,
        )

    def test_build_summary_counts(self):
        results = [
            CookieCheckResult(profile_name="a", state=CookieCheckState.LIVE),
            CookieCheckResult(profile_name="b", state=CookieCheckState.LIVE),
            CookieCheckResult(profile_name="c", state=CookieCheckState.DEAD),
            CookieCheckResult(profile_name="d", state=CookieCheckState.UNKNOWN),
            CookieCheckResult(profile_name="e", state=CookieCheckState.SKIPPED),
        ]
        counts = build_summary(results)
        self.assertEqual(counts["total"], 5)
        self.assertEqual(counts["live"], 2)
        self.assertEqual(counts["dead"], 1)
        self.assertEqual(counts["unknown"], 1)
        self.assertEqual(counts["skipped"], 1)

    def test_display_labels(self):
        live = CookieCheckResult(profile_name="a", state=CookieCheckState.LIVE, source=CookieSource.SAVED_COOKIE)
        self.assertEqual(live.display_state(), "Live")
        self.assertEqual(live.display_source(), "Cookie lưu")
        self.assertEqual(CookieCheckResult(profile_name="a", state=CookieCheckState.PENDING).display_state(), "Chờ")

    def test_mask_detail_hides_cookie_and_secret(self):
        detail = mask_detail("sessionid=abc123 token=supersecret", secrets=("supersecret",))
        self.assertNotIn("abc123", detail)
        self.assertNotIn("supersecret", detail)
        self.assertIn("***", detail)

    def test_mask_detail_handles_empty(self):
        self.assertEqual(mask_detail(""), "")


if __name__ == "__main__":
    unittest.main()
