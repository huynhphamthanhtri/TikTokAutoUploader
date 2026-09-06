import copy
import hmac
import json
import math
import os
import queue
import random
import re
import shutil
import subprocess
import threading
import time
import traceback
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from pyngrok import conf as ngconf
from pyngrok import ngrok
from werkzeug.serving import make_server
from yt_dlp import YoutubeDL
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .activity import append_activity, remember_download
from . import ffmpeg_helper
from . import ngrok_helper
from . import ngrok_owner
from .state_store import VideoStateStore
from .login_browser import YouTubeLoginBrowser
from .key_manager import ApiKeyManager
from .schedule_learner import ScheduleLearner, PollingWindow, DEFAULT_TIMEZONE
from .predictive_scheduler import PredictiveScheduler
from .polling_settings import normalize_settings


def _app_root():
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundled_root():
    import sys
    if getattr(sys, "frozen", False):
        internal = Path(sys.executable).resolve().parent / "_internal"
        if internal.exists():
            return internal
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = _app_root()
DOWNLOADS_DIR = APP_ROOT / "downloads"
TEMP_DIR = APP_ROOT / "temp_dl"
CHANNELS_JSON = APP_ROOT / "channels.json"
CHANNEL_CACHE_JSON = APP_ROOT / "channel_cache.json"
CONFIG_JSON = APP_ROOT / "youtube_config.json"
CSV_LOG = APP_ROOT / "downloads_log.csv"
STATE_DB = APP_ROOT / "youtube_monitor_state.db"
YOUTUBE_LOGIN_PROFILE = APP_ROOT / "Auto_Data" / "YouTube_Login_Profile"
NGROK_BINARY = APP_ROOT / "ngrok.exe"

NGROK_PORT_DEFAULT = 5000
SHORT_SLOW_MIN_DURATION = 40.0
SHORT_SLOW_MAX_DURATION = 60.0
SHORT_TARGET_DURATION = 61.0
MIN_SECONDS = 61
LOOP_MIN_DURATION = 60
RESUBSCRIBE_LEAD_TIME_HOURS = 24
RESUBSCRIBE_CHECK_SECONDS = 900
SUBSCRIBE_RETRY_TICK_SECONDS = 5
SUBSCRIBE_RETRY_DELAYS = (5 * 3600,)
SUBSCRIBE_VERIFICATION_GRACE_SECONDS = 600
YOUTUBE_FEED_URL_TEMPLATE = "https://www.youtube.com/xml/feeds/videos.xml?channel_id={}"
MONITOR_START_GRACE_SECONDS = 300
MAX_ACCEPTABLE_AGE_HOURS = int(os.environ.get("MAX_ACCEPTABLE_AGE_HOURS", "12"))
WATERMARK_SLACK_MINUTES = int(os.environ.get("WATERMARK_SLACK_MINUTES", "30"))
SEEN_MAX_PER_CHANNEL = 1200
BATCH_SCAN_LIMIT = 50
FORMAT_FAST_720P = (
    "bv[height<=720][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/"
    "bv[height<=720][ext=mp4]+ba[ext=m4a]/"
    "bv[height<=720][vcodec^=avc1]+ba/"
    "b[height<=720][ext=mp4][vcodec^=avc1]/"
    "b[height<=720][ext=mp4]/"
    "b[height<=720]"
)
FORMAT_COMPAT_720P = (
    "bv[height<=720]+ba/"
    "b[height<=720]"
)
FORMAT_FAST_1080P = (
    "bv[height<=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/"
    "bv[height<=1080][ext=mp4]+ba[ext=m4a]/"
    "bv[height<=1080][vcodec^=avc1]+ba/"
    "b[height<=1080][ext=mp4][vcodec^=avc1]/"
    "b[height<=1080][ext=mp4]/"
    "b[height<=1080]"
)
FORMAT_COMPAT_1080P = (
    "bv[height<=1080]+ba/"
    "b[height<=1080]"
)


def _quality_format_selectors(quality):
    q = str(quality or "").strip().lower()
    if q == "1080p":
        return FORMAT_FAST_1080P, FORMAT_COMPAT_1080P
    return FORMAT_FAST_720P, FORMAT_COMPAT_720P


CONFIG_DEFAULTS = {
    "api_keys": [],
    "ngrok_port": NGROK_PORT_DEFAULT,
    "download_workers": 4,
    "max_video_minutes": 0,
    "video_quality": "720p",
    "auto_start": True,
    "cookies_file": "",
    "proxy_rotation": True,
    "concurrent_fragments": 8,
    "youtube_proxy_fallback": False,
    "youtube_cookie_policy": "fallback",
    "poll_interval_healthy_seconds": 120,
    "poll_interval_degraded_seconds": 30,
    "poll_scan_limit": 10,
    "push_detection_mode": "auto",
}

FAILURE_PERMANENT = "permanent"
FAILURE_AUTH_REQUIRED = "auth_required"
FAILURE_HTTP_403 = "http_403"
FAILURE_YOUTUBE_BLOCK = "youtube_block"
FAILURE_PROXY_TRANSPORT = "proxy_transport"
FAILURE_FORMAT_UNAVAILABLE = "format_unavailable"
FAILURE_TRANSIENT_NETWORK = "transient_network"
FAILURE_RETRY = "retry"

_FAILURE_PERMANENT_KW = (
    "members-only", "members only", "copyright", "removed by user",
    "this video is private", "this video is unavailable", "video unavailable",
    "copyright claim", "copyright strike", "age-restricted", "age restriction",
    "sign in to age verify", "unavailable for legal reasons",
    "has not made this video available in your country", "video has been removed",
    "account associated with this video has been terminated",
)

_FAILURE_AUTH_KW = (
    "sign in to watch", "sign in required", "you need to sign in",
    "log in to watch", "login required", "please sign in", "authentication required",
    "you must be logged in", "sign in to access",
)

_FAILURE_TRANSIENT_KW = (
    "timed out", "timeout", "connection refused", "connection reset",
    "connection aborted", "remote end closed connection", "temporary failure",
    "unexpected end of stream", "500", "502", "503",
)

_FAILURE_FORMAT_KW = (
    "unable to extract", "no video formats", "no matching formats",
    "format is not available", "requested format is not available",
    "format unavailable", "no such format", "unable to download video data",
)

DOWNLOADS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

log_queue = queue.Queue()
download_queue = queue.Queue()
websub_payload_queue = queue.Queue()
stop_event = threading.Event()
_state_lock = threading.RLock()
_store_lock = threading.RLock()
_config_json_lock = threading.RLock()
_encode_sem = threading.Semaphore(1)
_download_sem = threading.Semaphore(CONFIG_DEFAULTS["download_workers"])
_active_downloads = {}
_active_downloads_lock = threading.Lock()
_callback_server = None
_callback_server_thread = None
_callback_port = None
_callback_instance_id = None
_callback_owner_token = None
_monitor_started = False
_monitor_started_epoch = None
_monitor_session_id = None
_monitor_gen = 0
_monitor_gen_lock = threading.Lock()
_monitor_state = "STOPPED"
_monitor_state_lock = threading.Lock()
_last_websub_ok_at = None
_last_websub_error = ""
_recovery_attempt = 0
_recovery_kick = threading.Event()
_recovery_lock = threading.Lock()
_ngrok_auth_status = "unknown"
_ngrok_auth_source = ""
_ngrok_auth_lock = threading.Lock()
MAX_RECOVERY_ATTEMPTS = 3
RECOVERY_BACKOFF_BASE = 15
RECOVERY_BACKOFF_MAX = 300
_all_threads = []
_proxy_pool = []
_proxy_by_profile = {}
_proxy_rr_index = 0
_proxy_lock = threading.Lock()
public_callback_url = None
public_callback_verified = False
active_tunnel_provider = "none"
_last_verified_tunnel_url = None
_ngrok_tunnel_was_healthy = False
_ngrok_verify_log_lock = threading.Lock()
last_callback_post_time = None
last_error = ""
downloaded_today = 0
downloaded_today_date = datetime.now().strftime("%Y-%m-%d")

_pending_video_ids = set()
_pending_lock = threading.Lock()
_retry_after = {}
_finalize_lock = threading.Lock()
_retry_lock = threading.Lock()
_video_detected_callback = None
_video_detected_callback_lock = threading.Lock()
_video_ready_callback = None
_video_ready_callback_lock = threading.Lock()
MAX_RETRIES = 4
RETRY_DELAYS = [0, 15, 45, 120]
RETRY_COOLDOWN = 300

_subscription_status = {}
_subscription_lock = threading.RLock()
_subscription_inflight = set()
_websub_secret_lock = threading.Lock()
_websub_secret_cache = None
_video_state_store = None
_video_state_store_lock = threading.Lock()
_last_poll_ok_at = None
_last_poll_error = ""
_youtube_login_browser = YouTubeLoginBrowser(YOUTUBE_LOGIN_PROFILE)


def _get_video_state_store():
    global _video_state_store
    with _video_state_store_lock:
        if _video_state_store is None:
            _video_state_store = VideoStateStore(STATE_DB)
        return _video_state_store


def log(message):
    text = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    try:
        log_queue.put_nowait(text)
    except Exception:
        pass


def get_logs(limit=200):
    items = []
    while len(items) < limit:
        try:
            items.append(log_queue.get_nowait())
        except queue.Empty:
            break
    return items


_config_cache = None
_config_cache_path = None
_config_cache_mtime = None
_config_cache_lock = threading.Lock()


def get_config():
    global _config_cache, _config_cache_path, _config_cache_mtime
    path_str = str(CONFIG_JSON)
    try:
        mtime = CONFIG_JSON.stat().st_mtime if CONFIG_JSON.exists() else 0.0
    except OSError:
        mtime = 0.0

    with _config_cache_lock:
        if _config_cache is not None and _config_cache_path == path_str and _config_cache_mtime == mtime:
            return dict(_config_cache)

        cfg = dict(CONFIG_DEFAULTS)
        if CONFIG_JSON.exists():
            try:
                with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                cfg.update(data)
            except Exception as e:
                log(f"[Config] Không đọc được youtube_config.json: {e}")
        cfg["predictive_polling"] = normalize_settings(cfg.get("predictive_polling"))
        _config_cache = dict(cfg)
        _config_cache_path = path_str
        _config_cache_mtime = mtime
        return dict(_config_cache)


def _save_config(cfg):
    global _config_cache, _config_cache_path, _config_cache_mtime
    merged = dict(CONFIG_DEFAULTS)
    merged.update(cfg or {})
    merged["predictive_polling"] = normalize_settings(merged.get("predictive_polling"))
    tmp = CONFIG_JSON.with_name(f"{CONFIG_JSON.name}.{uuid.uuid4().hex}.tmp")
    with _config_json_lock:
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_JSON)
            with _config_cache_lock:
                _config_cache = dict(merged)
                _config_cache_path = str(CONFIG_JSON)
                try:
                    _config_cache_mtime = CONFIG_JSON.stat().st_mtime
                except OSError:
                    _config_cache_mtime = None
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def set_predictive_polling(settings):
    values = normalize_settings(settings, strict=True)
    with _config_json_lock:
        cfg = get_config()
        cfg["predictive_polling"] = {**cfg["predictive_polling"], **values}
        _save_config(cfg)
    return values


def get_predictive_runtime_status():
    if _predictive_scheduler and _predictive_scheduler_thread and _predictive_scheduler_thread.is_alive():
        return _predictive_scheduler.get_runtime_status()
    return {cid: {"label": "Đang chờ"} for cid in channels_store.all_items()}


