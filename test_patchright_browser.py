import asyncio
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import CancelledError
from dataclasses import FrozenInstanceError, dataclass

from browser_runtime import BrowserRuntime, RuntimeClosedError, RuntimeState
from patchright_browser import (
    BrowserServiceClosedError,
    BrowserSessionConfig,
    PatchrightBrowser,
    ProfileInUseError,
    SessionMode,
    StaleSessionError,
)


class FakePage:
    pass


@dataclass(frozen=True)
class FakeOperationValue:
    outcome: str
    details: dict


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]
        self.closed = False
        self.added_cookies = []
        self.owner_thread = threading.get_ident()

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    async def cookies(self):
        return list(self.added_cookies) + [{"name": "sid", "value": "abc", "domain": ".example.test"}]

    async def add_cookies(self, cookies):
        for cookie in cookies:
            item = dict(cookie)
            if not item.get("domain") and item.get("url"):
                from urllib.parse import urlparse

                item["domain"] = urlparse(item["url"]).hostname or ""
            self.added_cookies.append(item)

    async def clear_cookies(self, name=None, domain=None, path=None):
        kept = []
        for cookie in self.added_cookies:
            if name is not None and cookie.get("name") == name:
                continue
            if domain is not None:
                try:
                    dom = str(cookie.get("domain", ""))
                    matches = bool(domain.match(dom)) if hasattr(domain, "match") else dom == domain
                except Exception:
                    matches = False
                if matches:
                    continue
            kept.append(cookie)
        self.added_cookies = kept

    async def close(self):
        self.closed = True
        self.close_thread = threading.get_ident()


class FakeChromium:
    def __init__(self):
        self.contexts = []
        self.launches = []
        self.launch_entered = None
        self.launch_gate = None

    async def launch_persistent_context(self, **kwargs):
        if self.launch_entered is not None:
            self.launch_entered.set()
        if self.launch_gate is not None:
            await self.launch_gate.wait()
        context = FakeContext()
        self.contexts.append(context)
        self.launches.append((threading.get_ident(), kwargs))
        return context


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()
        self.stop_thread = None

    async def stop(self):
        self.stop_thread = threading.get_ident()


class FakeManager:
    def __init__(self, playwright):
        self.playwright = playwright
        self.start_thread = None

    async def start(self):
        self.start_thread = threading.get_ident()
        return self.playwright


class BrowserRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.playwright = FakePlaywright()
        self.manager = FakeManager(self.playwright)
        self.runtime = BrowserRuntime(lambda: self.manager)

    def tearDown(self):
        self.runtime.shutdown()

    def test_calls_and_patchright_lifecycle_stay_on_owner_thread(self):
        async def identify(_playwright):
            return threading.get_ident()

        call_thread = self.runtime.call(identify).result(2)
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot.state, RuntimeState.RUNNING)
        self.assertEqual(call_thread, snapshot.thread_id)
        self.assertEqual(self.manager.start_thread, snapshot.thread_id)

        self.runtime.shutdown()
        self.assertEqual(self.playwright.stop_thread, call_thread)
        self.assertEqual(self.runtime.snapshot().state, RuntimeState.STOPPED)

    def test_future_cancellation_reaches_async_task(self):
        entered = threading.Event()

        async def wait_forever(_playwright):
            entered.set()
            await asyncio.Event().wait()

        future = self.runtime.call(wait_forever)
        self.assertTrue(entered.wait(2))
        self.assertTrue(future.cancel())
        with self.assertRaises(CancelledError):
            future.result(2)

    def test_shutdown_before_start_is_idempotent(self):
        runtime = BrowserRuntime(lambda: self.manager)
        self.assertEqual(runtime.shutdown().state, RuntimeState.STOPPED)
        self.assertEqual(runtime.shutdown().state, RuntimeState.STOPPED)
        with self.assertRaises(RuntimeClosedError):
            runtime.call(lambda _playwright: asyncio.sleep(0))


class PatchrightBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.playwright = FakePlaywright()
        self.manager = FakeManager(self.playwright)
        self.runtime = BrowserRuntime(lambda: self.manager)
        self.browser = PatchrightBrowser(self.runtime)
        self.profile = self.temp_dir.name

    def tearDown(self):
        self.browser.shutdown(timeout=10)
        self.runtime.shutdown()
        self.temp_dir.cleanup()

    def open(self, **changes):
        values = {"profile_path": self.profile}
        values.update(changes)
        return self.browser.open_session(BrowserSessionConfig(**values)).result(10)

    def test_open_run_and_close_use_runtime_thread(self):
        result = self.open(
            viewport=(1280, 720),
            locale="en-US",
            geolocation={"latitude": 10.75, "longitude": 106.67, "accuracy": 50},
            permissions=("geolocation",),
            executable_path=r"C:\browser\chrome.exe",
        )
        owner = self.runtime.snapshot().thread_id
        launch_thread, kwargs = self.playwright.chromium.launches[0]
        self.assertEqual(launch_thread, owner)
        self.assertTrue(kwargs["headless"])
        self.assertEqual(kwargs["executable_path"], r"C:\browser\chrome.exe")
        self.assertEqual(kwargs["viewport"], {"width": 1280, "height": 720})
        self.assertEqual(kwargs["geolocation"]["latitude"], 10.75)
        self.assertEqual(kwargs["permissions"], ["geolocation"])

        async def inspect_page(page):
            return {"thread": threading.get_ident(), "values": [1, 2]}

        operation = self.browser.run(result.handle, inspect_page).result(2)
        self.assertEqual(operation.value["thread"], owner)
        self.assertEqual(operation.value["values"], (1, 2))
        with self.assertRaises(TypeError):
            operation.value["new"] = 3

        context = self.playwright.chromium.contexts[0]
        self.browser.close_session(result.handle).result(2)
        self.assertTrue(context.closed)
        self.assertEqual(context.close_thread, owner)

    def test_manual_session_is_always_headed_and_config_is_frozen(self):
        config = BrowserSessionConfig(self.profile, mode=SessionMode.MANUAL)
        self.assertTrue(config.headed)
        with self.assertRaises(FrozenInstanceError):
            config.headed = False
        self.browser.open_session(config).result(2)
        self.assertFalse(self.playwright.chromium.launches[0][1]["headless"])

    def test_profile_is_exclusive_and_reopen_increments_generation(self):
        first = self.open()
        with self.assertRaises(ProfileInUseError):
            self.open()
        self.browser.close_session(first.handle).result(2)
        second = self.open()
        self.assertGreater(second.handle.generation, first.handle.generation)
        with self.assertRaises(StaleSessionError):
            self.browser.export_cookies(first.handle).result(2)

    def test_close_session_removes_profile_from_registry(self):
        result = self.open()
        self.assertEqual(self.browser.status().active_sessions, 1)
        self.browser.close_session(result.handle).result(2)
        status = self.browser.status()
        self.assertEqual(status.active_sessions, 0)
        self.assertEqual(status.profile_paths, ())
        with self.assertRaises(StaleSessionError):
            self.browser.export_cookies(result.handle).result(2)

    def test_double_close_is_stale_but_does_not_hold_profile(self):
        result = self.open()
        self.browser.close_session(result.handle).result(2)
        with self.assertRaises(StaleSessionError):
            self.browser.close_session(result.handle).result(2)
        status = self.browser.status()
        self.assertEqual(status.active_sessions, 0)
        self.assertEqual(status.profile_paths, ())
        reopened = self.open()
        self.assertGreater(reopened.handle.generation, result.handle.generation)

    def test_status_stays_synced_after_open_close_reopen(self):
        first = self.open()
        self.assertEqual(self.browser.status().active_sessions, 1)
        self.browser.close_session(first.handle).result(2)
        self.assertEqual(self.browser.status().active_sessions, 0)
        second = self.open()
        self.assertEqual(self.browser.status().active_sessions, 1)
        self.browser.close_session(second.handle).result(2)
        self.assertEqual(self.browser.status().active_sessions, 0)
        self.assertEqual(self.browser.status().profile_paths, ())

    def test_cookie_import_and_export(self):
        session = self.open()
        exported = self.browser.export_cookies(session.handle).result(2)
        self.assertEqual(json.loads(exported.cookies_json)[0]["name"], "sid")
        report = self.browser.import_cookies(
            session.handle, '[{"name":"token","value":"x","url":"https://example.test"}]'
        ).result(2).value
        self.assertEqual(report.requested, 1)
        self.assertEqual(report.accepted, 1)
        self.assertEqual(report.auth_requested, 0)
        self.assertEqual(
            self.playwright.chromium.contexts[0].added_cookies[0]["name"], "token"
        )

    def test_import_replaces_tiktok_cookies_only_and_reports_auth(self):
        from patchright_browser import CookieImportReport

        session = self.open()
        report = self.browser.import_cookies(
            session.handle,
            [
                {"name": "sessionid", "value": "sess", "domain": ".tiktok.com", "path": "/"},
                {"name": "msToken", "value": "guest", "domain": ".tiktok.com", "path": "/"},
                {"name": "other", "value": "x", "domain": ".example.test", "path": "/"},
            ],
        ).result(2).value
        self.assertIsInstance(report, CookieImportReport)
        self.assertEqual(report.accepted, 3)
        self.assertEqual(report.auth_requested, 1)
        self.assertEqual(report.auth_accepted, 1)
        self.assertEqual(report.missing_auth_names, ())
        context = self.playwright.chromium.contexts[0]
        tiktok = [c for c in context.added_cookies if "tiktok.com" in str(c.get("domain", ""))]
        self.assertEqual(len(tiktok), 2)

    def test_import_rolls_back_tiktok_cookies_on_add_failure(self):
        session = self.open()
        context = self.playwright.chromium.contexts[0]
        context.added_cookies.append({"name": "old", "value": "1", "domain": ".tiktok.com", "path": "/"})
        original_add = context.add_cookies

        async def flaky(cookies):
            if getattr(context, "_fail_next", False):
                context._fail_next = False
                raise RuntimeError("add failed")
            return await original_add(cookies)

        context.add_cookies = flaky
        context._fail_next = True
        with self.assertRaisesRegex(RuntimeError, "add failed"):
            self.browser.import_cookies(session.handle, [{"name": "new", "value": "2", "domain": ".tiktok.com", "path": "/"}]).result(2)
        names = [c["name"] for c in context.added_cookies]
        self.assertIn("old", names)
        self.assertNotIn("new", names)

    def test_operation_cannot_return_owned_page(self):
        session = self.open()

        async def leak(page):
            return page

        with self.assertRaises(TypeError):
            self.browser.run(session.handle, leak).result(2)

    def test_frozen_dataclass_operation_results_remain_typed_and_immutable(self):
        session = self.open()

        async def result(_page):
            return FakeOperationValue("posted", {"id": "123"})

        value = self.browser.run(session.handle, result).result(2).value
        self.assertIsInstance(value, FakeOperationValue)
        self.assertEqual(value.details["id"], "123")
        with self.assertRaises(TypeError):
            value.details["id"] = "changed"

    def test_cancel_session_cancels_active_operations(self):
        session = self.open()
        entered = threading.Event()

        async def wait(_page):
            entered.set()
            await asyncio.Event().wait()

        operation = self.browser.run(session.handle, wait)
        self.assertTrue(entered.wait(2))
        cancelled = self.browser.cancel_session(session.handle).result(2)
        self.assertEqual(cancelled.value, 1)
        with self.assertRaises(CancelledError):
            operation.result(2)

    def test_status_and_shutdown(self):
        self.open()
        status = self.browser.status()
        self.assertEqual(status.active_sessions, 1)
        self.assertEqual(status.profile_paths, (os.path.normcase(str(self.profile)),))
        stopped = self.browser.shutdown()
        self.assertTrue(stopped.closed)
        self.assertEqual(stopped.active_sessions, 0)
        with self.assertRaises(BrowserServiceClosedError):
            self.browser.open_session(BrowserSessionConfig(self.profile))

    def test_shutdown_cancels_a_context_still_opening(self):
        entered = threading.Event()
        self.playwright.chromium.launch_entered = entered

        async def install_gate(_playwright):
            self.playwright.chromium.launch_gate = asyncio.Event()

        self.runtime.call(install_gate).result(2)
        opening = self.browser.open_session(BrowserSessionConfig(self.profile))
        self.assertTrue(entered.wait(2))
        self.browser.shutdown()
        with self.assertRaises(CancelledError):
            opening.result(2)
        self.assertEqual(self.browser.status().opening_sessions, 0)
        self.assertEqual(self.browser.status().profile_paths, ())


if __name__ == "__main__":
    unittest.main()
