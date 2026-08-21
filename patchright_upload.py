"""Standalone, single-dispatch TikTok uploader for Patchright's async API.

The caller owns the Patchright browser, context, and page lifecycle.  This
module deliberately has no import-time Patchright dependency, which also makes
the upload state machine straightforward to test with fakes.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video"
POST_ENDPOINT_FRAGMENT = "/tiktok/web/project/post/"

# Keep all TikTok DOM knowledge here. Exact popup text is intentional: broad
# Cancel/Close selectors can cancel the upload or interact with unrelated UI.
SELECTORS: Mapping[str, Any] = {
    "file_input": "input[type='file'][accept*='video'], input[type='file']",
    "upload_surface": (
        "div[data-e2e='select_video_container'], div[data-e2e='upload-card'], "
        "div[data-e2e='video-caption-editor'], div[data-e2e='publish-settings']"
    ),
    "editor_signals": (
        "div[data-e2e='upload-progress'], div[data-e2e='upload-loading'], "
        "div[data-e2e='video-caption-editor'], "
        "div[data-e2e='video-caption-editor-container'], "
        "div[data-e2e='recommend-caption-editor'], "
        "div[data-e2e='publish-settings'], div[data-e2e='caption-editor'], "
        "div[data-e2e='upload-card'], textarea"
    ),
    "post_button": "button[data-e2e='post_video_button']",
    "login": "form[action*='login'], input[name='username'], [data-e2e='login-button']",
    "visible_dialog": "[role='dialog']:visible, .TUXModal:visible",
    "joyride_root": ".react-joyride__tooltip",
    "joyride_overlay": "#react-joyride-portal .react-joyride__overlay",
    "safe_popups": (
        (
            "cancel_upload_no",
            "xpath=//div[(contains(@class,'TUXModal') or @role='dialog') "
            "and .//*[normalize-space()='Sure you want to cancel your upload?']]"
            "//button[.//*[normalize-space()='No'] or normalize-space()='No']",
        ),
        (
            "content_checks_cancel",
            "xpath=//div[(contains(@class,'TUXModal') or @role='dialog') "
            "and (.//*[normalize-space()='Turn on automatic content checks?'] "
            "or .//*[normalize-space()='コンテンツの自動チェックをオンにしますか？'])]"
            "//button[.//*[normalize-space()='Cancel' or normalize-space()='キャンセル'] "
            "or normalize-space()='Cancel' or normalize-space()='キャンセル']",
        ),
        (
            "joyride_skip",
            ".react-joyride__tooltip button[data-action='skip'], "
            ".react-joyride__tooltip [aria-label='Skip']",
        ),
        (
            "joyride_close",
            ".react-joyride__tooltip button[data-action='close'], "
            ".react-joyride__tooltip [aria-label='Close']",
        ),
        (
            "joyride_got_it",
            "xpath=//div[contains(@class, 'react-joyride__tooltip')]"
            "//button[normalize-space(.)='Got it' or normalize-space(.)='OK' "
            "or .//*[normalize-space()='Got it' or normalize-space()='OK'] "
            "or (@data-type='primary' and ancestor::div[contains(@class,'tutorial-tooltip__footer')])]",
        ),
    ),
    "known_popup_roots": (
        "xpath=//div[(contains(@class,'TUXModal') or @role='dialog') and "
        "(.//*[normalize-space()='Sure you want to cancel your upload?'] or "
        ".//*[normalize-space()='Turn on automatic content checks?'])]"
    ),
    "success": (
        "[role='alert']:has-text('Your video has been posted'), "
        "[role='status']:has-text('Video has been posted'), "
        "[role='alert']:has-text('Content under review')"
    ),
    "rejection": (
        "[role='alert']:has-text(\"couldn't upload\"), "
        "[role='alert']:has-text('Could not upload'), "
        "[role='alert']:has-text('Upload failed'), "
        "[role='dialog']:has-text('Something went wrong')"
    ),
}

_READINESS_JS = (
    "(() => {"
    "const MAP = %s;"
    "const vis = el => {"
    " if (!el) return false;"
    " const st = getComputedStyle(el);"
    " if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;"
    " const r = el.getBoundingClientRect();"
    " return r.width > 0 && r.height > 0;"
    "};"
    "const nodes = sel => {"
    " try {"
    "  if (sel.startsWith('xpath=')) {"
    "   const rr = document.evaluate(sel.slice(6), document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);"
    "   const out = []; for (let i = 0; i < rr.snapshotLength; i++) out.push(rr.snapshotItem(i));"
    "   return out;"
    "  }"
    "  return Array.prototype.slice.call(document.querySelectorAll(sel));"
    " } catch (e) { return []; }"
    "};"
    "const out = { post_actionable: false };"
    "for (const el of nodes(MAP.__post__)) {"
    " if (vis(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true') { out.post_actionable = true; break; }"
    "}"
    "for (const key of Object.keys(MAP)) { if (key === '__post__') continue; out[key] = nodes(MAP[key]).some(vis); }"
    "return out;"
    "})()"
) % json.dumps({**{name: sel for name, sel in SELECTORS["safe_popups"]}, "__post__": SELECTORS["post_button"]})

OUTCOMES = frozenset(
    {
        "posted",
        "login_required",
        "rejected",
        "failed",
        "cancelled_safe",
        "cancelled_uncertain",
        "post_uncertain",
        "prepared",
    }
)

# Rejection scope classifier: tells the caller whether a confirmed rejection applies to
# the whole account or only to one video. Only explicit, evidence-based account-level
# patterns are ever classified as account_posting_blocked; a generic per-video rejection
# must never be inferred as an account ban.
REJECTION_SCOPE_ACCOUNT_BLOCKED = "account_posting_blocked"
REJECTION_SCOPE_VIDEO = "video_rejected"
REJECTION_SCOPE_UNKNOWN = "unknown_rejection"

# New patterns are only added when backed by a real payload / official documentation.
ACCOUNT_POSTING_BLOCK_PATTERNS = (
    re.compile(r"temporarily\s+prevented\s+from\s+posting", re.IGNORECASE),
)


@dataclass(frozen=True)
class UploadTimeouts:
    page_ready: float = 60.0
    editor_ready: float = 600.0
    confirmation: float = 30.0
    poll_interval: float = 0.15
    navigation_ms: float = 60_000.0
    popup_dismiss_timeout: float = 20.0
    popup_dismiss_max_rounds: int = 8
    pre_dispatch_clear_timeout: float = 15.0


@dataclass(frozen=True)
class UploadResult:
    outcome: str
    message: str
    post_dispatched: bool = False
    confirmation: Optional[str] = None
    diagnostic_paths: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def posted(self) -> bool:
        return self.outcome == "posted"


StatusCallback = Callable[[str], Any]


class _Cancelled(Exception):
    pass


class _LoginRequired(Exception):
    pass


class _Rejected(Exception):
    pass


async def upload_tiktok(
    page: Any,
    video_path: str | Path,
    *,
    status_callback: Optional[StatusCallback] = None,
    cancellation_event: Any = None,
    timeouts: Optional[UploadTimeouts] = None,
    diagnostics_dir: str | Path | None = "patchright_diagnostics",
    upload_url: str = UPLOAD_URL,
    stop_before_post: bool = False,
) -> UploadResult:
    """Upload and publish one video using an existing async Patchright Page.

    There is exactly one call to the Post locator's ``click`` method. Once the
    dispatch boundary is crossed, exceptions and cancellation never restart or
    click again; they produce an uncertainty outcome unless TikTok explicitly
    confirms success or rejection. A click that fails before any POST request
    to the publish endpoint was observed is treated as a pre-dispatch failure
    and returns ``failed`` so the caller may retry safely.
    """

    cfg = timeouts or UploadTimeouts()
    path = Path(video_path).expanduser().resolve()
    post_dispatched = False
    response_state: dict[str, Any] = {}
    dispatch_state: dict[str, Any] = {"request_seen": False, "request_url": None}
    response_tasks: set[asyncio.Task[Any]] = set()
    timings: dict[str, float] = {}

    async def report(message: str) -> None:
        if status_callback is None:
            return
        try:
            result = status_callback(message)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # UI/logging callbacks must not change upload safety semantics.
            pass

    async def record_response(response: Any) -> None:
        try:
            url = str(getattr(response, "url", ""))
            request = getattr(response, "request", None)
            method = str(getattr(request, "method", "POST")).upper()
            if POST_ENDPOINT_FRAGMENT not in url or method != "POST":
                return
            status = int(getattr(response, "status", 0) or 0)
            try:
                payload = await response.json()
            except Exception:
                try:
                    payload = {"raw_body": (await response.text())[:2000]}
                except Exception:
                    payload = {}
            code = payload.get("status_code", payload.get("statusCode")) if isinstance(payload, dict) else None
            response_state.update(http_status=status, status_code=code, payload=payload, seen=True)
            if not 200 <= status < 300 or (code is not None and code != 0):
                response_state["rejected"] = True
            elif code == 0:
                response_state["confirmed"] = True
        except Exception as error:
            response_state["listener_error"] = str(error)

    def response_listener(response: Any) -> None:
        task = asyncio.create_task(record_response(response))
        response_tasks.add(task)
        task.add_done_callback(response_tasks.discard)

    async def record_request(request: Any) -> None:
        try:
            url = str(getattr(request, "url", ""))
            method = str(getattr(request, "method", "")).upper()
            if POST_ENDPOINT_FRAGMENT in url and method == "POST":
                dispatch_state["request_seen"] = True
                dispatch_state["request_url"] = url
        except Exception:
            pass

    def request_listener(request: Any) -> None:
        task = asyncio.create_task(record_request(request))
        response_tasks.add(task)
        task.add_done_callback(response_tasks.discard)

    def result(outcome: str, message: str, **kwargs: Any) -> UploadResult:
        assert outcome in OUTCOMES
        return UploadResult(
            outcome=outcome,
            message=message,
            post_dispatched=post_dispatched,
            details=dict({**response_state, **dispatch_state, "timings": dict(timings)}),
            **kwargs,
        )

    try:
        if not path.is_file():
            return result("failed", f"Video file does not exist: {path}")
        _raise_if_cancelled(cancellation_event)
        await report(f"Opening TikTok upload page for {path.name}")
        _t = time.perf_counter()
        if await upload_surface_ready(page):
            timings["goto_seconds"] = 0.0
            await report("Upload surface already ready, skipping navigation")
        else:
            await page.goto(upload_url, wait_until="domcontentloaded", timeout=cfg.navigation_ms)
            timings["goto_seconds"] = time.perf_counter() - _t

        _t = time.perf_counter()
        page_state = await _wait_for_page_state(page, cancellation_event, cfg)
        timings["page_ready_seconds"] = time.perf_counter() - _t
        if page_state == "login":
            raise _LoginRequired("TikTok redirected to or displayed login")

        file_input = page.locator(SELECTORS["file_input"]).first
        _raise_if_cancelled(cancellation_event)
        _t = time.perf_counter()
        await file_input.set_input_files(str(path))
        timings["file_select_seconds"] = time.perf_counter() - _t
        await report(f"Video selected: {path.name}")

        _t = time.perf_counter()
        post_button = await _wait_for_editor(page, cancellation_event, cfg, report)
        timings["wait_editor_seconds"] = time.perf_counter() - _t
        _t = time.perf_counter()
        await _ensure_post_clear(page, report, cancellation_event, cfg)
        timings["popup_clear_seconds"] = time.perf_counter() - _t
        post_button = await _actionable_post_button(page) or post_button
        if post_button is None:
            raise RuntimeError("No actionable TikTok Post button")

        if stop_before_post:
            await report("Pre-Post dry-run: editor ready and TikTok blockers clear")
            return result(
                "prepared",
                "Pre-Post dry-run completed; Post was not clicked",
            )

        baseline_success = set(await _visible_texts(page.locator(SELECTORS["success"])))
        baseline_url = str(getattr(page, "url", ""))

        # Register before click. This listener remains active throughout the
        # confirmation window so a fast request/response cannot be missed.
        page.on("request", request_listener)
        page.on("response", response_listener)
        await report(f"Dispatching Post for {path.name}")
        _raise_if_cancelled(cancellation_event)
        _t = time.perf_counter()
        try:
            await post_button.click()
        except Exception as error:
            timings["click_seconds"] = time.perf_counter() - _t
            if dispatch_state.get("request_seen"):
                post_dispatched = True
                raise
            raise RuntimeError(
                f"Post click was blocked before dispatch (TikTok overlay/popup intercept): {error}"
            )
        timings["click_seconds"] = time.perf_counter() - _t
        post_dispatched = True

        _t = time.perf_counter()
        confirmation = await _wait_for_confirmation(
            page,
            cancellation_event,
            cfg,
            response_state,
            baseline_success,
            baseline_url,
        )
        timings["confirmation_seconds"] = time.perf_counter() - _t
        if confirmation == "rejected":
            raise _Rejected(_rejection_message(response_state))
        await report(f"TikTok confirmed Post via {confirmation}: {path.name}")
        return result("posted", "TikTok confirmed the post", confirmation=confirmation)
    except _LoginRequired as error:
        paths = await _capture_diagnostics(page, path.name, diagnostics_dir, await _collect_upload_metadata(page))
        return result("login_required", str(error), diagnostic_paths=paths)
    except _Rejected as error:
        response_state["rejection_scope"] = classify_rejection(response_state)
        paths = await _capture_diagnostics(page, path.name, diagnostics_dir, await _collect_upload_metadata(page))
        return result("rejected", str(error), diagnostic_paths=paths, confirmation="network_or_dom_rejection")
    except _Cancelled:
        paths = await _capture_diagnostics(page, path.name, diagnostics_dir, await _collect_upload_metadata(page))
        outcome = "cancelled_uncertain" if post_dispatched else "cancelled_safe"
        message = "Cancellation observed after Post dispatch" if post_dispatched else "Cancelled before Post dispatch"
        return result(outcome, message, diagnostic_paths=paths)
    except Exception as error:
        paths = await _capture_diagnostics(page, path.name, diagnostics_dir, await _collect_upload_metadata(page))
        outcome = "post_uncertain" if post_dispatched else "failed"
        return result(outcome, str(error), diagnostic_paths=paths)
    finally:
        try:
            page.remove_listener("response", response_listener)
        except Exception:
            pass
        try:
            page.remove_listener("request", request_listener)
        except Exception:
            pass
        if response_tasks:
            await asyncio.gather(*tuple(response_tasks), return_exceptions=True)


async def _wait_for_page_state(page: Any, cancellation_event: Any, cfg: UploadTimeouts) -> str:
    deadline = time.monotonic() + cfg.page_ready
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancellation_event)
        if _is_login_url(str(getattr(page, "url", ""))) or await _has_visible(page.locator(SELECTORS["login"])):
            return "login"
        if await _has_visible(page.locator(SELECTORS["file_input"])) or await _has_visible(page.locator(SELECTORS["upload_surface"])):
            return "upload"
        await _cancel_aware_sleep(cancellation_event, cfg.poll_interval)
    if _is_login_url(str(getattr(page, "url", ""))):
        return "login"
    raise TimeoutError(f"TikTok upload page was not ready within {cfg.page_ready:g}s")


async def upload_surface_ready(page: Any) -> bool:
    """Fast DOM check: is the upload surface already visible on the live page?

    Returns True only when the upload surface (file input or upload card) is
    currently visible and the page is not a login page. This lets the upload
    path reuse the already-navigated prewarmed session instead of issuing a
    second navigation. The result is never trusted absolutely: a missing/blank
    surface falls back to the regular navigation path."""
    try:
        if _is_login_url(str(getattr(page, "url", ""))):
            return False
        if await _has_visible(page.locator(SELECTORS["login"])):
            return False
        return await _has_visible(page.locator(SELECTORS["file_input"])) or await _has_visible(
            page.locator(SELECTORS["upload_surface"])
        )
    except Exception:
        return False


async def _wait_for_editor(
    page: Any,
    cancellation_event: Any,
    cfg: UploadTimeouts,
    report: Callable[[str], Any],
) -> Any:
    deadline = time.monotonic() + cfg.editor_ready
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancellation_event)
        if _is_login_url(str(getattr(page, "url", ""))) or await _has_visible(page.locator(SELECTORS["login"])):
            raise _LoginRequired("TikTok requested login while preparing the upload")
        snapshot = await _readiness_snapshot(page)
        if snapshot is None:
            # Fallback: the page does not expose a JS snapshot, keep the
            # locator-based polling unchanged.
            await _dismiss_safe_popups(page, report, cancellation_event, cfg)
            button = await _actionable_post_button(page)
            if button is not None:
                return button
        else:
            # Fast path: one read-only evaluate per poll instead of many CDP
            # round-trips. Clicks still use real locators only.
            if snapshot["post_actionable"]:
                button = await _actionable_post_button(page)
                if button is not None:
                    return button
            if any(snapshot["popup_visible"].values()):
                await _dismiss_safe_popups(page, report, cancellation_event, cfg)
        # Presence of editor signals keeps polling bounded but is not treated
        # as readiness: only an actionable Post button permits dispatch.
        await _has_visible(page.locator(SELECTORS["editor_signals"]))
        await _cancel_aware_sleep(cancellation_event, cfg.poll_interval)
    raise TimeoutError(f"TikTok Post button was not actionable within {cfg.editor_ready:g}s")


async def _readiness_snapshot(page: Any) -> Optional[dict[str, Any]]:
    """Return a single read-only readiness snapshot, or None when unavailable.

    One ``page.evaluate`` call replaces the many locator round-trips that a
    poll would otherwise make while the renderer is busy uploading/processing.
    Values are only used to *decide* whether to act; every click still goes
    through a real locator, so the dispatch safety semantics are unchanged."""
    try:
        raw = await page.evaluate(_READINESS_JS)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    popup_names = [name for name, _ in SELECTORS["safe_popups"]]
    return {
        "post_actionable": bool(raw.get("post_actionable")),
        "popup_visible": {name: bool(raw.get(name)) for name in popup_names},
    }


async def _wait_for_confirmation(
    page: Any,
    cancellation_event: Any,
    cfg: UploadTimeouts,
    response_state: dict[str, Any],
    baseline_success: set[str],
    baseline_url: str,
) -> str:
    deadline = time.monotonic() + cfg.confirmation
    success_url_parts = ("/tiktokstudio/content", "/creator-center/content", "/manage")
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancellation_event)
        if response_state.get("rejected"):
            return "rejected"
        if response_state.get("confirmed"):
            return "network"
        if await _has_visible(page.locator(SELECTORS["rejection"])):
            return "rejected"
        success_texts = set(await _visible_texts(page.locator(SELECTORS["success"])))
        if success_texts - baseline_success:
            return "dom"
        url = str(getattr(page, "url", "")).lower()
        if url != baseline_url.lower() and "/upload" not in url and any(part in url for part in success_url_parts):
            return "url"
        await _cancel_aware_sleep(cancellation_event, cfg.poll_interval)
    raise TimeoutError(f"TikTok did not confirm the post within {cfg.confirmation:g}s")


async def _dismiss_safe_popups(
    page: Any,
    report: Callable[[str], Any],
    cancellation_event: Any,
    cfg: UploadTimeouts,
) -> None:
    """Dismiss identified safe popups one at a time until none remain.

    TikTok can show several popups in sequence (e.g. content-checks modal then
    an onboarding tooltip). Each round finds at most one identifiable action,
    clicks it, and waits for that action's popup to close before looking for the
    next one. A bare Joyride overlay without an identifiable action is left
    alone during polling: the onboarding tooltip may still be rendering, and the
    pre-dispatch guard enforces overlay clearance only right before Post.
    Ambiguous matches or a stuck overlay fail closed instead of guessing."""
    popup_selectors = dict(SELECTORS["safe_popups"])
    deadline = time.monotonic() + cfg.popup_dismiss_timeout
    for _round in range(1, cfg.popup_dismiss_max_rounds + 1):
        _raise_if_cancelled(cancellation_event)
        if time.monotonic() >= deadline:
            break
        action = await _find_safe_popup_action(page)
        if action is None:
            return
        name, locator = action
        await locator.click()
        await report(f"Dismissed identified safe TikTok popup: {name}")
        await _wait_for_popup_clear(
            page,
            name,
            popup_selectors[name],
            deadline,
            cancellation_event,
            cfg,
        )
    raise RuntimeError("TikTok safe popups did not clear within the allowed time")


async def _find_safe_popup_action(page: Any):
    """Return the first visible identified safe-popup action, else None.

    Raises when one selector is ambiguous. Several independently identified
    blockers may coexist; SELECTORS order is the deterministic dismissal order."""
    for name, selector in SELECTORS["safe_popups"]:
        locator = page.locator(selector)
        count = await locator.count()
        visible_indices = [
            index for index in range(count) if await locator.nth(index).is_visible()
        ]
        if len(visible_indices) == 1:
            return name, locator.nth(visible_indices[0])
        elif len(visible_indices) > 1:
            raise RuntimeError(
                f"Ambiguous safe popup selector {name}: {len(visible_indices)} matches"
            )
    return None


async def _wait_for_popup_clear(
    page: Any,
    name: str,
    selector: str,
    deadline: float,
    cancellation_event: Any,
    cfg: UploadTimeouts,
) -> None:
    """Wait until the popup that was just dismissed is gone.

    Joyride actions additionally require the onboarding overlay to disappear,
    because the overlay is what actually intercepts the Post button."""
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancellation_event)
        locator = page.locator(selector)
        visible = [await locator.nth(i).is_visible() for i in range(await locator.count())]
        if not any(visible):
            if name.startswith("joyride") and await _has_visible(
                page.locator(SELECTORS["joyride_overlay"])
            ):
                await _cancel_aware_sleep(cancellation_event, cfg.poll_interval)
                continue
            return
        await _cancel_aware_sleep(cancellation_event, cfg.poll_interval)
    raise RuntimeError(f"TikTok safe popup {name} did not close within the allowed time")


async def _ensure_post_clear(
    page: Any,
    report: Callable[[str], Any],
    cancellation_event: Any,
    cfg: UploadTimeouts,
) -> None:
    """Clear every blocker that would intercept the Post button.

    The onboarding tooltip can appear shortly after the editor becomes ready, so
    this polls: each pass dismisses any identifiable safe popup, then checks the
    Joyride overlay/tooltip and unknown dialogs. Only a blocker that survives
    the whole deadline fails closed before dispatch."""
    deadline = time.monotonic() + cfg.pre_dispatch_clear_timeout
    while True:
        _raise_if_cancelled(cancellation_event)
        await _dismiss_safe_popups(page, report, cancellation_event, cfg)
        joyride_blocked = await _has_visible(
            page.locator(SELECTORS["joyride_overlay"])
        ) or await _has_visible(page.locator(SELECTORS["joyride_root"]))
        if not joyride_blocked:
            await _guard_against_unknown_dialog(page)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("TikTok Joyride overlay/tooltip still blocks Post dispatch")
        await _cancel_aware_sleep(cancellation_event, cfg.poll_interval)


async def _guard_against_unknown_dialog(page: Any) -> None:
    dialogs = page.locator(SELECTORS["visible_dialog"])
    known = page.locator(SELECTORS["known_popup_roots"])
    visible_dialogs = 0
    for index in range(await dialogs.count()):
        visible_dialogs += bool(await dialogs.nth(index).is_visible())
    visible_known = 0
    for index in range(await known.count()):
        visible_known += bool(await known.nth(index).is_visible())
    if visible_dialogs > visible_known:
        raise RuntimeError("An unidentified TikTok dialog blocks safe Post dispatch")
    if await _has_visible(page.locator(SELECTORS["joyride_overlay"])):
        raise RuntimeError("TikTok Joyride overlay blocks safe Post dispatch")
    if await _has_visible(page.locator(SELECTORS["joyride_root"])):
        raise RuntimeError("TikTok Joyride tooltip blocks safe Post dispatch")


async def _actionable_post_button(page: Any) -> Any:
    locator = page.locator(SELECTORS["post_button"])
    for index in range(await locator.count()):
        button = locator.nth(index)
        if not await button.is_visible() or not await button.is_enabled():
            continue
        if (await button.get_attribute("aria-disabled")) == "true":
            continue
        return button
    return None


async def _has_visible(locator: Any) -> bool:
    for index in range(await locator.count()):
        if await locator.nth(index).is_visible():
            return True
    return False


async def _visible_texts(locator: Any) -> list[str]:
    texts = []
    for index in range(await locator.count()):
        item = locator.nth(index)
        if await item.is_visible():
            texts.append((await item.inner_text()).strip().lower())
    return texts


def _is_login_url(url: str) -> bool:
    return bool(re.search(r"tiktok\.com/(?:[^?#]+/)?login(?:[/?#]|$)", url.lower()))


def _cancelled(event: Any) -> bool:
    return event is not None and bool(event.is_set())


def _raise_if_cancelled(event: Any) -> None:
    if _cancelled(event):
        raise _Cancelled()


async def _cancel_aware_sleep(event: Any, seconds: float) -> None:
    if seconds <= 0:
        _raise_if_cancelled(event)
        await asyncio.sleep(0)
        return
    if isinstance(event, asyncio.Event):
        try:
            await asyncio.wait_for(event.wait(), timeout=seconds)
            raise _Cancelled()
        except asyncio.TimeoutError:
            return
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _raise_if_cancelled(event)
        await asyncio.sleep(min(0.05, end - time.monotonic()))


def _rejection_message(state: Mapping[str, Any]) -> str:
    payload = state.get("payload")
    if isinstance(payload, dict):
        detail = payload.get("status_msg") or payload.get("message")
        if detail:
            return f"TikTok rejected the post: {detail}"
    return f"TikTok rejected the post (HTTP {state.get('http_status', 'unknown')}, status_code={state.get('status_code')})"


def classify_rejection(state: Mapping[str, Any]) -> str:
    """Classify a confirmed TikTok rejection into an account-level or per-video scope.

    Reads only the already-captured, non-sensitive response_state (payload text, HTTP
    status). An explicit account posting block (e.g. ``temporarily prevented from
    posting``) maps to REJECTION_SCOPE_ACCOUNT_BLOCKED. A rejection message that is not
    account-level maps to REJECTION_SCOPE_VIDEO; a rejection with no message at all maps
    to REJECTION_SCOPE_UNKNOWN. Generic rejections are never upgraded to account blocks.
    """
    payload = state.get("payload")
    text = ""
    if isinstance(payload, dict):
        text = str(payload.get("status_msg") or payload.get("message") or "")
    text = text.strip().lower()
    if any(pattern.search(text) for pattern in ACCOUNT_POSTING_BLOCK_PATTERNS):
        return REJECTION_SCOPE_ACCOUNT_BLOCKED
    if text:
        return REJECTION_SCOPE_VIDEO
    return REJECTION_SCOPE_UNKNOWN


async def _collect_upload_metadata(page: Any) -> dict[str, Any]:
    """Non-sensitive DOM facts captured at a failure for diagnostics.

    Never includes cookies, tokens, request bodies or auth headers."""
    meta: dict[str, Any] = {
        "url": str(getattr(page, "url", "")),
        "post_button_visible": False,
        "post_button_enabled": False,
        "joyride_tooltip_visible": False,
        "joyride_overlay_visible": False,
        "joyride_labels": [],
        "visible_dialogs": [],
    }
    try:
        button_locator = page.locator(SELECTORS["post_button"])
        for index in range(await button_locator.count()):
            button = button_locator.nth(index)
            if await button.is_visible():
                meta["post_button_visible"] = True
                try:
                    meta["post_button_enabled"] = bool(await button.is_enabled())
                except Exception:
                    pass
                break
    except Exception:
        pass
    try:
        meta["joyride_tooltip_visible"] = await _has_visible(page.locator(SELECTORS["joyride_root"]))
    except Exception:
        pass
    try:
        meta["joyride_overlay_visible"] = await _has_visible(page.locator(SELECTORS["joyride_overlay"]))
    except Exception:
        pass
    try:
        tooltip = page.locator(SELECTORS["joyride_root"])
        for index in range(await tooltip.count()):
            if await tooltip.nth(index).is_visible():
                text = (await tooltip.nth(index).inner_text()) or ""
                meta["joyride_labels"].append(" ".join(text.split())[:300])
    except Exception:
        pass
    try:
        dialogs = page.locator(SELECTORS["visible_dialog"])
        for index in range(await dialogs.count()):
            dialog = dialogs.nth(index)
            if not await dialog.is_visible():
                continue
            snapshot = await dialog.evaluate(
                """el => ({
                    text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 1200),
                    role: el.getAttribute('role') || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    className: typeof el.className === 'string' ? el.className.slice(0, 300) : '',
                    dataE2e: el.getAttribute('data-e2e') || '',
                    buttons: Array.from(el.querySelectorAll('button, [role="button"]')).slice(0, 30).map(btn => ({
                        text: (btn.innerText || btn.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
                        ariaLabel: btn.getAttribute('aria-label') || '',
                        dataE2e: btn.getAttribute('data-e2e') || '',
                        disabled: !!btn.disabled || btn.getAttribute('aria-disabled') === 'true'
                    }))
                })"""
            )
            if isinstance(snapshot, dict):
                meta["visible_dialogs"].append(snapshot)
    except Exception:
        pass
    return meta


async def _capture_diagnostics(
    page: Any,
    video_name: str,
    directory: str | Path | None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> tuple[str, ...]:
    if directory is None:
        return ()
    try:
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(video_name).name)[:80] or "video"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = output / f"{stamp}_{safe_name}"
        html_path = base.with_suffix(".html")
        png_path = base.with_suffix(".png")
        meta_path = base.with_suffix(".json")
        html_path.write_text(await page.content(), encoding="utf-8")
        meta_payload = {
            "url": str(getattr(page, "url", "")),
            "video": video_name,
        }
        if metadata:
            meta_payload.update(dict(metadata))
        meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
        await page.screenshot(path=str(png_path), full_page=True)
        return (str(html_path), str(png_path), str(meta_path))
    except Exception:
        return ()


__all__ = ["OUTCOMES", "SELECTORS", "UPLOAD_URL", "UploadResult", "UploadTimeouts", "upload_tiktok"]