def _resolve_cookies_file():
    cfg = get_config()
    candidates = []
    configured = str(cfg.get("cookies_file") or "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.append(APP_ROOT / "cookies.txt")
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                return str(path)
        except Exception:
            continue
    return None


def get_cookies_file():
    return _resolve_cookies_file() or ""


def set_cookies_file(path):
    path = str(path or "").strip()
    if path and not Path(path).exists():
        return False, "File cookie không tồn tại."
    cfg = get_config()
    cfg["cookies_file"] = path
    _save_config(cfg)
    if path:
        log(f"[Cookies] Đã lưu cookie file: {path}")
        return True, "Đã lưu cookie file."
    log("[Cookies] Đã xóa cookie file cấu hình.")
    return True, "Đã xóa cookie file."


def set_max_video_minutes(minutes):
    try:
        value = int(str(minutes).strip())
    except Exception:
        return False, "Giới hạn phút không hợp lệ."
    if value < 0:
        return False, "Giới hạn phút phải >= 0."
    cfg = get_config()
    cfg["max_video_minutes"] = value
    _save_config(cfg)
    text = "không giới hạn" if value == 0 else f"{value} phút"
    log(f"[Config] Max video: {text}")
    return True, f"Đã lưu giới hạn: {text}."


def get_video_quality():
    try:
        q = str(get_config().get("video_quality", "720p")).strip().lower()
        return "1080p" if q == "1080p" else "720p"
    except Exception:
        return "720p"


def set_video_quality(quality):
    q = str(quality or "").strip().lower()
    if q not in ("720p", "1080p"):
        return False, "Chất lượng video không hợp lệ (chỉ chấp nhận 720p hoặc 1080p)."
    cfg = get_config()
    cfg["video_quality"] = q
    _save_config(cfg)
    label = "Tối đa 1080p (Sắc nét)" if q == "1080p" else "Tối đa 720p (Nhanh)"
    log(f"[Config] Chất lượng video: {label}")
    return True, f"Đã lưu chất lượng video: {label}."


def _format_duration(seconds):
    try:
        seconds = float(seconds or 0)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return "?"
    minutes = seconds / 60
    return f"{minutes:.1f} phút"


def _parse_proxy_string(proxy_str):
    if not proxy_str:
        return None
    clean = str(proxy_str).replace("http://", "").replace("https://", "").strip()
    parts = clean.split(":")
    if len(parts) == 2:
        return {"ip": parts[0], "port": parts[1], "user": "", "pass": ""}
    if len(parts) >= 4:
        return {"ip": parts[0], "port": parts[1], "user": parts[2], "pass": parts[3]}
    return None


def _proxy_to_url(parsed):
    if not parsed or not parsed.get("ip") or not parsed.get("port"):
        return None
    if parsed.get("user"):
        return f"http://{parsed['user']}:{parsed.get('pass', '')}@{parsed['ip']}:{parsed['port']}"
    return f"http://{parsed['ip']}:{parsed['port']}"


def _mask_proxy(proxy_url):
    if not proxy_url:
        return "direct"
    clean = str(proxy_url).replace("http://", "").replace("https://", "")
    if "@" in clean:
        clean = clean.split("@", 1)[1] + " (auth)"
    return clean


def _try_pending(cid, vid):
    with _pending_lock:
        full_id = f"{cid}:{vid}"
        if full_id in _pending_video_ids:
            return False
        _pending_video_ids.add(full_id)
        return True


def _remove_pending(cid, vid):
    with _pending_lock:
        _pending_video_ids.discard(f"{cid}:{vid}")


def _is_pending(cid, vid):
    with _pending_lock:
        return f"{cid}:{vid}" in _pending_video_ids


def _is_http_403(message):
    text = str(message or "").lower()
    return (
        "http error 403" in text
        or "http status 403" in text
        or "403 forbidden" in text
        or "error 403" in text
        or "403: forbidden" in text
    )


def _is_proxy_transport_error(message):
    text = str(message or "").lower()
    keywords = (
        "407", "proxy", "socks", "tunnel",
    )
    return any(k in text for k in keywords)


def _is_transient_network_error(message):
    text = str(message or "").lower()
    return any(k in text for k in _FAILURE_TRANSIENT_KW)


def _is_format_unavailable_error(message):
    text = str(message or "").lower()
    return any(k in text for k in _FAILURE_FORMAT_KW)


def _is_auth_required_error(message):
    text = str(message or "").lower()
    return any(k in text for k in _FAILURE_AUTH_KW)


def _classify_failure(error):
    """Structured failure taxonomy used by the download attempt planner.

    Order matters: permanent wins first, then HTTP 403, then the YouTube bot
    block (which contains 'sign in to confirm'), then auth, proxy transport,
    transient network and format-unavailable. Everything else falls back to a
    generic retryable class.
    """
    text = str(error or "").lower()
    for kw in _FAILURE_PERMANENT_KW:
        if kw in text:
            return FAILURE_PERMANENT
    if _is_http_403(text):
        return FAILURE_HTTP_403
    if _is_youtube_block_error(error):
        return FAILURE_YOUTUBE_BLOCK
    if _is_auth_required_error(text):
        return FAILURE_AUTH_REQUIRED
    if _is_proxy_transport_error(text):
        return FAILURE_PROXY_TRANSPORT
    if _is_transient_network_error(text):
        return FAILURE_TRANSIENT_NETWORK
    if _is_format_unavailable_error(text):
        return FAILURE_FORMAT_UNAVAILABLE
    return FAILURE_RETRY


def _classify_download_error(error):
    """Legacy classifier kept for compatibility with existing callers/tests.

    Maps the structured taxonomy back to the historical ``retry``/``retry_block``/
    ``retry_proxy``/``permanent`` values.
    """
    cls = _classify_failure(error)
    if cls == FAILURE_PERMANENT:
        return "permanent"
    if cls == FAILURE_YOUTUBE_BLOCK:
        return "retry_block"
    if cls == FAILURE_PROXY_TRANSPORT:
        return "retry_proxy"
    return "retry"


def _schedule_retry(cid, vid, attempt):
    delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_COOLDOWN
    with _retry_lock:
        _retry_after[f"{cid}:{vid}"] = time.time() + delay


def _clear_retry(cid, vid):
    with _retry_lock:
        _retry_after.pop(f"{cid}:{vid}", None)
        _retry_after.pop(f"{cid}:{vid}:attempt", None)
        _retry_after.pop(f"{cid}:{vid}:due", None)


def _get_retry_due(cid, vid):
    with _retry_lock:
        deadline = _retry_after.get(f"{cid}:{vid}")
        if deadline is None:
            return None
        remaining = deadline - time.time()
        return max(0, remaining)


def _load_tiktok_proxies():
    config_path = APP_ROOT / "configs.json"
    proxy_by_profile = {}
    proxy_pool = []
    if not config_path.exists():
        return proxy_by_profile, proxy_pool
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        profiles = data.get("profiles") if isinstance(data, dict) else None
        if not isinstance(profiles, dict):
            profiles = data if isinstance(data, dict) else {}
        for name, prof in profiles.items():
            if not isinstance(prof, dict) or not prof.get("use_proxy"):
                continue
            proxy_url = _proxy_to_url(_parse_proxy_string(prof.get("proxy_string", "")))
            if not proxy_url:
                continue
            proxy_by_profile[str(name)] = proxy_url
            proxy_pool.append(proxy_url)
        proxy_pool = list(dict.fromkeys(proxy_pool))
        if proxy_pool:
            log(f"[Proxy] Loaded {len(proxy_by_profile)} profile proxies, pool={len(proxy_pool)}")
        else:
            log("[Proxy] Không tìm thấy proxy hợp lệ trong profiles")
    except Exception as e:
        log(f"[Proxy] Load lỗi: {e}")
    return proxy_by_profile, proxy_pool


def _next_proxy():
    global _proxy_rr_index
    if not _proxy_pool:
        return None
    with _proxy_lock:
        proxy = _proxy_pool[_proxy_rr_index % len(_proxy_pool)]
        _proxy_rr_index += 1
        return proxy


def _ensure_proxy_pool_loaded():
    global _proxy_pool, _proxy_by_profile
    if _proxy_pool or _proxy_by_profile:
        return
    _proxy_by_profile, _proxy_pool = _load_tiktok_proxies()


def _proxy_for_profile(profile_name):
    if not profile_name:
        return None
    return _proxy_by_profile.get(str(profile_name))


def _is_youtube_block_error(message):
    text = str(message or "").lower()
    keywords = (
        "sign in to confirm", "not a bot", "captcha", "confirm you",
        "too many requests", "429", "rate limit", "temporarily blocked",
    )
    return any(k in text for k in keywords)


def _is_proxy_download_error(message):
    text = str(message or "").lower()
    keywords = (
        "proxy", "407", "tunnel", "cannot connect", "connection refused",
        "connection reset", "connection aborted", "timed out", "timeout",
        "502", "503", "socks", "remote end closed connection",
    )
    return any(k in text for k in keywords) or _is_youtube_block_error(text)


@dataclass(frozen=True)
class YtdlpAttempt:
    name: str
    route: str
    proxy: str
    use_cookies: bool
    format_selector: str
    reason: str = ""
    player_client: str = ""
    triggers: tuple = ()


@dataclass
class DownloadOutcome:
    ok: bool
    retryable: bool
    permanent: bool
    failure_class: str = FAILURE_RETRY
    attempts_used: int = 0
    final_path: str = ""
    detail: str = ""


def validate_youtube_cookie_file(path):
    """Offline (no-network) sanity check of a Netscape cookie file.

    Never reads cookie values into logs or activity records. Returns
    ``(ok, reason)`` where reason is a short human-readable description.
    """
    if not path:
        return False, "Chưa cấu hình file cookie."
    p = Path(path)
    if not p.exists():
        return False, "File cookie không tồn tại."
    if not p.is_file():
        return False, "Đường dẫn cookie không phải file."
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(65536)
    except Exception as e:
        return False, f"Không đọc được file cookie: {e}"

    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        return False, "File cookie rỗng (không có entry hợp lệ)."

    yt_entries = []
    for line in lines:
        fields = line.split("\t")
        if len(fields) >= 6:
            domain = fields[0].lower()
            if "youtube.com" in domain or "google.com" in domain:
                yt_entries.append(fields)

    if not yt_entries:
        return False, "Không tìm thấy cookie cho domain YouTube hoặc Google (.youtube.com / .google.com)."

    now = time.time()
    valid_yt = 0
    expired_yt = 0
    has_auth_cookie = False
    auth_cookie_names = {"login_info", "sid", "hsid", "ssid", "apisid", "sapisid", "__secure-1psid", "__secure-3psid"}

    for fields in yt_entries:
        try:
            expires = int(fields[4]) if len(fields) > 4 else 0
        except Exception:
            expires = 0

        cookie_name = fields[5].lower() if len(fields) > 5 else ""
        if cookie_name in auth_cookie_names:
            has_auth_cookie = True

        if expires > 0 and expires < now:
            expired_yt += 1
        else:
            valid_yt += 1

    if valid_yt == 0:
        return False, f"Tất cả {expired_yt} cookie YouTube/Google trong file đã hết hạn."

    auth_msg = " (Đã có cookie đăng nhập)" if has_auth_cookie else ""
    return True, f"Tìm thấy {valid_yt} cookie YouTube/Google hợp lệ{auth_msg}."


def check_youtube_cookie_live(path=None):
    """Kiểm tra tính hợp lệ và khả năng kết nối live của file cookie YouTube với yt-dlp."""
    resolved = path or _resolve_cookies_file()
    if not resolved:
        return False, "Chưa chọn hoặc cấu hình file cookie."

    ok, reason = validate_youtube_cookie_file(resolved)
    if not ok:
        return False, f"File cookie không hợp lệ: {reason}"

    try:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "cookiefile": str(resolved),
            "cookies": str(resolved),
            "socket_timeout": 12,
            "extractor_args": {"youtube": {"skip": ["hls"]}},
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False)
            if info and info.get("id"):
                return True, f"Cookie YouTube sẵn sàng & Live OK! ({reason})"
        return True, f"Cookie YouTube hợp lệ: {reason}"
    except Exception as e:
        err_msg = str(e)
        if "confirm you are not a bot" in err_msg.lower() or "sign in" in err_msg.lower():
            return False, f"Cookie bị YouTube yêu cầu xác minh bot: {err_msg[:120]}"
        if "403" in err_msg:
            return False, f"Cookie bị lỗi 403 Forbidden: {err_msg[:120]}"
        return False, f"Kiểm tra live cookie thất bại: {err_msg[:120]}"


def _ytdlp_alternate_client():
    """Pick a supported alternate player client from the installed yt-dlp runtime.

    Returns the client name or ``""`` when none of the known fallback clients is
    present at runtime (so callers skip the alternate-client attempt entirely).
    """
    try:
        from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS
        available = set(INNERTUBE_CLIENTS.keys())
    except Exception:
        return ""
    for candidate in ("android_vr", "web_embedded", "web_safari"):
        if candidate in available:
            return candidate
    return ""


def _build_ytdlp_opts(base_opts, attempt, attempt_dir):
    opts = copy.deepcopy(base_opts)
    opts["outtmpl"] = str(Path(attempt_dir) / "%(title).100s.%(ext)s")
    opts["format"] = attempt.format_selector
    if attempt.route == "direct":
        opts["proxy"] = ""
    else:
        opts["proxy"] = attempt.proxy
    if attempt.use_cookies:
        cookies = _resolve_cookies_file()
        if cookies:
            opts["cookiefile"] = cookies
            opts["cookies"] = cookies
    else:
        opts.pop("cookiefile", None)
        opts.pop("cookies", None)
    if attempt.player_client:
        ea = {}
        for key, value in (opts.get("extractor_args") or {}).items():
            ea[key] = dict(value) if isinstance(value, dict) else {}
        youtube_ea = dict(ea.get("youtube") or {})
        youtube_ea["player_client"] = [attempt.player_client]
        ea["youtube"] = youtube_ea
        opts["extractor_args"] = ea
    return opts


def _build_attempt_plan(profile_name, explicit_proxy=None):
    """Build the ordered attempt chain. Direct/no-proxy is always first."""
    cfg = get_config()
    cookie_policy = str(cfg.get("youtube_cookie_policy", "fallback")).strip().lower()
    use_cookies = cookie_policy not in ("never", "off", "none")
    cookie_available = bool(_resolve_cookies_file())
    proxy_fallback = bool(cfg.get("youtube_proxy_fallback", False))
    quality = get_video_quality()
    fast_fmt, compat_fmt = _quality_format_selectors(quality)

    attempts = [
        YtdlpAttempt(
            "direct-primary", "direct", "", False, fast_fmt,
            "direct anonymous",
        ),
        YtdlpAttempt(
            "direct-alt-format", "direct", "", False, compat_fmt,
            "403 -> alternate format",
            triggers=(FAILURE_HTTP_403, FAILURE_FORMAT_UNAVAILABLE),
        ),
    ]
    alt_client = _ytdlp_alternate_client()
    if alt_client:
        attempts.append(YtdlpAttempt(
            "direct-alt-client", "direct", "", False, fast_fmt,
            "403 -> alternate client",
            player_client=alt_client,
            triggers=(FAILURE_HTTP_403,),
        ))
    if use_cookies and cookie_available:
        attempts.append(YtdlpAttempt(
            "direct-cookies", "direct", "", True, fast_fmt,
            "auth/403 -> cookies",
            triggers=(FAILURE_HTTP_403, FAILURE_AUTH_REQUIRED, FAILURE_YOUTUBE_BLOCK),
        ))
    proxy = None
    if proxy_fallback:
        proxy = _proxy_for_profile(profile_name) or explicit_proxy
    elif explicit_proxy:
        proxy = explicit_proxy
    if proxy:
        attempts.append(YtdlpAttempt(
            "proxy-exact", "proxy", proxy, False, fast_fmt,
            "proxy fallback",
            triggers=(FAILURE_HTTP_403, FAILURE_YOUTUBE_BLOCK, FAILURE_PROXY_TRANSPORT, FAILURE_TRANSIENT_NETWORK),
        ))
    return attempts


def _select_next_attempt(attempts, from_index, failure_cls):
    """Pick the next attempt index justified by the failure class, or None."""
    if failure_cls == FAILURE_PERMANENT:
        return None
    target = (failure_cls,) if failure_cls != FAILURE_AUTH_REQUIRED else (FAILURE_AUTH_REQUIRED,)
    for i in range(from_index + 1, len(attempts)):
        triggers = attempts[i].triggers
        if not triggers or any(t in target for t in triggers):
            return i
    return None


api_key_manager = ApiKeyManager()


def get_api_key():
    api_key_manager.load_from_config(get_config())
    return api_key_manager.get_active_key() or ""


def get_youtube_client(api_key=None):
    api_key_manager.load_from_config(get_config())
    return api_key_manager.get_youtube_client(api_key)


def check_api_key_validity(api_key):
    return api_key_manager.test_key(api_key)


def check_and_save_api_key(api_key):
    api_key = str(api_key or "").strip()
    ok, msg = api_key_manager.test_key(api_key)
    if ok:
        cfg = get_config()
        api_key_manager.load_from_config(cfg)
        api_key_manager.add_key(api_key)
        api_key_manager.save_to_config(cfg)
        _save_config(cfg)
        log("[API] Đã lưu YouTube API key vào pool.")
    else:
        log(f"[API] {msg}")
    return ok, msg


def add_api_key_to_pool(api_key):
    api_key = str(api_key or "").strip()
    ok, msg = api_key_manager.test_key(api_key)
    if ok:
        cfg = get_config()
        api_key_manager.load_from_config(cfg)
        api_key_manager.add_key(api_key)
        api_key_manager.save_to_config(cfg)
        _save_config(cfg)
        log(f"[API] Đã thêm key ...{api_key[-4:]} vào pool.")
    return ok, msg


def remove_api_key_from_pool(api_key):
    api_key = str(api_key or "").strip()
    cfg = get_config()
    api_key_manager.load_from_config(cfg)
    removed = api_key_manager.remove_key(api_key)
    if removed:
        api_key_manager.save_to_config(cfg)
        _save_config(cfg)
        log(f"[API] Đã xóa key ...{api_key[-4:]} khỏi pool.")
    return removed


def get_api_keys_pool_status():
    cfg = get_config()
    plain_keys = cfg.get("api_keys") or []
    pool = cfg.get("api_keys_pool") or []
    configured_keys = {str(key).strip() for key in plain_keys if str(key).strip()} if isinstance(plain_keys, list) else set()
    if isinstance(pool, list):
        configured_keys.update(str(item["key"]).strip() for item in pool if isinstance(item, dict) and item.get("key"))
    # Refreshing the UI must not overwrite a cooldown just recorded by polling.
    if set(api_key_manager.get_keys()) != configured_keys:
        api_key_manager.load_from_config(cfg)
    return api_key_manager.get_all_status()


def test_api_key_in_pool(api_key):
    get_api_keys_pool_status()
    result = api_key_manager.test_key(api_key)
    cfg = get_config()
    api_key_manager.save_to_config(cfg)
    _save_config(cfg)
    return result


