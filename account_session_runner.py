"""Shared authenticated session runner for read-only TikTok operations.

Owns the steps both the cookie live-check and the account inspection need so
they follow the same safety rules: proxy resolution + browser exit-IP
verification and an auth probe that prefers API evidence over DOM selectors.

Kept out of main.py so main.py's proxy-verification call count stays stable
(test_proxy_configuration asserts an exact ``browser_glue.verify_exit_ip(``
count inside main.py only).
"""

from typing import Optional, Tuple

import browser_patchright_glue as browser_glue
from browser_environment import verify_direct_endpoint, verify_proxy_endpoint
from core_helpers import parse_proxy_string

# Read-only identity endpoints used as authentication evidence. These returned
# real identity payloads during discovery on AUTO 6.
IDENTITY_PATHS = (
    "/tiktokstudio/api/web/user",
    "/node-webapp/api/common-app-context",
)

_CHALLENGE_MARKERS = ("/challenge", "/verify", "/captcha")


class SessionCancelled(RuntimeError):
    """User asked to stop the current read-only session."""


class SessionRunnerError(RuntimeError):
    """A shared session step failed (proxy/browser/auth)."""

    def __init__(self, reason, kind="error"):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind  # "proxy" | "browser" | "auth"


def _raise_if_cancelled(cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        raise SessionCancelled("Đã dừng bởi người dùng")


def resolve_proxy(config):
    """Parse + endpoint-preflight the proxy.

    Returns ``(proxy_data, preflight)``. Raises :class:`SessionRunnerError`
    with ``kind="proxy"`` when the proxy is invalid or unverifiable.
    """
    if not config.get("use_proxy", False):
        return None, {"proxy_exit_ip": None, "direct_ip": None}
    proxy_data = parse_proxy_string(config.get("proxy_string", ""))
    if not proxy_data:
        raise SessionRunnerError("Proxy sai định dạng; từ chối mở browser trực tiếp", kind="proxy")
    preflight = {"proxy_exit_ip": None, "direct_ip": None}
    try:
        preflight["proxy_exit_ip"] = verify_proxy_endpoint(proxy_data)
    except Exception:
        preflight["proxy_exit_ip"] = None
    if not preflight["proxy_exit_ip"]:
        raise SessionRunnerError("Không xác minh được proxy endpoint", kind="proxy")
    try:
        preflight["direct_ip"] = verify_direct_endpoint()
    except Exception:
        preflight["direct_ip"] = None
    return proxy_data, preflight


def verify_browser_proxy(token, proxy_data, preflight):
    """Verify the live browser egresses via the configured proxy.

    Returns ``(is_match, current_ip, detail)``.

    * Proxy exit IP matches endpoint preflight → pass.
    * Rotating proxy (browser IP != direct IP) → pass with a warning.
    * Browser fell back to the direct IP → fail closed.
    * Unknown browser IP → indeterminate (never a concrete mismatch).
    """
    expected_ip = preflight.get("proxy_exit_ip") or (proxy_data or {}).get("ip")
    direct_ip = preflight.get("direct_ip")
    try:
        is_match, current_ip = browser_glue.verify_exit_ip(token, expected_ip)
    except Exception:
        return False, None, "Proxy Verification Indeterminate"
    if is_match:
        return True, current_ip, ""
    if direct_ip and current_ip and current_ip != direct_ip:
        return True, current_ip, "Proxy exit IP thay đổi"
    if not current_ip:
        return False, None, "Proxy Verification Indeterminate"
    return False, current_ip, f"Proxy sai IP: {current_ip}"


def _is_challenge_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


def probe_account_auth(token, cancel_event=None, timeout=40):
    """Determine TikTok authentication using API evidence.

    Returns one of ``authenticated``, ``login_required``, ``checkpoint``,
    ``rate_limited``, ``indeterminate``. ``checkpoint`` is reported only when
    there is concrete challenge evidence; everything ambiguous stays
    ``indeterminate`` and is never fabricated as a checkpoint.
    """
    _raise_if_cancelled(cancel_event)
    try:
        state = browser_glue.wait_page_login_state(token, timeout=timeout)
    except Exception:
        state = "indeterminate"
    if state == "authenticated":
        return "authenticated"
    try:
        fetched = browser_glue.inspect_tiktok_account(
            token, endpoint_paths=IDENTITY_PATHS, timeout=timeout
        )
    except Exception:
        fetched = {"results": {}, "errors": []}
    results = fetched.get("results") or {}
    errors = fetched.get("errors") or []
    if results:
        return "authenticated"
    statuses = []
    for error in errors:
        try:
            statuses.append(int(error.get("status") or 0))
        except (TypeError, ValueError):
            continue
    if 429 in statuses:
        return "rate_limited"
    if any(status in (401, 403) for status in statuses):
        return "login_required"
    try:
        url = browser_glue.page_url(token, timeout=timeout)
    except Exception:
        url = ""
    if _is_challenge_url(url):
        return "checkpoint"
    return "indeterminate"


def close_token_confirmed(token) -> bool:
    """Close a session token and report whether release was confirmed."""
    if token is None:
        return True
    try:
        return bool(token.quit())
    except Exception:
        return False


def _mask(text: str) -> str:
    from cookie_live_check import mask_detail

    return mask_detail(text)
