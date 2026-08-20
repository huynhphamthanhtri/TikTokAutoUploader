import unittest
from unittest.mock import patch

import browser_patchright_glue as glue
from browser_patchright_glue import LoginRequiredError


def _token():
    return object()


def _config(**overrides):
    config = {"cookie_str": ""}
    config.update(overrides)
    return config


class AuthenticateSessionTests(unittest.TestCase):
    def test_profile_session_authenticated_no_cookie_fallback(self):
        calls = []
        with patch.object(glue, "navigate") as mock_navigate, patch.object(
            glue, "wait_page_login_state", return_value="authenticated"
        ) as mock_state, patch.object(glue, "import_cookies", side_effect=lambda *a: calls.append("import")):
            source = glue.authenticate_session(
                _token(), _config(cookie_str='[{"name":"x"}]'), "P", "https://upload"
            )
        self.assertEqual(source, "profile_session")
        mock_state.assert_called_once()
        self.assertEqual(calls, [])

    def test_login_required_without_cookie_raises(self):
        with patch.object(glue, "navigate"), patch.object(
            glue, "wait_page_login_state", return_value="login_required"
        ), patch.object(glue, "import_cookies") as mock_import, patch.object(
            glue, "parse_cookie", return_value=None
        ):
            with self.assertRaises(LoginRequiredError):
                glue.authenticate_session(_token(), _config(), "P", "https://upload")
        mock_import.assert_not_called()

    def test_cookie_fallback_imported_once_on_login_required(self):
        import_calls = []
        states = iter(["login_required", "authenticated"])
        with patch.object(glue, "navigate") as mock_navigate, patch.object(
            glue, "wait_page_login_state", side_effect=lambda *a, **k: next(states)
        ), patch.object(glue, "import_cookies", side_effect=lambda *a: import_calls.append(a[1])), patch.object(
            glue, "parse_cookie", return_value=[{"name": "sid_tt"}]
        ):
            source = glue.authenticate_session(
                _token(), _config(cookie_str='[{"name":"sid_tt"}]'), "P", "https://upload"
            )
        self.assertEqual(source, "cookie_fallback")
        self.assertEqual(len(import_calls), 1)
        self.assertEqual(mock_navigate.call_count, 2)

    def test_cookie_fallback_rejected_raises(self):
        states = iter(["login_required", "login_required"])
        with patch.object(glue, "navigate"), patch.object(
            glue, "wait_page_login_state", side_effect=lambda *a, **k: next(states)
        ), patch.object(glue, "import_cookies") as mock_import, patch.object(
            glue, "parse_cookie", return_value=[{"name": "sid_tt"}]
        ):
            with self.assertRaises(LoginRequiredError):
                glue.authenticate_session(_token(), _config(cookie_str="x"), "P", "https://upload")
        mock_import.assert_called_once()

    def test_indeterminate_never_imports_cookie_fallback(self):
        with patch.object(glue, "navigate"), patch.object(
            glue, "wait_page_login_state", return_value="indeterminate"
        ), patch.object(glue, "import_cookies") as mock_import, patch.object(
            glue, "parse_cookie", return_value=[{"name": "sid_tt"}]
        ):
            with self.assertRaisesRegex(LoginRequiredError, "giữ nguyên session"):
                glue.authenticate_session(_token(), _config(cookie_str="x"), "P", "https://upload")
        mock_import.assert_not_called()

    def test_fallback_disabled_never_imports(self):
        with patch.object(glue, "navigate"), patch.object(
            glue, "wait_page_login_state", return_value="indeterminate"
        ), patch.object(glue, "import_cookies") as mock_import, patch.object(
            glue, "parse_cookie", return_value=[{"name": "sid_tt"}]
        ):
            with self.assertRaises(LoginRequiredError):
                glue.authenticate_session(
                    _token(), _config(cookie_str="x"), "P", "https://upload", allow_cookie_fallback=False
                )
        mock_import.assert_not_called()


if __name__ == "__main__":
    unittest.main()
