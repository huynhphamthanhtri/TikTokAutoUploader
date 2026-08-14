"""Automated login environment runner for a single account profile.

Creates clean per-proxy browser profiles, synchronizes the browser environment
(proxy, timezone, geolocation, locale), attempts exactly one login per
candidate, classifies the outcome, verifies session persistence across
close/reopen, then promotes the winning profile into the official owned
``Profile-Patchright`` sibling and updates the account config. When no
candidate passes, the account config and the existing profile are left
untouched.

Security: the report, log output and transaction records never contain
credentials. Proxies are referenced by their ``ip:port`` label only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import browser_patchright_glue as glue
import proxy_diagnostics as diag
from browser_environment import proxy_cache_key
from config_store import save_configs_file
from core_helpers import parse_proxy_string
from patchright_browser import SessionMode
from patchright_profile_migration import (
    create_patchright_profile,
    mark_profile_login_verified,
)
from profile_ownership import ensure_account_uuid

# ---------------------------------------------------------------------------
# Outcome taxonomy
# ---------------------------------------------------------------------------
PREFLIGHT_FAILED = "preflight_failed"
INTERNAL_SERVER_ERROR = "internal_server_error"
CREDENTIAL_REJECTED = "credential_rejected"
CAPTCHA = "captcha"
CHECKPOINT = "checkpoint"
AUTHENTICATED = "authenticated"
SESSION_NOT_PERSISTENT = "session_not_persistent"
TIMEOUT = "timeout"
ERROR = "error"

# Outcomes that must stop the whole run so the account is not risked further.
STOP_ON_FIRST = frozenset({CAPTCHA, CHECKPOINT, CREDENTIAL_REJECTED})

# Outcomes that let the runner continue with the next proxy candidate.
RETRYABLE = frozenset({PREFLIGHT_FAILED, INTERNAL_SERVER_ERROR, TIMEOUT, ERROR})

LOGIN_ATTEMPT_TIMEOUT = int(os.environ.get("LOGIN_TEST_ATTEMPT_TIMEOUT", "60"))
STUDIO_VERIFY_TIMEOUT = int(os.environ.get("LOGIN_TEST_STUDIO_TIMEOUT", "30"))
PERSISTENCE_VERIFY_TIMEOUT = 45
PROBE_TIMEOUT = 10
LOGIN_PAGE_URL = "https://www.tiktok.com/login/phone-or-email/email"
STUDIO_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center"

# Config fields that the runner may touch and therefore snapshots/restores.
RESTORED_CONFIG_FIELDS = (
    "proxy_string",
    "proxy_type",
    "use_proxy",
    "fingerprint",
    "browser_profile_path",
    "migration_state",
    "cookie_str",
    "session_auth_state",
    "session_source",
    "session_verified_at",
    "session_verified_profile_path",
    "session_verified_proxy_key",
    "session_last_failure_at",
    "session_last_failure_reason",
    "manual_login_pending",
    "profile_owner_state",
    "profile_created_at",
    "profile_isolation_state",
    "cookies_last_injected_at",
    "cookies_last_injected_profile_path",
)

_COUNTRY_LOCALE = {
    "JP": "ja-JP",
    "US": "en-US",
    "CA": "en-CA",
    "SG": "en-SG",
    "GB": "en-GB",
    "AU": "en-AU",
    "KR": "ko-KR",
    "TW": "zh-TW",
    "HK": "zh-HK",
    "TH": "th-TH",
    "VN": "vi-VN",
    "ID": "id-ID",
    "MY": "en-MY",
    "PH": "en-PH",
    "DE": "de-DE",
    "FR": "fr-FR",
    "ES": "es-ES",
    "MX": "es-MX",
    "BR": "pt-BR",
    "IN": "en-IN",
}

_LOGIN_URL_RE = re.compile(r"tiktok\.com/(?:[^?#]+/)?login(?:[/?#]|$)", re.IGNORECASE)

# After submitting credentials a failed login sometimes redirects back to the
# generic login landing page (QR code / social sign-in options). That landing is
# itself a definitive "not authenticated" signal.
_LOGIN_HUB_URL_RE = re.compile(r"/login\?(?:[^#]*redirect_url|[^#]*enter_method)", re.IGNORECASE)
_LOGIN_HUB_TEXT_RE = re.compile(
    r"qr code|use phone number|continue with google|continue with facebook|"
    r"qrコード|電話番号/メール|googleで続ける|facebookで続ける|"
    r"qr코드|qr コード",
    re.IGNORECASE,
)

_INTERNAL_ERROR_RE = re.compile(
    r"internal server error|something went wrong|đã xảy ra lỗi|please try again later",
    re.IGNORECASE,
)
_CAPTCHA_RE = re.compile(
    r"captcha|prove you are human|verify you are human|xác minh bạn không phải",
    re.IGNORECASE,
)
_CHECKPOINT_RE = re.compile(
    r"checkpoint|verify your device|device verification|security verification|"
    r"suspicious activity|verify to continue|xác minh thiết bị|đăng nhập thiết bị mới",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(
    r"incorrect (password|username|email)|wrong password|invalid (username|password|email)|"
    r"password is incorrect|username or password|email address not found|"
    r"account.*not found|does not exist|sai mật khẩu|sai tên đăng nhập|không tồn tại",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def locale_for_country(country_code):
    """Map a GeoIP country code to a browser locale; fallback ``en-US``."""
    code = str(country_code or "").upper().strip()
    return _COUNTRY_LOCALE.get(code, "en-US")


def build_fingerprint_for_probe(probe, proxy_string):
    """Build the environment fingerprint for a candidate proxy probe.

    ``probe`` is a ``proxy_diagnostics.probe_proxy`` result. The locale is
    derived from the country so it always matches the exit location.
    """
    geo = probe.get("geo", {}) or {}
    country_code = str(geo.get("country_code", "") or "").strip()
    timezone_id = str(geo.get("timezone", "") or "").strip() or "UTC"
    latitude = geo.get("latitude", "")
    longitude = geo.get("longitude", "")
    fingerprint = {
        "lang": locale_for_country(country_code),
        "timezone": timezone_id,
        "device_preset": "desktop",
        "geo_exit_ip": str(probe.get("exit_ip", "") or "").strip(),
        "geo_proxy_hash": proxy_cache_key(parse_proxy_string(proxy_string)),
        "geo_resolved_at": datetime.now(timezone.utc).isoformat(),
        "geo_source": "ipwho.is",
    }
    try:
        fingerprint["geolocation"] = {
            "latitude": float(latitude),
            "longitude": float(longitude),
            "accuracy": 50,
        }
    except (TypeError, ValueError):
        pass
    return fingerprint


def plan_candidates(probes):
    """Order healthy probes by latency; drop failed or unstable ones.

    ``probes`` are ``probe_proxy`` results enriched with a ``proxy`` key.
    Returns only candidates that authenticated and reported a stable exit IP.
    """
    candidates = []
    for probe in probes or []:
        if not probe.get("ok"):
            continue
        label = str(probe.get("label", "") or "")
        exit_ip = str(probe.get("exit_ip", "") or "").strip()
        if not label or not exit_ip:
            continue
        candidates.append(probe)
    candidates.sort(key=lambda probe: probe.get("latency_ms") if isinstance(probe.get("latency_ms"), int) else 10 ** 9)
    return candidates


def scan_login_signals(text, url):
    """Return a signal dict from the live login page text and URL."""
    body = str(text or "")
    signals = {
        "has_captcha": bool(_CAPTCHA_RE.search(body)),
        "has_checkpoint": bool(_CHECKPOINT_RE.search(body)),
        "has_internal_error": bool(_INTERNAL_ERROR_RE.search(body)),
        "has_credential_error": bool(_CREDENTIAL_RE.search(body)),
        "has_login_hub": bool(_LOGIN_HUB_URL_RE.search(str(url or "")))
        and bool(_LOGIN_HUB_TEXT_RE.search(body)),
    }
    return signals


def classify_login_signals(signals):
    """Map scraped signals to an outcome taxonomy value."""
    signals = signals or {}
    if signals.get("has_captcha"):
        return CAPTCHA
    if signals.get("has_checkpoint"):
        return CHECKPOINT
    if signals.get("has_credential_error"):
        return CREDENTIAL_REJECTED
    if signals.get("has_internal_error"):
        return INTERNAL_SERVER_ERROR
    if signals.get("has_login_hub"):
        return INTERNAL_SERVER_ERROR
    return None


def snapshot_config(config):
    """Copy every field the runner may touch into a detached snapshot."""
    return {key: config.get(key) for key in RESTORED_CONFIG_FIELDS}


def restore_config(config, snapshot):
    """Restore snapshot fields back into the live config dict."""
    for key in RESTORED_CONFIG_FIELDS:
        if key in snapshot:
            config[key] = snapshot[key]


def redact_report(report):
    """Return a credential-free copy of a run report (no proxy secrets)."""
    redacted = json.loads(json.dumps(report))
    for candidate in redacted.get("candidates", []):
        candidate.pop("proxy", None)
        candidate.pop("proxy_string", None)
    return redacted


def build_report(profile_name, overall, stop_reason, candidates, promoted, applied_proxy_label=None, error=""):
    return {
        "profile_name": profile_name,
        "overall": overall,
        "stop_reason": stop_reason,
        "promoted": bool(promoted),
        "applied_proxy_label": applied_proxy_label or "",
        "error": error,
        "candidates": [
            {
                "index": c.get("index"),
                "label": c.get("label", ""),
                "exit_ip": c.get("exit_ip", ""),
                "timezone": (c.get("geo", {}) or {}).get("timezone", ""),
                "country_code": (c.get("geo", {}) or {}).get("country_code", ""),
                "latency_ms": c.get("latency_ms"),
                "outcome": c.get("outcome", ""),
                "error": c.get("error", ""),
                "profile_path": c.get("profile_path", ""),
                "promoted": bool(c.get("promoted")),
            }
            for c in candidates
        ],
    }


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _config_file_path():
    return Path(__file__).resolve().parent / "configs.json"


def _transaction_path(managed_root, run_id):
    return Path(managed_root) / "LoginTests" / ("transaction-%s.json" % run_id)


# ---------------------------------------------------------------------------
# Browser interaction helpers (async, run inside page operations)
# ---------------------------------------------------------------------------
async def _first_visible(page, selectors):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(count):
            item = locator.nth(index)
            try:
                if await item.is_visible():
                    return item
            except Exception:
                continue
    return None


async def _fill_login_form(page, email, password):
    email_selectors = (
        'input[name="email"]',
        'input[type="email"]',
        'input[data-e2e="login-email"]',
        'input[placeholder*="Email"]',
        'input[placeholder*="email"]',
        'input[type="text"]',
    )
    password_selectors = (
        'input[name="password"]',
        'input[type="password"]',
        'input[data-e2e="login-password"]',
        'input[placeholder*="Password"]',
        'input[placeholder*="Mật khẩu"]',
    )
    email_field = await _first_visible(page, email_selectors)
    password_field = await _first_visible(page, password_selectors)
    if email_field is None or password_field is None:
        return False
    try:
        await email_field.fill(email)
        await password_field.fill(password)
    except Exception:
        return False
    return True


async def _click_login_button(page):
    button_selectors = (
        'button[data-e2e="login-button"]',
        'button[type="submit"]',
        'input[type="submit"]',
        'button[data-e2e="login-form-button"]',
    )
    button = await _first_visible(page, button_selectors)
    if button is not None:
        try:
            await button.click()
            return True
        except Exception:
            pass
    try:
        candidates = page.locator("button")
        count = await candidates.count()
        for index in range(count):
            item = candidates.nth(index)
            try:
                if not await item.is_visible():
                    continue
                text = (await item.inner_text()).strip().lower()
            except Exception:
                continue
            if any(token in text for token in (
                "log in", "đăng nhập", "ログイン", "로그인", "登录", "登入",
            )):
                await item.click()
                return True
    except Exception:
        pass
    return False


async def _await_login_result(page, timeout=LOGIN_ATTEMPT_TIMEOUT, responses=None):
    """Poll until a definitive login outcome is seen.

    Signals accumulate across samples (union) so transient error text is not
    lost. The loop only short-circuits on an explicit 4xx/5xx login response,
    a visible stop signal, or a confirmed navigation away from the login page.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    signals = {
        "has_captcha": False,
        "has_checkpoint": False,
        "has_internal_error": False,
        "has_credential_error": False,
    }
    navigated_away = False
    left_since = None
    error_response = False
    while loop.time() < deadline:
        url = ""
        body = ""
        try:
            url = str(getattr(page, "url", "") or "")
            body = await page.evaluate("document.body ? document.body.innerText : ''")
        except Exception:
            pass
        sample = scan_login_signals(body, url)
        for key in signals:
            signals[key] = signals[key] or sample[key]
        if responses and any(r.get("status", 0) >= 400 for r in responses):
            error_response = True
            break
        on_login = bool(_LOGIN_URL_RE.search(url.lower()))
        if on_login:
            left_since = None
        else:
            if left_since is None:
                left_since = loop.time()
            elif loop.time() - left_since >= 3.0:
                navigated_away = True
                break
        if any(signals.values()):
            break
        await asyncio.sleep(0.3)
    signals["navigated_away"] = navigated_away
    signals["error_response"] = error_response
    return signals


