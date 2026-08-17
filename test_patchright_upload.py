import asyncio
import tempfile
import unittest
from pathlib import Path

from patchright_upload import SELECTORS, UploadTimeouts, upload_tiktok


class FakeElement:
    def __init__(self, *, visible=True, enabled=True, text="", on_click=None, attributes=None):
        self.visible = visible
        self.enabled = enabled
        self.text = text
        self.on_click = on_click
        self.attributes = attributes or {}
        self.clicks = 0
        self.files = []

    async def is_visible(self):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def get_attribute(self, name):
        return self.attributes.get(name)

    async def inner_text(self):
        return self.text

    async def click(self):
        self.clicks += 1
        if self.on_click:
            result = self.on_click()
            if asyncio.iscoroutine(result):
                await result

    async def set_input_files(self, path):
        self.files.append(path)


class FakeLocator:
    def __init__(self, elements=()):
        self.elements = list(elements)

    @property
    def first(self):
        return self.elements[0]

    async def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]


class FakeRequest:
    method = "POST"


class FakeResponse:
    url = "https://www.tiktok.com/api/v1/tiktok/web/project/post/submit"
    status = 200
    request = FakeRequest()

    async def json(self):
        return {"status_code": 0}


class FakePostRequest:
    method = "POST"
    url = "https://www.tiktok.com/api/v1/tiktok/web/project/post/submit"


class StatefulLocator:
    def __init__(self, get_elements):
        self._get = get_elements

    @property
    def first(self):
        return self._get()[0]

    @property
    def elements(self):
        return self._get()

    async def count(self):
        return len(self._get())

    def nth(self, index):
        return self._get()[index]


class FakePage:
    def __init__(self, post_click=None):
        self.url = "about:blank"
        self.listeners = {}
        self.file_input = FakeElement()
        self.post_button = FakeElement(on_click=post_click)
        self.readiness_snapshot = None
        self.evaluate_calls = 0
        self.locators = {
            SELECTORS["file_input"]: FakeLocator([self.file_input]),
            SELECTORS["upload_surface"]: FakeLocator([FakeElement()]),
            SELECTORS["post_button"]: FakeLocator([self.post_button]),
            SELECTORS["editor_signals"]: FakeLocator([FakeElement()]),
        }

    async def goto(self, url, **kwargs):
        self.url = url

    def locator(self, selector):
        return self.locators.get(selector, FakeLocator())

    def on(self, event, callback):
        self.listeners[event] = callback

    def remove_listener(self, event, callback):
        if self.listeners.get(event) is callback:
            del self.listeners[event]

    def emit_response(self, response):
        self.listeners["response"](response)

    def emit_request(self, request):
        self.listeners["request"](request)

    async def evaluate(self, script):
        self.evaluate_calls += 1
        if self.readiness_snapshot is None:
            raise AttributeError("evaluate is not available")
        return self.readiness_snapshot

    async def content(self):
        return "<html></html>"

    async def screenshot(self, **kwargs):
        return b""


class PatchrightUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.video = Path(self.temp_dir.name) / "video.mp4"
        self.video.write_bytes(b"video")
        self.timeouts = UploadTimeouts(
            page_ready=0.2,
            editor_ready=0.2,
            confirmation=0.5,
            poll_interval=0.001,
            navigation_ms=200,
            popup_dismiss_timeout=0.2,
            popup_dismiss_max_rounds=3,
            pre_dispatch_clear_timeout=0.2,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_network_confirmation_uses_exactly_one_post_click(self):
        page = FakePage()
        page.post_button.on_click = lambda: page.emit_response(FakeResponse())

        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "posted")
        self.assertTrue(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 1)

    async def test_readiness_snapshot_fast_path_posts_once(self):
        page = FakePage()
        page.post_button.on_click = lambda: page.emit_response(FakeResponse())
        page.readiness_snapshot = {
            "post_actionable": True,
            **{name: False for name, _ in SELECTORS["safe_popups"]},
        }

        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "posted")
        self.assertEqual(page.post_button.clicks, 1)
        self.assertGreater(page.evaluate_calls, 0)
        self.assertIn("timings", result.details)
        self.assertIn("wait_editor_seconds", result.details["timings"])
        self.assertIn("confirmation_seconds", result.details["timings"])

    async def test_confirmation_timeout_never_retries_post_click(self):
        page = FakePage()

        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "post_uncertain")
        self.assertTrue(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 1)

    async def test_cancel_before_dispatch_is_safe_and_does_not_click(self):
        page = FakePage()
        cancelled = asyncio.Event()
        cancelled.set()

        result = await upload_tiktok(
            page,
            self.video,
            cancellation_event=cancelled,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "cancelled_safe")
        self.assertFalse(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 0)

    async def test_cancel_after_dispatch_is_uncertain_and_never_second_clicks(self):
        cancelled = asyncio.Event()
        page = FakePage(post_click=cancelled.set)

        result = await upload_tiktok(
            page,
            self.video,
            cancellation_event=cancelled,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "cancelled_uncertain")
        self.assertTrue(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 1)

    async def test_click_exception_after_request_seen_is_uncertain_without_retry(self):
        page = FakePage()

        async def fail_click_after_request():
            page.emit_request(FakePostRequest())
            await asyncio.sleep(0)
            raise RuntimeError("browser disconnected")

        page.post_button.on_click = fail_click_after_request
        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "post_uncertain")
        self.assertTrue(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 1)
        self.assertTrue(result.details.get("request_seen"))

    async def test_click_exception_before_request_is_failed_and_retryable(self):
        page = FakePage()

        def fail_click():
            raise RuntimeError("overlay intercepts pointer events")

        page.post_button.on_click = fail_click
        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "failed")
        self.assertFalse(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 1)
        self.assertFalse(result.details.get("request_seen"))

    async def test_stop_before_post_prepares_without_clicking(self):
        page = FakePage()
        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
            stop_before_post=True,
        )

        self.assertEqual(result.outcome, "prepared")
        self.assertFalse(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 0)

    def _got_it_selector(self):
        for name, selector in SELECTORS["safe_popups"]:
            if name == "joyride_got_it":
                return selector
        self.fail("joyride_got_it popup not defined")

    def _content_checks_selector(self):
        for name, selector in SELECTORS["safe_popups"]:
            if name == "content_checks_cancel":
                return selector
        self.fail("content_checks_cancel popup not defined")

    def _popup_page(self, *, content_checks_first=False, sticky_overlay=False):
        page = FakePage()
        page.post_button.on_click = lambda: page.emit_response(FakeResponse())
        overlay = FakeElement()
        got_it = FakeElement()
        content = FakeElement()

        def on_got_it():
            got_it.visible = False
            if not sticky_overlay:
                overlay.visible = False

        def on_content():
            content.visible = False
            if content_checks_first:
                got_it.visible = True
                overlay.visible = True

        got_it.on_click = on_got_it
        content.on_click = on_content
        state = {"got_it": got_it, "overlay": overlay, "content": content}

        def elems(key):
            def get():
                return [state[key]] if state[key].visible else []

            return StatefulLocator(get)

        page.locators[SELECTORS["joyride_overlay"]] = elems("overlay")
        page.locators[SELECTORS["joyride_root"]] = elems("got_it")
        page.locators[self._got_it_selector()] = elems("got_it")
        page.locators[self._content_checks_selector()] = elems("content")

        if content_checks_first:
            content.visible = True
            got_it.visible = False
            overlay.visible = False
        else:
            got_it.visible = True
            overlay.visible = True
            content.visible = False
        return page, state

    async def test_got_it_popup_dismissed_then_post_clicks_once(self):
        page, state = self._popup_page()

        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "posted")
        self.assertEqual(state["got_it"].clicks, 1)
        self.assertEqual(page.post_button.clicks, 1)

    async def test_sequential_popups_content_checks_then_got_it(self):
        page, state = self._popup_page(content_checks_first=True)

        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "posted")
        self.assertEqual(state["content"].clicks, 1)
        self.assertEqual(state["got_it"].clicks, 1)
        self.assertEqual(page.post_button.clicks, 1)

    async def test_sticky_joyride_overlay_blocks_post_before_dispatch(self):
        page, state = self._popup_page(sticky_overlay=True)

        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "failed")
        self.assertFalse(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 0)

    async def test_overlay_without_tooltip_blocks_post(self):
        page = FakePage()
        page.post_button.on_click = lambda: page.emit_response(FakeResponse())
        overlay = FakeElement()
        page.locators[SELECTORS["joyride_overlay"]] = FakeLocator([overlay])

        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "failed")
        self.assertFalse(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 0)

    async def test_ambiguous_got_it_popup_fails_closed_before_post(self):
        page = FakePage()
        page.post_button.on_click = lambda: page.emit_response(FakeResponse())
        got_it_one = FakeElement()
        got_it_two = FakeElement()
        page.locators[self._got_it_selector()] = FakeLocator([got_it_one, got_it_two])

        result = await upload_tiktok(
            page,
            self.video,
            timeouts=self.timeouts,
            diagnostics_dir=None,
        )

        self.assertEqual(result.outcome, "failed")
        self.assertFalse(result.post_dispatched)
        self.assertEqual(page.post_button.clicks, 0)


if __name__ == "__main__":
    unittest.main()