class ChannelsStore:
    def __init__(self, path):
        self.path = Path(path)
        self._channels = {}
        self._dirty = False
        self._revision = 0
        self._autosave_stop = threading.Event()
        self._autosave_thread = None

    def _serialize(self):
        out = {}
        for cid, meta in self._channels.items():
            out[cid] = {
                "folder": meta.get("folder"),
                "profile_name": meta.get("profile_name", ""),
                "active": bool(meta.get("active", True)),
                "seen": sorted(list(meta.get("seen", set()))),
                "last_pub_utc": meta.get("last_pub_utc"),
                "last_known_video_id": meta.get("last_known_video_id", ""),
                "process_short": bool(meta.get("process_short", True)),
                "title": meta.get("title", ""),
                "thumbnail": meta.get("thumbnail", ""),
                "channel_url": meta.get("channel_url", ""),
                "added_at": meta.get("added_at", ""),
                "meta_attempted": bool(meta.get("meta_attempted", False)),
                "uploads_playlist_id": meta.get("uploads_playlist_id", ""),
                "manual_windows": meta.get("manual_windows") or [],
                "schedule_learned": meta.get("schedule_learned") or {},
                "timezone": meta.get("timezone") or DEFAULT_TIMEZONE,
            }
        return out

    def load(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            with _store_lock:
                self._channels = {}
                for cid, meta in data.items():
                    self._channels[cid] = {
                        "folder": meta.get("folder") or str(DOWNLOADS_DIR / cid),
                        "profile_name": meta.get("profile_name", ""),
                        "active": meta.get("active", True),
                        "seen": set(meta.get("seen", [])),
                        "last_pub_utc": meta.get("last_pub_utc"),
                        "last_known_video_id": meta.get("last_known_video_id", ""),
                        "process_short": meta.get("process_short", True),
                        "title": meta.get("title", ""),
                        "thumbnail": meta.get("thumbnail", ""),
                        "channel_url": meta.get("channel_url", ""),
                        "added_at": meta.get("added_at", ""),
                        "meta_attempted": bool(meta.get("meta_attempted", False)),
                        "uploads_playlist_id": meta.get("uploads_playlist_id", ""),
                        "manual_windows": meta.get("manual_windows") or [],
                        "schedule_learned": meta.get("schedule_learned") or {},
                        "timezone": meta.get("timezone") or DEFAULT_TIMEZONE,
                    }
            log(f"[Channels] Loaded {len(self._channels)} channels.")
        except Exception as e:
            log(f"[Channels] Load lỗi: {e}")

    def save_now(self):
        try:
            tmp = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
            with _store_lock:
                data = self._serialize()
                snapshot_revision = self._revision
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            with _store_lock:
                if self._revision == snapshot_revision:
                    self._dirty = False
            return True
        except Exception as e:
            log(f"[Channels] Save lỗi: {e}")
            return False
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def start_autosave(self):
        if self._autosave_thread and self._autosave_thread.is_alive():
            return
        self._autosave_stop.clear()
        def loop():
            while not self._autosave_stop.is_set():
                self._autosave_stop.wait(5)
                if self._dirty:
                    self.save_now()
        self._autosave_thread = threading.Thread(target=loop, daemon=True)
        self._autosave_thread.start()

    def stop_autosave(self):
        self._autosave_stop.set()
        if self._autosave_thread:
            self._autosave_thread.join(timeout=2)
        if self._dirty:
            self.save_now()

    def all_items(self):
        with _store_lock:
            return {cid: dict(m, seen=set(m.get("seen", set()))) for cid, m in self._channels.items()}

    def add_channel(self, cid, folder, profile_name="", process_short=True, title="", thumbnail="", channel_url=""):
        with _store_lock:
            existing = self._channels.get(cid, {})
            self._channels[cid] = {
                "folder": folder,
                "profile_name": profile_name,
                "active": True,
                "seen": set(existing.get("seen", set())),
                "last_pub_utc": existing.get("last_pub_utc"),
                "process_short": process_short,
                "title": title or existing.get("title", ""),
                "thumbnail": thumbnail or existing.get("thumbnail", ""),
                "channel_url": channel_url or existing.get("channel_url", ""),
                "added_at": existing.get("added_at") or datetime.now(timezone.utc).isoformat(),
            }
            self._dirty = True
            self._revision += 1

    def remove_channel(self, cid):
        with _store_lock:
            self._channels.pop(cid, None)
            self._dirty = True
            self._revision += 1

    def set_folder(self, cid, folder, profile_name=""):
        with _store_lock:
            if cid in self._channels:
                self._channels[cid]["folder"] = folder
                if profile_name:
                    self._channels[cid]["profile_name"] = profile_name
                self._dirty = True
                self._revision += 1

    def rename_profile(self, old_name, new_name):
        with _store_lock:
            renamed = 0
            for meta in self._channels.values():
                if meta.get("profile_name") == old_name:
                    meta["profile_name"] = new_name
                    renamed += 1
            if renamed:
                self._dirty = True
                self._revision += 1
            return renamed

    def count_by_profile(self, profile_name):
        with _store_lock:
            return sum(1 for meta in self._channels.values() if meta.get("profile_name") == profile_name)

    def toggle_active(self, cid):
        with _store_lock:
            if cid not in self._channels:
                return None
            self._channels[cid]["active"] = not bool(self._channels[cid].get("active", True))
            self._dirty = True
            self._revision += 1
            return self._channels[cid]["active"]

    def toggle_process_short(self, cid):
        with _store_lock:
            if cid not in self._channels:
                return None
            self._channels[cid]["process_short"] = not bool(self._channels[cid].get("process_short", True))
            self._dirty = True
            self._revision += 1
            return self._channels[cid]["process_short"]

    def get_meta(self, cid):
        with _store_lock:
            meta = self._channels.get(cid)
            return dict(meta) if meta else None

    def get_active_and_unseen_guard(self, cid, vid):
        with _store_lock:
            meta = self._channels.get(cid)
            if not meta or not meta.get("active", True):
                return False
            seen = meta.setdefault("seen", set())
            if vid in seen:
                return False
            seen.add(vid)
            if len(seen) > SEEN_MAX_PER_CHANNEL:
                for old in list(seen)[:len(seen) - SEEN_MAX_PER_CHANNEL]:
                    seen.discard(old)
            self._dirty = True
            self._revision += 1
            return True

    def mark_seen_only(self, cid, vid):
        with _store_lock:
            meta = self._channels.get(cid)
            if meta:
                meta.setdefault("seen", set()).add(vid)
                self._dirty = True
                self._revision += 1

    def update_meta(self, cid, title="", thumbnail="", channel_url="", meta_attempted=False, **kwargs):
        with _store_lock:
            meta = self._channels.get(cid)
            if meta:
                if title:
                    meta["title"] = title
                if thumbnail:
                    meta["thumbnail"] = thumbnail
                if channel_url:
                    meta["channel_url"] = channel_url
                if meta_attempted:
                    meta["meta_attempted"] = True
                for k, v in kwargs.items():
                    meta[k] = v
                self._dirty = True
                self._revision += 1

    def update_watermark(self, cid, pub_epoch):
        if pub_epoch is None:
            return
        with _store_lock:
            meta = self._channels.get(cid)
            if meta and (meta.get("last_pub_utc") is None or pub_epoch > meta.get("last_pub_utc")):
                meta["last_pub_utc"] = pub_epoch
                self._dirty = True
                self._revision += 1

    def should_reject_by_watermark(self, cid, pub_epoch, slack_sec):
        if pub_epoch is None:
            return False
        with _store_lock:
            meta = self._channels.get(cid)
            cur = meta.get("last_pub_utc") if meta else None
            return cur is not None and pub_epoch < (cur - slack_sec)

    def subscribe_all(self, cb_url):
        for ch in list(self.all_items().keys()):
            if _needs_resubscribe(ch):
                threading.Thread(target=subscribe_websub, args=(ch, cb_url), daemon=True).start()


_predictive_scheduler = None
_predictive_scheduler_thread = None

channels_store = ChannelsStore(CHANNELS_JSON)
channels_store.load()
flask_app = Flask(__name__)


@flask_app.route("/youtube_callback", methods=["GET", "POST"])
@flask_app.route("/youtube_callback/<owner>", methods=["GET", "POST"])
def youtube_callback(owner=None):
    supplied_owner = owner or request.args.get("owner")
    if _callback_owner_token and supplied_owner != _callback_owner_token:
        return "Invalid callback owner", 404
    if request.method == "GET":
        challenge = request.args.get("hub.challenge", "")
        mode = request.args.get("hub.mode", "")
        topic = request.args.get("hub.topic", "")
        if mode == "subscribe" and topic:
            ch_match = re.search(r"channel_id=([^&]+)", topic)
            if ch_match:
                cid = ch_match.group(1)
                lease = request.args.get("hub.lease_seconds", "")
                with _subscription_lock:
                    lease_sec = int(lease) if lease and lease.isdigit() else 0
                    _subscription_status[cid] = {
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                        "lease_seconds": lease_sec,
                        "lease_expires_at": (
                            (datetime.now(timezone.utc) + timedelta(seconds=lease_sec)).isoformat()
                            if lease_sec > 0 else ""
                        ),
                        "mode": mode,
                        "topic": topic,
                        "last_error": "",
                        "next_retry_at": 0,
                        "verification_pending": False,
                    }
                try:
                    _get_video_state_store().subscription_verified(cid, lease_sec)
                except Exception as e:
                    log(f"[WebSub] Không lưu được verification {cid}: {e}")
                log(f"[WebSub] Verification GET: {cid} verified, lease={lease}s")
        return challenge, 200
    global last_callback_post_time
    payload = request.get_data()
    if len(payload) > MAX_CALLBACK_BODY:
        log(f"[WebSub] Body too large: {len(payload)} bytes, rejected")
        return "Body too large", 413
    sig = request.headers.get("X-Hub-Signature-256", "") or request.headers.get("X-Hub-Signature", "")
    if not _verify_websub_signature(payload, sig):
        log(f"[WebSub] Invalid signature, rejected ({len(payload)} bytes)")
        return "Invalid signature", 401
    data = payload.decode("utf-8", errors="ignore")
    last_callback_post_time = datetime.now(timezone.utc).isoformat()
    websub_payload_queue.put((data, last_callback_post_time))
    if _predictive_scheduler:
        try:
            entries = _parse_websub_xml(data)
            for vid, cid, pub in entries:
                _predictive_scheduler.trigger_immediate_poll(cid)
        except Exception:
            pass
    log(f"[WebSub] POST verified bytes={len(data)}")
    return "", 200


@flask_app.route("/youtube_health")
@flask_app.route("/youtube_health/<owner>")
def youtube_health(owner=None):
    if owner is not None and _callback_owner_token and owner != _callback_owner_token:
        return jsonify({"ok": False}), 404
    return jsonify({
        "ok": True,
        "instance_id": _callback_instance_id or "",
        "port": _callback_port,
        "generation": _get_monitor_gen(),
        "monitor_running": _monitor_started,
        "started_at": getattr(_callback_server, "_started_at", ""),
    })


NS_ATOM = "http://www.w3.org/2005/Atom"
NS_YT = "http://www.youtube.com/xml/schemas/2015"


def _parse_websub_xml(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        log(f"[WebSub] XML parse error: {xml_text[:200]}")
        return []
    entries = []
    for entry in root.findall(f"{{{NS_ATOM}}}entry"):
        vid_elem = entry.find(f"{{{NS_YT}}}videoId")
        chan_elem = entry.find(f"{{{NS_YT}}}channelId")
        pub_elem = entry.find(f"{{{NS_ATOM}}}published")
        vid = vid_elem.text.strip() if vid_elem is not None and vid_elem.text else None
        chan = chan_elem.text.strip() if chan_elem is not None and chan_elem.text else None
        pub = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else None
        if vid and chan:
            entries.append((vid, chan, pub))
    return entries


def iso_to_epoch(value):
    try:
        value = value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def _playlist_item_video_id(item):
    return item.get("contentDetails", {}).get("videoId") or item.get("snippet", {}).get("resourceId", {}).get("videoId")


def _playlist_item_published(item):
    return item.get("snippet", {}).get("publishedAt", "")


def _channel_needs_polling_baseline(meta):
    return not meta.get("last_pub_utc") and not meta.get("seen")


def _polling_item_is_at_or_before_watermark(meta, pub_epoch):
    watermark = meta.get("last_pub_utc")
    return pub_epoch is not None and watermark is not None and pub_epoch <= watermark


def _seed_polling_baseline(channel_id, items):
    seeded = 0
    latest_pub_epoch = None
    for item in items:
        vid = _playlist_item_video_id(item)
        if not vid:
            continue
        channels_store.mark_seen_only(channel_id, vid)
        seeded += 1
        published = _playlist_item_published(item)
        pub_epoch = iso_to_epoch(published) if published else None
        if pub_epoch is not None and (latest_pub_epoch is None or pub_epoch > latest_pub_epoch):
            latest_pub_epoch = pub_epoch
    if latest_pub_epoch is not None:
        channels_store.update_watermark(channel_id, latest_pub_epoch)
    return seeded


@dataclass(frozen=True)
class VideoDetectedIntent:
    channel_id: str
    video_id: str
    profile_name: str
    published_iso: str
    detected_iso: str
    source: str = "WEBSUB"
    monitor_generation: int = 0


@dataclass(frozen=True)
class VideoReadyIntent:
    profile_name: str
    final_path: str
    channel_id: str
    youtube_video_id: str
    title: str
    published_iso: str = ""
    detected_iso: str = ""
    source: str = "FAST_PATH"


def _register_detected_video(channel_id, video_id, published_iso, detected_iso, source):
    """Apply the shared WebSub/polling policy and atomically enqueue one video."""
    pub_epoch = iso_to_epoch(published_iso) if published_iso else None
    meta = channels_store.get_meta(channel_id)
    if not meta or not meta.get("active", True):
        log(f"[{source}] Skip {video_id}: channel {channel_id} inactive or missing")
        return False
    if video_id in meta.get("seen", set()):
        return False
    if _is_pending(channel_id, video_id):
        return False
    if pub_epoch is not None and time.time() - pub_epoch > MAX_ACCEPTABLE_AGE_HOURS * 3600:
        channels_store.mark_seen_only(channel_id, video_id)
        log(f"[{source}] Skip {video_id}: older than {MAX_ACCEPTABLE_AGE_HOURS}h")
        return False
    if channels_store.should_reject_by_watermark(
        channel_id, pub_epoch, WATERMARK_SLACK_MINUTES * 60
    ):
        channels_store.mark_seen_only(channel_id, video_id)
        log(f"[{source}] Skip {video_id}: older than watermark")
        return False
    try:
        should_queue, state = _get_video_state_store().register_detection(
            channel_id,
            video_id,
            pub_epoch,
            _monitor_session_id or "legacy",
            _monitor_started_epoch or time.time(),
            source,
            published_iso=published_iso or "",
            detected_iso=detected_iso or "",
        )
    except Exception as e:
        log(f"[{source}] State store lỗi {video_id}: {e}")
        return False
    if not should_queue:
        return False
    if not _try_pending(channel_id, video_id):
        return False
    _get_video_state_store().transition(channel_id, video_id, "QUEUED_DOWNLOAD")
    download_queue.put((channel_id, video_id, published_iso or None, detected_iso))
    channels_store.update_watermark(channel_id, pub_epoch)
    log(f"[{source}] Enqueue {video_id}@{channel_id}")
    scheduler = _predictive_scheduler
    if scheduler and not scheduler.stop_event.is_set():
        try:
            scheduler.record_detection(channel_id, video_id)
        except Exception:
            log("[Scheduler] Không thể ghi nhận trạng thái khung quét cho video đã tiếp nhận.")
    return True


def set_video_detected_callback(callback):
    """Đăng ký callback nhận sự kiện video mới được phát hiện qua WebSub.

    Callback nhận một đối tượng VideoDetectedIntent. Có thể gọi lại nhiều lần,
    callback mới nhất được dùng. Truyền None để hủy đăng ký.
    """
    global _video_detected_callback
    with _video_detected_callback_lock:
        _video_detected_callback = callback


def set_video_ready_callback(callback):
    """Register the application-owned delivery callback for finalized videos."""
    global _video_ready_callback
    with _video_ready_callback_lock:
        _video_ready_callback = callback


def _safe_emit_detection(intent):
    if intent is None:
        return
    with _video_detected_callback_lock:
        callback = _video_detected_callback
    if callback is None:
        return
    try:
        callback(intent)
    except Exception as e:
        log(f"[WebSub] Detection callback lỗi: {e}")


def _safe_emit_video_ready(intent):
    """Return ``(ok, reason)`` without importing the application entry module."""
    with _video_ready_callback_lock:
        callback = _video_ready_callback
    if callback is None:
        return False, "ready_callback_missing"
    try:
        result = callback(intent)
        if isinstance(result, tuple) and len(result) >= 2:
            return bool(result[0]), str(result[1])
        return bool(result), "enqueued" if result else "ready_callback_rejected"
    except Exception as e:
        log(f"[FastPath] Video-ready callback lỗi: {e}")
        return False, "ready_callback_failed"


def websub_processor_worker(run_gen=None):
    log("[WebSub] Processor started")
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log("[WebSub] Generation changed, stopping processor")
            break
        try:
            data, detected_utc_iso = websub_payload_queue.get(timeout=1)
        except queue.Empty:
            continue
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log("[WebSub] Generation changed after dequeue, returning event to queue")
            websub_payload_queue.put((data, detected_utc_iso))
            websub_payload_queue.task_done()
            break
        now_epoch = time.time()
        entries = _parse_websub_xml(data)
        if entries:
            log(f"[WebSub] Processing {len(entries)} entries")
        for vid, chan, published in entries:
            if not chan:
                continue
            if run_gen is not None and _get_monitor_gen() != run_gen:
                break
            pub_epoch = iso_to_epoch(published) if published else None
            meta = channels_store.get_meta(chan)
            if not meta or not meta.get("active", True):
                log(f"[WebSub] Skip {vid}: channel {chan} inactive")
                continue
            seen = meta.get("seen", set())
            if vid in seen:
                continue
            if _is_pending(chan, vid):
                continue
            if not _register_detected_video(chan, vid, published or "", detected_utc_iso, "WEBSUB"):
                continue
            intent = VideoDetectedIntent(
                channel_id=chan,
                video_id=vid,
                profile_name=meta.get("profile_name", "") or "",
                published_iso=published or "",
                detected_iso=detected_utc_iso,
                source="WEBSUB",
                monitor_generation=_get_monitor_gen(),
            )
            _safe_emit_detection(intent)
        channels_store._dirty = True
        try:
            websub_payload_queue.task_done()
        except Exception:
            pass
    log("[WebSub] Processor stopped")


def load_channel_cache():
    try:
        with open(CHANNEL_CACHE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_channel_cache(cache):
    try:
        with open(CHANNEL_CACHE_JSON, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"[Cache] Save lỗi: {e}")


def get_channel_id_from_link(link, youtube):
    cache = load_channel_cache()
    link = str(link or "").strip()
    if link in cache:
        return cache[link]
    if link.startswith("@"):
        link = f"https://www.youtube.com/{link}"
    elif not link.startswith("http") and not link.startswith("youtube.com") and not link.startswith("youtu.be"):
        link = f"https://www.youtube.com/@{link}"
    channel_match = re.search(r"youtube\.com/channel/([^\s/?#]+)", link)
    if channel_match:
        cid = channel_match.group(1)
        response = youtube.channels().list(part="contentDetails", id=cid).execute()
        if response.get("items"):
            info = {"id": cid, "playlistId": response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]}
            cache[link] = info
            save_channel_cache(cache)
            return info
    user_match = re.search(r"youtube\.com/(@|c/|user/)([^\s/?#]+)", link)
    if user_match:
        username = urllib.parse.unquote(user_match.group(2))
        response = youtube.channels().list(part="id,contentDetails", forHandle=f"@{username}").execute()
        if not response.get("items"):
            response = youtube.search().list(part="snippet", q=username, type="channel", maxResults=1).execute()
            if response.get("items"):
                cid = response["items"][0]["snippet"]["channelId"]
                response = youtube.channels().list(part="id,contentDetails", id=cid).execute()
        if response.get("items"):
            item = response["items"][0]
            info = {"id": item["id"], "playlistId": item["contentDetails"]["relatedPlaylists"]["uploads"]}
            cache[link] = info
            save_channel_cache(cache)
            return info
    short_match = re.search(r"youtu\.be/([^\s/?#]+)", link)
    if short_match:
        return get_channel_from_video_id(short_match.group(1), youtube)
    watch_match = re.search(r"[?&]v=([^&\s]+)", link)
    if watch_match:
        return get_channel_from_video_id(watch_match.group(1), youtube)
    return None


def get_channel_from_video_id(video_id, youtube):
    response = youtube.videos().list(part="snippet", id=video_id).execute()
    if not response.get("items"):
        return None
    cid = response["items"][0]["snippet"]["channelId"]
    details = youtube.channels().list(part="contentDetails", id=cid).execute()
    if not details.get("items"):
        return None
    return {"id": cid, "playlistId": details["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]}


def sanitize_filename(name, max_length=150):
    name = (name or "video").replace("\n", " ").replace("\r", " ").strip()
    name = name.translate(str.maketrans({ch: " " for ch in r'<>:"/\|?*'}))
    name = re.sub(r"\s+", " ", "".join(ch for ch in name if ord(ch) >= 32)).strip().rstrip(" .")
    return (name[:max_length].rstrip(" .") or "video")


def _choose_final_path_unlocked(out_folder, title, video_id, ext):
    base = sanitize_filename(title)
    for candidate in [Path(out_folder) / f"{base}{ext}", Path(out_folder) / f"{base} - {video_id[:8]}{ext}"]:
        if not candidate.exists():
            return str(candidate)
    for i in range(2, 1000):
        candidate = Path(out_folder) / f"{base} ({i}){ext}"
        if not candidate.exists():
            return str(candidate)
    return str(Path(out_folder) / f"{base} - {uuid.uuid4().hex[:6]}{ext}")


def build_final_path(out_folder, title, video_id, ext):
    with _finalize_lock:
        return _choose_final_path_unlocked(out_folder, title, video_id, ext)


def _finalize_workspace(out_folder):
    workspace = _staging_dir(out_folder) / f"finalize-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _commit_final_file(staged_path, out_folder, title, video_id, ext):
    with _finalize_lock:
        final_path = _choose_final_path_unlocked(out_folder, title, video_id, ext)
        os.replace(staged_path, final_path)
        return final_path


def _detect_container(path):
    out = ffmpeg_helper.run_ffprobe([
        "-v", "error",
        "-show_entries", "format=format_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return (out or "").strip().lower()


def _detect_video_codec(path):
    out = ffmpeg_helper.run_ffprobe([
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return (out or "").strip().lower()


def _detect_audio_codec(path):
    out = ffmpeg_helper.run_ffprobe([
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return (out or "").strip().lower()


MP4_FORMAT_NAMES = {"mp4", "mov,mp4,m4a,3gp,3g2,mj2", "mov"}
COMPATIBLE_VIDEO_CODECS = {"h264", "avc1"}
COMPATIBLE_AUDIO_CODECS = {"aac", "mp4a"}
MUST_TRANSCODE_VIDEO = {"vp9", "vp8", "av1"}
MUST_TRANSCODE_AUDIO = {"opus", "vorbis"}


def _is_mp4_container(container):
    if not container:
        return False
    for name in MP4_FORMAT_NAMES:
        if container == name:
            return True
    return False


def _probe_media(path):
    out = ffmpeg_helper.run_ffprobe([
        "-v", "error",
        "-show_entries", "format=format_name,duration:stream=codec_name,codec_type",
        "-of", "json",
        str(path),
    ])
    if not out:
        return None
    try:
        data = json.loads(out)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    container = (fmt.get("format_name") or "").strip().lower()
    vcodec = ""
    acodec = ""
    duration = fmt.get("duration")
    for s in streams:
        st = (s.get("codec_type") or "").strip()
        if st == "video" and not vcodec:
            vcodec = (s.get("codec_name") or "").strip().lower()
        elif st == "audio" and not acodec:
            acodec = (s.get("codec_name") or "").strip().lower()
    return {
        "container": container,
        "vcodec": vcodec,
        "acodec": acodec,
        "duration": float(duration) if duration else None,
    }


def _finalize_video(input_path, out_folder, title, video_id):
    input_path = str(input_path)
    workspace = _finalize_workspace(out_folder)
    probe = _probe_media(input_path)
    if not probe:
        shutil.rmtree(workspace, ignore_errors=True)
        raise RuntimeError(f"Không probe được media {video_id}")

    container = probe["container"]
    vcodec = probe["vcodec"]
    acodec = probe["acodec"]
    duration = probe["duration"]

    log(f"[Finalize] {video_id}: container={container} vcodec={vcodec} acodec={acodec} dur={duration}")

    needs_transcode_v = vcodec in MUST_TRANSCODE_VIDEO
    needs_transcode_a = bool(acodec and acodec in MUST_TRANSCODE_AUDIO)
    is_compat_v = vcodec in COMPATIBLE_VIDEO_CODECS
    is_compat_a = not acodec or acodec in COMPATIBLE_AUDIO_CODECS
    is_mp4 = _is_mp4_container(container)

    try:
        if is_mp4 and is_compat_v and is_compat_a and not needs_transcode_v and not needs_transcode_a:
            staged_path = str(workspace / "direct.mp4")
            shutil.copy2(input_path, staged_path)
            _probe_output(staged_path, video_id)
            final_path = _commit_final_file(staged_path, out_folder, title, video_id, ".mp4")
            os.remove(input_path)
            return final_path

        if is_compat_v and is_compat_a and not needs_transcode_v and not needs_transcode_a:
            remux_path = str(workspace / "remux.mp4")
            p, err = ffmpeg_helper.run_ffmpeg([
                "-y", "-i", str(input_path),
                "-c:v", "copy", "-c:a", "copy",
                "-movflags", "+faststart",
                remux_path,
            ])
            if p and p.returncode == 0 and Path(remux_path).exists():
                _probe_output(remux_path, video_id)
                final_path = _commit_final_file(remux_path, out_folder, title, video_id, ".mp4")
                os.remove(input_path)
                return final_path
            log(f"[Finalize] Remux thất bại {video_id}: {err[:200]}")

        log(f"[Finalize] Transcode {video_id}: vcodec={vcodec} acodec={acodec}")

        out_path = str(workspace / "transcode.mp4")
        encoder = _pick_video_encoder()
        has_aud = bool(acodec)
        cmd = ["-y", "-i", str(input_path)]
        if not has_aud:
            cmd += ["-an"]
        cmd += encoder + ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        if has_aud:
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += [out_path]
        p, err = ffmpeg_helper.run_ffmpeg(cmd)
        if p and p.returncode == 0 and Path(out_path).exists():
            _probe_output(out_path, video_id)
            final_path = _commit_final_file(out_path, out_folder, title, video_id, ".mp4")
            os.remove(input_path)
            return final_path

        if encoder[1] != "libx264":
            ffmpeg_helper._encoder_cache = "libx264"
            log(f"[Finalize] GPU transcode thất bại {video_id}, fallback CPU: {err}")
            cmd2 = ["-y", "-i", str(input_path)]
            if not has_aud:
                cmd2 += ["-an"]
            cmd2 += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            if has_aud:
                cmd2 += ["-c:a", "aac", "-b:a", "128k"]
            cmd2 += [out_path]
            p2, err2 = ffmpeg_helper.run_ffmpeg(cmd2)
            if p2 and p2.returncode == 0 and Path(out_path).exists():
                _probe_output(out_path, video_id)
                final_path = _commit_final_file(out_path, out_folder, title, video_id, ".mp4")
                os.remove(input_path)
                return final_path
            log(f"[Finalize] CPU fallback also failed {video_id}: {err2[:200]}")

        log(f"[Finalize] Transcode thất bại {video_id}: {err[:200]}")
        raise RuntimeError(f"Không thể finalize video {video_id}: transcode thất bại")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _probe_output(path, video_id):
    probe = _probe_media(path)
    if not probe:
        raise RuntimeError(f"Output probe failed {video_id}")
    if not _is_mp4_container(probe["container"]):
        raise RuntimeError(f"Output không phải MP4 {video_id}: {probe['container']}")
    if probe["vcodec"] not in COMPATIBLE_VIDEO_CODECS:
        raise RuntimeError(f"Output codec {probe['vcodec']} không phải H.264 {video_id}")
    if probe["acodec"] and probe["acodec"] not in COMPATIBLE_AUDIO_CODECS:
        raise RuntimeError(f"Output audio {probe['acodec']} không phải AAC {video_id}")
    if probe.get("duration") is None or probe["duration"] <= 0:
        raise RuntimeError(f"Output duration không hợp lệ {video_id}: {probe.get('duration')}")
    log(f"[Finalize] Output verified {video_id}: {probe['container']} v={probe['vcodec']} a={probe['acodec']} dur={probe['duration']}")


def append_csv_log(channel_id, video_id, published_iso, detected_iso, saved_path):
    header_needed = not CSV_LOG.exists()
    with open(CSV_LOG, "a", encoding="utf-8") as f:
        if header_needed:
            f.write("channel_id,video_id,published_utc,detected_utc,saved_path\n")
        f.write(f'{channel_id},{video_id},{published_iso or ""},{detected_iso or ""},"{saved_path}"\n')


def probe_duration_seconds(path):
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(p.stdout.strip()) if p.returncode == 0 else None
    except Exception:
        return None


def has_audio_stream(path):
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return p.returncode == 0 and bool(p.stdout.strip())
    except Exception:
        return False


def _build_atempo_chain(playback_factor):
    tempo = 1.0 / max(1e-9, playback_factor)
    chain = []
    while tempo < 0.5:
        chain.append(0.5)
        tempo /= 0.5
    while tempo > 2.0:
        chain.append(2.0)
        tempo /= 2.0
    chain.append(max(0.5, min(2.0, tempo)))
    return chain


def _pick_video_encoder():
    enc = ffmpeg_helper.detect_gpu_encoder()
    if enc == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    return ["-c:v", enc, "-preset", "p7" if enc == "h264_nvenc" else "medium" if enc == "h264_qsv" else "balanced" if enc == "h264_amf" else "medium"]


def slowdown_to_min_duration_in_temp(input_path, target_seconds):
    dur = ffmpeg_helper.probe_duration(input_path) or 0
    if dur >= target_seconds - 0.01 or dur <= 0:
        return input_path, []
    factor = max(1.0, target_seconds / dur)
    out_path = str(TEMP_DIR / f"{uuid.uuid4().hex}.slow.mp4")
    has_aud = ffmpeg_helper.has_audio(input_path)
    if has_aud:
        atempo = ",".join([f"atempo={t:.5g}" for t in _build_atempo_chain(factor)])
        filters = ["-filter_complex", f"[0:v]setpts={factor:.6f}*PTS,format=yuv420p[v];[0:a]{atempo}[a]", "-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    else:
        filters = ["-filter:v", f"setpts={factor:.6f}*PTS,format=yuv420p", "-an"]
    encoder = _pick_video_encoder()
    cmd = ["ffmpeg", "-y", "-i", input_path] + filters + ["-t", str(int(target_seconds))] + encoder + ["-threads", "2", out_path]
    try:
        _encode_sem.acquire()
        p, err = ffmpeg_helper.run_ffmpeg(cmd[1:])
        if p and p.returncode == 0 and os.path.exists(out_path):
            out_dur = ffmpeg_helper.probe_duration(out_path)
            if out_dur is None or out_dur < target_seconds - 1.0:
                log(f"[FFmpeg] slow-mo output không hợp lệ (dur={out_dur}) -> giữ file gốc")
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            else:
                try: os.remove(input_path)
                except Exception: pass
                return out_path, [out_path]
        if p is None:
            log(f"[FFmpeg] slow-mo lỗi (binary): {err}")
        else:
            log(f"[FFmpeg] slow-mo lỗi: {err}")
        if encoder[1] != "libx264":
            ffmpeg_helper._encoder_cache = "libx264"
            enc = "libx264"
            cmd2 = ["ffmpeg", "-y", "-i", input_path] + filters + ["-t", str(int(target_seconds)), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2", out_path]
            p2, err2 = ffmpeg_helper.run_ffmpeg(cmd2[1:])
            if p2 and p2.returncode == 0 and os.path.exists(out_path):
                out_dur = ffmpeg_helper.probe_duration(out_path)
                if out_dur is None or out_dur < target_seconds - 1.0:
                    log(f"[FFmpeg] CPU fallback output không hợp lệ (dur={out_dur}) -> giữ file gốc")
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass
                else:
                    try: os.remove(input_path)
                    except Exception: pass
                    return out_path, [out_path]
            log(f"[FFmpeg] CPU fallback also failed: {err2}")
    finally:
        try: _encode_sem.release()
        except Exception: pass
    return input_path, []


def loop_to_min_duration_in_temp(input_path, target_seconds):
    dur = ffmpeg_helper.probe_duration(input_path) or 0
    if dur >= target_seconds - 0.01 or dur <= 0:
        return input_path, []
    out_path = str(TEMP_DIR / f"{uuid.uuid4().hex}.loop.mp4")
    list_file = str(TEMP_DIR / f"{uuid.uuid4().hex}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for _ in range(math.ceil(target_seconds / dur)):
            f.write(f"file '{input_path}'\n")
    try:
        _encode_sem.acquire()
        p, err = ffmpeg_helper.run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-t", str(int(target_seconds)), out_path])
        if p and p.returncode == 0 and os.path.exists(out_path):
            try: os.remove(input_path)
            except Exception: pass
            return out_path, [out_path, list_file]
        log(f"[FFmpeg] loop lỗi: {err[:200]}")
    finally:
        try: _encode_sem.release()
        except Exception: pass
    return input_path, [list_file]


def _extract_video_id(value):
    value = str(value or "").strip()
    patterns = [r"youtu\.be/([^\s/?#]+)", r"[?&]v=([^&\s]+)", r"youtube\.com/shorts/([^\s/?#]+)"]
    for pattern in patterns:
        m = re.search(pattern, value)
        if m:
            return m.group(1)
    return value if re.match(r"^[A-Za-z0-9_-]{8,}$", value) else ""


def _normalize_channel_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if _extract_video_id(value):
        return value
    if value.startswith("@"):
        return f"https://www.youtube.com/{value}/videos"
    if not value.startswith("http"):
        return f"https://www.youtube.com/@{value}/videos"
    clean = value.rstrip("/")
    if any(token in clean for token in ("/videos", "/shorts/", "watch?v=", "youtu.be/")):
        return clean
    return clean + "/videos"


def _fetch_video_duration(video_id, retries=2):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extractor_args": {"youtube": {"skip": ["hls"]}},
    }
    cookies = _resolve_cookies_file()
    if cookies:
        opts["cookiefile"] = cookies
        opts["cookies"] = cookies
    last_error = None
    for attempt in range(max(1, int(retries or 1))):
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"https://youtu.be/{video_id}", download=False)
            return float((info or {}).get("duration") or 0)
        except Exception as e:
            last_error = e
            if attempt + 1 < max(1, int(retries or 1)):
                time.sleep(0.5)
    raise last_error


def find_latest_video(channel_link, max_seconds=0, scan_limit=BATCH_SCAN_LIMIT):
    url = _normalize_channel_url(channel_link)
    if not url:
        return None, "Link kênh trống."
    video_id = _extract_video_id(url)
    if video_id:
        if max_seconds > 0:
            try:
                duration = _fetch_video_duration(video_id)
                if duration > max_seconds:
                    return None, f"Video URL dài {_format_duration(duration)} > giới hạn {_format_duration(max_seconds)}"
            except Exception as e:
                return None, f"Không kiểm tra được độ dài video: {e}"
        return video_id, "Video URL"
    max_seconds = max(0, int(max_seconds or 0))
    playlistend = max(1, int(scan_limit or BATCH_SCAN_LIMIT)) if max_seconds > 0 else 1
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": playlistend,
        "noplaylist": False,
        "extractor_args": {"youtube": {"skip": ["hls"]}},
    }
    cookies = _resolve_cookies_file()
    if cookies:
        opts["cookiefile"] = cookies
        opts["cookies"] = cookies
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = list((info or {}).get("entries") or [])
        if not entries:
            return None, "Không tìm thấy video trên kênh."
        for entry in entries:
            entry = entry or {}
            video_id = entry.get("id") or _extract_video_id(entry.get("url", ""))
            if not video_id:
                continue
            title = entry.get("title") or video_id
            if max_seconds <= 0:
                return video_id, title
            duration = entry.get("duration")
            if not duration:
                try:
                    duration = _fetch_video_duration(video_id)
                except Exception as e:
                    log(f"[Batch] Bỏ qua {video_id}: không xác minh được độ dài sau 2 lần ({e})")
                    continue
            try:
                duration = float(duration or 0)
            except Exception:
                duration = 0
            if duration > 0 and duration <= max_seconds:
                log(f"[Batch] Chọn {video_id}: {title} ({_format_duration(duration)})")
                return video_id, f"{title} ({_format_duration(duration)})"
            if duration > max_seconds:
                log(f"[Batch] Bỏ qua {video_id}: {_format_duration(duration)} > giới hạn {_format_duration(max_seconds)}")
        return None, f"Không tìm thấy video <= {_format_duration(max_seconds)} trong {playlistend} video gần nhất."
    except Exception as e:
        return None, str(e)


def _discover_channel_latest(link, profile_name="", max_seconds=0):
    video_id, title_or_error = find_latest_video(link, max_seconds=max_seconds)
    return link, video_id, title_or_error


def batch_download_latest(channel_links, target_folder, profile_name="", progress_callback=None, stop_event=None):
    links = [str(link or "").strip() for link in (channel_links or []) if str(link or "").strip()]
    if not links:
        return False, "Danh sách kênh trống."
    if not target_folder:
        return False, "Chưa chọn thư mục đích."
    Path(target_folder).mkdir(parents=True, exist_ok=True)
    if get_config().get("proxy_rotation", True):
        _ensure_proxy_pool_loaded()
    seen_video_ids = set()
    ok_count = 0
    total = len(links)

    def emit(kind, message):
        log(f"[Batch] {message}")
        if progress_callback:
            try:
                progress_callback(kind, message)
            except Exception:
                pass

    emit("info", f"Bắt đầu batch: {total} kênh")
    max_seconds = get_max_video_seconds()
    discovered = {}
    discovery_pool = []
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, total)) as pool:
        futures = {}
        for link in links:
            f = pool.submit(_discover_channel_latest, link, profile_name, max_seconds)
            futures[f] = link
        for future in concurrent.futures.as_completed(futures):
            if stop_event and stop_event.is_set():
                for f in futures:
                    f.cancel()
                break
            link = futures[future]
            try:
                _, video_id, title_or_error = future.result()
                if video_id:
                    discovered[link] = (video_id, title_or_error)
                    emit("info", f"{link}: {video_id}")
                else:
                    emit("error", f"{link}: {title_or_error}")
                    append_activity("batch_find", video_name=link, video_url=link, profile=profile_name, status="skipped", detail=title_or_error)
            except Exception as e:
                emit("error", f"{link}: {e}")

    if stop_event and stop_event.is_set():
        emit("warn", "Đã dừng batch.")
        return False, "Đã dừng."

    for link, (video_id, title_or_error) in discovered.items():
        if stop_event and stop_event.is_set():
            break
        if video_id in seen_video_ids:
            emit("warn", f"Bỏ qua trùng video: {video_id}")
            continue
        seen_video_ids.add(video_id)
        emit("info", f"Đang tải {video_id}: {title_or_error}")
        ok = download_one(
            f"BATCH_{video_id}",
            video_id,
            target_folder=target_folder,
            process_short=True,
            activity_profile=profile_name,
        )
        if ok:
            ok_count += 1
            emit("success", f"OK {video_id}")
        else:
            emit("error", f"FAIL {video_id}")
    message = f"Xong: {ok_count}/{total}"
    emit("done", message)
    return ok_count == total, message


def get_max_video_seconds():
    try:
        return max(0, int(get_config().get("max_video_minutes", 0))) * 60
    except Exception:
        return 0


def _run_ytdlp_download(video_id, url, opts):
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        limit_sec = get_max_video_seconds()
        duration = float(info.get("duration") or 0)
        if limit_sec > 0 and duration > limit_sec:
            reason = f"duration {_format_duration(duration)} > giới hạn {_format_duration(limit_sec)}"
            log(f"[DL] Bỏ qua {video_id}: {reason}")
            return None, None, reason
        fmt_id = info.get("format_id", "?")
        fmt_h = info.get("height", "?")
        fmt_ext = info.get("ext", "?")
        fmt_vcodec = info.get("vcodec", "?")
        requested = info.get("requested_formats")
        if requested:
            log(f"[DL] Format {video_id}: video={requested[0].get('format_id','?')} {requested[0].get('height','?')}p {requested[0].get('ext','?')} + audio={requested[1].get('format_id','?')} {requested[1].get('ext','?')}")
        else:
            log(f"[DL] Format {video_id}: {fmt_id} {fmt_h}p {fmt_ext} {fmt_vcodec}")
        ydl.process_ie_result(info, download=True)
        downloaded_path = info.get("requested_downloads", [{}])[0].get("filepath") if info.get("requested_downloads") else ydl.prepare_filename(info)
    return info, downloaded_path, ""


def _claim_download(video_id):
    with _active_downloads_lock:
        if video_id in _active_downloads:
            return False
        _active_downloads[video_id] = threading.current_thread().ident
        return True


def _release_download(video_id):
    with _active_downloads_lock:
        _active_downloads.pop(video_id, None)


def _staging_dir(target_folder):
    # Keep staging inside the configured destination. Using its parent can escape
    # the writable profile folder (for example C:\\Users\\...\\AppData\\Local).
    p = Path(target_folder) / ".youtube_tmp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cleanup_temp_dl(older_than_seconds=86400):
    """Remove orphaned FFmpeg/downloaad temp artifacts (slow/loop/concat/paths).
    Only files older than the threshold are swept so in-flight jobs are untouched."""
    if not TEMP_DIR.exists():
        return 0
    cutoff = time.time() - older_than_seconds
    removed = 0
    for p in TEMP_DIR.iterdir():
        if not p.is_file():
            continue
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        log(f"[Cleanup] Đã dọn {removed} file tạm cũ trong {TEMP_DIR.name}")
    return removed


def apply_short_processing(downloaded_path, duration, process_short, log_fn=log):
    """Apply the YouTube Short duration rules to a downloaded video.

    Rules:
    * ``process_short`` off -> video is kept unchanged.
    * duration ``< 40s``   -> kept unchanged.
    * duration in ``[40s, 60s]`` -> slowed down to ``SHORT_TARGET_DURATION``.
    * duration ``> 60s``  -> kept unchanged.
    * Unknown duration (``<= 0`` or in the slow window) is re-probed from the
      downloaded file before deciding.

    Returns ``(processed_path, created_paths)``. Raises if the slowdown helper
    fails; the original ``downloaded_path`` is never modified by this function.
    """
    dur = float(duration or 0)
    processed_path, created_paths = downloaded_path, []

    if process_short:
        if dur <= 0 or (SHORT_SLOW_MIN_DURATION <= dur <= SHORT_SLOW_MAX_DURATION):
            actual_dur = ffmpeg_helper.probe_duration(downloaded_path)
            if actual_dur is not None and actual_dur > 0:
                dur = actual_dur

        if SHORT_SLOW_MIN_DURATION <= dur <= SHORT_SLOW_MAX_DURATION:
            log_fn(f"[Short] Video dài {dur:.1f}s (trong khoảng 40s-60s) -> Làm chậm đạt {SHORT_TARGET_DURATION}s")
            processed_path, created_paths = slowdown_to_min_duration_in_temp(downloaded_path, SHORT_TARGET_DURATION)
        elif dur < SHORT_SLOW_MIN_DURATION:
            log_fn(f"[Short] Video dài {dur:.1f}s (< 40s) -> Giữ nguyên thời lượng")
        else:
            log_fn(f"[Short] Video dài {dur:.1f}s (> 60s) -> Giữ nguyên thời lượng")
    else:
        log_fn(f"[Short] Kênh tắt tính năng Short -> Giữ nguyên thời lượng video ({dur:.1f}s)")

    return processed_path, created_paths


def _download_one_result(channel_id, video_id, published_iso=None, detected_iso=None, target_folder=None, process_short=None, explicit_proxy=None, activity_profile=None):
    """Execute the direct-first yt-dlp attempt chain for one video.

    Returns a :class:`DownloadOutcome`. The first attempt is always direct with
    ``proxy=""`` (no inherited environment proxy); a profile proxy is only used
    as a last-resort attempt when ``youtube_proxy_fallback`` is enabled.
    """
    global downloaded_today, downloaded_today_date
    t_start = time.perf_counter()
    if not _claim_download(video_id):
        log(f"[DL] Bỏ qua {video_id}: đang tải")
        return DownloadOutcome(ok=False, retryable=False, permanent=False, failure_class="busy", detail="đang tải")
    _permanent = False
    try:
        meta = channels_store.get_meta(channel_id) or {}
        out_folder = target_folder or meta.get("folder") or str(DOWNLOADS_DIR / channel_id)
        Path(out_folder).mkdir(parents=True, exist_ok=True)
        process_short = meta.get("process_short", True) if process_short is None else bool(process_short)
        url = f"https://youtu.be/{video_id}"
        profile_name = meta.get("profile_name", "")
        activity_profile = activity_profile or profile_name or channel_id
        staging = _staging_dir(out_folder)
        dl_uuid = uuid.uuid4().hex[:8]
        dl_staging = staging / f"{video_id}-{dl_uuid}"
        dl_staging.mkdir(parents=True, exist_ok=True)

        cfg = get_config()
        cf = max(1, int(cfg.get("concurrent_fragments", 8)))
        attempts = _build_attempt_plan(profile_name, explicit_proxy)
        multi = len(attempts) > 1
        quality = get_video_quality()
        fast_fmt, _compat_fmt = _quality_format_selectors(quality)
        base_opts = {
            "format": fast_fmt,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 1 if multi else 3,
            "fragment_retries": 1 if multi else 3,
            "socket_timeout": 12,
            "nocheckcertificate": True,
            "windowsfilenames": True,
            "concurrent_fragment_downloads": cf,
            "http_chunk_size": 10485760,
            "buffersize": 16384,
            "postprocessor_args": {"Merger": ["-movflags", "+faststart"]},
            "cachedir": str(staging / ".ydl_cache"),
            "extractor_args": {"youtube": {"player_client": ["ios", "android", "web"], "skip": ["hls"]}},
            "check_formats": False,
            "merge_output_format": "mp4",
        }
        Path(base_opts["cachedir"]).mkdir(exist_ok=True)

        t_meta = time.perf_counter()
        log(f"[DL] Bắt đầu tải {video_id} ({len(attempts)} attempt, first=direct)")

        info = None
        downloaded_path = None
        skip_reason = ""
        last_error = None
        last_cls = FAILURE_RETRY
        attempts_used = 0
        wait_s = 0.0
        dl_s = 0.0
        try:
            with _download_sem:
                wait_s = time.perf_counter() - t_meta
                t_dl_start = time.perf_counter()
                idx = 0
                while idx < len(attempts):
                    attempt = attempts[idx]
                    attempt_dir = dl_staging / f"attempt-{idx + 1:02d}"
                    attempt_dir.mkdir(parents=True, exist_ok=True)
                    opts = _build_ytdlp_opts(base_opts, attempt, attempt_dir)
                    label = f"route={attempt.route} cookie={int(attempt.use_cookies)} format={attempt.name}"
                    log(f"[DL][{video_id}][{idx + 1}/{len(attempts)}] {label}")
                    try:
                        info, downloaded_path, skip_reason = _run_ytdlp_download(video_id, url, opts)
                        attempts_used = idx + 1
                        log(f"[DL][{video_id}][{idx + 1}/{len(attempts)}] OK {label}")
                        break
                    except Exception as e:
                        last_error = e
                        attempts_used = idx + 1
                        last_cls = _classify_failure(e)
                        log(f"[DL][{video_id}][{idx + 1}/{len(attempts)}] fail class={last_cls} ({str(e)[:200]})")
                        shutil.rmtree(attempt_dir, ignore_errors=True)
                        if last_cls == FAILURE_PERMANENT:
                            _permanent = True
                            break
                        if skip_reason:
                            break
                        nxt = _select_next_attempt(attempts, idx, last_cls)
                        if nxt is None:
                            break
                        idx = nxt
                dl_s = time.perf_counter() - t_dl_start
        except Exception as e:
            last_error = e
            last_cls = _classify_failure(e)
            log(f"[DL] Lỗi ngoài ý muốn {video_id}: {e}")
        if info is None:
            if skip_reason:
                if "duration" in (skip_reason or "").lower() or "limit" in (skip_reason or "").lower():
                    _permanent = True
                append_activity("youtube_download", video_name=video_id, video_url=url, profile=activity_profile, status="skipped", detail=skip_reason or "skipped")
                return DownloadOutcome(ok=False, retryable=False, permanent=_permanent, failure_class="skipped", attempts_used=attempts_used, detail=skip_reason)
            if last_error is None:
                last_error = Exception("Không lấy được thông tin video")
            log(f"[DL] Tải lỗi {video_id}: {last_error}")
            retryable = last_cls not in (FAILURE_PERMANENT, FAILURE_AUTH_REQUIRED)
            if last_cls in (FAILURE_YOUTUBE_BLOCK,):
                log("[DL] YouTube chặn. Hãy bật cookie fallback hoặc login browser để nạp cookies.txt.")
            if last_cls == FAILURE_AUTH_REQUIRED:
                log("[DL] Video yêu cầu đăng nhập. Hãy cấu hình cookies.txt từ browser login YouTube.")
            append_activity("youtube_download", video_name=video_id, video_url=url, profile=activity_profile, status="fail", detail=str(last_error)[:500])
            if last_cls == FAILURE_PERMANENT:
                _permanent = True
            if last_cls == FAILURE_AUTH_REQUIRED:
                # Not automatically retried, but not burned as seen either: if the
                # user later adds valid cookies the next poll can re-attempt it.
                _remove_pending(channel_id, video_id)
                _clear_retry(channel_id, video_id)
            return DownloadOutcome(ok=False, retryable=retryable, permanent=_permanent, failure_class=last_cls, attempts_used=attempts_used, detail=str(last_error)[:500])
        if not downloaded_path or not os.path.exists(downloaded_path):
            candidates = list(dl_staging.rglob("*.mp4"))
            downloaded_path = str(candidates[0]) if candidates else ""
        if not downloaded_path or not os.path.exists(downloaded_path):
            log(f"[DL] Không tìm thấy file tải về cho {video_id}")
            append_activity("youtube_download", video_name=info.get("title") or video_id, video_url=url, profile=activity_profile, status="fail", detail="Không tìm thấy file tải về")
            _permanent = True
            return DownloadOutcome(ok=False, retryable=False, permanent=True, failure_class="no_file", attempts_used=attempts_used, detail="Không tìm thấy file tải về")

        dur = float(info.get("duration") or 0)
        t_process_start = time.perf_counter()
        processed_path, created_paths = apply_short_processing(downloaded_path, dur, process_short)
        t_process_end = time.perf_counter()
        process_s = t_process_end - t_process_start

        t_move = time.perf_counter()
        try:
            final_path = _finalize_video(processed_path, out_folder, info.get("title") or video_id, video_id)
        except Exception as e:
            log(f"[DL] Finalize lỗi {video_id}: {e}")
            try:
                if processed_path and str(processed_path).startswith(str(TEMP_DIR)):
                    if os.path.exists(processed_path):
                        os.remove(processed_path)
            except Exception:
                pass
            append_activity("youtube_download", video_name=info.get("title") or video_id, video_url=url, profile=activity_profile, status="fail", detail=str(e)[:500])
            return DownloadOutcome(ok=False, retryable=True, permanent=False, failure_class=FAILURE_RETRY, attempts_used=attempts_used, detail=str(e)[:500])
        t_move_end = time.perf_counter()
        move_s = t_move_end - t_move

        for path in created_paths:
            if path != final_path:
                try:
                    if os.path.exists(path): os.remove(path)
                except Exception:
                    pass

        total_s = time.perf_counter() - t_start
        size_mb = 0
        try: size_mb = os.path.getsize(final_path) / (1024 * 1024)
        except Exception: pass
        speed = f"{size_mb / max(0.1, dl_s):.1f}MB/s" if dl_s > 0 else "?"
        meta_s = t_meta - t_start
        log(f"[DL] {video_id} metadata={meta_s:.1f}s wait={wait_s:.1f}s download={dl_s:.1f}s process={process_s:.1f}s move={move_s:.2f}s total={total_s:.1f}s speed={speed}")

        append_csv_log(channel_id, video_id, published_iso, detected_iso or datetime.now(timezone.utc).isoformat(), final_path)
        title = info.get("title") or video_id
        append_activity("youtube_download", video_name=title, video_url=url, profile=activity_profile, status="success", detail=f"channel={channel_id}", file_path=final_path)
        remember_download(final_path, video_id=video_id, title=title, channel_id=channel_id, profile=activity_profile)
        today = datetime.now().strftime("%Y-%m-%d")
        if downloaded_today_date != today:
            downloaded_today_date = today
            downloaded_today = 0
        downloaded_today += 1
        log(f"[DL] Đã lưu: {final_path}")
        delivery_state = "DOWNLOADED"
        delivery_reason = "download_only"
        # Dependency inversion: the monitor must never import the application entry
        # module. Under `python main.py`, doing so executes main.py again as module
        # `main` and creates a second Tk root in the same process.
        if profile_name:
            intent = VideoReadyIntent(
                profile_name=profile_name,
                final_path=str(final_path),
                channel_id=channel_id,
                youtube_video_id=video_id,
                title=title,
                published_iso=published_iso or "",
                detected_iso=detected_iso or "",
            )
            enqueued, delivery_reason = _safe_emit_video_ready(intent)
            delivery_state = "ENQUEUED_UPLOAD" if enqueued else "DELIVERY_WAITING"
            if not enqueued:
                log(f"[FastPath] {video_id} chờ delivery reconciliation: {delivery_reason}")
                try:
                    append_activity(
                        "youtube_download", video_name=title, video_url=url,
                        profile=activity_profile, status="warn",
                        detail=f"fastpath_delivery_waiting: {delivery_reason}",
                    )
                except Exception:
                    pass
        channels_store.mark_seen_only(channel_id, video_id)
        try:
            _get_video_state_store().transition(
                channel_id, video_id, delivery_state, final_path=str(final_path),
                last_error_class=None if delivery_state != "DELIVERY_WAITING" else delivery_reason,
                last_error_detail=None if delivery_state != "DELIVERY_WAITING" else delivery_reason,
            )
        except Exception as e:
            log(f"[DL] Không lưu được trạng thái hoàn tất {video_id}: {e}")
        _remove_pending(channel_id, video_id)
        _clear_retry(channel_id, video_id)
        return DownloadOutcome(ok=True, retryable=False, permanent=False, failure_class="", attempts_used=attempts_used, final_path=final_path)
    finally:
        _release_download(video_id)
        if _permanent:
            channels_store.mark_seen_only(channel_id, video_id)
            try:
                _get_video_state_store().transition(channel_id, video_id, "SKIPPED_PERMANENT")
            except Exception:
                pass
            _remove_pending(channel_id, video_id)
            _clear_retry(channel_id, video_id)
        try:
            if 'dl_staging' in locals() and Path(dl_staging).is_dir():
                shutil.rmtree(dl_staging, ignore_errors=True)
        except Exception:
            pass


def download_one(channel_id, video_id, published_iso=None, detected_iso=None, target_folder=None, process_short=None, proxy=None, activity_profile=None):
    """Boolean wrapper kept for existing callers/tests."""
    return _download_one_result(channel_id, video_id, published_iso=published_iso, detected_iso=detected_iso, target_folder=target_folder, process_short=process_short, explicit_proxy=proxy, activity_profile=activity_profile).ok


def worker_main(worker_id, run_gen=None):
    log(f"[Worker-{worker_id}] started")
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log(f"[Worker-{worker_id}] Generation changed, stopping")
            break
        try:
            ch_id, vid_id, published_iso, detected_iso = download_queue.get(timeout=1)
        except queue.Empty:
            continue
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log(f"[Worker-{worker_id}] Generation changed after dequeue, stopping")
            download_queue.task_done()
            break
        try:
            try:
                _get_video_state_store().transition(ch_id, vid_id, "DOWNLOADING")
            except Exception as e:
                log(f"[Worker-{worker_id}] State transition lỗi {vid_id}: {e}")
            outcome = _download_one_result(ch_id, vid_id, published_iso, detected_iso)
            meta_seen = (channels_store.get_meta(ch_id) or {}).get("seen", set())
            if not outcome.ok and outcome.retryable and not outcome.permanent and _is_pending(ch_id, vid_id) and vid_id not in meta_seen:
                with _retry_lock:
                    attempt_key = f"{ch_id}:{vid_id}:attempt"
                    attempt = _retry_after.get(attempt_key, 0)
                    if attempt < MAX_RETRIES:
                        _retry_after[attempt_key] = attempt + 1
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        _retry_after[f"{ch_id}:{vid_id}:due"] = time.time() + delay
                        _get_video_state_store().transition(
                            ch_id, vid_id, "RETRY_WAIT", attempts=attempt + 1,
                            next_retry_at=time.time() + delay,
                            last_error_class=outcome.failure_class,
                            last_error_detail=outcome.detail,
                        )
                        log(f"[Worker-{worker_id}] {vid_id}: retry {attempt+1}/{MAX_RETRIES} sau {delay}s")
                    else:
                        due = time.time() + RETRY_COOLDOWN
                        log(f"[Worker-{worker_id}] {vid_id}: exhausted retries; cooldown {RETRY_COOLDOWN}s rồi thử lại")
                        _retry_after[attempt_key] = 0
                        _retry_after[f"{ch_id}:{vid_id}:due"] = due
                        _get_video_state_store().transition(
                            ch_id, vid_id, "RETRY_WAIT", attempts=0, next_retry_at=due,
                            last_error_class=outcome.failure_class,
                            last_error_detail=outcome.detail,
                        )
            elif not outcome.ok:
                state = "SKIPPED_PERMANENT" if outcome.permanent else "RETRY_WAIT"
                _get_video_state_store().transition(
                    ch_id, vid_id, state,
                    last_error_class=outcome.failure_class,
                    last_error_detail=outcome.detail,
                )
                log(f"[Worker-{worker_id}] {vid_id}: không retry (class={outcome.failure_class})")
        except Exception as e:
            log(f"[Worker-{worker_id}] lỗi {e}\n{traceback.format_exc()}")
        finally:
            download_queue.task_done()
    log(f"[Worker-{worker_id}] stopped")


def _retry_maintainer(run_gen=None):
    log("[Retry] Maintainer started")
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log("[Retry] Generation changed, stopping")
            break
        now = time.time()
        to_requeue = []
        now_expired = []
        with _retry_lock:
            for key, due in list(_retry_after.items()):
                if key.endswith(":due") and due <= now:
                    parts = key.rsplit(":", 2)
                    if len(parts) == 3:
                        ch_id, vid_id = parts[0], parts[1]
                        attempt_key = f"{ch_id}:{vid_id}:attempt"
                        attempt = _retry_after.get(attempt_key, 0)
                        if attempt <= MAX_RETRIES:
                            to_requeue.append((ch_id, vid_id, attempt))
                            _retry_after.pop(key, None)
                        else:
                            _retry_after[attempt_key] = 0
                            _retry_after[key] = now + RETRY_COOLDOWN
                    continue
                if key.endswith(":cooldown") and due <= now:
                    now_expired.append(key)
        for key in now_expired:
            with _retry_lock:
                _retry_after.pop(key, None)

        for ch_id, vid_id, attempt in to_requeue:
            if _is_pending(ch_id, vid_id) and not stop_event.is_set():
                _get_video_state_store().transition(ch_id, vid_id, "QUEUED_DOWNLOAD", next_retry_at=None)
                download_queue.put((ch_id, vid_id, None, datetime.now(timezone.utc).isoformat()))
                log(f"[Retry] Re-enqueue {vid_id} (attempt {attempt+1}/{MAX_RETRIES})")
        stop_event.wait(2)
    log("[Retry] Maintainer stopped")


def _recover_durable_downloads():
    recovered = 0
    try:
        now = time.time()
        retired = _get_video_state_store().discard_before_session(_monitor_started_epoch or now)
        if retired:
            log(f"[Recovery] Bỏ qua {retired} video thuộc phiên trước")
        for row in _get_video_state_store().recoverable(include_future=True):
            cid = row.get("channel_id")
            vid = row.get("video_id")
            if not cid or not vid or not channels_store.get_meta(cid):
                continue
            if _try_pending(cid, vid):
                next_retry_at = row.get("next_retry_at")
                if next_retry_at is not None and float(next_retry_at) > now:
                    with _retry_lock:
                        _retry_after[f"{cid}:{vid}:attempt"] = int(row.get("attempts") or 0)
                        _retry_after[f"{cid}:{vid}:due"] = float(next_retry_at)
                    recovered += 1
                    continue
                _get_video_state_store().transition(cid, vid, "QUEUED_DOWNLOAD")
                download_queue.put((cid, vid, row.get("published_iso") or None,
                                    row.get("detected_iso") or datetime.now(timezone.utc).isoformat()))
                recovered += 1
        if recovered:
            log(f"[Recovery] Khôi phục {recovered} video chưa hoàn tất")
    except Exception as e:
        log(f"[Recovery] Không khôi phục được durable queue: {e}")
    return recovered


def subscribe_websub(channel_id, callback_url):
    # Multiple startup/recovery paths can request the same subscription at once.
    # Claim it atomically so only one network call per channel can be in flight.
    now = time.time()
    with _subscription_lock:
        status = _subscription_status.setdefault(channel_id, {})
        if channel_id in _subscription_inflight:
            return False
        if float(status.get("next_retry_at") or 0) > now:
            return False
        _subscription_inflight.add(channel_id)
        status["requested_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _get_video_state_store().subscription_requesting(
            channel_id, callback_url, "ngrok", _monitor_gen
        )
    except Exception as e:
        log(f"[WebSub] Không lưu được trạng thái REQUESTING {channel_id}: {e}")
    try:
        secret = _get_websub_secret()
        data = {
            "hub.mode": "subscribe",
            "hub.topic": YOUTUBE_FEED_URL_TEMPLATE.format(channel_id),
            "hub.callback": callback_url,
            "hub.verify": "async",
            "hub.lease_seconds": "432000",
        }
        if secret:
            data["hub.secret"] = secret
        hub_url = "https://pubsubhubbub.appspot.com/subscribe"
        r = requests.post(hub_url, data=data, timeout=(10, 60))
        route = "direct"
        if not 200 <= r.status_code < 300:
            raise requests.HTTPError(f"Hub returned HTTP {r.status_code}")
        with _subscription_lock:
            status = _subscription_status.setdefault(channel_id, {})
            status["last_status"] = r.status_code
            status["last_error"] = ""
            status["retry_attempts"] = 0
            status["next_retry_at"] = time.time() + SUBSCRIBE_VERIFICATION_GRACE_SECONDS
            status["verification_pending"] = True
            status["last_route"] = route
        try:
            _get_video_state_store().subscription_accepted(
                channel_id, r.status_code, route, status["next_retry_at"]
            )
        except Exception as e:
            log(f"[WebSub] Không lưu được trạng thái ACCEPTED {channel_id}: {e}")
        log(f"[WebSub] Subscribe {channel_id}: {r.status_code} ({route})")
        return True
    except Exception as e:
        with _subscription_lock:
            status = _subscription_status.setdefault(channel_id, {})
            # Async verification can finish while the subscribe POST is still
            # waiting and later times out locally. Verification is authoritative.
            if status.get("verified_at") and not status.get("verification_pending", False):
                status["last_error"] = ""
                status["next_retry_at"] = 0
                log(f"[WebSub] {channel_id} đã verified dù request subscribe kết thúc lỗi: {e}")
                return True
            attempts = int(status.get("retry_attempts") or 0) + 1
            delay = SUBSCRIBE_RETRY_DELAYS[min(attempts - 1, len(SUBSCRIBE_RETRY_DELAYS) - 1)]
            status["retry_attempts"] = attempts
            status["next_retry_at"] = time.time() + delay
            status["last_error"] = str(e)
        try:
            _get_video_state_store().subscription_failed(
                channel_id, e, attempts, status["next_retry_at"]
            )
        except Exception as store_error:
            log(f"[WebSub] Không lưu được RETRY_WAIT {channel_id}: {store_error}")
        hours_str = f"{delay // 3600} giờ" if delay >= 3600 else f"{delay}s"
        err_msg = "Read timed out (hết thời gian chờ phản hồi)" if "Read timed out" in str(e) else str(e)
        log(f"[WebSub] Hub chưa phản hồi xác nhận cho {channel_id} ({err_msg}); sẽ thử lại sau {hours_str}. Quét API dự đoán vẫn hoạt động độc lập.")
        return False
    finally:
        with _subscription_lock:
            _subscription_inflight.discard(channel_id)


def _ngrok_bin_path():
    return ngrok_helper.get_ngrok_bin_path()


def _start_callback_server(preferred_port):
    global _callback_server, _callback_server_thread, _callback_port, _callback_instance_id, _callback_owner_token
    if _callback_server is not None:
        return True, _callback_port
    port = preferred_port
    last_error = None
    for attempt in range(3):
        try:
            server = make_server("0.0.0.0", port, flask_app, threaded=True)
            actual_port = server.server_address[1] if hasattr(server, "server_address") else port
            _callback_server = server
            _callback_port = actual_port
            _callback_instance_id = uuid.uuid4().hex[:8]
            _callback_owner_token = uuid.uuid4().hex
            server._started_at = datetime.now(timezone.utc).isoformat()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            _callback_server_thread = thread
            time.sleep(0.15)
            try:
                resp = requests.get(f"http://127.0.0.1:{actual_port}/youtube_health", timeout=2)
                if resp.status_code == 200:
                    log(f"[Callback] Server OK on :{actual_port}, instance={_callback_instance_id}")
                    return True, actual_port
            except Exception:
                pass
            log(f"[Callback] Server started on :{actual_port} but health check failed, retrying")
            try:
                server.shutdown()
            except Exception:
                pass
            _callback_server = None
            _callback_server_thread = None
            _callback_port = None
            _callback_instance_id = None
            _callback_owner_token = None
            last_error = "Health check failed"
            port = 0
        except Exception as e:
            err_text = str(e).lower()
            if "address already in use" in err_text or "errno 10048" in err_text or "errno 98" in err_text:
                log(f"[Callback] Port {port} in use, trying fallback port")
                port = 0
            else:
                log(f"[Callback] Failed to bind :{port}: {e}")
                last_error = str(e)
                port = 0
    return False, last_error or "Cannot bind callback server"


def _stop_callback_server():
    global _callback_server, _callback_server_thread, _callback_port, _callback_instance_id, _callback_owner_token
    try:
        if _callback_server:
            _callback_server.shutdown()
            _callback_server.server_close()
    except Exception:
        pass
    if _callback_server_thread and _callback_server_thread.is_alive():
        try:
            _callback_server_thread.join(timeout=2)
        except Exception:
            pass
    _callback_server = None
    _callback_server_thread = None
    _callback_port = None
    _callback_instance_id = None
    _callback_owner_token = None


def _verify_public_tunnel(tunnel_url, provider="Tunnel"):
    global _last_verified_tunnel_url, _ngrok_tunnel_was_healthy
    normalized_url = str(tunnel_url or "").rstrip("/")
    challenge = f"verify_{uuid.uuid4().hex[:12]}"
    try:
        resp = requests.get(
            f"{normalized_url}/youtube_callback/{_callback_owner_token}?hub.challenge={challenge}",
            timeout=10
        )
        if resp.status_code == 200 and resp.text.strip() == challenge:
            with _ngrok_verify_log_lock:
                should_log = not _ngrok_tunnel_was_healthy or _last_verified_tunnel_url != normalized_url
                _ngrok_tunnel_was_healthy = True
                _last_verified_tunnel_url = normalized_url
            if should_log:
                log(f"[{provider}] Tunnel verified")
            return True
        else:
            with _ngrok_verify_log_lock:
                _ngrok_tunnel_was_healthy = False
            log(f"[{provider}] Tunnel verification failed: status={resp.status_code}")
            return False
    except Exception as e:
        with _ngrok_verify_log_lock:
            _ngrok_tunnel_was_healthy = False
        log(f"[{provider}] Tunnel verification error: {e}")
        return False


def _verify_ngrok_tunnel(ngrok_url):
    """Backward-compatible wrapper retained for existing callers/tests."""
    return _verify_public_tunnel(ngrok_url, "Ngrok")


def _reset_ngrok_verification_log_state():
    global _last_verified_tunnel_url, _ngrok_tunnel_was_healthy
    with _ngrok_verify_log_lock:
        _last_verified_tunnel_url = None
        _ngrok_tunnel_was_healthy = False


def _tunnel_public_url():
    if not public_callback_url:
        return None
    return public_callback_url.rsplit("/youtube_callback", 1)[0]


def _ngrok_public_url():
    return _tunnel_public_url()


def _callback_health_ok():
    if not _callback_port:
        return False
    try:
        resp = requests.get(f"http://127.0.0.1:{_callback_port}/youtube_health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _stop_active_tunnel():
    global active_tunnel_provider
    try:
        ngrok_owner.stop_owned_agent()
    except Exception:
        pass
    active_tunnel_provider = "none"


def _active_tunnel_alive():
    if active_tunnel_provider == "ngrok":
        alive, _record = ngrok_owner.owned_agent_alive()
        return alive
    return False


def _recover_tunnel(run_gen):
    """Recreate the preferred tunnel and resubscribe channels."""
    global public_callback_url, public_callback_verified
    log("[Tunnel] Đang khôi phục tunnel...")
    _stop_active_tunnel()
    if stop_event.is_set() or (run_gen is not None and _get_monitor_gen() != run_gen):
        return False
    _refresh_ngrok_auth_status()
    if _ngrok_auth_status != "ready":
        return False
    ok = _start_ngrok(_callback_port or 0)
    if not ok:
        return False
    if public_callback_url:
        try:
            channels_store.subscribe_all(public_callback_url)
        except Exception as e:
            log(f"[Tunnel] Resubscribe sau recovery lỗi: {e}")
    return True


def _recover_ngrok(run_gen):
    return _recover_tunnel(run_gen)


def _wait_recovery(seconds):
    _recovery_kick.wait(seconds)
    _recovery_kick.clear()


def retry_ngrok_recovery():
    """Manual retry (UI). Forces an immediate recovery attempt."""
    global _recovery_attempt
    with _recovery_lock:
        _recovery_attempt = 0
    if not _monitor_started:
        return False, "YouTube Monitor chưa chạy."
    _set_monitor_state("RECOVERING")
    _recovery_kick.set()
    set_websub_health(False, "Đang thử khôi phục tunnel...")
    return True, "Đang thử khôi phục tunnel..."


def _recovery_worker(run_gen=None):
    global _recovery_attempt
    local_interval = 30
    public_interval = 120
    last_public_check = 0.0
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            break
        if not _monitor_started:
            stop_event.wait(5)
            continue
        health_ok = _callback_health_ok()
        now = time.time()
        tunnel_ok = health_ok
        if health_ok and now - last_public_check >= public_interval:
            last_public_check = now
            url = _tunnel_public_url()
            if url:
                tunnel_ok = _verify_public_tunnel(url, active_tunnel_provider.title())
            else:
                tunnel_ok = False
        else:
            if not _active_tunnel_alive():
                tunnel_ok = False
        if tunnel_ok:
            with _recovery_lock:
                _recovery_attempt = 0
            set_websub_health(True)
            if _monitor_state == "RECOVERING" or _monitor_state == "DEGRADED":
                _set_monitor_state("RUNNING")
            stop_event.wait(local_interval)
            continue
        with _recovery_lock:
            _recovery_attempt += 1
            attempt = _recovery_attempt
        if attempt > MAX_RECOVERY_ATTEMPTS:
            _set_monitor_state("DEGRADED")
            set_websub_health(False, "Tunnel không khôi phục được; polling vẫn hoạt động.")
            log("[Tunnel] Hết lượt recovery, chuyển DEGRADED. Nhấn Retry để thử lại.")
            _wait_recovery(300)
            continue
        _set_monitor_state("RECOVERING")
        set_websub_health(False, f"Tunnel gián đoạn, đang khôi phục (lần {attempt}/{MAX_RECOVERY_ATTEMPTS})")
        delay = min(RECOVERY_BACKOFF_BASE * (2 ** (attempt - 1)), RECOVERY_BACKOFF_MAX)
        log(f"[Tunnel] Recovery lần {attempt}, thử lại sau {delay}s")
        _wait_recovery(delay)
        if stop_event.is_set():
            break
        if _recover_tunnel(run_gen):
            with _recovery_lock:
                _recovery_attempt = 0
            set_websub_health(True)
            _set_monitor_state("RUNNING")
    log("[Tunnel] Recovery worker stopped")


def _refresh_ngrok_auth_status():
    global _ngrok_auth_status, _ngrok_auth_source
    ok, msg = ngrok_owner.validate_auth_ready()
    with _ngrok_auth_lock:
        if ok:
            _ngrok_auth_status = "ready"
            _ngrok_auth_source = "environment" if "environment" in msg else "user_config"
        else:
            _ngrok_auth_status = "missing"
            _ngrok_auth_source = ""


def _start_ngrok(port):
    global public_callback_url, public_callback_verified, last_error, active_tunnel_provider
    ok, payload = ngrok_owner.start_owned_agent(
        port,
        _callback_instance_id or "",
        _get_monitor_gen(),
    )
    if not ok:
        log(f"[Ngrok] Start lỗi: {payload}")
        last_error = str(payload)
        return False
    ngrok_url = str(payload["public_url"]).rstrip("/")
    public_callback_url = f"{ngrok_url}/youtube_callback/{_callback_owner_token}"
    log(f"[Ngrok] Callback: {public_callback_url}")
    if _verify_ngrok_tunnel(ngrok_url):
        public_callback_verified = True
        active_tunnel_provider = "ngrok"
        return True
    else:
        try:
            ngrok_owner.stop_owned_agent()
        except Exception:
            pass
        public_callback_url = None
        public_callback_verified = False
        last_error = "Ngrok tunnel public không phản hồi"
        return False


def _needs_resubscribe(cid):
    """A subscription needs refresh when it was never verified, the hub errored, or the
    lease is missing / expired / within RESUBSCRIBE_LEAD_TIME of expiring."""
    with _subscription_lock:
        s = _subscription_status.get(cid) or {}
        if cid in _subscription_inflight:
            return False
        if float(s.get("next_retry_at") or 0) > time.time():
            return False
    if not s.get("verified_at"):
        return True
    if s.get("last_error"):
        return True
    expires = s.get("lease_expires_at") or ""
    if not expires:
        return True
    try:
        exp = datetime.fromisoformat(expires)
    except (ValueError, TypeError):
        return True
    if exp <= datetime.now(timezone.utc):
        return True
    return exp <= datetime.now(timezone.utc) + timedelta(hours=RESUBSCRIBE_LEAD_TIME_HOURS)


def _resubscribe_worker(run_gen=None):
    last_cleanup_at = 0.0
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            break
        if public_callback_url:
            try:
                if not stop_event.is_set():
                    channels_store.subscribe_all(public_callback_url)
            except Exception as e:
                log(f"[WebSub] Resubscribe lỗi: {e}")
        now = time.monotonic()
        if now - last_cleanup_at >= RESUBSCRIBE_CHECK_SECONDS:
            _cleanup_temp_dl(older_than_seconds=86400)
            last_cleanup_at = now
        # A short tick makes failed requests retry promptly. Verified leases are
        # still gated by _needs_resubscribe(), so this does not spam the hub.
        stop_event.wait(SUBSCRIBE_RETRY_TICK_SECONDS)


def _poll_interval_seconds():
    cfg = get_config()
    healthy = max(60, int(cfg.get("poll_interval_healthy_seconds", 120) or 120))
    degraded = max(15, int(cfg.get("poll_interval_degraded_seconds", 30) or 30))
    with _subscription_lock:
        active_count = sum(1 for meta in channels_store.all_items().values() if meta.get("active", True))
        verified_count = sum(1 for s in _subscription_status.values() if s.get("verified_at"))
    if not public_callback_verified or verified_count < active_count:
        base = degraded
    else:
        base = healthy
    # Avoid synchronized request bursts when many installed clients start together.
    return max(15, base + random.uniform(-min(15, base * 0.15), min(15, base * 0.15)))


def _polling_worker(run_gen=None):
    global _last_poll_ok_at, _last_poll_error
    log("[Polling] Reconciliation worker started")
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            break
        try:
            scan_limit = min(50, max(5, int(get_config().get("poll_scan_limit", 10) or 10)))
            youtube = None
            for cid, meta in channels_store.all_items().items():
                if stop_event.is_set() or (run_gen is not None and _get_monitor_gen() != run_gen):
                    break
                if not meta.get("active", True):
                    continue
                # Predictive windows have one polling owner, including completed
                # windows. Reconciliation resumes normally outside the window.
                if _predictive_scheduler and _predictive_scheduler.is_channel_in_window(cid):
                    continue
                entries = []
                try:
                    feed = requests.get(
                        YOUTUBE_FEED_URL_TEMPLATE.format(urllib.parse.quote(cid)),
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                            "Accept": "application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
                        },
                        timeout=10,
                    )
                    feed.raise_for_status()
                    entries = _parse_websub_xml(feed.text)[:scan_limit]
                except Exception as feed_error:
                    log(f"[Polling] Feed lỗi {cid}, fallback Data API: {feed_error}")
                    if _predictive_scheduler and _predictive_scheduler.is_channel_in_window(cid):
                        continue
                    if youtube is None:
                        youtube = get_youtube_client()
                    playlist_id = _get_uploads_playlist_id(cid, youtube)
                    if playlist_id:
                        response = youtube.playlistItems().list(
                            part="snippet,contentDetails", playlistId=playlist_id, maxResults=scan_limit
                        ).execute()
                        entries = [
                            (_playlist_item_video_id(item), cid, _playlist_item_published(item))
                            for item in response.get("items", [])
                        ]
                # Feeds are newest-first. Process oldest-first so watermark advances
                # monotonically and a burst of uploads cannot hide an earlier item.
                for vid, _entry_cid, published in reversed(entries):
                    if not vid:
                        continue
                    _register_detected_video(
                        cid, vid, published,
                        datetime.now(timezone.utc).isoformat(), "POLLING",
                    )
                stop_event.wait(0.15)
            _last_poll_ok_at = datetime.now(timezone.utc).isoformat()
            _last_poll_error = ""
        except Exception as e:
            _last_poll_error = str(e)[:500]
            log(f"[Polling] Lỗi reconciliation: {_last_poll_error}")
        stop_event.wait(_poll_interval_seconds())
    log("[Polling] Reconciliation worker stopped")


def _ensure_channel_metadata(channel_id, youtube):
    """Fetch and persist channel title/thumbnail/url when missing (one-time enrichment)."""
    meta = channels_store.get_meta(channel_id) or {}
    if meta.get("title") or meta.get("meta_attempted"):
        return meta
    try:
        resp = youtube.channels().list(part="snippet", id=channel_id).execute()
        items = resp.get("items") or []
        if not items:
            channels_store.update_meta(channel_id, meta_attempted=True)
            return meta
        sn = items[0].get("snippet") or {}
        title = sn.get("title") or ""
        thumbs = sn.get("thumbnails") or {}
        thumb = ""
        if isinstance(thumbs, dict):
            t = thumbs.get("default") or thumbs.get("medium") or thumbs.get("high") or {}
            thumb = t.get("url") or ""
        channels_store.update_meta(channel_id, title=title or "Untitled",
                                   thumbnail=thumb,
                                   channel_url=f"https://www.youtube.com/channel/{channel_id}",
                                   meta_attempted=True)
        if title:
            log(f"[Channel] Metadata: {title}")
    except Exception as e:
        log(f"[Channel] Metadata lỗi {channel_id}: {e}")
        channels_store.update_meta(channel_id, meta_attempted=True)
    return channels_store.get_meta(channel_id) or {}


def _get_uploads_playlist_id(channel_id, youtube):
    try:
        response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
        items = response.get("items", [])
        if items:
            return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception:
        pass
    return None


WEBSECRET_KEY = "websub_secret"
MAX_CALLBACK_BODY = 1 * 1024 * 1024


def _get_websub_secret():
    global _websub_secret_cache
    with _websub_secret_lock:
        if _websub_secret_cache:
            return _websub_secret_cache
        try:
            cfg = get_config()
            secret = cfg.get(WEBSECRET_KEY, "")
            if secret and len(secret) >= 16:
                _websub_secret_cache = secret
                return secret
            import hashlib
            secret = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
            cfg[WEBSECRET_KEY] = secret
            _save_config(cfg)
            _websub_secret_cache = secret
            log("[WebSub] Generated new secret")
            return secret
        except Exception as e:
            log(f"[WebSub] Secret error: {e}")
            return ""


def _verify_websub_signature(payload_bytes, signature_header):
    if not signature_header:
        return False
    secret = _get_websub_secret()
    if not secret:
        return False
    sig = signature_header.strip()
    if sig.startswith("sha256="):
        algo = "sha256"
        provided = sig[len("sha256="):]
    elif sig.startswith("sha1="):
        algo = "sha1"
        provided = sig[len("sha1="):]
    else:
        return False
    expected = hmac.new(secret.encode(), payload_bytes, algo).hexdigest()
    return hmac.compare_digest(expected, provided.lower())


def _get_monitor_gen():
    with _monitor_gen_lock:
        return _monitor_gen


def _join_all_threads(timeout=8):
    deadline = time.time() + timeout
    threads = list(_all_threads)
    for t in threads:
        if t.is_alive():
            remaining = max(0.1, deadline - time.time())
            if remaining <= 0:
                break
            t.join(timeout=remaining)
    if any(t.is_alive() for t in threads):
        time.sleep(0.15)
        for t in threads:
            if t.is_alive():
                t.join(timeout=1.0)


def _live_monitor_threads():
    return [t for t in list(_all_threads) if t.is_alive()]


def _add_thread(t):
    _all_threads.append(t)


def start_monitor():
    global _monitor_started, _monitor_started_epoch, _monitor_session_id, _monitor_gen, last_error, _proxy_pool, _proxy_by_profile, _proxy_rr_index, _download_sem, public_callback_url, public_callback_verified
    with _state_lock:
        _set_monitor_state("STARTING")
        if _monitor_started:
            ok, msg = get_monitor_health()
            if ok:
                return True, "YouTube Monitor đang chạy."
            log("[Monitor] State says started but unhealthy, force stopping")
            _force_stop()
        live_threads = _live_monitor_threads()
        if live_threads:
            return False, f"Monitor cũ chưa dừng hết ({len(live_threads)} thread còn sống)."
        with _monitor_gen_lock:
            _monitor_gen += 1
            run_gen = _monitor_gen
        _monitor_session_id, _monitor_started_epoch = _get_video_state_store().begin_session()
        stop_event.clear()
        _all_threads[:] = [t for t in _all_threads if t.is_alive()]
        channels_store.load()
        channels_store.start_autosave()
        cfg = get_config()
        if cfg.get("proxy_rotation", True):
            _proxy_by_profile, _proxy_pool = _load_tiktok_proxies()
        else:
            _proxy_by_profile, _proxy_pool = {}, []
            log("[Proxy] Proxy rotation disabled")
        _proxy_rr_index = 0
        if "workers" in cfg and "download_workers" not in cfg:
            cfg["download_workers"] = max(1, int(cfg.pop("workers", 8) // 2 or 4))
            _save_config(cfg)
        workers = max(1, int(cfg.get("download_workers", 4) or 4))
        _download_sem = threading.Semaphore(workers)
        ngrok_port = int(cfg.get("ngrok_port", NGROK_PORT_DEFAULT) or NGROK_PORT_DEFAULT)
        ok, port_or_err = _start_callback_server(ngrok_port)
        if not ok:
            last_error = f"Callback server: {port_or_err}"
            log(f"[Monitor] {last_error}")
            channels_store.stop_autosave()
            _get_video_state_store().end_session(_monitor_session_id)
            _monitor_session_id = None
            _monitor_started_epoch = None
            _set_monitor_state("STOPPED")
            return False, last_error
        if not _get_websub_secret():
            _stop_callback_server()
            channels_store.stop_autosave()
            last_error = "Không tạo được WebSub secret"
            log(f"[Monitor] {last_error}")
            _get_video_state_store().end_session(_monitor_session_id)
            _monitor_session_id = None
            _monitor_started_epoch = None
            _set_monitor_state("STOPPED")
            return False, last_error
        _refresh_ngrok_auth_status()
        tunnel_ok = False
        try:
            if _ngrok_auth_status == "ready":
                tunnel_ok = _start_ngrok(_callback_port)
            else:
                log("[Ngrok] Không có authtoken; tiếp tục bằng polling")
        except Exception as e:
            last_error = f"Tunnel: {e}"
            log(f"[Tunnel] Start lỗi: {e}")
        if not tunnel_ok:
            last_error = last_error or "Tunnel không hoạt động"
            log(f"[Monitor] {last_error}; tiếp tục bằng polling reconciliation")
            _set_monitor_state("DEGRADED")
        t = threading.Thread(target=websub_processor_worker, args=(run_gen,), daemon=True)
        _add_thread(t)
        t.start()
        for i in range(workers):
            t = threading.Thread(target=worker_main, args=(i + 1, run_gen), daemon=True)
            _add_thread(t)
            t.start()
        t = threading.Thread(target=_retry_maintainer, args=(run_gen,), daemon=True)
        _add_thread(t)
        t.start()
        t = threading.Thread(target=_resubscribe_worker, args=(run_gen,), daemon=True)
        _add_thread(t)
        t.start()
        t = threading.Thread(target=_recovery_worker, args=(run_gen,), daemon=True)
        _add_thread(t)
        t.start()
        global _predictive_scheduler, _predictive_scheduler_thread
        _predictive_scheduler = PredictiveScheduler(
            channels_store=channels_store,
            key_manager=api_key_manager,
            on_video_detected=_register_detected_video,
            stop_event=stop_event,
            settings_provider=lambda: get_config()["predictive_polling"],
        )
        t = threading.Thread(target=_polling_worker, args=(run_gen,), daemon=True, name="youtube-polling-reconciliation")
        _add_thread(t)
        t.start()
        _predictive_scheduler_thread = threading.Thread(
            target=_predictive_scheduler.run_loop, daemon=True, name="youtube-predictive-scheduler"
        )
        _add_thread(_predictive_scheduler_thread)
        _predictive_scheduler_thread.start()
        _recover_durable_downloads()
        if public_callback_url:
            channels_store.subscribe_all(public_callback_url)
        _monitor_started = True
        if tunnel_ok:
            _set_monitor_state("RUNNING")
            return True, f"YouTube Monitor đã start (WebSub qua {active_tunnel_provider} + polling)."
        return True, "YouTube Monitor đang chạy bằng polling; tunnel/WebSub đang tự khôi phục."


def get_monitor_health():
    if not _monitor_started:
        return False, "Monitor chưa chạy"
    polling_alive = any(
        t.is_alive() and t.name == "youtube-polling-reconciliation" for t in _all_threads
    )
    if not polling_alive:
        return False, "Polling reconciliation không hoạt động"
    if _monitor_state == "DEGRADED":
        return True, "Polling fallback đang chạy; tunnel/WebSub chưa sẵn sàng."
    if _monitor_state == "RECOVERING":
        return True, "Polling fallback đang chạy; đang khôi phục tunnel/WebSub."
    if _callback_port:
        try:
            resp = requests.get(f"http://127.0.0.1:{_callback_port}/youtube_health", timeout=2)
            if resp.status_code == 200:
                with _subscription_lock:
                    total_subs = len(_subscription_status)
                    subs_ok = sum(1 for s in _subscription_status.values() if s.get("verified_at"))
                if total_subs > 0 and subs_ok < total_subs:
                    return True, "WebSub gián đoạn; video mới có thể bị bỏ lỡ."
                return True, "OK"
        except Exception:
            pass
    return False, "Callback server không phản hồi"


def get_monitor_state():
    return _monitor_state


def open_youtube_login_browser():
    """Open the dedicated persistent browser without starting another app instance."""
    ok, message = _youtube_login_browser.open()
    log(f"[YouTube Login] {message}")
    return ok, message


def _set_monitor_state(state):
    global _monitor_state
    with _monitor_state_lock:
        _monitor_state = state


def set_websub_health(ok, error_msg=""):
    global _last_websub_ok_at, _last_websub_error
    if ok:
        _last_websub_ok_at = datetime.now(timezone.utc).isoformat()
        _last_websub_error = ""
    else:
        _last_websub_error = error_msg or _last_websub_error


def _force_stop():
    global _monitor_started, _monitor_started_epoch, _monitor_session_id, _callback_server, _callback_server_thread, _callback_port, _callback_instance_id, public_callback_url, public_callback_verified
    stop_event.set()
    _stop_callback_server()
    _stop_active_tunnel()
    _join_all_threads(timeout=3)
    if _live_monitor_threads():
        log("[Monitor] Force stop còn thread sống, giữ state để tránh restart đè generation")
        return
    public_callback_url = None
    public_callback_verified = False
    _reset_ngrok_verification_log_state()
    _active_downloads.clear()
    _pending_video_ids.clear()
    _retry_after.clear()
    _monitor_started = False
    _get_video_state_store().end_session(_monitor_session_id)
    _monitor_session_id = None
    _monitor_started_epoch = None


def stop_monitor():
    global _monitor_started, _monitor_started_epoch, _monitor_session_id, public_callback_url, public_callback_verified
    with _state_lock:
        if not _monitor_started:
            return True, "YouTube Monitor chưa chạy."
        _set_monitor_state("STOPPING")
        stop_event.set()
        channels_store.stop_autosave()
        _stop_callback_server()
        _stop_active_tunnel()
        _join_all_threads(timeout=5)
        if _live_monitor_threads():
            return False, f"YouTube Monitor chưa dừng hết ({len(_live_monitor_threads())} thread còn sống)."
        public_callback_url = None
        public_callback_verified = False
        _reset_ngrok_verification_log_state()
        _active_downloads.clear()
        _pending_video_ids.clear()
        _retry_after.clear()
        _all_threads.clear()
        _monitor_started = False
        _get_video_state_store().end_session(_monitor_session_id)
        _monitor_session_id = None
        _monitor_started_epoch = None
        _set_monitor_state("STOPPED")
        log("[Monitor] Stopped")
    return True, "YouTube Monitor đã dừng."


def analyze_channel_schedule(channel_id):
    """Lấy 30 video gần nhất qua API và phân tích thói quen đăng video của kênh."""
    cid = str(channel_id or "").strip()
    meta = channels_store.get_meta(cid)
    if not meta:
        return False, "Kênh không tồn tại trong danh sách."
    try:
        yt = get_youtube_client()
        playlist_id = meta.get("uploads_playlist_id") or _get_uploads_playlist_id(cid, yt)
        if not playlist_id:
            return False, "Không xác định được uploads playlist của kênh."

        resp = yt.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=30,
        ).execute()

        published_list = []
        for it in resp.get("items", []):
            pub = it.get("snippet", {}).get("publishedAt")
            if pub:
                published_list.append(pub)

        learner = ScheduleLearner(meta.get("timezone") or DEFAULT_TIMEZONE)
        res = learner.analyze_timestamps(published_list)
        res["history"] = published_list
        channels_store.update_meta(cid, schedule_learned=res, uploads_playlist_id=playlist_id)
        channels_store.save_now()
        log(f"[Learner] Đã phân tích lịch {cid}: {len(res.get('predicted_windows', []))} khung giờ dự đoán (tin cậy: {res.get('confidence_score')})")
        return True, res
    except Exception as e:
        log(f"[Learner] Lỗi phân tích lịch kênh {cid}: {e}")
        return False, str(e)


def update_channel_manual_windows(channel_id, manual_windows):
    """Lưu khung giờ thủ công của người dùng cho kênh."""
    cid = str(channel_id or "").strip()
    meta = channels_store.get_meta(cid)
    if not meta:
        return False, "Kênh không tồn tại."
    clean_wins = []
    for w in manual_windows:
        if isinstance(w, PollingWindow):
            clean_wins.append(w.to_dict())
        elif isinstance(w, dict):
            clean_wins.append(w)
    channels_store.update_meta(cid, manual_windows=clean_wins)
    channels_store.save_now()
    log(f"[Scheduler] Đã lưu {len(clean_wins)} khung giờ thủ công cho kênh {cid}")
    return True, "Đã lưu khung giờ."


def add_channel_for_profile(channel_input, profile_name, folder_path):
    youtube = get_youtube_client()
    info = get_channel_id_from_link(channel_input, youtube)
    if not info or not info.get("id"):
        return False, "Không lấy được Channel ID."
    cid = info["id"]
    channel_title = ""
    channel_thumbnail = ""
    channel_url = f"https://www.youtube.com/channel/{cid}"
    try:
        resp = youtube.channels().list(part="snippet,contentDetails", id=cid).execute()
        if resp.get("items"):
            sn = resp["items"][0].get("snippet") or {}
            channel_title = sn.get("title") or ""
            thumbs = sn.get("thumbnails") or {}
            if isinstance(thumbs, dict):
                thumb = thumbs.get("default") or thumbs.get("medium") or thumbs.get("high") or {}
                channel_thumbnail = thumb.get("url") or ""
    except Exception as e:
        log(f"[Channel] Lấy metadata lỗi {cid}: {e}")
    channels_store.add_channel(cid, folder_path, profile_name=profile_name, process_short=True,
                               title=channel_title, thumbnail=channel_thumbnail, channel_url=channel_url)
    try:
        playlist_id = info.get("playlistId") or _get_uploads_playlist_id(cid, youtube)
        if playlist_id:
            channels_store.update_meta(cid, uploads_playlist_id=playlist_id)
            response = youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=5
            ).execute()
            seeded = _seed_polling_baseline(cid, response.get("items", []))
            if seeded:
                log(f"[Channel] Baseline {seeded} existing videos for {cid}")
            try:
                analyze_channel_schedule(cid)
            except Exception as sch_err:
                log(f"[Learner] Phân tích lịch ban đầu {cid} lỗi: {sch_err}")
    except Exception as e:
        log(f"[Channel] Baseline lỗi {cid}: {e}")
    channels_store.save_now()
    if public_callback_url:
        subscribe_websub(cid, public_callback_url)
    log(f"[Channel] Added {cid} ({channel_title or 'no title'}) -> {profile_name}")
    return True, cid


def download_test_video(video_input, profile_name, folder_path):
    video_id = _extract_video_id(video_input)
    if not video_id:
        return False, "Video URL/ID không hợp lệ."
    def run():
        ok = download_one(f"TEST_{profile_name}", video_id, None, datetime.now(timezone.utc).isoformat(), target_folder=folder_path, process_short=True, activity_profile=profile_name)
        log(f"[Test] {'OK' if ok else 'FAIL'} {video_id} -> {profile_name}")
    threading.Thread(target=run, daemon=True).start()
    return True, f"Đã bắt đầu tác vụ tải thử {video_id} (direct-first)."


def get_channels():
    items = []
    for cid, meta in channels_store.all_items().items():
        items.append({
            "channel_id": cid,
            "folder": meta.get("folder", ""),
            "profile_name": meta.get("profile_name", ""),
            "active": bool(meta.get("active", True)),
            "process_short": bool(meta.get("process_short", True)),
            "seen_count": len(meta.get("seen", set())),
            "last_pub_utc": meta.get("last_pub_utc"),
            "title": meta.get("title", ""),
            "thumbnail": meta.get("thumbnail", ""),
            "channel_url": meta.get("channel_url", ""),
            "manual_windows": meta.get("manual_windows") or [],
            "schedule_learned": meta.get("schedule_learned") or {},
            "timezone": meta.get("timezone") or DEFAULT_TIMEZONE,
        })
    return items


def remove_channel(channel_id):
    channels_store.remove_channel(channel_id)
    channels_store.save_now()
    return True, "Đã xóa channel."


def set_channel_profile(channel_id, profile_name, folder_path):
    channels_store.set_folder(channel_id, folder_path, profile_name=profile_name)
    channels_store.save_now()
    return True, "Đã cập nhật profile đích."


def toggle_channel_active(channel_id):
    value = channels_store.toggle_active(channel_id)
    channels_store.save_now()
    return True, f"Active={value}"


def toggle_channel_short(channel_id):
    value = channels_store.toggle_process_short(channel_id)
    channels_store.save_now()
    return True, f"Short={value}"


def rename_channel_profile(old_name, new_name):
    renamed = channels_store.rename_profile(old_name, new_name)
    if renamed:
        channels_store.save_now()
        log(f"[Channels] Đổi profile tham chiếu: {old_name} -> {new_name} ({renamed} channel)")
    return True, f"Đã đồng bộ {renamed} channel sang profile mới."


def channel_count_for_profile(profile_name):
    return channels_store.count_by_profile(profile_name)


def get_status():
    cfg = get_config()
    healthy, health_msg = get_monitor_health()
    subs_ok = 0
    with _subscription_lock:
        total_subs = len(_subscription_status)
        subs_ok = sum(1 for s in _subscription_status.values() if s.get("verified_at"))
    cookie_path = _resolve_cookies_file()
    if cookie_path:
        cookie_valid, cookie_reason = validate_youtube_cookie_file(cookie_path)
        cookies_status = "ok" if cookie_valid else "invalid"
    else:
        cookies_status = "missing"
        cookie_reason = "Chưa cấu hình file cookie."
    return {
        "running": _monitor_started,
        "healthy": healthy,
        "health_msg": health_msg,
        "monitor_state": _monitor_state,
        "recovery_attempt": _recovery_attempt,
        "detection_source": "WEBSUB+POLLING",
        "last_websub_ok_at": _last_websub_ok_at,
        "last_websub_error": _last_websub_error,
        "last_poll_ok_at": _last_poll_ok_at,
        "last_poll_error": _last_poll_error,
        "callback_url": public_callback_url or "",
        "callback_port": _callback_port,
        "callback_verified": public_callback_verified,
        "tunnel_provider": active_tunnel_provider,
        "ngrok_auth_status": _ngrok_auth_status,
        "ngrok_auth_source": _ngrok_auth_source,
        "last_callback_post": last_callback_post_time,
        "channels": len(channels_store.all_items()),
        "queue": download_queue.qsize(),
        "workers": len([t for t in _all_threads if t.is_alive()]),
        "downloaded_today": downloaded_today,
        "last_error": last_error,
        "api_key_set": bool((cfg.get("api_keys") or [""])[0]),
        "cookies_set": bool(cookie_path),
        "cookies_status": cookies_status,
        "cookies_detail": cookie_reason,
        "download_workers": max(1, int(cfg.get("download_workers", 4) or 4)),
        "video_quality": get_video_quality(),
        "subscriptions_total": total_subs,
        "subscriptions_ok": subs_ok,
        "subscriptions_degraded": total_subs - subs_ok,
        "pending": len([v for v in _pending_video_ids if not stop_event.is_set()]),
        "video_states": _get_video_state_store().counts(),
    }
