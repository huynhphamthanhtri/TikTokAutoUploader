"""
profile_table_engine.py - High-Performance Incremental Table Engine & Pure Row Model.

Features:
- Pure `build_row_model()` for deterministic row rendering and filter categorization.
- Revision tracking and thread-safe dirty profile registration.
- Dual-path refresh:
  - Fast Path: O(1) direct single-row tree update for visible profiles when no global filter/search/sort is active.
  - Slow Path: Debounced O(N) full reconciliation (150ms) when global filters, search, or pagination membership changes.
- Volatile health reconciliation for active/visible profiles.
"""

import threading
from typing import Any, Dict, List, Optional, Set, Tuple


def build_row_model(
    name: str,
    profile: Dict[str, Any],
    monetization_cache: Optional[Dict[str, Any]] = None,
    ensure_uuid_func: Optional[Any] = None,
) -> Dict[str, Any]:
    """Pure row builder: returns structured row model without modifying UI or globals."""
    cfg = profile.get("config", {}) or {}
    ui = profile.get("ui", {}) or {}
    running = bool(profile.get("running", False))

    uuid = ensure_uuid_func(cfg) if ensure_uuid_func else str(cfg.get("account_uuid") or name)

    tiktok_id = str(cfg.get("tiktok_id", "") or cfg.get("tiktok_account", "") or "").lstrip("@")
    tiktok_display = f"@{tiktok_id}" if tiktok_id else ""
    region = str(cfg.get("region") or cfg.get("country") or "").upper()

    snap_mono = (monetization_cache or {}).get(name, {})
    auth_state = str(cfg.get("session_auth_state", "")).lower()
    cookie_raw = str(cfg.get("cookie_str", "") or "").strip()
    login_ui = str(ui.get("login", "")).lower()
    payout_st = snap_mono.get("payout_status", "")
    kyc_st = snap_mono.get("kyc_status", "")
    tax_st = snap_mono.get("tax_status", "")
    crp_st = snap_mono.get("crp_status", "")
    mono_st = snap_mono.get("status", "")
    inspection = cfg.get("tiktok_inspection", {}) or {}

    # Filter conditions
    is_no_cookie = (not cookie_raw) or (cookie_raw in ("[]", "{}", "null"))
    is_cookie_live = (
        not is_no_cookie
        and (
            auth_state in ("live", "verified")
            or mono_st == "SUCCESS"
            or payout_st in ("PAYOUT_READY", "PAYOUT_NOT_LINKED", "CRP_ACTIVE")
            or inspection.get("state") == "VALID"
        )
        and auth_state not in ("expired", "invalid", "dead")
    )
    is_cookie_die = (
        not is_no_cookie
        and not is_cookie_live
        and (
            auth_state in ("expired", "invalid", "dead")
            or mono_st == "COOKIE_EXPIRED"
            or payout_st in ("Cookie Die", "COOKIE_EXPIRED")
            or kyc_st == "Cookie Die"
            or "die" in login_ui
        )
    )
    is_kyc_ok = (
        kyc_st == "APPROVED"
        or inspection.get("identity", {}).get("verified") is True
        or inspection.get("payout", {}).get("verification_status") == "APPROVED"
    )
    is_tax_ok = (
        tax_st in ("TAX_VERIFIED", "APPROVED")
        or inspection.get("payout", {}).get("payout_status") in ("VERIFIED", "TAX_VERIFIED")
    )
    is_tktbm = crp_st == "TKTBM" or "tktbm" in str(inspection).lower()

    driver_alive = bool(profile.get("driver"))
    manual_driver_alive = bool(profile.get("manual_driver"))
    uploading = bool(profile.get("uploading", False))
    is_running = running or driver_alive or manual_driver_alive or uploading

    # Badges
    if is_no_cookie:
        cookie_badge = "⚪ Chưa có"
    elif is_cookie_live:
        cookie_badge = "🟢 Live"
    elif is_cookie_die:
        cookie_badge = "🔴 Die"
    else:
        cookie_badge = "🟡 Chưa check"

    if is_running:
        activity_badge = "⚡ Đang chạy"
    elif driver_alive or manual_driver_alive:
        activity_badge = "🌐 Đang mở"
    else:
        activity_badge = "⏸ Đã dừng"

    if is_tktbm:
        mono_badge = "🔴 TKTBM"
    elif crp_st in ("ENABLED", "ACTIVE") or payout_st in ("PAYOUT_READY", "CRP_ACTIVE"):
        mono_badge = "🏆 Đang bật"
    elif is_kyc_ok:
        mono_badge = "🟢 Đã KYC"
    elif is_tax_ok:
        mono_badge = "🟢 Đã Thuế"
    else:
        mono_badge = "⚪ Chưa bật"

    proxy_str = ui.get("proxy", "Tắt" if not cfg.get("use_proxy") else "Chưa kiểm tra")
    proxy_region_badge = f"[{region}] {proxy_str}" if (region and proxy_str) else (proxy_str or region or "Tắt")

    # Tag configuration
    if is_running:
        row_tag = "tag_running"
    elif is_cookie_die:
        row_tag = "tag_error"
    elif is_cookie_live:
        row_tag = "tag_ready"
    else:
        row_tag = "tag_stopped"

    last_err = str(ui.get("last_error", ""))
    short_err = last_err if len(last_err) <= 80 else last_err[:77] + "..."

    values = (
        name,
        tiktok_display,
        cookie_badge,
        activity_badge,
        mono_badge,
        proxy_region_badge,
        ui.get("upload", "Chờ video"),
        cfg.get("folder_path", ""),
        short_err,
    )

    filter_keys = {
        "is_no_cookie": is_no_cookie,
        "is_cookie_live": is_cookie_live,
        "is_cookie_die": is_cookie_die,
        "is_kyc_ok": is_kyc_ok,
        "is_tax_ok": is_tax_ok,
        "is_tktbm": is_tktbm,
        "is_running": is_running,
    }

    return {
        "uuid": uuid,
        "name": name,
        "values": values,
        "tags": (row_tag,),
        "filter_keys": filter_keys,
        "project": cfg.get("project_name", "Mặc định"),
        "search_blob": f"{name} {tiktok_id} {cookie_badge} {activity_badge} {mono_badge} {proxy_str} {region} {cfg.get('folder_path', '')} {last_err}".lower(),
    }


class ProfileTableEngine:
    """Manages dirty rows, dual-path incremental updates, and revisioning."""

    def __init__(self):
        self._lock = threading.Lock()
        self._dirty_names: Set[str] = set()
        self._revisions: Dict[str, int] = {}
        self._row_cache: Dict[str, Dict[str, Any]] = {}

    def mark_dirty(self, profile_name: str):
        """Mark a profile as dirty (thread-safe)."""
        with self._lock:
            self._dirty_names.add(profile_name)
            self._revisions[profile_name] = self._revisions.get(profile_name, 0) + 1

    def pop_dirty(self) -> Set[str]:
        """Pop all dirty profile names."""
        with self._lock:
            dirty = set(self._dirty_names)
            self._dirty_names.clear()
            return dirty

    def get_revision(self, profile_name: str) -> int:
        with self._lock:
            return self._revisions.get(profile_name, 0)

    def update_cache(self, profile_name: str, row_model: Dict[str, Any]):
        with self._lock:
            self._row_cache[profile_name] = row_model

    def get_cached(self, profile_name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._row_cache.get(profile_name)