def _perform_login(token, email, password, timeout=LOGIN_ATTEMPT_TIMEOUT):
    """Fill the login form, submit once, and scrape the resulting signals."""
    async def _run(page):
        responses = []
        all_responses = []
        requests = []
        posts = []

        def _on_response(response):
            url = str(response.url or "")
            all_responses.append({"url": url[:160], "status": int(response.status)})
            if any(key in url for key in ("/passport/", "/login/", "/api/v1/", "verify")):
                responses.append({"url": url[:160], "status": int(response.status)})

        def _on_request(request):
            method = str(request.method or "")
            url = str(request.url or "")
            if method.upper() == "POST":
                posts.append({"url": url[:160]})

        def _on_request_failed(request):
            url = str(request.url or "")
            if any(key in url for key in ("/passport/", "/login/", "/api/v1/")):
                requests.append({"url": url[:160], "error": str(request.failure) or ""})

        page.on("response", _on_response)
        page.on("request", _on_request)
        page.on("requestfailed", _on_request_failed)
        try:
            filled = await _fill_login_form(page, email, password)
            if not filled:
                return {"filled": False, "clicked": False, "signals": {}, "responses": responses, "requests": requests}
            responses.clear()
            all_responses.clear()
            submitted = False
            try:
                await page.locator('input[type="password"]').press("Enter")
                submitted = True
            except Exception:
                submitted = False
            if not submitted:
                submitted = await _click_login_button(page)
            if not submitted:
                return {"filled": True, "clicked": False, "signals": {}, "responses": responses, "requests": requests}
            signals = await _await_login_result(page, timeout=timeout, responses=responses)
            return {"filled": True, "clicked": True, "signals": signals, "responses": responses, "requests": requests, "all_responses": all_responses, "posts": posts}
        finally:
            page.remove_listener("response", _on_response)
            page.remove_listener("request", _on_request)
            page.remove_listener("requestfailed", _on_request_failed)

    result = dict(glue.run_operation(token, _run, timeout=timeout + 60))
    result["responses"] = _json_plain(result.get("responses", []))
    result["requests"] = _json_plain(result.get("requests", []))
    result["all_responses"] = _json_plain(result.get("all_responses", []))
    result["posts"] = _json_plain(result.get("posts", []))
    result["url"] = glue.page_url(token, timeout=15)
    return result


