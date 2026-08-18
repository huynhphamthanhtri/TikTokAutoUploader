import json
import tempfile
import threading
import unittest
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from pathlib import Path
from unittest.mock import patch

import browser_patchright_glue as glue
from patchright_browser import ProfileInUseError
from patchright_profile_migration import create_patchright_profile


class _WatcherService:
    """Fake PatchrightBrowser for watch_manual_close / quit tests."""

    def __init__(self):
        self.closed_handle = None
        self.close_calls = 0

    def run(self, handle, operation):
        class FakeContext:
            pages = []

        class FakePage:
            context = FakeContext()

        value = asyncio_run(operation(FakePage()))
        future = Future()
        future.set_result(value)
        return future

    def close_session(self, handle):
        self.closed_handle = handle
        self.close_calls += 1
        future = Future()
        future.set_result(None)
        return future


def asyncio_run(coroutine):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


class BrowserPatchrightGlueTests(unittest.TestCase):
    def test_proxy_native_mapping_with_credentials(self):
        config = {
            "browser_profile_path": "profile",
            "use_proxy": True,
            "proxy_string": "http://127.0.0.1:8080:user:secret",
        }

        with patch.object(
            glue, "resolve_browser_executable", return_value=r"C:\browser\chrome.exe"
        ):
            session = glue.build_session_config(config)

        self.assertEqual(dict(session.proxy), {
            "server": "http://127.0.0.1:8080",
            "username": "user",
            "password": "secret",
        })
        self.assertEqual(session.executable_path, r"C:\browser\chrome.exe")

    def test_geo_environment_maps_to_native_context_options(self):
        with patch.object(
            glue, "resolve_browser_executable", return_value=r"C:\browser\chrome.exe"
        ):
            session = glue.build_session_config({
                "browser_profile_path": "profile",
                "fingerprint": {
                    "lang": "en-US",
                    "timezone": "Asia/Ho_Chi_Minh",
                    "geolocation": {"latitude": 10.75, "longitude": 106.67, "accuracy": 50},
                },
            })

        self.assertEqual(session.timezone_id, "Asia/Ho_Chi_Minh")
        self.assertEqual(dict(session.geolocation)["longitude"], 106.67)
        self.assertEqual(session.permissions, ("geolocation",))
        self.assertEqual(session.executable_path, r"C:\browser\chrome.exe")

    def test_invalid_proxy_fails_closed(self):
        config = {
            "browser_profile_path": "profile",
            "use_proxy": True,
            "proxy_string": "127.0.0.1:not-a-port",
        }

        with patch.object(
            glue, "resolve_browser_executable", return_value=r"C:\browser\chrome.exe"
        ):
            with self.assertRaises(glue.SessionSetupError):
                glue.build_session_config(config)

    def test_no_browser_raises_clear_error(self):
        with patch.object(glue, "resolve_browser_executable", return_value=None):
            with self.assertRaisesRegex(glue.SessionSetupError, "Không tìm thấy browser"):
                glue.build_session_config({"browser_profile_path": "profile"})

    def test_config_browser_executable_override_resolver(self):
        config = {
            "browser_profile_path": "profile",
            "browser_executable": r"C:\custom\chrome.exe",
        }
        session = glue.build_session_config(config)
        self.assertEqual(session.executable_path, r"C:\custom\chrome.exe")

    def test_resolve_orbita_144_beats_chrome_win64(self):
        with tempfile.TemporaryDirectory() as temporary:
            orbita = Path(temporary) / "Browser" / "orbita-browser-144"
            orbita.mkdir(parents=True)
            orbita_exe = orbita / "chrome.exe"
            orbita_exe.write_bytes(b"x")

            chrome64 = Path(temporary) / "Browser" / "chrome-win64"
            chrome64.mkdir(parents=True)
            chrome64_exe = chrome64 / "chrome.exe"
            chrome64_exe.write_bytes(b"x")

            with patch("profile_config_engine.find_ttm_profile_config", return_value={"license_key": "dummy"}):
                self.assertEqual(
                    glue.resolve_browser_executable(app_base=temporary, profile_name="AUTO 22"),
                    str(orbita_exe),
                )

    def test_resolve_chrome_win64_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            browser = Path(temporary) / "Browser" / "chrome-win64"
            browser.mkdir(parents=True)
            exe = browser / "chrome.exe"
            exe.write_bytes(b"x")
            self.assertEqual(
                glue.resolve_browser_executable(app_base=temporary),
                str(exe),
            )

    def test_resolve_bundled_beats_system_chrome(self):
        with tempfile.TemporaryDirectory() as temporary:
            browser = Path(temporary) / "Browser"
            browser.mkdir(parents=True)
            bundled = browser / "chrome.exe"
            bundled.write_bytes(b"x")
            with patch.object(
                glue, "_find_system_chrome_executable", return_value=r"C:\system\chrome.exe"
            ):
                self.assertEqual(
                    glue.resolve_browser_executable(app_base=temporary),
                    str(bundled),
                )

    def test_resolve_falls_back_to_system_chrome(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(
                glue, "_find_system_chrome_executable", return_value=r"C:\system\chrome.exe"
            ):
                self.assertEqual(
                    glue.resolve_browser_executable(app_base=temporary),
                    r"C:\system\chrome.exe",
                )

    def test_resolve_ignores_empty_or_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            browser = Path(temporary) / "Browser"
            browser.mkdir(parents=True)
            empty = browser / "chrome-win64" / "chrome.exe"
            empty.parent.mkdir(parents=True)
            empty.write_bytes(b"")
            dir_exe = browser / "chrome.exe"
            dir_exe.mkdir()
            with patch.object(glue, "_find_system_chrome_executable", return_value=None):
                self.assertIsNone(glue.resolve_browser_executable(app_base=temporary))

    def test_resolve_internal_browser_dir(self):
        with tempfile.TemporaryDirectory() as temporary:
            browser = Path(temporary) / "_internal" / "Browser" / "chrome-win64"
            browser.mkdir(parents=True)
            exe = browser / "chrome.exe"
            exe.write_bytes(b"x")
            self.assertEqual(
                glue.resolve_browser_executable(app_base=temporary),
                str(exe),
            )

    def test_profile_creation_and_resume_after_legacy_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            managed = Path(temporary) / "account"
            legacy = managed / "Profile"
            legacy.mkdir(parents=True)
            config = {"chrome_profile": str(legacy)}

            target = Path(glue.ensure_patchright_profile(config))
            legacy.rmdir()
            resumed = Path(glue.ensure_patchright_profile(config))

            self.assertEqual(resumed, target)
            self.assertTrue(target.is_dir())
            self.assertFalse(legacy.exists())

    def test_profile_resume_rejects_unowned_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            legacy = Path(temporary) / "account" / "Profile"
            target = legacy.with_name("Profile-Patchright")
            target.mkdir(parents=True)

            with self.assertRaises(ValueError):
                glue.ensure_patchright_profile({"chrome_profile": str(legacy)})
            self.assertFalse(legacy.exists())

    def test_profile_resume_rejects_tampered_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            managed = Path(temporary) / "account"
            legacy = managed / "Profile"
            legacy.mkdir(parents=True)
            target = create_patchright_profile(legacy, managed)
            marker = target / ".patchright-profile-owned"
            marker.write_text("{}", encoding="ascii")

            with self.assertRaises(ValueError):
                glue.ensure_patchright_profile({"chrome_profile": str(legacy)})

    def test_upload_timeout_cancels_operation_and_closes_session(self):
        token = glue.SessionToken("account", object(), glue.SessionMode.AUTOMATION, "profile")

        class TimedOutFuture:
            def result(self, timeout=None):
                raise FutureTimeoutError()

        class Service:
            def __init__(self):
                self.closed_handle = None

            def run(self, handle, operation):
                return TimedOutFuture()

            def cancel_session(self, handle):
                completed = Future()
                completed.set_result(None)
                return completed

            def close_session(self, handle):
                self.closed_handle = handle
                completed = Future()
                completed.set_result(None)
                return completed

        service = Service()
        with patch.object(glue, "browser_service", return_value=service):
            with self.assertRaisesRegex(glue.SessionSetupError, "Đăng video vượt quá"):
                glue.run_upload(token, "video.mp4", timeout=0.01)

        self.assertIs(service.closed_handle, token.handle)
        self.assertFalse(token.is_alive())

    def test_upload_timeout_does_not_report_cancelled_safe(self):
        token = glue.SessionToken("account", object(), glue.SessionMode.AUTOMATION, "profile")

        class TimedOutFuture:
            def result(self, timeout=None):
                raise FutureTimeoutError()

        class Service:
            def run(self, handle, operation):
                return TimedOutFuture()

            def cancel_session(self, handle):
                completed = Future()
                completed.set_result(None)
                return completed

            def close_session(self, handle):
                completed = Future()
                completed.set_result(None)
                return completed

        with patch.object(glue, "browser_service", return_value=Service()):
            with self.assertRaisesRegex(glue.SessionSetupError, "Đăng video vượt quá"):
                glue.run_upload(token, "video.mp4", timeout=0.01)

        self.assertFalse(token.cancellation_event.is_set())

    def test_manual_watch_close_closes_session_in_registry(self):
        token = glue.SessionToken("account", "handle", glue.SessionMode.MANUAL, "profile")
        service = _WatcherService()
        with patch.object(glue, "browser_service", return_value=service):
            glue.watch_manual_close(token, poll=0.01)
        self.assertIs(service.closed_handle, token.handle)
        self.assertFalse(token.is_alive())

    def test_manual_close_then_reopen_same_profile_succeeds(self):
        from browser_runtime import BrowserRuntime
        from patchright_browser import BrowserSessionConfig, PatchrightBrowser, SessionMode
        from test_patchright_browser import FakeManager, FakePlaywright

        playwright = FakePlaywright()
        runtime = BrowserRuntime(lambda: FakeManager(playwright))
        browser = PatchrightBrowser(runtime)
        try:
            config = BrowserSessionConfig("profile", mode=SessionMode.MANUAL)
            with patch.object(glue, "browser_service", return_value=browser):
                opened = browser.open_session(config).result(2)
                token = glue.SessionToken("account", opened.handle, SessionMode.MANUAL, "profile")
                playwright.chromium.contexts[0].pages.clear()
                glue.watch_manual_close(token, poll=0.01)
                status = browser.status()
                self.assertEqual(status.active_sessions, 0)
                self.assertEqual(status.profile_paths, ())
                browser.open_session(BrowserSessionConfig("profile")).result(2)
        finally:
            browser.shutdown()
            runtime.shutdown()

    def test_open_session_maps_profile_in_use_to_profile_busy(self):
        class BusyService:
            def open_session(self, config):
                future = Future()
                future.set_exception(ProfileInUseError("profile is already in use: profile"))
                return future

        with patch.object(
            glue, "resolve_browser_executable", return_value=r"C:\browser\chrome.exe"
        ), patch.object(glue, "browser_service", return_value=BusyService()):
            with self.assertRaises(glue.ProfileBusyError):
                glue.open_session({"browser_profile_path": "profile"}, "account")

    def test_open_session_keeps_real_handle_and_profile_name(self):
        from patchright_browser import SessionHandle, SessionMode, SessionResult

        profile_path = "profile"
        handle = SessionHandle(
            session_id="sid-1",
            generation=3,
            profile_path=profile_path,
            mode=SessionMode.AUTOMATION,
        )

        class Service:
            def open_session(self, config):
                future = Future()
                future.set_result(SessionResult(handle=handle, page_count=1))
                return future

        with patch.object(
            glue, "resolve_browser_executable", return_value=r"C:\browser\chrome.exe"
        ), patch.object(glue, "browser_service", return_value=Service()):
            token = glue.open_session({"browser_profile_path": profile_path}, "AUTO 6")

        self.assertEqual(token.profile_name, "AUTO 6")
        self.assertIs(token.handle, handle)
        self.assertEqual(token.handle.session_id, "sid-1")
        self.assertEqual(token.handle.generation, 3)
        self.assertEqual(token.profile_path, profile_path)

    def test_open_session_rejects_malformed_handle_and_closes_session(self):
        from patchright_browser import SessionResult

        class Service:
            def __init__(self):
                self.closed_handle = None

            def open_session(self, config):
                future = Future()
                future.set_result(SessionResult(handle="not-a-handle", page_count=0))
                return future

            def close_session(self, handle):
                self.closed_handle = handle
                completed = Future()
                completed.set_result(None)
                return completed

        service = Service()
        with patch.object(
            glue, "resolve_browser_executable", return_value=r"C:\browser\chrome.exe"
        ), patch.object(glue, "browser_service", return_value=service):
            with self.assertRaisesRegex(glue.SessionSetupError, "Session trả về không hợp lệ"):
                glue.open_session({"browser_profile_path": "profile"}, "AUTO 6")

        self.assertEqual(service.closed_handle, "not-a-handle")

    def test_open_session_rejects_profile_path_mismatch_and_closes_session(self):
        from patchright_browser import SessionHandle, SessionMode, SessionResult

        handle = SessionHandle(
            session_id="sid-1",
            generation=1,
            profile_path="other-profile",
            mode=SessionMode.AUTOMATION,
        )

        class Service:
            def __init__(self):
                self.closed_handle = None

            def open_session(self, config):
                future = Future()
                future.set_result(SessionResult(handle=handle, page_count=1))
                return future

            def close_session(self, handle):
                self.closed_handle = handle
                completed = Future()
                completed.set_result(None)
                return completed

        service = Service()
        with patch.object(
            glue, "resolve_browser_executable", return_value=r"C:\browser\chrome.exe"
        ), patch.object(glue, "browser_service", return_value=service):
            with self.assertRaisesRegex(glue.SessionSetupError, "Session trả về không hợp lệ"):
                glue.open_session({"browser_profile_path": "profile"}, "AUTO 6")

        self.assertIs(service.closed_handle, handle)

    def test_open_session_import_failure_then_cleanup_then_reopen_succeeds(self):
        from browser_runtime import BrowserRuntime
        from patchright_browser import PatchrightBrowser
        from test_patchright_browser import FakeManager, FakePlaywright

        playwright = FakePlaywright()
        runtime = BrowserRuntime(lambda: FakeManager(playwright))
        browser = PatchrightBrowser(runtime)
        config = {"browser_profile_path": "profile"}
        try:
            with patch.object(
                glue, "resolve_browser_executable", return_value=r"C:\browser\chrome.exe"
            ), patch.object(glue, "browser_service", return_value=browser):
                token = glue.open_session(config, "AUTO 6")
                with patch.object(glue, "import_cookies", side_effect=RuntimeError("cookie import failed")):
                    with self.assertRaisesRegex(RuntimeError, "cookie import failed"):
                        glue.import_cookies(token, [{"name": "sid", "value": "x", "domain": ".example.test"}])
                self.assertTrue(token.quit())
                status = browser.status()
                self.assertEqual(status.active_sessions, 0)
                self.assertEqual(status.profile_paths, ())
                reopened = glue.open_session(config, "AUTO 6")
                self.assertEqual(reopened.profile_name, "AUTO 6")
                self.assertTrue(reopened.handle.session_id)
                self.assertIsNot(reopened.handle, token.handle)
                self.assertTrue(reopened.quit())
        finally:
            browser.shutdown()
            runtime.shutdown()

    def test_quit_is_idempotent(self):
        token = glue.SessionToken("account", "handle", glue.SessionMode.AUTOMATION, "profile")
        service = _WatcherService()
        with patch.object(glue, "browser_service", return_value=service):
            self.assertTrue(token.quit())
            self.assertTrue(token.quit())
        self.assertFalse(token.is_alive())
        self.assertEqual(service.close_calls, 1)

    def test_quit_is_thread_safe_single_close(self):
        token = glue.SessionToken("account", "handle", glue.SessionMode.AUTOMATION, "profile")
        service = _WatcherService()
        with patch.object(glue, "browser_service", return_value=service):
            threads = [threading.Thread(target=token.quit) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(service.close_calls, 1)
        self.assertFalse(token.is_alive())

    def test_quit_close_timeout_does_not_mark_closed(self):
        token = glue.SessionToken("account", "handle", glue.SessionMode.AUTOMATION, "profile")

        class SlowService:
            def close_session(self, handle):
                return Future()

        with patch.object(glue, "browser_service", return_value=SlowService()):
            closed = token.quit(timeout=0.01)

        self.assertFalse(closed)
        self.assertTrue(token.is_alive())

    def test_proxy_verification_exception_is_indeterminate(self):
        token = glue.SessionToken("account", object(), glue.SessionMode.AUTOMATION, "profile")
        with patch.object(glue, "run_operation", side_effect=RuntimeError("203.0.113.7")):
            matched, current_ip = glue.verify_exit_ip(token, "198.51.100.1")

        self.assertFalse(matched)
        self.assertIsNone(current_ip)

    def test_navigation_failure_is_not_hidden(self):
        token = glue.SessionToken("account", object(), glue.SessionMode.AUTOMATION, "profile")

        async def invoke(operation):
            class Page:
                async def goto(self, *_args, **_kwargs):
                    raise RuntimeError("navigation failed")

            return await operation(Page())

        with patch.object(glue, "run_operation", side_effect=lambda _token, operation, timeout: __import__('asyncio').run(invoke(operation))):
            with self.assertRaisesRegex(RuntimeError, "navigation failed"):
                glue.navigate(token, "https://example.test")

    def test_is_login_url_matches_only_login_paths(self):
        self.assertTrue(glue._is_login_url("https://www.tiktok.com/login"))
        self.assertTrue(glue._is_login_url("https://www.tiktok.com/login/"))
        self.assertTrue(glue._is_login_url("https://www.tiktok.com/v/login?lang=en"))
        self.assertFalse(glue._is_login_url("https://www.tiktok.com/tiktokstudio/content"))
        self.assertFalse(glue._is_login_url("https://www.tiktok.com/tiktokstudio/upload/"))
        self.assertFalse(glue._is_login_url(""))

    def _patch_login_state_run(self, page):
        async def invoke(operation):
            return await operation(page)

        def run_fake(_token, operation, timeout=None):
            return __import__('asyncio').run(invoke(operation))

        return patch.object(glue, "run_operation", side_effect=run_fake)

    def test_page_login_state_detects_login_form_on_content_url(self):
        from patchright_upload import SELECTORS

        token = glue.SessionToken("account", object(), glue.SessionMode.AUTOMATION, "profile")

        class FakeLocator:
            def __init__(self, count):
                self._count = count

            async def count(self):
                return self._count

            def nth(self, index):
                return self

            async def is_visible(self):
                return True

        class FakePage:
            url = "https://www.tiktok.com/tiktokstudio/content"

            def locator(self, selector):
                return FakeLocator(1 if selector == SELECTORS["login"] else 0)

        with self._patch_login_state_run(FakePage()):
            self.assertEqual(glue.page_login_state(token), "login_required")

    def test_page_login_state_detects_upload_shell(self):
        from patchright_upload import SELECTORS

        token = glue.SessionToken("account", object(), glue.SessionMode.AUTOMATION, "profile")

        class FakeLocator:
            def __init__(self, count):
                self._count = count

            async def count(self):
                return self._count

            def nth(self, index):
                return self

            async def is_visible(self):
                return True

        class FakePage:
            url = "https://www.tiktok.com/tiktokstudio/upload/"

            def locator(self, selector):
                return FakeLocator(1 if selector == SELECTORS["file_input"] else 0)

        with self._patch_login_state_run(FakePage()):
            self.assertEqual(glue.page_login_state(token), "authenticated")

    def test_page_login_state_is_fail_closed(self):
        token = glue.SessionToken("account", object(), glue.SessionMode.AUTOMATION, "profile")

        class FakeLocator:
            async def count(self):
                return 0

            def nth(self, index):
                return self

            async def is_visible(self):
                return False

        class FakePage:
            url = "https://www.tiktok.com/tiktokstudio/content"

            def locator(self, selector):
                return FakeLocator()

        with self._patch_login_state_run(FakePage()):
            self.assertEqual(glue.page_login_state(token), "indeterminate")

    def test_build_session_config_ram_optimization_args(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "browser_profile_path": temp_dir,
                "browser_executable": r"C:\browser\chrome.exe",
            }
            session = glue.build_session_config(config)
            self.assertIn("--renderer-process-limit=2", session.args)
            self.assertIn("--js-flags=--max-old-space-size=256", session.args)
            self.assertNotIn("--expose-gc", " ".join(session.args))
            self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", session.args)
            self.assertIn("--disable-webrtc-multiple-routes", session.args)
            self.assertIn("--disk-cache-size=33554432", session.args)
            self.assertIn("--media-cache-size=67108864", session.args)
            self.assertIn("--aggressive-cache-discard", session.args)
            self.assertIn("--enable-features=MemoryReducer,PurgeAndSuspend,ResourceLoadScheduler", session.args)

    def test_clean_profile_volatile_caches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            p = Path(temp_dir)
            cache_dir = p / "Default" / "Cache"
            code_cache_dir = p / "Default" / "Code Cache"
            cookies_file = p / "Default" / "Cookies"
            
            cache_dir.mkdir(parents=True)
            code_cache_dir.mkdir(parents=True)
            (cache_dir / "data_0").write_bytes(b"cache")
            cookies_file.write_bytes(b"secret_session")
            
            glue.clean_profile_volatile_caches(temp_dir)
            
            self.assertFalse(cache_dir.exists())
            self.assertFalse(code_cache_dir.exists())
            self.assertTrue(cookies_file.exists())

    def test_build_session_config_stealth_and_no_dead_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "browser_profile_path": temp_dir,
                "browser_executable": r"C:\browser\chrome.exe",
                "account_uuid": "test-uuid-stealth",
            }
            session = glue.build_session_config(config)
            self.assertIsNotNone(session.stealth_config)
            self.assertEqual(len(session.stealth_config["webgl"]["glParamValues"]), 43)
            self.assertEqual(len(session.stealth_config["webgl"]["extensions"]), 34)
            self.assertEqual(len(session.stealth_config["plugins"]["list"]), 5)
            self.assertEqual(session.stealth_config["clientHints"]["formFactors"], ["Desktop"])

            # Verify config files were written to disk for C++ HT Browser
            p = Path(temp_dir)
            self.assertTrue((p / "data.huynhthang").exists())
            self.assertTrue((p / "data.orbita").exists())
            with open(p / "data.huynhthang", "r", encoding="utf-8") as f:
                dh_json = json.load(f)
            self.assertTrue(bool(dh_json.get("profile_name") or dh_json.get("account_uuid")), "Profile name or UUID must be present in config")
            self.assertTrue(bool(dh_json["license_key"]))


if __name__ == "__main__":
    unittest.main()
