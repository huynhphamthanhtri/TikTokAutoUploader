import os
import sys
import queue
import json
import shutil
import requests
import copy
from dataclasses import replace
from urllib.parse import urlsplit
from pathlib import Path
from datetime import datetime, timezone, timedelta

def app_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_base_dir():
    if getattr(sys, "frozen", False):
        internal = Path(sys.executable).resolve().parent / "_internal"
        if internal.exists():
            return internal
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog_service import (
    DeliveryOutcome,
    DeliveryState,
    QueueItem,
    configure_delivery_registry,
    get_delivery_registry,
    get_watchdog_manager,
)
import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk, StringVar, Menu
from tkinter.scrolledtext import ScrolledText
from app_ui import configure_ttk_styles, build_dashboard, classify_log_message
from ui_components import UIThemeTokens, fit_and_center_dialog, calculate_centered_geometry, apply_app_icon
from core_helpers import (
    parse_proxy_string,
    parse_cookie,
    is_file_stable,
    normalize_profile_path,
    process_uses_profile,
    copy_video_atomically,
)
from account_io import (
    DEFAULT_FIELDS,
    DEFAULT_FORMAT,
    LEGACY_FORMAT,
    SENSITIVE_FIELDS,
    parse_format,
    parse_data_into_records,
    plan_import,
    apply_update_to_config,
    serialize_records,
    record_from_config,
    masked_record,
)
from profile_ownership import (
    build_profile_inventory,
    conflict_account_names,
    detect_profile_conflicts,
    ensure_account_uuid,
    invalidate_session_auth,
    session_proxy_key as ownership_session_proxy_key,
)
from browser_maintenance import (
    FULL as BROWSER_MAINTENANCE_FULL,
    QUICK as BROWSER_MAINTENANCE_QUICK,
    SESSION as BROWSER_MAINTENANCE_SESSION,
    adopt_legacy_owned_root,
    create_owned_root,
    maintain_browser,
)
import browser_patchright_glue as browser_glue
from browser_patchright_glue import ProfileBusyError, SessionSetupError, SessionToken
from profile_runtime_status import (
    RuntimeSignals,
    build_runtime_snapshot,
    automation_label,
    browser_label,
    upload_label,
    row_tags,
    batch_start_preflight,
    OperationState,
)
from cookie_live_check import (
    CookieCheckState,
    CookieSource,
    CookieCheckResult,
    CookieCheckMode,
    classify_login_state,
    primary_auth_cookie_names,
    build_summary,
    mask_detail,
    check_cookie_fast_http,
)
import account_session_runner as session_runner
from tiktok_account_inspection import (
    AccountInspectionResult,
    InspectionState,
    build_inspection_result,
    build_inspection_summary,
    classify_account,
    to_plain,
)
from tiktok_account_discovery import SEED_ENDPOINTS
from inspection_repository import InspectionRepository
from tiktok_capability_requests import build_capability_requests
from tiktok_schema_adapters import build_capability_results
from patchright_profile_migration import (
    MigrationState,
    advance_migration,
    cleanup_legacy_profile,
    create_patchright_profile,
    migration_status,
)
from config_store import (
    build_configs_payload,
    save_configs_file,
    load_configs_file,
    normalize_loaded_config,
    build_runtime_profiles,
)
from browser_environment import (
    GEO_ENVIRONMENT_KEYS,
    ensure_fingerprint_defaults,
    geo_cache_is_current,
    locale_for_country,
    proxy_cache_key,
    resolve_geoip,
    verify_direct_endpoint,
    verify_proxy_endpoint,
)
from proxy_environment import (
    RISKY_CHANGE as PROXY_ENV_RISKY,
    SAME as PROXY_ENV_SAME,
    UNKNOWN as PROXY_ENV_UNKNOWN,
    apply_proxy_environment_warning,
    compare_proxy_environment,
    proxy_environment_snapshot,
)
from browser_profile_quarantine import (
    cleanup_quarantines,
    latest_quarantine,
    quarantine_profile,
    restore_quarantine,
    restore_target,
)
import youtube_monitor
from youtube_monitor.activity import append_activity, clear_activity_log, get_activity_logs, get_activity_mtime, get_activity_stats, lookup_download
from version import __version__ as CURRENT_VERSION, APP_NAME, GITHUB_REPO_OWNER, GITHUB_REPO_NAME
from updater import GitHubReleaseUpdater, get_current_version
from updater_config import load_updater_config, update_updater_config
import logging
import zipfile
import time
import threading
import signal
import warnings
import psutil

# --- LICENSE IMPORTS ---
import platform
import uuid
import hashlib
import re
import subprocess
import webbrowser
try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

# Ẩn cảnh báo không quan trọng
warnings.filterwarnings("ignore", category=DeprecationWarning)

# =========================
# Cấu hình Chung
# =========================
PRIVACY_LEVEL = "PUBLIC"
VIDEO_EXTENSIONS = (".mp4", ".mov")
MAX_FILE_SIZE = 500 * 1024 * 1024
RETRY_COUNT = 1 
HASHTAGS = " #fyp #tiktok"
CONFIGS_FILE = app_base_dir() / "configs.json"
FAST_MODE = True
FILE_STABLE_CHECKS = 2
FILE_STABLE_INTERVAL = 0.15
SMALL_WAIT = 0.5 
ALL_OPTION = "Default"
START_PROFILE_TIMEOUT = 180 
DRIVER_INIT_RETRIES = 2 
DRIVER_INIT_RETRY_DELAY = 1.0
IDLE_SHUTDOWN_TIMEOUT = 0
LIMIT_REACHED_SHUTDOWN_DELAY = 5
MAX_STATUS_LOG_LINES = 1000
MAX_IMPORTANT_LOG_LINES = 300
TIKTOK_BASE_URL = "https://www.tiktok.com"
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video"
FAILED_UPLOADS_LOG = app_base_dir() / "failed_uploads.log"
UPLOAD_BENCHMARK_LOG = app_base_dir() / "upload_benchmarks.jsonl"

# =========================
# FINGERPRINT CONFIG
# =========================
def _generate_fingerprint(seed=None):
    return ensure_fingerprint_defaults({"lang": "en-US"}, seed=seed)

def _refresh_profile_geoip(profile_name, config, proxy_data, force=False):
    """Resolve proxy location only when its identity changed or cache is missing."""
    fingerprint = ensure_fingerprint_defaults(
        config.get('fingerprint', {}),
        seed=profile_name + str(config.get('cookie_str', '')),
    )
    config['fingerprint'] = fingerprint
    if not proxy_data:
        if fingerprint.get('geo_source') == 'ipwho.is':
            for key in GEO_ENVIRONMENT_KEYS:
                fingerprint.pop(key, None)
            return True
        return False
    if fingerprint.get('geo_proxy_hash') != proxy_cache_key(proxy_data):
        for key in GEO_ENVIRONMENT_KEYS:
            fingerprint.pop(key, None)
    if not force and geo_cache_is_current(fingerprint, proxy_data):
        return False
    try:
        resolved = resolve_geoip(proxy_data)
        fingerprint.update(resolved)
        config['fingerprint'] = fingerprint
        config['geoip_last_error'] = ''
        update_status(
            f"[{profile_name}] GeoIP: {resolved['timezone']} "
            f"({resolved['geolocation']['latitude']:.4f}, "
            f"{resolved['geolocation']['longitude']:.4f})"
        )
        return True
    except Exception as error:
        config['geoip_last_error'] = str(error)
        update_status(f"[{profile_name}] [WARN] Không lấy được GeoIP qua proxy: {error}")
        return False

# =========================
# CẤU HÌNH LICENSE
# =========================
LICENSE_REQUIRED = True
SERVICE_ACCOUNT_FILE = app_base_dir() / "service_account.json"
LICENSE_SHEET_ID = "1vvuYkp06zLAJYuky8hNCMKJ7IxcUAuByizSb-9Ry4jw" 
LICENSE_WORKSHEET = "license_sheet_sample" 
OFFLINE_CACHE_FILE = app_base_dir() / "license_cache.json"
LICENSE_GRACE_SECONDS = 3 * 24 * 3600 
LICENSE_RECHECK_INTERVAL = 6 * 3600 
VALID_STATUSES = {"ACTIVE", "TRIAL"}

# Biến toàn cục License
LICENSE_OK = False
LICENSE_INFO = {}
LICENSE_KEY = None

# Biến toàn cục App
profiles = {}
projects = {}
running_profiles = set()
profile_operation_locks = {}
_profile_refresh_pending = False
_tree_sort_state = None
_cookie_check_batch = {"active": False, "cancel": False}
_inspection_batch = {"active": False}

from browser_lifecycle import get_lifecycle, remove_lifecycle


def _browser_session_valid(session):
    if isinstance(session, SessionToken):
        try:
            return session.is_alive()
        except Exception:
            return False
    return False


def _sync_patchright_migration(config):
    profile_path = config.get('browser_profile_path')
    if not profile_path:
        return None
    status = migration_status(profile_path)
    config['migration_state'] = status['state']
    return status


def _advance_patchright_migration(config, expected_state, new_state):
    status = _sync_patchright_migration(config)
    if status and status['state'] == expected_state:
        status = advance_migration(config['browser_profile_path'], new_state)
        config['migration_state'] = status['state']
    return status


def _cleanup_legacy_profile_after_verified_upload(profile_name, config):
    status = _sync_patchright_migration(config)
    if not status or status['state'] not in {
        MigrationState.UPLOAD_VERIFIED.value,
        MigrationState.LEGACY_CLEANUP_PENDING.value,
    }:
        return status
    profile_path = Path(config['browser_profile_path'])
    status = cleanup_legacy_profile(
        profile_path,
        profile_path.parent,
        explicit_confirmation=True,
    )
    config['migration_state'] = status['state']
    update_status(f"[{profile_name}] Đã xóa profile Orbita cũ sau khi xác minh upload Patchright.")
    return status


def _profile_operation_lock(profile_name):
    lock = profile_operation_locks.get(profile_name)
    if lock is None:
        lock = threading.Lock()
        profile_operation_locks[profile_name] = lock
    return lock


class OperationClaimError(RuntimeError):
    """Raised when a profile cannot be claimed for a session operation."""

    def __init__(self, reason, profile_busy=False):
        super().__init__(reason)
        self.reason = reason
        self.profile_busy = profile_busy


def _claim_profile_operation(name, operation, preflight_fn=None):
    """Atomically claim an operation slot for one profile.

    Runs the preflight while the profile is still IDLE, then sets operation +
    session_busy + a claim id. Returns the claim id. Raises OperationClaimError
    when the profile cannot be claimed (missing, busy, conflict, or preflight
    rejected).
    """
    lock = _profile_operation_lock(name)
    with lock:
        profile = profiles.get(name)
        if profile is None:
            raise OperationClaimError("Không tồn tại")
        if _blocked_by_profile_conflict(name):
            raise OperationClaimError(_profile_conflict_message(name))
        if _browser_session_valid(profile.get('manual_driver')):
            raise OperationClaimError("Browser thủ công đang mở")
        if _profile_browser_process_count(name) > 0:
            raise OperationClaimError("Profile đang bị browser khác giữ", profile_busy=True)
        if preflight_fn is not None:
            reason = preflight_fn(name)
            if reason:
                raise OperationClaimError(reason)
        claim_id = uuid.uuid4().hex
        profile['operation'] = operation.value if hasattr(operation, 'value') else str(operation)
        profile['operation_claim'] = claim_id
        profile['session_busy'] = True
        request_profile_refresh()
        return claim_id


def _release_profile_operation(name, claim_id, close_confirmed=True):
    """Release an operation slot only if ``claim_id`` is still the owner.

    Never overwrites a newer operation owned by another claim. When the browser
    close was not confirmed, the profile stays busy and is marked close-
    unconfirmed so Start/upload cannot collide with a still-open session.
    """
    lock = _profile_operation_lock(name)
    with lock:
        profile = profiles.get(name)
        if profile is None:
            return
        if profile.get('operation_claim') != claim_id:
            return
        profile['operation_claim'] = ''
        profile['operation'] = OperationState.IDLE.value
        if close_confirmed:
            profile['session_busy'] = False
            _set_profile_ui(name, browser='Đã đóng')
        else:
            profile['session_busy'] = True
            _set_profile_ui(name, browser='đóng chưa sạch')
        request_profile_refresh()

UPLOAD_EVENT_TIMINGS = {}
UPLOAD_TERMINAL_RESULTS = {}
upload_benchmark_lock = threading.Lock()
video_event_lock = threading.Lock()


class TikTokLoginRequiredError(Exception):
    """Raised when TikTok explicitly redirects the browser to its login page."""


# -------------------------------------------------------

# Cấu hình logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(message)s",
    handlers=[
        logging.FileHandler(app_base_dir() / "upload.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

def _timing_log(profile_name, label, start_ts):
    update_status(f"[{profile_name}] [TIMING] {label}: {time.perf_counter() - start_ts:.2f}s")


def _upload_timing_key(video_path):
    return os.path.normcase(os.path.abspath(str(video_path)))


def _mark_upload_timing(video_path, label, value=None):
    key = _upload_timing_key(video_path)
    with upload_benchmark_lock:
        timing = UPLOAD_EVENT_TIMINGS.setdefault(key, {})
        timing[label] = time.perf_counter() if value is None else value
        return dict(timing)


def _claim_video_path(video_path):
    """Legacy dedupe gate backed by the unified delivery registry."""
    ok, _rec, _reason = get_delivery_registry().claim_delivery(video_path, "", 0, "LEGACY")
    return ok


def _release_video_path(video_path):
    get_delivery_registry().release_delivery(video_path)


def enqueue_video(profile_name, video_path, source="FAST_PATH", channel_id=None, youtube_video_id=None, title=None):
    """Single coordinator for enqueueing a video into a profile's queue.

    Applies at-most-once semantics per session through the unified delivery registry:
      - profile missing / not running  -> marks WAITING_PROFILE (keeps the file)
      - claim is atomic; only one producer (Fast Path / Watchdog / startup scan) wins
      - generation is re-checked immediately before queue.put()
      - any pre-enqueue failure rolls the claim back so the video can be picked up later
    Returns (ok: bool, reason: str).
    """
    reg = get_delivery_registry()
    if profile_name not in profiles:
        reg.mark_waiting_profile(video_path, profile_name, source=source,
                                 channel_id=channel_id, youtube_video_id=youtube_video_id)
        return False, "profile_missing"
    profile = profiles[profile_name]
    lc = get_lifecycle(profile_name)
    if not profile.get('running', False) or lc.is_cancelled:
        reg.mark_waiting_profile(video_path, profile_name, source=source,
                                 channel_id=channel_id, youtube_video_id=youtube_video_id)
        update_status(f"[{profile_name}] Video đang chờ hồ sơ đích khởi động: {Path(video_path).name}")
        return False, "waiting_profile"

    gen = lc.generation
    ok, rec, reason = reg.claim_delivery(
        video_path, profile_name, gen, source,
        channel_id=channel_id, youtube_video_id=youtube_video_id,
    )
    if not ok:
        if reason == "tombstone":
            update_status(f"[{profile_name}] Bỏ qua {Path(video_path).name}: đã xử lý trước đó (không tự đăng lại).")
        return False, reason

    # Generation re-check immediately before enqueue.
    if lc.is_cancelled or lc.generation != gen or not profiles.get(profile_name, {}).get('running', False):
        reg.release_delivery(video_path, error_code="GENERATION_CHANGED", error_detail="lifecycle changed before enqueue")
        return False, "generation_changed"

    try:
        item = QueueItem(
            path=str(video_path),
            profile_name=profile_name,
            lifecycle_generation=gen,
            source=source,
            delivery_id=rec.delivery_id,
            enqueued_at=time.time(),
        )
        profile['queue'].put(item)
    except Exception as e:
        reg.release_delivery(video_path, error_code="ENQUEUE_FAILED", error_detail=str(e)[:500])
        logging.warning(f"[{profile_name}] Enqueue thất bại, đã trả lại quyền xử lý: {e}")
        update_status(f"[{profile_name}] Không thể đưa video vào hàng chờ: {e}")
        return False, "enqueue_failed"

    reg.transition_delivery(video_path, DeliveryState.ENQUEUED)
    update_status(f"[{profile_name}] ({source}) Nhận video mới: {Path(video_path).name}")
    return True, "enqueued"


def _complete_delivery_from_upload(video_path, result, last_error, benchmark_success):
    """Map an upload_video() result to a terminal delivery outcome.

    Never auto-retries after an uncertain post; leaves a tombstone so a possibly
    posted video is not enqueued again.
    """
    reg = get_delivery_registry()
    if result is not None and getattr(result, 'outcome', None):
        outcome_val = str(result.outcome)
        mapping = {
            'posted': DeliveryOutcome.POSTED,
            'prepared': DeliveryOutcome.PREPARED,
            'post_uncertain': DeliveryOutcome.POST_UNCERTAIN,
            'cancelled_safe': DeliveryOutcome.CANCELLED_SAFE,
            'cancelled_uncertain': DeliveryOutcome.CANCELLED_UNCERTAIN,
            'rejected': DeliveryOutcome.REJECTED,
            'login_required': DeliveryOutcome.LOGIN_REQUIRED,
        }
        outcome = mapping.get(outcome_val)
        if outcome is not None:
            reg.complete_delivery(
                video_path,
                outcome,
                post_dispatched=bool(getattr(result, 'post_dispatched', False)),
                error_code=None if outcome in (DeliveryOutcome.POSTED, DeliveryOutcome.PREPARED) else outcome_val,
                error_detail=str(getattr(result, 'message', '') or outcome_val)[:500],
            )
            return
    reg.complete_delivery(
        video_path,
        DeliveryOutcome.FAILED_SAFE,
        post_dispatched=False,
        error_code=last_error or 'unknown',
        error_detail=str(last_error or '')[:500],
    )


def _watchdog_enqueue_callback(profile_name, file_path, generation):
    """Adapter from SharedWatchdogManager's (profile, path, gen) callback to the coordinator."""
    try:
        enqueue_video(profile_name, file_path, source="WATCHDOG_EVENT")
    except Exception as e:
        logging.warning(f"[{profile_name}] Watchdog enqueue lỗi: {e}")


def _reconcile_startup_folder(profile_name, folder, generation):
    """Startup scan: adopt only videos that must be resumed for THIS profile.

    A file is adopted when either:
    - it is genuinely new (mtime after watch_started_at) and has a known origin, or
    - it has an existing delivery record for this profile that is reclaimable
      (e.g. WAITING_PROFILE queued while the profile was down).

    Files that already existed before watching started are never adopted on metadata
    alone: the download-index basename fallback is disabled here so a video belonging to
    another profile/folder cannot be mistaken for this profile's file. Skipped files do
    not create a delivery record, so a later manual claim stays clean.
    """
    reg = get_delivery_registry()
    folder_path = Path(folder)
    if not folder_path.is_dir():
        return 0
    watch_started_at = profiles.get(profile_name, {}).get('watch_started_at', 0)
    candidates = []
    for p in folder_path.iterdir():
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0
        candidates.append((mtime, p))
    candidates.sort(key=lambda t: t[0])
    adopted = 0
    skipped_old = 0
    for _mtime, p in candidates:
        if profile_name not in profiles or not profiles[profile_name].get('running', False):
            break
        rec = reg.get_delivery(p)
        if rec is not None and rec.profile_name and rec.profile_name != profile_name:
            # Delivery belongs to another profile; never adopt for this one.
            skipped_old += 1
            continue
        is_waiting = rec is not None and rec.state == DeliveryState.WAITING_PROFILE
        is_new_file = _mtime > watch_started_at
        if not is_new_file and not is_waiting:
            # File predates watching: only a matching WAITING_PROFILE record may resume it.
            skipped_old += 1
            continue
        ok_eligible, reason = reg.is_eligible_for_startup(p, profile_name, generation)
        if not ok_eligible:
            continue
        meta = None
        try:
            meta = lookup_download(str(p), allow_basename_fallback=False)
        except Exception:
            meta = None
        if meta is None and rec is None:
            continue  # unknown/manual file -> do not auto-enqueue
        ok, _reason = enqueue_video(
            profile_name,
            str(p),
            source="WATCHDOG_STARTUP",
            channel_id=(meta or {}).get("channel_id"),
            youtube_video_id=(meta or {}).get("video_id"),
            title=(meta or {}).get("title"),
        )
        if ok:
            adopted += 1
        elif _reason in ("waiting_profile", "profile_missing"):
            break
    if skipped_old:
        update_status(f"[{profile_name}] Bỏ qua {skipped_old} video có trước khi bắt đầu theo dõi.")
    if adopted:
        update_status(f"[{profile_name}] Phục hồi {adopted} video đang chờ profile.")
    return adopted


def _write_upload_benchmark(profile_name, video_path, success, reason, phases, meta=None):
    key = _upload_timing_key(video_path)
    with upload_benchmark_lock:
        event_timing = UPLOAD_EVENT_TIMINGS.pop(key, {})
        total_start = event_timing.get('copy_started') or event_timing.get('detected_at')
        total_seconds = time.perf_counter() - total_start if total_start else sum(phases.values())
        row = {
            'finished_at': datetime.now(timezone.utc).isoformat(),
            'round': int(os.environ.get('UPLOAD_TEST_ROUND', '0') or 0),
            'profile_name': profile_name,
            'video_name': Path(video_path).name,
            'success': bool(success),
            'reason': str(reason or ''),
            'total_seconds': round(total_seconds, 3),
            'phases': {name: round(float(value), 3) for name, value in phases.items()},
            'meta': dict(meta or {}),
        }
        copy_started = event_timing.get('copy_started')
        copy_finished = event_timing.get('copy_finished')
        detected_at = event_timing.get('detected_at')
        enqueued_at = event_timing.get('enqueued_at')
        dequeued_at = event_timing.get('dequeued_at')
        if copy_started and copy_finished:
            row['phases']['copy_seconds'] = round(copy_finished - copy_started, 3)
        if copy_finished and detected_at:
            row['phases']['detect_latency_seconds'] = round(max(0, detected_at - copy_finished), 3)
        if enqueued_at and dequeued_at:
            row['phases']['queue_latency_seconds'] = round(max(0, dequeued_at - enqueued_at), 3)
        with open(UPLOAD_BENCHMARK_LOG, 'a', encoding='utf-8') as benchmark_file:
            benchmark_file.write(json.dumps(row, ensure_ascii=False) + '\n')
            benchmark_file.flush()
            os.fsync(benchmark_file.fileno())
        UPLOAD_TERMINAL_RESULTS[key] = dict(row)
        return row

def _bundled_browser_dir():
    root_dir = app_base_dir() / "Browser"
    if root_dir.exists():
        return root_dir
    internal_dir = app_base_dir() / "_internal" / "Browser"
    if internal_dir.exists():
        return internal_dir
    return root_dir

def _proxy_endpoint_preflight(profile_name, proxy_data):
    result = {'proxy_exit_ip': None, 'direct_ip': None}
    try:
        result['proxy_exit_ip'] = verify_proxy_endpoint(proxy_data)
        update_status(f"[{profile_name}] [DEBUG] Proxy endpoint preflight OK: {result['proxy_exit_ip']}")
    except Exception as error:
        update_status(f"[{profile_name}] [WARN] Proxy endpoint preflight lỗi: {type(error).__name__}")
    try:
        result['direct_ip'] = verify_direct_endpoint()
    except Exception as error:
        update_status(f"[{profile_name}] [WARN] Direct IP baseline lỗi: {type(error).__name__}")
    return result


def _append_failed_upload_log(profile_name, file_name, reason, outcome='failed'):
    try:
        _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=reason)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"{timestamp} | profile={profile_name} | outcome={outcome} | file={file_name} | reason={reason}"
        line += "\n"
        with open(FAILED_UPLOADS_LOG, 'a', encoding='utf-8') as f:
            f.write(line)
        try:
            meta = lookup_download(file_name)
            append_activity(
                "tiktok_upload",
                video_name=meta.get("title") or Path(file_name).name,
                video_url=meta.get("video_url", ""),
                profile=profile_name,
                status="fail",
                detail=f"{outcome}: {reason}",
                file_path=file_name,
            )
        except Exception:
            pass
        try:
            if failed_uploads_text.winfo_exists():
                failed_uploads_text.configure(state='normal')
                failed_uploads_text.insert(ctk.END, line, 'FAILED')
                failed_uploads_text.see(ctk.END)
                failed_uploads_text.configure(state='disabled')
        except Exception:
            pass
    except Exception as e:
        update_status(f"[{profile_name}] [DEBUG] Không thể ghi failed upload log: {e}")

def _get_cookie_hash(cookie_str):
    if not cookie_str:
        return None
    normalized = str(cookie_str).strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()

def _save_cookie_injection_metadata(profile_name, cookie_str):
    try:
        config = profiles[profile_name]['config']
        config['cookie_hash'] = _get_cookie_hash(cookie_str)
        config['cookies_last_injected_at'] = datetime.now(timezone.utc).isoformat()
        config['cookies_last_injected_profile_path'] = browser_glue.active_profile_path(config)
        save_configs()
    except Exception:
        pass

def _session_proxy_key(config):
    return ownership_session_proxy_key(config)

def _save_session_auth_metadata(profile_name, state, source=''):
    try:
        config = profiles[profile_name]['config']
        config['session_auth_state'] = state
        config['session_source'] = source
        config['session_verified_at'] = datetime.now(timezone.utc).isoformat()
        config['session_verified_profile_path'] = browser_glue.active_profile_path(config)
        config['session_verified_proxy_key'] = _session_proxy_key(config)
        if state == 'verified':
            config['session_last_failure_at'] = ''
            config['session_last_failure_reason'] = ''
            config['manual_login_pending'] = False
        save_configs()
    except Exception:
        pass

def _mark_session_failure(profile_name, reason):
    try:
        config = profiles[profile_name]['config']
        config['session_auth_state'] = 'expired'
        config['session_last_failure_at'] = datetime.now(timezone.utc).isoformat()
        config['session_last_failure_reason'] = str(reason)[:200]
        save_configs()
    except Exception:
        pass

def _wait_profile_lock_release(profile_name, max_wait=6.0):
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        if _profile_browser_process_count(profile_name) == 0:
            return True
        time.sleep(0.4)
    return _profile_browser_process_count(profile_name) == 0

def _find_profile_with_data_dir(profile_path, exclude_name=None):
    target = normalize_profile_path(profile_path)
    if not target:
        return None
    for other_name, other in profiles.items():
        if other_name == exclude_name:
            continue
        other_path = normalize_profile_path(browser_glue.active_profile_path(other.get('config', {})))
        if other_path == target:
            return other_name
    return None


def _find_profile_with_same_data_dir(profile_name):
    profile = profiles.get(profile_name, {})
    profile_path = browser_glue.active_profile_path(profile.get('config', {}))
    return _find_profile_with_data_dir(profile_path, exclude_name=profile_name)


def _profile_conflicts():
    """Global shared-profile conflicts across all accounts (read-only)."""
    try:
        inventory = build_profile_inventory(profiles)
        return detect_profile_conflicts(inventory)
    except Exception:
        return []


def _profile_conflict_blocked_names():
    return conflict_account_names(_profile_conflicts())


def _blocked_by_profile_conflict(profile_name):
    return profile_name in _profile_conflict_blocked_names()


def _profile_conflict_message(profile_name):
    for conflict in _profile_conflicts():
        if profile_name in conflict.get('names', []):
            names = ", ".join(conflict.get('names', []))
            return f"Profile đang bị nhiều tài khoản dùng chung: {names}. Hãy tách profile trước."
    return "Profile đang xung đột ownership; không thể mở."


def _profile_browser_process_count(profile_name):
    profile_path = browser_glue.active_profile_path(profiles.get(profile_name, {}).get('config', {}))
    count = 0
    try:
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                name = str(proc.info.get('name') or '').lower()
                if name in ('chrome.exe', 'chrome') and process_uses_profile(
                    proc.info.get('cmdline'), profile_path
                ):
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass
    return count


def _export_live_cookies_to_config(driver, profile_name):
    if not _browser_session_valid(driver):
        return False
    try:
        live_cookies = browser_glue.export_cookies(driver)
        if not browser_glue.has_primary_tiktok_auth_cookie(live_cookies):
            update_status(
                f"[{profile_name}] [WARN] Không lưu cookie live: thiếu cookie đăng nhập TikTok."
            )
            return False
        try:
            login_state = browser_glue.page_login_state(driver, timeout=15)
        except Exception:
            login_state = 'indeterminate'
        if login_state != 'authenticated':
            update_status(
                f"[{profile_name}] [WARN] Không lưu cookie live: browser chưa xác nhận trạng thái đăng nhập."
            )
            return False
        cookie_json = json.dumps(live_cookies, ensure_ascii=False)
        profiles[profile_name]['config']['cookie_str'] = cookie_json
        _save_cookie_injection_metadata(profile_name, cookie_json)
        save_configs()
        return True
    except Exception:
        pass
    return False


def _capture_tiktok_cookies_worker(profile_name, source_label='profile_session', auto_after_manual=False):
    cfg = profiles[profile_name]['config']
    token = None
    try:
        if auto_after_manual:
            _wait_profile_lock_release(profile_name)
        proxy_data = parse_proxy_string(cfg.get('proxy_string', '')) if cfg.get('use_proxy', False) else None
        if cfg.get('use_proxy', False) and not proxy_data:
            raise ValueError("Proxy sai định dạng; từ chối mở browser trực tiếp")
        proxy_expected_ip = proxy_data['ip'] if proxy_data else None
        direct_ip = None
        geo_changed = _refresh_profile_geoip(profile_name, cfg, proxy_data)
        if geo_changed:
            save_configs()
        if proxy_data:
            preflight = _proxy_endpoint_preflight(profile_name, proxy_data)
            if not preflight['proxy_exit_ip']:
                raise RuntimeError("Không xác minh được proxy endpoint khi lấy cookie")
            proxy_expected_ip = preflight['proxy_exit_ip']
            direct_ip = preflight['direct_ip']
        browser_glue.ensure_patchright_profile(cfg)
        _sync_patchright_migration(cfg)
        save_configs()
        token = browser_glue.open_session(cfg, profile_name)
        if proxy_data:
            is_match, current_ip = browser_glue.verify_exit_ip(token, proxy_expected_ip)
            if not is_match and direct_ip and current_ip and current_ip != direct_ip:
                is_match = True
            if not is_match:
                if not current_ip:
                    raise RuntimeError("Không xác minh được proxy trong browser khi lấy cookie")
                raise RuntimeError(f"Proxy mismatch khi lấy cookie: {current_ip} != {proxy_expected_ip}")
        browser_glue.navigate(token, TIKTOK_UPLOAD_URL)
        login_state = browser_glue.wait_page_login_state(token, timeout=30)
        if login_state != 'authenticated':
            _mark_session_failure(profile_name, 'Không xác minh được session trên profile')
            _set_profile_ui(profile_name, login='Cần đăng nhập lại', last_error='Profile chưa đăng nhập; hãy bấm Mở Chrome để đăng nhập tay')
            update_status(f"[{profile_name}] Profile chưa đăng nhập. Hãy bấm 'Mở Chrome' để đăng nhập thủ công rồi đóng lại.")
            return False
        _advance_patchright_migration(cfg, MigrationState.CREATED.value, MigrationState.COOKIES_IMPORTED)
        _advance_patchright_migration(cfg, MigrationState.COOKIES_IMPORTED.value, MigrationState.LOGIN_VERIFIED)
        live_cookies = browser_glue.export_cookies(token)
        if not browser_glue.has_primary_tiktok_auth_cookie(live_cookies):
            raise TikTokLoginRequiredError('TikTok không trả về cookie đăng nhập nào sau khi xác minh.')
        cookie_json = json.dumps(live_cookies, ensure_ascii=False)
        cfg['cookie_str'] = cookie_json
        _save_cookie_injection_metadata(profile_name, cookie_json)
        cfg['cookies_last_captured_at'] = datetime.now(timezone.utc).isoformat()
        _save_session_auth_metadata(profile_name, 'verified', source_label)
        save_configs()
        _set_profile_ui(profile_name, login='Session đã lưu', browser='Đã đóng', last_error='')
        update_status(f"[{profile_name}] Đã lưu {len(live_cookies)} cookie TikTok từ session profile (nguồn: {source_label}).")
        return True
    except TikTokLoginRequiredError as e:
        _mark_session_failure(profile_name, str(e))
        _set_profile_ui(profile_name, login='Cần đăng nhập lại', last_error=str(e))
        update_status(f"[{profile_name}] Không lấy cookie: {e}")
        return False
    except Exception as e:
        _set_profile_ui(profile_name, last_error=str(e))
        update_status(f"[{profile_name}] Lỗi lấy cookie TikTok: {e}")
        return False
    finally:
        if token:
            token.quit()
        if profile_name in profiles:
            _set_profile_ui(profile_name, browser='Đã đóng')


def get_tiktok_cookies():
    if not _license_guard():
        return
    sel = tree.selection()
    if not sel:
        messagebox.showwarning('Lấy Cookie', 'Hãy chọn một profile.')
        return
    profile_name = tree.item(sel[0])['values'][0]
    profile = profiles.get(profile_name)
    if not profile:
        return
    if _blocked_by_profile_conflict(profile_name):
        messagebox.showerror('Lấy Cookie', _profile_conflict_message(profile_name))
        return
    if profile.get('running') or profile.get('uploading'):
        messagebox.showwarning('Lấy Cookie', 'Hãy Stop profile và đóng browser trước khi lấy cookie.')
        return
    if _browser_session_valid(profile.get('manual_driver')):
        messagebox.showwarning('Lấy Cookie', 'Hãy đóng cửa sổ browser thủ công trước khi lấy cookie.')
        return
    if _profile_browser_process_count(profile_name) > 0:
        messagebox.showwarning('Lấy Cookie', 'User Data vẫn đang được browser sử dụng. Hãy đóng browser rồi thử lại.')
        return

    def worker():
        with _profile_operation_lock(profile_name):
            profile['session_busy'] = True
            try:
                _set_profile_ui(profile_name, browser='Đang kiểm tra', login='Đang kiểm tra')
                _capture_tiktok_cookies_worker(profile_name)
            finally:
                profile['session_busy'] = False

    update_status(f'[{profile_name}] Đang mở ngầm User Data để lấy cookie TikTok...')
    threading.Thread(target=worker, daemon=True).start()


# =========================
# Cookie Live Check (Batch)
# =========================
def _cookie_check_preflight_reason(name):
    profile = profiles.get(name)
    if profile is None:
        return "Không tồn tại"
    if _blocked_by_profile_conflict(name):
        return _profile_conflict_message(name)
    snapshot = _build_profile_snapshot(name, profile)
    if not snapshot.can_check_cookie:
        return snapshot.blocking_reason or "Đang bận"
    return None


def _commit_cookie_update(name, cfg, live_cookies, source_label):
    """Persist a live cookie capture transactionally.

    Returns True only when the config write (cookie + session metadata) fully
    succeeded. Never claims ``kept_cookies`` on a partial/failed save.
    """
    try:
        cookie_json = json.dumps(live_cookies, ensure_ascii=False)
        cfg['cookie_str'] = cookie_json
        _save_cookie_injection_metadata(name, cookie_json)
        cfg['cookies_last_captured_at'] = datetime.now(timezone.utc).isoformat()
        _save_session_auth_metadata(name, 'verified', source_label)
        cfg['manual_login_pending'] = False
        save_configs()
        return True
    except Exception:
        return False


def _check_profile_cookie_live(name, claim_id=None, cancel_event=None, mode="BROWSER_FULL"):
    """Check live status for one account and keep a live fallback cookie.

    If mode == "HTTP_FAST": uses check_cookie_fast_http() via Webcast/Passport API with proxy.
    If mode == "BROWSER_FULL": claims the operation slot, opens verified anti-detect browser session.
    """
    profile = profiles.get(name)
    if profile is None:
        return CookieCheckResult(profile_name=name, state=CookieCheckState.SKIPPED, detail="Không tồn tại")
    cfg = profile['config']
    uuid = ensure_account_uuid(cfg)
    checked_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if mode == "HTTP_FAST":
        cookie_raw = cfg.get('cookie_str', '')
        state, detail, auth_names = check_cookie_fast_http(cookie_raw, proxy_cfg=cfg, timeout=8.0)
        return CookieCheckResult(
            account_uuid=uuid,
            profile_name=name,
            state=state,
            source=CookieSource.SAVED_COOKIE,
            mode=CookieCheckMode.HTTP_FAST,
            auth_cookie_names=auth_names,
            checked_at=checked_at,
            detail=detail,
            kept_cookies=(state == CookieCheckState.LIVE),
        )

    token = None
    owned = False
    try:
        if claim_id is None:
            try:
                claim_id = _claim_profile_operation(
                    name, OperationState.CHECKING_COOKIE,
                    preflight_fn=_cookie_check_preflight_reason,
                )
            except OperationClaimError as error:
                state = CookieCheckState.PROFILE_BUSY if error.profile_busy else CookieCheckState.SKIPPED
                return CookieCheckResult(account_uuid=uuid, profile_name=name, state=state, checked_at=checked_at, detail=mask_detail(error.reason))

        try:
            proxy_data, preflight = session_runner.resolve_proxy(cfg)
            browser_glue.ensure_patchright_profile(cfg)
            _sync_patchright_migration(cfg)
            token = browser_glue.open_session(cfg, name)
            owned = True
            if proxy_data:
                is_match, _current_ip, detail = session_runner.verify_browser_proxy(token, proxy_data, preflight)
                if not is_match:
                    return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.PROXY_ERROR, checked_at=checked_at, detail=mask_detail(detail))
        except session_runner.SessionRunnerError as error:
            state = CookieCheckState.PROXY_ERROR if error.kind == 'proxy' else CookieCheckState.BROWSER_ERROR
            return CookieCheckResult(account_uuid=uuid, profile_name=name, state=state, checked_at=checked_at, detail=mask_detail(error.reason))
        except Exception as error:
            return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.BROWSER_ERROR, checked_at=checked_at, detail=mask_detail(f"Lỗi mở browser: {type(error).__name__}"))

        if cancel_event is not None and cancel_event.is_set():
            return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.CANCELLED, checked_at=checked_at, detail='Đã dừng bởi người dùng')

        browser_glue.navigate(token, TIKTOK_UPLOAD_URL)
        login_state = browser_glue.wait_page_login_state(token, timeout=30)
        if login_state == 'authenticated':
            live_cookies = browser_glue.export_cookies(token)
            auth_names = primary_auth_cookie_names(live_cookies)
            if auth_names:
                if not _commit_cookie_update(name, cfg, live_cookies, 'cookie_check_profile_session'):
                    return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.PERSIST_ERROR, checked_at=checked_at, detail='Lưu cookie mới thất bại; profile giữ cookie cũ')
                return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.LIVE, source=CookieSource.PROFILE_SESSION, auth_cookie_names=auth_names, checked_at=checked_at, detail='Session profile đang đăng nhập', kept_cookies=True)

        saved_cookies = parse_cookie(cfg.get('cookie_str', ''))
        auth_names = primary_auth_cookie_names(saved_cookies)
        if not saved_cookies or not auth_names:
            return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.DEAD, source=CookieSource.SAVED_COOKIE, checked_at=checked_at, detail='Không có cookie đăng nhập TikTok' if not saved_cookies else 'Cookie lưu thiếu cookie đăng nhập TikTok')

        old_cookies = browser_glue.export_cookies(token)
        try:
            browser_glue.import_cookies(token, saved_cookies)
            browser_glue.navigate(token, TIKTOK_UPLOAD_URL)
            fallback_state = browser_glue.wait_page_login_state(token, timeout=30)
        except Exception as error:
            try:
                browser_glue.import_cookies(token, old_cookies)
            except Exception:
                pass
            return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.UNKNOWN, checked_at=checked_at, detail=mask_detail(f"Chromium từ chối cookie lưu: {type(error).__name__}"))

        if fallback_state == 'authenticated':
            live_cookies = browser_glue.export_cookies(token)
            if not _commit_cookie_update(name, cfg, live_cookies, 'cookie_check_saved_cookie'):
                try:
                    browser_glue.import_cookies(token, old_cookies)
                except Exception:
                    pass
                return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.PERSIST_ERROR, checked_at=checked_at, detail='Lưu cookie mới thất bại; profile giữ cookie cũ')
            _advance_patchright_migration(cfg, MigrationState.CREATED.value, MigrationState.COOKIES_IMPORTED)
            _advance_patchright_migration(cfg, MigrationState.COOKIES_IMPORTED.value, MigrationState.LOGIN_VERIFIED)
            return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.LIVE, source=CookieSource.SAVED_COOKIE, auth_cookie_names=primary_auth_cookie_names(live_cookies), checked_at=checked_at, detail='Cookie lưu vẫn live; đã giữ trong profile', kept_cookies=True)

        try:
            browser_glue.import_cookies(token, old_cookies)
        except Exception:
            pass
        if fallback_state == 'login_required':
            _mark_session_failure(name, 'Cookie dự phòng bị TikTok từ chối')
            return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.DEAD, source=CookieSource.SAVED_COOKIE, auth_cookie_names=auth_names, checked_at=checked_at, detail='Cookie hết hạn hoặc bị TikTok từ chối')
        return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.UNKNOWN, source=CookieSource.SAVED_COOKIE, auth_cookie_names=auth_names, checked_at=checked_at, detail='Captcha/checkpoint/timeout; chưa kết luận')
    except session_runner.SessionCancelled as error:
        return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.CANCELLED, checked_at=checked_at, detail=mask_detail(str(error)))
    except TikTokLoginRequiredError as error:
        _mark_session_failure(name, str(error))
        return CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.DEAD, checked_at=checked_at, detail=mask_detail(str(error)))
    except Exception as error:
        detail = mask_detail(f"Lỗi kiểm tra: {type(error).__name__}")
        state = CookieCheckState.PROXY_ERROR if 'proxy' in str(error).lower() else CookieCheckState.UNKNOWN
        return CookieCheckResult(account_uuid=uuid, profile_name=name, state=state, checked_at=checked_at, detail=detail)
    finally:
        close_confirmed = True
        if owned and token is not None:
            close_confirmed = session_runner.close_token_confirmed(token)
        if claim_id is not None:
            _release_profile_operation(name, claim_id, close_confirmed=close_confirmed)


class CookieCheckDialog:
    """Batch live-cookie dialog. Supports Fast HTTP check, Full browser check, and parallel workers."""

    def __init__(self, targets):
        self.targets = targets
        self.results = {}
        self.running = False
        self.cancel_requested = False
        self.cancel_event = threading.Event()
        self.dialog = ctk.CTkToplevel(root)
        self.dialog.title("Kiểm tra Live Cookie")
        fit_and_center_dialog(self.dialog, 960, 560, parent=root, min_w=760, min_h=400)
        self.dialog.resizable(True, True)
        self.dialog.transient(root)
        self.dialog.configure(fg_color=UIThemeTokens.BG_ROOT)

        self.mode_var = ctk.StringVar(value="HTTP_FAST")
        self.workers_var = ctk.StringVar(value="3 luồng")
        self.summary_var = ctk.StringVar(value=f"Đã chọn: {len(targets)} hồ sơ")

        self._build_ui()
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)
        _cookie_check_batch['active'] = True

    def _build_ui(self):
        container = ctk.CTkFrame(self.dialog, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=14, pady=12)

        # 1. Header Card
        header_card = ctk.CTkFrame(container, fg_color=UIThemeTokens.BG_CARD, corner_radius=8, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        header_card.pack(fill="x", pady=(0, 8))

        h_inner = ctk.CTkFrame(header_card, fg_color="transparent")
        h_inner.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(
            h_inner,
            text="🍪 Kiểm Tra Trạng Thái Cookie (Batch Live Check)",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            h_inner,
            text="Kiểm tra trạng thái đăng nhập TikTok siêu tốc qua HTTP API hoặc mô phỏng đầy đủ qua Trình duyệt.",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        # 2. Options Control Bar
        opt_card = ctk.CTkFrame(container, fg_color=UIThemeTokens.BG_CARD, corner_radius=8, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        opt_card.pack(fill="x", pady=(0, 8))

        opt_inner = ctk.CTkFrame(opt_card, fg_color="transparent")
        opt_inner.pack(fill="x", padx=14, pady=8)

        # Mode radio buttons
        ctk.CTkLabel(opt_inner, text="Chế độ:", font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 8))
        
        self.radio_fast = ctk.CTkRadioButton(
            opt_inner,
            text="⚡ Nhanh (HTTP API ~0.3s/acc)",
            variable=self.mode_var,
            value="HTTP_FAST",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            command=self._on_mode_change,
        )
        self.radio_fast.pack(side="left", padx=(0, 14))

        self.radio_full = ctk.CTkRadioButton(
            opt_inner,
            text="🌐 Đầy đủ (Trình duyệt Anti-detect)",
            variable=self.mode_var,
            value="BROWSER_FULL",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            command=self._on_mode_change,
        )
        self.radio_full.pack(side="left", padx=(0, 20))

        # Workers ComboBox
        ctk.CTkLabel(opt_inner, text="Số luồng:", font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 6))
        self.workers_combo = ctk.CTkComboBox(
            opt_inner,
            values=["1 luồng", "2 luồng", "3 luồng", "5 luồng"],
            variable=self.workers_var,
            width=100,
            height=28,
            font=UIThemeTokens.FONT_BODY,
        )
        self.workers_combo.pack(side="left")

        # Summary text right
        ctk.CTkLabel(opt_inner, textvariable=self.summary_var, font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.ACCENT_PRIMARY).pack(side="right")

        # 3. Table Frame
        table_frame = ctk.CTkFrame(container, fg_color=UIThemeTokens.BG_CARD, corner_radius=8, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        table_frame.pack(fill="both", expand=True, pady=(0, 8))

        columns = ('name', 'tiktok', 'mode', 'state', 'source', 'auth', 'time', 'detail')
        self.table = ttk.Treeview(table_frame, columns=columns, show='headings', height=11)
        for col, text, width in (
            ('name', 'Tài khoản', 140),
            ('tiktok', 'TikTok ID', 100),
            ('mode', 'Chế độ', 80),
            ('state', 'Trạng thái', 100),
            ('source', 'Nguồn', 100),
            ('auth', 'Cookie auth', 110),
            ('time', 'Kiểm tra lúc', 130),
            ('detail', 'Chi tiết', 220),
        ):
            self.table.heading(col, text=text)
            self.table.column(col, width=width, anchor='w' if col in ('name', 'detail') else 'center')
        self.table.pack(fill='both', expand=True, padx=6, pady=6)

        for name, uuid in self.targets:
            self.table.insert('', 'end', iid=uuid, values=(name, '', '', 'Chờ', '', '', '', ''))

        # 4. Action Buttons Frame
        btn_frame = ctk.CTkFrame(container, fg_color="transparent")
        btn_frame.pack(fill="x")

        self.start_btn = ctk.CTkButton(
            btn_frame,
            text="▶ Bắt đầu kiểm tra",
            width=140,
            height=32,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._start,
        )
        self.start_btn.pack(side='left')

        self.stop_btn = ctk.CTkButton(
            btn_frame,
            text="⏹ Dừng",
            width=80,
            height=32,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.STATUS_ERROR,
            hover_color="#b91c1c",
            command=self._request_cancel,
        )
        self.stop_btn.pack(side='left', padx=6)

        self.retry_btn = ctk.CTkButton(
            btn_frame,
            text="🔄 Kiểm tra lại lỗi",
            width=130,
            height=32,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color="#475569",
            hover_color="#334155",
            command=self._retry_failed,
        )
        self.retry_btn.pack(side='left')

        self.close_btn = ctk.CTkButton(
            btn_frame,
            text="Đóng",
            width=80,
            height=32,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color="#64748b",
            hover_color="#475569",
            command=self._on_close,
        )
        self.close_btn.pack(side='right')

    def _on_mode_change(self):
        if self.mode_var.get() == "BROWSER_FULL":
            self.workers_var.set("1 luồng")

    def _start(self):
        if self.running:
            return
        self.running = True
        self.cancel_requested = False
        self.cancel_event = threading.Event()
        self.start_btn.configure(state='disabled')
        self.retry_btn.configure(state='disabled')
        self.radio_fast.configure(state='disabled')
        self.radio_full.configure(state='disabled')
        self.workers_combo.configure(state='disabled')
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        raw_workers = self.workers_var.get() or "1"
        try:
            max_workers = int(raw_workers.split()[0])
        except Exception:
            max_workers = 1
        check_mode = self.mode_var.get() or "HTTP_FAST"

        def _execute_single(target_tuple):
            name, uuid = target_tuple
            if self.cancel_requested or self.cancel_event.is_set():
                if uuid not in self.results:
                    res = CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.CANCELLED, detail='Đã dừng bởi người dùng')
                    self.results[uuid] = res
                    self._update_row(uuid, res)
                return
            
            res_checking = CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.CHECKING, detail='Đang kiểm tra...')
            self.results[uuid] = res_checking
            self._update_row(uuid, res_checking)

            profile = profiles.get(name)
            if profile is None:
                res_skip = CookieCheckResult(account_uuid=uuid, profile_name=name, state=CookieCheckState.SKIPPED, detail='Không tồn tại')
                self.results[uuid] = res_skip
                self._update_row(uuid, res_skip)
                return

            res_final = _check_profile_cookie_live(name, cancel_event=self.cancel_event, mode=check_mode)
            self.results[uuid] = res_final
            self._update_row(uuid, res_final)

        try:
            if max_workers <= 1 or check_mode == "BROWSER_FULL":
                for target in self.targets:
                    if self.cancel_requested:
                        break
                    _execute_single(target)
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(_execute_single, t) for t in self.targets]
                    for f in as_completed(futures):
                        if self.cancel_requested:
                            break
            self._finish()
        except Exception:
            self._finish()

    def _update_row(self, uuid, result):
        try:
            cfg = profiles.get(result.profile_name, {}).get('config', {}) or {}
            tiktok_id = str(cfg.get('tiktok_id', '') or '').lstrip('@')
        except Exception:
            tiktok_id = ''
        try:
            root.after(0, lambda: self._apply_row(uuid, result, tiktok_id))
        except Exception:
            pass

    def _apply_row(self, uuid, result, tiktok_id):
        try:
            if not self.table.winfo_exists():
                return
            mode_str = "⚡ HTTP" if getattr(result, 'mode', None) == CookieCheckMode.HTTP_FAST else "🌐 Browser"
            st_text = result.display_state()
            if result.state == CookieCheckState.LIVE:
                st_text = "🟢 Live"
            elif result.state == CookieCheckState.DEAD:
                st_text = "🔴 Die"
            elif result.state == CookieCheckState.PROXY_ERROR:
                st_text = "🟡 Lỗi proxy"
            elif result.state == CookieCheckState.CHECKING:
                st_text = "⏳ Đang check..."

            values = (
                result.profile_name,
                tiktok_id,
                mode_str,
                st_text,
                result.display_source(),
                ", ".join(result.auth_cookie_names),
                result.checked_at,
                mask_detail(result.detail),
            )
            if uuid in self.table.get_children(''):
                self.table.item(uuid, values=values)
            else:
                self.table.insert('', 'end', iid=uuid, values=values)

            counts = build_summary(list(self.results.values()))
            self.summary_var.set(
                f"Đã chọn: {counts['total']} | 🟢 Live: {counts['live']} | 🔴 Die: {counts['dead']} "
                f"| 🟡 Lỗi proxy: {counts['proxy_error']} | ⏳ Đang check: {counts['checking']}"
            )
        except Exception:
            pass

    def _finish(self):
        self.running = False
        try:
            self.start_btn.configure(state='normal')
            self.retry_btn.configure(state='normal')
            self.radio_fast.configure(state='normal')
            self.radio_full.configure(state='normal')
            self.workers_combo.configure(state='normal')
        except Exception:
            pass

    def _request_cancel(self):
        self.cancel_requested = True
        self.cancel_event.set()
        update_status("Đã yêu cầu dừng kiểm tra cookie.")

    def _retry_failed(self):
        failed = [
            (result.profile_name, uuid)
            for uuid, result in self.results.items()
            if result.state in (
                CookieCheckState.DEAD,
                CookieCheckState.UNKNOWN,
                CookieCheckState.PROXY_ERROR,
                CookieCheckState.PROFILE_BUSY,
                CookieCheckState.BROWSER_ERROR,
                CookieCheckState.PERSIST_ERROR,
            )
        ]
        if not failed:
            messagebox.showinfo("Kiểm tra Cookie", "Không có kết quả lỗi để kiểm tra lại.")
            return
        for _, uuid in failed:
            try:
                self.table.delete(uuid)
            except Exception:
                pass
        self.targets = failed
        self.results = {}
        self._start()

    def _on_close(self):
        self.cancel_requested = True
        self.cancel_event.set()
        _cookie_check_batch['active'] = False
        try:
            self.dialog.destroy()
        except Exception:
            pass


def check_cookie_live():
    if not _license_guard():
        return
    if _cookie_check_batch['active']:
        messagebox.showinfo("Kiểm tra Cookie", "Một đợt kiểm tra cookie khác đang chạy.")
        return
    sel = tree.selection()
    if not sel:
        messagebox.showwarning("Kiểm tra Cookie", "Hãy chọn ít nhất 1 hồ sơ.")
        return
    targets = []
    for iid in sel:
        name = tree.item(iid, 'values')[0]
        profile = profiles.get(name)
        if profile is None:
            continue
        uuid = ensure_account_uuid(profile['config'])
        targets.append((name, uuid))
    if not targets:
        messagebox.showwarning("Kiểm tra Cookie", "Không có hồ sơ hợp lệ trong lựa chọn.")
        return
    CookieCheckDialog(targets)


def _inspection_preflight_reason(name):
    profile = profiles.get(name)
    if profile is None:
        return "Không tồn tại"
    if _blocked_by_profile_conflict(name):
        return _profile_conflict_message(name)
    if _browser_session_valid(profile.get('manual_driver')):
        return "Browser thủ công đang mở"
    snapshot = _build_profile_snapshot(name, profile)
    if not snapshot.can_check_cookie:
        return snapshot.blocking_reason or "Đang bận"
    return None


def _persist_inspection_snapshot(name, result):
    """Store a normalized inspection snapshot in the profile config.

    Persists every terminal result (including failures) so a stale success
    snapshot never survives a newer failed check. Returns the result with a
    warning appended when ``save_configs()`` itself fails.
    """
    profile = profiles.get(name)
    if profile is None:
        return result
    cfg = profile['config']
    snapshot = {
        'schema_version': 1,
        'state': result.state.value,
        'checked_at': result.checked_at,
        'detail': result.detail,
        'identity': {
            'numeric_user_id': result.identity.numeric_user_id,
            'unique_id': result.identity.unique_id,
            'nickname': result.identity.nickname,
            'region': result.identity.region,
            'verified': result.identity.verified,
            'account_status': result.identity.account_status,
        },
        'analytics': {
            'total_views': result.analytics.total_views,
            'views_30d': result.analytics.views_30d,
            'partial': result.analytics.partial,
        },
        'monetization': {
            'currency': result.monetization.currency,
            'balance_amount': result.monetization.balance_amount,
            'available_amount': result.monetization.available_amount,
            'pending_amount': result.monetization.pending_amount,
        },
        'programs': [
            {
                'program_id': program.program_id,
                'name': program.name,
                'status': program.status.value,
                'eligible': program.eligible,
                'enrolled': program.enrolled,
                'linked': program.linked,
            }
            for program in result.programs
        ],
        'payout': {
            'payout_linked': result.payout.payout_linked,
            'provider': result.payout.provider,
            'masked_identifier': result.payout.masked_identifier,
            'verification_status': result.payout.verification_status,
            'payout_status': result.payout.payout_status,
        },
        'capabilities': [
            {
                'capability': capability.capability,
                'state': capability.state.value,
                'endpoint_id': capability.endpoint_id,
                'checked_at': capability.checked_at,
                'schema_hash': capability.schema_hash,
                'warnings': list(capability.warnings),
            }
            for capability in result.capabilities.results
        ],
        'sources': [
            {
                'path': source.path,
                'status': source.status,
                'content_type': source.content_type,
                'group': source.group,
                'payload_keys': list(source.payload_keys or ()),
                'safe_get': source.safe_get,
                'error': source.error,
            }
            for source in result.sources
        ],
        'warnings': list(result.warnings),
        'classification': classify_account(result),
    }
    cfg['tiktok_inspection'] = snapshot
    repository_warning = ''
    try:
        account_uuid = ensure_account_uuid(cfg)
        InspectionRepository().save_capabilities(
            account_uuid,
            name,
            result.capabilities,
            state=result.state.value,
        )
    except Exception as error:
        repository_warning = mask_detail(
            f"Không lưu được SQLite Insights: {type(error).__name__}"
        )
    try:
        save_configs()
    except Exception as error:
        detail = mask_detail(f"Không lưu được snapshot kiểm tra: {type(error).__name__}")
        return AccountInspectionResult(
            profile_name=result.profile_name,
            state=result.state,
            checked_at=result.checked_at,
            identity=result.identity,
            analytics=result.analytics,
            monetization=result.monetization,
            programs=result.programs,
            payout=result.payout,
            capabilities=result.capabilities,
            sources=result.sources,
            warnings=tuple(list(result.warnings) + ([repository_warning] if repository_warning else []) + [detail]),
            classification=result.classification,
            detail=result.detail,
        )
    if repository_warning:
        return AccountInspectionResult(
            profile_name=result.profile_name,
            state=result.state,
            checked_at=result.checked_at,
            identity=result.identity,
            analytics=result.analytics,
            monetization=result.monetization,
            programs=result.programs,
            payout=result.payout,
            capabilities=result.capabilities,
            sources=result.sources,
            warnings=tuple(list(result.warnings) + [repository_warning]),
            classification=result.classification,
            detail=result.detail,
        )
    return result


def _inspect_tiktok_account_worker(name, claim_id=None, cancel_event=None):
    """Inspect one account using Fast HTTP (TikTokMonetizationClient) + auto-sync to config."""
    profile = profiles.get(name)
    if profile is None:
        return {
            "status": "ERROR",
            "profile_name": name,
            "error": "Profile không tồn tại",
            "checked_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
    cfg = profile.get('config', {})
    checked_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if cancel_event is not None and cancel_event.is_set():
        return {
            "status": "CANCELLED",
            "profile_name": name,
            "error": "Đã dừng bởi người dùng",
            "checked_at": checked_at,
        }

    try:
        from tiktok_monetization_client import TikTokMonetizationClient
        client = TikTokMonetizationClient(name, cfg, timeout=12.0)
        data = client.fetch_all_monetization_data()

        # Auto-sync extracted UID & @username into profile config
        if data.get("unique_id"):
            cfg['tiktok_id'] = data['unique_id']
            cfg['tiktok_account'] = data['unique_id']
        if data.get("tiktok_user_id"):
            cfg['tiktok_user_id'] = str(data['tiktok_user_id'])

        # Auto-sync auth state
        if data.get("status") == "SUCCESS":
            cfg['session_auth_state'] = 'verified'
        elif data.get("status") in ("COOKIE_EXPIRED", "NO_AUTH"):
            cfg['session_auth_state'] = 'expired'

        # Update monetization cache
        monetization_cache[name] = data
        try:
            _save_monetization_cache()
        except Exception:
            pass
        try:
            save_configs()
        except Exception:
            pass
        try:
            request_profile_refresh()
        except Exception:
            pass

        return data
    except Exception as e:
        return {
            "status": "ERROR",
            "profile_name": name,
            "error": str(e),
            "checked_at": checked_at,
        }


class InspectionDialog:
    """Single/batch Fast HTTP TikTok account inspection dialog (Clean SaaS Light)."""

    def __init__(self, targets):
        self.targets = targets
        self.results = {}
        self.running = False
        self.cancel_event = threading.Event()
        self.dialog = ctk.CTkToplevel(root)
        self.dialog.title("Kiểm Tra Thông Tin TikTok — Fast HTTP Engine")
        fit_and_center_dialog(self.dialog, 1200, 660, parent=root, min_w=850, min_h=460)
        self.dialog.configure(fg_color=UIThemeTokens.BG_ROOT)
        self.dialog.transient(root)
        self.summary_var = ctk.StringVar(value=f"Đã chọn: {len(targets)} hồ sơ")
        self.concurrency_var = ctk.StringVar(value="2 luồng")
        self._build_ui()
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)
        _inspection_batch['active'] = True

    def _build_ui(self):
        # Header / Control bar
        top_bar = ctk.CTkFrame(self.dialog, fg_color=UIThemeTokens.BG_CARD, corner_radius=10, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        top_bar.pack(fill='x', padx=14, pady=(12, 8))

        lbl_sum = ctk.CTkLabel(top_bar, textvariable=self.summary_var, font=("Segoe UI", 13, "bold"), text_color=UIThemeTokens.TEXT_PRIMARY)
        lbl_sum.pack(side='left', padx=14, pady=8)

        ctk.CTkLabel(top_bar, text="Số luồng:", font=("Segoe UI", 12), text_color=UIThemeTokens.TEXT_MUTED).pack(side='left', padx=(16, 4))
        self.concurrency_menu = ctk.CTkOptionMenu(
            top_bar,
            values=["1 luồng", "2 luồng", "3 luồng", "5 luồng"],
            variable=self.concurrency_var,
            width=100,
            height=30,
            fg_color=UIThemeTokens.BG_HOVER,
            button_color=UIThemeTokens.ACCENT_PRIMARY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.concurrency_menu.pack(side='left', padx=(0, 12))

        self.start_btn = ctk.CTkButton(
            top_bar,
            text="⚡ Bắt Đầu Kiểm Tra",
            width=150,
            height=32,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            text_color="#ffffff",
            font=("Segoe UI", 12, "bold"),
            command=self._start,
        )
        self.start_btn.pack(side='right', padx=12, pady=8)

        self.stop_btn = ctk.CTkButton(
            top_bar,
            text="⏹ Dừng",
            width=85,
            height=32,
            fg_color=UIThemeTokens.BG_HOVER,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            command=self._request_cancel,
            state='disabled',
        )
        self.stop_btn.pack(side='right', padx=4, pady=8)

        # Table frame
        frame = ctk.CTkFrame(self.dialog, fg_color=UIThemeTokens.BG_CARD, corner_radius=10, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        frame.pack(fill='both', expand=True, padx=14, pady=(0, 8))

        columns = ('name', 'tiktok', 'uid', 'state', 'followers', 'likes', 'videos', 'balance', 'crp', 'kyc', 'tax', 'payment', 'time')
        self.table = ttk.Treeview(frame, columns=columns, show='headings', height=12)
        
        headers_def = [
            ('name', 'Tên Hồ Sơ', 130, 'w'),
            ('tiktok', 'TikTok ID', 125, 'center'),
            ('uid', 'TikTok UID', 140, 'center'),
            ('state', 'Trạng Thái', 95, 'center'),
            ('followers', 'Follower', 85, 'center'),
            ('likes', 'Thích', 80, 'center'),
            ('videos', 'Video', 70, 'center'),
            ('balance', 'Số Dư', 85, 'center'),
            ('crp', 'Kiếm Tiền', 110, 'center'),
            ('kyc', 'KYC', 85, 'center'),
            ('tax', 'Thuế', 80, 'center'),
            ('payment', 'PTTT', 130, 'center'),
            ('time', 'Cập Nhật', 120, 'center'),
        ]
        for col, text, width, anchor in headers_def:
            self.table.heading(col, text=text)
            self.table.column(col, width=width, anchor=anchor)

        self.table.pack(fill='both', expand=True, padx=4, pady=4)
        for name in self.targets:
            cfg = profiles.get(name, {}).get('config', {}) or {}
            cur_uid = str(cfg.get('tiktok_user_id', '') or '')
            cur_user = str(cfg.get('tiktok_id', '') or cfg.get('tiktok_account', '') or '')
            self.table.insert('', 'end', iid=name, values=(name, f"@{cur_user.lstrip('@')}" if cur_user else '', cur_uid, '⚪ Chờ check') + ('',) * 9)

        self.table.bind('<<TreeviewSelect>>', lambda _e: self._show_detail())

        # Detail box
        detail_frame = ctk.CTkFrame(self.dialog, fg_color=UIThemeTokens.BG_CARD, corner_radius=10, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        detail_frame.pack(fill='x', padx=14, pady=(0, 8))
        self.detail_var = ctk.StringVar(value="👉 Chọn một dòng trong bảng trên để xem chi tiết thông tin tài khoản.")
        ctk.CTkLabel(detail_frame, textvariable=self.detail_var, anchor='w', justify='left', wraplength=1200, font=("Segoe UI", 11), text_color=UIThemeTokens.TEXT_PRIMARY).pack(fill='x', padx=12, pady=10)

        # Action bar
        btn_frame = ctk.CTkFrame(self.dialog, fg_color='transparent')
        btn_frame.pack(fill='x', padx=14, pady=(0, 12))

        self.btn_copy_uid = ctk.CTkButton(btn_frame, text="📋 Copy UID", width=110, height=30, fg_color=UIThemeTokens.BG_HOVER, text_color=UIThemeTokens.TEXT_PRIMARY, command=self._copy_selected_uid)
        self.btn_copy_uid.pack(side='left', padx=(0, 6))

        self.btn_copy_user = ctk.CTkButton(btn_frame, text="📋 Copy @Username", width=130, height=30, fg_color=UIThemeTokens.BG_HOVER, text_color=UIThemeTokens.TEXT_PRIMARY, command=self._copy_selected_username)
        self.btn_copy_user.pack(side='left', padx=6)

        self.btn_open_web = ctk.CTkButton(btn_frame, text="🌐 Mở Kênh TikTok", width=130, height=30, fg_color=UIThemeTokens.BG_HOVER, text_color=UIThemeTokens.TEXT_PRIMARY, command=self._open_selected_tiktok)
        self.btn_open_web.pack(side='left', padx=6)

        self.close_btn = ctk.CTkButton(btn_frame, text="Đóng", width=85, height=30, fg_color=UIThemeTokens.BG_HOVER, text_color=UIThemeTokens.TEXT_PRIMARY, command=self._on_close)
        self.close_btn.pack(side='right')

    def _start(self):
        if self.running:
            return
        self.running = True
        self.cancel_event = threading.Event()
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.concurrency_menu.configure(state='disabled')

        c_str = self.concurrency_var.get().split()[0]
        max_workers = int(c_str) if c_str.isdigit() else 2
        threading.Thread(target=self._worker_pool, args=(max_workers,), daemon=True).start()

    def _worker_pool(self, max_workers):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        completed = 0
        total = len(self.targets)

        def _check_single(name):
            if self.cancel_event.is_set():
                return name, {"status": "CANCELLED", "profile_name": name, "error": "Đã dừng"}
            self._update_row_status(name, "⏳ Đang check...")
            res = _inspect_tiktok_account_worker(name, cancel_event=self.cancel_event)
            return name, res

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_name = {executor.submit(_check_single, name): name for name in self.targets}
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    p_name, result = future.result()
                    self.results[p_name] = result
                    self._update_row_result(p_name, result)
                except Exception as e:
                    self.results[name] = {"status": "ERROR", "error": str(e)}
                    self._update_row_result(name, self.results[name])
                completed += 1
                self.summary_var.set(f"Tiến độ: {completed}/{total} | Live: {sum(1 for r in self.results.values() if r.get('status') == 'SUCCESS')} | Lỗi: {sum(1 for r in self.results.values() if r.get('status') in ('ERROR', 'COOKIE_EXPIRED', 'NO_AUTH'))}")

        self.running = False
        try:
            root.after(0, self._finish_inspection)
        except Exception:
            pass

    def _finish_inspection(self):
        try:
            self.start_btn.configure(state='normal')
            self.stop_btn.configure(state='disabled')
            self.concurrency_menu.configure(state='normal')
            update_status(f"Kiểm tra thông tin TikTok hoàn tất ({len(self.results)} hồ sơ).")
        except Exception:
            pass

    def _request_cancel(self):
        self.cancel_event.set()
        update_status("Đã yêu cầu dừng kiểm tra thông tin TikTok.")

    def _update_row_status(self, name, status_text):
        try:
            def _apply():
                if self.table.winfo_exists() and name in self.table.get_children(''):
                    cur = list(self.table.item(name, 'values'))
                    cur[3] = status_text
                    self.table.item(name, values=cur)
            root.after(0, _apply)
        except Exception:
            pass

    def _update_row_result(self, name, data):
        try:
            st = data.get("status", "")
            if st == "SUCCESS":
                badge_st = "🟢 Live"
            elif st in ("COOKIE_EXPIRED", "NO_AUTH"):
                badge_st = "🔴 Die"
            elif st == "CANCELLED":
                badge_st = "⚪ Đã dừng"
            else:
                badge_st = "🔴 Lỗi"

            user_str = f"@{data.get('unique_id', '').lstrip('@')}" if data.get('unique_id') else ""
            uid_str = str(data.get('tiktok_user_id', '') or '')
            f_cnt = data.get('follower_count') or data.get('crp_followers') or 0
            f_str = f"{f_cnt:,}" if f_cnt else "0"
            l_cnt = data.get('heart_count', 0) or 0
            l_str = f"{l_cnt:,}" if l_cnt else "0"
            v_cnt = data.get('video_count', 0) or 0
            v_str = f"{v_cnt:,}" if v_cnt else "0"

            bal = data.get('balance', 0.0) or 0.0
            sym = data.get('currency_symbol', '$')
            bal_str = f"{sym}{bal:.2f}"

            crp_str = data.get('crp_display', '⚪ Chưa bật')
            if data.get('kyc_status') == "APPROVED":
                kyc_str = "🟢 Đã KYC"
            elif data.get('kyc_status') == "PENDING":
                kyc_str = "🟡 Đang chờ"
            elif data.get('kyc_status') in ("RESUBMIT", "WARNING"):
                kyc_str = "🔴 Nộp lại"
            elif data.get('kyc_status') == "REJECTED":
                kyc_str = "🔴 Từ chối"
            else:
                kyc_str = "⚪ Chưa"
            tax_str = "🟢 Đã thuế" if data.get('tax_status') == "TAX_VERIFIED" else "⚪ Chưa"
            pay_str = data.get('payment_method', 'Chưa liên kết')
            time_str = data.get('checked_at', '')

            values = (name, user_str, uid_str, badge_st, f_str, l_str, v_str, bal_str, crp_str, kyc_str, tax_str, pay_str, time_str)

            def _apply():
                if self.table.winfo_exists():
                    if name in self.table.get_children(''):
                        self.table.item(name, values=values)
                    else:
                        self.table.insert('', 'end', iid=name, values=values)
            root.after(0, _apply)
        except Exception:
            pass

    def _show_detail(self):
        sel = self.table.selection()
        if not sel:
            return
        name = sel[0]
        data = self.results.get(name)
        if not data:
            return

        lines = [
            f"👤 Tài Khoản: {name}  |  Nickname: {data.get('nickname', 'N/A')}  |  Vùng: {data.get('region', 'N/A')}  |  UID: {data.get('tiktok_user_id', 'N/A')}  |  @{data.get('unique_id', 'N/A')}",
            f"📊 Thống Kê Kênh: Follower: {data.get('follower_count', 0):,}  |  Lượt Thích: {data.get('heart_count', 0):,}  |  Video: {data.get('video_count', 0):,}",
            f"💰 Tài Chính & Kiếm Tiền: Số dư: {data.get('currency_symbol','$')}{data.get('balance', 0.0):.2f}  |  CRP: {data.get('crp_display', 'N/A')}  |  RPM: {data.get('crp_rpm', 0.0):.2f}  |  Views Đạt Chuẩn: {data.get('crp_qualified_views', 0):,}",
            f"🛡️ Định Danh & Thanh Toán: KYC: {data.get('kyc_status', 'N/A')} ({data.get('kyc_full_name','')})  |  Thuế: {data.get('tax_status', 'N/A')}  |  PTTT: {data.get('payment_method', 'N/A')}",
        ]
        if data.get('errors'):
            lines.append(f"⚠️ Cảnh báo: {', '.join(str(e) for e in data['errors'])}")

        self.detail_var.set("\n".join(lines))

    def _copy_selected_uid(self):
        sel = self.table.selection()
        if not sel: return
        data = self.results.get(sel[0], {})
        uid = data.get('tiktok_user_id') or (profiles.get(sel[0], {}).get('config', {}) or {}).get('tiktok_user_id')
        if uid:
            root.clipboard_clear()
            root.clipboard_append(str(uid))
            toast_manager.enqueue(f"Đã copy UID: {uid}", level="info")
        else:
            toast_manager.enqueue("Chưa có thông tin UID", level="warning")

    def _copy_selected_username(self):
        sel = self.table.selection()
        if not sel: return
        data = self.results.get(sel[0], {})
        user = data.get('unique_id') or (profiles.get(sel[0], {}).get('config', {}) or {}).get('tiktok_id')
        if user:
            root.clipboard_clear()
            root.clipboard_append(f"@{str(user).lstrip('@')}")
            toast_manager.enqueue(f"Đã copy Username: @{str(user).lstrip('@')}", level="info")
        else:
            toast_manager.enqueue("Chưa có thông tin Username", level="warning")

    def _open_selected_tiktok(self):
        sel = self.table.selection()
        if not sel: return
        data = self.results.get(sel[0], {})
        user = data.get('unique_id') or (profiles.get(sel[0], {}).get('config', {}) or {}).get('tiktok_id')
        if user:
            import webbrowser
            webbrowser.open(f"https://www.tiktok.com/@{str(user).lstrip('@')}")
        else:
            toast_manager.enqueue("Chưa có thông tin Username để mở", level="warning")

    def _on_close(self):
        self.cancel_event.set()
        _inspection_batch['active'] = False
        try:
            self.dialog.destroy()
        except Exception:
            pass


def inspect_selected_tiktok_account():
    if not _license_guard():
        return
    if _inspection_batch['active']:
        messagebox.showinfo("Kiểm tra tài khoản", "Một đợt kiểm tra tài khoản khác đang chạy.")
        return
    sel = tree.selection()
    if not sel:
        messagebox.showwarning("Kiểm tra tài khoản", "Hãy chọn ít nhất 1 hồ sơ.")
        return
    targets = []
    for iid in sel:
        name = tree.item(iid, 'values')[0]
        if name in profiles:
            targets.append(name)
    if not targets:
        messagebox.showwarning("Kiểm tra tài khoản", "Không có hồ sơ hợp lệ trong lựa chọn.")
        return
    InspectionDialog(targets)


def _choose_browser_maintenance_mode(profile_name):
    result = {'mode': None}
    dialog = ctk.CTkToplevel(root)
    dialog.title('Reset Browser')
    fit_and_center_dialog(dialog, 560, 520, parent=root, min_w=460, min_h=380)
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()

    ctk.CTkLabel(
        dialog,
        text=f"Reset Browser: {profile_name}",
        font=("", 18, "bold"),
    ).pack(pady=(18, 8))
    ctk.CTkLabel(
        dialog,
        text="Chọn mức reset. Browser phải được đóng trước khi thực hiện.",
        text_color="#475569",
    ).pack(pady=(0, 12))

    def add_choice(title, description, mode, color):
        frame = ctk.CTkFrame(dialog, fg_color="#f8fafc", border_width=1, border_color="#dbe3ee")
        frame.pack(fill='x', padx=18, pady=5)
        ctk.CTkLabel(frame, text=title, font=("", 14, "bold"), anchor='w').pack(
            fill='x', padx=12, pady=(9, 1)
        )
        ctk.CTkLabel(frame, text=description, text_color="#475569", anchor='w').pack(
            fill='x', padx=12, pady=(0, 7)
        )

        def choose():
            result['mode'] = mode
            dialog.destroy()

        ctk.CTkButton(frame, text='Chọn', width=90, fg_color=color, command=choose).pack(
            anchor='e', padx=12, pady=(0, 9)
        )

    add_choice(
        'Dọn cache',
        'Xóa cache và file tạm; giữ đăng nhập, cookie và fingerprint.',
        BROWSER_MAINTENANCE_QUICK,
        '#2563eb',
    )
    add_choice(
        'Đăng xuất và login lại',
        'Xóa cache, cookie và dữ liệu đăng nhập; giữ fingerprint và proxy.',
        BROWSER_MAINTENANCE_SESSION,
        '#d97706',
    )
    add_choice(
        'Tạo môi trường login mới',
        'Chuyển browser profile hiện tại vào BrowserQuarantine (giữ 7 ngày), '
        'tạo profile sạch và fingerprint mới theo GEO proxy.',
        BROWSER_MAINTENANCE_FULL,
        '#dc2626',
    )

    try:
        quarantine = latest_quarantine(
            browser_glue.active_profile_path(profiles[profile_name]['config'])
        )
    except Exception:
        quarantine = None
    if quarantine:
        created = str(quarantine.get('created_at', ''))[:19].replace('T', ' ')
        expires = str(quarantine.get('expires_at', ''))[:19].replace('T', ' ')
        add_choice(
            'Khôi phục browser trước đó',
            f"Khôi phục profile đã quarantine (tạo lúc {created}, hết hạn {expires}).",
            'restore',
            '#16a34a',
        )
    dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
    root.wait_window(dialog)
    return result['mode']


def _reset_full_with_quarantine(profile_name, cfg):
    """Move the active Patchright profile into quarantine and create a clean one.

    Returns True when the quarantine-based reset ran, False when the active
    profile is not an owned Patchright profile (caller falls back to legacy).
    """
    active = browser_glue.active_profile_path(cfg)
    active_path = Path(active) if active else None
    if not active_path or active_path.name != 'Profile-Patchright' or not active_path.is_dir():
        return False

    account_uuid = str(cfg.get('account_uuid', '') or '')
    try:
        quarantine_profile(
            active_path,
            account_uuid=account_uuid,
            profile_name=profile_name,
            proxy_environment=proxy_environment_snapshot(cfg.get('fingerprint', {})),
        )
    except Exception as error:
        raise RuntimeError(f'Không quarantine được profile hiện tại: {error}')

    managed_root = active_path.parent
    legacy_dir = managed_root / 'Profile'
    if not legacy_dir.is_dir():
        create_owned_root(str(legacy_dir))
    try:
        new_profile = create_patchright_profile(str(legacy_dir), str(managed_root), account_id=account_uuid or None)
    except Exception as error:
        raise RuntimeError(f'Không tạo được browser profile sạch: {error}')
    cfg['browser_profile_path'] = str(new_profile)
    cfg['migration_state'] = migration_status(new_profile)['state']

    for key in ('cookie_str', 'cookie_hash', 'cookies_last_injected_at', 'cookies_last_captured_at', 'cookies_last_injected_profile_path'):
        cfg.pop(key, None)
    invalidate_session_auth(cfg, 'Tạo môi trường login mới')
    cfg['session_auth_state'] = 'expired'
    cfg['session_last_failure_reason'] = 'Browser environment reset'
    cfg['manual_login_pending'] = True

    seed = profile_name + str(time.time_ns())
    cfg['fingerprint'] = _generate_fingerprint(seed=seed)
    cfg['fingerprint_reset_at'] = datetime.now(timezone.utc).isoformat()
    if cfg.get('use_proxy', False):
        proxy_data = parse_proxy_string(cfg.get('proxy_string', ''))
        if proxy_data:
            try:
                resolved = resolve_geoip(proxy_data, timeout=8)
                resolved['lang'] = locale_for_country(resolved.get('geo_country_code', ''))
                cfg['fingerprint'].update(resolved)
            except Exception as error:
                cfg['geoip_last_error'] = str(error)
    save_configs()
    _set_profile_ui(profile_name, login='Cần đăng nhập', browser='Môi trường mới', upload='Chờ video', last_error='')
    update_status(
        f"[{profile_name}] Đã tạo môi trường login mới. Profile cũ đã chuyển vào "
        f"BrowserQuarantine (giữ 7 ngày, có thể khôi phục)."
    )
    return True


def _clean_browser_worker(profile_name, mode):
    profile = profiles[profile_name]
    cfg = profile['config']
    lifecycle_report = get_lifecycle(profile_name).cleanup(quit_timeout=3, kill_timeout=2)
    profile['driver'] = None
    profile['manual_driver'] = None
    profile['observer'] = None
    if lifecycle_report.get('errors'):
        raise RuntimeError('; '.join(lifecycle_report['errors']))
    if _profile_browser_process_count(profile_name):
        raise RuntimeError('User Data vẫn đang được một tiến trình Chrome sử dụng.')

    other_profile_roots = [
        browser_glue.active_profile_path(other.get('config', {}))
        for name, other in profiles.items()
        if name != profile_name and other.get('config', {}).get('chrome_profile')
    ]
    forbidden_roots = [
        other.get('config', {}).get('folder_path', '')
        for other in profiles.values()
        if other.get('config', {}).get('folder_path')
    ]
    browser_dir = _bundled_browser_dir()
    if browser_dir:
        forbidden_roots.append(browser_dir)

    if mode == BROWSER_MAINTENANCE_FULL:
        managed_data_root = app_base_dir() / 'Auto_Data'
        if managed_data_root.is_dir():
            try:
                adopt_legacy_owned_root(cfg['chrome_profile'], managed_data_root)
            except ValueError:
                pass
        if _reset_full_with_quarantine(profile_name, cfg):
            return

    report = maintain_browser(
        browser_glue.active_profile_path(cfg),
        mode,
        forbidden_roots=forbidden_roots,
        configured_profile_roots=other_profile_roots,
        stale_lock_age_seconds=0,
    )
    if not report['success']:
        if mode == BROWSER_MAINTENANCE_FULL and any(
            'ownership marker' in item['error'].lower() for item in report['errors']
        ):
            raise RuntimeError(
                'Không thể khôi phục toàn bộ vì User Data này không được tool tạo. '
                'Hãy dùng Làm sạch nhanh hoặc Xóa phiên đăng nhập.'
            )
        details = '; '.join(item['error'] for item in report['errors']) or 'Không xác định'
        raise RuntimeError(details)

    if mode in (BROWSER_MAINTENANCE_SESSION, BROWSER_MAINTENANCE_FULL):
        for key in ('cookie_str', 'cookie_hash', 'cookies_last_injected_at', 'cookies_last_captured_at', 'cookies_last_injected_profile_path'):
            cfg.pop(key, None)
    if mode == BROWSER_MAINTENANCE_FULL:
        seed = profile_name + str(time.time_ns())
        cfg['fingerprint'] = _generate_fingerprint(seed=seed)
        cfg['fingerprint_reset_at'] = datetime.now(timezone.utc).isoformat()
    save_configs()
    login_state = 'Chưa có cookie' if mode != BROWSER_MAINTENANCE_QUICK else None
    _set_profile_ui(profile_name, login=login_state, browser='Đã làm sạch', upload='Chờ video', last_error='')
    update_status(
        f"[{profile_name}] Đã làm sạch browser ({mode}): "
        f"{len(report['removed'])} mục."
    )


def _restore_browser_profile_worker(profile_name):
    profile = profiles[profile_name]
    cfg = profile['config']
    lifecycle_report = get_lifecycle(profile_name).cleanup(quit_timeout=3, kill_timeout=2)
    profile['driver'] = None
    profile['manual_driver'] = None
    profile['observer'] = None
    if lifecycle_report.get('errors'):
        raise RuntimeError('; '.join(lifecycle_report['errors']))
    if _profile_browser_process_count(profile_name):
        raise RuntimeError('User Data vẫn đang được một tiến trình Chrome sử dụng.')

    active = browser_glue.active_profile_path(cfg)
    active_path = Path(active) if active else None
    quarantine = latest_quarantine(active_path if active_path else cfg.get('chrome_profile', ''))
    if not quarantine:
        raise RuntimeError('Không tìm thấy browser profile đã quarantine.')
    quarantine_dir = Path(quarantine['_path'])
    target = restore_target(quarantine_dir)
    if target.exists():
        raise RuntimeError(
            'Không thể khôi phục vì browser profile hiện tại vẫn tồn tại. '
            'Hãy tạo môi trường login mới trước rồi thử lại.'
        )

    restore_quarantine(quarantine_dir)
    cfg['browser_profile_path'] = str(target)
    cfg['migration_state'] = migration_status(target)['state']
    invalidate_session_auth(cfg, 'Khôi phục browser profile từ quarantine')
    cfg['manual_login_pending'] = True
    save_configs()
    _set_profile_ui(profile_name, login='Cần đăng nhập', browser='Đã khôi phục', upload='Chờ video', last_error='')
    update_status(
        f"[{profile_name}] Đã khôi phục browser profile từ quarantine. "
        f"Hãy dùng 'Mở Chrome' để đăng nhập và xác minh session."
    )


def clean_browser():
    if not _license_guard():
        return
    sel = tree.selection()
    if not sel:
        messagebox.showwarning('Làm sạch Browser', 'Hãy chọn một profile.')
        return
    profile_name = tree.item(sel[0])['values'][0]
    profile = profiles.get(profile_name)
    if (
        not profile
        or profile.get('running')
        or profile.get('uploading')
        or profile.get('session_busy')
        or _browser_session_valid(profile.get('manual_driver'))
        or get_lifecycle(profile_name).has_active_driver()
        or _profile_browser_process_count(profile_name)
    ):
        messagebox.showwarning('Làm sạch Browser', 'Hãy Stop profile và đóng browser trước khi làm sạch browser.')
        return
    mode = _choose_browser_maintenance_mode(profile_name)
    if not mode:
        return
    if mode == BROWSER_MAINTENANCE_SESSION and not messagebox.askyesno(
        'Đăng xuất và login lại',
        'Thao tác này sẽ đăng xuất các website trong browser. Tiếp tục?',
    ):
        return
    if mode == BROWSER_MAINTENANCE_FULL and not messagebox.askyesno(
        'Tạo môi trường login mới',
        'Browser profile hiện tại sẽ được chuyển vào BrowserQuarantine '
        '(giữ 7 ngày, có thể khôi phục) và tạo profile sạch mới. Tiếp tục?',
    ):
        return
    if mode == 'restore' and not messagebox.askyesno(
        'Khôi phục browser',
        'Profile hiện tại sẽ bị thay thế bằng profile đã quarantine. '
        'Session hiện tại có thể không còn tác dụng. Tiếp tục?',
    ):
        return

    def worker():
        with _profile_operation_lock(profile_name):
            profile['session_busy'] = True
            try:
                if mode == 'restore':
                    _set_profile_ui(profile_name, browser='Đang khôi phục')
                    _restore_browser_profile_worker(profile_name)
                else:
                    _set_profile_ui(profile_name, browser='Đang làm sạch')
                    _clean_browser_worker(profile_name, mode)
            except Exception as e:
                _set_profile_ui(profile_name, browser='Bị lỗi', last_error=str(e))
                update_status(f'[{profile_name}] Lỗi làm sạch browser: {e}')
            finally:
                profile['session_busy'] = False

    threading.Thread(target=worker, daemon=True).start()

# =========================
# LICENSE KEY: Core Logic
# =========================
def _device_fingerprint():
    try:
        host = platform.node()
        mac = uuid.getnode()
        base = f"{host}-{mac}"
    except Exception:
        base = str(uuid.uuid4())
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]

def _load_license_cache():
    try:
        if os.path.exists(OFFLINE_CACHE_FILE):
            with open(OFFLINE_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None

def _save_license_cache(data: dict):
    try:
        with open(OFFLINE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _parse_date_yyyy_mm_dd(s):
    try:
        m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$", str(s))
        if not m:
            return None
        y, mo, d = map(int, m.groups())
        return datetime(y, mo, d, 23, 59, 59, tzinfo=timezone.utc)
    except Exception:
        return None

def _gs_open_worksheet():
    if gspread is None or Credentials is None:
        raise RuntimeError("Thiếu thư viện gspread/google-auth. Hãy pip install gspread google-auth")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") and os.path.exists(os.getenv("GOOGLE_APPLICATION_CREDENTIALS")):
        creds = Credentials.from_service_account_file(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), scopes=scopes)
    else:
        sa_file = None
        for candidate in (SERVICE_ACCOUNT_FILE, bundled_base_dir() / "service_account.json"):
            if candidate.exists():
                sa_file = candidate
                break
        if sa_file is None:
            exe_name = Path(sys.executable).name if getattr(sys, "frozen", False) else "main.py"
            raise FileNotFoundError(f"Không tìm thấy service_account.json cạnh {exe_name}")
        creds = Credentials.from_service_account_file(str(sa_file), scopes=scopes)

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(LICENSE_SHEET_ID)
    ws = sh.worksheet(LICENSE_WORKSHEET)
    return ws

def _gs_fetch_record_by_key(ws, key):
    headers = ws.row_values(1)
    header_map = {h.strip(): i+1 for i, h in enumerate(headers) if h.strip()}
    rows = ws.get_all_records(empty2zero=False, head=1)
    row_index_start = 2
    for idx, rec in enumerate(rows, start=row_index_start):
        if str(rec.get("Key", "")).strip() == str(key).strip():
            return rec, idx, header_map
    return None, None, header_map

def _gs_update_record(ws, row_index, header_map, updates: dict):
    data = []
    for col_name, value in updates.items():
        if col_name not in header_map:
            continue
        col_idx = header_map[col_name]
        data.append({
            "range": gspread.utils.rowcol_to_a1(row_index, col_idx),
            "values": [[value]],
        })
    if not data:
        return
    body = {"valueInputOption": "RAW", "data": data}
    ws.spreadsheet.values_batch_update(body)

def _validate_against_sheet(key, device_id):
    try:
        ws = _gs_open_worksheet()
        rec, row_idx, header_map = _gs_fetch_record_by_key(ws, key)
        if rec is None:
            return False, {}, "License Key không tồn tại."

        status = str(rec.get("Status", "")).strip().upper()
        if status not in VALID_STATUSES:
            return False, rec, f"License ở trạng thái {status}."

        expiry = _parse_date_yyyy_mm_dd(rec.get("Expiry", ""))
        now_utc = datetime.now(tz=timezone.utc)
        if expiry and now_utc > expiry:
            return False, rec, "License đã hết hạn."

        max_devices = 1
        try:
            if rec.get("MaxDevices", "") != "":
                max_devices = int(str(rec.get("MaxDevices")).strip())
        except Exception:
            pass

        bound_ids = str(rec.get("BoundIDs", "") or "").strip()
        bound_list = [x.strip() for x in bound_ids.split(",") if x.strip()]
        
        if device_id not in bound_list:
            if max_devices > 0 and len(bound_list) >= max_devices:
                return False, rec, "Key đã đạt giới hạn thiết bị."
            bound_list.append(device_id)

        new_bound = ",".join(sorted(set(bound_list)))
        updates = {
            "BoundIDs": new_bound,
            "LastSeen": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _gs_update_record(ws, row_idx, header_map, updates)

        info = {
            "status": status,
            "expiry": rec.get("Expiry", ""),
            "max_devices": max_devices,
            "bound_ids": new_bound,
        }
        return True, info, "License hợp lệ."
    except Exception as e:
        return False, {}, f"Lỗi kiểm tra license: {e}"

def check_license_online_or_cache(key):
    device_id = _device_fingerprint()
    ok, info, msg = _validate_against_sheet(key, device_id)
    if ok:
        exp_dt = _parse_date_yyyy_mm_dd(info.get("expiry", ""))
        exp_epoch = int(exp_dt.timestamp()) if exp_dt else None
        cache = {
            "key": key,
            "device_id": device_id,
            "last_ok": int(time.time()),
            "expiry_epoch": exp_epoch,
        }
        _save_license_cache(cache)
        return True, info, msg

    cache = _load_license_cache()
    if cache and cache.get("key") == key and cache.get("device_id") == device_id:
        now_epoch = int(time.time())
        last_ok = int(cache.get("last_ok", 0))
        within_grace = (now_epoch - last_ok) <= LICENSE_GRACE_SECONDS
        not_expired = True
        if cache.get("expiry_epoch"):
            not_expired = now_epoch <= int(cache["expiry_epoch"])
        if within_grace and not_expired:
            return True, {"status": "CACHED"}, "Dùng cache offline."

    return False, {}, msg

def _license_dialog(on_success, is_first_run=True, initial_message=""):
    from ui_dialogs import LicenseModal
    cache = _load_license_cache() or {}
    prefill_key = LICENSE_KEY or (cache.get("key", "") if isinstance(cache, dict) else "")

    def _on_modal_success(key, info):
        global LICENSE_OK, LICENSE_INFO, LICENSE_KEY
        LICENSE_OK = True
        LICENSE_INFO = info
        LICENSE_KEY = key
        if on_success:
            on_success()

    dlg = LicenseModal(
        parent=root,
        check_func=check_license_online_or_cache,
        on_success=_on_modal_success,
        initial_key=prefill_key,
        initial_status=str(LICENSE_INFO.get("status", "")),
        initial_message=initial_message,
        is_first_run=is_first_run,
        on_close_app=on_closing,
    )
    return dlg

def _set_ui_enabled(enabled: bool):
    targets = []
    try:
        targets.extend(manage_frame.winfo_children())
        targets.extend(control_frame.winfo_children())
        targets.extend(topbar.winfo_children())
    except Exception: pass

    for w in targets:
        try:
            if hasattr(w, "configure"):
                w.configure(state=("normal" if enabled else "disabled"))
        except Exception: pass
    try:
        tree.configure(selectmode="extended" if enabled else "none") 
    except Exception: pass

def require_license_then_boot():
    global LICENSE_OK, LICENSE_KEY, LICENSE_INFO

    FROZEN_SMOKE = os.environ.get('FROZEN_SMOKE_TEST', '').strip().lower() in ('1', 'true')
    if FROZEN_SMOKE:
        LICENSE_OK = True
        LICENSE_KEY = 'FROZEN_SMOKE'
        LICENSE_INFO = {'status': 'FROZEN_SMOKE'}
        root.after(0, _smoke_mode_init)
        return

    if os.environ.get('AUTO_TEST_MODE') == '1':
        LICENSE_OK = True
        LICENSE_KEY = 'AUTO_TEST'
        LICENSE_INFO = {'status': 'AUTO_TEST'}
        load_configs()
        update_profile_list()
        _set_ui_enabled(True)
        update_status("AUTO_TEST_MODE: bỏ qua nhập license để test tự động.")
        _start_youtube_monitor_safe()
        return

    if not LICENSE_REQUIRED:
        load_configs()
        update_profile_list()
        _set_ui_enabled(True)
        _start_youtube_monitor_safe()
        root.after(5000, _run_background_update_check)
        return

    _set_ui_enabled(False)
    def _after_ok():
        _set_ui_enabled(True)
        load_configs()
        update_profile_list()
        update_status("License OK. Hệ thống sẵn sàng.")
        _start_youtube_monitor_safe()
        threading.Thread(target=_license_watchdog, daemon=True).start()
        root.after(500, _first_run_download_check)
        root.after(5000, _run_background_update_check)

    # 1. Thử xác thực ngầm nếu đã có License Key lưu trong cache
    cache = _load_license_cache()
    saved_key = str(cache.get("key", "")).strip() if isinstance(cache, dict) else ""
    if saved_key:
        try:
            ok, info, _msg = check_license_online_or_cache(saved_key)
            if ok:
                LICENSE_OK = True
                LICENSE_KEY = saved_key
                LICENSE_INFO = info
                root.after(50, _after_ok)
                return
        except Exception:
            pass

    # 2. Nếu không có key hoặc xác thực ngầm thất bại -> Mở LicenseModal
    root.after(100, lambda: _license_dialog(on_success=_after_ok, is_first_run=True))

LICENSE_WATCHDOG_STOP = threading.Event()

def _license_watchdog():
    while not LICENSE_WATCHDOG_STOP.is_set():
        try:
            if LICENSE_WATCHDOG_STOP.wait(timeout=LICENSE_RECHECK_INTERVAL):
                break
            if not LICENSE_KEY: continue
            ok, _info, _msg = check_license_online_or_cache(LICENSE_KEY)
            if ok: continue
            stop_all_in_project()
            _set_ui_enabled(False)
            update_status("License mất hiệu lực.")
            root.after(0, lambda: _license_dialog(on_success=lambda: _set_ui_enabled(True), is_first_run=True, initial_message="License mất hiệu lực. Vui lòng kích hoạt lại."))
        except Exception:
            pass

def _license_guard():
    if not LICENSE_REQUIRED: return True
    if LICENSE_OK: return True
    messagebox.showerror("License", "Bạn chưa kích hoạt License.")
    return False


def _smoke_mode_init():
    import sys
    import platform
    smoke_marker_dir = Path(os.environ.get('FROZEN_SMOKE_MARKER_DIR', ''))
    smoke_nonce = os.environ.get('FROZEN_SMOKE_NONCE', '')
    if not smoke_marker_dir.is_absolute() or not smoke_marker_dir.is_dir():
        root.after(100, lambda: sys.exit(2))
        return
    smoke_marker_dir = Path(smoke_marker_dir)
    marker_file = smoke_marker_dir / 'smoke_ready.json'

    try:
        _set_ui_enabled(True)
        update_status("FROZEN_SMOKE_TEST: bỏ qua license, monitor, ngrok, updater, resource.")
        requests_version = requests.__version__
        import flask
        import blinker
        import importlib.metadata as importlib_metadata
        def _package_version(dist_name):
            try:
                return importlib_metadata.version(dist_name)
            except Exception:
                return "import-ok"
        try:
            import charset_normalizer
            csn_version = charset_normalizer.__version__
        except Exception:
            csn_version = None
        marker = {
            'ready': True,
            'app_version': CURRENT_VERSION,
            'python_version': platform.python_version(),
            'frozen': getattr(sys, 'frozen', False),
            'nonce': smoke_nonce,
            'requests_version': requests_version,
            'charset_normalizer_version': csn_version,
            'flask_version': _package_version('Flask'),
            'blinker_version': _package_version('blinker'),
            'status': 'ok',
        }
    except Exception as e:
        marker = {
            'ready': False,
            'app_version': CURRENT_VERSION,
            'error': str(e),
            'status': 'error',
        }

    try:
        with open(marker_file, 'w', encoding='utf-8') as f:
            json.dump(marker, f, indent=2)
    except Exception:
        pass

    if marker.get('ready'):
        root.after(100, _smoke_clean_exit)
    else:
        root.after(100, lambda: (update_status(f"FROZEN_SMOKE_ERROR: {marker.get('error', 'unknown')}"), sys.exit(1)))


def _smoke_clean_exit():
    """Exit the frozen smoke process immediately.

    ``root.destroy()`` from inside the mainloop can race with pending ``after``
    callbacks (icon, titlebar, monetization refresh) and hang before the process
    exits, which would fail the CI smoke timeout. ``os._exit`` guarantees the
    process terminates with a clean code; this path only runs in smoke mode."""
    import os
    import sys
    try:
        root.destroy()
    except Exception:
        pass
    os._exit(0)

# =========================
# Tiện ích UI
# =========================
def _treeview_sort_column(tv, col, reverse):
    global _tree_sort_state
    _tree_sort_state = (col, reverse)
    try:
        data = [(tv.set(k, col), k) for k in tv.get_children('')]
        if col == 'cookie_st':
            def sort_key_cookie(t):
                val = str(t[0]).lower()
                if 'live' in val: return 3
                if 'check' in val: return 2
                if 'die' in val: return 1
                return 0
            data.sort(key=sort_key_cookie, reverse=reverse)
        elif col == 'activity':
            def sort_key_activity(t):
                val = str(t[0]).lower()
                if 'chạy' in val: return 2
                if 'mở' in val: return 1
                return 0
            data.sort(key=sort_key_activity, reverse=reverse)
        elif col == 'monetization':
            def sort_key_mono(t):
                val = str(t[0]).lower()
                if 'đang bật' in val: return 4
                if 'kyc' in val: return 3
                if 'thuế' in val: return 2
                if 'tktbm' in val: return 1
                return 0
            data.sort(key=sort_key_mono, reverse=reverse)
        elif col == 'status':
            def sort_key_status(t):
                val = str(t[0]).lower()
                if 'lỗi' in val: return 3
                if 'đang xử lý' in val: return 2
                if 'đang chạy' in val or 'running' in val: return 1
                return 0
            data.sort(key=sort_key_status, reverse=reverse)
        else:
            try: data.sort(key=lambda t: float(t[0]), reverse=reverse)
            except Exception: data.sort(key=lambda t: str(t[0]).lower(), reverse=reverse)
        for idx, (_, k) in enumerate(data):
            tv.move(k, '', idx)
        tv.heading(col, command=lambda: _treeview_sort_column(tv, col, not reverse))
    except Exception: pass

# =========================
# Core Helper Functions
# =========================
# --- CẬP NHẬT HÀM SAVE/LOAD CONFIG ĐỂ LƯU STATS ---
def save_configs(allow_truncate: bool = False):
    try:
        from config_service import get_config_service
        svc = get_config_service(CONFIGS_FILE)
        if svc.ui_dispatcher is None and 'root' in globals():
            svc.ui_dispatcher = lambda cb: root.after(0, cb) if root.winfo_exists() else None
        svc.request_save(profiles, projects, ui_callback=update_project_dropdown, allow_truncate=allow_truncate)
    except Exception:
        configs = build_configs_payload(profiles, projects)
        save_configs_file(CONFIGS_FILE, configs, allow_truncate=allow_truncate)
        update_project_dropdown()

def load_configs():
    try:
        configure_delivery_registry(app_base_dir() / "delivery_ledger.json")
        get_watchdog_manager().enqueue_callback = _watchdog_enqueue_callback
        configs = load_configs_file(CONFIGS_FILE)
        loaded_profiles, loaded_projects = normalize_loaded_config(configs)
        runtime_profiles = build_runtime_profiles(loaded_profiles)

        profiles.clear()
        for name, prof in runtime_profiles.items():
            prof['queue'] = queue.Queue()
            seed = name + prof.get('config', {}).get('cookie_str', '')
            prof['config']['fingerprint'] = ensure_fingerprint_defaults(
                prof.get('config', {}).get('fingerprint', {}),
                seed=seed,
            )
            profiles[name] = prof

        projects.clear()
        projects.update({k: set(v) for k, v in loaded_projects.items()})
        if 'Mặc định' not in projects: projects['Mặc định'] = set()

        # Reconcile persisted browser ownership before the first table render.
        # Rendering calls ensure_account_uuid(), which must not generate a new
        # UUID before an existing Profile-Patchright marker can restore it.
        _migrate_profile_drivers()
        update_project_dropdown()
        selected_project_var.set(ALL_OPTION)
        update_profile_list()
        _cleanup_expired_quarantines()
    except FileNotFoundError:
        projects['Mặc định'] = set()
        update_project_dropdown()
        selected_project_var.set(ALL_OPTION)


def _cleanup_expired_quarantines():
    for profile_name, profile in profiles.items():
        try:
            active = browser_glue.active_profile_path(profile['config'])
            removed = cleanup_quarantines(active)
            if removed:
                update_status(
                    f"[{profile_name}] Đã dọn {len(removed)} browser profile "
                    f"quarantine hết hạn/quá cũ."
                )
        except Exception:
            continue


def _migrate_profile_drivers():
    changed = False
    for profile_name, profile in profiles.items():
        config = profile['config']
        before = (config.get('browser_profile_path'), config.get('migration_state'))
        try:
            browser_glue.ensure_patchright_profile(config)
            _sync_patchright_migration(config)
            after = (config.get('browser_profile_path'), config.get('migration_state'))
            if after != before:
                changed = True
        except Exception as e:
            update_status(f"[{profile_name}] [WARN] Không thể tạo/resume Profile-Patchright: {e}")
    if changed:
        save_configs()

# --- BẢNG THỐNG KÊ MỚI ---
def show_statistics_board():
    if not _license_guard(): return

    dlg = ctk.CTkToplevel(root)
    dlg.title("Thống kê hoạt động")
    fit_and_center_dialog(dlg, 500, 400, parent=root, min_w=400, min_h=300)
    dlg.grab_set()

    # Frame Tổng
    total_today = sum(p['uploads_today_count'] for p in profiles.values())
    total_yesterday = sum(p.get('uploads_yesterday_count', 0) for p in profiles.values())

    f_sum = ctk.CTkFrame(dlg)
    f_sum.pack(fill='x', padx=10, pady=10)
    ctk.CTkLabel(f_sum, text=f"Tổng hôm nay: {total_today}", font=("", 16, "bold"), text_color="#16a34a").pack(side='left', padx=20)
    ctk.CTkLabel(f_sum, text=f"Tổng hôm qua: {total_yesterday}", font=("", 16, "bold"), text_color="#64748b").pack(side='right', padx=20)

    # Bảng chi tiết
    cols = ('name', 'today', 'yesterday')
    tv = ttk.Treeview(dlg, columns=cols, show='headings', height=15)
    tv.heading('name', text='Tên Hồ Sơ')
    tv.heading('today', text='Hôm nay')
    tv.heading('yesterday', text='Hôm qua')

    tv.column('name', width=200)
    tv.column('today', width=100, anchor='center')
    tv.column('yesterday', width=100, anchor='center')

    tv.pack(fill='both', expand=True, padx=10, pady=(0, 10))

    # Load dữ liệu (có filter theo Project đang chọn bên ngoài cho tiện)
    p = selected_project_var.get()
    targets = sorted(profiles.keys()) if p == ALL_OPTION else sorted(projects.get(p, []))

    for name in targets:
        if name in profiles:
            td = profiles[name]['uploads_today_count']
            yd = profiles[name].get('uploads_yesterday_count', 0)
            tv.insert('', 'end', values=(name, td, yd))

    ctk.CTkButton(dlg, text="Đóng", command=dlg.destroy).pack(pady=5)
# ------------------------------

class VideoFolderHandler(FileSystemEventHandler):
    def __init__(self, profile_name):
        self.profile_name = profile_name

    def on_created(self, event):
        if not event.is_directory:
            self._schedule_path(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._schedule_path(event.dest_path)

    def _schedule_path(self, file_path):
        if not str(file_path).lower().endswith(VIDEO_EXTENSIONS):
            return
        if not _claim_video_path(file_path):
            return
        threading.Thread(target=self._enqueue_when_stable, args=(file_path,), daemon=True).start()

    def _enqueue_when_stable(self, file_path):
        if self.profile_name not in profiles:
            _release_video_path(file_path)
            return
        current_time = time.time()
        last_time = profiles[self.profile_name]['last_event_time'].get(file_path, 0)
        if current_time - last_time < 0.8:
            _release_video_path(file_path)
            return
        profiles[self.profile_name]['last_event_time'][file_path] = current_time

        stable_deadline = time.time() + 180
        while time.time() < stable_deadline:
            if is_file_stable(file_path, FILE_STABLE_CHECKS, FILE_STABLE_INTERVAL):
                break
            time.sleep(FILE_STABLE_INTERVAL)
        else:
            update_status(f"[{self.profile_name}] Video chưa copy xong sau 180 giây: {Path(file_path).name}")
            _release_video_path(file_path)
            return

        _mark_upload_timing(file_path, 'detected_at')

        try: file_size = os.path.getsize(file_path)
        except Exception: file_size = 0
        if file_size > MAX_FILE_SIZE or file_size == 0:
            update_status(f"[{self.profile_name}] Kích thước video không hợp lệ.")
            _release_video_path(file_path)
            return
        try:
            config = profiles[self.profile_name]['config']
            if config.get('open_only_when_video', False):
                watch_started_at = profiles[self.profile_name].get('watch_started_at', 0)
                file_mtime = os.path.getmtime(file_path)
                if file_mtime <= watch_started_at:
                    update_status(f"[{self.profile_name}] Bỏ qua video cũ: {Path(file_path).name}")
                    _release_video_path(file_path)
                    return
        except Exception:
            pass
        if FAST_MODE: logging.warning(f"[{self.profile_name}] Phát hiện video mới.")
        _set_profile_ui(self.profile_name, upload='Có video mới')
        update_status(f"[{self.profile_name}] Phát hiện video mới: {Path(file_path).name}")
        _mark_upload_timing(file_path, 'enqueued_at')
        profiles[self.profile_name]['queue'].put(file_path)

def check_system_resources(profile_name):
    try:
        # Kiểm tra tài nguyên, nếu cao thì chờ 5s rồi kiểm tra lại
        for _ in range(3):
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0.5)
            
            if mem.percent < 92 and cpu < 95:
                return True
            
            # Nếu cao, log và đợi
            msg = f"RAM > 90%" if mem.percent > 90 else f"CPU > 95%"
            update_status(f"[{profile_name}] {msg}. Đợi giảm tải...")
            time.sleep(5)
            
        return False
    except Exception: return True

def after_kill_cleanup_running_profiles():
    for name in list(running_profiles):
        if name in profiles:
            lc = get_lifecycle(name)
            if not profiles[name].get('running', False) and not lc.has_active_driver():
                running_profiles.discard(name)


def kill_stale_chrome_processes(profile_name):
    target_dir = browser_glue.active_profile_path(profiles[profile_name]['config'])
    killed_count = 0

    def _force_kill(proc):
        nonlocal killed_count
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
        except Exception:
            return False
        killed_count += 1
        return True

    try:
        # Only terminate Chrome processes whose command line proves ownership of this User Data.
        lc = get_lifecycle(profile_name)
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'exe']):
            try:
                if not proc.is_running():
                    continue
                raw_name = proc.info.get('name') or ''
                name_str = raw_name.lower() if raw_name else ''
                if name_str not in ('chrome.exe', 'chrome'):
                    continue
                cmdline = proc.info['cmdline'] or []
                if process_uses_profile(cmdline, target_dir):
                    _force_kill(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        lc.clear_owned_pids()

        if killed_count > 0:
            time.sleep(0.5)
    except Exception as e:
        logging.warning(f"[{profile_name}] Lỗi khi kill process: {e}")

def ensure_driver(profile_name, lifecycle_gen=None):
    total_start = time.perf_counter()
    token = profiles[profile_name]['driver']
    if _browser_session_valid(token):
        return token

    config = profiles[profile_name]['config']
    if isinstance(token, SessionToken):
        token.set_cancelled()
        token.quit()
    profiles[profile_name]['driver'] = None

    lc = get_lifecycle(profile_name)
    if lifecycle_gen is None:
        lifecycle_gen = lc.generation

    for attempt in range(DRIVER_INIT_RETRIES):
        if lc.is_cancelled or not lc.is_current(lifecycle_gen):
            update_status(f"[{profile_name}] Lifecycle đã bị hủy, không mở session.")
            raise RuntimeError("Lifecycle cancelled")
        token = None
        try:
            attempt_start = time.perf_counter()
            if not check_system_resources(profile_name):
                update_status(f"[{profile_name}] Tài nguyên thấp. Tạm nghỉ 5s.")
                time.sleep(5)
                if attempt == DRIVER_INIT_RETRIES - 1:
                    raise SessionSetupError("System Resource Low")
                continue

            update_status(f"[{profile_name}] Mở Patchright (Lần {attempt + 1})...")
            _set_profile_ui(profile_name, status='Đang khởi động', browser='Đang mở', upload='Chờ video', last_error='')

            proxy_data = None
            proxy_expected_ip = None
            direct_ip = None
            if config.get('use_proxy', False):
                _set_profile_ui(profile_name, proxy='Đang kiểm tra')
                proxy_data = parse_proxy_string(config.get('proxy_string', ''))
                if not proxy_data:
                    _set_profile_ui(profile_name, proxy='Sai định dạng', last_error='Proxy sai định dạng')
                    raise SessionSetupError("Proxy sai định dạng; từ chối mở browser trực tiếp")
                proxy_expected_ip = proxy_data['ip']
                update_status(f"[{profile_name}] [DEBUG] Đã nhận cấu hình proxy: {proxy_data['ip']}")
            else:
                _set_profile_ui(profile_name, proxy='Tắt')

            geo_changed = _refresh_profile_geoip(profile_name, config, proxy_data)
            if geo_changed:
                save_configs()
            if proxy_data:
                preflight = _proxy_endpoint_preflight(profile_name, proxy_data)
                if not preflight['proxy_exit_ip']:
                    raise SessionSetupError("Không xác minh được proxy endpoint; từ chối mở browser")
                proxy_expected_ip = preflight['proxy_exit_ip']
                direct_ip = preflight['direct_ip']

            profile_path = browser_glue.ensure_patchright_profile(config)
            _sync_patchright_migration(config)
            save_configs()
            token = browser_glue.open_session(config, profile_name)
            token.generation = lifecycle_gen

            if proxy_data:
                update_status(f"[{profile_name}] [DEBUG] Đang check IP trên browser mới...")
                is_match, current_ip = browser_glue.verify_exit_ip(token, proxy_expected_ip)
                if not is_match and direct_ip and current_ip and current_ip != direct_ip:
                    update_status(f"[{profile_name}] [DEBUG] Proxy exit IP thay đổi sau preflight: {current_ip}")
                    is_match = True
                if not is_match:
                    message = "Proxy Verification Indeterminate: browser không xác minh được proxy" if not current_ip else f"Proxy sai IP: {current_ip}"
                    _set_profile_ui(profile_name, proxy='Không xác minh được' if not current_ip else 'Sai IP', last_error=message)
                    raise SessionSetupError(message)
                _set_profile_ui(profile_name, proxy=f"OK: {current_ip}")
                update_status(f"[{profile_name}] Proxy OK: {current_ip}")

            auth_source = browser_glue.authenticate_session(
                token,
                config,
                profile_name,
                TIKTOK_UPLOAD_URL,
                allow_cookie_fallback=True,
                status_callback=update_status,
            )
            if auth_source == 'cookie_fallback':
                _save_cookie_injection_metadata(profile_name, config.get('cookie_str', ''))
            _save_session_auth_metadata(profile_name, 'verified', auth_source)
            _advance_patchright_migration(config, MigrationState.CREATED.value, MigrationState.COOKIES_IMPORTED)
            _advance_patchright_migration(config, MigrationState.COOKIES_IMPORTED.value, MigrationState.LOGIN_VERIFIED)
            save_configs()

            if not lc.register_automation(lifecycle_gen, token):
                token.set_cancelled()
                token.quit()
                raise RuntimeError("Lifecycle changed before session publish")
            profiles[profile_name]['driver'] = token
            _set_profile_ui(profile_name, status='Đang chạy', browser='Sẵn sàng', upload='Chờ video')
            update_status(f"[{profile_name}] [DEBUG] Patchright sẵn sàng: {profile_path}")
            _timing_log(profile_name, "driver_attempt_total", attempt_start)
            _timing_log(profile_name, "driver_ready_total", total_start)
            return token

        except Exception as error:
            if token:
                token.set_cancelled()
                token.quit()
            if isinstance(error, (TikTokLoginRequiredError, browser_glue.LoginRequiredError)):
                _mark_session_failure(profile_name, str(error))
                _set_profile_ui(
                    profile_name,
                    status='Lỗi',
                    browser='Bị lỗi',
                    login='Cần đăng nhập lại',
                    last_error='Cookie bị TikTok từ chối hoặc đã hết hạn',
                )
                update_status(f"[{profile_name}] Dừng khởi tạo: cookie/session bị TikTok từ chối.")
                raise
            if isinstance(error, ProfileBusyError):
                _set_profile_ui(
                    profile_name,
                    status='Lỗi',
                    browser='Bị lỗi',
                    last_error='Profile đang được session khác dùng; hãy đóng browser và thử lại',
                )
                update_status(
                    f"[{profile_name}] Profile-Patchright vẫn còn session chưa đóng sạch; không retry: {error}"
                )
                raise
            update_status(f"[{profile_name}] Lỗi mở Patchright lần {attempt + 1}: {error}")
            if attempt == DRIVER_INIT_RETRIES - 1:
                _set_profile_ui(profile_name, status='Lỗi', browser='Bị lỗi', last_error=str(error))
                update_status(f"[{profile_name}] Lỗi mở Patchright sau {DRIVER_INIT_RETRIES} lần: {error}")
                raise
            update_status(f"[{profile_name}] Thử mở Patchright lại lần {attempt + 2}...")
            time.sleep(DRIVER_INIT_RETRY_DELAY)

    return None

# =========================
# Upload Logic
# =========================
def upload_video(profile_name, video_path):
    last_error = None
    token = None
    file_name = Path(video_path).name
    short_name = shorten_filename(file_name)
    benchmark_phases = {}
    benchmark_success = False
    benchmark_post_clicked = False
    benchmark_outcome = ''
    result = None
    account_blocked = False
    driver_reused_before = _browser_session_valid(profiles.get(profile_name, {}).get('driver'))

    def record_phase(name, started_at):
        elapsed = time.perf_counter() - started_at
        benchmark_phases[name] = benchmark_phases.get(name, 0.0) + elapsed

    try:
        max_attempts = 1 if os.environ.get('UPLOAD_TEST_MODE') == '1' else RETRY_COUNT + 1
        for attempt in range(1, max_attempts + 1):
            upload_invoked = False
            try:
                if not profiles.get(profile_name, {}).get('running', False) or get_lifecycle(profile_name).is_cancelled:
                    last_error = 'cancelled_safe'
                    break
                phase_started = time.perf_counter()
                token = ensure_driver(profile_name)
                record_phase('ensure_driver_seconds', phase_started)
                update_status(f"[{profile_name}] Đang đăng: {short_name}")
                _set_profile_ui(profile_name, upload='Đang tải video', last_error='')
                phase_started = time.perf_counter()
                upload_invoked = True
                result = browser_glue.run_upload(
                    token,
                    video_path,
                    status_callback=lambda message: update_status(f"[{profile_name}] {message}"),
                    stop_before_post=os.environ.get('UPLOAD_TEST_STOP_BEFORE_POST') == '1',
                )
                # Patchright owns before_dispatch=mark_post_dispatch_started semantics.
                record_phase('patchright_upload_seconds', phase_started)
                benchmark_post_clicked = bool(result.post_dispatched)
                benchmark_outcome = str(result.outcome or '')
                last_error = result.message or result.outcome

                if result.outcome == 'posted':
                    benchmark_success = True
                    _set_profile_ui(profile_name, upload='Đã đăng', last_error='')
                    _advance_patchright_migration(
                        profiles[profile_name]['config'],
                        MigrationState.LOGIN_VERIFIED.value,
                        MigrationState.UPLOAD_VERIFIED,
                    )
                    try:
                        _cleanup_legacy_profile_after_verified_upload(
                            profile_name,
                            profiles[profile_name]['config'],
                        )
                    except Exception as cleanup_error:
                        update_status(
                            f"[{profile_name}] [WARN] Upload đã xác minh nhưng chưa xóa được profile cũ: {cleanup_error}"
                        )
                    save_configs()
                    try:
                        os.remove(video_path)
                    except Exception as error:
                        update_status(f"[{profile_name}] [WARN] Đã đăng nhưng không xóa được video: {error}")
                    try:
                        _export_live_cookies_to_config(token, profile_name)
                    except Exception as error:
                        update_status(f"[{profile_name}] [WARN] Đã đăng nhưng không lưu được cookie live: {error}")
                    if not profiles[profile_name]['config'].get('open_only_when_video', False):
                        _return_to_upload_page(profile_name)
                    return True

                if result.outcome == 'prepared':
                    benchmark_success = True
                    _set_profile_ui(profile_name, upload='Dry-run OK (chưa Post)', last_error='')
                    update_status(
                        f"[{profile_name}] Pre-Post dry-run hoàn tất: editor sẵn sàng, chưa bấm Post."
                    )
                    return 'prepared'

                no_retry = result.post_dispatched or result.outcome in {
                    'post_uncertain', 'cancelled_safe', 'cancelled_uncertain', 'rejected', 'login_required'
                }
                upload_state = 'Chưa xác nhận' if result.post_dispatched or result.outcome in {'post_uncertain', 'cancelled_uncertain'} else 'Đăng lỗi'
                if result.outcome == 'login_required':
                    _set_profile_ui(profile_name, login='Cần đăng nhập lại')
                _set_profile_ui(profile_name, upload=upload_state, last_error=last_error)
                if result.outcome == 'rejected' and str(result.details.get('rejection_scope') or '') == 'account_posting_blocked':
                    # Account-level posting block: TikTok explicitly says the ACCOUNT is
                    # temporarily prevented from posting. Dispatch is pointless and harmful
                    # for the remaining queue, so surface it as a terminal profile stop.
                    account_blocked = True
                    last_error = 'Tài khoản bị TikTok tạm khóa đăng; đã dừng queue.'
                    _set_profile_ui(profile_name, upload='TikTok khóa đăng', last_error=shorten_text(last_error))
                    update_status(f"[{profile_name}] {last_error}")
                    break
                if no_retry:
                    update_status(f"[{profile_name}] Không retry {short_name}: {result.outcome} ({last_error})")
                    break
                update_status(f"[{profile_name}] Upload lỗi trước Post (lần {attempt}), có thể retry: {last_error}")
            except SessionSetupError as error:
                last_error = str(error)
                if isinstance(error, ProfileBusyError):
                    last_error = f'profile_busy: {error}'
                    break
                if upload_invoked:
                    last_error = 'cancelled_safe' if token and token.cancellation_event.is_set() else f'post_uncertain: {error}'
                    break
                if attempt >= max_attempts:
                    break
                if token:
                    token.quit()
                profiles[profile_name]['driver'] = None
                get_lifecycle(profile_name).detach_automation()
                update_status(f"[{profile_name}] Session lỗi trước Post, thử lại: {last_error}")
                time.sleep(0.5)

        if account_blocked:
            _set_profile_ui(profile_name, upload='TikTok khóa đăng', last_error='Tài khoản bị TikTok tạm khóa đăng; đã dừng queue.')
            return 'account_blocked'
        _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=str(last_error or 'Không đăng được video'))
        _append_failed_upload_log(profile_name, file_name, last_error or 'failed_after_retries', outcome=last_error or 'failed_after_retries')
        return False
    except Exception as e:
        last_error = str(e)
        try:
            _append_failed_upload_log(profile_name, file_name, str(e), outcome='fatal')
        except Exception:
            pass
        update_status(f"[{profile_name}] Lỗi nghiêm trọng: {e}")
        _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=str(e))
        return False
    finally:
        if profile_name in profiles:
            profiles[profile_name]['uploading'] = False
        try:
            if result is not None:
                timings = result.details.get("timings") or {}
                for timing_key, timing_value in timings.items():
                    benchmark_phases[timing_key] = timing_value
        except Exception:
            pass
        try:
            _write_upload_benchmark(
                profile_name,
                video_path,
                benchmark_success,
                '' if benchmark_success else (last_error or 'upload_failed'),
                benchmark_phases,
                meta={
                    'driver_reused_before': driver_reused_before,
                    'driver_reused_actual': driver_reused_before,
                    'post_clicked': benchmark_post_clicked,
                    'outcome': benchmark_outcome,
                    'driver_mode': 'warm' if driver_reused_before else 'cold',
                },
            )
        except Exception as benchmark_error:
            update_status(f"[{profile_name}] [WARN] Không ghi được benchmark upload: {benchmark_error}")
        finally:
            try:
                _complete_delivery_from_upload(video_path, result, last_error, benchmark_success)
            except Exception as delivery_error:
                update_status(f"[{profile_name}] [WARN] Không ghi được kết quả giao nhận: {delivery_error}")
                _release_video_path(video_path)

def _return_to_upload_page(profile_name):
    """Return a keep-open profile's browser to the upload page to await the next
    video. Non-fatal: a confirmed upload is never downgraded by a failed
    navigation; on failure the session is detached so the next upload opens a
    clean session."""
    profile = profiles.get(profile_name)
    if not profile or not profile.get('running'):
        return False
    token = profile.get('driver')
    if not _browser_session_valid(token):
        return False
    try:
        ready = browser_glue.navigate_upload_ready(token, TIKTOK_UPLOAD_URL)
    except Exception as error:
        update_status(f"[{profile_name}] [WARN] Không quay lại trang upload để chờ video kế: {error}")
        ready = False
    if ready:
        _set_profile_ui(profile_name, browser='Sẵn sàng', last_error='')
        update_status(f"[{profile_name}] Đã quay lại trang upload, sẵn sàng nhận video tiếp theo.")
        return True
    if _browser_session_valid(token):
        try:
            token.quit()
        except Exception:
            pass
    profiles.get(profile_name, {}).pop('driver', None)
    try:
        get_lifecycle(profile_name).detach_automation()
    except Exception:
        pass
    update_status(f"[{profile_name}] [WARN] Chưa quay lại được trang upload; video kế sẽ mở session mới.")
    return False


def _release_queued_items(profile_name):
    """Release every queued-but-unprocessed delivery back to DISCOVERED.

    Called when a profile stops because its TikTok account is temporarily blocked from
    posting. Files stay on disk and their records remain recoverable (never stuck in
    ENQUEUED forever); a later explicit claim or profile start can pick them up again.
    """
    if profile_name not in profiles:
        return 0
    q = profiles[profile_name].get('queue')
    if q is None:
        return 0
    reg = get_delivery_registry()
    released = 0
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        try:
            path = getattr(item, 'path', None) or item
            reg.release_delivery(path, error_code='ACCOUNT_BLOCKED', error_detail='queue stopped: account posting blocked')
            released += 1
        except Exception:
            pass
        try:
            q.task_done()
        except Exception:
            pass
    return released


def process_video_queue_thread(profile_name):
    idle_start = None
    lc = get_lifecycle(profile_name)
    while True:
        try:
            if profile_name not in profiles or not profiles[profile_name]['running'] or lc.is_cancelled:
                break

            limit = profiles[profile_name]['config'].get('max_uploads_per_day', 0)
            if limit > 0 and profiles[profile_name]['uploads_today_count'] >= limit:
                update_status(f"[{profile_name}] Đã đạt giới hạn {limit} video/ngày. Profile sẽ tự dừng.")
                _set_profile_ui(profile_name, upload='Đạt giới hạn', last_error=f'Đã đạt {limit} video/ngày')
                time.sleep(LIMIT_REACHED_SHUTDOWN_DELAY)
                stop_profile(profile_name)
                break

            try:
                raw_item = profiles[profile_name]['queue'].get(timeout=1)
            except queue.Empty:
                now = time.time()
                if IDLE_SHUTDOWN_TIMEOUT > 0:
                    if idle_start is None:
                        idle_start = now
                    elif now - idle_start > IDLE_SHUTDOWN_TIMEOUT:
                        update_status(f"[{profile_name}] Hàng chờ rỗng quá {IDLE_SHUTDOWN_TIMEOUT}s. Tự động dừng.")
                        stop_profile(profile_name)
                        break
                else:
                    idle_start = None
                
                # Watchdog health check (shared observer)
                if not profiles[profile_name].get('running', False):
                    continue
                try:
                    mgr = get_watchdog_manager()
                    if not mgr.is_observer_alive():
                        update_status(f"[{profile_name}] Watchdog bị lỗi, khởi động lại...")
                        mgr.restart_observer()
                        folder = profiles[profile_name]['config'].get('folder_path', '')
                        if folder:
                            lifecycle = get_lifecycle(profile_name)
                            mgr.schedule_folder(profile_name, folder, lifecycle.generation)
                            update_status(f"[{profile_name}] Watchdog đã khởi động lại.")
                except Exception as e:
                    logging.warning(f"[{profile_name}] Health check watchdog lỗi: {e}")
                continue

            idle_start = None
            # Normalize legacy str items and generation-tagged QueueItems.
            if isinstance(raw_item, QueueItem):
                item_gen = raw_item.lifecycle_generation
                video_path = raw_item.path
            else:
                item_gen = None
                video_path = str(raw_item)
            # Reject stale queue items produced before a profile restart.
            if item_gen is not None and item_gen != get_lifecycle(profile_name).generation:
                update_status(f"[{profile_name}] Bỏ qua video cũ sau khởi động lại: {Path(video_path).name}")
                get_delivery_registry().release_delivery(
                    video_path, error_code="STALE_GENERATION", error_detail="queue item generation mismatch"
                )
                profiles[profile_name]['queue'].task_done()
                continue
            get_delivery_registry().transition_delivery(video_path, DeliveryState.PROCESSING)
            _mark_upload_timing(video_path, 'dequeued_at')
            update_status(f"[{profile_name}] Đã đưa video vào hàng chờ xử lý: {shorten_filename(Path(video_path).name)}")
            config = profiles[profile_name]['config']
            open_only = config.get('open_only_when_video', False)
            if open_only and not _browser_session_valid(profiles[profile_name].get('driver')):
                _set_profile_ui(profile_name, browser='Đang mở', upload='Có video mới')
                update_status(f"[{profile_name}] Có video mới, đang mở profile để đăng.")
            _set_profile_ui(profile_name, upload='Đang đăng')
            profiles[profile_name]['uploading'] = True
            ok = upload_video(profile_name, video_path)
            if ok == 'prepared':
                _set_profile_ui(profile_name, upload='Dry-run OK (chưa Post)', last_error='')
                update_status(f"[{profile_name}] Dry-run hoàn tất; không tăng số video đã đăng.")
            elif ok == 'account_blocked':
                _set_profile_ui(profile_name, upload='TikTok khóa đăng', last_error='Tài khoản bị TikTok tạm khóa đăng; đã dừng queue.')
                update_status(f"[{profile_name}] Tài khoản bị TikTok tạm khóa đăng; đã dừng queue.")
                released = _release_queued_items(profile_name)
                if released:
                    update_status(f"[{profile_name}] Đã trả {released} video chưa đăng về trạng thái chờ xử lý.")
                profiles[profile_name]['queue'].task_done()
                stop_profile(profile_name)
                if profiles.get(profile_name, {}).get('running', False):
                    # License guard may block stop_profile(); force the stop so the
                    # watchdog/browser never keep running after an account block.
                    try:
                        _stop_profile_driver(profile_name)
                    except Exception:
                        pass
                break
            elif ok:
                profiles[profile_name]['uploads_today_count'] += 1
                try:
                    meta = lookup_download(video_path)
                    append_activity(
                        "tiktok_upload",
                        video_name=meta.get("title") or Path(video_path).name,
                        video_url=meta.get("video_url", ""),
                        profile=profile_name,
                        status="success",
                        detail="uploaded",
                        file_path=video_path,
                    )
                except Exception:
                    pass
                
                # --- LƯU STATS NGAY SAU KHI ĐĂNG THÀNH CÔNG ---
                save_configs() 
                # ----------------------------------------------

                cnt = profiles[profile_name]['uploads_today_count']
                lmt_str = str(limit) if limit > 0 else "∞"
                _set_profile_ui(profile_name, upload=f'Đã đăng {cnt}/{lmt_str}', last_error='')
                update_status(f"[{profile_name}] Đã đăng {cnt}/{lmt_str} hôm nay.")
            else:
                _set_profile_ui(profile_name, upload='Đăng lỗi')
            profiles[profile_name]['queue'].task_done()
            if open_only and profiles.get(profile_name, {}).get('running') and profiles[profile_name]['queue'].empty():
                close_profile_browser(profile_name)
        except KeyError:
            update_status(f"[{profile_name}] Profile đã bị xóa, dừng queue thread.")
            break
        except Exception as e:
            _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=str(e))
            update_status(f"[{profile_name}] Lỗi Queue: {e}")
            try:
                if 'raw_item' in locals():
                    profiles[profile_name]['queue'].task_done()
            except Exception:
                pass
            continue

# =========================
# UI Helpers & Log
# =========================
def trim_text_widget_lines(widget, max_lines):
    try:
        current_lines = int(widget.index("end-1c").split(".")[0])
        if current_lines > max_lines:
            delete_to = f"{current_lines - max_lines + 1}.0"
            widget.delete("1.0", delete_to)
    except Exception:
        pass

def shorten_text(value, max_len=70):
    value = str(value or "")
    if len(value) <= max_len:
        return value
    return value[:max_len - 3] + "..."

def shorten_filename(filename, max_len=64):
    filename = str(filename or "")
    if len(filename) <= max_len:
        return filename
    base, ext = os.path.splitext(filename)
    keep = max_len - len(ext) - 3
    if keep <= 10:
        return filename[:max_len - 3] + "..."
    return base[:keep] + "..." + ext

LOG_RING_BUFFER = {
    "important": [],
    "failed": [],
    "detail": [],
}

def add_log_entry(message, tag="INFO", important=False):
    line_obj = {"text": f"{datetime.now().strftime('%H:%M:%S')} {message}", "tag": tag}
    LOG_RING_BUFFER["detail"].append(line_obj)
    if len(LOG_RING_BUFFER["detail"]) > 300:
        LOG_RING_BUFFER["detail"].pop(0)
    if important or tag in ("ERROR", "CRITICAL", "SUCCESS", "WARN"):
        LOG_RING_BUFFER["important"].append(line_obj)
        if len(LOG_RING_BUFFER["important"]) > 150:
            LOG_RING_BUFFER["important"].pop(0)

def update_status(message):
    tag, important_tag = classify_log_message(message)
    add_log_entry(message, tag=tag or "INFO", important=bool(important_tag))
    try:
        from log_engine import get_log_engine
        get_log_engine().post_log(message, tag=tag or "INFO", important_tag=important_tag)
    except Exception:
        pass

def _apply_scale(*_):
    try:
        value = scale_var.get().strip()
        if not value:
            return
        ctk.set_widget_scaling(int(value.replace('%', '')) / 100)
    except Exception:
        pass

def clear_failed_uploads_panel():
    try:
        if failed_uploads_text.winfo_exists():
            failed_uploads_text.configure(state='normal')
            failed_uploads_text.delete('1.0', ctk.END)
            failed_uploads_text.configure(state='disabled')
        update_status("[UI] Đã xóa danh sách lỗi trên dashboard.")
    except Exception as e:
        update_status(f"[UI] Không thể xóa danh sách lỗi trên dashboard: {e}")

def cleanup_failed_videos():
    # === PHASE 1: VIDEO LỖI ===
    failed_files = {}
    if os.path.exists(FAILED_UPLOADS_LOG):
        try:
            with open(FAILED_UPLOADS_LOG, 'r', encoding='utf-8') as f:
                for line in f:
                    m = re.search(r'profile=(\S+).*?file=(\S+)', line)
                    if m:
                        failed_files.setdefault(m.group(1), set()).add(m.group(2))
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể đọc file log: {e}")
            return

    failed_total_size = 0
    failed_count = 0
    failed_to_delete = []
    for pname, fnames in failed_files.items():
        cfg = profiles.get(pname, {}).get('config', {})
        folder = cfg.get('folder_path', '')
        if not folder or not os.path.isdir(folder):
            continue
        for fname in fnames:
            fpath = os.path.join(folder, fname)
            if os.path.isfile(fpath):
                failed_total_size += os.path.getsize(fpath)
                failed_count += 1
                failed_to_delete.append(fpath)

    # === PHASE 2: VIDEO ĐANG CHỜ ===
    VIDEO_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv')
    pending_total_size = 0
    pending_count = 0
    pending_to_delete = []
    for pname, prof in profiles.items():
        cfg = prof.get('config', {})
        folder = cfg.get('folder_path', '')
        if not folder or not os.path.isdir(folder):
            continue
        failed_set = failed_files.get(pname, set())
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if not os.path.isfile(fpath) or not fname.lower().endswith(VIDEO_EXTS):
                continue
            if fname in failed_set:
                continue
            pending_total_size += os.path.getsize(fpath)
            pending_count += 1
            pending_to_delete.append(fpath)
    
    if failed_count == 0 and pending_count == 0:
        messagebox.showinfo("Thông báo", "Không tìm thấy video nào để dọn dẹp.")
        return
    
    if failed_count > 0:
        size_mb = failed_total_size / (1024 * 1024)
        if not messagebox.askyesno("Xác nhận", f"Tìm thấy {failed_count} video lỗi ({size_mb:.1f} MB).\nXóa để giải phóng bộ nhớ?"):
            failed_to_delete = []
    
    if pending_count > 0:
        pending_mb = pending_total_size / (1024 * 1024)
        if not messagebox.askyesno("Xác nhận", f"Còn {pending_count} video đang chờ upload ({pending_mb:.1f} MB).\nXóa luôn?"):
            pending_to_delete = []
    
    all_to_delete = failed_to_delete + pending_to_delete
    if not all_to_delete:
        return
    
    deleted = 0
    for fpath in all_to_delete:
        try:
            os.remove(fpath)
            deleted += 1
        except Exception:
            pass
    
    try:
        open(FAILED_UPLOADS_LOG, 'w', encoding='utf-8').close()
    except Exception:
        pass
    
    try:
        if failed_uploads_text.winfo_exists():
            failed_uploads_text.configure(state='normal')
            failed_uploads_text.delete('1.0', ctk.END)
            failed_uploads_text.configure(state='disabled')
    except Exception:
        pass
    
    total_mb = (failed_total_size + pending_total_size) / (1024 * 1024)
    update_status(f"Đã xóa {deleted}/{failed_count + pending_count} video, giải phóng {total_mb:.1f} MB.")
    messagebox.showinfo("Hoàn tất", f"Đã xóa {deleted}/{failed_count + pending_count} video, giải phóng {total_mb:.1f} MB.")

def update_project_dropdown():
    if 'project_dropdown' not in globals(): return
    pl = [ALL_OPTION] + sorted(list(projects.keys()))
    project_dropdown.configure(values=pl)
    if selected_project_var.get() not in pl: selected_project_var.set(ALL_OPTION)

_row_tags_configured = False

def _apply_row_tags():
    global _row_tags_configured
    if _row_tags_configured:
        return
    try:
        tree.tag_configure('tag_ready', background='#DCFCE7', foreground='#166534')
        tree.tag_configure('tag_processing', background='#FEF3C7', foreground='#92400E')
        tree.tag_configure('tag_error', background='#FEE2E2', foreground='#991B1B')
        tree.tag_configure('tag_stopped', background='#F3F4F6', foreground='#374151')
        tree.tag_configure('tag_running', background='#DCFCE7', foreground='#166534')
        tree.tag_configure('tag_manual', background='#DBEAFE', foreground='#1D4ED8')
        tree.tag_configure('tag_warning', background='#FFEDD5', foreground='#C2410C')
        _row_tags_configured = True
    except Exception:
        pass

def _profile_ui(name):
    if name not in profiles:
        return {}
    ui = profiles[name].setdefault('ui', {})
    ui.setdefault('status', 'Đang chạy' if profiles[name].get('running') else 'Đã dừng')
    ui.setdefault('login', 'Chưa kiểm tra')
    ui.setdefault('proxy', 'Tắt' if not profiles[name].get('config', {}).get('use_proxy') else 'Chưa kiểm tra')
    ui.setdefault('browser', 'Chưa mở')
    ui.setdefault('upload', 'Chờ video')
    ui.setdefault('last_error', '')
    return ui

def request_profile_refresh(delay_ms=150):
    """Coalesce table refreshes: at most one pending redraw."""
    global _profile_refresh_pending
    if _profile_refresh_pending:
        return
    _profile_refresh_pending = True
    try:
        root.after(delay_ms, _flush_profile_refresh)
    except Exception:
        _profile_refresh_pending = False
        try:
            update_profile_list()
        except Exception:
            pass


def _flush_profile_refresh():
    global _profile_refresh_pending
    _profile_refresh_pending = False
    try:
        update_profile_list()
    except Exception:
        pass


def _update_action_buttons(*_):
    """Enable/disable Start/Stop/Check Cookie for the current selection."""
    try:
        sel = tree.selection()
        if not sel:
            _set_buttons_state(("btn_start_selected", "btn_stop_selected", "btn_check_cookie"), "disabled")
            return
        start_ok = stop_ok = check_ok = False
        for iid in sel:
            name = tree.item(iid, 'values')[0]
            profile = profiles.get(name)
            if profile is None:
                continue
            snapshot = _build_profile_snapshot(name, profile)
            if snapshot.can_start:
                start_ok = True
            if snapshot.can_stop:
                stop_ok = True
            if snapshot.can_check_cookie:
                check_ok = True
        _set_buttons_state(("btn_start_selected",), "normal" if start_ok else "disabled")
        _set_buttons_state(("btn_stop_selected",), "normal" if stop_ok else "disabled")
        _set_buttons_state(("btn_check_cookie",), "normal" if check_ok else "disabled")
    except Exception:
        pass


def _set_profile_ui(name, refresh=True, **fields):
    if name not in profiles:
        return
    ui = _profile_ui(name)
    changed = False
    for key, value in fields.items():
        if value is not None:
            new_value = str(value)
            if ui.get(key) != new_value:
                ui[key] = new_value
                changed = True
    if refresh and changed:
        try:
            sp = selected_project_var.get() if 'selected_project_var' in globals() else ALL_OPTION
            kw = filter_var.get().strip() if 'filter_var' in globals() else ""
            chip = active_filter_chip_var.get() if 'active_filter_chip_var' in globals() else "ALL"
            if sp == ALL_OPTION and not kw and chip == "ALL" and 'tree' in globals() and tree.winfo_exists():
                cfg = profiles[name].get('config', {})
                uuid = ensure_account_uuid(cfg)
                if uuid in tree.get_children(''):
                    from profile_table_engine import build_row_model
                    model = build_row_model(name, profiles[name], monetization_cache, ensure_account_uuid)
                    tree.item(uuid, values=model["values"], tags=model["tags"])
                    return
        except Exception:
            pass
        request_profile_refresh()

def _short_ui_text(value, max_len=80):
    text = str(value or '')
    return text if len(text) <= max_len else text[:max_len - 3] + '...'

def _profile_row_tag(ui, running):
    status = str(ui.get('status', '')).lower()
    login = str(ui.get('login', '')).lower()
    proxy = str(ui.get('proxy', '')).lower()
    browser = str(ui.get('browser', '')).lower()
    upload = str(ui.get('upload', '')).lower()
    last_error = str(ui.get('last_error', '')).strip().lower()

    if last_error:
        return ('tag_error',)

    error_values = (
        status == 'lỗi'
        or browser in ('bị lỗi', 'mất kết nối')
        or upload in ('đăng lỗi', 'bị kẹt')
        or login in ('cookie lỗi', 'cần đăng nhập lại')
        or proxy in ('sai ip', 'sai định dạng', 'proxy lỗi')
    )
    if error_values:
        return ('tag_error',)

    processing_values = (
        status in ('đang khởi động', 'đang dừng')
        or any(x in upload for x in ('đang', 'chờ', 'đã chọn', 'đã gửi'))
        or any(x in browser for x in ('đang mở', 'đang cấu hình', 'đang đóng'))
        or any(x in login for x in ('đang nạp', 'chưa kiểm tra'))
        or any(x in proxy for x in ('đang kiểm tra',))
    )
    if processing_values:
        return ('tag_processing',)

    if running:
        return ('tag_ready',)
    return ('tag_stopped',)

def _refresh_status_bar():
    total_all = total_records_var.get() if 'total_records_var' in globals() else sum(1 for _ in tree.get_children(''))
    total_on_page = sum(1 for _ in tree.get_children(''))
    running = sum(1 for p in profiles.values() if p.get('running'))
    status_count_label.configure(text=f"Hiển thị: {total_on_page}/{total_all} | Đang chạy: {running}")
    clock_label.configure(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    try:
        header_total_label.set(str(total_all))
        header_running_label.set(str(running))
        header_project_label.set(selected_project_var.get() or ALL_OPTION)

        from ui_components import calculate_summary_counts
        counts = calculate_summary_counts(
            profiles,
            active_project=selected_project_var.get() or ALL_OPTION,
            filter_text=filter_var.get(),
        )
        if 'summary_cookie_var' in ui_state:
            ui_state['summary_cookie_var'].set(str(counts["cookie_live"]))
        if 'summary_error_var' in ui_state:
            ui_state['summary_error_var'].set(str(counts["errors"]))

        if 'project_list_view' in ui_widgets:
            proj_counts = {ALL_OPTION: len(profiles)}
            for p_name in projects:
                proj_counts[p_name] = sum(
                    1 for p in profiles.values()
                    if p.get('config', {}).get('project_name', 'Mặc định') == p_name
                )
            ui_widgets['project_list_view'].update_projects(
                proj_counts,
                active_project=selected_project_var.get() or ALL_OPTION,
            )
    except Exception:
        pass

def _first_run_download_check():
    """Kiểm tra và tải tài nguyên thiếu (Browser, FFmpeg, ngrok.exe)."""
    from version import RESOURCE_ASSETS, RESOURCE_RELEASE_VERSION
    import youtube_monitor.ffmpeg_helper as fh
    import youtube_monitor.ngrok_helper as nh

    missing = {}
    for local_name, info in RESOURCE_ASSETS.items():
        path = app_base_dir() / local_name
        if not path.exists():
            missing[local_name] = info

    # Upgrade case: Browser folder exists but engine is absent or outdated/unpatched
    browser_info = RESOURCE_ASSETS.get("Browser") or {}
    from browser_engine_manager import verify_installed_engine_compatibility, clean_legacy_browser_engines
    is_engine_compat, compat_msg = verify_installed_engine_compatibility(app_base_dir())
    if "Browser" not in missing:
        if not is_engine_compat:
            clean_legacy_browser_engines(app_base_dir(), remove_primary=True)
            missing["Browser"] = browser_info
        elif browser_info.get("validate"):
            if any(not (app_base_dir() / p).exists() for p in browser_info["validate"]):
                clean_legacy_browser_engines(app_base_dir(), remove_primary=True)
                missing["Browser"] = browser_info

    ffmpeg_ok, ffmpeg_msg, ffmpeg_src = fh.check_ffmpeg()
    ffmpeg_needed = not ffmpeg_ok

    ngrok_ok, ngrok_msg, ngrok_src = nh.check_ngrok()
    ngrok_needed = not ngrok_ok

    if not missing and not ffmpeg_needed and not ngrok_needed:
        return

    if list(missing) == ["Browser"] and not ffmpeg_needed and not ngrok_needed:
        _start_browser_engine_download()
        return

    items_list = [f"- {name}" for name in missing]
    if ffmpeg_needed:
        items_list.append("- FFmpeg")
    if ngrok_needed:
        items_list.append("- ngrok.exe (Tunnel WebSub YouTube)")
    if "Browser" in missing:
        items_list.append("  (Browser ~700 MB sau khi giải nén; tải về sẽ giải nén và xác minh)")
    popup_msg = "Cần tải tài nguyên lần đầu:\n" + "\n".join(items_list) + "\n\nTiếp tục?"

    if not messagebox.askyesno("Tải tài nguyên", popup_msg):
        return

    dlg = ctk.CTkToplevel(root)
    dlg.title("Đang tải tài nguyên lần đầu...")
    fit_and_center_dialog(dlg, 480, 180, parent=root, min_w=400, min_h=160)
    dlg.grab_set()
    dlg.resizable(False, False)
    label = ctk.CTkLabel(dlg, text="Đang tải...", font=("", 13))
    label.pack(pady=(16, 8))
    progress = ctk.CTkProgressBar(dlg, width=380)
    progress.pack(pady=8)
    progress.set(0)

    def _update_status(text, pct):
        try:
            root.after(0, lambda t=text, p=pct: _do_update(t, p))
        except Exception:
            pass

    def _do_update(text, pct):
        try:
            label.configure(text=text)
            progress.set(pct)
        except Exception:
            pass

    def _done(success, msg):
        try:
            dlg.destroy()
        except Exception:
            pass
        if not success:
            messagebox.showerror("Lỗi", f"Tải tài nguyên thất bại:\n{msg}")

    def _run():
        def _cleanup_part(part):
            try:
                p = Path(part)
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        try:
            total = len(missing) + (1 if ffmpeg_needed else 0) + (1 if ngrok_needed else 0)
            i = 0
            resource_tag = f"v{RESOURCE_RELEASE_VERSION}"
            base_url = f"https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/download/{resource_tag}"
            for local_name, info in missing.items():
                i += 1
                _update_status(f"Đang tải {local_name}...", (i - 0.9) / total)
                asset_name = info["asset"].format(version=RESOURCE_RELEASE_VERSION)
                url = f"{base_url}/{asset_name}"
                try:
                    if info["type"] == "zip_dir":
                        temp_dir = app_base_dir() / "temp_dl" / "resources"
                        temp_dir.mkdir(parents=True, exist_ok=True)
                        part_path = temp_dir / (asset_name + ".part")
                        zip_path = temp_dir / asset_name
                        zip_path.unlink(missing_ok=True)
                        _cleanup_part(part_path)
                        updater = GitHubReleaseUpdater(app_base_dir(), GITHUB_REPO_OWNER, GITHUB_REPO_NAME)
                        updater.download_asset(url, part_path)
                        os.replace(part_path, zip_path)
                        expected_sha = (info.get("sha256") or "").strip().lower()
                        if expected_sha and expected_sha != "placeholder":
                            from browser_engine_manager import compute_sha256
                            if compute_sha256(zip_path) != expected_sha:
                                raise RuntimeError(f"Checksum SHA-256 không khớp cho {asset_name}")
                        _update_status(f"Đang giải nén {local_name}...", (i - 0.4) / total)
                        extract_temp = temp_dir / f"extract_{local_name}"
                        shutil.rmtree(extract_temp, ignore_errors=True)
                        extract_temp.mkdir(parents=True, exist_ok=True)
                        root_dir = extract_temp.resolve()
                        with zipfile.ZipFile(zip_path, "r") as zf:
                            for member in zf.infolist():
                                target = (root_dir / member.filename).resolve()
                                if os.path.commonpath([str(root_dir), str(target)]) != str(root_dir):
                                    raise RuntimeError(f"ZIP path traversal detected: {member.filename}")
                            zf.extractall(extract_temp)
                        dest = app_base_dir() / local_name
                        dest_backup = app_base_dir() / f"{local_name}.bak"
                        dest_backup_exists = dest.exists()
                        if dest_backup_exists:
                            shutil.rmtree(dest_backup, ignore_errors=True)
                            shutil.copytree(dest, dest_backup)
                        shutil.rmtree(dest, ignore_errors=True)
                        try:
                            items = list(extract_temp.iterdir())
                            if len(items) == 1 and items[0].is_dir():
                                shutil.copytree(items[0], dest)
                            else:
                                shutil.copytree(extract_temp, dest)
                            for validate_path in info.get("validate", []):
                                if not (app_base_dir() / validate_path).exists():
                                    raise RuntimeError(f"Thiếu file bắt buộc: {validate_path}")
                            shutil.rmtree(dest_backup, ignore_errors=True)
                        except Exception:
                            if dest_backup_exists:
                                shutil.rmtree(dest, ignore_errors=True)
                                shutil.copytree(dest_backup, dest)
                                shutil.rmtree(dest_backup, ignore_errors=True)
                            elif not dest.exists() or not any(dest.iterdir()):
                                shutil.rmtree(dest, ignore_errors=True)
                            raise
                        finally:
                            shutil.rmtree(extract_temp, ignore_errors=True)
                            zip_path.unlink(missing_ok=True)
                            _cleanup_part(part_path)
                    else:
                        dest = app_base_dir() / local_name
                        part_path = dest.with_suffix(dest.suffix + ".part")
                        _cleanup_part(part_path)
                        updater = GitHubReleaseUpdater(app_base_dir(), GITHUB_REPO_OWNER, GITHUB_REPO_NAME)
                        updater.download_asset(url, part_path)
                        if not part_path.exists() or part_path.stat().st_size == 0:
                            _cleanup_part(part_path)
                            raise RuntimeError(f"Tải {local_name} thất bại: file rỗng hoặc không tồn tại.")
                        dest_backup = app_base_dir() / f"{local_name}.bak"
                        dest_backup_exists = dest.exists()
                        if dest_backup_exists:
                            shutil.copy2(dest, dest_backup)
                        try:
                            os.replace(part_path, dest)
                            if not dest.exists() or dest.stat().st_size == 0:
                                raise RuntimeError(f"Xác minh {local_name} thất bại")
                            if dest_backup_exists:
                                dest_backup.unlink(missing_ok=True)
                        except Exception:
                            if dest_backup_exists and dest_backup.exists():
                                shutil.move(str(dest_backup), str(dest))
                            else:
                                _cleanup_part(part_path)
                                dest.unlink(missing_ok=True)
                            raise
                        finally:
                            _cleanup_part(part_path)
                except Exception as e:
                    _cleanup_part(part_path)
                    raise
                _update_status(f"Đã tải {local_name}.", i / total)

            if ffmpeg_needed:
                i += 1
                _update_status("Đang tải FFmpeg...", (i - 0.9) / total)
                def _ff_progress(text, pct):
                    _update_status(text, (i - 1 + pct) / total)
                ff_ok, ff_msg = fh.ensure_ffmpeg(progress_callback=_ff_progress)
                if not ff_ok:
                    raise RuntimeError(f"FFmpeg: {ff_msg}")
                _update_status("Đã tải FFmpeg.", i / total)

            if ngrok_needed:
                i += 1
                _update_status("Đang tải ngrok.exe...", (i - 0.9) / total)
                def _ng_progress(text, pct):
                    _update_status(text, (i - 1 + pct) / total)
                ng_ok, ng_msg = nh.ensure_ngrok(progress_callback=_ng_progress)
                if not ng_ok:
                    raise RuntimeError(f"Ngrok: {ng_msg}")
                _update_status("Đã tải ngrok.exe.", i / total)

            root.after(500, lambda: _done(True, ""))
        except requests.RequestException as e:
            error_msg = f"Lỗi mạng: {e}"
            root.after(0, lambda msg=error_msg: _done(False, msg))
        except Exception as e:
            error_msg = str(e).strip() or "Lỗi không xác định"
            root.after(0, lambda msg=error_msg: _done(False, msg))

    threading.Thread(target=_run, daemon=True).start()


def _start_browser_engine_download(clean_first: bool = True):
    """Tải Dong Lao TikTok Browser 144 qua dialog có progress, checksum và giải nén atomic."""
    from version import (
        RESOURCE_ASSETS,
        RESOURCE_RELEASE_VERSION,
        RESOURCE_BROWSER_ENGINE_DIR,
    )
    from browser_engine_manager import clean_legacy_browser_engines
    from ui_browser_downloader import BrowserEngineDownloadDialog

    if clean_first:
        clean_legacy_browser_engines(app_base_dir(), remove_primary=True)

    info = RESOURCE_ASSETS.get("Browser") or {}
    asset_name = info.get("asset", "Browser-v{version}.zip").format(version=RESOURCE_RELEASE_VERSION)
    expected_sha = (info.get("sha256") or "").strip().lower()
    if expected_sha == "placeholder":
        expected_sha = ""
    download_url = (
        f"https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
        f"/releases/download/v{RESOURCE_RELEASE_VERSION}/{asset_name}"
    )

    def _on_complete(success, message):
        if success:
            update_status("Browser Dong Lao TikTok 144 đã sẵn sàng.")
        else:
            update_status(f"Lỗi tải Browser: {message}")

    BrowserEngineDownloadDialog(
        parent=root,
        download_url=download_url,
        target_engine_name=RESOURCE_BROWSER_ENGINE_DIR,
        expected_sha256=expected_sha or None,
        on_complete=_on_complete,
    )


def _stop_all_profiles():
    for name in list(profiles.keys()):
        lc = get_lifecycle(name)
        if profiles.get(name, {}).get("running") or lc.has_active_driver() or _browser_session_valid(lc.get_automation_driver()) or _browser_session_valid(lc.get_manual_driver()):
            lc.cancel()
    for name in list(profiles.keys()):
        try:
            stop_profile(name)
        except Exception:
            pass
    after_kill_cleanup_running_profiles()
    return len(running_profiles) == 0


_update_ui_queue = queue.Queue()


def _enqueue_update_ui(callback):
    _update_ui_queue.put(callback)


MAX_UI_CALLBACKS_PER_TICK = 25
MAX_UI_TIME_BUDGET_MS = 10.0


def _drain_update_ui_queue():
    start_t = time.perf_counter()
    processed_count = 0
    while processed_count < MAX_UI_CALLBACKS_PER_TICK:
        if (time.perf_counter() - start_t) * 1000.0 >= MAX_UI_TIME_BUDGET_MS:
            break
        try:
            callback = _update_ui_queue.get_nowait()
        except queue.Empty:
            break
        try:
            callback()
        except Exception as error:
            update_status(f"[Update] Lỗi giao diện cập nhật: {error}")
        processed_count += 1

    has_more = not _update_ui_queue.empty()
    next_delay = 10 if has_more else 50
    try:
        root.after(next_delay, _drain_update_ui_queue)
    except Exception:
        pass


def _run_background_update_check():
    from updater import run_background_check as _bg_check

    updater_config = load_updater_config()
    if not updater_config.get('auto_check', True):
        return
    remind_after = int(updater_config.get('remind_after_epoch', 0) or 0)
    remaining = remind_after - int(time.time())
    if remaining > 0:
        root.after(remaining * 1000, _run_background_update_check)
        return

    def _on_update(result):
        _show_update_available_dialog(result)

    _bg_check(GITHUB_REPO_OWNER, GITHUB_REPO_NAME, "",
              app_base_dir(),
              on_update=_on_update,
              on_error=lambda err: None,
              on_current=lambda ver: None,
              schedule=_enqueue_update_ui)


def check_update_clicked():
    if not _license_guard():
        return
    _do_check_update()


def _do_check_update():
    def _on_result(result):
        if result.get("error"):
            update_status(f"[Update] Lỗi: {result['error']}")
            messagebox.showerror("Cập nhật", f"Lỗi kiểm tra: {result['error']}")
            return
        if result.get("current"):
            update_status(f"[Update] Đang dùng phiên bản mới nhất ({result['current_version']}).")
            messagebox.showinfo("Cập nhật", f"Đang dùng phiên bản mới nhất ({result['current_version']}).")
            return
        if result.get("has_update"):
            _show_update_available_dialog(result)

    def _on_error(err):
        update_status(f"[Update] Lỗi: {err}")
        messagebox.showerror("Cập nhật", err)

    update_status("[Update] Đang kiểm tra phiên bản mới...")

    def _run():
        try:
            updater = GitHubReleaseUpdater(app_base_dir(), GITHUB_REPO_OWNER, GITHUB_REPO_NAME,
                                           log_func=lambda m: update_status(f"[Update] {m}"))
            result = updater.check_update()
            _enqueue_update_ui(lambda result=result: _on_result(result))
        except Exception as e:
            error_message = str(e)
            _enqueue_update_ui(lambda error_message=error_message: _on_error(error_message))

    threading.Thread(target=_run, daemon=True).start()


_update_available_dialog = None


def _valid_release_url(url):
    expected = f"https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/"
    text = str(url or '').strip()
    return text if text.startswith(expected) else ''


def _show_update_available_dialog(result):
    global _update_available_dialog
    latest = result.get("latest_version", "?")
    current = result.get("current_version", "?")
    running_count = sum(1 for n in running_profiles if profiles.get(n, {}).get("running"))
    notes = str(result.get('release_notes') or 'Bản cập nhật mới giúp ứng dụng ổn định và dễ sử dụng hơn.')
    release_url = _valid_release_url(result.get('release_url'))

    try:
        if _update_available_dialog and _update_available_dialog.winfo_exists():
            _update_available_dialog.lift()
            _update_available_dialog.focus_force()
            return
    except Exception:
        _update_available_dialog = None

    dlg = ctk.CTkToplevel(root)
    _update_available_dialog = dlg
    dlg.title(f"Có phiên bản mới v{latest}")
    fit_and_center_dialog(dlg, 680, 560, parent=root, min_w=520, min_h=420)
    dlg.grab_set()
    dlg.focus_force()

    header = ctk.CTkFrame(dlg, fg_color="#eff6ff", corner_radius=10)
    header.pack(fill='x', padx=16, pady=(16, 10))
    ctk.CTkLabel(
        header,
        text=f"Đã có phiên bản mới v{latest}",
        font=("", 20, "bold"),
        text_color="#1d4ed8",
    ).pack(anchor='w', padx=16, pady=(12, 2))
    ctk.CTkLabel(
        header,
        text=f"Bạn đang sử dụng phiên bản v{current}.",
        text_color="#475569",
    ).pack(anchor='w', padx=16, pady=(0, 12))

    ctk.CTkLabel(dlg, text="Thông tin cập nhật", font=("", 14, "bold")).pack(anchor='w', padx=18, pady=(4, 6))
    notes_box = ctk.CTkTextbox(dlg, wrap='word', font=("", 13))
    notes_box.pack(fill='both', expand=True, padx=16, pady=(0, 10))
    notes_box.insert('1.0', notes)
    notes_box.configure(state='disabled')

    status_text = "Ứng dụng sẽ tự đóng và mở lại sau khi cập nhật."
    if running_count > 0:
        status_text = f"Sẽ dừng {running_count} profile đang chạy trước khi cập nhật."
    if not getattr(sys, 'frozen', False):
        status_text = "Bản mã nguồn chỉ mở trang tải; tự cập nhật áp dụng cho bản đã đóng gói."
    ctk.CTkLabel(dlg, text=status_text, text_color="#64748b").pack(anchor='w', padx=18, pady=(0, 8))

    buttons = ctk.CTkFrame(dlg, fg_color='transparent')
    buttons.pack(fill='x', padx=16, pady=(0, 16))

    def close_dialog():
        global _update_available_dialog
        try:
            dlg.grab_release()
            dlg.destroy()
        except Exception:
            pass
        _update_available_dialog = None

    def skip_release():
        try:
            update_updater_config(skip_version=str(latest), remind_after_epoch=0)
            update_status(f"[Update] Đã bỏ qua phiên bản v{latest}.")
            close_dialog()
        except Exception as error:
            messagebox.showerror("Cập nhật", str(error))

    def remind_later():
        try:
            delay_seconds = 6 * 60 * 60
            update_updater_config(remind_after_epoch=int(time.time()) + delay_seconds)
            close_dialog()
            root.after(delay_seconds * 1000, _run_background_update_check)
            update_status("[Update] Sẽ nhắc lại sau 6 giờ.")
        except Exception as error:
            messagebox.showerror("Cập nhật", str(error))

    def view_release():
        if release_url:
            webbrowser.open(release_url)

    def install_release():
        if not getattr(sys, 'frozen', False):
            view_release()
            messagebox.showinfo("Cập nhật", "Đã mở trang phát hành. Tự cập nhật chỉ dùng cho bản ứng dụng đã đóng gói.")
            return
        close_dialog()
        if running_count > 0:
            update_status("[Update] Đang dừng profiles...")
            _stop_all_profiles()
            after_kill_cleanup_running_profiles()
        try:
            update_updater_config(skip_version='', remind_after_epoch=0)
        except Exception as error:
            messagebox.showerror("Cập nhật", str(error))
            return
        _do_download_update(result)

    ctk.CTkButton(buttons, text="Cập nhật ngay", command=install_release, fg_color="#2563eb", hover_color="#1d4ed8").pack(side='right', padx=(8, 0))
    ctk.CTkButton(buttons, text="Nhắc lại sau", command=remind_later, fg_color="#64748b", hover_color="#475569").pack(side='right', padx=(8, 0))
    ctk.CTkButton(buttons, text="Bỏ qua bản này", command=skip_release, fg_color="#94a3b8", hover_color="#64748b").pack(side='right', padx=(8, 0))
    if release_url:
        ctk.CTkButton(buttons, text="Xem chi tiết", command=view_release, fg_color='transparent', text_color="#2563eb", border_width=1, border_color="#93c5fd").pack(side='left')
    dlg.protocol("WM_DELETE_WINDOW", close_dialog)


def _do_download_update(result):
    if not getattr(sys, 'frozen', False):
        messagebox.showinfo("Cập nhật", "Tự cập nhật chỉ khả dụng trên bản ứng dụng đã đóng gói.")
        return
    asset_url = result.get("asset_url")
    if not asset_url:
        messagebox.showerror("Cập nhật", "Không có URL tải về.")
        return

    dlg = ctk.CTkToplevel(root)
    dlg.title("Đang tải bản cập nhật...")
    fit_and_center_dialog(dlg, 420, 150, parent=root, min_w=380, min_h=130)
    dlg.grab_set()
    dlg.resizable(False, False)

    ctk.CTkLabel(dlg, text=f"Đang tải {result.get('asset_name', 'file')}...", font=("", 13)).pack(pady=(16, 8))
    progress = ctk.CTkProgressBar(dlg, width=320)
    progress.pack(pady=8)
    progress.set(0)

    def _set_progress(ratio):
        try:
            progress.set(ratio)
            dlg.update_idletasks()
        except Exception:
            pass

    progress_state = {'latest': 0.0, 'queued': False}
    progress_lock = threading.Lock()

    def _flush_progress():
        with progress_lock:
            ratio = progress_state['latest']
            progress_state['queued'] = False
        _set_progress(ratio)

    def _progress(ratio):
        with progress_lock:
            progress_state['latest'] = ratio
            if progress_state['queued']:
                return
            progress_state['queued'] = True
        _enqueue_update_ui(_flush_progress)

    def _done(success, msg):
        try:
            dlg.destroy()
        except Exception:
            pass
        if success:
            update_status(f"[Update] {msg}")
        else:
            update_status(f"[Update] Lỗi: {msg}")
            messagebox.showerror("Cập nhật", msg)

    def _run():
        try:
            app_root = app_base_dir()
            updater = GitHubReleaseUpdater(app_root, GITHUB_REPO_OWNER, GITHUB_REPO_NAME)

            temp_dir = app_root / "temp_dl" / "update"
            temp_dir.mkdir(parents=True, exist_ok=True)
            zip_path = temp_dir / f"{APP_NAME}-update.zip"

            updater.download_asset(asset_url, zip_path, progress_callback=_progress)

            extract_dir = temp_dir / "extracted"
            updater.extract_update(zip_path, extract_dir)

            if not updater.validate_package(extract_dir):
                shutil.rmtree(str(extract_dir), ignore_errors=True)
                _enqueue_update_ui(lambda: _done(False, "File tải về không hợp lệ (thiếu exe hoặc _internal)."))
                return

            script = updater.write_update_script(extract_dir)
            def _launch_when_ready():
                _done(True, "Sẵn sàng cập nhật. Ứng dụng sẽ tự động đóng và khởi động lại.")
                def _shutdown_and_launch_update():
                    try:
                        on_closing()
                    finally:
                        updater.launch_update(script)
                root.after(2000, _shutdown_and_launch_update)
            _enqueue_update_ui(_launch_when_ready)
        except Exception as e:
            error_message = str(e)
            _enqueue_update_ui(lambda error_message=error_message: _done(False, error_message))

    threading.Thread(target=_run, daemon=True).start()


def _profile_region(cfg):
    fp = cfg.get('fingerprint', {}) or {}
    code = str(fp.get('geo_country_code', '') or '').strip()
    if code:
        return code
    country = str(fp.get('geo_country', '') or '').strip()
    if country:
        return country[:2].upper()
    return ''


def _health_summary(ui, running):
    tag = _profile_row_tag(ui, running)
    if 'tag_error' in tag:
        return 'Lỗi'
    if 'tag_processing' in tag:
        return 'Đang xử lý'
    if running:
        return 'Đang chạy'
    return 'Đã dừng'


def _build_profile_snapshot(name, profile):
    cfg = profile.get('config', {})
    ui = _profile_ui(name)
    return build_runtime_snapshot(RuntimeSignals(
        running=bool(profile.get('running', False)),
        observer_active=bool(profile.get('observer')),
        driver_alive=_browser_session_valid(profile.get('driver')),
        manual_driver_alive=_browser_session_valid(profile.get('manual_driver')),
        session_busy=bool(profile.get('session_busy', False)),
        uploading=bool(profile.get('uploading', False)),
        operation=profile.get('operation', OperationState.IDLE.value),
        has_error=bool(str(ui.get('last_error', '') or '').strip()),
        blocked_conflict=_blocked_by_profile_conflict(name),
        ui_status=ui.get('status', ''),
        ui_browser=ui.get('browser', ''),
        ui_upload=ui.get('upload', ''),
    ))


def _reapply_tree_sort():
    if _tree_sort_state is None:
        return
    col, reverse = _tree_sort_state
    try:
        _treeview_sort_column(tree, col, reverse)
    except Exception:
        pass


def _update_pagination_ui(cur_page, total_pages, total_records):
    try:
        if 'ui_widgets' not in globals():
            return
        lbl = ui_widgets.get('pagination_page_info_label')
        if lbl:
            if total_records == 0:
                lbl.configure(text="Không có hồ sơ nào")
            else:
                lbl.configure(text=f"Trang {cur_page} / {total_pages}   (Tổng {total_records} hồ sơ)")

        btn_f = ui_widgets.get('pagination_btn_first')
        if btn_f:
            btn_f.configure(state="normal" if cur_page > 1 else "disabled")

        btn_p = ui_widgets.get('pagination_btn_prev')
        if btn_p:
            btn_p.configure(state="normal" if cur_page > 1 else "disabled")

        btn_n = ui_widgets.get('pagination_btn_next')
        if btn_n:
            btn_n.configure(state="normal" if cur_page < total_pages else "disabled")

        btn_l = ui_widgets.get('pagination_btn_last')
        if btn_l:
            btn_l.configure(state="normal" if cur_page < total_pages else "disabled")
    except Exception:
        pass


def update_profile_list(*args):
    sp = selected_project_var.get()
    kw = filter_var.get().strip().lower()
    if sp == ALL_OPTION:
        iter_names = sorted(profiles.keys())
    else:
        proj_members = set(projects.get(sp, []))
        for p_k, p_v in profiles.items():
            if (p_v.get('config', {}) or {}).get('project_name') == sp:
                proj_members.add(p_k)
        iter_names = sorted(proj_members)
    iter_names = [n for n in iter_names if n in profiles]

    filtered_matching_items = []
    active_chip = active_filter_chip_var.get() if 'active_filter_chip_var' in globals() else "ALL"

    for name in iter_names:
        ui = _profile_ui(name)
        running = profiles[name]['running']
        cfg = profiles[name]['config']
        lim = str(cfg.get('max_uploads_per_day', 0)) if cfg.get('max_uploads_per_day', 0) > 0 else "Không"
        tiktok_id = str(cfg.get('tiktok_id', '') or cfg.get('tiktok_account', '') or '').lstrip('@')
        tiktok_display = f"@{tiktok_id}" if tiktok_id else ""
        region = _profile_region(cfg)
        snapshot = _build_profile_snapshot(name, profiles[name])

        snap_mono = monetization_cache.get(name, {})
        auth_state = str(cfg.get('session_auth_state', '')).lower()
        cookie_raw = str(cfg.get('cookie_str', '') or '').strip()
        login_ui = str(ui.get('login', '')).lower()
        payout_st = snap_mono.get('payout_status', '')
        kyc_st = snap_mono.get('kyc_status', '')
        tax_st = snap_mono.get('tax_status', '')
        crp_st = snap_mono.get('crp_status', '')
        mono_st = snap_mono.get('status', '')
        inspection = cfg.get('tiktok_inspection', {}) or {}

        # 1. NO_COOKIE: Thực sự không có chuỗi cookie
        is_no_cookie = (not cookie_raw) or (cookie_raw in ("[]", "{}", "null"))

        # 2. COOKIE_LIVE: Có cookie và được xác thực hợp lệ (qua API/Browser)
        is_cookie_live = (
            not is_no_cookie
            and (
                auth_state in ("live", "verified")
                or mono_st == "SUCCESS"
                or payout_st in ("PAYOUT_READY", "PAYOUT_NOT_LINKED", "CRP_ACTIVE")
                or browser_label(snapshot.browser) == "Sẵn sàng"
                or inspection.get("state") == "VALID"
            )
            and auth_state not in ("expired", "invalid", "dead")
        )

        # 3. COOKIE_DIE: Có cookie nhưng hết hạn hoặc lỗi đăng nhập
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

        # 4. KYC_OK: Đã vượt qua xác minh danh tính
        is_kyc_ok = (
            kyc_st == "APPROVED"
            or inspection.get("identity", {}).get("verified") is True
            or inspection.get("payout", {}).get("verification_status") == "APPROVED"
        )

        # 5. TAX_OK: Đã hoàn tất hồ sơ thuế
        is_tax_ok = (
            tax_st in ("TAX_VERIFIED", "APPROVED")
            or inspection.get("payout", {}).get("payout_status") in ("VERIFIED", "TAX_VERIFIED")
        )

        # 6. TKTBM: Bị hạn chế bảo mật
        is_tktbm = (
            crp_st == "TKTBM"
            or "tktbm" in str(inspection).lower()
        )

        # 7. RUNNING: Đang chạy tác vụ
        driver_alive = _browser_session_valid(profiles[name].get('driver'))
        manual_driver_alive = _browser_session_valid(profiles[name].get('manual_driver'))
        uploading = bool(profiles[name].get('uploading'))
        is_running = running or snapshot.can_stop or driver_alive or manual_driver_alive or uploading

        # Badge text format cho các cột bảng
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

        proxy_str = ui.get('proxy', '')
        proxy_region_badge = f"[{region}] {proxy_str}" if (region and proxy_str) else (proxy_str or region or "Tắt")

        # Khớp từ khóa tìm kiếm (Search filter)
        row_blob = (
            f"{name} {tiktok_id} {cookie_badge} {activity_badge} {mono_badge} "
            f"{proxy_str} {region} {upload_label(snapshot.upload)} "
            f"{ui.get('last_error','')} {cfg.get('folder_path','')}"
        ).lower()
        if kw and kw not in row_blob:
            continue

        # Lọc theo Active Filter Chip
        if active_chip == "COOKIE_LIVE" and not is_cookie_live:
            continue
        elif active_chip == "COOKIE_DIE" and not is_cookie_die:
            continue
        elif active_chip == "NO_COOKIE" and not is_no_cookie:
            continue
        elif active_chip == "KYC_OK" and not is_kyc_ok:
            continue
        elif active_chip == "TAX_OK" and not is_tax_ok:
            continue
        elif active_chip == "TKTBM" and not is_tktbm:
            continue
        elif active_chip == "RUNNING" and not is_running:
            continue

        filtered_matching_items.append((
            name,
            cfg,
            tiktok_display,
            cookie_badge,
            activity_badge,
            mono_badge,
            proxy_region_badge,
            snapshot,
            ui,
        ))

    # Pagination calculations
    total_records = len(filtered_matching_items)
    if 'total_records_var' in globals():
        total_records_var.set(total_records)

    page_size_str = str(page_size_var.get()) if 'page_size_var' in globals() else "10 / trang"
    if "10" in page_size_str and "100" not in page_size_str:
        page_size = 10
    elif "25" in page_size_str:
        page_size = 25
    elif "50" in page_size_str:
        page_size = 50
    elif "100" in page_size_str:
        page_size = 100
    elif "200" in page_size_str:
        page_size = 200
    else:
        page_size = max(1, total_records)

    import math
    total_pages = max(1, math.ceil(total_records / page_size)) if total_records > 0 else 1
    if 'total_pages_var' in globals():
        total_pages_var.set(total_pages)

    cur_page = current_page_var.get() if 'current_page_var' in globals() else 1
    cur_page = max(1, min(cur_page, total_pages))
    if 'current_page_var' in globals():
        current_page_var.set(cur_page)

    # Slice page items
    start_idx = (cur_page - 1) * page_size
    end_idx = start_idx + page_size
    paged_items = filtered_matching_items[start_idx:end_idx]

    row_map = {}
    order = []
    for item in paged_items:
        name, cfg, tiktok_display, cookie_badge, activity_badge, mono_badge, proxy_region_badge, snapshot, ui = item
        uuid = ensure_account_uuid(cfg)
        row_map[uuid] = (
            name,
            (
                name,
                tiktok_display,
                cookie_badge,
                activity_badge,
                mono_badge,
                proxy_region_badge,
                upload_label(snapshot.upload),
                cfg.get('folder_path', ''),
                _short_ui_text(ui.get('last_error', '')),
            ),
            row_tags(snapshot),
        )
        order.append(uuid)

    existing = set(tree.get_children(''))
    for iid in existing:
        if iid not in row_map:
            tree.delete(iid)
    for iid in order:
        name, values, tags = row_map[iid]
        if iid in existing:
            if tuple(tree.item(iid, 'values')) != values:
                tree.item(iid, values=values)
            if tuple(tree.item(iid, 'tags')) != tags:
                tree.item(iid, tags=tags)
        else:
            tree.insert('', 'end', iid=iid, values=values, tags=tags)

    if _tree_sort_state is None:
        for index, iid in enumerate(order):
            try:
                tree.move(iid, '', index)
            except Exception:
                pass
    else:
        _reapply_tree_sort()
    _apply_row_tags()
    _refresh_status_bar()
    _update_action_buttons()
    _update_pagination_ui(cur_page, total_pages, total_records)

# =========================
# Worker Functions (Batch)
# =========================
_batch_start_in_progress = False
_batch_stop_in_progress = False

def _thread_sequential_start(targets, context_name):
    global _batch_start_in_progress
    try:
        update_status(f"Bắt đầu khởi động {len(targets)} hồ sơ ({context_name})...")
        snapshots = {}
        for name in targets:
            if name in profiles:
                snapshots[name] = _build_profile_snapshot(name, profiles[name])
        startable, skipped = batch_start_preflight(snapshots, targets)
        summary = {"started": 0, "already": 0}
        skip_reasons = {}
        for name, _snapshot in startable:
            try:
                if name not in profiles or profiles[name]['running']:
                    summary['already'] += 1
                    continue
                if not check_system_resources(name):
                    update_status(f"[{name}] Bỏ qua (Low Res).")
                    skip_reasons["Tài nguyên thấp"] = skip_reasons.get("Tài nguyên thấp", 0) + 1
                    continue
                update_status(f"[{name}] Đang khởi động...")
                start_profile(name)
                summary['started'] += 1

                start_t = time.time()
                while time.time() - start_t < START_PROFILE_TIMEOUT:
                    if _browser_session_valid(profiles[name].get('driver')): break
                    if not profiles[name]['running']: break
                    time.sleep(1)

                if _browser_session_valid(profiles[name].get('driver')):
                    update_status(f"[{name}] OK ({time.time()-start_t:.1f}s).")
                    time.sleep(1)
                elif not profiles[name]['running']: pass
                else:
                    update_status(f"[{name}] Timeout. Dừng.")
                    stop_profile(name)
            except Exception as e:
                update_status(f"[{name}] Lỗi Batch Start: {e}")
                if profiles.get(name, {}).get('running'): stop_profile(name)
        for name, reason in skipped:
            update_status(f"[{name}] Bỏ qua: {reason}")
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        if skip_reasons:
            skip_summary = " | ".join(f"{reason}: {count}" for reason, count in skip_reasons.items())
            update_status(f"Bỏ qua {len(skipped)} hồ sơ ({context_name}): {skip_summary}")
        update_status(f"Hoàn tất khởi động batch ({context_name}): Đã start {summary['started']}, Đã chạy sẵn {summary['already']}, bỏ qua {len(skipped)}.")
    finally:
        _batch_start_in_progress = False
        try:
            root.after(0, lambda: _set_start_buttons_state("normal"))
        except Exception:
            _set_start_buttons_state("normal")

def _set_buttons_state(keys, state):
    for key in keys:
        try:
            btn = ui_widgets.get(key)
            if btn and btn.winfo_exists():
                btn.configure(state=state)
        except Exception:
            pass

def _set_start_buttons_state(state):
    _set_buttons_state(("btn_start_selected", "btn_start_all"), state)

def _set_stop_buttons_state(state):
    _set_buttons_state(("btn_stop_selected", "btn_stop_all"), state)

def _thread_sequential_stop(targets, context_name):
    global _batch_stop_in_progress
    try:
        update_status(f"Bắt đầu dừng {len(targets)} hồ sơ ({context_name})...")
        for name in targets:
            if name in profiles:
                lc = get_lifecycle(name)
                if profiles[name].get('running', False) or lc.has_active_driver() or _browser_session_valid(profiles[name].get('manual_driver')):
                    lc.cancel()
                lc.cancel()
        for name in targets:
            if name not in profiles: continue
            try:
                update_status(f"[{name}] Đang dừng...")
                stop_profile(name)
                time.sleep(0.5)
            except Exception as e:
                update_status(f"[{name}] Lỗi dừng: {e}")
        update_status(f"Hoàn tất dừng batch ({context_name}).")
        root.after(0, request_profile_refresh)
    finally:
        _batch_stop_in_progress = False
        try:
            root.after(0, lambda: (_set_stop_buttons_state("normal"), _set_start_buttons_state("normal") if not _batch_start_in_progress else None))
        except Exception:
            _set_stop_buttons_state("normal")
            if not _batch_start_in_progress:
                _set_start_buttons_state("normal")

# =========================
# Main Actions
# =========================
def start_selected_batch():
    if not _license_guard(): return
    global _batch_start_in_progress
    if _batch_start_in_progress or _batch_stop_in_progress:
        messagebox.showinfo("Đang xử lý", "Một lệnh khởi động batch khác đang chạy. Vui lòng đợi.")
        return
    selected = tree.selection()
    if not selected:
        messagebox.showerror("Lỗi", "Hãy chọn ít nhất 1 hồ sơ")
        return
    targets = [tree.item(i)['values'][0] for i in selected]
    _batch_start_in_progress = True
    _set_start_buttons_state("disabled")
    _set_stop_buttons_state("normal")
    threading.Thread(target=_thread_sequential_start, args=(targets, "Đã chọn"), daemon=True).start()

def stop_selected_batch():
    if not _license_guard(): return
    global _batch_stop_in_progress
    if _batch_stop_in_progress:
        messagebox.showinfo("Đang xử lý", "Một lệnh dừng batch khác đang chạy. Vui lòng đợi.")
        return
    selected = tree.selection()
    if not selected:
        messagebox.showerror("Lỗi", "Hãy chọn ít nhất 1 hồ sơ")
        return
    targets = [tree.item(i)['values'][0] for i in selected]
    _batch_stop_in_progress = True
    _set_start_buttons_state("disabled")
    _set_stop_buttons_state("disabled")
    threading.Thread(target=_thread_sequential_stop, args=(targets, "Đã chọn"), daemon=True).start()

def start_all_in_project():
    if not _license_guard(): return
    global _batch_start_in_progress
    if _batch_start_in_progress or _batch_stop_in_progress:
        messagebox.showinfo("Đang xử lý", "Một lệnh khởi động batch khác đang chạy. Vui lòng đợi.")
        return
    p = selected_project_var.get()
    targets = sorted(profiles.keys()) if p == ALL_OPTION else sorted(projects.get(p, []))
    if not targets:
        update_status("Không có hồ sơ nào.")
        return
    _batch_start_in_progress = True
    _set_start_buttons_state("disabled")
    _set_stop_buttons_state("normal")
    threading.Thread(target=_thread_sequential_start, args=(targets, p), daemon=True).start()

def stop_all_in_project():
    if not _license_guard(): return
    global _batch_stop_in_progress
    if _batch_stop_in_progress:
        messagebox.showinfo("Đang xử lý", "Một lệnh dừng batch khác đang chạy. Vui lòng đợi.")
        return
    p = selected_project_var.get()
    targets = sorted(profiles.keys()) if p == ALL_OPTION else sorted(projects.get(p, []))
    if not targets:
        update_status("Không có hồ sơ nào.")
        return
    _batch_stop_in_progress = True
    _set_start_buttons_state("disabled")
    _set_stop_buttons_state("disabled")
    threading.Thread(target=_thread_sequential_stop, args=(targets, p), daemon=True).start()

# =========================
# Single Profile Actions
# =========================
def start_profile(name=None):
    if not _license_guard(): return
    if name is None:
        sel = tree.selection()
        if not sel:
            messagebox.showerror("Lỗi", "Chọn 1 hồ sơ")
            return
        name = tree.item(sel[0])['values'][0]
    
    if name not in profiles or profiles[name]['running']:
        return
    if profiles[name].get('session_busy') or _browser_session_valid(profiles[name].get('manual_driver')):
        update_status(f"[{name}] Không thể Start khi browser thủ công hoặc thao tác session đang hoạt động.")
        _set_profile_ui(name, status='Lỗi', last_error='Profile đang được sử dụng bởi thao tác session khác')
        return
    
    config = profiles[name]['config']

    if _blocked_by_profile_conflict(name):
        message = _profile_conflict_message(name)
        _set_profile_ui(name, status='Lỗi', browser='Bị lỗi', last_error=message)
        update_status(f"[{name}] Không thể khởi động: {message}.")
        return

    duplicate_profile = _find_profile_with_same_data_dir(name)
    if duplicate_profile:
        message = f"Chrome profile trùng với hồ sơ đang chạy: {duplicate_profile}"
        _set_profile_ui(name, status='Lỗi', browser='Bị lỗi', last_error=message)
        update_status(f"[{name}] Không thể khởi động: {message}.")
        return

    lc = get_lifecycle(name)
    if lc.has_active_driver():
        stale_tokens = (lc.get_automation_driver(), lc.get_manual_driver())
        if any(_browser_session_valid(token) for token in stale_tokens):
            message = 'Lifecycle vẫn còn browser đang hoạt động'
            _set_profile_ui(name, status='Lỗi', browser='Bị lỗi', last_error=message)
            update_status(f"[{name}] Không thể khởi động: {message}.")
            return
        lc.cleanup_fast()
    start_gen = lc.begin()

    auto_created = False
    if not os.path.exists(config["folder_path"]):
        try:
            os.makedirs(config["folder_path"], exist_ok=True)
            auto_created = True
        except Exception as e:
            update_status(f"[{name}] Không thể tạo folder video: {e}")

    try:
        browser_glue.ensure_patchright_profile(config)
        _sync_patchright_migration(config)
        save_configs()
    except Exception as error:
        lc.cancel()
        _set_profile_ui(name, status='Lỗi', browser='Bị lỗi', last_error=str(error))
        update_status(f"[{name}] Không thể tạo/resume Profile-Patchright: {error}")
        return

    if not os.path.exists(config["folder_path"]) or not os.path.exists(config["browser_profile_path"]):
        update_status(f"[{name}] Đường dẫn không hợp lệ.")
        return

    if auto_created:
        update_status(f"[{name}] Đã tự động tạo thư mục còn thiếu.")

    profiles[name]['running'] = True
    running_profiles.add(name)
    _set_profile_ui(name, status='Đang khởi động', browser='Đang mở', upload='Chờ video', last_error='', refresh=False)
    update_profile_list()

    def _worker():
        try:
            if name not in profiles or not profiles[name].get('running', False) or lc.is_cancelled or not lc.is_current(start_gen):
                return

            if config.get('open_only_when_video', False):
                profiles[name]['watch_started_at'] = time.time()
                try:
                    mgr = get_watchdog_manager()
                    ok_sched, msg = mgr.schedule_folder(name, config["folder_path"], start_gen)
                    if not ok_sched:
                        update_status(f"[{name}] Không thể theo dõi thư mục: {msg}")
                        return
                    adopted = _reconcile_startup_folder(name, config["folder_path"], start_gen)
                    if adopted:
                        update_status(f"[{name}] Đã tiếp nhận {adopted} video có sẵn trong thư mục.")
                except Exception as e:
                    update_status(f"[{name}] Lỗi khởi tạo watchdog: {e}")
                    return
                _set_profile_ui(name, status='Đang chạy', browser='Chờ video', upload='Chờ video mới')
                update_status(f"[{name}] Chế độ chỉ mở khi có video mới: bỏ qua video cũ, đang chờ video mới.")
                threading.Thread(target=process_video_queue_thread, args=(name,), daemon=True).start()
                return

            ensure_driver(name, lifecycle_gen=start_gen)
            if name not in profiles or not profiles[name].get('running', False) or lc.is_cancelled or not lc.is_current(start_gen):
                drv = profiles.get(name, {}).get('driver')
                if drv:
                    try: drv.quit()
                    except Exception: pass
                if name in profiles:
                    profiles[name]['driver'] = None
                    kill_stale_chrome_processes(name)
                return

            if _browser_session_valid(profiles[name].get('driver')):
                try:
                    mgr = get_watchdog_manager()
                    ok_sched, msg = mgr.schedule_folder(name, config["folder_path"], start_gen)
                    if not ok_sched:
                        update_status(f"[{name}] Không thể theo dõi thư mục: {msg}")
                        stop_profile(name)
                        return
                    profiles[name]['watch_started_at'] = time.time()
                    adopted = _reconcile_startup_folder(name, config["folder_path"], start_gen)
                    if adopted:
                        update_status(f"[{name}] Đã tiếp nhận {adopted} video có sẵn trong thư mục.")
                except Exception as e:
                    update_status(f"[{name}] Lỗi khởi tạo watchdog: {e}")
                    stop_profile(name)
                    return
                _set_profile_ui(name, status='Đang chạy', browser='Sẵn sàng', upload='Chờ video')
                update_status(f"[{name}] Browser đã mở sẵn, đang chờ video mới.")
                threading.Thread(target=process_video_queue_thread, args=(name,), daemon=True).start()
            else:
                _set_profile_ui(name, status='Lỗi', browser='Bị lỗi', last_error='Driver lỗi hoặc proxy sai')
                update_status(f"[{name}] Driver lỗi/Proxy sai. Dừng.")
                stop_profile(name)
        except Exception as e:
            _set_profile_ui(name, status='Lỗi', browser='Bị lỗi', last_error=str(e))
            update_status(f"[{name}] Exception Init: {e}")
            stop_profile(name)
            
    threading.Thread(target=_worker, daemon=True).start()

def _stop_profile_driver(name):
    if name not in profiles:
        return
    profile = profiles[name]
    lc = get_lifecycle(name)
    lc.cancel()
    stop_gen = lc.generation
    profile['running'] = False
    running_profiles.discard(name)

    drv_auto = profile.get('driver')
    profile['driver'] = None
    drv_manual = profile.get('manual_driver')
    profile['manual_driver'] = None
    profile['observer'] = None

    try:
        from watchdog_service import get_watchdog_manager
        get_watchdog_manager().unschedule_profile(name)
    except Exception:
        pass

    for token in (drv_auto, drv_manual):
        if isinstance(token, SessionToken):
            token.set_cancelled()

    # Save cookies best-effort before cleanup
    for drv in (drv_auto, drv_manual):
        if drv is not None:
            try:
                _export_live_cookies_to_config(drv, name)
            except Exception:
                pass

    # Gen-scoped cleanup: only clean if gen still current
    report = lc.cleanup_gen(stop_gen, quit_timeout=3, kill_timeout=2)
    if report.get("gen_mismatch"):
        logging.info(f"[{name}] Generation changed during stop, skipping lifecycle cleanup")
    else:
        if report.get("errors"):
            for err in report["errors"]:
                logging.warning(f"[{name}] Cleanup warning: {err}")
    kill_stale_chrome_processes(name)

def close_profile_browser(name):
    if name not in profiles:
        return
    lc = get_lifecycle(name)
    drv = profiles[name].get('driver')
    profiles[name]['driver'] = None
    lifecycle_driver, lifecycle_service, _owned_pids = lc.detach_automation()
    if drv is None:
        drv = lifecycle_driver
    if drv:
        if isinstance(drv, SessionToken):
            drv.set_cancelled()
        try:
            _export_live_cookies_to_config(drv, name)
        except Exception:
            pass
        try:
            drv.quit()
        except Exception:
            pass
    kill_stale_chrome_processes(name)
    _set_profile_ui(name, browser='Đã đóng', upload='Chờ video mới')
    update_status(f"[{name}] Hết video mới trong hàng chờ, đã đóng trình duyệt để tiết kiệm tài nguyên.")

def stop_profile(selected_name=None):
    if not _license_guard(): return
    if selected_name: name = selected_name
    else:
        sel = tree.selection()
        if not sel: return
        name = tree.item(sel[0])['values'][0]

    if name not in profiles: return

    lc = get_lifecycle(name)
    has_any_driver = (
        profiles[name].get('running', False)
        or _browser_session_valid(profiles[name].get('driver'))
        or _browser_session_valid(profiles[name].get('manual_driver'))
        or lc.has_active_driver()
    )
    if not has_any_driver:
        profiles[name]['running'] = False
        running_profiles.discard(name)
        return

    _set_profile_ui(name, status='Đang dừng', browser='Đang đóng', upload='Đang dừng')
    _stop_profile_driver(name)

    _set_profile_ui(name, status='Đã dừng', browser='Chưa mở', upload='Chờ video')
    update_status(f"[{name}] Đã dừng.")
    root.after(0, request_profile_refresh)

# =========================
# CRUD Actions
# =========================
def create_project():
    if not _license_guard(): return
    dlg = ctk.CTkToplevel(root)
    dlg.title("Tạo dự án")
    fit_and_center_dialog(dlg, 340, 180, parent=root, min_w=280, min_h=140)
    ctk.CTkLabel(dlg, text="Tên dự án:").pack(pady=5)
    e = ctk.CTkEntry(dlg, width=200)
    e.pack(pady=5)
    def save():
        v = e.get().strip()
        if not v or v in projects or v == ALL_OPTION:
            messagebox.showerror("Lỗi", "Tên không hợp lệ")
            return
        projects[v] = set()
        save_configs()
        dlg.destroy()
    ctk.CTkButton(dlg, text="Lưu", command=save).pack(pady=10)

def delete_project():
    if not _license_guard(): return
    p = selected_project_var.get()
    if not p or p == 'Mặc định' or p == ALL_OPTION or p not in projects:
        messagebox.showerror("Lỗi", "Không thể xoá dự án này")
        return
    
    to_stop = [n for n in projects[p] if n in profiles and profiles[n]['running']]
    if to_stop:
        threading.Thread(target=_thread_sequential_stop, args=(to_stop, p), daemon=True).start()
        messagebox.showinfo("Info", "Đang dừng hồ sơ. Vui lòng thử lại sau khi dừng xong.")
        return

    profile_count = len(projects[p])
    ok = messagebox.askyesno("Xác nhận xoá dự án",
        f"Bạn có chắc muốn xoá dự án '{p}'?\n\n"
        f"{profile_count} hồ sơ trong dự án này sẽ được chuyển về 'Mặc định'.\n"
        "Không xoá hồ sơ, thư mục video hoặc Chrome profile.")
    if not ok: return

    for n in list(projects[p]):
        if n in profiles:
            profiles[n]['project'] = 'Mặc định'
            projects['Mặc định'].add(n)
    del projects[p]
    save_configs()
    selected_project_var.set(ALL_OPTION)
    update_status(f"[UI] Đã xoá dự án '{p}'.")

def assign_to_project():
    if not _license_guard(): return
    sel = tree.selection()
    if not sel: return
    name = tree.item(sel[0])['values'][0]
    dlg = ctk.CTkToplevel(root)
    dlg.title("Gán dự án")
    fit_and_center_dialog(dlg, 340, 180, parent=root, min_w=280, min_h=140)
    ctk.CTkLabel(dlg, text="Dự án:").pack(pady=5)
    var = StringVar(dlg, value=profiles[name].get('project', 'Mặc định'))
    cb = ctk.CTkComboBox(dlg, values=list(projects.keys()), variable=var)
    cb.pack(pady=5)
    def save():
        np = var.get()
        if np not in projects: return
        op = profiles[name].get('project')
        if op and op in projects: projects[op].discard(name)
        projects[np].add(name)
        profiles[name]['project'] = np
        save_configs()
        dlg.destroy()
    ctk.CTkButton(dlg, text="Lưu", command=save).pack(pady=10)

def add_profile():
    if not _license_guard(): return
    
    dlg = ctk.CTkToplevel(root)
    dlg.title("DONGLAO-TIKTOK — Thêm Hồ Sơ Mới")
    fit_and_center_dialog(dlg, 960, 720, parent=root, min_w=620, min_h=450)
    dlg.transient(root)
    try:
        dlg.grab_set()
    except Exception:
        pass

    scroll = ctk.CTkScrollableFrame(dlg, fg_color='#f3f4f6')
    scroll.pack(fill='both', expand=True, padx=10, pady=(10, 0))

    # --- Top Bar: Dán Nhanh Chuỗi Tài Khoản ---
    quick_card, quick_body = _ui_card(scroll, '⚡ Dán Nhanh Chuỗi Dữ Liệu', 'Tự động phân tách và điền vào các ô nhập liệu bên dưới')
    quick_row = ctk.CTkFrame(quick_body, fg_color='transparent')
    quick_row.pack(fill='x', pady=2)
    
    e_quick_paste = ctk.CTkEntry(
        quick_row,
        placeholder_text="Dán chuỗi (vd: Name|Email|Pass|TikTokID|2FA|Cookie|Proxy hoặc UID|User|Pass|Cookie|Proxy)...",
        height=32,
        border_width=1,
        border_color='#cbd5e1',
    )
    e_quick_paste.pack(side='left', fill='x', expand=True)

    # --- Card 1: Thông tin nhận diện ---
    card1, body1 = _ui_card(scroll, '1. Thông Tin Nhận Diện', 'Tên profile và thông tin tài khoản cơ bản')
    body1.grid_columnconfigure(0, weight=1)
    body1.grid_columnconfigure(1, weight=1)
    
    _, e_name = _edit_field(body1, 0, 0, 'Tên hồ sơ (*)')
    
    proj_frame = ctk.CTkFrame(body1, fg_color='transparent')
    proj_frame.grid(row=0, column=1, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(proj_frame, text='Dự án / Nhóm', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    v_proj = StringVar(proj_frame, value='Mặc định' if 'Mặc định' in projects else (list(projects.keys())[0] if projects else 'Mặc định'))
    cb_proj = ctk.CTkComboBox(proj_frame, values=list(projects.keys()) if projects else ['Mặc định'], variable=v_proj, height=32)
    cb_proj.pack(fill='x', pady=(3, 0))

    _, e_email = _edit_field(body1, 1, 0, 'Email liên kết')
    _, e_tiktok_id = _edit_field(body1, 1, 1, 'TikTok ID (@username hoặc Numeric UID)')

    note_row = ctk.CTkFrame(body1, fg_color='transparent')
    note_row.grid(row=2, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(note_row, text='Ghi chú', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    e_note = ctk.CTkTextbox(note_row, height=54, wrap='word', border_width=1, border_color='#cbd5e1')
    e_note.pack(fill='x', pady=(3, 0))

    # --- Card 2: Bảo mật & Cookie ---
    card2, body2 = _ui_card(scroll, '2. Bảo Mật & Xác Thực', 'Mật khẩu, mã 2FA và Cookie phiên đăng nhập')
    body2.grid_columnconfigure(0, weight=1)
    body2.grid_columnconfigure(1, weight=1)
    
    _, e_password = _edit_secret(body2, 0, 0, 'Mật khẩu TikTok')
    _, e_auth2fa = _edit_secret(body2, 0, 1, 'Khóa 2FA (Secret Key)')
    _, e_passmail = _edit_secret(body2, 1, 0, 'Mật khẩu email')
    _, e_mail_backup = _edit_field(body2, 1, 1, 'Email backup')
    _, e_pass_mail_backup = _edit_secret(body2, 2, 0, 'Mật khẩu email backup')

    cookie_row = ctk.CTkFrame(body2, fg_color='transparent')
    cookie_row.grid(row=3, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(cookie_row, text='Cookie TikTok (tùy chọn, dùng khi chưa có session trình duyệt)', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    e_cookie = ctk.CTkTextbox(cookie_row, height=72, wrap='word', border_width=1, border_color='#cbd5e1')
    e_cookie.pack(fill='x', pady=(3, 0))

    # --- Card 3: Proxy & Mạng ---
    card3, body3 = _ui_card(scroll, '3. Cấu Hình Proxy & Mạng', 'Định tuyến lưu lượng qua Proxy riêng biệt')
    body3.grid_columnconfigure(0, weight=1)
    body3.grid_columnconfigure(1, weight=1)

    proxy_top = ctk.CTkFrame(body3, fg_color='transparent')
    proxy_top.grid(row=0, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    v_use_proxy = ctk.BooleanVar(proxy_top, value=False)
    ctk.CTkCheckBox(proxy_top, text="Kích hoạt sử dụng Proxy cho hồ sơ này", variable=v_use_proxy, font=('Segoe UI', 12, 'bold')).pack(side='left')

    ctk.CTkLabel(proxy_top, text="Loại:", font=('Segoe UI', 11), text_color='#64748b').pack(side='left', padx=(20, 6))
    v_proxy_type = ctk.StringVar(value="http")
    cb_proxy_type = ctk.CTkOptionMenu(proxy_top, values=["http", "socks5"], variable=v_proxy_type, width=90, height=28)
    cb_proxy_type.pack(side='left')

    proxy_input_frame = ctk.CTkFrame(body3, fg_color='transparent')
    proxy_input_frame.grid(row=1, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(proxy_input_frame, text='Chuỗi Proxy (IP:Port hoặc IP:Port:User:Pass)', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    e_proxy = ctk.CTkEntry(proxy_input_frame, height=32, border_width=1, border_color='#cbd5e1', placeholder_text="192.168.1.1:8080 hoặc 192.168.1.1:8080:user:pass")
    e_proxy.pack(fill='x', pady=(3, 0))

    test_proxy_row = ctk.CTkFrame(body3, fg_color='transparent')
    test_proxy_row.grid(row=2, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    lbl_proxy_test = ctk.CTkLabel(test_proxy_row, text="", font=('Segoe UI', 11))
    
    def _test_proxy_live():
        raw_p = e_proxy.get().strip()
        if not raw_p:
            lbl_proxy_test.configure(text="⚠️ Vui lòng nhập chuỗi proxy trước khi test", text_color="#d97706")
            return
        lbl_proxy_test.configure(text="⏳ Đang kiểm tra kết nối proxy...", text_color="#0284c7")
        def _bg_test():
            try:
                p_data = parse_proxy_string(raw_p, v_proxy_type.get())
                if not p_data:
                    root.after(0, lambda: lbl_proxy_test.configure(text="❌ Định dạng chuỗi proxy không hợp lệ", text_color="#dc2626"))
                    return
                geo = resolve_geoip(p_data, timeout=8)
                ip = geo.get('ip') or p_data.get('ip')
                country = geo.get('country') or 'Unknown'
                city = geo.get('city', '')
                loc_str = f"[{country}] {city} - IP: {ip}".strip()
                root.after(0, lambda: lbl_proxy_test.configure(text=f"🟢 Proxy LIVE: {loc_str}", text_color="#16a34a"))
            except Exception as exc:
                root.after(0, lambda: lbl_proxy_test.configure(text=f"🔴 Lỗi kết nối proxy: {exc}", text_color="#dc2626"))
        threading.Thread(target=_bg_test, daemon=True).start()

    btn_test_proxy = ctk.CTkButton(
        test_proxy_row,
        text="⚡ Kiểm Tra Proxy",
        width=120,
        height=28,
        fg_color=UIThemeTokens.ACCENT_PRIMARY,
        hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
        text_color="#ffffff",
        font=('Segoe UI', 11, 'bold'),
        command=_test_proxy_live,
    )
    btn_test_proxy.pack(side='left')
    lbl_proxy_test.pack(side='left', padx=10)

    # --- Card 4: Thư mục dữ liệu & Vận hành ---
    card4, body4 = _ui_card(scroll, '4. Thư Mục Dữ Liệu & Vận Hành', 'Cấu hình đường dẫn lưu trữ video và profile browser')
    body4.grid_columnconfigure(0, weight=1)
    body4.grid_columnconfigure(1, weight=1)

    v_auto_dirs = ctk.BooleanVar(body4, value=True)
    chk_auto_dirs = ctk.CTkCheckBox(
        body4,
        text="Tự động sinh thư mục chuẩn (Auto_Data/<TênProfile>/...) (Khuyên dùng)",
        variable=v_auto_dirs,
        font=('Segoe UI', 12, 'bold'),
    )
    chk_auto_dirs.grid(row=0, column=0, columnspan=2, sticky='w', padx=8, pady=4)

    folder_frame = ctk.CTkFrame(body4, fg_color='transparent')
    folder_frame.grid(row=1, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(folder_frame, text='Thư mục video (*)', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    folder_row = ctk.CTkFrame(folder_frame, fg_color='transparent')
    folder_row.pack(fill='x', pady=(3, 0))
    e_folder = ctk.CTkEntry(folder_row, height=32, border_width=1, border_color='#cbd5e1')
    e_folder.pack(side='left', fill='x', expand=True)
    ctk.CTkButton(folder_row, text='Chọn...', width=70, height=32, fg_color='#eef2ff', text_color='#2563eb',
                  hover_color='#dbeafe', command=lambda: _browse_dir(e_folder)).pack(side='left', padx=(4, 0))
    ctk.CTkButton(folder_row, text='Mở', width=44, height=32, fg_color='#f1f5f9', text_color='#334155',
                  hover_color='#e2e8f0', command=lambda: _open_dir(e_folder.get())).pack(side='left', padx=(4, 0))

    chrome_frame = ctk.CTkFrame(body4, fg_color='transparent')
    chrome_frame.grid(row=2, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(chrome_frame, text='Chrome User Data (Profile Browser) (*)', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    chrome_row = ctk.CTkFrame(chrome_frame, fg_color='transparent')
    chrome_row.pack(fill='x', pady=(3, 0))
    e_chrome = ctk.CTkEntry(chrome_row, height=32, border_width=1, border_color='#cbd5e1')
    e_chrome.pack(side='left', fill='x', expand=True)
    ctk.CTkButton(chrome_row, text='Chọn...', width=70, height=32, fg_color='#eef2ff', text_color='#2563eb',
                  hover_color='#dbeafe', command=lambda: _browse_dir(e_chrome)).pack(side='left', padx=(4, 0))
    ctk.CTkButton(chrome_row, text='Mở', width=44, height=32, fg_color='#f1f5f9', text_color='#334155',
                  hover_color='#e2e8f0', command=lambda: _open_dir(e_chrome.get())).pack(side='left', padx=(4, 0))

    def _sync_auto_paths(*_):
        if not v_auto_dirs.get():
            return
        nm = e_name.get().strip()
        if nm:
            safe = "".join(c for c in nm if c.isalnum() or c in (' ', '-', '_')).strip() or "Profile"
            base = app_base_dir() / "Auto_Data" / safe
            e_folder.delete(0, 'end')
            e_folder.insert(0, str(base / "Video"))
            e_chrome.delete(0, 'end')
            e_chrome.insert(0, str(base / "Profile"))

    e_name.bind('<KeyRelease>', _sync_auto_paths)

    _, e_limit = _edit_field(body4, 3, 0, 'Giới hạn video / ngày (0 = không giới hạn)', '0')
    _, v_head = _edit_check(body4, 3, 1, 'Chạy ngầm (Headless)', True)
    _, v_open_only = _edit_check(body4, 4, 0, 'Chỉ mở khi có video mới', False)

    # --- Quick Paste Handler ---
    def _apply_quick_paste():
        raw_text = e_quick_paste.get().strip()
        if not raw_text:
            return
        parts = [p.strip() for p in raw_text.replace('\t', '|').split('|') if p.strip()]
        if not parts:
            return
        
        # Heuristic Auto-Fill based on parts count
        if len(parts) >= 1 and not e_name.get().strip():
            e_name.insert(0, parts[0])
            _sync_auto_paths()
        
        for part in parts[1:]:
            if '@' in part and '.' in part and not e_email.get().strip():
                e_email.insert(0, part)
            elif 'sessionid=' in part or 'sid_guard=' in part:
                e_cookie.delete('1.0', 'end')
                e_cookie.insert('1.0', part)
            elif (':' in part and any(c.isdigit() for c in part)) and not e_proxy.get().strip():
                e_proxy.insert(0, part)
                v_use_proxy.set(True)
            elif part.startswith('@') and not e_tiktok_id.get().strip():
                e_tiktok_id.insert(0, part)
            elif len(part) in (16, 26, 32) and part.isalnum() and part.isupper() and not e_auth2fa.get().strip():
                e_auth2fa.insert(0, part)
            elif not e_password.get().strip() and len(part) >= 6:
                e_password.insert(0, part)

        toast_manager.enqueue("Đã tự động trích xuất chuỗi vào các ô", level="info")

    btn_quick_apply = ctk.CTkButton(
        quick_row,
        text="⚡ Áp Dụng",
        width=95,
        height=32,
        fg_color=UIThemeTokens.ACCENT_PRIMARY,
        hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
        text_color="#ffffff",
        font=('Segoe UI', 11, 'bold'),
        command=_apply_quick_paste,
    )
    btn_quick_apply.pack(side='left', padx=(6, 0))

    # --- Save Logic ---
    def save():
        nm = e_name.get().strip()
        if not nm:
            messagebox.showerror("Lỗi", "Vui lòng nhập Tên hồ sơ.", parent=dlg)
            return
        if nm in profiles:
            messagebox.showerror("Lỗi", f"Hồ sơ '{nm}' đã tồn tại.", parent=dlg)
            return
        fd = e_folder.get().strip()
        cp = e_chrome.get().strip()
        pj = v_proj.get().strip() or 'Mặc định'
        try:
            lm = int(e_limit.get().strip())
        except Exception:
            lm = 0
        if lm < 0:
            lm = 0

        if not fd or not cp:
            messagebox.showerror("Lỗi", "Vui lòng nhập đầy đủ Thư mục video và Chrome Profile.", parent=dlg)
            return

        if pj not in projects:
            projects[pj] = set()

        duplicate = _find_profile_with_data_dir(cp)
        if duplicate:
            messagebox.showerror("Lỗi", f"Thư mục Chrome Profile đang được hồ sơ '{duplicate}' sử dụng.", parent=dlg)
            return

        try:
            profile_path = Path(cp)
            if not profile_path.exists():
                create_owned_root(profile_path)
            video_path = Path(fd)
            video_path.mkdir(parents=True, exist_ok=True)
        except Exception as error:
            messagebox.showerror("Lỗi", f"Không tạo được thư mục an toàn: {error}", parent=dlg)
            return

        fp_seed = nm + e_cookie.get('1.0', 'end').strip() + str(time.time_ns())
        fingerprint = _generate_fingerprint(seed=fp_seed)
        
        profiles[nm] = {
            'config': {
                "folder_path": fd,
                "chrome_profile": cp,
                "cookie_str": e_cookie.get('1.0', 'end').strip(),
                "email": e_email.get().strip(),
                "password": e_password.get(),
                "tiktok_id": _normalize_tiktok_id(e_tiktok_id.get()),
                "auth2fa": e_auth2fa.get().strip(),
                "passmail": e_passmail.get(),
                "mail_backup": e_mail_backup.get().strip(),
                "pass_mail_backup": e_pass_mail_backup.get(),
                "note": e_note.get('1.0', 'end').strip(),
                "proxy_string": e_proxy.get().strip(),
                "proxy_type": v_proxy_type.get().lower(),
                "use_proxy": v_use_proxy.get(),
                "headless": v_head.get(),
                "open_only_when_video": v_open_only.get(),
                "max_uploads_per_day": lm,
                "fingerprint": fingerprint,
                "stats_today": 0,
                "stats_yesterday": 0,
                "stats_date": datetime.now().strftime('%Y-%m-%d'),
            },
            'queue': queue.Queue(),
            'observer': None,
            'driver': None,
            'running': False,
            'processed_files': set(),
            'last_event_time': {},
            'uploading': False,
            'project': pj,
            'uploads_today_count': 0,
            'uploads_yesterday_count': 0,
            'uploads_today_date': datetime.now().strftime('%Y-%m-%d'),
        }
        ensure_account_uuid(profiles[nm]['config'])
        projects[pj].add(nm)
        save_configs()
        update_profile_list()
        try:
            request_profile_refresh()
        except Exception:
            pass
        toast_manager.enqueue(f"Đã thêm hồ sơ: {nm}", level="info")
        dlg.destroy()

    _ui_footer(dlg, primary_text="💾 Lưu Hồ Sơ", primary_command=save, secondary_text="Hủy", secondary_command=dlg.destroy)

# --- IMPORT HÀNG LOẠT ---
def _apply_import_plans(plans, proxy_type='http', default_project='Mặc định'):
    added = []
    updated_backup = {}
    created_dirs = []
    default_project_created = False
    if default_project not in projects:
        projects[default_project] = set()
        default_project_created = True

    for plan in plans:
        action = plan['action']
        record = plan['record']
        name = record.get('name', '')
        if action in ('skip', 'error') or not name:
            continue
        cfg = profiles.get(name, {}).get('config')
        if action == 'add':
            safe = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
            if not safe:
                safe = f"Profile_{uuid.uuid4().hex[:8]}"
            profile_root = os.path.join(str(app_base_dir() / "Auto_Data"), safe)
            video_dir = os.path.join(profile_root, "Video")
            chrome_dir = os.path.join(profile_root, "Profile")
            if _find_profile_with_data_dir(chrome_dir):
                continue
            try:
                os.makedirs(video_dir, exist_ok=True)
                if not Path(chrome_dir).exists():
                    create_owned_root(chrome_dir)
            except Exception as error:
                update_status(f"[Import] Lỗi tạo folder {name}: {error}")
                continue
            created_dirs.append(video_dir)
            fp_seed = name + record.get('cookie_str', '') + str(time.time_ns())
            fingerprint = _generate_fingerprint(seed=fp_seed)
            config = {
                "folder_path": video_dir,
                "chrome_profile": chrome_dir,
                "cookie_str": record.get('cookie_str', ''),
                "email": record.get('email', ''),
                "password": record.get('password', ''),
                "tiktok_id": _normalize_tiktok_id(record.get('tiktok_id', '')),
                "auth2fa": record.get('auth2fa', ''),
                "passmail": record.get('passmail', ''),
                "mail_backup": record.get('mail_backup', ''),
                "pass_mail_backup": record.get('pass_mail_backup', ''),
                "note": record.get('note', ''),
                "proxy_string": record.get('proxy_string', ''),
                "proxy_type": proxy_type,
                "use_proxy": bool(record.get('proxy_string', '')),
                "headless": True,
                "open_only_when_video": False,
                "max_uploads_per_day": 3,
                "fingerprint": fingerprint,
                "stats_today": 0,
                "stats_yesterday": 0,
                "stats_date": datetime.now().strftime('%Y-%m-%d')
            }
            profiles[name] = {
                'config': config,
                'queue': queue.Queue(), 'observer': None, 'driver': None, 'running': False,
                'processed_files': set(), 'last_event_time': {}, 'uploading': False,
                'project': default_project,
                'uploads_today_count': 0,
                'uploads_yesterday_count': 0,
                'uploads_today_date': datetime.now().strftime('%Y-%m-%d')
            }
            projects[default_project].add(name)
            ensure_account_uuid(profiles[name]['config'])
            added.append(name)
        elif action == 'update' and cfg is not None:
            updated_backup[name] = copy.deepcopy(cfg)
            invalidation_reasons = []
            for key in ('cookie_str', 'tiktok_id', 'email', 'proxy_string'):
                if record.get(key) and record.get(key) != cfg.get(key):
                    invalidation_reasons.append(key)
            updated = apply_update_to_config(cfg, record, proxy_type)
            if record.get('tiktok_id'):
                updated['tiktok_id'] = _normalize_tiktok_id(record['tiktok_id'])
            cfg.clear()
            cfg.update(updated)
            if invalidation_reasons:
                invalidate_session_auth(cfg, 'Thay đổi khi import: ' + ', '.join(invalidation_reasons))

    return {
        'added': added,
        'updated': list(updated_backup.keys()),
        'updated_backup': updated_backup,
        'created_dirs': created_dirs,
        'default_project_created': default_project_created,
    }


def _rollback_import(result):
    for name in result.get('added', []):
        prof_project = profiles.get(name, {}).get('project')
        if prof_project in projects:
            projects[prof_project].discard(name)
        profiles.pop(name, None)
    for name, backup in result.get('updated_backup', {}).items():
        if name in profiles:
            profiles[name]['config'] = backup
    for directory in result.get('created_dirs', []):
        try:
            if Path(directory).exists() and not any(Path(directory).iterdir()):
                Path(directory).rmdir()
        except Exception:
            pass
    if result.get('default_project_created') and 'Mặc định' in projects and not projects['Mặc định']:
        del projects['Mặc định']


def batch_add_profiles():
    if not _license_guard(): return

    dlg = ctk.CTkToplevel(root)
    dlg.title("DONGLAO-TIKTOK — Import Tài Khoản Hàng Loạt")
    fit_and_center_dialog(dlg, 1000, 750, parent=root, min_w=700, min_h=520)
    dlg.configure(fg_color=UIThemeTokens.BG_ROOT)
    dlg.transient(root)
    try:
        dlg.grab_set()
    except Exception:
        pass

    top = ctk.CTkFrame(dlg, fg_color=UIThemeTokens.BG_CARD, corner_radius=10, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
    top.pack(side='top', fill='x', padx=12, pady=(12, 6))
    top.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(top, text="Định dạng:", font=('Segoe UI', 12, 'bold'), text_color=UIThemeTokens.TEXT_PRIMARY).grid(row=0, column=0, sticky='w', padx=10, pady=(8, 2))
    preset_var = StringVar(value='Đầy đủ 11 trường')
    presets = {
        'Đầy đủ 11 trường': DEFAULT_FORMAT,
        'Format cũ (Tên|Cookie|Proxy|ID TikTok)': LEGACY_FORMAT,
        'Tùy chỉnh': '',
    }
    def _apply_preset(*_):
        if preset_var.get() in presets and presets[preset_var.get()]:
            format_var.set(presets[preset_var.get()])
    preset_combo = ctk.CTkComboBox(top, values=list(presets.keys()), variable=preset_var, width=320, height=30, command=lambda _: _apply_preset())
    preset_combo.grid(row=0, column=1, sticky='w', padx=6, pady=(8, 2))

    format_var = StringVar(value=DEFAULT_FORMAT)
    format_entry = ctk.CTkEntry(top, textvariable=format_var, width=340, height=30, border_width=1, border_color='#cbd5e1')
    format_entry.grid(row=1, column=1, sticky='ew', padx=6, pady=(4, 0))

    ctk.CTkLabel(top, text="(Phân tách bằng dấu `|`, bấm các thẻ bên phải để chèn nhanh trường)", font=('Segoe UI', 10), text_color=UIThemeTokens.TEXT_MUTED).grid(row=2, column=1, sticky='w', padx=6, pady=(2, 8))

    opt = ctk.CTkFrame(dlg, fg_color=UIThemeTokens.BG_CARD, corner_radius=10, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
    opt.pack(side='top', fill='x', padx=12, pady=(0, 6))
    skip_header_var = ctk.BooleanVar(opt, value=False)
    ctk.CTkCheckBox(opt, text="Dòng đầu là tiêu đề", variable=skip_header_var, font=('Segoe UI', 11)).pack(side='left', padx=(10, 16), pady=8)
    ctk.CTkLabel(opt, text="Loại Proxy:", font=('Segoe UI', 11), text_color=UIThemeTokens.TEXT_PRIMARY).pack(side='left')
    proxy_type_var = StringVar(opt, value='http')
    ctk.CTkOptionMenu(opt, values=['http', 'socks5'], variable=proxy_type_var, width=100, height=28).pack(side='left', padx=(4, 16), pady=8)
    ctk.CTkLabel(opt, text="Khi trùng tên:", font=('Segoe UI', 11, 'bold'), text_color=UIThemeTokens.TEXT_PRIMARY).pack(side='left')
    dup_policy_var = StringVar(opt, value='Cập nhật')
    ctk.CTkOptionMenu(opt, values=['Cập nhật', 'Bỏ qua', 'Báo lỗi'], variable=dup_policy_var, width=120, height=28, button_color=UIThemeTokens.ACCENT_PRIMARY).pack(side='left', padx=4, pady=8)

    # 1. Pinned Bottom Action Bar (Packed first with side='bottom' so it is NEVER cut off)
    btn_row = ctk.CTkFrame(dlg, fg_color='transparent')
    btn_row.pack(side='bottom', fill='x', padx=12, pady=(4, 12))

    # 2. Pinned Bottom Preview Table (Packed above bottom buttons)
    prev_frame = ctk.CTkFrame(dlg, fg_color=UIThemeTokens.BG_CARD, corner_radius=10, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
    prev_frame.pack(side='bottom', fill='x', padx=12, pady=(0, 6))
    prev_cols = ('name', 'email', 'tiktok', 'proxy', 'status')
    prev_tree = ttk.Treeview(prev_frame, columns=prev_cols, show='headings', height=4)
    for col, text, width in (
        ('name', 'Tên Profile', 160), ('email', 'Email', 180), ('tiktok', 'TikTok ID', 120),
        ('proxy', 'Proxy', 140), ('status', 'Trạng thái xử lý', 140),
    ):
        prev_tree.heading(col, text=text)
        prev_tree.column(col, width=width, anchor='w')
    prev_tree.pack(fill='x', padx=8, pady=(8, 4))
    error_label = ctk.CTkLabel(prev_frame, text="", text_color=UIThemeTokens.STATUS_ERROR, font=('Segoe UI', 11))
    error_label.pack(anchor='w', padx=10, pady=(0, 6))

    # 3. Middle Expandable Section (Takes all remaining flexible space in the center)
    middle = ctk.CTkFrame(dlg, fg_color=UIThemeTokens.BG_CARD, corner_radius=10, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
    middle.pack(side='top', fill='both', expand=True, padx=12, pady=(0, 6))
    middle.grid_columnconfigure(0, weight=1)
    middle.grid_columnconfigure(1, weight=0)
    middle.grid_rowconfigure(0, weight=1)

    txt_input = ctk.CTkTextbox(middle, width=620, height=180, font=("Consolas", 10))
    txt_input.grid(row=0, column=0, sticky='nsew', padx=(8, 8), pady=8)

    field_panel = ctk.CTkScrollableFrame(middle, width=220, height=180, label_text="Nhấn để chèn trường")
    field_panel.grid(row=0, column=1, sticky='ns', padx=(0, 8), pady=8)
    for field in DEFAULT_FIELDS:
        ctk.CTkButton(
            field_panel, text=field, height=26, fg_color='#e2e8f0', hover_color='#cbd5e1', text_color='#0f172a', font=('Segoe UI', 10),
            command=lambda f=field: format_var.set((format_var.get() + ('' if format_var.get().endswith('|') else '|') + f)),
        ).pack(fill='x', pady=2)

    def _parse_current():
        fields = parse_format(format_var.get())
        text = txt_input.get('1.0', 'end')
        records, errors = parse_data_into_records(text, fields, skip_header=skip_header_var.get())
        return fields, records, errors

    def do_preview():
        for item in prev_tree.get_children():
            prev_tree.delete(item)
        try:
            _, records, errors = _parse_current()
        except ValueError as exc:
            error_label.configure(text=str(exc))
            return
        existing = set(profiles.keys())
        policy = 'skip' if dup_policy_var.get() == 'Bỏ qua' else ('update' if dup_policy_var.get() == 'Cập nhật' else 'error')
        plans = plan_import(records, existing, policy)
        for plan in plans:
            mask = masked_record(plan['record'])
            status = {'skip': '⚪ Bỏ qua (Đã tồn tại)', 'update': '🟡 Cập nhật thông tin', 'error': '🔴 Trùng tên', 'add': '🟢 Thêm mới'}[plan['action']]
            prev_tree.insert('', 'end', values=(mask.get('name', ''), mask.get('email', ''), mask.get('tiktok_id', ''), mask.get('proxy_string', ''), status))
        error_label.configure(text=f"Hợp lệ: {len(records)} | Lỗi định dạng: {len(errors)}" + (f" | ví dụ dòng {errors[0][0]}: {errors[0][1]}" if errors else ""))

    def open_file():
        path = filedialog.askopenfilename(filetypes=[('Text files', '*.txt'), ('All files', '*.*')], parent=dlg)
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                txt_input.delete('1.0', 'end')
                txt_input.insert('1.0', f.read())
            do_preview()
        except Exception as exc:
            messagebox.showerror("Lỗi", f"Không đọc được file:\n{exc}", parent=dlg)

    def run_import():
        try:
            _, records, errors = _parse_current()
        except ValueError as exc:
            messagebox.showerror("Format không hợp lệ", str(exc), parent=dlg)
            return
        if errors:
            lines = '\n'.join(f"Dòng {ln}: {reason}" for ln, reason in errors[:20])
            messagebox.showerror("Dữ liệu không hợp lệ", f"Có {len(errors)} dòng lỗi:\n{lines}", parent=dlg)
            return
        if not records:
            messagebox.showwarning("Import", "Không có dữ liệu hợp lệ để import.", parent=dlg)
            return
        existing = set(profiles.keys())
        policy = 'skip' if dup_policy_var.get() == 'Bỏ qua' else ('update' if dup_policy_var.get() == 'Cập nhật' else 'error')
        plans = plan_import(records, existing, policy)
        if any(plan['action'] == 'error' for plan in plans):
            names = [plan['record']['name'] for plan in plans if plan['action'] == 'error']
            messagebox.showerror("Trùng tên", "Chính sách 'Báo lỗi' được chọn nhưng có tên đã tồn tại:\n" + '\n'.join(names[:20]), parent=dlg)
            return
        proxy_type = proxy_type_var.get()
        result = _apply_import_plans(plans, proxy_type)
        try:
            save_configs()
            update_profile_list()
            try:
                request_profile_refresh()
            except Exception:
                pass
        except Exception as exc:
            _rollback_import(result)
            messagebox.showerror("Lỗi lưu", f"Không lưu được cấu hình, đã hoàn nguyên:\n{exc}", parent=dlg)
            return
        skipped = sum(1 for plan in plans if plan['action'] == 'skip')
        toast_manager.enqueue(f"Import thành công: {len(result['added'])} thêm mới, {len(result['updated'])} cập nhật", level="info")
        messagebox.showinfo(
            "Hoàn tất",
            f"Đã thêm: {len(result['added'])}\nĐã cập nhật: {len(result['updated'])}\nBỏ qua: {skipped}",
            parent=dlg,
        )
        dlg.destroy()

    ctk.CTkButton(btn_row, text="📁 Mở File TXT", command=open_file, fg_color="#64748b", hover_color="#475569", height=32, text_color="#ffffff").pack(side='left', padx=2)
    ctk.CTkButton(btn_row, text="👁️ Xem Trước", command=do_preview, fg_color=UIThemeTokens.ACCENT_PRIMARY, hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER, height=32, text_color="#ffffff").pack(side='left', padx=6)
    ctk.CTkButton(btn_row, text="⚡ Nhập Dữ Liệu", command=run_import, fg_color=UIThemeTokens.STATUS_LIVE, hover_color="#15803d", height=32, text_color="#ffffff", font=('Segoe UI', 11, 'bold')).pack(side='right', padx=2)

    dlg.after(200, do_preview)


def export_profiles():
    if not _license_guard(): return
    sel = tree.selection()
    selected_names = [tree.item(i)['values'][0] for i in sel] if sel else []

    dlg = ctk.CTkToplevel(root)
    dlg.title("DONGLAO-TIKTOK — Xuất Dữ Liệu Tài Khoản")
    fit_and_center_dialog(dlg, 880, 640, parent=root, min_w=650, min_h=450)
    dlg.configure(fg_color=UIThemeTokens.BG_ROOT)
    dlg.transient(root)
    try:
        dlg.grab_set()
    except Exception:
        pass

    top = ctk.CTkFrame(dlg, fg_color=UIThemeTokens.BG_CARD, corner_radius=10, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
    top.pack(side='top', fill='x', padx=12, pady=(12, 6))
    top.grid_columnconfigure(1, weight=1)

    scope_var = StringVar(value='Tất cả')
    ctk.CTkLabel(top, text="Phạm vi:", font=('Segoe UI', 11, 'bold'), text_color=UIThemeTokens.TEXT_PRIMARY).grid(row=0, column=0, sticky='w', padx=10, pady=(8, 4))
    scope_row = ctk.CTkFrame(top, fg_color='transparent')
    scope_row.grid(row=0, column=1, sticky='w', padx=4, pady=(8, 4))
    ctk.CTkRadioButton(scope_row, text="Tất cả", variable=scope_var, value='Tất cả').pack(side='left', padx=(0, 14))
    ctk.CTkRadioButton(scope_row, text="Đã chọn", variable=scope_var, value='Đã chọn').pack(side='left')

    field_row = ctk.CTkScrollableFrame(dlg, width=400, height=64, label_text="Nhấn đôi để chèn trường")
    field_row.pack(side='top', fill='x', padx=10, pady=4)
    inner = ctk.CTkFrame(field_row, fg_color='transparent')
    inner.pack(fill='x')
    for field in DEFAULT_FIELDS:
        ctk.CTkButton(
            inner, text=field, height=24, fg_color='#e2e8f0', hover_color='#cbd5e1', text_color='#0f172a',
            command=lambda f=field: format_var.set((format_var.get() + ('' if format_var.get().endswith('|') else '|') + f)),
        ).pack(side='left', padx=2, pady=2)

    # 1. Pinned Bottom Action Bar
    btn_row = ctk.CTkFrame(dlg, fg_color='transparent')
    btn_row.pack(side='bottom', fill='x', padx=10, pady=(4, 10))

    # 2. Expandable Preview Section
    prev_frame = ctk.CTkFrame(dlg)
    prev_frame.pack(side='top', fill='both', expand=True, padx=10, pady=4)
    prev_tree = ttk.Treeview(prev_frame, columns=('name', 'email', 'tiktok', 'proxy'), show='headings', height=12)
    for col, text, width in (('name', 'Name', 180), ('email', 'Email', 200), ('tiktok', 'TikTok ID', 140), ('proxy', 'Proxy', 180)):
        prev_tree.heading(col, text=text)
        prev_tree.column(col, width=width, anchor='w')
    prev_tree.pack(fill='both', expand=True)

    def _collect():
        if scope_var.get() == 'Đã chọn':
            names = [n for n in selected_names if n in profiles]
            if not names:
                messagebox.showwarning("Xuất", "Không có profile nào được chọn.")
                return None
        else:
            names = sorted(profiles.keys())
        try:
            fields = parse_format(format_var.get())
        except ValueError as exc:
            messagebox.showerror("Format không hợp lệ", str(exc))
            return None
        records = [record_from_config(n, profiles[n]['config']) for n in names]
        return fields, records

    def refresh_preview():
        for item in prev_tree.get_children():
            prev_tree.delete(item)
        try:
            fields, records = _collect()
        except Exception:
            return
        if not records:
            return
        for record in records[:100]:
            mask = masked_record(record)
            prev_tree.insert('', 'end', values=(mask.get('name', ''), mask.get('email', ''), mask.get('tiktok_id', ''), mask.get('proxy_string', '')))

    def _confirm_sensitive(fields):
        sensitive = [f for f in fields if f in SENSITIVE_FIELDS]
        if not sensitive:
            return True
        return messagebox.askyesno(
            "Cảnh báo bảo mật",
            "File xuất sẽ chứa dữ liệu nhạy cảm (không mã hóa):\n" + ', '.join(sensitive) +
            "\n\nBạn có chắc muốn xuất không?",
        )

    def do_copy():
        result = _collect()
        if not result:
            return
        fields, records = result
        if not _confirm_sensitive(fields):
            return
        text = serialize_records(fields, records)
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        messagebox.showinfo("Xuất", f"Đã sao chép {len(records)} profile vào clipboard.")

    def do_save():
        result = _collect()
        if not result:
            return
        fields, records = result
        if not _confirm_sensitive(fields):
            return
        path = filedialog.asksaveasfilename(
            defaultextension='.txt',
            filetypes=[('Text files', '*.txt'), ('All files', '*.*')],
        )
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(serialize_records(fields, records))
        except Exception as exc:
            messagebox.showerror("Lỗi", f"Không lưu được file:\n{exc}")
            return
        messagebox.showinfo("Xuất", f"Đã lưu {len(records)} profile vào:\n{path}")

    btn_row = ctk.CTkFrame(dlg)
    btn_row.pack(fill='x', padx=10, pady=(4, 10))
    ctk.CTkButton(btn_row, text="Xem trước", command=refresh_preview, fg_color="#2563eb", hover_color="#1d4ed8").pack(side='left', padx=2)
    ctk.CTkButton(btn_row, text="Sao chép", command=do_copy, fg_color="#64748b", hover_color="#475569").pack(side='right', padx=2)
    ctk.CTkButton(btn_row, text="Lưu file TXT", command=do_save, fg_color="#16a34a", hover_color="#15803d").pack(side='right', padx=2)

    dlg.after(200, refresh_preview)


# =========================
# Dialog UI helpers (shared)
# =========================
def _dialog_size(pref_w, pref_h, margin=48):
    try:
        work_w = root.winfo_screenwidth()
        work_h = root.winfo_screenheight()
    except Exception:
        work_w, work_h = 1366, 768
    w, h, _, _, _ = calculate_centered_geometry(
        pref_w, pref_h, work_w, work_h, margin_w=margin, margin_h=margin + 48
    )
    return w, h


def _ui_card(parent, title, subtitle=None):
    card = ctk.CTkFrame(parent, corner_radius=12, fg_color='#ffffff', border_width=1, border_color='#e5e7eb')
    card.pack(fill='x', padx=10, pady=(8, 2))
    header = ctk.CTkFrame(card, fg_color='transparent')
    header.pack(fill='x', padx=14, pady=(10, 2))
    ctk.CTkLabel(header, text=title, font=('Segoe UI Semibold', 14), text_color='#0f172a').pack(anchor='w')
    if subtitle:
        ctk.CTkLabel(header, text=subtitle, font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w', pady=(2, 0))
    body = ctk.CTkFrame(card, fg_color='transparent')
    body.pack(fill='both', expand=True, padx=14, pady=(4, 12))
    return card, body


def _ui_footer(dialog, primary_text, primary_command, secondary_text='Đóng', secondary_command=None):
    footer = ctk.CTkFrame(dialog, corner_radius=12, fg_color='#ffffff', border_width=1, border_color='#e5e7eb')
    footer.pack(side='bottom', fill='x', padx=10, pady=10)
    if secondary_command is None:
        secondary_command = dialog.destroy
    ctk.CTkButton(footer, text=secondary_text, fg_color='#f1f5f9', text_color='#334155',
                  hover_color='#e2e8f0', command=secondary_command).pack(side='left', padx=8, pady=6)
    ctk.CTkButton(footer, text=primary_text, fg_color='#2563eb', hover_color='#1d4ed8',
                  text_color='#ffffff', command=primary_command).pack(side='right', padx=8, pady=6)
    return footer


def _ui_badge(parent, text, color):
    ctk.CTkLabel(parent, text=text, fg_color=color, text_color='#ffffff',
                 corner_radius=10, font=('Segoe UI Semibold', 10), padx=8).pack(side='left', padx=(0, 6))


def _cell_copy(parent, value):
    btn = ctk.CTkButton(parent, text='Copy', width=50, height=26, fg_color='#eef2ff',
                        text_color='#2563eb', hover_color='#dbeafe', font=('Segoe UI', 10))
    def do():
        if not value:
            return
        try:
            root.clipboard_clear()
            root.clipboard_append(value)
            root.update()
        except Exception:
            pass
        btn.configure(text='Đã copy')
        root.after(1200, lambda: btn.configure(text='Copy'))
    btn.configure(command=do)
    btn.pack(side='right', padx=(4, 0))
    return btn


def _edit_field(body, r, c, label, value=''):
    frame = ctk.CTkFrame(body, fg_color='transparent')
    frame.grid(row=r, column=c, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(frame, text=label, font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    entry = ctk.CTkEntry(frame, height=32, border_width=1, border_color='#cbd5e1')
    entry.insert(0, value)
    entry.pack(fill='x', pady=(3, 0))
    return frame, entry


def _edit_secret(body, r, c, label, value=''):
    frame = ctk.CTkFrame(body, fg_color='transparent')
    frame.grid(row=r, column=c, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(frame, text=label, font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    row = ctk.CTkFrame(frame, fg_color='transparent')
    row.pack(fill='x', pady=(3, 0))
    entry = ctk.CTkEntry(row, show='*', height=32, border_width=1, border_color='#cbd5e1')
    entry.insert(0, value)
    entry.pack(side='left', fill='x', expand=True)
    btn = ctk.CTkButton(row, text='Hiện', width=48, height=32, fg_color='#eef2ff',
                        text_color='#2563eb', hover_color='#dbeafe', font=('Segoe UI', 10))
    def toggle(b=btn, e=entry):
        if e.cget('show'):
            e.configure(show='')
            b.configure(text='Ẩn')
        else:
            e.configure(show='*')
            b.configure(text='Hiện')
    btn.configure(command=toggle)
    btn.pack(side='left', padx=(4, 0))
    return frame, entry


def _edit_check(parent, r, c, text, value):
    frame = ctk.CTkFrame(parent, fg_color='transparent')
    frame.grid(row=r, column=c, sticky='w', padx=8, pady=4)
    var = ctk.BooleanVar(frame, value=value)
    ctk.CTkCheckBox(frame, text=text, variable=var, font=('Segoe UI', 12)).pack(anchor='w')
    return frame, var


def _evaluate_proxy_environment_change(profile_name, cfg, proxy_data, proxy_string):
    """Resolve a new proxy, compare continuity and record an audit entry.

    Never raises; best-effort. Returns a dict with ``decision``, ``warnings``
    and ``resolved`` so the caller can inform the user without blocking.
    """
    previous = proxy_environment_snapshot(cfg.get('fingerprint', {}))
    try:
        resolved = resolve_geoip(proxy_data, timeout=8)
    except Exception as error:
        current = dict(previous)
        current['geo_exit_ip'] = str(proxy_data.get('ip', ''))
        message = "Không xác minh được môi trường proxy mới: {}".format(error)
        apply_proxy_environment_warning(cfg, PROXY_ENV_UNKNOWN, previous, current, message)
        return {'decision': PROXY_ENV_UNKNOWN, 'warnings': [message], 'resolved': False}
    comparison = compare_proxy_environment(previous, resolved)
    warning = ' | '.join(comparison['warnings']) or comparison['decision']
    apply_proxy_environment_warning(cfg, comparison['decision'], previous, resolved, warning)
    return dict(comparison, resolved=True)


def edit_profile(selected_name=None):
    if not _license_guard(): return
    if selected_name is None:
        sel = tree.selection()
        if not sel: return
        selected_name = tree.item(sel[0])['values'][0]
    nm = selected_name
    if profiles[nm].get('running') or profiles[nm].get('session_busy') or _browser_session_valid(profiles[nm].get('manual_driver')):
        messagebox.showwarning('Sửa tài khoản', 'Hãy Stop profile và đóng browser trước khi sửa.')
        return
    if _blocked_by_profile_conflict(nm):
        messagebox.showerror('Sửa tài khoản', _profile_conflict_message(nm))
        return
    cfg = profiles[nm]['config']
    ensure_account_uuid(cfg)
    fingerprint_backup = copy.deepcopy(cfg.get('fingerprint', {}))

    dlg = ctk.CTkToplevel(root)
    dlg.title(f"Sửa tài khoản: {nm}")
    fit_and_center_dialog(dlg, 940, 700, parent=root, min_w=600, min_h=450)
    dlg.transient(root)
    try:
        dlg.grab_set()
    except Exception:
        pass

    scroll = ctk.CTkScrollableFrame(dlg, fg_color='#f3f4f6')
    scroll.pack(fill='both', expand=True, padx=10, pady=(10, 0))

    # --- Card: Tài khoản ---
    card1, body1 = _ui_card(scroll, 'Tài khoản', 'Thông tin nhận diện tài khoản')
    body1.grid_columnconfigure(0, weight=1)
    body1.grid_columnconfigure(1, weight=1)
    _, e_email = _edit_field(body1, 0, 0, 'Email', cfg.get('email', ''))
    _, e_tiktok_id = _edit_field(body1, 0, 1, 'TikTok ID', cfg.get('tiktok_id', ''))
    note_row = ctk.CTkFrame(body1, fg_color='transparent')
    note_row.grid(row=1, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(note_row, text='Ghi chú', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    e_note = ctk.CTkTextbox(note_row, height=64, wrap='word', border_width=1, border_color='#cbd5e1')
    e_note.insert('1.0', cfg.get('note', ''))
    e_note.pack(fill='x', pady=(3, 0))

    # --- Card: Bảo mật ---
    card2, body2 = _ui_card(scroll, 'Bảo mật', 'Thông tin đăng nhập và mã xác thực')
    body2.grid_columnconfigure(0, weight=1)
    body2.grid_columnconfigure(1, weight=1)
    _, e_password = _edit_secret(body2, 0, 0, 'Mật khẩu TikTok', cfg.get('password', ''))
    _, e_auth2fa = _edit_secret(body2, 0, 1, 'Khóa 2FA', cfg.get('auth2fa', ''))
    _, e_passmail = _edit_secret(body2, 1, 0, 'Mật khẩu email', cfg.get('passmail', ''))
    _, e_mail_backup = _edit_field(body2, 1, 1, 'Email backup', cfg.get('mail_backup', ''))
    _, e_pass_mail_backup = _edit_secret(body2, 2, 0, 'Mật khẩu email backup', cfg.get('pass_mail_backup', ''))
    cookie_row = ctk.CTkFrame(body2, fg_color='transparent')
    cookie_row.grid(row=3, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(cookie_row, text='Cookie (tùy chọn, dùng khi chưa có session)', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    e_cookie = ctk.CTkTextbox(cookie_row, height=88, wrap='word', border_width=1, border_color='#cbd5e1')
    e_cookie.insert('1.0', cfg.get('cookie_str', ''))
    e_cookie.pack(fill='x', pady=(3, 0))

    # --- Card: Dữ liệu tài khoản ---
    card3, body3 = _ui_card(scroll, 'Dữ liệu tài khoản', 'Thư mục video và browser profile riêng theo tài khoản')
    body3.grid_columnconfigure(0, weight=1)
    body3.grid_columnconfigure(1, weight=1)
    uuid_frame = ctk.CTkFrame(body3, fg_color='transparent')
    uuid_frame.grid(row=0, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(uuid_frame, text='Mã tài khoản (bất biến)', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    ctk.CTkLabel(uuid_frame, text=str(cfg.get('account_uuid', '')), font=('Segoe UI', 12),
                 text_color='#0f172a', anchor='w').pack(anchor='w', pady=(3, 0))
    folder_frame = ctk.CTkFrame(body3, fg_color='transparent')
    folder_frame.grid(row=1, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(folder_frame, text='Thư mục video', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    folder_row = ctk.CTkFrame(folder_frame, fg_color='transparent')
    folder_row.pack(fill='x', pady=(3, 0))
    e_folder = ctk.CTkEntry(folder_row, height=32, border_width=1, border_color='#cbd5e1')
    e_folder.insert(0, cfg['folder_path'])
    e_folder.pack(side='left', fill='x', expand=True)
    ctk.CTkButton(folder_row, text='Chọn...', width=70, height=32, fg_color='#eef2ff', text_color='#2563eb',
                  hover_color='#dbeafe', command=lambda: _browse_dir(e_folder)).pack(side='left', padx=(4, 0))
    ctk.CTkButton(folder_row, text='Mở', width=44, height=32, fg_color='#f1f5f9', text_color='#334155',
                  hover_color='#e2e8f0', command=lambda: _open_dir(e_folder.get())).pack(side='left', padx=(4, 0))
    chrome_frame = ctk.CTkFrame(body3, fg_color='transparent')
    chrome_frame.grid(row=2, column=0, columnspan=2, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(chrome_frame, text='Chrome User Data (browser profile)', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    chrome_row = ctk.CTkFrame(chrome_frame, fg_color='transparent')
    chrome_row.pack(fill='x', pady=(3, 0))
    e_chrome = ctk.CTkEntry(chrome_row, height=32, border_width=1, border_color='#cbd5e1')
    e_chrome.insert(0, cfg.get('chrome_profile', ''))
    e_chrome.pack(side='left', fill='x', expand=True)
    ctk.CTkButton(chrome_row, text='Chọn...', width=70, height=32, fg_color='#eef2ff', text_color='#2563eb',
                  hover_color='#dbeafe', command=lambda: _browse_dir(e_chrome)).pack(side='left', padx=(4, 0))
    _, e_proj = _edit_field(body3, 3, 0, 'Project', profiles[nm].get('project', 'Mặc định'))
    browser_path = cfg.get('browser_profile_path', '') or cfg.get('chrome_profile', '')
    browser_frame = ctk.CTkFrame(body3, fg_color='transparent')
    browser_frame.grid(row=3, column=1, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(browser_frame, text='Browser profile đang dùng', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    browser_row = ctk.CTkFrame(browser_frame, fg_color='transparent')
    browser_row.pack(fill='x', pady=(3, 0))
    e_browser_ro = ctk.CTkEntry(browser_row, height=32, border_width=1, border_color='#cbd5e1')
    e_browser_ro.insert(0, browser_path)
    e_browser_ro.configure(state='readonly')
    e_browser_ro.pack(side='left', fill='x', expand=True)
    ctk.CTkButton(browser_row, text='Mở', width=44, height=32, fg_color='#f1f5f9', text_color='#334155',
                  hover_color='#e2e8f0', command=lambda: _open_dir(browser_path)).pack(side='left', padx=(4, 0))

    # --- Card: Proxy & vận hành ---
    card4, body4 = _ui_card(scroll, 'Proxy & vận hành')
    body4.grid_columnconfigure(0, weight=1)
    body4.grid_columnconfigure(1, weight=1)
    _, v_use_proxy = _edit_check(body4, 0, 0, 'Sử dụng Proxy', cfg.get('use_proxy', False))
    proxy_type_frame = ctk.CTkFrame(body4, fg_color='transparent')
    proxy_type_frame.grid(row=0, column=1, sticky='e', padx=8, pady=4)
    ctk.CTkLabel(proxy_type_frame, text='Loại proxy', font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    v_proxy_type = ctk.StringVar(proxy_type_frame, value=cfg.get('proxy_type', 'http'))
    ctk.CTkOptionMenu(proxy_type_frame, values=['http', 'socks5'], variable=v_proxy_type,
                      width=110, height=32, fg_color='#f8fafc', button_color='#2563eb',
                      button_hover_color='#1d4ed8', font=('Segoe UI', 12)).pack(anchor='w', pady=(3, 0))
    _, e_proxy = _edit_field(body4, 1, 0, 'Proxy (IP:Port:User:Pass)', cfg.get('proxy_string', ''))
    _, e_limit = _edit_field(body4, 1, 1, 'Limit/Ngày (0 = không giới hạn)', str(cfg.get('max_uploads_per_day', 0)))
    _, v_head = _edit_check(body4, 2, 0, 'Headless', cfg.get('headless', True))
    _, v_open_only = _edit_check(body4, 2, 1, 'Chỉ mở khi có video mới', cfg.get('open_only_when_video', False))
    geo_frame = ctk.CTkFrame(body4, fg_color='transparent')
    geo_frame.grid(row=3, column=0, columnspan=2, sticky='w', padx=8, pady=4)
    geo_label = ctk.CTkLabel(geo_frame, text='GeoIP: chưa tra', font=('Segoe UI', 12), text_color='#0f172a')
    geo_label.pack(side='left')

    def _fmt_geo():
        fp = cfg.get('fingerprint', {})
        geo = fp.get('geolocation') or {}
        tz = fp.get('timezone', 'chưa tra')
        lat = geo.get('latitude', '-')
        lon = geo.get('longitude', '-')
        return f"GeoIP: {tz} | {lat} / {lon}"

    def _apply_geo_label():
        geo_label.configure(text=_fmt_geo())

    def refresh_geo():
        if not v_use_proxy.get():
            geo_label.configure(text='GeoIP: cần bật và nhập proxy hợp lệ')
            return
        proxy_data = parse_proxy_string(e_proxy.get().strip())
        if not proxy_data:
            geo_label.configure(text='GeoIP: cần proxy hợp lệ')
            return
        try:
            ok = _refresh_profile_geoip(nm, cfg, proxy_data, force=True)
        except Exception as error:
            geo_label.configure(text=f'GeoIP: lỗi {error}')
            return
        if ok:
            _apply_geo_label()
        else:
            geo_label.configure(text='GeoIP: tra cứu thất bại')

    _apply_geo_label()
    ctk.CTkButton(geo_frame, text='Làm mới GeoIP', width=110, height=30, fg_color='#eef2ff', text_color='#2563eb',
                  hover_color='#dbeafe', font=('Segoe UI', 11), command=refresh_geo).pack(side='left', padx=(8, 0))
    ctk.CTkLabel(geo_frame, text='Lưu thay đổi mới ghi vào config', font=('Segoe UI', 10),
                 text_color='#94a3b8').pack(side='left', padx=(8, 0))

    def _snapshot():
        return {
            'folder': e_folder.get(), 'chrome': e_chrome.get(),
            'cookie': e_cookie.get('1.0', 'end').rstrip('\n'),
            'email': e_email.get(), 'password': e_password.get(),
            'tiktok_id': e_tiktok_id.get(), 'auth2fa': e_auth2fa.get(),
            'passmail': e_passmail.get(), 'mail_backup': e_mail_backup.get(),
            'pass_mail_backup': e_pass_mail_backup.get(), 'note': e_note.get('1.0', 'end').rstrip('\n'),
            'proxy': e_proxy.get(), 'use_proxy': v_use_proxy.get(),
            'headless': v_head.get(), 'open_only': v_open_only.get(),
            'limit': e_limit.get(), 'proxy_type': v_proxy_type.get(),
            'project': e_proj.get(),
        }
    initial_snapshot = _snapshot()

    def _is_dirty():
        return _snapshot() != initial_snapshot

    def _restore_fingerprint():
        if fingerprint_backup:
            cfg['fingerprint'] = copy.deepcopy(fingerprint_backup)

    def _close(confirm=True):
        if confirm and _is_dirty():
            if not messagebox.askyesno('Chưa lưu', 'Bạn có thay đổi chưa lưu. Bỏ thay đổi?'):
                return
        _restore_fingerprint()
        dlg.destroy()

    dlg.protocol('WM_DELETE_WINDOW', _close)
    dlg.bind('<Escape>', lambda e: _close())

    def save():
        errors = []
        limit_raw = e_limit.get().strip()
        try:
            lm = int(limit_raw) if limit_raw else 0
        except ValueError:
            lm = -1
        if lm < 0:
            errors.append('Limit/Ngày phải là số nguyên >= 0.')
        proxy_str = e_proxy.get().strip()
        if v_use_proxy.get():
            if not proxy_str:
                errors.append('Chưa nhập proxy (đang bật Sử dụng Proxy).')
            elif not parse_proxy_string(proxy_str):
                errors.append('Proxy sai định dạng (IP:Port:User:Pass).')
        if errors:
            messagebox.showerror('Kiểm tra dữ liệu', '\n'.join(errors))
            return
        new_chrome_profile = e_chrome.get().strip()
        old_chrome_profile = str(cfg.get('chrome_profile', ''))
        if normalize_profile_path(new_chrome_profile) != normalize_profile_path(old_chrome_profile):
            duplicate = _find_profile_with_data_dir(new_chrome_profile, exclude_name=nm)
            if duplicate:
                messagebox.showerror('Lỗi', f"Chrome User Data đang được hồ sơ '{duplicate}' sử dụng.")
                return
            try:
                if new_chrome_profile and not Path(new_chrome_profile).exists():
                    create_owned_root(new_chrome_profile)
            except Exception as error:
                messagebox.showerror('Lỗi', f"Không tạo được Chrome User Data an toàn: {error}")
                return
        new_cookie = e_cookie.get('1.0', 'end').rstrip('\n')
        new_tiktok_id = _normalize_tiktok_id(e_tiktok_id.get())
        new_email = e_email.get().strip()
        invalidation = []
        for key, new_val in (
            ('cookie_str', new_cookie),
            ('tiktok_id', new_tiktok_id),
            ('email', new_email),
            ('proxy_string', proxy_str),
        ):
            if str(new_val or '') != str(cfg.get(key, '') or ''):
                invalidation.append(key)
        proxy_changed = 'proxy_string' in invalidation
        proxy_disabled = v_use_proxy.get() is False
        env_comparison = None
        if proxy_changed and v_use_proxy.get():
            proxy_data = parse_proxy_string(proxy_str)
            if proxy_data:
                env_comparison = _evaluate_proxy_environment_change(nm, cfg, proxy_data, proxy_str)
            else:
                current = proxy_environment_snapshot(cfg.get('fingerprint', {}))
                apply_proxy_environment_warning(
                    cfg, PROXY_ENV_UNKNOWN, current, current,
                    'Proxy mới không parse được; môi trường chưa được xác minh.',
                )
                env_comparison = {
                    'decision': PROXY_ENV_UNKNOWN,
                    'warnings': ['Proxy mới không parse được; môi trường chưa được xác minh.'],
                    'resolved': False,
                }
        elif proxy_changed and proxy_disabled:
            current = proxy_environment_snapshot(cfg.get('fingerprint', {}))
            apply_proxy_environment_warning(
                cfg, PROXY_ENV_UNKNOWN, current, current,
                'Proxy đã bị tắt; môi trường proxy không còn được xác minh.',
            )
            env_comparison = {
                'decision': PROXY_ENV_UNKNOWN,
                'warnings': ['Proxy đã bị tắt; môi trường proxy không còn được xác minh.'],
                'resolved': False,
            }
        cfg.update({
            "folder_path": e_folder.get().strip(),
            "chrome_profile": new_chrome_profile,
            "cookie_str": new_cookie,
            "email": new_email,
            "password": e_password.get(),
            "tiktok_id": new_tiktok_id,
            "auth2fa": e_auth2fa.get(),
            "passmail": e_passmail.get(),
            "mail_backup": e_mail_backup.get().strip(),
            "pass_mail_backup": e_pass_mail_backup.get(),
            "note": e_note.get('1.0', 'end').rstrip('\n'),
            "proxy_string": proxy_str,
            "proxy_type": v_proxy_type.get(),
            "use_proxy": v_use_proxy.get(),
            "headless": v_head.get(),
            "open_only_when_video": v_open_only.get(),
            "max_uploads_per_day": max(0, lm)
        })
        if normalize_profile_path(new_chrome_profile) != normalize_profile_path(old_chrome_profile):
            cfg['legacy_chrome_profile'] = new_chrome_profile
            cfg['browser_profile_path'] = ''
            cfg['migration_state'] = MigrationState.PENDING.value
        if invalidation:
            invalidate_session_auth(cfg, 'Thay đổi khi sửa: ' + ', '.join(invalidation))
        cfg['fingerprint'] = ensure_fingerprint_defaults(
            cfg.get('fingerprint', fingerprint_backup),
            seed=nm + str(cfg.get('account_uuid', '')),
        )
        save_configs()
        dlg.destroy()
        if env_comparison is not None:
            decision = env_comparison.get('decision')
            warnings = env_comparison.get('warnings') or []
            if decision == PROXY_ENV_RISKY:
                messagebox.showwarning(
                    'Đổi môi trường proxy rủi ro',
                    'Thay đổi proxy làm môi trường khác Country/ASN/Timezone:\n\n'
                    + '\n'.join(warnings)
                    + '\n\nBrowser profile và fingerprint hiện tại vẫn được giữ nguyên. '
                    'Nếu bạn muốn đăng nhập trong môi trường mới, hãy dùng nút '
                    '"Reset Browser" -> "Tạo môi trường login mới".',
                )
            elif decision == PROXY_ENV_UNKNOWN:
                messagebox.showinfo(
                    'Môi trường proxy chưa xác minh',
                    '\n'.join(warnings)
                    + '\n\nHãy mở lại "Sửa tài khoản" và bấm "Làm mới GeoIP" '
                    'sau khi proxy hoạt động để xác minh môi trường.',
                )
            elif warnings:
                messagebox.showinfo(
                    'Thay đổi proxy tương thích',
                    '\n'.join(warnings),
                )

    _ui_footer(dlg, 'Lưu thay đổi', save, secondary_text='Hủy', secondary_command=_close)


def _browse_dir(entry):
    chosen = filedialog.askdirectory()
    if chosen:
        entry.delete(0, 'end')
        entry.insert(0, chosen)


def _open_dir(path):
    if not path:
        return
    p = Path(path)
    if not p.exists():
        try:
            create_owned_root(str(p))
        except Exception:
            pass
    try:
        os.startfile(str(p))
    except Exception:
        pass
def rename_profile():
    if not _license_guard(): return
    sel = tree.selection()
    if not sel: return
    old = tree.item(sel[0])['values'][0]
    if profiles[old]['running'] or profiles[old].get('session_busy') or _browser_session_valid(profiles[old].get('manual_driver')) or get_lifecycle(old).has_active_driver():
        messagebox.showerror("Lỗi", "Hãy dừng hồ sơ và đóng browser trước")
        return
    dlg = ctk.CTkToplevel(root)
    dlg.title("Đổi tên")
    fit_and_center_dialog(dlg, 340, 180, parent=root, min_w=280, min_h=140)
    ctk.CTkLabel(dlg, text="Tên mới:").pack(pady=5)
    e = ctk.CTkEntry(dlg, width=200)
    e.pack(pady=5)
    def save():
        new = e.get().strip()
        if not new or new in profiles:
            messagebox.showerror("Lỗi", "Tên không hợp lệ")
            return
        prof = profiles.pop(old)
        p = prof.get('project', 'Mặc định')
        if p in projects:
            projects[p].discard(old)
            projects[p].add(new)
        profiles[new] = prof
        remove_lifecycle(old)
        profile_operation_locks.pop(old, None)
        try:
            youtube_monitor.rename_channel_profile(old, new)
        except Exception as error:
            update_status(f"[UI] Không đồng bộ channel khi đổi tên: {error}")
        save_configs()
        update_profile_list()
        dlg.destroy()
    ctk.CTkButton(dlg, text="Lưu", command=save).pack(pady=10)

def delete_profile():
    if not _license_guard(): return
    sel = tree.selection()
    if not sel: return

    # Collect all selected profile names
    selected_names = []
    for s in sel:
        vals = tree.item(s).get('values')
        if vals and len(vals) > 0:
            nm = str(vals[0])
            if nm in profiles and nm not in selected_names:
                selected_names.append(nm)

    if not selected_names:
        return

    # Check if any profile is currently running or browser is open
    running_profiles = [
        nm for nm in selected_names
        if profiles[nm].get('running') or profiles[nm].get('session_busy')
        or _browser_session_valid(profiles[nm].get('manual_driver'))
        or get_lifecycle(nm).has_active_driver()
    ]
    if running_profiles:
        messagebox.showerror(
            "Lỗi",
            f"Các hồ sơ sau đang chạy hoặc đang mở trình duyệt:\n\n"
            f"{', '.join(running_profiles[:5])}{'...' if len(running_profiles) > 5 else ''}\n\n"
            "Vui lòng dừng hồ sơ và đóng browser trước khi xoá."
        )
        return

    count = len(selected_names)
    if count == 1:
        nm = selected_names[0]
        msg = f"Bạn có chắc muốn xoá hồ sơ '{nm}' khỏi cấu hình?"
    else:
        msg = f"Bạn có chắc muốn xoá {count} hồ sơ đã chọn khỏi cấu hình?"

    ok = messagebox.askyesno("Xác nhận xoá hồ sơ", msg)
    if not ok: return

    # Check YouTube channel links
    total_linked_channels = 0
    try:
        for nm in selected_names:
            total_linked_channels += youtube_monitor.channel_count_for_profile(nm)
        if total_linked_channels > 0:
            ok_channels = messagebox.askyesno(
                "Cảnh báo channel",
                f"Có {total_linked_channels} channel YouTube đang liên kết với các hồ sơ này.\n\n"
                "Các channel này sẽ trở thành orphan và cần chọn lại profile TikTok.\n"
                "Bạn có chắc chắn muốn tiếp tục xoá?",
            )
            if not ok_channels: return
    except Exception:
        pass

    # Prompt for disk cleanup option
    delete_disk = messagebox.askyesno(
        "Xóa dữ liệu ổ đĩa",
        f"Bạn có muốn XÓA LUÔN thư mục dữ liệu trình duyệt trên ổ đĩa của {count} hồ sơ này để giải phóng dung lượng không?\n\n"
        "• Chọn 'Yes' (Có): Xoá hồ sơ và dọn sạch dữ liệu trình duyệt trên ổ cứng.\n"
        "• Chọn 'No' (Không): Chỉ xoá khỏi danh sách quản lý, giữ lại dữ liệu trên ổ cứng."
    )

    deleted_count = 0
    for nm in selected_names:
        prof = profiles.get(nm, {})
        if delete_disk:
            try:
                p_dir = browser_glue.active_profile_path(prof.get('config', {}))
                if p_dir and os.path.isdir(p_dir):
                    shutil.rmtree(p_dir, ignore_errors=True)
            except Exception as e:
                update_status(f"[{nm}] [WARN] Không thể xóa thư mục profile trên đĩa: {e}")

        p = prof.get('project')
        if p in projects:
            projects[p].discard(nm)
        profiles.pop(nm, None)
        remove_lifecycle(nm)
        profile_operation_locks.pop(nm, None)
        monetization_cache.pop(nm, None)
        deleted_count += 1

    save_configs(allow_truncate=True)
    update_profile_list()
    update_status(f"[UI] Đã xoá {deleted_count} hồ sơ thành công.")

# =========================
# Chi tiết tài khoản + Trợ giúp nhập/xuất
# =========================
def _detail_cell(body, r, c, label, value, sensitive=False, multiline=False):
    frame = ctk.CTkFrame(body, fg_color='transparent')
    frame.grid(row=r, column=c, sticky='nsew', padx=8, pady=4)
    ctk.CTkLabel(frame, text=label, font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w')
    val_row = ctk.CTkFrame(frame, fg_color='transparent')
    val_row.pack(fill='x', pady=(3, 0))
    real = str(value or '')
    if multiline:
        box = ctk.CTkTextbox(val_row, height=90, wrap='word', fg_color='#f8fafc',
                             border_width=1, border_color='#e5e7eb')
        box.insert('1.0', real)
        box.configure(state='disabled')
        box.pack(fill='x')
        if real:
            _cell_copy(val_row, real)
    elif sensitive:
        entry = ctk.CTkEntry(val_row, show='*', fg_color='#f8fafc', border_width=1, border_color='#e5e7eb')
        entry.insert(0, real)
        entry.configure(state='readonly')
        entry.pack(side='left', fill='x', expand=True)
        btn = ctk.CTkButton(val_row, text='Hiện', width=48, height=26, fg_color='#eef2ff',
                            text_color='#2563eb', hover_color='#dbeafe', font=('Segoe UI', 10))
        def toggle(b=btn, e=entry):
            if e.cget('show'):
                e.configure(show='')
                b.configure(text='Ẩn')
            else:
                e.configure(show='*')
                b.configure(text='Hiện')
        btn.configure(command=toggle)
        btn.pack(side='left', padx=(4, 0))
        if real:
            _cell_copy(val_row, real)
    else:
        if real:
            ctk.CTkLabel(val_row, text=real, font=('Segoe UI', 12), text_color='#0f172a',
                         anchor='w', justify='left', wraplength=300).pack(side='left', fill='x', expand=True)
            _cell_copy(val_row, real)
        else:
            ctk.CTkLabel(val_row, text='Chưa thiết lập', font=('Segoe UI', 12), text_color='#94a3b8',
                         anchor='w').pack(side='left', fill='x', expand=True)
    return frame


def view_profile_details(selected_name=None):
    if not _license_guard(): return
    if selected_name is None:
        sel = tree.selection()
        if not sel: return
        selected_name = tree.item(sel[0])['values'][0]
    if selected_name not in profiles: return
    cfg = profiles[selected_name]['config']
    ensure_account_uuid(cfg)

    dlg = ctk.CTkToplevel(root)
    dlg.title(f"Chi tiết tài khoản: {selected_name}")
    fit_and_center_dialog(dlg, 900, 660, parent=root, min_w=550, min_h=420)
    dlg.transient(root)
    try:
        dlg.grab_set()
    except Exception:
        pass

    scroll = ctk.CTkScrollableFrame(dlg, fg_color='#f3f4f6')
    scroll.pack(fill='both', expand=True, padx=10, pady=(10, 0))

    header = ctk.CTkFrame(scroll, corner_radius=12, fg_color='#ffffff', border_width=1, border_color='#e5e7eb')
    header.pack(fill='x', pady=(0, 4))
    initial = (selected_name[:1] or '?').upper()
    avatar = ctk.CTkLabel(header, text=initial, width=46, height=46, corner_radius=23,
                          fg_color='#2563eb', text_color='#ffffff', font=('Segoe UI Semibold', 18))
    avatar.pack(side='left', padx=(14, 12), pady=12)
    text_col = ctk.CTkFrame(header, fg_color='transparent')
    text_col.pack(side='left', fill='x', expand=True, pady=12)
    ctk.CTkLabel(text_col, text=selected_name, font=('Segoe UI Semibold', 18), text_color='#0f172a').pack(anchor='w')
    subtitle_parts = []
    tiktok_id = str(cfg.get('tiktok_id', '') or '').lstrip('@')
    if tiktok_id:
        subtitle_parts.append('@' + tiktok_id)
    subtitle_parts.append('ID: ' + str(cfg.get('account_uuid', ''))[:8])
    ctk.CTkLabel(text_col, text='   •   '.join(subtitle_parts), font=('Segoe UI', 12), text_color='#64748b').pack(anchor='w', pady=(2, 0))
    badges = ctk.CTkFrame(header, fg_color='transparent')
    badges.pack(side='right', padx=12, pady=12)
    session_state = cfg.get('session_auth_state', 'unknown')
    if session_state == 'verified':
        source = cfg.get('session_source', '')
        if source == 'manual_login':
            _ui_badge(badges, 'Session đã lưu', '#16a34a')
        elif source == 'cookie_fallback':
            _ui_badge(badges, 'Cookie dự phòng', '#d97706')
        else:
            _ui_badge(badges, 'Session profile', '#16a34a')
    elif session_state == 'expired':
        _ui_badge(badges, 'Cần đăng nhập', '#dc2626')
    else:
        _ui_badge(badges, 'Chưa xác minh', '#64748b')
    if cfg.get('use_proxy', False):
        _ui_badge(badges, 'Proxy', '#2563eb')
    else:
        _ui_badge(badges, 'Proxy tắt', '#94a3b8')

    card1, body1 = _ui_card(scroll, 'Tài khoản & bảo mật')
    body1.grid_columnconfigure(0, weight=1)
    body1.grid_columnconfigure(1, weight=1)
    _detail_cell(body1, 0, 0, 'Email', cfg.get('email', ''))
    _detail_cell(body1, 0, 1, 'TikTok ID', cfg.get('tiktok_id', ''))
    _detail_cell(body1, 1, 0, 'Mật khẩu TikTok', cfg.get('password', ''), sensitive=True)
    _detail_cell(body1, 1, 1, 'Khóa 2FA', cfg.get('auth2fa', ''), sensitive=True)
    _detail_cell(body1, 2, 0, 'Email backup', cfg.get('mail_backup', ''))
    _detail_cell(body1, 2, 1, 'Mật khẩu email backup', cfg.get('pass_mail_backup', ''), sensitive=True)
    _detail_cell(body1, 3, 0, 'Mật khẩu email', cfg.get('passmail', ''), sensitive=True)

    card2, body2 = _ui_card(scroll, 'Trình duyệt & vận hành')
    body2.grid_columnconfigure(0, weight=1)
    body2.grid_columnconfigure(1, weight=1)
    _detail_cell(body2, 0, 0, 'Thư mục video', cfg.get('folder_path', ''))
    _detail_cell(body2, 0, 1, 'Project', profiles[selected_name].get('project', 'Mặc định'))
    _detail_cell(body2, 1, 0, 'Browser profile', cfg.get('browser_profile_path', '') or cfg.get('chrome_profile', ''))
    _detail_cell(body2, 1, 1, 'Limit/Ngày', str(cfg.get('max_uploads_per_day', 0)))
    _detail_cell(body2, 2, 0, 'Proxy', cfg.get('proxy_string', ''), sensitive=True)
    _detail_cell(body2, 2, 1, 'Headless', 'Có' if cfg.get('headless', True) else 'Không')

    card3, body3 = _ui_card(scroll, 'Cookie')
    body3.grid_columnconfigure(0, weight=1)
    _detail_cell(body3, 0, 0, 'Cookie', cfg.get('cookie_str', ''), sensitive=True)

    if cfg.get('note'):
        card4, body4 = _ui_card(scroll, 'Ghi chú')
        body4.grid_columnconfigure(0, weight=1)
        _detail_cell(body4, 0, 0, 'Ghi chú', cfg.get('note', ''), multiline=True)

    def _edit():
        dlg.destroy()
        edit_profile(selected_name)

    _ui_footer(dlg, 'Sửa thông tin', _edit, secondary_text='Đóng')


def open_browser():
    if not _license_guard(): return
    sel = tree.selection()
    if not sel: return
    nm = tree.item(sel[0])['values'][0]
    cfg = profiles[nm]['config']
    if profiles[nm].get('running') or profiles[nm].get('uploading') or profiles[nm].get('session_busy'):
        messagebox.showwarning('Mở Chrome', 'Hãy Stop profile trước khi mở browser thủ công.')
        return
    if _browser_session_valid(profiles[nm].get('manual_driver')):
        messagebox.showwarning('Mở Chrome', 'Browser của profile này đang mở.')
        return
    if _blocked_by_profile_conflict(nm):
        messagebox.showerror('Mở Chrome', _profile_conflict_message(nm))
        return

    lc = get_lifecycle(nm)
    if lc.has_active_driver():
        messagebox.showwarning('Mở Chrome', 'Lifecycle của profile vẫn còn browser đang hoạt động.')
        return
    manual_gen = lc.begin()
    profiles[nm]['session_busy'] = True
    _set_profile_ui(nm, browser='Đang mở', last_error='')

    def _worker():
        token = None
        try:
            proxy_data = parse_proxy_string(cfg.get('proxy_string', '')) if cfg.get('use_proxy', False) else None
            if cfg.get('use_proxy', False) and not proxy_data:
                raise SessionSetupError("Proxy sai định dạng; từ chối mở browser trực tiếp")
            proxy_expected_ip = proxy_data['ip'] if proxy_data else None
            direct_ip = None
            if _refresh_profile_geoip(nm, cfg, proxy_data):
                save_configs()
            if proxy_data:
                _set_profile_ui(nm, proxy='Đang kiểm tra')
                preflight = _proxy_endpoint_preflight(nm, proxy_data)
                if not preflight['proxy_exit_ip']:
                    raise SessionSetupError("Không xác minh được proxy endpoint; từ chối mở browser")
                proxy_expected_ip = preflight['proxy_exit_ip']
                direct_ip = preflight['direct_ip']

            browser_glue.ensure_patchright_profile(cfg)
            _sync_patchright_migration(cfg)
            save_configs()
            session_config = browser_glue.build_session_config(
                cfg, mode=browser_glue.SessionMode.MANUAL, headed=True, profile_name=nm
            )
            opened = browser_glue.browser_service().open_session(session_config).result(
                timeout=browser_glue.SESSION_OPEN_TIMEOUT
            )
            token = SessionToken(
                profile_name=nm,
                handle=opened.handle,
                mode=browser_glue.SessionMode.MANUAL,
                profile_path=session_config.profile_path,
                generation=manual_gen,
            )

            if proxy_data:
                matched, current_ip = browser_glue.verify_exit_ip(token, proxy_expected_ip)
                if not matched and direct_ip and current_ip and current_ip != direct_ip:
                    matched = True
                if not matched:
                    raise SessionSetupError("Không xác minh được proxy trong browser" if not current_ip else f"Proxy sai IP: {current_ip}")
                _set_profile_ui(nm, proxy=f"OK: {current_ip}")

            # Cookie-First Injection: Ưu tiên nạp Cookie đã lưu vào phiên làm việc
            cookies = parse_cookie(cfg.get('cookie_str', ''))
            if cookies:
                try:
                    browser_glue.import_cookies_report(token, cookies)
                except Exception as e:
                    update_status(f"[{nm}] [WARN] Không thể nạp cookie sẵn: {e}")
                target_url = TIKTOK_BASE_URL
            else:
                target_url = "https://www.tiktok.com/login"

            browser_glue.navigate(token, target_url)

            if not lc.register_manual(manual_gen, token):
                raise RuntimeError("Lifecycle changed before manual session publish")
            profiles[nm]['manual_driver'] = token
            profiles[nm]['session_busy'] = False
            _set_profile_ui(nm, browser='Patchright đang mở')
            update_status(f"[{nm}] Browser thủ công đã mở. Đóng cửa sổ khi hoàn tất.")
            browser_glue.watch_manual_close(token)
        except Exception as error:
            if token:
                token.set_cancelled()
                token.quit()
            _set_profile_ui(nm, browser='Bị lỗi', last_error=str(error))
            update_status(f"[{nm}] Lỗi mở browser thủ công: {error}")
        finally:
            closed_ok = True
            if token:
                try:
                    closed_ok = token.quit()
                except Exception:
                    closed_ok = False
            if profiles.get(nm, {}).get('manual_driver') is token:
                profiles[nm]['manual_driver'] = None
            if token:
                try:
                    lc.release_manual(token)
                except Exception:
                    pass
            profiles.get(nm, {})['session_busy'] = False
            if token:
                if closed_ok:
                    _set_profile_ui(nm, browser='Đã đóng', login='Đang lưu session')
                    update_status(f"[{nm}] Browser thủ công đã đóng. Đang lưu session ngầm (Headless)...")
                    threading.Thread(target=_capture_after_manual_close, args=(nm,), daemon=True).start()
                else:
                    _set_profile_ui(nm, browser='Đóng lỗi', last_error='Browser chưa được đóng sạch; hãy thử lại')
                    update_status(f"[{nm}] Browser thủ công chưa được đóng sạch; profile có thể vẫn bị khóa.")

    threading.Thread(target=_worker, daemon=True).start()

def _capture_after_manual_close(profile_name):
    try:
        if profile_name not in profiles:
            return
        with _profile_operation_lock(profile_name):
            profile = profiles[profile_name]
            if profile.get('running') or profile.get('uploading') or profile.get('session_busy'):
                return
            profile['session_busy'] = True
            try:
                # 1. Trích xuất và lưu Cookie mới ngầm trong headless mode
                saved = _capture_tiktok_cookies_worker(
                    profile_name, source_label='manual_login', auto_after_manual=True
                )
                # 2. Tự động lấy thông tin tài khoản (UID, @Username, Region) ngầm
                if saved:
                    try:
                        _inspect_tiktok_account_worker(profile_name)
                    except Exception as ie:
                        update_status(f"[{profile_name}] [WARN] Không lấy được thông tin chi tiết: {ie}")
            finally:
                profile['session_busy'] = False
    except Exception as error:
        update_status(f"[{profile_name}] [WARN] Không lưu được session sau khi đóng browser: {error}")

def _wait_and_close_driver(driver, name):
    pass 

# --- TIkTok ID HELPER ---
def _normalize_tiktok_id(value):
    value = str(value or "").strip()
    if not value:
        return ""
    value = value.rstrip("/")
    lowered = value.lower()
    marker = "tiktok.com/@"
    if marker in lowered:
        idx = lowered.find(marker)
        value = value[idx + len(marker):]
    value = value.strip().strip("/")
    if value.startswith("@"):
        value = value[1:]
    value = value.split("?", 1)[0].split("#", 1)[0]
    return value.strip().strip("/")

def copy_channel_link():
    sel = tree.selection()
    if not sel:
        return
    name = tree.item(sel[0])['values'][0]
    if name not in profiles:
        return
    tiktok_id = _normalize_tiktok_id(profiles[name]['config'].get('tiktok_id', ''))
    if not tiktok_id:
        messagebox.showwarning("Warning", "Profile này chưa có ID TikTok.")
        return
    link = f"https://www.tiktok.com/@{tiktok_id}"
    root.clipboard_clear()
    root.clipboard_append(link)
    root.update()
    update_status(f"[{name}] Đã copy link kênh: {link}")

# --- HÀM COPY PATH ---
def copy_folder_path():
    sel = tree.selection()
    if not sel: return
    
    name = tree.item(sel[0])['values'][0]
    if name in profiles:
        path = profiles[name]['config'].get('folder_path', '')
        if path:
            root.clipboard_clear()
            root.clipboard_append(path)
            root.update() # Bắt buộc để lưu clipboard
            update_status(f"[{name}] Đã copy đường dẫn folder video.")
        else:
            messagebox.showwarning("Warning", "Không tìm thấy đường dẫn folder.")
# ------------------------------

# =========================
# System Exit
# =========================
_shutting_down = False

APP_SHUTDOWN_DEADLINE = 15


def _shutdown_all_profiles():
    for name in list(profiles.keys()):
        lc = get_lifecycle(name)
        lc.cancel()
        profiles[name]['running'] = False
        running_profiles.discard(name)

    def _stop_one(name):
        try:
            _stop_profile_driver(name)
        except Exception as e:
            update_status(f"[{name}] Lỗi dừng khi tắt app: {e}")

    threads = []
    for name in list(profiles.keys()):
        t = threading.Thread(target=_stop_one, args=(name,), daemon=True)
        t.start()
        threads.append(t)
    deadline = time.time() + APP_SHUTDOWN_DEADLINE
    for t in threads:
        remaining = max(0.1, deadline - time.time())
        t.join(timeout=remaining)
    after_kill_cleanup_running_profiles()
    try:
        browser_glue.shutdown_browser_service(timeout=5.0)
    except Exception as error:
        update_status(f"[Browser] Lỗi shutdown Patchright service: {error}")


def on_closing():
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True

    try:
        youtube_monitor.stop_monitor()
    except Exception as e:
        update_status(f"[YouTube] Lỗi dừng monitor khi đóng app: {e}")

    _shutdown_all_profiles()
    try:
        from log_engine import get_log_engine
        get_log_engine().shutdown()
    except Exception:
        pass
    try:
        from config_service import get_config_service
        get_config_service().shutdown(timeout=5.0)
    except Exception:
        pass
    try:
        from watchdog_service import get_watchdog_manager
        get_watchdog_manager().stop()
    except Exception:
        pass
    root.after(500, root.destroy)

def change_license_key():
    _license_dialog(on_success=lambda: _set_ui_enabled(True), is_first_run=False)


def _run_auto6_watcher_test_from_env():
    if os.environ.get('AUTO6_WATCHER_TEST') != '1' or os.environ.get('UPLOAD_TEST_MODE') == '1':
        return

    test_state = {'start_count': None, 'started_at': time.time(), 'cloned': False}

    def _finish(success, reason):
        try:
            update_status(f"[AUTO 6] AUTO TEST {'PASS' if success else 'FAIL'}: {reason}")
            print(f"AUTO6_WATCHER_TEST_RESULT={'PASS' if success else 'FAIL'}: {reason}", flush=True)
        except Exception:
            pass
        try:
            stop_profile('AUTO 6')
        except Exception:
            pass
        root.after(2500, root.destroy)

    def _watch_result():
        if 'AUTO 6' not in profiles:
            root.after(1000, _watch_result)
            return
        prof = profiles['AUTO 6']
        start_count = test_state.get('start_count')
        if start_count is not None and prof.get('uploads_today_count', 0) > start_count:
            _finish(True, f"uploads_today_count {start_count} -> {prof.get('uploads_today_count', 0)}")
            return
        ui = prof.get('ui', {})
        upload_state = str(ui.get('upload', ''))
        last_error = str(ui.get('last_error', ''))
        if 'Đăng lỗi' in upload_state or last_error:
            _finish(False, last_error or upload_state)
            return
        if time.time() - test_state['started_at'] > int(os.environ.get('AUTO6_TEST_TIMEOUT', '420')):
            _finish(False, 'timeout chờ AUTO 6 upload xong')
            return
        root.after(1000, _watch_result)

    def _start():
        if 'AUTO 6' not in profiles:
            root.after(1000, _start)
            return
        test_state['start_count'] = profiles['AUTO 6'].get('uploads_today_count', 0)
        profiles['AUTO 6']['config']['open_only_when_video'] = True
        _set_profile_ui('AUTO 6', last_error='')
        start_profile('AUTO 6')
        root.after(5000, _clone)
        root.after(6000, _watch_result)

    def _clone():
        try:
            folder = profiles['AUTO 6']['config']['folder_path']
            src = os.path.join(folder, 'THIỆP MỜI ONLINE - PHƯƠNG THẢO & THÀNH CÔNG - YouTube.mp4')
            dst = os.path.join(folder, f"AUTO6_WATCHER_TEST_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4")
            shutil.copy2(src, dst)
            os.utime(dst, None)
            update_status(f"[AUTO 6] Đã clone video test: {Path(dst).name}")
        except Exception as e:
            update_status(f"[AUTO 6] Lỗi clone video test: {e}")

    root.after(3000, _start)


def _run_live_verify_from_env():
    """Env-gated live verification for Check Cookie + Inspection (AUTO 6)."""
    if os.environ.get('LIVE_VERIFY') != '1':
        return
    profile_name = os.environ.get('LIVE_VERIFY_PROFILE', 'AUTO 6').strip()

    def _finish(payload):
        try:
            print(f"LIVE_VERIFY_RESULT={json.dumps(payload, ensure_ascii=True)}", flush=True)
        except Exception:
            pass
        root.after(2000, root.destroy)

    def _run():
        try:
            cookie_result = _check_profile_cookie_live(profile_name)
            inspect_result = _inspect_tiktok_account_worker(profile_name)
            payload = {
                'cookie': {
                    'state': cookie_result.state.value,
                    'source': cookie_result.source.value,
                    'kept_cookies': cookie_result.kept_cookies,
                    'auth_cookie_names': list(cookie_result.auth_cookie_names),
                    'checked_at': cookie_result.checked_at,
                    'detail': cookie_result.detail,
                },
                'inspect': {
                    'state': inspect_result.state.value,
                    'classification': inspect_result.classification,
                    'checked_at': inspect_result.checked_at,
                    'detail': inspect_result.detail,
                    'identity': {
                        'numeric_user_id': inspect_result.identity.numeric_user_id,
                        'unique_id': inspect_result.identity.unique_id,
                        'nickname': inspect_result.identity.nickname,
                        'region': inspect_result.identity.region,
                    },
                    'analytics': {
                        'total_views': inspect_result.analytics.total_views,
                        'views_30d': inspect_result.analytics.views_30d,
                    },
                    'monetization_balance': inspect_result.monetization.balance_amount,
                    'sources_count': len(inspect_result.sources),
                    'warnings_count': len(inspect_result.warnings),
                    'capabilities': [
                        {
                            'name': capability.capability,
                            'state': capability.state.value,
                            'endpoint_id': capability.endpoint_id,
                            'schema_hash': capability.schema_hash,
                            'warnings': list(capability.warnings),
                        }
                        for capability in inspect_result.capabilities.results
                    ],
                },
                'operation': {
                    'idle': profiles.get(profile_name, {}).get('operation') == OperationState.IDLE.value,
                    'session_busy': bool(profiles.get(profile_name, {}).get('session_busy')),
                },
            }
            _finish(payload)
        except Exception as error:
            _finish({'error': f'{type(error).__name__}: {error}'})

    def _start():
        if profile_name not in profiles:
            root.after(500, _start)
            return
        threading.Thread(target=_run, daemon=True).start()

    root.after(2000, _start)


def _run_single_upload_test_from_env():
    if os.environ.get('UPLOAD_TEST_MODE') != '1':
        return
    profile_name = os.environ.get('UPLOAD_TEST_PROFILE', '').strip()
    source_path = Path(os.environ.get('UPLOAD_TEST_SOURCE', '').strip())
    round_number = int(os.environ.get('UPLOAD_TEST_ROUND', '1') or 1)
    timeout = int(os.environ.get('UPLOAD_TEST_TIMEOUT', '600') or 600)
    verify_only = os.environ.get('UPLOAD_TEST_VERIFY_ONLY') == '1'
    open_only_mode = os.environ.get('UPLOAD_TEST_OPEN_ONLY') == '1'
    keep_source_name = os.environ.get('UPLOAD_TEST_KEEP_NAME') == '1'
    state = {
        'started_at': time.time(),
        'start_count': None,
        'target': None,
        'copied': False,
        'finished': False,
        'original_open_only': None,
    }

    def finish(success, reason):
        if state['finished']:
            return
        state['finished'] = True
        result = {
            'success': bool(success),
            'profile': profile_name,
            'round': round_number,
            'reason': str(reason),
            'target': str(state.get('target') or ''),
        }
        update_status(f"[{profile_name}] UPLOAD TEST {'PASS' if success else 'FAIL'} R{round_number:02d}: {reason}")
        print(f"UPLOAD_TEST_RESULT={json.dumps(result, ensure_ascii=True)}", flush=True)
        active_upload = bool(profiles.get(profile_name, {}).get('uploading'))
        if active_upload:
            update_status(f"[{profile_name}] [WARN] Test đã timeout nhưng upload còn chạy; giữ browser mở để tránh trạng thái Post không xác định.")
            return
        profile = profiles.get(profile_name)
        if profile and state.get('original_open_only') is not None:
            profile['config']['open_only_when_video'] = state['original_open_only']
            try:
                save_configs()
            except Exception as error:
                update_status(f"[{profile_name}] [WARN] Không khôi phục được chế độ mở browser: {error}")
        if (
            success
            and os.environ.get('UPLOAD_TEST_STOP_BEFORE_POST') == '1'
            and state.get('copied')
            and state.get('target')
        ):
            try:
                target_path = Path(state['target'])
                if target_path.name.startswith('UPLOAD_TEST_') and target_path.is_file():
                    target_path.unlink()
                    update_status(f"[{profile_name}] Đã xóa bản clone dry-run: {target_path.name}")
            except Exception as error:
                update_status(f"[{profile_name}] [WARN] Không xóa được bản clone dry-run: {error}")
        if not success and state.get('target') and Path(state['target']).is_file():
            try:
                quarantine = app_base_dir() / 'temp_dl' / 'upload_test_failed'
                quarantine.mkdir(parents=True, exist_ok=True)
                failed_target = quarantine / Path(state['target']).name
                os.replace(state['target'], failed_target)
                update_status(f"[{profile_name}] Đã cách ly video test lỗi: {failed_target}")
            except Exception as error:
                update_status(f"[{profile_name}] [WARN] Không cách ly được video test lỗi: {error}")
        try:
            stop_profile(profile_name)
        except Exception:
            pass
        root.after(2500, root.destroy)

    def watch_result():
        if state['finished']:
            return
        if time.time() - state['started_at'] > timeout:
            finish(False, f'timeout sau {timeout}s')
            return
        profile = profiles.get(profile_name)
        if not profile:
            root.after(250, watch_result)
            return
        start_count = state.get('start_count')
        target = state.get('target')
        terminal = None
        if target:
            with upload_benchmark_lock:
                terminal = UPLOAD_TERMINAL_RESULTS.get(_upload_timing_key(target))
        if terminal and terminal.get('success') and start_count is not None and profile.get('uploads_today_count', 0) > start_count:
            finish(True, f"uploads_today_count {start_count} -> {profile.get('uploads_today_count', 0)}")
            return
        if terminal and terminal.get('success') and terminal.get('meta', {}).get('outcome') == 'prepared':
            finish(True, 'prepared: editor sẵn sàng, popup đã dọn, chưa bấm Post')
            return
        if terminal and not terminal.get('success') and not profile.get('uploading'):
            finish(False, terminal.get('reason') or 'upload_failed')
            return
        root.after(250, watch_result)

    def clone_one_video():
        if state['finished'] or state['copied']:
            return
        try:
            folder = Path(profiles[profile_name]['config']['folder_path'])
            suffix = source_path.suffix.lower() if source_path.suffix.lower() in VIDEO_EXTENSIONS else '.mp4'
            if keep_source_name:
                target = folder / source_path.name
            else:
                target = folder / f"UPLOAD_TEST_R{round_number:02d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
            if target.exists():
                raise FileExistsError(f'Video đích đã tồn tại, không ghi đè: {target}')
            state['target'] = target
            _mark_upload_timing(target, 'copy_started')
            copy_video_atomically(source_path, target)
            _mark_upload_timing(target, 'copy_finished')
            state['copied'] = True
            update_status(f"[{profile_name}] Đã tạo video test vòng {round_number}: {target.name}")
        except Exception as error:
            finish(False, f'lỗi copy video test: {error}')

    def capture_content_page():
        try:
            token = profiles[profile_name]['driver']
            login_state = browser_glue.page_login_state(token, timeout=15)
            if login_state == 'login_required':
                finish(False, 'login_required: TikTok hiển thị trang đăng nhập')
                return
            artifacts = app_base_dir() / 'temp_dl' / 'upload_test_verify'
            artifacts.mkdir(parents=True, exist_ok=True)
            screenshot_path = artifacts / f'round_{round_number:02d}.png'
            text_path = artifacts / f'round_{round_number:02d}.txt'
            async def _capture(page):
                body = await page.evaluate("document.body ? document.body.innerText : ''")
                await page.screenshot(path=str(screenshot_path), full_page=True)
                return body
            body_text = browser_glue.run_operation(token, _capture) or ''
            text_path.write_text(body_text, encoding='utf-8')
            print(f"UPLOAD_VERIFY_ARTIFACT={json.dumps({'text': str(text_path), 'screenshot': str(screenshot_path)}, ensure_ascii=True)}", flush=True)
            finish(True, 'đã chụp trang quản lý nội dung')
        except Exception as error:
            finish(False, f'lỗi chụp trang quản lý nội dung: {error}')

    def open_content_page():
        try:
            token = profiles[profile_name]['driver']
            browser_glue.navigate(token, 'https://www.tiktok.com/tiktokstudio/content')
            root.after(8000, capture_content_page)
        except Exception as error:
            finish(False, f'lỗi mở trang quản lý nội dung: {error}')

    def wait_browser_ready():
        if state['finished']:
            return
        if time.time() - state['started_at'] > timeout:
            finish(False, f'timeout khởi động sau {timeout}s')
            return
        profile = profiles.get(profile_name)
        driver = profile.get('driver') if profile else None
        if open_only_mode and profile and profile.get('running') and not state['copied']:
            observer = profile.get('observer')
            if observer and observer.is_alive():
                state['start_count'] = profile.get('uploads_today_count', 0)
                clone_one_video()
                return
        if profile and profile.get('running') and _browser_session_valid(driver):
            if verify_only:
                open_content_page()
                return
            state['start_count'] = profile.get('uploads_today_count', 0)
            clone_one_video()
            return
        if profile and str(profile.get('ui', {}).get('last_error', '')):
            finish(False, profile.get('ui', {}).get('last_error'))
            return
        root.after(250, wait_browser_ready)

    def start_test():
        if not profile_name:
            finish(False, 'thiếu UPLOAD_TEST_PROFILE')
            return
        if not verify_only and not source_path.is_file():
            finish(False, f'không tìm thấy UPLOAD_TEST_SOURCE: {source_path}')
            return
        if profile_name not in profiles:
            root.after(500, start_test)
            return
        config = profiles[profile_name]['config']
        state['original_open_only'] = bool(config.get('open_only_when_video', False))
        config['open_only_when_video'] = open_only_mode
        _set_profile_ui(profile_name, last_error='')
        start_profile(profile_name)
        root.after(250, wait_browser_ready)
        root.after(250, watch_result)

    root.after(1500, start_test)

# =========================
# UI Setup
# =========================
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")
root = ctk.CTk()
root.title("DONGLAO-TIKTOK — Automation & Studio Suite")
root.geometry("1380x920")
root.minsize(1180, 760)
root.configure(fg_color="#f3f4f6")
apply_app_icon(root)

selected_project_var = StringVar(master=root)
filter_var = StringVar(master=root, value="")
theme_var = StringVar(master=root, value="System")
scale_var = StringVar(master=root, value="100%")
header_total_label = StringVar(master=root, value="0")
header_running_label = StringVar(master=root, value="0")
header_project_label = StringVar(master=root, value=ALL_OPTION)
summary_cookie_var = StringVar(master=root, value="0")
summary_error_var = StringVar(master=root, value="0")
mono_total_balance_var = StringVar(master=root, value="$0.00")
mono_crp_count_var = StringVar(master=root, value="0 Acc")
mono_kyc_count_var = StringVar(master=root, value="0 Acc")
mono_tax_count_var = StringVar(master=root, value="0 Acc")
mono_tktbm_count_var = StringVar(master=root, value="0 Acc")
mono_ready_count_var = StringVar(master=root, value="0")
mono_action_needed_var = StringVar(master=root, value="0")
mono_status_var = StringVar(master=root, value="")
active_filter_chip_var = StringVar(master=root, value="ALL")
mono_active_filter_chip_var = StringVar(master=root, value="ALL")
current_page_var = ctk.IntVar(master=root, value=1)
page_size_var = ctk.StringVar(master=root, value="10 / trang")
total_pages_var = ctk.IntVar(master=root, value=1)
total_records_var = ctk.IntVar(master=root, value=0)

from ui_components import ToastManager
toast_manager = ToastManager(root)

from tiktok_monetization_client import fetch_monetization_snapshot, apply_creative_rewards_for_profile
from ui_dialogs import MonetizationDetailModal
from concurrent.futures import ThreadPoolExecutor

MONETIZATION_CACHE_FILE = app_base_dir() / "monetization_cache.json"
monetization_cache = {}

def _load_monetization_cache():
    global monetization_cache
    if MONETIZATION_CACHE_FILE.exists():
        try:
            with open(MONETIZATION_CACHE_FILE, "r", encoding="utf-8") as f:
                monetization_cache = json.load(f)
        except Exception:
            monetization_cache = {}
    else:
        monetization_cache = {}

def _save_monetization_cache():
    try:
        with open(MONETIZATION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(monetization_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

_load_monetization_cache()

configure_ttk_styles()

ui_state = {
    'selected_project_var': selected_project_var,
    'filter_var': filter_var,
    'scale_var': scale_var,
    'header_total_label': header_total_label,
    'header_running_label': header_running_label,
    'header_project_label': header_project_label,
    'summary_cookie_var': summary_cookie_var,
    'summary_error_var': summary_error_var,
    'mono_total_balance_var': mono_total_balance_var,
    'mono_crp_count_var': mono_crp_count_var,
    'mono_kyc_count_var': mono_kyc_count_var,
    'mono_tax_count_var': mono_tax_count_var,
    'mono_tktbm_count_var': mono_tktbm_count_var,
    'mono_ready_count_var': mono_ready_count_var,
    'mono_action_needed_var': mono_action_needed_var,
    'mono_status_var': mono_status_var,
    'active_filter_chip': active_filter_chip_var,
    'mono_active_filter_chip': mono_active_filter_chip_var,
}

def _update_monetization_table(*_args):
    mono_tree = ui_widgets.get('monetization_tree') if 'ui_widgets' in globals() else None
    if not mono_tree:
        return

    current_proj = selected_project_var.get()
    kw = filter_var.get().strip().lower()
    mono_chip = mono_active_filter_chip_var.get()

    total_balance = 0.0
    ready_count = 0
    crp_count = 0
    kyc_count = 0
    tax_count = 0
    tktbm_count = 0

    for item in mono_tree.get_children():
        mono_tree.delete(item)

    for name, prof in sorted(profiles.items()):
        config = prof.get('config', {})
        snap = monetization_cache.get(name, {})
        bal_val = float(snap.get('balance', 0.0) or 0.0)
        p_status = snap.get('payout_status', 'CHƯA CHECK')
        k_status = snap.get('kyc_status', 'N/A')
        tax_st = snap.get('tax_status', 'N/A')
        p_method = snap.get('payment_method', 'N/A')
        chk_at = snap.get('checked_at', 'Chưa kiểm tra')
        reg = snap.get('region', config.get('region', 'US'))
        crp_display = snap.get('crp_display', 'Chưa check')
        crp_status = snap.get('crp_status', '')

        # Global stats calculation
        total_balance += bal_val
        if p_status == 'PAYOUT_READY':
            ready_count += 1
        if crp_status in ('ACTIVE', 'ELIGIBLE'):
            crp_count += 1
        if k_status == 'APPROVED':
            kyc_count += 1
        if tax_st in ('TAX_VERIFIED', 'APPROVED'):
            tax_count += 1
        if crp_status == 'TKTBM':
            tktbm_count += 1

        # Project and keyword filters
        if current_proj != ALL_OPTION and config.get('project_name') != current_proj:
            continue
        uid_val = str(snap.get('tiktok_user_id') or snap.get('unique_id') or config.get('tiktok_account') or '')
        if kw and kw not in name.lower() and kw not in uid_val.lower() and kw not in p_method.lower():
            continue

        # Filter Chip match
        if mono_chip == "PAYOUT_READY":
            if p_status != 'PAYOUT_READY':
                continue
        elif mono_chip == "CRP_ACTIVE":
            if crp_status not in ('ACTIVE', 'ELIGIBLE'):
                continue
        elif mono_chip == "TAX_OK":
            if tax_st not in ('TAX_VERIFIED', 'APPROVED'):
                continue
        elif mono_chip == "KYC_OK":
            if k_status != 'APPROVED':
                continue
        elif mono_chip == "TKTBM":
            if crp_status != 'TKTBM':
                continue

        if p_status == 'PAYOUT_READY':
            p_display = "🟢 SẴN SÀNG"
        elif p_status == 'PAYOUT_PENDING':
            p_display = "🟡 ĐANG XÁC MINH"
        elif p_status == 'Cookie Die':
            p_display = "🔴 COOKIE DIE"
        elif p_status in ('Chưa có Cookie', 'NO_AUTH'):
            p_display = "⚪ CHƯA CÓ COOKIE"
        elif p_status == 'Lỗi Proxy':
            p_display = "🟡 LỖI PROXY"
        elif p_status == 'PAYOUT_NOT_LINKED':
            p_display = "🔵 CHƯA LIÊN KẾT"
        else:
            p_display = str(p_status)

        # Tax Status formatting
        if tax_st in ('TAX_VERIFIED', 'APPROVED'):
            tax_display = "🟢 ĐÃ KHAI THUẾ"
        elif tax_st in ('TAX_PENDING', 'PENDING'):
            tax_display = "🟡 ĐANG DUYỆT"
        elif tax_st == 'Cookie Die':
            tax_display = "🔴 COOKIE DIE"
        elif tax_st in ('Chưa có Cookie', 'NO_AUTH'):
            tax_display = "⚪ CHƯA CÓ COOKIE"
        elif tax_st == 'Lỗi Proxy':
            tax_display = "🟡 LỖI PROXY"
        elif tax_st == 'NOT_STARTED':
            tax_display = "⚪ CHƯA KHAI"
        else:
            tax_display = str(tax_st)

        # KYC Status formatting
        if k_status == 'APPROVED':
            k_display = "🟢 ĐÃ KYC"
        elif k_status == 'PENDING':
            k_display = "🟡 ĐANG CHỜ"
        elif k_status in ('RESUBMIT', 'WARNING'):
            k_display = "🔴 NỘP LẠI"
        elif k_status == 'REJECTED':
            k_display = "🔴 BỊ TỪ CHỐI"
        elif k_status == 'Cookie Die':
            k_display = "🔴 COOKIE DIE"
        elif k_status in ('Chưa có Cookie', 'NO_AUTH'):
            k_display = "⚪ CHƯA CÓ COOKIE"
        elif k_status == 'NOT_STARTED':
            k_display = "⚪ CHƯA KYC"
        else:
            k_display = str(k_status)

        # TikTok ID / UID & Region formatting
        uid_display = str(snap.get('tiktok_user_id') or snap.get('unique_id') or config.get('tiktok_account') or 'N/A')
        reg_display = str(snap.get('region') or snap.get('store_region') or config.get('region', 'US')).upper()

        mono_tree.insert(
            '',
            'end',
            iid=name,
            values=(
                name,
                uid_display,
                reg_display,
                crp_display,
                f"${bal_val:,.2f}",
                p_display,
                tax_display,
                k_display,
                p_method,
                chk_at,
            ),
        )

    mono_total_balance_var.set(f"${total_balance:,.2f}")
    mono_ready_count_var.set(f"{ready_count} Acc")
    mono_crp_count_var.set(f"{crp_count} Acc")
    mono_kyc_count_var.set(f"{kyc_count} Acc")
    mono_tax_count_var.set(f"{tax_count} Acc")
    mono_tktbm_count_var.set(f"{tktbm_count} Acc")


def _do_fetch_monetization_worker(targets):
    total = len(targets)
    toast_manager.enqueue(f"Bắt đầu quét thu nhập {total} tài khoản...", level="info")
    update_status(f"[Thu Nhập] Bắt đầu quét {total} tài khoản...")
    mono_status_var.set(f"Đang chuẩn bị quét {total} tài khoản...")
    success_count = 0
    processed_count = 0

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(fetch_monetization_snapshot, name, profiles[name].get('config', {})): name
            for name in targets if name in profiles
        }
        for fut in futures:
            name = futures[fut]
            processed_count += 1
            try:
                data = fut.result(timeout=20.0)
                monetization_cache[name] = data
                st = data.get("status")
                if st == "SUCCESS":
                    success_count += 1
                    status_desc = f"OK (${data.get('balance', 0.0):.2f})"
                elif st == "COOKIE_EXPIRED":
                    _set_profile_ui(name, last_error="Cookie die - Hết hạn phiên đăng nhập", login="Die", refresh=False)
                    _mark_session_failure(name, "Cookie die - Hết hạn phiên đăng nhập")
                    status_desc = "🔴 Cookie Die"
                elif st == "NO_AUTH":
                    _set_profile_ui(name, last_error="Chưa có cookie (Cần đăng nhập)", login="Chưa có Cookie", refresh=False)
                    status_desc = "⚪ Chưa có Cookie"
                elif st == "PROXY_ERROR":
                    _set_profile_ui(name, last_error="Lỗi kết nối Proxy", proxy="Lỗi Proxy", refresh=False)
                    status_desc = "🟡 Lỗi Proxy"
                else:
                    status_desc = str(st)
            except Exception as e:
                monetization_cache[name] = {
                    "balance": 0.0,
                    "payout_status": "ERROR",
                    "tax_status": "ERROR",
                    "kyc_status": "ERROR",
                    "payment_method": "N/A",
                    "checked_at": "Lỗi",
                    "errors": [str(e)],
                }
                _set_profile_ui(name, last_error=f"Lỗi kiểm tra: {e}", refresh=False)
                status_desc = f"Lỗi ({e})"

            # Realtime progress notification
            msg_progress = f"[{processed_count}/{total}] Đang quét: {name} ({status_desc})"
            update_status(f"[Thu Nhập] {msg_progress}")
            mono_status_var.set(msg_progress)
            _save_monetization_cache()
            root.after(0, _update_monetization_table)
            root.after(0, update_profile_list)

    _save_monetization_cache()
    fin_time = time.strftime("%H:%M:%S")
    mono_status_var.set(f"Đã cập nhật lúc {fin_time} ({success_count}/{total} OK)")
    update_status(f"[Thu Nhập] Hoàn tất quét {total} tài khoản: {success_count}/{total} thành công.")
    toast_manager.enqueue(
        f"Hoàn tất quét thu nhập: {success_count}/{total} OK",
        level="success" if success_count > 0 else "warning"
    )


def refresh_all_monetization():
    current_proj = selected_project_var.get()
    targets = [
        name for name, prof in profiles.items()
        if current_proj == ALL_OPTION or prof.get('config', {}).get('project_name') == current_proj
    ]
    if not targets:
        toast_manager.enqueue("Không có profile nào để quét thu nhập.", level="warning")
        return
    threading.Thread(target=_do_fetch_monetization_worker, args=(targets,), daemon=True).start()


def refresh_selected_monetization():
    mono_tree = ui_widgets.get('monetization_tree') if 'ui_widgets' in globals() else None
    selected = list(mono_tree.selection()) if mono_tree else []
    if not selected and 'tree' in globals():
        selected = list(tree.selection())
    if not selected:
        toast_manager.enqueue("Vui lòng chọn ít nhất 1 profile để quét thu nhập.", level="warning")
        return
    threading.Thread(target=_do_fetch_monetization_worker, args=(selected,), daemon=True).start()


def apply_crp_selected():
    mono_tree = ui_widgets.get('monetization_tree') if 'ui_widgets' in globals() else None
    selected = list(mono_tree.selection()) if mono_tree else []
    if not selected and 'tree' in globals():
        selected = list(tree.selection())
    if not selected:
        toast_manager.enqueue("Vui lòng chọn ít nhất 1 profile để gửi duyệt CRP.", level="warning")
        return

    def _run():
        toast_manager.enqueue(f"Bắt đầu gửi đơn duyệt kiếm tiền cho {len(selected)} tài khoản...", level="info")
        success_c = 0
        for name in selected:
            prof = profiles.get(name, {})
            cfg = prof.get('config', {})
            res = apply_creative_rewards_for_profile(name, cfg)
            if res.get("success"):
                success_c += 1
                update_status(f"[{name}] Gửi đơn duyệt CRP thành công!")
                toast_manager.enqueue(f"[{name}] {res.get('message')}", level="success")
            else:
                update_status(f"[{name}] Gửi đơn duyệt CRP thất bại: {res.get('message')}")
                toast_manager.enqueue(f"[{name}] {res.get('message')}", level="error")
        update_status(f"Hoàn tất gửi đơn duyệt CRP: {success_c}/{len(selected)} thành công.")

    threading.Thread(target=_run, daemon=True).start()


def view_monetization_details():
    mono_tree = ui_widgets.get('monetization_tree') if 'ui_widgets' in globals() else None
    selected = list(mono_tree.selection()) if mono_tree else []
    if not selected and 'tree' in globals():
        selected = list(tree.selection())
    if not selected:
        toast_manager.enqueue("Vui lòng chọn 1 profile để xem chi tiết.", level="warning")
        return
    prof_name = selected[0]
    snap = monetization_cache.get(prof_name, {})
    if not snap:
        config = profiles.get(prof_name, {}).get('config', {})
        snap = {
            "profile_name": prof_name,
            "region": config.get("region", "US"),
            "balance": 0.0,
            "payout_status": "CHƯA KIỂM TRA",
            "tax_status": "CHƯA KIỂM TRA",
            "payment_method": "N/A",
            "kyc_status": "N/A",
            "crp_display": "Chưa check",
            "rewards_estimated": "$0.00",
            "checked_at": "Chưa kiểm tra",
        }
    MonetizationDetailModal(root, prof_name, snap)


def _youtube_profile_names():
    return sorted(profiles.keys())

def _youtube_profile_folder(profile_name):
    prof = profiles.get(profile_name)
    if not prof:
        raise ValueError(f"Không tìm thấy profile TikTok: {profile_name}")
    folder = prof.get('config', {}).get('folder_path', '')
    if not folder:
        raise ValueError(f"Profile {profile_name} chưa có folder video")
    return folder

def _youtube_add_channel(channel_input, profile_name):
    return youtube_monitor.add_channel_for_profile(channel_input, profile_name, _youtube_profile_folder(profile_name))

def _youtube_set_profile(channel_id, profile_name):
    return youtube_monitor.set_channel_profile(channel_id, profile_name, _youtube_profile_folder(profile_name))

def _youtube_download_test(video_input, profile_name):
    return youtube_monitor.download_test_video(video_input, profile_name, _youtube_profile_folder(profile_name))

def _youtube_get_profile_folder(profile_name):
    return True, _youtube_profile_folder(profile_name)

def _youtube_batch_download_latest(links, folder, profile_name, progress_callback=None, stop_event=None):
    return youtube_monitor.batch_download_latest(
        links,
        folder,
        profile_name=profile_name,
        progress_callback=progress_callback,
        stop_event=stop_event,
    )

def _youtube_get_max_video_minutes():
    return int(youtube_monitor.get_config().get('max_video_minutes', 0) or 0)

def _youtube_start_monitor():
    return youtube_monitor.start_monitor()

def _youtube_stop_monitor():
    return youtube_monitor.stop_monitor()

youtube_monitor_handlers = {
    'get_profiles': _youtube_profile_names,
    'get_status': youtube_monitor.get_status,
    'get_channels': youtube_monitor.get_channels,
    'get_cookies_file': youtube_monitor.get_cookies_file,
    'get_max_video_minutes': _youtube_get_max_video_minutes,
    'get_profile_folder': _youtube_get_profile_folder,
    'get_logs': youtube_monitor.get_logs,
    'save_api_key': youtube_monitor.check_and_save_api_key,
    'set_cookies_file': youtube_monitor.set_cookies_file,
    'set_max_video_minutes': youtube_monitor.set_max_video_minutes,
    'batch_download_latest': _youtube_batch_download_latest,
    'start': _youtube_start_monitor,
    'stop': _youtube_stop_monitor,
    'add_channel': _youtube_add_channel,
    'set_profile': _youtube_set_profile,
    'download_test': _youtube_download_test,
    'toggle_active': youtube_monitor.toggle_channel_active,
    'toggle_short': youtube_monitor.toggle_channel_short,
    'remove_channel': youtube_monitor.remove_channel,
}

def open_profile_folder(selected_name=None):
    if selected_name is None:
        sel = tree.selection() if 'tree' in globals() else ()
        if not sel: return
        selected_name = tree.item(sel[0])['values'][0] if sel[0] in tree.get_children('') else sel[0]
    cfg = profiles.get(selected_name, {}).get('config', {})
    p_path = cfg.get('browser_profile_path') or cfg.get('chrome_profile')
    if p_path and os.path.exists(p_path):
        _open_dir(p_path)
    else:
        messagebox.showinfo("Thư mục Profile", f"Thư mục Profile chưa tồn tại:\n{p_path}")

def open_video_folder(selected_name=None):
    if selected_name is None:
        sel = tree.selection() if 'tree' in globals() else ()
        if not sel: return
        selected_name = tree.item(sel[0])['values'][0] if sel[0] in tree.get_children('') else sel[0]
    cfg = profiles.get(selected_name, {}).get('config', {})
    f_path = cfg.get('folder_path')
    if f_path and os.path.exists(f_path):
        _open_dir(f_path)
    else:
        messagebox.showinfo("Thư mục Video", f"Thư mục Video chưa tồn tại:\n{f_path}")

def copy_tiktok_uid(selected_name=None):
    if selected_name is None:
        sel = tree.selection() if 'tree' in globals() else ()
        mono_sel = ui_widgets.get('monetization_tree').selection() if 'ui_widgets' in globals() and ui_widgets.get('monetization_tree') else ()
        if sel:
            selected_name = tree.item(sel[0])['values'][0] if sel[0] in tree.get_children('') else sel[0]
        elif mono_sel:
            selected_name = mono_sel[0]
        else:
            return
    cfg = profiles.get(selected_name, {}).get('config', {})
    snap = monetization_cache.get(selected_name, {})
    uid = snap.get('tiktok_user_id') or snap.get('unique_id') or cfg.get('tiktok_id') or cfg.get('tiktok_account') or ''
    if uid:
        root.clipboard_clear()
        root.clipboard_append(str(uid))
        toast_manager.enqueue(f"Đã copy TikTok UID: {uid}", level="info")
    else:
        toast_manager.enqueue("Chưa có thông tin TikTok UID", level="warning")

def copy_proxy_string(selected_name=None):
    if selected_name is None:
        sel = tree.selection() if 'tree' in globals() else ()
        if not sel: return
        selected_name = tree.item(sel[0])['values'][0] if sel[0] in tree.get_children('') else sel[0]
    cfg = profiles.get(selected_name, {}).get('config', {})
    proxy = cfg.get('proxy_string', '')
    if proxy:
        root.clipboard_clear()
        root.clipboard_append(proxy)
        toast_manager.enqueue("Đã copy chuỗi Proxy vào Clipboard", level="info")
    else:
        toast_manager.enqueue("Profile này không sử dụng Proxy", level="warning")

def copy_cookie_string(selected_name=None):
    if selected_name is None:
        sel = tree.selection() if 'tree' in globals() else ()
        if not sel: return
        selected_name = tree.item(sel[0])['values'][0] if sel[0] in tree.get_children('') else sel[0]
    cfg = profiles.get(selected_name, {}).get('config', {})
    cookie = cfg.get('cookie_str', '')
    if cookie:
        root.clipboard_clear()
        root.clipboard_append(cookie)
        toast_manager.enqueue("Đã copy chuỗi Cookie vào Clipboard", level="info")
    else:
        toast_manager.enqueue("Chưa có chuỗi Cookie lưu sẵn", level="warning")

def copy_payout_method(selected_name=None):
    mono_tree = ui_widgets.get('monetization_tree') if 'ui_widgets' in globals() else None
    if not mono_tree: return
    sel = mono_tree.selection()
    if not sel: return
    name = sel[0]
    snap = monetization_cache.get(name, {})
    pm = snap.get('payment_method', '')
    if pm:
        root.clipboard_clear()
        root.clipboard_append(pm)
        toast_manager.enqueue(f"Đã copy PTTT: {pm}", level="info")
    else:
        toast_manager.enqueue("Chưa có thông tin PTTT", level="warning")

def apply_filter_chip(chip_key):
    active_filter_chip_var.set(chip_key)
    current_page_var.set(1)
    update_profile_list()

def apply_mono_filter_chip(chip_key):
    mono_active_filter_chip_var.set(chip_key)
    _update_monetization_table()

def go_first_page():
    if current_page_var.get() != 1:
        current_page_var.set(1)
        update_profile_list()

def go_prev_page():
    cur = current_page_var.get()
    if cur > 1:
        current_page_var.set(cur - 1)
        update_profile_list()

def go_next_page():
    cur = current_page_var.get()
    tot = total_pages_var.get()
    if cur < tot:
        current_page_var.set(cur + 1)
        update_profile_list()

def go_last_page():
    tot = total_pages_var.get()
    if current_page_var.get() != tot:
        current_page_var.set(tot)
        update_profile_list()

def change_page_size(val):
    page_size_var.set(val)
    current_page_var.set(1)
    update_profile_list()

activity_handlers = {
    'get_logs': get_activity_logs,
    'get_stats': get_activity_stats,
    'clear': clear_activity_log,
    'get_mtime': get_activity_mtime,
    'get_profiles': _youtube_profile_names,
}

ui_handlers = {
    'create_project': create_project,
    'delete_project': delete_project,
    'add_profile': add_profile,
    'batch_add_profiles': batch_add_profiles,
    'edit_profile': edit_profile,
    'view_profile_details': view_profile_details,
    'export_profiles': export_profiles,
    'delete_profile': delete_profile,
    'rename_profile': rename_profile,
    'assign_to_project': assign_to_project,
    'show_statistics_board': show_statistics_board,
    'open_browser': open_browser,
    'get_tiktok_cookies': get_tiktok_cookies,
    'check_cookie_live': check_cookie_live,
    'inspect_tiktok_account': inspect_selected_tiktok_account,
    'refresh_all_monetization': refresh_all_monetization,
    'refresh_selected_monetization': refresh_selected_monetization,
    'view_monetization_details': view_monetization_details,
    'apply_crp_selected': apply_crp_selected,
    'clean_browser': clean_browser,
    'change_license_key': change_license_key,
    'check_update': check_update_clicked,
    'clear_failed_uploads_panel': clear_failed_uploads_panel,
    'cleanup_failed_videos': cleanup_failed_videos,
    'start_selected_batch': start_selected_batch,
    'stop_selected_batch': stop_selected_batch,
    'start_all_in_project': start_all_in_project,
    'stop_all_in_project': stop_all_in_project,
    'copy_folder_path': copy_folder_path,
    'copy_channel_link': copy_channel_link,
    'open_profile_folder': open_profile_folder,
    'open_video_folder': open_video_folder,
    'copy_tiktok_uid': copy_tiktok_uid,
    'copy_proxy_string': copy_proxy_string,
    'copy_cookie_string': copy_cookie_string,
    'copy_payout_method': copy_payout_method,
    'apply_filter_chip': apply_filter_chip,
    'apply_mono_filter_chip': apply_mono_filter_chip,
    'sort_tree': _treeview_sort_column,
    'go_first_page': go_first_page,
    'go_prev_page': go_prev_page,
    'go_next_page': go_next_page,
    'go_last_page': go_last_page,
    'change_page_size': change_page_size,
    'youtube_monitor': youtube_monitor_handlers,
    'activity': activity_handlers,
}
ui_widgets = build_dashboard(root, ui_state, ui_handlers)

# Bind double-click & right-click on monetization tree
if 'monetization_tree' in ui_widgets:
    ui_widgets['monetization_tree'].bind("<Double-1>", lambda _e: view_monetization_details())
    def _on_mono_tree_right_click(event):
        mono_t = ui_widgets.get('monetization_tree')
        if not mono_t: return
        iid = mono_t.identify_row(event.y)
        if not iid: return
        if iid not in mono_t.selection():
            mono_t.selection_set(iid)
        mono_ctx = ui_widgets.get('mono_ctx_menu')
        if mono_ctx:
            mono_ctx.post(event.x_root, event.y_root)
    ui_widgets['monetization_tree'].bind("<Button-3>", _on_mono_tree_right_click)

topbar = ui_widgets['topbar']
manage_frame = ui_widgets['manage_frame']
control_frame = ui_widgets['control_frame']
project_dropdown = ui_widgets['project_dropdown']
tree = ui_widgets['tree']
important_log_text = ui_widgets['important_log_text']
failed_uploads_text = ui_widgets['failed_uploads_text']
ctx_menu = ui_widgets['ctx_menu']
status_text = ui_widgets['status_text']
status_count_label = ui_widgets['status_count_label']
clock_label = ui_widgets['clock_label']
youtube_monitor_view = ui_widgets.get('youtube_monitor_view')
batch_download_view = ui_widgets.get('batch_download_view')
activity_view = ui_widgets.get('activity_view')

# Initialize Bounded LogEngine
from log_engine import get_log_engine
get_log_engine().initialize_ui(
    root=root,
    status_widget=status_text,
    important_widget=important_log_text,
    trim_func=trim_text_widget_lines,
)

def _start_youtube_monitor_safe():
    if os.environ.get('FROZEN_SMOKE_TEST', '').strip().lower() in ('1', 'true'):
        update_status("[YouTube] Smoke mode: bỏ qua monitor auto-start")
        return
    if 'pytest' in sys.modules or 'unittest' in sys.modules:
        update_status("[YouTube] Test mode: bỏ qua monitor auto-start")
        return
    cfg = youtube_monitor.get_config()
    if not cfg.get('auto_start', True):
        update_status("[YouTube] auto_start=false, bỏ qua.")
        return
    def _run():
        try:
            ok, msg = youtube_monitor.start_monitor()
            update_status(f"[YouTube] {msg}")
        except Exception as e:
            update_status(f"[YouTube] Auto-start lỗi: {e}")
    threading.Thread(target=_run, daemon=True).start()

def _on_profile_filter_changed(*_args):
    current_page_var.set(1)
    update_profile_list()

selected_project_var.trace('w', _on_profile_filter_changed)
filter_var.trace('w', _on_profile_filter_changed)
selected_project_var.trace('w', _update_monetization_table)
filter_var.trace('w', _update_monetization_table)
scale_var.trace('w', _apply_scale)

# Initial population of monetization table
root.after(200, _update_monetization_table)

def _on_tree_right_click(event):
    iid = tree.identify_row(event.y)
    if not iid: return
    if iid not in tree.selection():
        tree.selection_set(iid)
    ctx_menu.post(event.x_root, event.y_root)

tree.bind("<Button-3>", _on_tree_right_click)
tree.bind("<Double-1>", lambda _event: view_profile_details())
tree.bind("<<TreeviewSelect>>", _update_action_buttons)
tree.bind("<Delete>", lambda _event: delete_profile())

def _tick():
    # Cập nhật UI
    _refresh_status_bar()
    try:
        toast_manager.poll_queue()
    except Exception:
        pass
    try:
        if youtube_monitor_view:
            youtube_monitor_view.refresh_data()
        if batch_download_view:
            batch_download_view.refresh_data()
        if activity_view:
            activity_view.refresh_data()
    except Exception:
        pass
    
    # --- LOGIC RESET NGÀY TỰ ĐỘNG (MIDNIGHT CHECK) ---
    current_date_str = datetime.now().strftime('%Y-%m-%d')
    need_save = False
    
    # Quét qua tất cả profile để xem đã qua ngày mới chưa
    for name, prof in profiles.items():
        if prof['uploads_today_date'] != current_date_str:
            # Phát hiện ngày mới -> Reset
            prof['uploads_yesterday_count'] = prof['uploads_today_count']
            prof['uploads_today_count'] = 0
            prof['uploads_today_date'] = current_date_str
            need_save = True
            
    if need_save:
        save_configs()
        update_status("Đã tự động reset bộ đếm ngày mới (Midnight Reset).")
    # ------------------------------------------------
    
    root.after(1000, _tick)

root.after(1000, _tick)
root.after(100, _drain_update_ui_queue)

if __name__ == "__main__":
    require_license_then_boot()
    _run_auto6_watcher_test_from_env()
    _run_single_upload_test_from_env()
    _run_live_verify_from_env()
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
