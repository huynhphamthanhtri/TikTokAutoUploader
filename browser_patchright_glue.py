"""Sync facade tying main.py to the Patchright runtime.

The app keeps storing a token in the legacy ``driver`` slots so the queue,
lifecycle and UI logic keep working unchanged. All browser work runs on the
runtime thread; callers wait synchronously on the returned futures.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

from browser_maintenance import create_owned_root
from core_helpers import _extract_ip_address, parse_proxy_string
from patchright_browser import (
    BrowserServiceClosedError,
    BrowserSessionConfig,
    CookieImportReport,
    PatchrightBrowser,
    ProfileInUseError,
    SessionMode,
    StaleSessionError,
)
from patchright_cookie_codec import (
    TIKTOK_AUTH_COOKIES,
    has_primary_tiktok_auth_cookie,
    patchright_cookies_to_selenium,
    selenium_cookies_to_patchright,
)
from patchright_profile_migration import create_patchright_profile, migration_status

try:
    from patchright_upload import SELECTORS as _UPLOAD_SELECTORS
    from patchright_upload import upload_tiktok
except ImportError:  # pragma: no cover - only needed at upload time
    _UPLOAD_SELECTORS = None
    upload_tiktok = None


class SessionSetupError(RuntimeError):
    """Browser session could not be opened or configured."""


class ProfileBusyError(SessionSetupError):
    """Profile directory is still owned by another live session."""


class ProxyVerificationError(RuntimeError):
    """Browser exit IP could not be verified."""

    def __init__(self, reason, current_ip=None, expected_ip=None):
        super().__init__(reason)
        self.reason = reason
        self.current_ip = current_ip
        self.expected_ip = expected_ip


class LoginRequiredError(RuntimeError):
    """TikTok redirected the freshly opened session to its login page."""


UPLOAD_TOTAL_TIMEOUT = 15 * 60  # page_ready + editor + confirmation margin
SESSION_OPEN_TIMEOUT = 90
OP_DEFAULT_TIMEOUT = 60

PROXY_IP_SERVICES = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
)

_service = None
_service_lock = threading.Lock()


class SessionToken:
    """Opaque session handle stored in the legacy driver slots."""

    __slots__ = (
        "profile_name",
        "handle",
        "mode",
        "profile_path",
        "_closed",
        "_close_lock",
        "generation",
        "cancellation_event",
    )

    def __init__(self, profile_name, handle, mode, profile_path, generation=0):
        self.profile_name = profile_name
        self.handle = handle
        self.mode = mode
        self.profile_path = profile_path
        self.generation = generation
        self._closed = False
        self._close_lock = threading.Lock()
        self.cancellation_event = threading.Event()

    def is_alive(self):
        return not self._closed

    def mark_closed(self):
        self._closed = True

    def set_cancelled(self):
        self.cancellation_event.set()

    def quit(self, timeout=10.0):
        """Lifecycle-compatible close: close the session and mark the token dead
        only once the registry confirms the session was released. Idempotent and
        thread-safe: concurrent callers share one close, and a close that timed
        out is not reported as a successful release."""
        with self._close_lock:
            if self._closed:
                return True
            closed = close_session(self.handle, timeout=timeout)
            if closed:
                self.mark_closed()
            return closed


def browser_service():
    global _service
    with _service_lock:
        if _service is None or _service.status().closed:
            _service = PatchrightBrowser()
        return _service


def shutdown_browser_service(timeout=10.0):
    global _service
    with _service_lock:
        service = _service
        _service = None
    if service is not None:
        service.shutdown(timeout=timeout)


def _resolve_profile(config):
    legacy = str(config.get("chrome_profile", "")).strip()
    if not legacy:
        raise SessionSetupError("chrome_profile chưa được cấu hình cho profile này")
    return Path(legacy)


def ensure_patchright_profile(config):
    """Create (or resume) the owned Profile-Patchright sibling of the legacy profile."""
    legacy_dir = _resolve_profile(config)
    if legacy_dir.name.casefold() != "profile":
        raise ValueError("Legacy profile directory must be named Profile")
    target = legacy_dir.with_name("Profile-Patchright")
    if target.exists():
        status = migration_status(target)
        if Path(status["legacy_profile"]).resolve() != legacy_dir.resolve():
            raise ValueError("Owned profile belongs to a different legacy profile")
    else:
        if not legacy_dir.is_dir():
            create_owned_root(legacy_dir)
        target = create_patchright_profile(str(legacy_dir), str(legacy_dir.parent))
    profile_path = str(target)
    config["browser_profile_path"] = profile_path
    return profile_path


def active_profile_path(config):
    """The data dir currently in use: Patchright sibling when available, else legacy."""
    if config.get("browser_profile_path"):
        return str(config["browser_profile_path"])
    return str(config.get("chrome_profile", ""))


def build_session_config(config, mode=SessionMode.AUTOMATION, headed=None):
    """Map app config to a patchright native session configuration."""
    profile_path = config.get("browser_profile_path")
    if not profile_path:
        raise SessionSetupError("browser_profile_path chưa được khởi tạo")
    if headed is None:
        headed = not config.get("headless", True)

    kwargs = {"profile_path": profile_path, "mode": mode, "headed": headed}

    proxy = None
    if config.get("use_proxy", False):
        proxy_data = parse_proxy_string(config.get("proxy_string", ""))
        if not proxy_data:
            raise SessionSetupError("Proxy sai định dạng; từ chối mở browser")
        host = str(proxy_data.get("ip", "")).strip()
        port = str(proxy_data.get("port", "")).strip()
        if not host or any(character.isspace() for character in host) or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise SessionSetupError("Proxy sai định dạng; từ chối mở browser")
        native = {
            "server": "http://{}:{}".format(host, port)
        }
        if proxy_data.get("user"):
            native["username"] = proxy_data["user"]
        if proxy_data.get("pass"):
            native["password"] = proxy_data["pass"]
        proxy = native
    if proxy is not None:
        kwargs["proxy"] = proxy

    fingerprint = config.get("fingerprint", {}) or {}
    timezone_id = fingerprint.get("timezone")
    if isinstance(timezone_id, str) and "/" in timezone_id and "utc" not in timezone_id.lower():
        kwargs["timezone_id"] = timezone_id
    lang = fingerprint.get("lang")
    if isinstance(lang, str) and len(lang) <= 64:
        kwargs["locale"] = lang
    geolocation = fingerprint.get("geolocation")
    if isinstance(geolocation, dict):
        try:
            latitude = float(geolocation["latitude"])
            longitude = float(geolocation["longitude"])
            accuracy = float(geolocation.get("accuracy", 50))
        except (KeyError, TypeError, ValueError):
            pass
        else:
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                kwargs["geolocation"] = {
                    "latitude": latitude,
                    "longitude": longitude,
                    "accuracy": accuracy,
                }
                kwargs["permissions"] = ("geolocation",)

    kwargs["args"] = ("--no-first-run", "--log-level=3")
    return BrowserSessionConfig(**kwargs)


def _canonical_profile(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _validate_session_handle(handle, session_config):
    """Verify a freshly opened SessionHandle before publishing a token.

    A malformed handle would make every later operation fail and leak the
    session in the registry, so close the raw handle immediately when the
    result is structurally invalid."""
    errors = []
    session_id = getattr(handle, "session_id", None)
    if not isinstance(session_id, str) or not session_id:
        errors.append("thiếu session_id hợp lệ")
    generation = getattr(handle, "generation", None)
    if not isinstance(generation, int):
        errors.append("thiếu generation hợp lệ")
    if getattr(handle, "mode", None) != SessionMode.AUTOMATION:
        errors.append("mode không đúng AUTOMATION")
    if _canonical_profile(getattr(handle, "profile_path", "")) != _canonical_profile(
        session_config.profile_path
    ):
        errors.append("profile_path không khớp với session")
    if errors:
        try:
            close_session(handle, timeout=10)
        except Exception:
            pass
        raise SessionSetupError(
            "Session trả về không hợp lệ: {}".format("; ".join(errors))
        )


def open_session(config, profile_name, timeout=SESSION_OPEN_TIMEOUT):
    service = browser_service()
    session_config = build_session_config(config, mode=SessionMode.AUTOMATION)
    try:
        future = service.open_session(session_config)
        result = future.result(timeout=timeout)
    except ProfileInUseError as error:
        raise ProfileBusyError(str(error)) from error
    except BrowserServiceClosedError as error:
        raise SessionSetupError("Browser service đã đóng") from error
    except FutureTimeoutError:
        future.cancel()
        raise SessionSetupError(
            "Mở browser quá {} giây; tài nguyên máy có thể đang quá tải".format(timeout)
        ) from None
    _validate_session_handle(result.handle, session_config)
    return SessionToken(
        profile_name=profile_name,
        handle=result.handle,
        mode=SessionMode.AUTOMATION,
        profile_path=session_config.profile_path,
    )


def import_cookies_report(token, selenium_cookies, timeout=OP_DEFAULT_TIMEOUT):
    """Import cookies and return the acceptance report without raising.

    Only the cookies the context actually kept are counted; TikTok session
    cookies are verified against ``www.tiktok.com`` coverage."""
    converted = selenium_cookies_to_patchright(selenium_cookies or [])
    if not converted:
        return CookieImportReport(
            requested=0,
            accepted=0,
            auth_requested=0,
            auth_accepted=0,
            missing_auth_names=(),
        )
    service = browser_service()
    future = service.import_cookies(token.handle, converted)
    result = future.result(timeout=timeout)
    return result.value


def import_cookies(token, selenium_cookies, timeout=OP_DEFAULT_TIMEOUT):
    """Import cookies; raises when TikTok session cookies are not accepted.

    Returns the number of cookies actually kept by the context, not the number
    of cookies that were merely sent to ``add_cookies``."""
    report = import_cookies_report(token, selenium_cookies, timeout=timeout)
    if report.requested > 0 and report.accepted == 0:
        raise SessionSetupError(
            "Không cookie nào được Chromium chấp nhận; cookie có thể sai domain hoặc hết hạn"
        )
    if report.auth_requested > 0 and report.auth_accepted == 0:
        raise SessionSetupError(
            "Cookie đăng nhập TikTok không được chấp nhận (thiếu: {})".format(
                ", ".join(report.missing_auth_names) or "không rõ"
            )
        )
    return report.accepted


def _run(handle, operation, timeout=OP_DEFAULT_TIMEOUT, convert_timeout=True):
    service = browser_service()
    future = service.run(handle, operation)
    try:
        result = future.result(timeout=timeout)
    except FutureTimeoutError:
        if not convert_timeout:
            raise
        try:
            service.cancel_session(handle).result(timeout=10)
        except Exception:
            pass
        raise SessionSetupError("Thao tác browser vượt quá {} giây".format(timeout)) from None
    return result.value


def run_operation(token, operation, timeout=OP_DEFAULT_TIMEOUT):
    try:
        return _run(token.handle, operation, timeout=timeout)
    except StaleSessionError:
        token.mark_closed()
        raise SessionSetupError("Phiên browser đã bị đóng") from None


def navigate(token, url, timeout=OP_DEFAULT_TIMEOUT):
    async def _goto(page):
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        return page.url

    return run_operation(token, _goto, timeout=timeout)


def _is_login_url(url):
    return bool(
        re.search(r"tiktok\.com/(?:[^?#]+/)?login(?:[/?#]|$)", str(url or "").lower())
    )


async def _any_visible(page, selector):
    locator = page.locator(selector)
    for index in range(await locator.count()):
        if await locator.nth(index).is_visible():
            return True
    return False


def page_login_state(token, timeout=OP_DEFAULT_TIMEOUT):
    """Inspect the live page for TikTok's login form.

    Returns ``authenticated``, ``login_required`` or ``indeterminate``.
    ``indeterminate`` is fail-closed: callers must not publish the driver as
    ready, because neither a login form nor an authenticated Studio shell was
    visible."""
    login_selectors = _UPLOAD_SELECTORS["login"]
    shell_selectors = (
        _UPLOAD_SELECTORS["file_input"],
        _UPLOAD_SELECTORS["upload_surface"],
    )

    async def _inspect(page):
        current_url = str(getattr(page, "url", "") or "").lower()
        if _is_login_url(current_url):
            return "login_required"
        if await _any_visible(page, login_selectors):
            return "login_required"
        for shell_selector in shell_selectors:
            if await _any_visible(page, shell_selector):
                return "authenticated"
        return "indeterminate"

    return run_operation(token, _inspect, timeout=timeout)


def wait_page_login_state(token, timeout=30.0, poll=1.0, run_timeout=OP_DEFAULT_TIMEOUT):
    """Poll until TikTok login state is definitive.

    Returns ``authenticated`` or ``login_required`` once either is observed,
    or ``indeterminate`` if no decision was reached within ``timeout``."""
    deadline = time.monotonic() + timeout
    last = "indeterminate"
    while True:
        last = page_login_state(token, timeout=run_timeout)
        if last != "indeterminate":
            return last
        if time.monotonic() >= deadline:
            return last
        time.sleep(poll)


def page_url(token, timeout=OP_DEFAULT_TIMEOUT):
    async def _url(page):
        return page.url

    return run_operation(token, _url, timeout=timeout)


def page_evaluate(token, script, timeout=OP_DEFAULT_TIMEOUT):
    async def _evaluate(page):
        return await page.evaluate(script)

    return run_operation(token, _evaluate, timeout=timeout)


def verify_exit_ip(token, expected_ip, timeout_per_service=6, diagnostics=None):
    """Fetch an IP reporter inside the browser; returns (is_match, ip, details)."""
    expected = _extract_ip_address(expected_ip) or str(expected_ip).strip()
    details = diagnostics if diagnostics is not None else []
    async def _verify(page):
        loop = asyncio.get_event_loop()
        for url in PROXY_IP_SERVICES:
            started_at = time.perf_counter()
            last_error = None
            last_body = ""
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
            except Exception as error:
                last_error = error
            deadline = loop.time() + timeout_per_service
            while loop.time() < deadline:
                try:
                    last_body = await page.evaluate(
                        "document.body ? document.body.innerText : ''"
                    )
                    current_ip = _extract_ip_address(str(last_body))
                    if current_ip:
                        return current_ip == expected, current_ip
                except Exception as error:
                    last_error = error
                await asyncio.sleep(0.2)
            details.append({
                "url": url,
                "elapsed": round(time.perf_counter() - started_at, 2),
                "body": (last_body or "")[:120],
                "error": None if last_error is None else type(last_error).__name__,
            })
        return False, None
    try:
        total_timeout = len(PROXY_IP_SERVICES) * (10 + timeout_per_service) + 5
        return run_operation(token, _verify, timeout=total_timeout)
    except Exception:
        return False, None


def export_cookies(token, timeout=OP_DEFAULT_TIMEOUT):
    """Return selenium-style cookie dicts captured from the live session."""
    service = browser_service()
    future = service.export_cookies(token.handle)
    export = future.result(timeout=timeout)
    cookies = json.loads(export.cookies_json)
    return patchright_cookies_to_selenium(cookies)


def cancel_session(handle, timeout=10):
    service = browser_service()
    try:
        future = service.cancel_session(handle)
        future.result(timeout=timeout)
    except (BrowserServiceClosedError, StaleSessionError, FutureTimeoutError):
        pass


def close_session(handle, timeout=10):
    """Close a session. Returns True when the session is confirmed released
    (closed, already stale, or the service shut down) and False when the close
    could not be confirmed within the timeout."""
    service = browser_service()
    try:
        future = service.close_session(handle)
        future.result(timeout=timeout)
    except (BrowserServiceClosedError, StaleSessionError):
        return True
    except FutureTimeoutError:
        return False
    except Exception:
        return False
    return True


def run_upload(token, video_path, status_callback=None, timeout=UPLOAD_TOTAL_TIMEOUT, stop_before_post=False):
    """Run the standalone uploader to completion on the runtime thread."""
    if upload_tiktok is None:  # pragma: no cover
        raise SessionSetupError("patchright_upload không khả dụng")
    log = status_callback or (lambda message: None)

    def _log_status(message):
        try:
            log(str(message))
        except Exception:
            pass

    async def _upload(page):
        return await upload_tiktok(
            page,
            video_path,
            status_callback=_log_status,
            cancellation_event=token.cancellation_event,
            stop_before_post=stop_before_post,
        )

    try:
        value = _run(token.handle, _upload, timeout=timeout, convert_timeout=False)
    except StaleSessionError:
        token.mark_closed()
        raise SessionSetupError("Phiên browser bị mất khi đang đăng") from None
    except FutureTimeoutError:
        cancel_session(token.handle, timeout=10)
        token.quit()
        raise SessionSetupError("Đăng video vượt quá {} giây".format(timeout)) from None
    return value


def watch_manual_close(token, poll=0.5):
    """Block until the manual browser is closed by the user (daemon callers only)."""
    async def _watch(page):
        while True:
            try:
                if not page.context.pages:
                    break
            except Exception:
                break
            await asyncio.sleep(poll)
        return True

    try:
        _run(token.handle, _watch, timeout=None)
    except (StaleSessionError, asyncio.CancelledError):
        pass
    except Exception:
        pass
    finally:
        try:
            token.quit()
        except Exception:
            pass