def _dump_page_text(token, email=""):
    """Best-effort visible page text, sanitized and truncated."""
    async def _run(page):
        return await page.evaluate("document.body ? document.body.innerText : ''")

    try:
        text = str(glue.run_operation(token, _run, timeout=15) or "")
    except Exception:
        return ""
    return _sanitize_text(text, email=email)[:600]


def _sanitize_text(text, email=""):
    """Mask credential-like values before logging."""
    value = str(text or "")
    if email:
        value = value.replace(email, "<EMAIL>")
    return value


def _json_plain(value):
    """Make frozen/mappingproxy structures JSON-serializable."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=True, default=lambda obj: dict(obj)))
    except TypeError:
        return str(value)


def _capture_screenshot(token, path):
    """Best-effort screenshot of the live page for offline inspection."""
    async def _shot(page):
        await page.screenshot(path=str(path))
    try:
        glue.run_operation(token, _shot, timeout=20)
    except Exception:
        pass


def _dump_login_dom(token):
    """Dump visible form controls (attributes only, never values) for debugging."""
    async def _dump(page):
        info = []
        forms = []
        for frame in page.frames:
            try:
                form_locator = frame.locator("form")
                form_count = await form_locator.count()
                for f in range(min(form_count, 10)):
                    form = form_locator.nth(f)
                    try:
                        meta = await form.evaluate(
                            "el => ({id: el.id || '', action: el.action || '', "
                            "method: (el.method || '').toUpperCase(), inputs: el.querySelectorAll('input,button').length})"
                        )
                    except Exception:
                        continue
                    if meta:
                        forms.append(meta)
            except Exception:
                continue
            try:
                for tag in ("input", "button"):
                    locator = frame.locator(tag)
                    count = await locator.count()
                    for index in range(min(count, 40)):
                        element = locator.nth(index)
                        try:
                            if not await element.is_visible():
                                continue
                            attrs = await element.evaluate(
                                "el => ({tag: el.tagName, type: el.type || '', "
                                "name: el.name || '', placeholder: el.placeholder || '', "
                                "dataE2e: el.getAttribute('data-e2e') || '', "
                                "text: (el.innerText || '').slice(0, 40)})"
                            )
                        except Exception:
                            continue
                        text = attrs.get("text", "")
                        if (
                            attrs.get("type") == "password"
                            or attrs.get("name")
                            or attrs.get("placeholder")
                            or (attrs.get("tag") == "BUTTON" and text)
                        ):
                            info.append({
                                "tag": attrs.get("tag", ""),
                                "type": attrs.get("type", ""),
                                "name": attrs.get("name", ""),
                                "placeholder": attrs.get("placeholder", ""),
                                "data_e2e": attrs.get("dataE2e", ""),
                                "text": text,
                                "frame": str(frame.url)[:80],
                            })
            except Exception:
                continue
        return {"controls": info, "forms": forms}

    try:
        return glue.run_operation(token, _dump, timeout=20)
    except Exception:
        return {"controls": [], "forms": []}


def _studio_login_state(token, timeout=STUDIO_VERIFY_TIMEOUT):
    glue.navigate(token, STUDIO_UPLOAD_URL)
    return glue.wait_page_login_state(token, timeout=timeout)


# ---------------------------------------------------------------------------
# Snapshot / transaction / rollback
# ---------------------------------------------------------------------------
def recover_interrupted_transaction(managed_root, config, profile_name=""):
    """Roll back any unfinished promotion left by a crashed previous run."""
    root = Path(managed_root)
    tests_dir = root / "LoginTests"
    if not tests_dir.is_dir():
        return None
    recovered = []
    for transaction in sorted(tests_dir.glob("transaction-*.json")):
        try:
            record = json.loads(transaction.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("status") == "committed":
            continue
        try:
            rollback_transaction(record)
            snapshot = record.get("config_snapshot")
            if snapshot and config and profile_name:
                restore_config(config, snapshot)
                _restore_stored_config(profile_name, snapshot)
            recovered.append(str(transaction.name))
        except Exception:
            pass
        try:
            transaction.unlink(missing_ok=True)
        except OSError:
            pass
    return recovered


def _restore_stored_config(profile_name, snapshot):
    """Persist a recovered config snapshot back into configs.json."""
    try:
        path = _config_file_path()
        payload = json.loads(path.read_text(encoding="utf-8"))
        profiles = payload.get("profiles")
        if not isinstance(profiles, dict) or profile_name not in profiles:
            return
        entry = profiles[profile_name]
        for key in RESTORED_CONFIG_FIELDS:
            if key in snapshot:
                entry[key] = snapshot[key]
        save_configs_file(path, payload)
    except Exception:
        pass


def rollback_transaction(record):
    """Undo a promotion using its transaction record (no config access)."""
    old = record.get("old_profile_path")
    quarantine = record.get("quarantine_path")
    official = record.get("official_path")
    if quarantine and os.path.exists(quarantine):
        if official and os.path.exists(official):
            shutil.rmtree(official, ignore_errors=True)
        os.replace(quarantine, old)
    elif official and os.path.exists(official) and old and not os.path.exists(old):
        shutil.rmtree(official, ignore_errors=True)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class LoginEnvironmentRunner:
    """Orchestrate the clean-login diagnostic for a single account."""

    def __init__(self, profile_name, config, proxies, status_callback=None, stop_event=None):
        self.profile_name = str(profile_name)
        self.config = config
        self.proxies = [proxy for proxy in (proxies or []) if parse_proxy_string(proxy)]
        self.status = status_callback or (lambda message: None)
        self.stop_event = stop_event or threading.Event()
        self.run_id = uuid.uuid4().hex[:12]
        self.managed_root = Path(str(config.get("chrome_profile", ""))).parent
        self.legacy_profile = Path(str(config.get("chrome_profile", "")))
        self.official_profile = Path(
            str(config.get("browser_profile_path") or "")
            or str(self.legacy_profile.with_name("Profile-Patchright"))
        )
        self.work_dir = None
        self.candidates = []
        self.report = {}

    def _log(self, message):
        try:
            self.status("[LoginEnv] %s" % message)
        except Exception:
            pass

    def run(self):
        started_at = _now_iso()
        self._log("Bắt đầu chạy tự động cho %s với %d proxy" % (self.profile_name, len(self.proxies)))
        overall = "FAIL"
        stop_reason = ""
        applied_proxy_label = None
        error = ""
        ensure_account_uuid(self.config)
        snapshot = snapshot_config(self.config)

        try:
            recovered = recover_interrupted_transaction(self.managed_root, self.config, self.profile_name)
            if recovered:
                self._log("Đã rollback giao dịch promotion còn dở: %s" % ", ".join(recovered))

            if not self.proxies:
                overall = "FAIL"
                stop_reason = "Không có proxy hợp lệ"
                self.report = build_report(self.profile_name, overall, stop_reason, self.candidates, False, error=stop_reason)
                return self.report

            probes = self._preflight_proxies()
            self.candidates = plan_candidates(probes)
            if not self.candidates:
                overall = "FAIL"
                stop_reason = "Không proxy nào preflight OK"
                self._log(stop_reason)
                self.report = build_report(self.profile_name, overall, stop_reason, self.candidates, False, error=stop_reason)
                return self.report

            self.work_dir = Path(self.managed_root) / "LoginTests" / ("run-%s" % started_at.replace(":", "-").replace("+", "-"))
            self.work_dir.mkdir(parents=True, exist_ok=True)

            for index, candidate in enumerate(self.candidates, start=1):
                if self.stop_event.is_set():
                    stop_reason = "Bị huỷ bởi người dùng"
                    self._log(stop_reason)
                    break
                candidate["index"] = index
                self._log("Candidate %d: %s (%s, tz=%s)" % (
                    index,
                    candidate.get("label", ""),
                    candidate.get("exit_ip", ""),
                    (candidate.get("geo", {}) or {}).get("timezone", ""),
                ))
                outcome, candidate_error, verified = self._run_candidate(index, candidate)
                candidate["outcome"] = outcome
                candidate["error"] = candidate_error or ""
                if outcome in STOP_ON_FIRST:
                    stop_reason = "Dừng toàn bộ: " + _describe_outcome(outcome)
                    self._log(stop_reason)
                    break
                if outcome == AUTHENTICATED:
                    promoted = self._promote(candidate)
                    candidate["promoted"] = promoted
                    if promoted:
                        applied_proxy_label = candidate.get("label", "")
                        overall = "PASS"
                        stop_reason = "Đã áp dụng proxy thắng cuộc"
                        self._log(stop_reason)
                        break
                    outcome = SESSION_NOT_PERSISTENT
                    candidate["outcome"] = outcome
                    candidate["error"] = candidate_error or "Session không bền vững sau khi đóng/mở lại"

            if overall != "PASS" and not stop_reason:
                overall = "FAIL"
                stop_reason = "Không proxy nào đạt login thành công"
                self._log(stop_reason)
        except Exception as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
            overall = "FAIL"
            stop_reason = "Lỗi runtime: " + error
            self._log(stop_reason)
            try:
                restore_config(self.config, snapshot)
            except Exception:
                pass
        finally:
            self.report = build_report(
                self.profile_name,
                overall,
                stop_reason,
                self.candidates,
                overall == "PASS",
                applied_proxy_label=applied_proxy_label,
                error=error,
            )
            self.report["started_at"] = started_at
            self.report["finished_at"] = _now_iso()
            self._write_run_files(snapshot)
        return self.report

    def _preflight_proxies(self):
        probes = []
        for proxy in self.proxies:
            if self.stop_event.is_set():
                break
            label = diag.proxy_label(proxy) or "?"
            self._log("Preflight %s..." % label)
            try:
                probe = diag.probe_proxy(proxy, timeout=PROBE_TIMEOUT)
            except Exception as exc:
                probe = {
                    "label": label,
                    "ok": False,
                    "exit_ip": "",
                    "latency_ms": None,
                    "geo": {},
                    "error": "%s: %s" % (type(exc).__name__, exc),
                }
            probe["proxy"] = proxy
            if probe.get("ok"):
                self._log("  OK %s -> %s (latency %sms, tz=%s)" % (
                    label,
                    probe.get("exit_ip", ""),
                    probe.get("latency_ms"),
                    (probe.get("geo", {}) or {}).get("timezone", ""),
                ))
            else:
                self._log("  Lỗi %s: %s" % (label, probe.get("error", "unknown")))
            probes.append(probe)
        return probes

    def _candidate_profile_path(self, index):
        return Path(self.work_dir) / ("candidate-%02d" % index)

    def _candidate_config(self, candidate, profile_path):
        cfg = dict(self.config)
        cfg["browser_profile_path"] = str(profile_path)
        cfg["proxy_string"] = candidate.get("proxy", "")
        cfg["proxy_type"] = "http"
        cfg["use_proxy"] = True
        cfg["fingerprint"] = build_fingerprint_for_probe(candidate, candidate.get("proxy", ""))
        return cfg

    def _open_candidate_session(self, candidate, profile_path, headed):
        cfg = self._candidate_config(candidate, profile_path)
        profile_path.mkdir(parents=True, exist_ok=True)
        service = glue.browser_service()
        session_config = glue.build_session_config(cfg, mode=SessionMode.AUTOMATION, headed=headed)
        future = service.open_session(session_config)
        try:
            result = future.result(timeout=glue.SESSION_OPEN_TIMEOUT)
        except Exception:
            try:
                future.cancel()
            except Exception:
                pass
            raise
        glue._validate_session_handle(result.handle, session_config)
        return glue.SessionToken(
            profile_name=self.profile_name,
            handle=result.handle,
            mode=SessionMode.AUTOMATION,
            profile_path=session_config.profile_path,
        )

    def _run_candidate(self, index, candidate):
        profile_path = self._candidate_profile_path(index)
        candidate["profile_path"] = str(profile_path)
        token = None
        try:
            token = self._open_candidate_session(candidate, profile_path, headed=True)
            match, current_ip = glue.verify_exit_ip(
                token, candidate.get("exit_ip", ""), diagnostics=[]
            )
            if not match:
                self._log("  Exit IP trong browser không khớp proxy (%s)" % current_ip)
                return PREFLIGHT_FAILED, "Exit IP trong browser không khớp proxy", False

            glue.navigate(token, LOGIN_PAGE_URL)
            time.sleep(2)
            email = str(self.config.get("email", "") or "").strip()
            password = str(self.config.get("password", "") or "")
            try:
                pre_dom = _json_plain(_dump_login_dom(token))
                pre_payload = {
                    "dom_controls": pre_dom.get("controls", []),
                    "forms": pre_dom.get("forms", []),
                    "page_text": _dump_page_text(token, email=email),
                }
                self._write_json(Path(profile_path) / "pre_login_dom.json", pre_payload)
                self._log("  Pre-login controls: %d" % len(pre_dom.get("controls", [])))
                self._log("  Pre-login forms: %s" % json.dumps(pre_dom.get("forms", []), ensure_ascii=True)[:400])
            except Exception as exc:
                self._log("  Pre-login dump error: %s" % exc)
            result = _perform_login(token, email, password)
            signals = result.get("signals", {}) or {}
            responses = result.get("responses", []) or []
            self._log("  form filled=%s clicked=%s url=%s" % (
                result.get("filled"),
                result.get("clicked"),
                result.get("url", ""),
            ))
            self._log("  login responses: %s" % json.dumps(responses, ensure_ascii=True)[:600])
            all_responses = result.get("all_responses", []) or []
            self._log("  all responses after click: %s" % json.dumps(all_responses[-12:], ensure_ascii=True)[:900])
            posts = result.get("posts", []) or []
            self._log("  POST requests after click: %s" % json.dumps(posts[-10:], ensure_ascii=True)[:700])
            if not result.get("filled"):
                self._log("  Không tìm thấy form login")
                self._capture_diagnostics(token, profile_path)
                return INTERNAL_SERVER_ERROR, "Không tìm thấy form login trên trang", False

            outcome = classify_login_signals(signals)
            if not outcome:
                server_errors = [r for r in responses if r.get("status", 0) >= 500]
                auth_rejects = [
                    r for r in responses
                    if r.get("status") in (400, 401) and "/passport/" in r.get("url", "")
                ]
                forbidden = [
                    r for r in responses
                    if r.get("status") == 403 and "/passport/" in r.get("url", "")
                ]
                if server_errors:
                    outcome = INTERNAL_SERVER_ERROR
                elif forbidden:
                    outcome = CHECKPOINT
                elif auth_rejects:
                    outcome = CREDENTIAL_REJECTED
            if outcome:
                self._log("  Login phân loại: %s" % _describe_outcome(outcome))
                return outcome, "", False

            if signals.get("navigated_away") or outcome is None:
                try:
                    state = _studio_login_state(token, timeout=STUDIO_VERIFY_TIMEOUT)
                except Exception as exc:
                    self._log("  Không xác minh được studio: %s" % exc)
                    self._capture_diagnostics(token, profile_path)
                    return TIMEOUT, "Không xác minh được studio", False
                if state == "authenticated":
                    self._log("  Login thành công, studio đã xác nhận")
                    return AUTHENTICATED, "", True

            self._capture_diagnostics(token, profile_path)
            return TIMEOUT, "Trạng thái login không xác định", False
        finally:
            if token is not None:
                try:
                    token.quit(timeout=10)
                except Exception:
                    pass
            try:
                self._clear_profile_lock(profile_path)
            except Exception:
                pass

    def _capture_diagnostics(self, token, profile_path):
        payload = {}
        try:
            dom = _json_plain(_dump_login_dom(token))
            payload["dom_controls"] = dom.get("controls", [])
            payload["forms"] = dom.get("forms", [])
        except Exception as exc:
            payload["dom_error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            payload["page_text"] = _dump_page_text(
                token, email=str(self.config.get("email", "") or "")
            )
        except Exception as exc:
            payload["page_text_error"] = "%s: %s" % (type(exc).__name__, exc)
        try:
            payload["url"] = glue.page_url(token, timeout=15)
        except Exception:
            pass
        try:
            self._write_json(Path(profile_path) / "login_diagnostics.json", payload)
            self._log("  Diagnostics: %s" % (Path(profile_path) / "login_diagnostics.json"))
        except Exception as exc:
            self._log("  Diagnostics write error: %s" % exc)
        try:
            _capture_screenshot(token, Path(profile_path) / "login_state.png")
            self._log("  Screenshot: %s" % (Path(profile_path) / "login_state.png"))
        except Exception:
            pass

    def _clear_profile_lock(self, profile_path):
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            try:
                lock = Path(profile_path) / name
                if lock.exists():
                    lock.unlink(missing_ok=True)
            except OSError:
                pass

    def _verify_session_persistence(self, candidate, profile_path):
        token = None
        try:
            token = self._open_candidate_session(candidate, profile_path, headed=False)
            state = _studio_login_state(token, timeout=PERSISTENCE_VERIFY_TIMEOUT)
            if state != "authenticated":
                return False, ""
            cookies = glue.export_cookies(token)
            cookie_json = json.dumps(cookies, ensure_ascii=False)
            return True, cookie_json
        except Exception as exc:
            return False, "%s: %s" % (type(exc).__name__, exc)
        finally:
            if token is not None:
                try:
                    token.quit(timeout=10)
                except Exception:
                    pass
            try:
                self._clear_profile_lock(profile_path)
            except Exception:
                pass

    def _promote(self, candidate):
        """Promote a verified candidate into the official owned profile."""
        self._log("Xác minh session bền vững cho candidate %s..." % candidate.get("label", ""))
        profile_path = Path(candidate.get("profile_path") or "")
        persistent, cookie_json = self._verify_session_persistence(candidate, profile_path)
        if not persistent:
            self._log("  Session không bền vững sau khi đóng/mở lại")
            return False

        self._log("  Promotion profile thắng cuộc...")
        account_id = str(self.config.get("account_uuid", "") or "").strip() or None
        quarantine_dir = Path(self.managed_root) / "LoginTests" / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        quarantine_path = quarantine_dir / ("Profile-Patchright-%s" % self.run_id)

        old_official = self.official_profile
        record = {
            "run_id": self.run_id,
            "profile_name": self.profile_name,
            "account_uuid": account_id,
            "status": "prepared",
            "old_profile_path": str(old_official),
            "quarantine_path": str(quarantine_path),
            "official_path": str(self.official_profile),
            "config_snapshot": snapshot_config(self.config),
        }
        transaction = _transaction_path(self.managed_root, self.run_id)
        self._write_json(transaction, record)
        try:
            if old_official.exists():
                os.replace(str(old_official), str(quarantine_path))
            record["status"] = "promoting"
            self._write_json(transaction, record)

            official = create_patchright_profile(
                str(self.legacy_profile), str(self.managed_root), account_id=account_id
            )
            self._copy_profile_data(profile_path, official)
            mark_profile_login_verified(official, note="auto-login-environment")

            self._apply_config_for_promotion(candidate, official, cookie_json)
            self._persist_config()

            committed = self._verify_promotion(candidate)
            if not committed:
                self._log("  Final verification thất bại; rollback promotion")
                self._rollback_promotion(record)
                return False

            record["status"] = "committed"
            self._write_json(transaction, record)
            try:
                transaction.unlink(missing_ok=True)
            except OSError:
                pass
            return True
        except Exception as exc:
            self._log("  Promotion lỗi: %s" % exc)
            self._rollback_promotion(record)
            return False

    def _rollback_promotion(self, record):
        """Undo an in-flight promotion: files first, then persisted config."""
        try:
            rollback_transaction(record)
        except Exception:
            pass
        snapshot = record.get("config_snapshot", {})
        try:
            restore_config(self.config, snapshot)
            _restore_stored_config(self.profile_name, snapshot)
        except Exception:
            pass

    def _copy_profile_data(self, source_dir, target_dir):
        source = Path(source_dir)
        target = Path(target_dir)
        for child in source.iterdir():
            destination = target / child.name
            if child.is_dir():
                shutil.copytree(child, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(child, destination)

    def _apply_config_for_promotion(self, candidate, official, cookie_json):
        cfg = self.config
        cfg["proxy_string"] = candidate.get("proxy", "")
        cfg["proxy_type"] = "http"
        cfg["use_proxy"] = True
        cfg["fingerprint"] = build_fingerprint_for_probe(candidate, candidate.get("proxy", ""))
        cfg["browser_profile_path"] = str(official)
        cfg["migration_state"] = "login_verified"
        if cookie_json:
            cfg["cookie_str"] = cookie_json
        cfg["session_auth_state"] = "verified"
        cfg["session_source"] = "automated_login"
        cfg["session_verified_at"] = _now_iso()
        cfg["session_verified_profile_path"] = str(official)
        cfg["session_verified_proxy_key"] = session_proxy_key(cfg)
        cfg["session_last_failure_at"] = ""
        cfg["session_last_failure_reason"] = ""
        cfg["manual_login_pending"] = False
        cfg["profile_owner_state"] = "verified"
        cfg["profile_isolation_state"] = "isolated"
        cfg["profile_created_at"] = _now_iso()
        cfg["cookies_last_injected_at"] = _now_iso()
        cfg["cookies_last_injected_profile_path"] = str(official)

    def _verify_promotion(self, candidate):
        """Final reopen with the official config to confirm promotion held."""
        token = None
        try:
            cfg = dict(self.config)
            cfg["headless"] = bool(self.config.get("headless", True))
            token = self._open_candidate_session(candidate, Path(self.official_profile), headed=not cfg["headless"])
            state = _studio_login_state(token, timeout=PERSISTENCE_VERIFY_TIMEOUT)
            return state == "authenticated"
        except Exception as exc:
            self._log("  Final verification lỗi: %s" % exc)
            return False
        finally:
            if token is not None:
                try:
                    token.quit(timeout=10)
                except Exception:
                    pass
            try:
                self._clear_profile_lock(self.official_profile)
            except Exception:
                pass

    def _config_path(self):
        return _config_file_path()

    def _persist_config(self):
        """Write the promoted config back into the stored profile entry."""
        path = self._config_path()
        payload = json.loads(path.read_text(encoding="utf-8"))
        profiles = payload.get("profiles")
        if not isinstance(profiles, dict):
            return
        entry = profiles.get(self.profile_name)
        if not isinstance(entry, dict):
            return
        for key in RESTORED_CONFIG_FIELDS:
            entry[key] = self.config.get(key, entry.get(key))
        entry["account_uuid"] = self.config.get("account_uuid", entry.get("account_uuid", ""))
        entry["headless"] = self.config.get("headless", entry.get("headless", True))
        save_configs_file(path, payload)

    def _write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".txn-", dir=str(path.parent))
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=True, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _write_run_files(self, snapshot):
        if self.work_dir is None:
            return
        try:
            report_file = self.work_dir / "report.json"
            self._write_json(report_file, redact_report(self.report))
        except Exception:
            pass


def _describe_outcome(outcome):
    return {
        PREFLIGHT_FAILED: "preflight thất bại",
        INTERNAL_SERVER_ERROR: "Internal server error",
        CREDENTIAL_REJECTED: "credential bị từ chối",
        CAPTCHA: "captcha xuất hiện",
        CHECKPOINT: "checkpoint/xác minh",
        AUTHENTICATED: "đăng nhập thành công",
        SESSION_NOT_PERSISTENT: "session không bền vững",
        TIMEOUT: "timeout",
        ERROR: "lỗi runtime",
    }.get(outcome, outcome)
