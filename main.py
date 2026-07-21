import os
import sys
import queue
import json
import shutil
import requests
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

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

# Selenium Wire chỉ dùng khi bật debug request trace; runtime thường dùng Selenium chuẩn.
from selenium import webdriver

from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk, StringVar, Menu
from tkinter.scrolledtext import ScrolledText
from app_ui import configure_ttk_styles, build_dashboard, classify_log_message
from core_helpers import (
    parse_cookie,
    parse_proxy_string,
    verify_proxy_ip,
    is_driver_valid,
    is_file_stable,
    clean_chrome_lock_files,
    normalize_profile_path,
    process_uses_profile,
    classify_webdriver_error,
    profile_driver_path,
    clear_profile_directory,
    copy_video_atomically,
)
from config_store import (
    build_configs_payload,
    save_configs_file,
    load_configs_file,
    normalize_loaded_config,
    build_runtime_profiles,
)
from browser_environment import (
    apply_device_preset,
    chrome_environment_arguments,
    chrome_environment_preferences,
    configure_driver_environment,
    ensure_fingerprint_defaults,
    geo_cache_is_current,
    proxy_cache_key,
    resolve_geoip,
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
from selenium.common.exceptions import (
    InvalidSessionIdException,
    SessionNotCreatedException,
    TimeoutException,
    NoSuchElementException,
    WebDriverException
)
import signal
import warnings
import psutil
from webdriver_manager.chrome import ChromeDriverManager

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
UPLOAD_READY_TIMEOUT = 180
SMALL_WAIT = 0.5 
PAGELOAD_TIMEOUT = 120 
SCRIPT_TIMEOUT = 120
UPLOAD_PROGRESS_WARN_AFTER = 12
UPLOAD_STALL_TIMEOUT = 45
UPLOAD_PHASE_STALL_TIMEOUT = 360
UPLOAD_HARD_TIMEOUT = 540
UPLOAD_STALL_POLL_INTERVAL = 0.05
UPLOAD_POST_SENDKEYS_SETTLE_SECONDS = 0.15
UPLOAD_SIGNAL_POLL_INTERVAL = 0.6
NETWORK_READY_POLL_INTERVAL = 0.4
ALL_OPTION = "Default"
START_PROFILE_TIMEOUT = 180 
DRIVER_INIT_RETRIES = 2 
DRIVER_INIT_RETRY_DELAY = 1.0
DRIVER_INIT_TIMEOUT = 60
UPLOAD_CONTAINER_QUICK_WAIT = 1.2
IDLE_SHUTDOWN_TIMEOUT = 0
LIMIT_REACHED_SHUTDOWN_DELAY = 5
MAX_STATUS_LOG_LINES = 1000
MAX_IMPORTANT_LOG_LINES = 300
TIKTOK_BASE_URL = "https://www.tiktok.com"
TIKTOK_PRIME_URL = "https://www.tiktok.com/robots.txt"
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center"
REQUEST_TRACE_DIR = app_base_dir() / "request_traces"
REQUEST_TRACE_MAX_REQUESTS = 120
REQUEST_TRACE_PREVIEW_BYTES = 1024
FAILED_UPLOADS_LOG = app_base_dir() / "failed_uploads.log"
UPLOAD_BENCHMARK_LOG = app_base_dir() / "upload_benchmarks.jsonl"
DEBUG_SELENIUM_WIRE = os.environ.get("TIKTOK_DEBUG_SELENIUM_WIRE", "").strip().lower() in ("1", "true", "yes", "on")
BLOCK_AUX_RESOURCES = os.environ.get("TIKTOK_BLOCK_AUX_RESOURCES", "").strip().lower() in ("1", "true", "yes", "on")
PROXY_OK_CACHE_TTL_SECONDS = 60 * 60
BLOCKED_AUX_RESOURCE_PATTERNS = [
    "*doubleclick.net/*",
    "*googleadservices.com/*",
    "*googlesyndication.com/*",
    "*googletagmanager.com/*",
    "*google-analytics.com/*",
    "*facebook.com/tr/*",
    "*bat.bing.com/*",
    "*ads.linkedin.com/*",
    "*scorecardresearch.com/*",
    "*criteo.*",
    "*criteo.com/*",
    "*hotjar.com/*",
    "*newrelic.com/*",
    "*sentry.io/*",
    "*cdn.segment.com/*",
    "*segment.io/*",
    "*amplitude.com/*",
    "*mixpanel.com/*",
]

# =========================
# FINGERPRINT CONFIG
# =========================
USER_AGENT_POOL = [
    # Orbita bundled with the application is Chromium 123.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

WINDOW_SIZES = [
    (1366, 768), (1440, 900), (1536, 864), (1920, 1080), (1280, 720),
    (1600, 900), (1280, 800), (1440, 960), (1680, 1050),
]

WEBGL_VENDORS = [
    ("Google Inc. (Intel)", "Intel Iris OpenGL Engine"),
    ("Google Inc. (NVIDIA)", "NVIDIA GeForce RTX 4060 OpenGL Engine"),
    ("Google Inc. (NVIDIA)", "NVIDIA GeForce RTX 3050 OpenGL Engine"),
    ("Google Inc. (AMD)", "AMD Radeon RX 6600 OpenGL Engine"),
    ("Google Inc. (Intel)", "Intel(R) UHD Graphics 620 OpenGL Engine"),
    ("Google Inc. (NVIDIA)", "NVIDIA GeForce GTX 1660 Ti OpenGL Engine"),
    ("Google Inc. (Intel)", "Intel(R) Iris(R) Xe Graphics OpenGL Engine"),
    ("Google Inc. (NVIDIA)", "NVIDIA GeForce RTX 3060 OpenGL Engine"),
]

LANG_POOL = ["en-US", "en-GB", "en-AU", "en-CA", "en-IN", "en-NZ", "en-ZA", "en-SG"]

def _generate_fingerprint(seed=None):
    rng = random.Random()
    rng.seed(seed or (str(time.time_ns()) + str(uuid.uuid4())))
    w, h = rng.choice(WINDOW_SIZES)
    ua = rng.choice(USER_AGENT_POOL)
    lang = rng.choice(LANG_POOL)
    cores = rng.choice([2, 4, 6, 8])
    webgl_vendor, webgl_renderer = rng.choice(WEBGL_VENDORS)
    canvas_noise = round(rng.uniform(0.0001, 0.002), 6)
    return ensure_fingerprint_defaults({
        "user_agent": ua,
        "window_width": w,
        "window_height": h,
        "lang": lang,
        "hardware_concurrency": cores,
        "webgl_vendor": webgl_vendor,
        "webgl_renderer": webgl_renderer,
        "canvas_noise": canvas_noise,
    }, seed=seed)

def _apply_fingerprint_to_options(chrome_options, fp):
    normalized = ensure_fingerprint_defaults(fp)
    user_agent = normalized.get("user_agent") or USER_AGENT_POOL[0]
    chrome_options.add_argument(f"--user-agent={user_agent}")
    w = normalized.get("window_width", 1920)
    h = normalized.get("window_height", 1080)
    chrome_options.add_argument(f"--window-size={w},{h}")
    lang = normalized.get("lang", "en-US")
    chrome_options.add_argument(f"--lang={lang}")
    chrome_options.add_argument(f"--accept-lang={lang}")

FINGERPRINT_JS = r"""
// Canvas noise
(function() {
    const originalGetContext = HTMLCanvasElement.prototype.getContext;
    const canvasNoise = {CANVAS_NOISE};
    HTMLCanvasElement.prototype.getContext = function(...args) {
        const ctx = originalGetContext.apply(this, args);
        if (ctx) {
            const originalFillRect = ctx.fillRect;
            ctx.fillRect = function(x, y, w, h) {
                originalFillRect.call(this, x + canvasNoise, y + canvasNoise, w, h);
            };
            const originalGetImageData = ctx.getImageData;
            ctx.getImageData = function(...args) {
                const imgData = originalGetImageData.apply(this, args);
                for (let i = 0; i < imgData.data.length; i += 4) {
                    imgData.data[i] = Math.min(255, imgData.data[i] + (canvasNoise * 255));
                }
                return imgData;
            };
        }
        return ctx;
    };
})();

// WebGL spoof
(function() {
    const vendor = '{WEBGL_VENDOR}';
    const renderer = '{WEBGL_RENDERER}';
    const getExt = HTMLCanvasElement.prototype.getContext;
    const origGetParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
        if (param === 37445) return vendor;
        if (param === 37446) return renderer;
        return origGetParameter.call(this, param);
    };
})();

// Navigator overrides
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => {HARDWARE_CONCURRENCY} });

// Languages
Object.defineProperty(navigator, 'languages', { get: () => ['{LANG}'] });
Object.defineProperty(navigator, 'language', { get: () => '{LANG}' });
"""

def _inject_fingerprint_js(driver, profile_name):
    fp = profiles.get(profile_name, {}).get('config', {}).get('fingerprint', {})
    if not fp:
        return
    try:
        configure_driver_environment(driver, fp)
        update_status(f"[{profile_name}] [DEBUG] Đã inject fingerprint JS")
    except Exception as e:
        update_status(f"[{profile_name}] [WARN] Lỗi inject fingerprint: {e}")


def _refresh_profile_geoip(profile_name, config, proxy_data, force=False):
    """Resolve proxy location only when its identity changed or cache is missing."""
    fingerprint = ensure_fingerprint_defaults(
        config.get('fingerprint', {}),
        seed=profile_name + str(config.get('cookie_str', '')),
    )
    config['fingerprint'] = fingerprint
    if not proxy_data:
        if fingerprint.get('geo_source') == 'ipwho.is':
            for key in ('timezone', 'geolocation', 'geo_exit_ip', 'geo_proxy_hash', 'geo_resolved_at', 'geo_source'):
                fingerprint.pop(key, None)
            return True
        return False
    if fingerprint.get('geo_proxy_hash') != proxy_cache_key(proxy_data):
        for key in ('timezone', 'geolocation', 'geo_exit_ip', 'geo_proxy_hash', 'geo_resolved_at', 'geo_source'):
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

def _enable_aux_resource_blocking(driver, profile_name):
    if not BLOCK_AUX_RESOURCES:
        return False
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": BLOCKED_AUX_RESOURCE_PATTERNS})
        update_status(f"[{profile_name}] [DEBUG] Đã bật block resource phụ: {len(BLOCKED_AUX_RESOURCE_PATTERNS)} patterns")
        return True
    except Exception as e:
        update_status(f"[{profile_name}] [WARN] Không bật được block resource phụ: {e}")
        return False

def _is_proxy_init_error(error):
    text = str(error).lower()
    return any(token in text for token in (
        "proxy mismatch",
        "proxy sai ip",
        "proxy lỗi",
        "proxy error",
        "proxy",
    ))

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


def _profile_operation_lock(profile_name):
    lock = profile_operation_locks.get(profile_name)
    if lock is None:
        lock = threading.Lock()
        profile_operation_locks[profile_name] = lock
    return lock

# --- KHÓA AN TOÀN CHO DRIVER (FIX LỖI ACCESS DENIED) ---
driver_install_lock = threading.Lock()
GLOBAL_DRIVER_PATH = None  # Biến lưu đường dẫn driver để tránh cài đặt lại nhiều lần
GLOBAL_CHROME_PATH = None
GLOBAL_BROWSER_MODE = None
GLOBAL_IS_ORBITA = None
PROXY_OK_CACHE = {}
UPLOAD_EVENT_TIMINGS = {}
UPLOAD_TERMINAL_RESULTS = {}
PENDING_VIDEO_PATHS = set()
upload_benchmark_lock = threading.Lock()
video_event_lock = threading.Lock()
LOCAL_DRIVER_CACHE_DIR = app_base_dir() / "temp_dl" / "driver_cache"
LOCAL_CHROMEDRIVER_PATH = LOCAL_DRIVER_CACHE_DIR / "chromedriver.exe"
LOCAL_DRIVER_METADATA_PATH = LOCAL_DRIVER_CACHE_DIR / "metadata.json"


class TikTokLoginRequiredError(Exception):
    """Raised when TikTok explicitly redirects the browser to its login page."""


class PostRejectedError(Exception):
    """Raised when TikTok explicitly rejects a publish request."""
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

def _wait_document_ready(driver, timeout=6.0, poll_interval=0.1):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        try:
            state = driver.execute_script("return document.readyState")
            if state in ('interactive', 'complete'):
                return True
        except Exception:
            pass
        time.sleep(poll_interval)
    return False

def _is_executable_file_ready(path):
    try:
        return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0
    except Exception:
        return False

def _safe_status(message):
    try:
        update_status(message)
    except Exception:
        try:
            logging.warning(message)
        except Exception:
            pass

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
    key = _upload_timing_key(video_path)
    with video_event_lock:
        if key in PENDING_VIDEO_PATHS:
            return False
        PENDING_VIDEO_PATHS.add(key)
        return True


def _release_video_path(video_path):
    with video_event_lock:
        PENDING_VIDEO_PATHS.discard(_upload_timing_key(video_path))


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

def _proxy_cache_key(profile_name, proxy_data):
    if not proxy_data:
        return None
    return (
        profile_name,
        str(proxy_data.get('ip') or ''),
        str(proxy_data.get('port') or ''),
        str(proxy_data.get('user') or ''),
    )

def _get_cached_proxy_ok(profile_name, proxy_data):
    key = _proxy_cache_key(profile_name, proxy_data)
    if not key:
        return None
    cached = PROXY_OK_CACHE.get(key)
    if not cached:
        return None
    age = time.time() - cached.get('checked_at', 0)
    if age > PROXY_OK_CACHE_TTL_SECONDS:
        PROXY_OK_CACHE.pop(key, None)
        return None
    return cached

def _remember_proxy_ok(profile_name, proxy_data, current_ip):
    key = _proxy_cache_key(profile_name, proxy_data)
    if key:
        PROXY_OK_CACHE[key] = {'ip': current_ip, 'checked_at': time.time()}

def _forget_proxy_ok(profile_name, proxy_data):
    key = _proxy_cache_key(profile_name, proxy_data)
    if key:
        PROXY_OK_CACHE.pop(key, None)

def _parse_major_version(version_text):
    if not version_text:
        return None
    match = re.search(r"(\d+)\.", str(version_text))
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None

def _bundled_browser_dir():
    root_dir = app_base_dir() / "Browser"
    if root_dir.exists():
        return root_dir
    internal_dir = app_base_dir() / "_internal" / "Browser"
    if internal_dir.exists():
        return internal_dir
    return root_dir

def _find_bundled_chrome_executable():
    global GLOBAL_CHROME_PATH, GLOBAL_BROWSER_MODE, GLOBAL_IS_ORBITA
    if GLOBAL_CHROME_PATH and _is_executable_file_ready(GLOBAL_CHROME_PATH):
        return GLOBAL_CHROME_PATH
    browser_dir = _bundled_browser_dir()
    candidates = [
        (browser_dir / "orbita-browser-123" / "chrome.exe", "bundled Orbita", True),
        (browser_dir / "chrome.exe", "bundled Chrome", False),
        (browser_dir / "chrome-win64" / "chrome.exe", "bundled Chrome for Testing", False),
        (browser_dir / "chrome" / "chrome.exe", "bundled Chrome", False),
    ]
    for path, mode, is_orbita in candidates:
        if _is_executable_file_ready(path):
            GLOBAL_CHROME_PATH = str(path)
            GLOBAL_BROWSER_MODE = mode
            GLOBAL_IS_ORBITA = is_orbita
            return GLOBAL_CHROME_PATH
    return None

def _find_bundled_chromedriver_executable():
    browser_dir = _bundled_browser_dir()
    candidates = [
        browser_dir / "chromedriver.exe",
        browser_dir / "chromedriver-win64" / "chromedriver.exe",
        browser_dir / "chromedriver" / "chromedriver.exe",
    ]
    for path in candidates:
        if _is_executable_file_ready(path):
            return str(path)
    return None


def _profile_driver_path(profile_name):
    config = profiles[profile_name]['config']
    path = profile_driver_path(config.get('chrome_profile', ''), config.get('chromedriver_path', ''))
    config['chromedriver_path'] = str(path)
    return path


def _ensure_profile_driver(profile_name):
    target = _profile_driver_path(profile_name)
    chrome_path, chrome_version, chrome_major = _get_chrome_version()
    if _is_executable_file_ready(target) and _is_chromedriver_compatible(target, chrome_major):
        return str(target)

    source = _find_bundled_chromedriver_executable()
    if not source or not _is_chromedriver_compatible(source, chrome_major):
        source = None
        if GLOBAL_DRIVER_PATH and _is_chromedriver_compatible(GLOBAL_DRIVER_PATH, chrome_major):
            source = GLOBAL_DRIVER_PATH
    if not source:
        with driver_install_lock:
            source = resolve_chromedriver_path()
    if not source or not _is_chromedriver_compatible(source, chrome_major):
        raise FileNotFoundError(
            f"Không tìm thấy ChromeDriver tương thích cho profile {profile_name} "
            f"(Chrome={chrome_version or chrome_path or 'unknown'})"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix('.tmp.exe')
    try:
        shutil.copy2(source, temp_target)
        os.replace(temp_target, target)
    finally:
        try:
            if temp_target.exists():
                temp_target.unlink()
        except Exception:
            pass
    update_status(f"[{profile_name}] [DEBUG] ChromeDriver riêng: {target}")
    return str(target)

def _find_preferred_chrome_executable():
    return _find_bundled_chrome_executable() or _find_chrome_executable()

def _using_bundled_chrome():
    return bool(_find_bundled_chrome_executable())

def _using_orbita_browser():
    _find_bundled_chrome_executable()
    if GLOBAL_IS_ORBITA is not None:
        return GLOBAL_IS_ORBITA
    return "orbita-browser" in (GLOBAL_CHROME_PATH or "").lower()

def _browser_mode_label():
    if _find_bundled_chrome_executable():
        return GLOBAL_BROWSER_MODE or "bundled Chrome"
    return "system Google Chrome"

def _find_chrome_executable():
    candidates = []
    try:
        import winreg
        for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root_key, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                    value, _ = winreg.QueryValueEx(key, None)
                    if value:
                        candidates.append(value)
            except Exception:
                pass
    except Exception:
        pass

    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))

    for path in candidates:
        try:
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass
    return None

def _get_chrome_version():
    chrome_path = _find_preferred_chrome_executable()
    if not chrome_path:
        return None, None, None
    version_file = Path(chrome_path).parent / 'version'
    if version_file.is_file():
        try:
            chrome_version = version_file.read_text(encoding='utf-8', errors='ignore').strip()
            if chrome_version:
                return chrome_path, chrome_version, _parse_major_version(chrome_version)
        except Exception:
            pass
    try:
        result = subprocess.run(
            [chrome_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (result.stdout or result.stderr or "").strip()
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
        chrome_version = match.group(1) if match else output
        return chrome_path, chrome_version, _parse_major_version(chrome_version)
    except Exception:
        return chrome_path, None, None

def _get_chromedriver_version(driver_path):
    if not _is_executable_file_ready(driver_path):
        return None, None
    try:
        result = subprocess.run(
            [str(driver_path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (result.stdout or result.stderr or "").strip()
        match = re.search(r"ChromeDriver\s+(\d+\.\d+\.\d+\.\d+)", output, re.IGNORECASE)
        driver_version = match.group(1) if match else output
        return driver_version, _parse_major_version(driver_version)
    except Exception:
        return None, None

def _is_chromedriver_compatible(driver_path, chrome_major=None):
    if not _is_executable_file_ready(driver_path):
        return False
    if chrome_major is None:
        return True
    _driver_version, driver_major = _get_chromedriver_version(driver_path)
    return driver_major == chrome_major

def _write_driver_metadata(driver_path, chrome_path=None, chrome_version=None, chrome_major=None):
    try:
        driver_version, driver_major = _get_chromedriver_version(driver_path)
        metadata = {
            "chrome_path": chrome_path,
            "chrome_version": chrome_version,
            "chrome_major": chrome_major,
            "driver_version": driver_version,
            "driver_major": driver_major,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        os.makedirs(LOCAL_DRIVER_CACHE_DIR, exist_ok=True)
        with open(LOCAL_DRIVER_METADATA_PATH, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _copy_driver_to_local_cache(src_path, chrome_path=None, chrome_version=None, chrome_major=None):
    os.makedirs(LOCAL_DRIVER_CACHE_DIR, exist_ok=True)
    if os.path.abspath(str(src_path)) != os.path.abspath(str(LOCAL_CHROMEDRIVER_PATH)):
        shutil.copy2(src_path, LOCAL_CHROMEDRIVER_PATH)
    _write_driver_metadata(LOCAL_CHROMEDRIVER_PATH, chrome_path, chrome_version, chrome_major)
    return LOCAL_CHROMEDRIVER_PATH

def _invalidate_chromedriver_cache(reason=""):
    global GLOBAL_DRIVER_PATH
    GLOBAL_DRIVER_PATH = None
    if reason:
        _safe_status(f"[Driver] Làm mới ChromeDriver cache: {reason}")
    for path in (LOCAL_CHROMEDRIVER_PATH, LOCAL_DRIVER_METADATA_PATH):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass

def _is_driver_version_mismatch_error(exc):
    text = str(exc).lower()
    mismatch_markers = (
        "this version of chromedriver",
        "only supports chrome version",
        "current browser version is",
        "chromedriver only supports chrome version",
    )
    return any(marker in text for marker in mismatch_markers)

def _scan_existing_wdm_chromedriver(chrome_major=None):
    base_dir = Path.home() / ".wdm" / "drivers" / "chromedriver"
    if not os.path.isdir(base_dir):
        return None

    candidates = []
    try:
        for root_dir, _dirs, files in os.walk(base_dir):
            for file_name in files:
                if file_name.lower() == "chromedriver.exe":
                    full_path = os.path.join(root_dir, file_name)
                    try:
                        mtime = os.path.getmtime(full_path)
                    except Exception:
                        mtime = 0
                    if chrome_major is not None and not _is_chromedriver_compatible(full_path, chrome_major):
                        continue
                    candidates.append((mtime, full_path))
    except Exception:
        return None

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]

def resolve_chromedriver_path():
    global GLOBAL_DRIVER_PATH

    bundled_driver = _find_bundled_chromedriver_executable()
    if bundled_driver:
        GLOBAL_DRIVER_PATH = bundled_driver
        return GLOBAL_DRIVER_PATH

    chrome_path, chrome_version, chrome_major = _get_chrome_version()

    if _is_chromedriver_compatible(GLOBAL_DRIVER_PATH, chrome_major):
        return GLOBAL_DRIVER_PATH
    if _is_executable_file_ready(GLOBAL_DRIVER_PATH):
        GLOBAL_DRIVER_PATH = None

    if _is_chromedriver_compatible(LOCAL_CHROMEDRIVER_PATH, chrome_major):
        GLOBAL_DRIVER_PATH = LOCAL_CHROMEDRIVER_PATH
        return GLOBAL_DRIVER_PATH
    if _is_executable_file_ready(LOCAL_CHROMEDRIVER_PATH):
        _invalidate_chromedriver_cache(f"ChromeDriver local không khớp Chrome major {chrome_major}")

    os.makedirs(LOCAL_DRIVER_CACHE_DIR, exist_ok=True)

    try:
        if os.path.isdir(LOCAL_DRIVER_CACHE_DIR):
            for f in os.listdir(LOCAL_DRIVER_CACHE_DIR):
                fp = os.path.join(LOCAL_DRIVER_CACHE_DIR, f)
                try:
                    if os.path.isfile(fp):
                        os.remove(fp)
                except Exception:
                    pass
    except Exception:
        pass

    installed_path = None
    install_error = None
    try:
        installed_path = _scan_existing_wdm_chromedriver(chrome_major)
        if not installed_path:
            installed_path = ChromeDriverManager().install()
    except Exception as e:
        install_error = e
        installed_path = _scan_existing_wdm_chromedriver(chrome_major)

    if _is_chromedriver_compatible(installed_path, chrome_major):
        try:
            GLOBAL_DRIVER_PATH = _copy_driver_to_local_cache(installed_path, chrome_path, chrome_version, chrome_major)
            return GLOBAL_DRIVER_PATH
        except Exception:
            GLOBAL_DRIVER_PATH = installed_path
            _write_driver_metadata(GLOBAL_DRIVER_PATH, chrome_path, chrome_version, chrome_major)
            return GLOBAL_DRIVER_PATH
    if _is_executable_file_ready(installed_path):
        driver_version, driver_major = _get_chromedriver_version(installed_path)
        raise RuntimeError(
            f"ChromeDriver không khớp Chrome. Chrome major={chrome_major}, "
            f"driver={driver_version or installed_path}, driver major={driver_major}"
        )

    if install_error:
        raise install_error
    raise FileNotFoundError("Không tìm thấy chromedriver khả dụng")

def _prime_tiktok_domain(driver, profile_name):
    driver.get(TIKTOK_PRIME_URL)
    _wait_document_ready(driver, timeout=4.0)

def _build_requests_proxy_config(config):
    if not config.get('use_proxy', False):
        return None
    proxy_data = parse_proxy_string(config.get('proxy_string', ''))
    if not proxy_data:
        return None
    proxy_url = f"http://{proxy_data['user']}:{proxy_data['pass']}@{proxy_data['ip']}:{proxy_data['port']}"
    return {'http': proxy_url, 'https': proxy_url}

def _active_webdriver_module():
    if not DEBUG_SELENIUM_WIRE:
        return webdriver
    from seleniumwire import webdriver as seleniumwire_webdriver
    return seleniumwire_webdriver

def _build_seleniumwire_options(proxy_data):
    if not DEBUG_SELENIUM_WIRE or not proxy_data:
        return {}
    proxy_url = f"http://{proxy_data['user']}:{proxy_data['pass']}@{proxy_data['ip']}:{proxy_data['port']}"
    return {
        'proxy': {
            'http': proxy_url,
            'https': proxy_url,
            'no_proxy': 'localhost,127.0.0.1'
        },
        'verify_ssl': False,
        'disable_capture': False,
    }

def _proxy_extension_dir(config, proxy_data):
    profile_path = Path(config['chrome_profile'])
    ext_key = f"{proxy_data['ip']}_{proxy_data['port']}"
    safe_key = re.sub(r'[^A-Za-z0-9_.-]+', '_', ext_key)
    return profile_path.parent / f"ProxyExtension_{safe_key}"

def _write_proxy_auth_extension(config, proxy_data):
    ext_dir = _proxy_extension_dir(config, proxy_data)
    os.makedirs(ext_dir, exist_ok=True)

    manifest = {
        "name": "TikTok Auto Proxy Auth",
        "version": "1.0",
        "manifest_version": 3,
        "permissions": ["proxy", "webRequest", "webRequestAuthProvider", "storage"],
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "background.js"}
    }
    background = f'''const proxyConfig = {{
    mode: "fixed_servers",
    rules: {{
        singleProxy: {{
            scheme: "http",
            host: "{proxy_data['ip']}",
            port: parseInt({proxy_data['port']})
        }},
        bypassList: ["localhost", "127.0.0.1"]
    }}
}};

function recordState(update) {{
    chrome.storage.local.set(Object.assign({{ updatedAt: Date.now() }}, update));
}}

chrome.proxy.settings.set({{value: proxyConfig, scope: "regular"}}, function() {{
    recordState({{
        proxySet: true,
        proxyHost: "{proxy_data['ip']}",
        proxyPort: "{proxy_data['port']}",
        lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : ""
    }});
}});

chrome.webRequest.onAuthRequired.addListener(
    function(details) {{
        recordState({{
            lastAuthAt: Date.now(),
            lastAuthUrl: details.url || "",
            isProxy: !!details.isProxy
        }});
        return {{
            authCredentials: {{username: "{proxy_data['user']}", password: "{proxy_data['pass']}"}}
        }};
    }},
    {{urls: ["<all_urls>"]}},
    ["blocking"]
);
'''
    with open(ext_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(ext_dir / "background.js", "w", encoding="utf-8") as f:
        f.write(background)
    return ext_dir

def _apply_chrome_proxy_options(chrome_options, config, proxy_data):
    if not proxy_data or DEBUG_SELENIUM_WIRE:
        return None
    if proxy_data.get('user') and proxy_data.get('pass'):
        if _using_orbita_browser():
            chrome_options.add_argument(f"--proxy-server=http://{proxy_data['ip']}:{proxy_data['port']}")
            chrome_options.add_argument(f"--host-resolver-rules=MAP * 0.0.0.0 , EXCLUDE {proxy_data['ip']}")
            chrome_options.add_argument(f"--gologing_proxy_server_username={proxy_data['user']}")
            chrome_options.add_argument(f"--gologing_proxy_server_password={proxy_data['pass']}")
            return "orbita-proxy-auth"
        ext_dir = _write_proxy_auth_extension(config, proxy_data)
        chrome_options.add_argument(f"--disable-extensions-except={ext_dir}")
        chrome_options.add_argument(f"--load-extension={ext_dir}")
        return str(ext_dir)
    chrome_options.add_argument(f"--proxy-server=http://{proxy_data['ip']}:{proxy_data['port']}")
    return None

def _is_google_chrome_driver(driver):
    try:
        product = driver.execute_cdp_cmd('Browser.getVersion', {}).get('product', '')
        return str(product).startswith('Chrome/')
    except Exception:
        return False

def _has_extension_target(driver):
    try:
        targets = driver.execute_cdp_cmd('Target.getTargets', {}).get('targetInfos', [])
        return any(str(t.get('url', '')).startswith('chrome-extension://') for t in targets)
    except Exception:
        return False

def _warn_if_auth_extension_blocked(profile_name, driver, proxy_data):
    if not proxy_data or not proxy_data.get('user') or not proxy_data.get('pass') or DEBUG_SELENIUM_WIRE:
        return
    if _using_orbita_browser():
        update_status(f"[{profile_name}] [DEBUG] Orbita proxy auth flags đang được sử dụng.")
        return
    if _has_extension_target(driver):
        update_status(f"[{profile_name}] [DEBUG] Proxy auth extension đã được Chrome load.")
        return
    if not _using_bundled_chrome() and _is_google_chrome_driver(driver):
        update_status(
            f"[{profile_name}] [WARN] Google Chrome hiện tại có thể chặn --load-extension; "
            "proxy auth extension có thể không hoạt động. Nên dùng proxy whitelist/no-auth hoặc bật debug Selenium Wire."
        )
    elif _using_bundled_chrome():
        update_status(f"[{profile_name}] [WARN] Chưa phát hiện proxy auth extension trong Chrome target list; sẽ xác nhận bằng bước check IP.")

def _build_requests_session_from_driver(driver, config):
    session = requests.Session()
    proxies = _build_requests_proxy_config(config)
    if proxies:
        session.proxies.update(proxies)

    user_agent = None
    try:
        user_agent = driver.execute_script("return navigator.userAgent")
        if user_agent:
            session.headers['User-Agent'] = user_agent
    except Exception:
        pass

    try:
        for cookie in driver.get_cookies():
            session.cookies.set(
                cookie.get('name'),
                cookie.get('value'),
                domain=cookie.get('domain'),
                path=cookie.get('path', '/'),
            )
    except Exception:
        pass

    return session, user_agent, proxies

def _clear_wire_requests(driver):
    if not DEBUG_SELENIUM_WIRE:
        return 0
    try:
        del driver.requests
        return 0
    except Exception:
        try:
            return len(driver.requests)
        except Exception:
            return 0

def _request_url_matches_upload(url):
    text = str(url or '').lower()
    keywords = (
        'upload', 'publish', 'commit', 'complete', 'finish', 'chunk', 'part',
        'project', 'video', 'aweme', 'tos', 'byte', 'storage', 'creator'
    )
    return any(k in text for k in keywords)

def _truncate_text(value, max_len=REQUEST_TRACE_PREVIEW_BYTES):
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= max_len else text[:max_len] + '...'

def _preview_body_bytes(body, max_len=REQUEST_TRACE_PREVIEW_BYTES):
    if not body:
        return None
    try:
        sample = body[:max_len]
        return sample.decode('utf-8', errors='replace')
    except Exception:
        return _truncate_text(body, max_len=max_len)

def _normalize_headers(headers):
    cleaned = {}
    for key, value in headers.items():
        k = str(key)
        if k.lower() in ('cookie', 'authorization', 'proxy-authorization'):
            continue
        cleaned[k] = _truncate_text(value, max_len=300)
    return cleaned

def _serialize_wire_request(req):
    response = getattr(req, 'response', None)
    item = {
        'method': getattr(req, 'method', ''),
        'url': getattr(req, 'url', ''),
        'request_headers': _normalize_headers(getattr(req, 'headers', {})),
        'request_body_size': len(req.body or b'') if hasattr(req, 'body') and req.body else 0,
        'request_body_preview': _preview_body_bytes(getattr(req, 'body', None)),
        'status_code': getattr(response, 'status_code', None),
        'response_headers': _normalize_headers(getattr(response, 'headers', {})) if response else {},
        'response_body_size': len(response.body or b'') if response and getattr(response, 'body', None) else 0,
        'response_body_preview': _preview_body_bytes(getattr(response, 'body', None)) if response else None,
    }
    return item

def _collect_upload_wire_trace(driver, start_index=0, limit=REQUEST_TRACE_MAX_REQUESTS):
    if not DEBUG_SELENIUM_WIRE:
        return []
    try:
        requests_list = list(driver.requests)[start_index:]
    except Exception:
        return []

    filtered = []
    for req in requests_list:
        url = getattr(req, 'url', '')
        response = getattr(req, 'response', None)
        if _request_url_matches_upload(url):
            filtered.append(_serialize_wire_request(req))
            continue
        if response and _request_url_matches_upload(getattr(response, 'headers', {}).get('location', '')):
            filtered.append(_serialize_wire_request(req))

    return filtered[-limit:]

def _extract_request_candidate_urls(trace_items):
    candidates = []
    seen = set()
    for item in trace_items:
        url = item.get('url', '')
        method = item.get('method', '')
        status = item.get('status_code')
        key = (method, url)
        if not url or key in seen:
            continue
        seen.add(key)
        candidates.append({
            'method': method,
            'url': url,
            'status_code': status,
        })
    return candidates

def _prepare_request_assist_context(profile_name, driver):
    config = profiles[profile_name]['config']
    session, user_agent, proxies = _build_requests_session_from_driver(driver, config)
    start_index = _clear_wire_requests(driver)
    return {
        'start_index': start_index,
        'cookie_names': list(session.cookies.keys()),
        'cookie_count': len(session.cookies),
        'proxy_enabled': bool(proxies),
        'proxy_hosts': sorted(list(proxies.keys())) if proxies else [],
        'user_agent': user_agent,
        'wire_debug_enabled': DEBUG_SELENIUM_WIRE,
    }

def _save_request_trace(profile_name, file_name, outcome, request_context, trace_items):
    if not trace_items:
        return None

    os.makedirs(REQUEST_TRACE_DIR, exist_ok=True)
    safe_profile = re.sub(r'[^\w\- ]+', '_', profile_name).strip() or 'profile'
    safe_video = re.sub(r'[^\w\- .]+', '_', file_name).strip() or 'video'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = Path(REQUEST_TRACE_DIR) / f"{safe_profile}__{timestamp}__{safe_video}.json"

    payload = {
        'profile_name': profile_name,
        'video_name': file_name,
        'captured_at': datetime.now().isoformat(timespec='seconds'),
        'outcome': outcome,
        'request_context': request_context,
        'candidate_urls': _extract_request_candidate_urls(trace_items),
        'trace': trace_items,
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return str(out_path)

def _capture_request_trace(profile_name, driver, file_name, request_context, outcome):
    if not request_context:
        return None
    trace_items = _collect_upload_wire_trace(driver, start_index=request_context.get('start_index', 0))
    trace_path = _save_request_trace(profile_name, file_name, outcome, request_context, trace_items)
    if trace_path:
        update_status(f"[{profile_name}] [REQ] Đã lưu trace request: {trace_path}")
    return trace_path

def _append_failed_upload_log(profile_name, file_name, reason, trace_path=None, outcome='failed'):
    try:
        _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=reason)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"{timestamp} | profile={profile_name} | outcome={outcome} | file={file_name} | reason={reason}"
        if trace_path:
            line += f" | trace={trace_path}"
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

def _cookie_metadata_matches(config):
    cookie_hash = _get_cookie_hash(config.get("cookie_str", ""))
    if not cookie_hash:
        return False
    return (
        config.get('cookie_hash') == cookie_hash and
        os.path.abspath(str(config.get('cookies_last_injected_profile_path', ''))) == os.path.abspath(str(config.get('chrome_profile', '')))
    )

def _save_cookie_injection_metadata(profile_name, cookie_str):
    try:
        config = profiles[profile_name]['config']
        config['cookie_hash'] = _get_cookie_hash(cookie_str)
        config['cookies_last_injected_at'] = datetime.now(timezone.utc).isoformat()
        config['cookies_last_injected_profile_path'] = config.get('chrome_profile', '')
        save_configs()
    except Exception:
        pass

def _has_tiktok_auth_cookie(driver):
    try:
        names = {str(cookie.get('name', '')).lower() for cookie in driver.get_cookies()}
        auth_names = {'sessionid', 'sessionid_ss', 'sid_tt', 'sid_guard', 'uid_tt', 'uid_tt_ss'}
        return bool(names & auth_names)
    except Exception:
        return False

def _has_upload_page_signal(driver):
    selectors = (
        "div[data-e2e='select_video_container']",
        "input[type='file']",
        "div[data-e2e='upload-card']",
        "div[data-e2e='video-caption-editor']",
        "button[data-e2e='post_video_button']",
    )
    for selector in selectors:
        try:
            for elem in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if elem.is_displayed():
                        return True
                except Exception:
                    return True
        except Exception:
            continue
    return False


def _find_running_profile_with_same_data_dir(profile_name):
    profile = profiles.get(profile_name, {})
    target = normalize_profile_path(profile.get('config', {}).get('chrome_profile', ''))
    if not target:
        return None
    for other_name, other in profiles.items():
        if other_name == profile_name or not other.get('running', False):
            continue
        other_path = normalize_profile_path(other.get('config', {}).get('chrome_profile', ''))
        if other_path == target:
            return other_name
    return None


def _profile_browser_process_count(profile_name):
    profile_path = profiles.get(profile_name, {}).get('config', {}).get('chrome_profile', '')
    count = 0
    try:
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                name = str(proc.info.get('name') or '').lower()
                if name in ('chrome.exe', 'chromedriver.exe', 'chrome', 'chromedriver') and process_uses_profile(
                    proc.info.get('cmdline'), profile_path
                ):
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass
    return count


def _log_webdriver_failure(profile_name, driver, error, stage, video_name=''):
    error_kind = classify_webdriver_error(error)
    current_url = '<unavailable>'
    browser_version = ''
    driver_pid = None
    try:
        current_url = driver.current_url or '<empty>'
    except Exception:
        pass
    try:
        browser_version = str((driver.capabilities or {}).get('browserVersion', ''))
    except Exception:
        pass
    try:
        driver_pid = driver.service.process.pid
    except Exception:
        pass
    try:
        memory_percent = psutil.virtual_memory().percent
        cpu_percent = psutil.cpu_percent(interval=None)
    except Exception:
        memory_percent = cpu_percent = 'unknown'
    process_count = _profile_browser_process_count(profile_name)
    detail = (
        f"[{profile_name}] [ERROR] WebDriver stage={stage}; kind={error_kind}; "
        f"exception={type(error).__name__}; message={error}; video={video_name or '-'}; "
        f"url={current_url}; browser={browser_version or 'unknown'}; driver_pid={driver_pid or 'unknown'}; "
        f"profile_processes={process_count}; ram={memory_percent}%; cpu={cpu_percent}%"
    )
    logging.warning(detail)
    update_status(detail)
    return error_kind

def _wait_for_upload_page_signal(driver, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _has_upload_page_signal(driver):
            return True
        time.sleep(0.25)
    return False

def _inject_cookies(driver, cookies):
    success = 0
    for cookie in cookies:
        try:
            driver.add_cookie(cookie)
            success += 1
        except Exception:
            pass
    return success

def _prepare_tiktok_cookies(driver, profile_name, config, require_upload_ready=False):
    cookie_str = config.get("cookie_str", "")
    cookies = parse_cookie(cookie_str)
    if not cookies:
        _set_profile_ui(profile_name, login='Chưa có cookie')
        return False

    metadata_matches = _cookie_metadata_matches(config)
    if require_upload_ready and metadata_matches:
        try:
            driver.get(TIKTOK_UPLOAD_URL)
            _wait_document_ready(driver, timeout=3.0)
            if metadata_matches and _wait_for_upload_page_signal(driver, timeout=3.0):
                _set_profile_ui(profile_name, login='Đã đăng nhập')
                update_status(f"[{profile_name}] Đã đăng nhập TikTok, bỏ qua nạp cookie.")
                return True
            if "/login" in (driver.current_url or "").lower():
                _set_profile_ui(profile_name, login='Đang nạp lại')
                update_status(f"[{profile_name}] TikTok yêu cầu đăng nhập lại, đang nạp lại cookie.")
            else:
                _set_profile_ui(profile_name, login='Đã có cookie')
                update_status(f"[{profile_name}] Upload page chưa sẵn sàng nhanh, giữ cookie hiện tại và tiếp tục mở trang upload.")
                return False
        except Exception:
            _set_profile_ui(profile_name, login='Đã có cookie')
            update_status(f"[{profile_name}] Chưa xác nhận được upload page nhanh, giữ cookie hiện tại và thử mở lại trang upload.")
            return False
    else:
        try:
            driver.get(TIKTOK_BASE_URL)
            _wait_document_ready(driver, timeout=3.0)
            if metadata_matches and _has_tiktok_auth_cookie(driver):
                _set_profile_ui(profile_name, login='Đã có cookie')
                update_status(f"[{profile_name}] Đã có cookie TikTok trong Chrome profile, bỏ qua nạp cookie.")
                return False
        except Exception:
            pass

    if metadata_matches:
        _set_profile_ui(profile_name, login='Đang nạp lại')
        update_status(f"[{profile_name}] Không xác nhận được đăng nhập TikTok, đang nạp lại cookie.")
    else:
        _set_profile_ui(profile_name, login='Đang nạp cookie')
        update_status(f"[{profile_name}] Phát hiện cookie mới hoặc profile đổi, đang nạp cookie.")

    driver.get(TIKTOK_BASE_URL)
    _wait_document_ready(driver, timeout=3.0)
    success = _inject_cookies(driver, cookies)
    if success:
        _save_cookie_injection_metadata(profile_name, cookie_str)
        _set_profile_ui(profile_name, login='Đã nạp cookie')
        update_status(f"[{profile_name}] Đã nạp {success}/{len(cookies)} cookie và lưu trạng thái.")
        if require_upload_ready:
            try:
                driver.get(TIKTOK_UPLOAD_URL)
                _wait_document_ready(driver, timeout=3.0)
                if "/login" in (driver.current_url or "").lower():
                    _set_profile_ui(profile_name, login='Cần đăng nhập lại', last_error='TikTok yêu cầu đăng nhập lại')
                    update_status(f"[{profile_name}] Cookie đã nạp vào Chrome nhưng bị TikTok từ chối.")
                    return False
                return _wait_for_upload_page_signal(driver, timeout=3.0)
            except Exception:
                return False
    else:
        _set_profile_ui(profile_name, login='Cookie lỗi', last_error='Không nạp được cookie')
        update_status(f"[{profile_name}] [WARN] Không nạp được cookie nào vào Chrome profile.")
    return success > 0

def _export_live_cookies_to_config(driver, profile_name):
    if not is_driver_valid(driver):
        return False
    try:
        live_cookies = driver.get_cookies()
        if live_cookies:
            cookie_json = json.dumps(live_cookies, ensure_ascii=False)
            profiles[profile_name]['config']['cookie_str'] = cookie_json
            _save_cookie_injection_metadata(profile_name, cookie_json)
            save_configs()
            return True
    except Exception:
        pass
    return False


def _snapshot_live_cookies_before_post(driver, profile_name):
    if _export_live_cookies_to_config(driver, profile_name):
        update_status(f"[{profile_name}] [DEBUG] Đã lưu cookie live trước khi bấm Đăng.")
        return True
    update_status(f"[{profile_name}] [WARN] Không lưu được cookie live trước khi bấm Đăng.")
    return False


def _capture_tiktok_cookies_worker(profile_name):
    cfg = profiles[profile_name]['config']
    driver = None
    try:
        probe_config = dict(cfg)
        probe_config['headless'] = True
        proxy_data = parse_proxy_string(cfg.get('proxy_string', '')) if cfg.get('use_proxy', False) else None
        geo_changed = _refresh_profile_geoip(profile_name, cfg, proxy_data)
        if geo_changed:
            save_configs()
        probe_config['fingerprint'] = cfg.get('fingerprint', {})
        options = _build_fast_chrome_options(probe_config, block_images=False)
        seleniumwire_options = _build_seleniumwire_options(proxy_data) if proxy_data else {}
        _apply_chrome_proxy_options(options, cfg, proxy_data)
        driver_path = _ensure_profile_driver(profile_name)
        driver_module = _active_webdriver_module()
        for launch_attempt in range(2):
            try:
                service = Service(driver_path)
                if DEBUG_SELENIUM_WIRE:
                    driver = driver_module.Chrome(service=service, options=options, seleniumwire_options=seleniumwire_options)
                else:
                    driver = driver_module.Chrome(service=service, options=options)
                break
            except Exception as error:
                if launch_attempt == 0 and _is_driver_version_mismatch_error(error):
                    _invalidate_chromedriver_cache("cookie capture driver/browser version mismatch")
                    try:
                        _profile_driver_path(profile_name).unlink(missing_ok=True)
                    except Exception:
                        pass
                    driver_path = _ensure_profile_driver(profile_name)
                    continue
                raise
        driver.implicitly_wait(0)
        driver.set_page_load_timeout(PAGELOAD_TIMEOUT)
        driver.set_script_timeout(SCRIPT_TIMEOUT)
        _inject_fingerprint_js(driver, profile_name)
        if proxy_data:
            is_match, current_ip = verify_proxy_ip(driver, proxy_data['ip'])
            if not is_match:
                raise RuntimeError(f"Proxy mismatch khi lấy cookie: {current_ip} != {proxy_data['ip']}")
        _open_upload_page(driver, profile_name)
        if not _has_tiktok_auth_cookie(driver):
            raise TikTokLoginRequiredError('TikTok chưa có auth cookie hợp lệ trong User Data.')
        live_cookies = driver.get_cookies()
        if not live_cookies:
            raise TikTokLoginRequiredError('TikTok không trả về cookie nào sau khi xác minh đăng nhập.')
        cookie_json = json.dumps(live_cookies, ensure_ascii=False)
        cfg['cookie_str'] = cookie_json
        _save_cookie_injection_metadata(profile_name, cookie_json)
        cfg['cookies_last_captured_at'] = datetime.now(timezone.utc).isoformat()
        save_configs()
        _set_profile_ui(profile_name, login='Đã đăng nhập', browser='Đã đóng', last_error='')
        update_status(f"[{profile_name}] Đã lấy và lưu {len(live_cookies)} cookie TikTok từ session Chrome.")
        return True
    except TikTokLoginRequiredError as e:
        _set_profile_ui(profile_name, login='Cần đăng nhập lại', last_error=str(e))
        update_status(f"[{profile_name}] Không lấy cookie: {e}")
        return False
    except Exception as e:
        _set_profile_ui(profile_name, last_error=str(e))
        update_status(f"[{profile_name}] Lỗi lấy cookie TikTok: {e}")
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
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
    if profile.get('running') or profile.get('uploading'):
        messagebox.showwarning('Lấy Cookie', 'Hãy Stop profile và đóng browser trước khi lấy cookie.')
        return
    if is_driver_valid(profile.get('manual_driver')):
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


def reset_fingerprint():
    if not _license_guard():
        return
    sel = tree.selection()
    if not sel:
        messagebox.showwarning('Reset Fingerprint', 'Hãy chọn một profile.')
        return
    profile_name = tree.item(sel[0])['values'][0]
    profile = profiles.get(profile_name)
    if not profile or profile.get('running') or profile.get('session_busy') or is_driver_valid(profile.get('manual_driver')):
        messagebox.showwarning('Reset Fingerprint', 'Hãy Stop profile và đóng browser trước khi reset fingerprint.')
        return
    if not messagebox.askyesno(
        'Reset Fingerprint',
        f"Tạo fingerprint mới cho '{profile_name}'?\n\nSession và cookie hiện tại sẽ được giữ lại.",
    ):
        return
    cfg = profile['config']
    seed = profile_name + str(cfg.get('cookie_str', '')) + str(time.time_ns())
    cfg['fingerprint'] = _generate_fingerprint(seed=seed)
    cfg['fingerprint_reset_at'] = datetime.now(timezone.utc).isoformat()
    save_configs()
    _set_profile_ui(profile_name, last_error='')
    update_status(f'[{profile_name}] Đã reset fingerprint; session/cookie được giữ lại.')


def _clean_browser_worker(profile_name):
    profile = profiles[profile_name]
    cfg = profile['config']
    driver = profile.get('driver')
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
        profile['driver'] = None
    manual_driver = profile.get('manual_driver')
    if manual_driver:
        try:
            manual_driver.quit()
        except Exception:
            pass
        profile['manual_driver'] = None
    kill_stale_chrome_processes(profile_name)
    clear_profile_directory(cfg['chrome_profile'])
    for key in ('cookie_str', 'cookie_hash', 'cookies_last_injected_at', 'cookies_last_captured_at', 'cookies_last_injected_profile_path'):
        cfg.pop(key, None)
    save_configs()
    _set_profile_ui(profile_name, login='Chưa có cookie', browser='Đã làm sạch', upload='Chờ video', last_error='')
    update_status(f'[{profile_name}] Đã làm sạch User Data và cookie; giữ nguyên video, proxy, fingerprint và driver.')


def clean_browser():
    if not _license_guard():
        return
    sel = tree.selection()
    if not sel:
        messagebox.showwarning('Làm sạch Browser', 'Hãy chọn một profile.')
        return
    profile_name = tree.item(sel[0])['values'][0]
    profile = profiles.get(profile_name)
    if not profile or profile.get('running') or profile.get('uploading') or profile.get('session_busy') or is_driver_valid(profile.get('manual_driver')):
        messagebox.showwarning('Làm sạch Browser', 'Hãy Stop profile và đóng browser trước khi làm sạch browser.')
        return
    if not messagebox.askyesno(
        'Làm sạch Browser',
        f"Xóa toàn bộ User Data và cookie của '{profile_name}'?\n\nVideo, proxy, fingerprint và driver sẽ được giữ lại.",
    ):
        return

    def worker():
        with _profile_operation_lock(profile_name):
            profile['session_busy'] = True
            try:
                _set_profile_ui(profile_name, browser='Đang làm sạch')
                _clean_browser_worker(profile_name)
            except Exception as e:
                _set_profile_ui(profile_name, browser='Bị lỗi', last_error=str(e))
                update_status(f'[{profile_name}] Lỗi làm sạch browser: {e}')
            finally:
                profile['session_busy'] = False

    threading.Thread(target=worker, daemon=True).start()

def _get_network_upload_signal(driver, request_start_index=0):
    trace_items = _collect_upload_wire_trace(driver, start_index=request_start_index, limit=25)
    for item in reversed(trace_items):
        url = item.get('url', '').lower()
        status = item.get('status_code')
        if not status or status >= 500:
            continue
        short_url = _truncate_text(item.get('url', ''), max_len=120)
        if any(k in url for k in ('commit', 'complete', 'finish')) and 200 <= status < 400:
            return True, f"network-ready:{short_url}", True
        if 200 <= status < 500:
            return True, f"network:{short_url}", False
    return False, None, False

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

def _license_dialog(on_success):
    dlg = ctk.CTkToplevel(root)
    dlg.title("Kích hoạt License")
    dlg.geometry("460x230")
    dlg.grab_set()
    dlg.focus_force()
    dlg.resizable(False, False)

    ctk.CTkLabel(dlg, text="Nhập License Key để sử dụng công cụ:", font=("", 14, "bold")).pack(pady=(16, 8))
    key_var = ctk.StringVar(value="")

    entry = ctk.CTkEntry(dlg, width=360, textvariable=key_var, placeholder_text="VD: USER-XXXX-YYYY-ZZZZ")
    entry.pack(pady=(0, 10))
    entry.focus_set()

    msg_var = ctk.StringVar(value="")
    msg_label = ctk.CTkLabel(dlg, textvariable=msg_var, text_color="#b91c1c")
    msg_label.pack(pady=(0, 4))

    def do_check():
        global LICENSE_OK, LICENSE_INFO, LICENSE_KEY
        key = key_var.get().strip()
        if not key:
            msg_var.set("Vui lòng nhập License Key.")
            return
        msg_var.set("Đang kiểm tra...")
        dlg.update()
        ok, info, message = check_license_online_or_cache(key)
        if ok:
            LICENSE_OK = True
            LICENSE_INFO = info
            LICENSE_KEY = key
            msg_var.set("")
            dlg.destroy()
            on_success()
        else:
            LICENSE_OK = False
            msg_var.set(message)

    def on_close():
        if not LICENSE_OK:
            try: root.destroy()
            except Exception: os._exit(0)

    btn = ctk.CTkButton(dlg, text="Kích hoạt", command=do_check, width=120)
    btn.pack(pady=10)
    dlg.protocol("WM_DELETE_WINDOW", on_close)
    dlg.bind("<Return>", lambda _e: do_check())

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

    root.after(100, lambda: _license_dialog(on_success=_after_ok))

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
            root.after(0, lambda: _license_dialog(on_success=lambda: _set_ui_enabled(True)))
        except Exception:
            pass

def _license_guard():
    if not LICENSE_REQUIRED: return True
    if LICENSE_OK: return True
    messagebox.showerror("License", "Bạn chưa kích hoạt License.")
    return False

# =========================
# Tiện ích UI
# =========================
def _treeview_sort_column(tv, col, reverse):
    try:
        data = [(tv.set(k, col), k) for k in tv.get_children('')]
        if col == 'status':
            key_map = {'Running': 1, 'Stopped': 0, 'Đang chạy': 1, 'Đã dừng': 0}
            data.sort(key=lambda t: key_map.get(t[0], -1), reverse=reverse)
        elif col == 'headless':
            data.sort(key=lambda t: str(t[0]).lower() in ('có', 'true', 'yes'), reverse=reverse)
        elif col == 'limit':
            def sort_key_limit(t):
                val_str = str(t[0]).lower()
                if val_str in ('không', 'no', '0'): return 0
                try: return float(val_str)
                except Exception: return float('inf') 
            data.sort(key=sort_key_limit, reverse=reverse)
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
def save_configs():
    configs = build_configs_payload(profiles, projects)
    save_configs_file(CONFIGS_FILE, configs)
    update_profile_list()
    update_project_dropdown()

def load_configs():
    try:
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
        
        update_project_dropdown()
        selected_project_var.set(ALL_OPTION)
        update_profile_list()
        _migrate_profile_drivers()
    except FileNotFoundError:
        projects['Mặc định'] = set()
        update_project_dropdown()
        selected_project_var.set(ALL_OPTION)


def _migrate_profile_drivers():
    changed = False
    for profile_name, profile in profiles.items():
        before = profile['config'].get('chromedriver_path')
        try:
            _ensure_profile_driver(profile_name)
            if profile['config'].get('chromedriver_path') != before:
                changed = True
        except Exception as e:
            update_status(f"[{profile_name}] [WARN] Không thể tạo ChromeDriver riêng khi migration: {e}")
    if changed:
        save_configs()

# --- BẢNG THỐNG KÊ MỚI ---
def show_statistics_board():
    if not _license_guard(): return
    
    dlg = ctk.CTkToplevel(root)
    dlg.title("Thống kê hoạt động")
    dlg.geometry("500x400")
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

# =========================
# Selenium Driver (Optimized with Selenium Wire)
# =========================
# ------------------------------

def _build_fast_chrome_options(config, block_images=True, force_visible=False):
    chrome_options = Options()
    if os.environ.get('UPLOAD_CAPTURE_NETWORK') == '1' or os.environ.get('UPLOAD_TEST_MODE') == '1':
        chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    chrome_binary = _find_bundled_chrome_executable()
    if chrome_binary:
        chrome_options.binary_location = chrome_binary
    chrome_options.add_argument(f"--user-data-dir={config['chrome_profile']}")
    
    fp = ensure_fingerprint_defaults(
        config.get('fingerprint', _generate_fingerprint(config.get('cookie_str', ''))),
        seed=config.get('cookie_str', ''),
    )
    config['fingerprint'] = fp
    _apply_fingerprint_to_options(chrome_options, fp)
    for argument in chrome_environment_arguments(fp):
        chrome_options.add_argument(argument)
    
    if force_visible or not config.get('headless', True):
        chrome_options.add_argument("--start-maximized")
    else:
        chrome_options.add_argument("--headless=new")
    
    # Giữ Chrome gần hành vi thật nhất; tránh flag can thiệp mạng/rendering làm TikTok load bất thường.
    chrome_options.add_argument("--remote-debugging-port=0")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage") 
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--mute-audio")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--force-color-profile=srgb")
    chrome_options.add_argument("--disable-features=TranslateUI,ChromeWhatsNewUI,MediaRouter")
    
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    
    prefs = {
        "profile.default_content_setting_values.notifications": 2,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False
    }
    prefs.update(chrome_environment_preferences(fp))
    if block_images:
        prefs["profile.managed_default_content_settings.images"] = 2
    else:
        prefs["profile.managed_default_content_settings.images"] = 1
    chrome_options.add_experimental_option("prefs", prefs)
    chrome_options.page_load_strategy = 'none'
    return chrome_options

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

def kill_stale_chrome_processes(profile_name):
    target_dir = profiles[profile_name]['config']['chrome_profile']
    killed_count = 0
    
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if not proc.is_running(): continue
                name = proc.info['name'].lower()
                cmdline = proc.info['cmdline']

                if name in ('chrome.exe', 'chromedriver.exe', 'chrome', 'chromedriver'):
                    if cmdline:
                        if process_uses_profile(cmdline, target_dir):
                            try:
                                proc.terminate() 
                                proc.wait(timeout=3)
                            except psutil.TimeoutExpired:
                                proc.kill() 
                            except Exception:
                                pass
                            killed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
        if killed_count > 0:
            time.sleep(0.5) 
    except Exception as e:
        logging.warning(f"[{profile_name}] Lỗi khi kill process: {e}")

def _init_driver_inner(profile_name, service, chrome_options, seleniumwire_options):
    update_status(f"[{profile_name}] [DEBUG] Đang gọi webdriver.Chrome()...")
    driver_module = _active_webdriver_module()
    if DEBUG_SELENIUM_WIRE:
        driver = driver_module.Chrome(
            service=service,
            options=chrome_options,
            seleniumwire_options=seleniumwire_options
        )
    else:
        driver = driver_module.Chrome(service=service, options=chrome_options)
    update_status(f"[{profile_name}] [DEBUG] webdriver.Chrome() đã trả về driver object")
    return driver

def _init_driver_with_timeout(profile_name, service, chrome_options, seleniumwire_options, timeout=DRIVER_INIT_TIMEOUT):
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_init_driver_inner, profile_name, service, chrome_options, seleniumwire_options)
    try:
        driver = future.result(timeout=timeout)
        return driver
    except FuturesTimeout:
        update_status(f"[{profile_name}] [ERROR] Khởi tạo driver quá {timeout}s, hủy tiến trình và thử lại...")
        executor.shutdown(wait=False, cancel_futures=True)
        raise TimeoutError(f"Khởi tạo driver quá {timeout}s")
    except Exception as e:
        executor.shutdown(wait=False)
        raise e
    finally:
        executor.shutdown(wait=False)

def ensure_driver(profile_name):
    global GLOBAL_DRIVER_PATH
    total_start = time.perf_counter()
    driver = profiles[profile_name]['driver']
    if is_driver_valid(driver):
        return driver
    
    config = profiles[profile_name]['config']
    
    if driver:
        try: driver.quit()
        except Exception: pass
    profiles[profile_name]['driver'] = None
    
    step_start = time.perf_counter()
    kill_stale_chrome_processes(profile_name)
    clean_chrome_lock_files(config['chrome_profile'])
    _timing_log(profile_name, "cleanup", step_start)
    
    max_attempts = DRIVER_INIT_RETRIES 
    driver_refreshed_for_mismatch = False
    
    for attempt in range(max_attempts):
        driver = None
        try:
            attempt_start = time.perf_counter()
            step_start = time.perf_counter()
            if not check_system_resources(profile_name):
                update_status(f"[{profile_name}] Tài nguyên thấp. Tạm nghỉ 5s.")
                time.sleep(5)
                if attempt == max_attempts - 1: raise Exception("System Resource Low")
                continue
            _timing_log(profile_name, "resource_check", step_start)

            update_status(f"[{profile_name}] Khởi tạo Driver (Lần {attempt+1})...")
            _set_profile_ui(profile_name, status='Đang khởi động', browser='Đang mở', upload='Chờ video', last_error='')
            
            # --- SETUP PROXY ---
            proxy_data = None
            seleniumwire_options = {}
            
            if config.get('use_proxy', False):
                step_start = time.perf_counter()
                _set_profile_ui(profile_name, proxy='Đang kiểm tra')
                proxy_str = config.get('proxy_string', '')
                proxy_data = parse_proxy_string(proxy_str)
                if proxy_data:
                    seleniumwire_options = _build_seleniumwire_options(proxy_data)
                    if DEBUG_SELENIUM_WIRE:
                        mode = "Selenium Wire debug"
                    elif proxy_data.get('user') and proxy_data.get('pass') and _using_orbita_browser():
                        mode = "Orbita proxy auth"
                    elif proxy_data.get('user') and proxy_data.get('pass'):
                        mode = "Chrome proxy extension"
                    else:
                        mode = "Chrome native proxy"
                    update_status(f"[{profile_name}] [DEBUG] Config Proxy ({mode}): {proxy_data['ip']}")
                else:
                    _set_profile_ui(profile_name, proxy='Sai định dạng', last_error='Proxy sai định dạng')
                    update_status(f"[{profile_name}] Cảnh báo: Proxy sai định dạng.")
            else:
                _set_profile_ui(profile_name, proxy='Tắt')
            if config.get('use_proxy', False):
                _timing_log(profile_name, "proxy_config", step_start)

            geo_changed = _refresh_profile_geoip(profile_name, config, proxy_data)
            if geo_changed:
                save_configs()

            # --- DRIVER RIÊNG THEO PROFILE ---
            step_start = time.perf_counter()
            driver_path = _ensure_profile_driver(profile_name)
            update_status(f"[{profile_name}] [DEBUG] ChromeDriver path: {driver_path}")
            chrome_path = _find_preferred_chrome_executable()
            update_status(f"[{profile_name}] [DEBUG] Browser ({_browser_mode_label()}): {chrome_path or 'không tìm thấy'}")
            _timing_log(profile_name, "driver_browser_resolve", step_start)
            
            service = Service(driver_path)
            
            # Setup standard Chrome Options
            step_start = time.perf_counter()
            chrome_options = _build_fast_chrome_options(config, block_images=False)
            proxy_ext_dir = _apply_chrome_proxy_options(chrome_options, config, proxy_data)
            if proxy_ext_dir:
                if proxy_ext_dir == "orbita-proxy-auth":
                    update_status(f"[{profile_name}] [DEBUG] Đã cấu hình Orbita proxy auth")
                else:
                    update_status(f"[{profile_name}] [DEBUG] Đã nạp proxy extension: {proxy_ext_dir}")
            update_status(f"[{profile_name}] [DEBUG] Đã tạo Chrome options, headless={config.get('headless', True)}")
            _timing_log(profile_name, "build_chrome_options", step_start)
            
            # INIT DRIVER WITH TIMEOUT
            update_status(f"[{profile_name}] [DEBUG] Đang khởi tạo Chrome (timeout {DRIVER_INIT_TIMEOUT}s)...")
            step_start = time.perf_counter()
            driver = _init_driver_with_timeout(
                profile_name,
                service,
                chrome_options,
                seleniumwire_options,
                timeout=DRIVER_INIT_TIMEOUT
            )
            _timing_log(profile_name, "webdriver_chrome", step_start)
            update_status(f"[{profile_name}] [DEBUG] Chrome đã khởi tạo xong, đang cấu hình...")
            _set_profile_ui(profile_name, browser='Đang cấu hình')
            
            step_start = time.perf_counter()
            driver.implicitly_wait(0)
            driver.set_page_load_timeout(PAGELOAD_TIMEOUT)
            driver.set_script_timeout(SCRIPT_TIMEOUT)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            _warn_if_auth_extension_blocked(profile_name, driver, proxy_data)
            update_status(f"[{profile_name}] [DEBUG] Đã cấu hình driver xong")

            _inject_fingerprint_js(driver, profile_name)
            _enable_aux_resource_blocking(driver, profile_name)
            _timing_log(profile_name, "driver_configure", step_start)

            # --- VERIFY PROXY IP ---
            if proxy_data:
                step_start = time.perf_counter()
                cached_proxy = _get_cached_proxy_ok(profile_name, proxy_data)
                if cached_proxy:
                    remaining = max(0, int((PROXY_OK_CACHE_TTL_SECONDS - (time.time() - cached_proxy.get('checked_at', 0))) / 60))
                    current_ip = cached_proxy.get('ip') or proxy_data['ip']
                    _set_profile_ui(profile_name, proxy=f"OK: {current_ip}")
                    update_status(f"[{profile_name}] Proxy OK cache: {current_ip} (bỏ qua check IP, còn hiệu lực ~{remaining} phút)")
                    _timing_log(profile_name, "proxy_check_cached", step_start)
                else:
                    update_status(f"[{profile_name}] [DEBUG] Đang check IP...")
                try:
                    if not cached_proxy:
                        is_match, current_ip = verify_proxy_ip(driver, proxy_data['ip'])
                        if not is_match:
                            _forget_proxy_ok(profile_name, proxy_data)
                            _set_profile_ui(profile_name, proxy='Sai IP', last_error=f"Proxy sai IP: {current_ip}")
                            update_status(f"[{profile_name}] LỖI PROXY: IP THỰC TẾ ({current_ip}) != PROXY ({proxy_data['ip']}).")
                            driver.quit()
                            raise Exception(f"Proxy Mismatch: {current_ip} != {proxy_data['ip']}")
                        else:
                            _remember_proxy_ok(profile_name, proxy_data, current_ip)
                            _set_profile_ui(profile_name, proxy=f"OK: {current_ip}")
                            update_status(f"[{profile_name}] Proxy OK: {current_ip}")
                        _timing_log(profile_name, "proxy_check", step_start)
                except Exception as e:
                     _forget_proxy_ok(profile_name, proxy_data)
                     _set_profile_ui(profile_name, proxy='Proxy lỗi', last_error=str(e))
                     update_status(f"[{profile_name}] Lỗi check Proxy: {e}")
                     if driver: driver.quit()
                     raise

            update_status(f"[{profile_name}] [DEBUG] Thiết lập session TikTok...")
            step_start = time.perf_counter()
            upload_page_ready = _prepare_tiktok_cookies(driver, profile_name, config, require_upload_ready=True)
            if not upload_page_ready:
                _open_upload_page(driver, profile_name)
            _timing_log(profile_name, "tiktok_prepare", step_start)
            profiles[profile_name]['driver'] = driver
            _set_profile_ui(profile_name, status='Đang chạy', browser='Sẵn sàng', upload='Chờ video')
            update_status(f"[{profile_name}] [DEBUG] Driver sẵn sàng!")
            _timing_log(profile_name, "driver_attempt_total", attempt_start)
            _timing_log(profile_name, "driver_ready_total", total_start)
            
            return driver

        except Exception as e:
            if driver: 
                try: driver.quit()
                except: pass

            kill_stale_chrome_processes(profile_name)
            clean_chrome_lock_files(config['chrome_profile'])

            if isinstance(e, TikTokLoginRequiredError):
                _set_profile_ui(
                    profile_name,
                    status='Lỗi',
                    browser='Bị lỗi',
                    login='Cần đăng nhập lại',
                    last_error='Cookie bị TikTok từ chối hoặc đã hết hạn',
                )
                update_status(f"[{profile_name}] Dừng khởi tạo: cookie/session bị TikTok từ chối, không retry browser.")
                raise

            if _is_proxy_init_error(e):
                _set_profile_ui(profile_name, status='Lỗi', browser='Bị lỗi', last_error=str(e))
                update_status(f"[{profile_name}] Lỗi proxy, không retry: {e}")
                raise

            if _is_driver_version_mismatch_error(e) and not driver_refreshed_for_mismatch:
                driver_refreshed_for_mismatch = True
                chrome_path, chrome_version, chrome_major = _get_chrome_version()
                driver_version, driver_major = _get_chromedriver_version(driver_path)
                update_status(
                    f"[{profile_name}] Phát hiện ChromeDriver lệch phiên bản Chrome. "
                    f"Browser={chrome_version or chrome_path}, driver={driver_version or driver_path}. Đang tải lại driver..."
                )
                with driver_install_lock:
                    _invalidate_chromedriver_cache("driver/browser version mismatch")
                    try:
                        _profile_driver_path(profile_name).unlink(missing_ok=True)
                    except Exception:
                        pass
            if attempt == max_attempts - 1:
                _set_profile_ui(profile_name, status='Lỗi', browser='Bị lỗi', last_error=str(e))
                update_status(f"[{profile_name}] Lỗi khởi tạo sau {max_attempts} lần: {e}")
                update_status(f"[{profile_name}] Exception Init: {e}")
                raise
            update_status(f"[{profile_name}] [DEBUG] Thử lại lần {attempt+2}...")
            time.sleep(DRIVER_INIT_RETRY_DELAY) 
            
    return None

def _open_upload_page(driver, profile_name):
    try:
        if not is_driver_valid(driver):
            raise InvalidSessionIdException("Driver không còn session hợp lệ trước khi mở trang upload")
        current_url = (driver.current_url or "").lower()
        if "tiktokstudio/upload" in current_url and _has_upload_page_signal(driver):
            return
        driver.get(TIKTOK_UPLOAD_URL)
        wait = WebDriverWait(driver, 60)
        quick_deadline = time.time() + 1.5
        while time.time() < quick_deadline:
            if "/login" in (driver.current_url or "").lower():
                _set_profile_ui(profile_name, login='Cần đăng nhập lại', last_error='TikTok yêu cầu đăng nhập lại')
                raise TikTokLoginRequiredError("TikTok chuyển về trang đăng nhập. Cookie/session không hợp lệ hoặc đã hết hạn.")
            if _has_upload_page_signal(driver):
                return
            time.sleep(0.2)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-e2e='select_video_container']")))
        except TimeoutException:
            if "/login" in (driver.current_url or "").lower():
                _set_profile_ui(profile_name, login='Cần đăng nhập lại', last_error='TikTok yêu cầu đăng nhập lại')
                raise TikTokLoginRequiredError("TikTok chuyển về trang đăng nhập. Cookie/session không hợp lệ hoặc đã hết hạn.")
            update_status(f"[{profile_name}] [WARN] Không tìm thấy select_video_container, thử fallback...")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[class*='upload'], button[data-e2e='post_video_button'], textarea")))
    except Exception as e:
        update_status(f"[{profile_name}] Lỗi tải trang upload: {e}")
        raise

def _safe_open_upload_page(driver, profile_name):
    if not is_driver_valid(driver):
        update_status(f"[{profile_name}] [DEBUG] Bỏ qua mở trang upload vì session trình duyệt không còn hợp lệ")
        return False
    try:
        _open_upload_page(driver, profile_name)
        return True
    except InvalidSessionIdException:
        update_status(f"[{profile_name}] [DEBUG] Không thể mở lại trang upload vì session đã mất")
        return False
    except Exception as e:
        update_status(f"[{profile_name}] [DEBUG] Mở lại trang upload chưa thành công: {e}")
        return False

def _dismiss_cancel_upload_popup(driver, profile_name):
    """Nếu popup hỏi hủy upload xuất hiện thì bấm NO ngay để giữ phiên upload hiện tại."""
    if not is_driver_valid(driver):
        return False
    try:
        no_button = WebDriverWait(driver, 1.0).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//div[contains(@class,'TUXModal') and .//div[contains(text(),'Sure you want to cancel your upload?')]]"
                "//button[.//div[text()='No']]"
            ))
        )
        ActionChains(driver).move_to_element(no_button).click().perform()
        update_status(f"[{profile_name}] [DEBUG] Đã phát hiện popup hủy upload và tự động chọn NO để tiếp tục tải video")
        return True
    except Exception:
        return False

def _dismiss_cancel_buttons_best_effort(driver):
    try:
        buttons = driver.find_elements(
            By.XPATH,
            "//button[@role='button' and @type='button'"
            " and @data-size='medium' and @data-type='neutral'"
            " and .//div[contains(@class,'Button__content') and normalize-space()='Cancel']]"
        )
        for button in buttons:
            try:
                if button.is_displayed() and button.is_enabled():
                    ActionChains(driver).move_to_element(button).click().perform()
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def _dismiss_content_checks_modal(driver, profile_name):
    if not is_driver_valid(driver):
        return False
    xpath = (
        "//div[(contains(@class,'TUXModal') or @role='dialog') and "
        "(.//*[contains(normalize-space(),'automatic content checks') or contains(normalize-space(),'content checks')])]"
        "//button[.//*[normalize-space()='Cancel'] or normalize-space()='Cancel']"
    )
    try:
        buttons = driver.find_elements(By.XPATH, xpath)
        if len(buttons) == 1:
            try:
                ActionChains(driver).move_to_element(buttons[0]).click().perform()
            except Exception:
                driver.execute_script("arguments[0].click();", buttons[0])
            update_status(f"[{profile_name}] [DEBUG] Đã đóng modal 'Turn on automatic content checks?'")
            return True
        elif len(buttons) > 1:
            update_status(f"[{profile_name}] [WARN] Phát hiện {len(buttons)} nút Cancel trong modal content checks, bỏ qua để tránh click nhầm.")
    except Exception as e:
            update_status(f"[{profile_name}] [DEBUG] Lỗi content checks modal: {e}")
    return False


def _has_content_checks_modal(driver):
    try:
        return bool(driver.execute_script(
            """
            return Array.from(document.querySelectorAll('[role="dialog"], .TUXModal, [class*="Modal"], [class*="modal"]')).some(root => {
              const style = window.getComputedStyle(root);
              const rect = root.getBoundingClientRect();
              if (style.display === 'none' || style.visibility === 'hidden' || rect.width <= 0 || rect.height <= 0) return false;
              const text = (root.innerText || root.textContent || '').toLowerCase();
              return text.includes('automatic content checks') || text.includes('content checks');
            });
            """
        ))
    except Exception:
        return False

def _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=3):
    if not is_driver_valid(driver):
        return False
    clicked = False
    safe_texts = ('cancel', 'not now', 'no', 'skip', 'later', 'close', 'got it', 'maybe later', 'dismiss')
    unsafe_texts = ('post', 'delete', 'discard', 'confirm', 'submit')

    for _ in range(max_rounds):
        did_click = False
        try:
            did_click = bool(driver.execute_script(
                """
                const safeTexts = arguments[0];
                const unsafeTexts = arguments[1];
                const roots = Array.from(document.querySelectorAll('[role="dialog"], .TUXModal, [class*="Modal"], [class*="modal"]'))
                  .filter(el => {
                    const s = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return s && s.display !== 'none' && s.visibility !== 'hidden' && r.width > 40 && r.height > 40;
                  });
                for (const root of roots) {
                  const rootText = (root.innerText || '').toLowerCase();
                  const contentCheck = rootText.includes('automatic content checks') || rootText.includes('content checks') || rootText.includes('copyright check') || rootText.includes('eligibility check');
                  const buttons = Array.from(root.querySelectorAll('button, [role="button"]'));
                  for (const btn of buttons) {
                    const text = (btn.innerText || btn.textContent || btn.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (!text) continue;
                    if (unsafeTexts.some(x => text === x || text.includes(x))) continue;
                    const isSafe = safeTexts.some(x => text === x || text.includes(x));
                    if (!isSafe && !(contentCheck && text.includes('cancel'))) continue;
                    const s = window.getComputedStyle(btn);
                    const r = btn.getBoundingClientRect();
                    if (s.display === 'none' || s.visibility === 'hidden' || r.width <= 0 || r.height <= 0) continue;
                    btn.click();
                    return true;
                  }
                }
                return false;
                """,
                list(safe_texts),
                list(unsafe_texts),
            ))
        except Exception:
            did_click = False

        if not did_click:
            xpaths = [
                "//div[(contains(@class,'TUXModal') or @role='dialog' or contains(@class,'Modal') or contains(@class,'modal'))]//button[.//*[normalize-space()='Cancel'] or normalize-space()='Cancel']",
                "//div[(contains(@class,'TUXModal') or @role='dialog' or contains(@class,'Modal') or contains(@class,'modal'))]//button[.//*[normalize-space()='Not now'] or normalize-space()='Not now']",
                "//div[(contains(@class,'TUXModal') or @role='dialog' or contains(@class,'Modal') or contains(@class,'modal'))]//button[.//*[normalize-space()='No'] or normalize-space()='No']",
                "//div[(contains(@class,'TUXModal') or @role='dialog' or contains(@class,'Modal') or contains(@class,'modal'))]//button[.//*[normalize-space()='Close'] or normalize-space()='Close']",
            ]
            for xpath in xpaths:
                try:
                    for button in driver.find_elements(By.XPATH, xpath):
                        if button.is_displayed() and button.is_enabled():
                            try:
                                driver.execute_script("arguments[0].click();", button)
                            except Exception:
                                ActionChains(driver).move_to_element(button).click().perform()
                            did_click = True
                            break
                    if did_click:
                        break
                except Exception:
                    continue

        if did_click:
            clicked = True
            update_status(f"[{profile_name}] [DEBUG] Đã đóng popup TikTok nhanh để tiếp tục đăng.")
            time.sleep(0.15)
            continue
        break
    return clicked


def _dismiss_tiktok_joyride(driver, profile_name, max_rounds=3):
    dismissed = False
    for _ in range(max_rounds):
        try:
            result = driver.execute_script(
                """
                const overlays = Array.from(document.querySelectorAll('.react-joyride__overlay, [data-test-id="overlay"]'))
                  .filter(el => {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                  });
                if (!overlays.length) return 'none';
                const preferred = ['skip', 'close', 'got it', 'done', 'finish', 'no thanks'];
                const controls = Array.from(document.querySelectorAll(
                  '.react-joyride__tooltip button, .react-joyride__tooltip [role="button"], [data-action="skip"], [data-action="close"]'
                ));
                for (const control of controls) {
                  const text = (control.innerText || control.textContent || control.getAttribute('aria-label') || '').trim().toLowerCase();
                  if (preferred.some(item => text === item || text.includes(item))) {
                    control.click();
                    return `button:${text}`;
                  }
                }
                overlays[0].click();
                return 'overlay';
                """
            )
        except Exception:
            return dismissed
        if result == 'none':
            return dismissed
        dismissed = True
        update_status(f"[{profile_name}] [DEBUG] Đã đóng hướng dẫn Joyride TikTok ({result}).")
        time.sleep(0.1)
    return dismissed

def _has_visible_tiktok_modal(driver):
    try:
        return bool(driver.execute_script(
            """
            return Array.from(document.querySelectorAll('[role="dialog"], .TUXModal, [class*="Modal"], [class*="modal"]')).some(el => {
              const s = window.getComputedStyle(el);
              const r = el.getBoundingClientRect();
              return s && s.display !== 'none' && s.visibility !== 'hidden' && r.width > 40 && r.height > 40;
            });
            """
        ))
    except Exception:
        return False

def _click_post_button(driver, profile_name, post_button):
    _dismiss_tiktok_joyride(driver, profile_name, max_rounds=3)
    _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=2)
    if _has_visible_tiktok_modal(driver):
        raise Exception('Có popup TikTok chưa xử lý, chưa bấm Đăng để tránh click sai.')
    post_button = _find_ready_post_button(driver) or post_button
    blocked_by = driver.execute_script(
        """
        const button = arguments[0];
        const rect = button.getBoundingClientRect();
        const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        if (!top || top === button || button.contains(top)) return '';
        return top.className || top.getAttribute('data-test-id') || top.tagName;
        """,
        post_button,
    )
    if blocked_by:
        raise Exception(f'Nút Đăng vẫn bị che bởi: {blocked_by}')
    ActionChains(driver).move_to_element(post_button).click().perform()

def _has_blocking_post_modal(driver):
    try:
        return bool(driver.execute_script(
            """
            const roots = Array.from(document.querySelectorAll(
              '[role="alert"], [role="status"], [role="dialog"], [data-e2e*="toast"], [class*="Toast"], [class*="toast"]'
            )).filter(el => {
              const style = window.getComputedStyle(el);
              const rect = el.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            });
            const blocking = [
              'couldn\'t upload', 'could not upload', 'failed to upload', 'upload failed',
              'something went wrong', 'vi phạm', 'không thể đăng'
            ];
            return roots.some(root => {
              const text = (root.innerText || root.textContent || '').toLowerCase();
              return blocking.some(item => text.includes(item));
            });
            """
        ))
    except Exception:
        return False

def _capture_post_confirmation_state(driver):
    try:
        surfaces = driver.execute_script(
            """
            return Array.from(document.querySelectorAll(
              '[role="alert"], [role="status"], [role="dialog"], [data-e2e*="toast"], [class*="Toast"], [class*="toast"]'
            )).filter(el => {
              const style = window.getComputedStyle(el);
              const rect = el.getBoundingClientRect();
              return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
            }).map(el => (el.innerText || el.textContent || '').trim().toLowerCase()).filter(Boolean);
            """
        ) or []
    except Exception:
        surfaces = []
    try:
        current_url = (driver.current_url or '').lower()
    except Exception:
        current_url = ''
    return {'url': current_url, 'surfaces': set(surfaces)}


def _save_post_diagnostics(driver, profile_name, short_name):
    try:
        output_dir = app_base_dir() / 'temp_dl' / 'post_diagnostics'
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', short_name)[:80] or 'video'
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base = output_dir / f'{profile_name}_{stamp}_{safe_name}'
        text_path = base.with_suffix('.txt')
        screenshot_path = base.with_suffix('.png')
        body_text = driver.execute_script("return document.body ? document.body.innerText : '';") or ''
        text_path.write_text(body_text, encoding='utf-8')
        driver.save_screenshot(str(screenshot_path))
        if os.environ.get('UPLOAD_CAPTURE_NETWORK') == '1':
            network_path = base.with_suffix('.network.json')
            network_rows = []
            for entry in driver.get_log('performance'):
                try:
                    message = json.loads(entry.get('message', '{}')).get('message', {})
                    method = message.get('method', '')
                    params = message.get('params', {})
                    if method == 'Network.requestWillBeSent':
                        request = params.get('request', {})
                        network_rows.append({
                            'event': 'request',
                            'method': request.get('method'),
                            'url': request.get('url'),
                        })
                    elif method == 'Network.responseReceived':
                        response = params.get('response', {})
                        network_rows.append({
                            'event': 'response',
                            'status': response.get('status'),
                            'url': response.get('url'),
                        })
                except Exception:
                    continue
            network_path.write_text(json.dumps(network_rows, ensure_ascii=False, indent=2), encoding='utf-8')
        update_status(f"[{profile_name}] Đã lưu chẩn đoán sau Post: {text_path}")
        return str(text_path)
    except Exception as error:
        update_status(f"[{profile_name}] [WARN] Không lưu được chẩn đoán sau Post: {error}")
        return None


def _read_post_network_result(driver):
    if os.environ.get('UPLOAD_CAPTURE_NETWORK') != '1' and os.environ.get('UPLOAD_TEST_MODE') != '1':
        return None
    try:
        entries = driver.get_log('performance')
    except Exception:
        return None
    for entry in entries:
        try:
            message = json.loads(entry.get('message', '{}')).get('message', {})
            if message.get('method') != 'Network.responseReceived':
                continue
            params = message.get('params', {})
            response = params.get('response', {})
            url = str(response.get('url', ''))
            if '/tiktok/web/project/post/' not in url:
                continue
            body = driver.execute_cdp_cmd('Network.getResponseBody', {
                'requestId': params.get('requestId'),
            }).get('body', '')
            try:
                payload = json.loads(body)
            except Exception:
                payload = {'raw_body': body[:2000]}
            return {
                'http_status': int(response.get('status') or 0),
                'payload': payload,
            }
        except Exception:
            continue
    return None


def _wait_post_submission_confirmed(driver, profile_name, short_name, baseline=None, timeout=25):
    end_time = time.time() + timeout
    baseline = baseline or {'url': '', 'surfaces': set()}
    success_markers = [
        'your video has been posted', 'video has been posted', 'post has been uploaded',
        'content under review', 'under review', 'đã đăng', 'đang xét duyệt'
    ]
    success_urls = ('/tiktokstudio/content', '/creator-center/content', '/manage')
    content_check_handled = False

    while time.time() < end_time:
        if not is_driver_valid(driver):
            raise WebDriverException('Mất kết nối trình duyệt khi chờ TikTok xác nhận đăng video')
        if not content_check_handled and _has_content_checks_modal(driver):
            if not _dismiss_content_checks_modal(driver, profile_name):
                raise Exception('Phát hiện modal content checks nhưng không đóng được an toàn.')
            time.sleep(0.1)
            final_button = _find_ready_post_button(driver)
            if not final_button:
                raise Exception('Không tìm lại được nút Đăng sau modal content checks.')
            ActionChains(driver).move_to_element(final_button).click().perform()
            content_check_handled = True
            update_status(f"[{profile_name}] [DEBUG] Đã bấm Đăng lại sau khi đóng modal content checks.")
            continue
        if _has_blocking_post_modal(driver):
            raise Exception('TikTok hiển thị popup/lỗi sau khi bấm Đăng. Chưa xác nhận đăng thành công.')
        network_result = _read_post_network_result(driver)
        if network_result:
            payload = network_result.get('payload') or {}
            status_code = payload.get('status_code', payload.get('statusCode'))
            if network_result.get('http_status') == 200 and status_code == 0:
                update_status(f"[{profile_name}] TikTok API đã xác nhận đăng video: {short_name}")
                return True
            raise PostRejectedError(
                f"post_rejected: HTTP {network_result.get('http_status')}, "
                f"status_code={status_code}, status_msg={payload.get('status_msg', '')}"
            )
        try:
            current_url = (driver.current_url or '').lower()
            state = _capture_post_confirmation_state(driver)
            new_surfaces = state['surfaces'] - set(baseline.get('surfaces') or set())
            if any(marker in text for text in new_surfaces for marker in success_markers):
                update_status(f"[{profile_name}] TikTok đã xác nhận đăng/xử lý video: {short_name}")
                return True
            if current_url != baseline.get('url') and any(marker in current_url for marker in success_urls) and '/upload' not in current_url:
                update_status(f"[{profile_name}] TikTok đã chuyển sang trang quản lý nội dung sau khi đăng: {short_name}")
                return True
        except Exception as e:
            if isinstance(e, WebDriverException):
                raise
        time.sleep(0.2)

    diagnostic_path = _save_post_diagnostics(driver, profile_name, short_name)
    detail = f"; diagnostic={diagnostic_path}" if diagnostic_path else ''
    raise TimeoutException(f"Chưa thấy TikTok xác nhận đăng thành công sau {timeout}s: {short_name}{detail}")

# =========================
# Upload Logic
# =========================
POST_BUTTON_SELECTOR = "button[data-e2e='post_video_button'][aria-disabled='false']"
UPLOAD_EDITOR_READY_SELECTORS = [
    "div[data-e2e='upload-progress']",
    "div[data-e2e='upload-loading']",
    "div[data-e2e='video-caption-editor']",
    "div[data-e2e='video-caption-editor-container']",
    "div[data-e2e='recommend-caption-editor']",
    "div[data-e2e='publish-settings']",
    "div[data-e2e='caption-editor']",
    "div[data-e2e='upload-card']",
    "div[class*='upload']",
    "div[class*='caption']",
    "textarea",
]

def _find_ready_post_button(driver):
    try:
        for button in driver.find_elements(By.CSS_SELECTOR, POST_BUTTON_SELECTOR):
            try:
                if button.is_displayed() and button.is_enabled():
                    return button
            except Exception:
                continue
    except Exception:
        pass
    return None

def _has_selected_upload_file(driver):
    try:
        return bool(driver.execute_script(
            """
            const inputs = Array.from(document.querySelectorAll("input[type='file']"));
            return inputs.some(input => input.files && input.files.length > 0);
            """
        ))
    except Exception:
        try:
            for elem in driver.find_elements(By.CSS_SELECTOR, "input[type='file']"):
                value = (elem.get_attribute('value') or '').strip()
                if value:
                    return True
        except Exception:
            pass
    return False

def _detect_upload_editor_state(driver):
    try:
        post_ready = bool(driver.execute_script(
            """
            const btn = document.querySelector("button[data-e2e='post_video_button']");
            if (!btn) return false;
            const ariaDisabled = btn.getAttribute('aria-disabled');
            const disabled = btn.disabled || ariaDisabled === 'true';
            const style = window.getComputedStyle(btn);
            const visible = style && style.display !== 'none' && style.visibility !== 'hidden' && btn.offsetParent !== null;
            return visible && !disabled;
            """
        ))
        if post_ready:
            return {
                'alive': True,
                'ready': True,
                'reason': 'post-ready-js',
            }
    except Exception:
        pass

    has_selected_file = _has_selected_upload_file(driver)

    for selector in UPLOAD_EDITOR_READY_SELECTORS:
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, selector)
            visible = [e for e in elems if e.is_displayed()]
            if visible:
                return {
                    'alive': True,
                    'ready': False,
                    'reason': f'editor:{selector}',
                    'has_selected_file': has_selected_file,
                }
        except Exception:
            continue

    if has_selected_file:
        return {
            'alive': True,
            'ready': False,
            'reason': 'file-selected-awaiting-editor',
            'has_selected_file': True,
        }

    return {
        'alive': False,
        'ready': False,
        'reason': 'no-editor-signal',
        'has_selected_file': False,
    }

def _wait_post_button(driver):
    return WebDriverWait(driver, UPLOAD_READY_TIMEOUT, poll_frequency=UPLOAD_STALL_POLL_INTERVAL).until(
        lambda d: _find_ready_post_button(d)
    )

def _has_upload_progress_signal(driver):
    """Siết tín hiệu upload: ưu tiên dấu hiệu mạnh, tránh nhận nhầm processing/treo."""
    try:
        state = _detect_upload_editor_state(driver)
        return state['alive'], state['reason']
    except Exception as e:
        return False, f"signal-error:{e}"

def _classify_upload_phase(signal_reason, editor_state=None, network_source=None, network_ready_hint=False):
    editor_state = editor_state or {}
    if editor_state.get('ready') or network_ready_hint or signal_reason == 'post-ready-js':
        return 'post-ready'

    reason = network_source or signal_reason or editor_state.get('reason') or ''
    reason = str(reason)

    if 'upload-progress' in reason or 'upload-loading' in reason:
        return 'uploading'
    if 'file-selected' in reason or 'input[type=\'file\']' in reason or 'input[type="file"]' in reason:
        return 'file-selected'
    if 'caption-editor' in reason or 'publish-settings' in reason or 'upload-card' in reason or 'recommend-caption-editor' in reason or 'video-caption-editor' in reason:
        return 'processing'
    if editor_state.get('alive'):
        return 'processing'
    return 'unknown'

def _watch_upload_until_ready_or_stalled(driver, profile_name, file_name, request_start_index=0):
    """Watchdog nhẹ: chỉ quan sát, log và chỉ can thiệp khi xác nhận stalled."""
    short_name = shorten_filename(file_name)
    start_ts = time.time()
    last_progress_ts = start_ts
    warned = False
    last_stage_log_ts = 0
    last_stage_key = None
    last_signal_check_ts = 0.0
    last_network_check_ts = 0.0
    last_signal_result = (False, "no-signal")
    last_phase = None
    last_phase_change_ts = start_ts
    last_network_source = None
    last_progress_fingerprint = None
    last_content_check_ts = 0.0

    update_status(f"[{profile_name}] [DEBUG] Bắt đầu theo dõi tiến trình tải video lên: {short_name}")

    while True:
        try:
            if not is_driver_valid(driver):
                return False, "trình duyệt điều khiển không còn phản hồi", None

            now = time.time()

            if now - last_content_check_ts >= 0.25:
                _dismiss_tiktok_joyride(driver, profile_name, max_rounds=1)
                _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=2)
                last_content_check_ts = now

            post_button = _find_ready_post_button(driver)
            if post_button:
                update_status(f"[{profile_name}] [DEBUG] TikTok đã xử lý xong video, nút Đăng đã sẵn sàng: {short_name}")
                return True, "nút Đăng đã sẵn sàng", post_button

            editor_state = _detect_upload_editor_state(driver)
            if editor_state.get('ready'):
                post_button = _find_ready_post_button(driver)
                if post_button:
                    return True, editor_state.get('reason', 'editor-ready'), post_button

            network_alive = False
            network_source = None
            network_ready_hint = False
            if now - last_network_check_ts >= NETWORK_READY_POLL_INTERVAL:
                network_alive, network_source, network_ready_hint = _get_network_upload_signal(driver, request_start_index)
                last_network_check_ts = now
                last_network_source = network_source
                if network_ready_hint and _find_ready_post_button(driver):
                    return True, "request trace + nút Đăng đã sẵn sàng", _find_ready_post_button(driver)

            if now - last_signal_check_ts >= UPLOAD_SIGNAL_POLL_INTERVAL:
                last_signal_result = _has_upload_progress_signal(driver)
                last_signal_check_ts = now

            alive, source = last_signal_result
            if editor_state.get('alive'):
                alive = True
                source = editor_state.get('reason', source)
            if network_alive:
                alive = True
                source = network_source or source

            phase = _classify_upload_phase(source, editor_state=editor_state, network_source=network_source, network_ready_hint=network_ready_hint)
            progress_fingerprint = (phase, source, bool(network_ready_hint), bool(editor_state.get('ready')))

            made_progress = False
            if phase != last_phase:
                made_progress = True
                last_phase = phase
                last_phase_change_ts = now
            if progress_fingerprint != last_progress_fingerprint:
                made_progress = True
                last_progress_fingerprint = progress_fingerprint

            if alive:
                if made_progress:
                    last_progress_ts = now
                if now - last_stage_log_ts >= 12:
                    stage_map = {
                        "selector:div[data-e2e='upload-loading']": "Hệ thống đang tải video lên máy chủ",
                        "selector:div[data-e2e='upload-progress']": "Thanh tiến trình tải video đang chạy",
                        f"selector:{POST_BUTTON_SELECTOR}": "Nút Đăng đã bật",
                        "selector:input[type='file'][accept*='video']": "Video đã được gắn vào biểu mẫu đăng",
                        "selector:input[type='file']": "Biểu mẫu tải video vẫn sẵn sàng",
                        "editor:div[data-e2e='upload-loading']": "TikTok đã nhận file và đang dựng giao diện upload",
                        "editor:div[data-e2e='upload-progress']": "Thanh tiến trình upload đã xuất hiện",
                        "editor:div[data-e2e='video-caption-editor']": "Khung caption/editor đã mount",
                        "editor:div[data-e2e='video-caption-editor-container']": "Khung caption/editor đã mount",
                        "editor:div[data-e2e='recommend-caption-editor']": "Khu vực caption gợi ý đã mount",
                        "editor:div[data-e2e='publish-settings']": "Khu vực publish settings đã mount",
                        "editor:div[data-e2e='caption-editor']": "Trình soạn caption đã sẵn sàng một phần",
                        "editor:div[data-e2e='upload-card']": "Card upload đã được TikTok dựng xong",
                        "editor:textarea": "Textarea caption đã xuất hiện",
                        "file-selected-awaiting-editor": "TikTok đã nhận file nhưng giao diện editor đang tải tiếp",
                        "post-ready-js": "Nút Đăng đã sẵn sàng từ kiểm tra JS",
                    }
                    stage_text = stage_map.get(source, "Video vẫn đang được TikTok xử lý")
                    if source != last_stage_key or now - last_stage_log_ts >= 20:
                        if made_progress:
                            update_status(f"[{profile_name}] [DEBUG] {stage_text}: {short_name}")
                        else:
                            update_status(f"[{profile_name}] [DEBUG] {stage_text} (chưa có tiến triển mới): {short_name}")
                        last_stage_log_ts = now
                        last_stage_key = source

            elapsed_total = now - start_ts
            idle_for = now - last_progress_ts
            phase_idle_for = now - last_phase_change_ts
            if not warned and idle_for >= UPLOAD_PROGRESS_WARN_AFTER:
                warned = True
                update_status(f"[{profile_name}] [DEBUG] Video đang xử lý lâu hơn bình thường (~{int(idle_for)} giây chưa thấy tiến triển mới): {short_name}")

            if elapsed_total >= UPLOAD_HARD_TIMEOUT:
                update_status(f"[{profile_name}] [DEBUG] Phát hiện video bị kẹt cứng sau {int(elapsed_total)} giây tổng thời gian upload: {short_name}")
                return False, f"video vượt hard-timeout {UPLOAD_HARD_TIMEOUT}s (phase={last_phase}, source={last_network_source or source})", None

            if phase in {'processing', 'file-selected', 'uploading'} and phase_idle_for >= UPLOAD_PHASE_STALL_TIMEOUT:
                update_status(f"[{profile_name}] [DEBUG] Phát hiện video bị kẹt quá lâu ở phase `{phase}` sau {int(phase_idle_for)} giây: {short_name}")
                return False, f"video kẹt ở phase {phase} quá {UPLOAD_PHASE_STALL_TIMEOUT}s", None

            if idle_for >= UPLOAD_STALL_TIMEOUT and (not alive or phase == 'unknown'):
                update_status(f"[{profile_name}] [DEBUG] Phát hiện video có dấu hiệu bị treo sau {int(idle_for)} giây không có tiến triển: {short_name}")
                return False, f"video tải lâu không có tiến triển ({last_phase or 'unknown'} / {source})", None

            time.sleep(UPLOAD_STALL_POLL_INTERVAL)
        except Exception as e:
            update_status(f"[{profile_name}] [DEBUG] Bộ theo dõi tiến trình tải video gặp lỗi: {e}")
            return False, f"lỗi theo dõi tiến trình tải video: {e}", None

def _ensure_upload_container_ready(driver, quick_only=False):
    try:
        elems = driver.find_elements(By.CSS_SELECTOR, "div[data-e2e='select_video_container']")
        if elems: return True
        if quick_only:
            deadline = time.perf_counter() + UPLOAD_CONTAINER_QUICK_WAIT
            while time.perf_counter() < deadline:
                time.sleep(0.15)
                elems = driver.find_elements(By.CSS_SELECTOR, "div[data-e2e='select_video_container']")
                if elems:
                    return True
            return False
        driver.get(TIKTOK_UPLOAD_URL)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-e2e='select_video_container']")))
        return True
    except Exception: return False

def _force_reopen_driver(profile_name, reason):
    driver = profiles.get(profile_name, {}).get('driver')
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
    profiles[profile_name]['driver'] = None
    update_status(f"[{profile_name}] [DEBUG] Đang mở lại browser sau lỗi upload: {reason}")
    ensure_driver(profile_name)
    return profiles[profile_name].get('driver')

def _recover_upload_page_after_failure(profile_name, driver, reason, force_reopen=False):
    if not profiles.get(profile_name, {}).get('running', False):
        return False
    update_status(f"[{profile_name}] [DEBUG] Đang reset trang upload sau lỗi: {reason}")
    if not force_reopen and is_driver_valid(driver):
        try:
            if _safe_open_upload_page(driver, profile_name) and _ensure_upload_container_ready(driver, quick_only=True):
                update_status(f"[{profile_name}] [DEBUG] Đã reset trang upload sau lỗi.")
                return True
        except Exception:
            pass
    try:
        new_driver = _force_reopen_driver(profile_name, reason)
        if new_driver and _ensure_upload_container_ready(new_driver, quick_only=True):
            update_status(f"[{profile_name}] [DEBUG] Đã mở lại browser và trang upload sẵn sàng.")
            return True
        if new_driver:
            return _safe_open_upload_page(new_driver, profile_name)
    except Exception as e:
        update_status(f"[{profile_name}] [DEBUG] Không thể reset trang upload sau lỗi: {e}")
    return False

def upload_video(profile_name, video_path):
    last_error = None
    request_context = None
    driver = None
    file_name = Path(video_path).name
    short_name = shorten_filename(file_name)
    benchmark_phases = {}
    benchmark_success = False
    benchmark_post_clicked = False
    driver_reused_before = is_driver_valid(profiles.get(profile_name, {}).get('driver'))

    def record_phase(name, started_at):
        elapsed = time.perf_counter() - started_at
        benchmark_phases[name] = benchmark_phases.get(name, 0.0) + elapsed

    try:
        phase_started = time.perf_counter()
        ensure_driver(profile_name)
        record_phase('ensure_driver_seconds', phase_started)
        driver = profiles[profile_name]['driver']
        request_context = _prepare_request_assist_context(profile_name, driver)
        stall_retry_used = False
        recovered_after_failure = False
        max_attempts = 1 if os.environ.get('UPLOAD_TEST_MODE') == '1' else RETRY_COUNT + 1
        for attempt in range(1, max_attempts + 1):
            post_clicked = False
            try:
                update_status(f"[{profile_name}] Đang đăng: {short_name}")
                _set_profile_ui(profile_name, upload='Đang tải video', last_error='')
                if not _ensure_upload_container_ready(driver, quick_only=True):
                    _ensure_upload_container_ready(driver, quick_only=False)
                phase_started = time.perf_counter()
                try:
                    file_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file'][accept*='video'], input[type='file']")))
                except TimeoutException:
                    _safe_open_upload_page(driver, profile_name)
                    file_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file'][accept*='video'], input[type='file']")))
                file_input.send_keys(video_path)
                record_phase('file_select_seconds', phase_started)
                _set_profile_ui(profile_name, upload='Đã chọn video')
                update_status(f"[{profile_name}] [DEBUG] Đã đưa video vào trình tải lên của TikTok: {short_name}")
                time.sleep(UPLOAD_POST_SENDKEYS_SETTLE_SECONDS)
                _dismiss_cancel_upload_popup(driver, profile_name)
                _dismiss_cancel_buttons_best_effort(driver)
                _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=3)
                # -------------------------------------------------------------------

                phase_started = time.perf_counter()
                ready, ready_reason, ready_post_button = _watch_upload_until_ready_or_stalled(
                    driver,
                    profile_name,
                    file_name,
                    request_start_index=request_context.get('start_index', 0) if request_context else 0,
                )
                record_phase('wait_until_ready_seconds', phase_started)
                if not ready:
                    last_error = ready_reason
                    _set_profile_ui(profile_name, upload='Bị kẹt', last_error=ready_reason)
                    update_status(f"[{profile_name}] [DEBUG] Kích hoạt cơ chế khôi phục do tải video bất thường: {ready_reason}")
                    if is_driver_valid(driver):
                        _safe_open_upload_page(driver, profile_name)
                        update_status(f"[{profile_name}] [DEBUG] Đã mở lại trang đăng video để khôi phục phiên tải: {short_name}")
                    raise TimeoutException(f"Video bị treo trước khi nút Đăng sẵn sàng: {ready_reason}")

                post_button = ready_post_button or _wait_post_button(driver)
                _set_profile_ui(profile_name, upload='Chờ nút Đăng')
                confirmation_baseline = _capture_post_confirmation_state(driver)
                phase_started = time.perf_counter()
                # From this point onward, never retry this file: click dispatch can be uncertain.
                post_clicked = True
                benchmark_post_clicked = True
                _click_post_button(driver, profile_name, post_button)
                record_phase('post_click_latency_seconds', phase_started)
                _set_profile_ui(profile_name, upload='Đã gửi lệnh đăng')
                update_status(f"[{profile_name}] Đã gửi lệnh đăng: {short_name}")

                _set_profile_ui(profile_name, upload='Chờ TikTok xác nhận')
                phase_started = time.perf_counter()
                _wait_post_submission_confirmed(driver, profile_name, short_name, baseline=confirmation_baseline)
                record_phase('confirmation_seconds', phase_started)
                benchmark_success = True

                try:
                    os.remove(video_path)
                except Exception as error:
                    update_status(f"[{profile_name}] [WARN] Đã đăng nhưng không xóa được video: {error}")
                try:
                    if not profiles[profile_name]['config'].get('open_only_when_video', False) and not _ensure_upload_container_ready(driver, quick_only=True):
                        _safe_open_upload_page(driver, profile_name)
                except Exception as error:
                    update_status(f"[{profile_name}] [WARN] Đã đăng nhưng chưa reset được trang upload: {error}")
                try:
                    _capture_request_trace(profile_name, driver, file_name, request_context, outcome='success')
                except Exception as error:
                    update_status(f"[{profile_name}] [WARN] Đã đăng nhưng không lưu được request trace: {error}")
                try:
                    _export_live_cookies_to_config(driver, profile_name)
                except Exception as error:
                    update_status(f"[{profile_name}] [WARN] Đã đăng nhưng không lưu được cookie live: {error}")
                return True
            except (InvalidSessionIdException, WebDriverException) as e:
                error_kind = _log_webdriver_failure(
                    profile_name,
                    driver,
                    e,
                    'after_post' if post_clicked else 'before_post',
                    short_name,
                )
                session_lost = error_kind in {
                    'invalid_session', 'window_closed', 'renderer_crash', 'browser_disconnected'
                } or not is_driver_valid(driver)
                if post_clicked:
                    last_error = 'driver_session_lost_after_post' if session_lost else f'{error_kind}_after_post'
                    browser_state = 'Mất kết nối' if session_lost else 'Đang khôi phục'
                    _set_profile_ui(profile_name, browser=browser_state, upload='Chưa xác nhận', last_error=last_error)
                    update_status(f"[{profile_name}] Lỗi sau khi bấm Đăng, không retry video này để tránh đăng trùng: {short_name} ({last_error})")
                    _recover_upload_page_after_failure(profile_name, driver, last_error, force_reopen=session_lost)
                    recovered_after_failure = True
                    break
                last_error = 'driver_session_lost' if session_lost else error_kind
                _set_profile_ui(profile_name, browser='Mất kết nối' if session_lost else 'Đang khôi phục', upload='Đăng lỗi', last_error=last_error)
                update_status(f"[{profile_name}] Lỗi WebDriver trước khi Đăng. Đang khôi phục... ({last_error})")
                if session_lost:
                    _force_reopen_driver(profile_name, last_error)
                else:
                    _recover_upload_page_after_failure(profile_name, driver, last_error, force_reopen=False)
                driver = profiles[profile_name]['driver']
                continue
            except Exception as e:
                last_error = str(e)
                if post_clicked:
                    if not isinstance(e, PostRejectedError):
                        last_error = f'post_submission_uncertain: {last_error}'
                    _set_profile_ui(profile_name, upload='Chưa xác nhận', last_error=last_error)
                    update_status(f"[{profile_name}] Lỗi sau khi thử bấm Đăng; không retry để tránh đăng trùng: {short_name} ({last_error})")
                    break
                if "File not found" in str(e):
                    _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=str(e))
                    trace_path = _capture_request_trace(profile_name, driver, file_name, request_context, outcome='file_not_found')
                    _append_failed_upload_log(profile_name, file_name, str(e), trace_path=trace_path, outcome='file_not_found')
                    return False
                if os.environ.get('UPLOAD_TEST_MODE') != '1' and "Video bị treo trước khi nút Đăng sẵn sàng" in str(e) and not stall_retry_used:
                    stall_retry_used = True
                    update_status(f"[{profile_name}] Video có dấu hiệu treo lâu hoặc kẹt phase. Thực hiện tải lại ngay 1 lần: {short_name}")
                    try:
                        if is_driver_valid(driver):
                            _safe_open_upload_page(driver, profile_name)
                    except Exception:
                        pass
                    continue
                update_status(f"[{profile_name}] Lỗi đăng (Lần {attempt}): {e}")
                _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=str(e))
                time.sleep(0.5)
        
        should_recover = str(last_error or '').lower() in {
            'driver_session_lost',
            'driver_session_lost_after_post',
        }
        if should_recover and not recovered_after_failure:
            _recover_upload_page_after_failure(profile_name, driver, last_error or 'failed_after_retries', force_reopen=True)
            update_status(f"[{profile_name}] [DEBUG] Đánh dấu video lỗi và reset trang upload trước video kế tiếp: {short_name}")
        elif should_recover:
            update_status(f"[{profile_name}] [DEBUG] Đánh dấu video lỗi; trang upload đã được reset trước video kế tiếp: {short_name}")
        else:
            update_status(f"[{profile_name}] [DEBUG] Đánh dấu video lỗi và chuyển sang video kế tiếp, giữ nguyên driver hiện tại: {short_name}")
        _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=str(last_error or 'Không đăng được video'))
        trace_path = _capture_request_trace(profile_name, driver, file_name, request_context, outcome='failed_after_retries')
        _append_failed_upload_log(profile_name, file_name, last_error or 'failed_after_retries', trace_path=trace_path, outcome='failed_after_retries')
        return False
    except Exception as e:
        last_error = str(e)
        try:
            trace_path = _capture_request_trace(profile_name, driver, file_name, request_context, outcome='fatal')
            _append_failed_upload_log(profile_name, file_name, str(e), trace_path=trace_path, outcome='fatal')
        except Exception:
            pass
        update_status(f"[{profile_name}] Lỗi nghiêm trọng: {e}")
        _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=str(e))
        return False
    finally:
        if profile_name in profiles:
            profiles[profile_name]['uploading'] = False
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
                    'driver_mode': 'warm' if driver_reused_before else 'cold',
                },
            )
        except Exception as benchmark_error:
            update_status(f"[{profile_name}] [WARN] Không ghi được benchmark upload: {benchmark_error}")
        finally:
            _release_video_path(video_path)

def process_video_queue_thread(profile_name):
    idle_start = None
    while True:
        try:
            if profile_name not in profiles or not profiles[profile_name]['running']:
                break

            limit = profiles[profile_name]['config'].get('max_uploads_per_day', 0)
            if limit > 0 and profiles[profile_name]['uploads_today_count'] >= limit:
                update_status(f"[{profile_name}] Đã đạt giới hạn {limit} video/ngày. Profile sẽ tự dừng.")
                _set_profile_ui(profile_name, upload='Đạt giới hạn', last_error=f'Đã đạt {limit} video/ngày')
                time.sleep(LIMIT_REACHED_SHUTDOWN_DELAY)
                stop_profile(profile_name)
                break

            try:
                video_path = profiles[profile_name]['queue'].get(timeout=1)
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
                
                # Watchdog health check
                if not profiles[profile_name].get('running', False):
                    continue
                obs = profiles[profile_name].get('observer')
                if obs and not obs.is_alive():
                    update_status(f"[{profile_name}] Watchdog bị lỗi, khởi động lại...")
                    folder = profiles[profile_name]['config'].get('folder_path', '')
                    if folder:
                        new_obs = Observer()
                        new_obs.schedule(VideoFolderHandler(profile_name), folder, recursive=False)
                        new_obs.start()
                        profiles[profile_name]['observer'] = new_obs
                        update_status(f"[{profile_name}] Watchdog đã khởi động lại.")
                continue

            idle_start = None
            _mark_upload_timing(video_path, 'dequeued_at')
            update_status(f"[{profile_name}] Đã đưa video vào hàng chờ xử lý: {shorten_filename(Path(video_path).name)}")
            config = profiles[profile_name]['config']
            open_only = config.get('open_only_when_video', False)
            if open_only and not is_driver_valid(profiles[profile_name].get('driver')):
                _set_profile_ui(profile_name, browser='Đang mở', upload='Có video mới')
                update_status(f"[{profile_name}] Có video mới, đang mở profile để đăng.")
            _set_profile_ui(profile_name, upload='Đang đăng')
            profiles[profile_name]['uploading'] = True
            ok = upload_video(profile_name, video_path)
            if ok:
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

def update_status(message):
    def _update():
        if not root.winfo_exists() or not status_text.winfo_exists(): return
        status_text.configure(state='normal')
        tag, important_tag = classify_log_message(message)
        line = f"{datetime.now().strftime('%H:%M:%S')} {message}\n"
        status_text.insert(ctk.END, line, tag)
        trim_text_widget_lines(status_text, MAX_STATUS_LOG_LINES)
        status_text.see(ctk.END)
        status_text.configure(state='disabled')

        try:
            if important_log_text.winfo_exists():
                if important_tag:
                    important_log_text.configure(state='normal')
                    important_log_text.insert(ctk.END, line, important_tag)
                    trim_text_widget_lines(important_log_text, MAX_IMPORTANT_LOG_LINES)
                    important_log_text.see(ctk.END)
                    important_log_text.configure(state='disabled')
        except Exception:
            pass
    root.after(0, _update)

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
    
    if failed_to_delete and os.path.isdir(REQUEST_TRACE_DIR):
        all_failed_fnames = {f for fnames in failed_files.values() for f in fnames}
        for tf in os.listdir(REQUEST_TRACE_DIR):
            for vname in all_failed_fnames:
                if vname in tf:
                    try:
                        os.remove(os.path.join(REQUEST_TRACE_DIR, tf))
                    except Exception:
                        pass
                    break
    
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

def _apply_row_tags():
    try:
        tree.tag_configure('tag_ready', background='#DCFCE7', foreground='#166534')
        tree.tag_configure('tag_processing', background='#FEF3C7', foreground='#92400E')
        tree.tag_configure('tag_error', background='#FEE2E2', foreground='#991B1B')
        tree.tag_configure('tag_stopped', background='#F3F4F6', foreground='#374151')
        tree.tag_configure('tag_running', background='#DCFCE7', foreground='#166534')
    except Exception: pass

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

def _set_profile_ui(name, refresh=True, **fields):
    if name not in profiles:
        return
    ui = _profile_ui(name)
    for key, value in fields.items():
        if value is not None:
            ui[key] = str(value)
    if refresh:
        try:
            root.after(0, update_profile_list)
        except Exception:
            try:
                update_profile_list()
            except Exception:
                pass

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
    total = sum(1 for _ in tree.get_children(''))
    running = sum(1 for iid in tree.get_children('') if tree.item(iid, 'values')[1] == 'Đang chạy')
    status_count_label.configure(text=f"Hồ sơ: {total} | Đang chạy: {running}")
    clock_label.configure(text=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    try:
        header_total_label.set(str(total))
        header_running_label.set(str(running))
        header_project_label.set(selected_project_var.get() or ALL_OPTION)
    except Exception:
        pass

def _first_run_download_check():
    """Kiểm tra và tải tài nguyên thiếu (Browser, ngrok.exe, service_account.json)."""
    from version import RESOURCE_ASSETS
    missing = {}
    for local_name, info in RESOURCE_ASSETS.items():
        path = app_base_dir() / local_name
        if not path.exists():
            missing[local_name] = info

    if not missing:
        return

    if not messagebox.askyesno(
        "Tải tài nguyên",
        "Cần tải tài nguyên lần đầu (Browser, ngrok, service_account). Tiếp tục?",
    ):
        return

    dlg = ctk.CTkToplevel(root)
    dlg.title("Đang tải tài nguyên lần đầu...")
    dlg.geometry("480x140")
    dlg.grab_set()
    dlg.resizable(False, False)
    label = ctk.CTkLabel(dlg, text="Đang tải...", font=("", 13))
    label.pack(pady=(16, 8))
    progress = ctk.CTkProgressBar(dlg, width=380)
    progress.pack(pady=8)
    progress.set(0)

    def _update_status(text, pct):
        try:
            label.configure(text=text)
            progress.set(pct)
            dlg.update_idletasks()
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
        try:
            tag = f"v{CURRENT_VERSION}"
            base_url = f"https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/download/{tag}"
            total = len(missing)
            for i, (local_name, info) in enumerate(missing.items()):
                _update_status(f"Đang tải {local_name}...", (i + 0.1) / total)
                asset_name = info["asset"].format(version=CURRENT_VERSION)
                url = f"{base_url}/{asset_name}"

                if info["type"] == "zip_dir":
                    temp_dir = app_base_dir() / "temp_dl" / "resources"
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    zip_path = temp_dir / asset_name
                    updater = GitHubReleaseUpdater(app_base_dir(), GITHUB_REPO_OWNER, GITHUB_REPO_NAME)
                    updater.download_asset(url, zip_path)
                    _update_status(f"Đang giải nén {local_name}...", (i + 0.6) / total)
                    extract_temp = temp_dir / f"extract_{local_name}"
                    shutil.rmtree(extract_temp, ignore_errors=True)
                    extract_temp.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(extract_temp)
                    dest = app_base_dir() / local_name
                    shutil.rmtree(dest, ignore_errors=True)
                    items = list(extract_temp.iterdir())
                    if len(items) == 1 and items[0].is_dir():
                        shutil.copytree(items[0], dest)
                    else:
                        shutil.copytree(extract_temp, dest)
                    shutil.rmtree(extract_temp, ignore_errors=True)
                    zip_path.unlink(missing_ok=True)
                    for validate_path in info.get("validate", []):
                        if not (app_base_dir() / validate_path).exists():
                            raise RuntimeError(f"Thiếu file bắt buộc: {validate_path}")
                else:
                    updater = GitHubReleaseUpdater(app_base_dir(), GITHUB_REPO_OWNER, GITHUB_REPO_NAME)
                    dest = app_base_dir() / local_name
                    updater.download_asset(url, dest)
                    if not dest.exists() or dest.stat().st_size == 0:
                        raise RuntimeError(f"Tải {local_name} thất bại: file rỗng hoặc không tồn tại.")

                _update_status(f"Đã tải {local_name}.", (i + 1) / total)
            _update_status("Hoàn tất.", 1.0)
            root.after(500, lambda: _done(True, ""))
        except requests.RequestException as e:
            root.after(0, lambda: _done(False, f"Lỗi mạng: {e}"))
        except Exception as e:
            root.after(0, lambda: _done(False, str(e)))

    threading.Thread(target=_run, daemon=True).start()


def _stop_all_profiles():
    for name in list(running_profiles):
        if profiles.get(name, {}).get("running"):
            stop_profile(name)
    return len(running_profiles) == 0


_update_ui_queue = queue.Queue()


def _enqueue_update_ui(callback):
    _update_ui_queue.put(callback)


def _drain_update_ui_queue():
    try:
        while True:
            callback = _update_ui_queue.get_nowait()
            try:
                callback()
            except Exception as error:
                update_status(f"[Update] Lỗi giao diện cập nhật: {error}")
    except queue.Empty:
        pass
    try:
        root.after(100, _drain_update_ui_queue)
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
    dlg.geometry("680x560")
    dlg.minsize(580, 460)
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
    dlg.geometry("400x120")
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
                root.after(2000, lambda: updater.launch_update(script))
                root.after(2500, root.destroy)
            _enqueue_update_ui(_launch_when_ready)
        except Exception as e:
            error_message = str(e)
            _enqueue_update_ui(lambda error_message=error_message: _done(False, error_message))

    threading.Thread(target=_run, daemon=True).start()


def update_profile_list(*args):
    sp = selected_project_var.get()
    for item in tree.get_children(): tree.delete(item)
    kw = filter_var.get().strip().lower()
    iter_names = sorted(profiles.keys()) if sp == ALL_OPTION else sorted(projects.get(sp, []))
    
    for name in iter_names:
        if name in profiles:
            ui = _profile_ui(name)
            st = ui.get('status') or ('Đang chạy' if profiles[name]['running'] else 'Đã dừng')
            cfg = profiles[name]['config']
            lim = str(cfg.get('max_uploads_per_day', 0)) if cfg.get('max_uploads_per_day', 0) > 0 else "Không"
            headless_text = "Có" if cfg.get("headless", True) else "Không"
            row = (
                f"{name} {st} {ui.get('login','')} {ui.get('proxy','')} {ui.get('browser','')} "
                f"{ui.get('upload','')} {ui.get('last_error','')} {cfg.get('folder_path','')} "
                f"{cfg.get('chrome_profile','')} {headless_text} {lim}"
            ).lower()
            if kw and kw not in row: continue
            tags = _profile_row_tag(ui, profiles[name]['running'])
            tree.insert(
                '',
                'end',
                values=(
                    name,
                    st,
                    ui.get('login', ''),
                    ui.get('proxy', ''),
                    ui.get('browser', ''),
                    ui.get('upload', ''),
                    _short_ui_text(ui.get('last_error', '')),
                    cfg.get('folder_path',''),
                    cfg.get('chrome_profile',''),
                    headless_text,
                    lim,
                ),
                tags=tags,
            )
    _apply_row_tags()
    _refresh_status_bar()

# =========================
# Worker Functions (Batch)
# =========================
_batch_start_in_progress = False
_batch_stop_in_progress = False

def _thread_sequential_start(targets, context_name):
    global _batch_start_in_progress
    try:
        update_status(f"Bắt đầu khởi động {len(targets)} hồ sơ ({context_name})...")
        for name in targets:
            try:
                if name not in profiles or profiles[name]['running']: continue
                if not check_system_resources(name):
                    update_status(f"[{name}] Bỏ qua (Low Res).")
                    continue
                update_status(f"[{name}] Đang khởi động...")
                start_profile(name)

                start_t = time.time()
                while time.time() - start_t < START_PROFILE_TIMEOUT:
                    if is_driver_valid(profiles[name].get('driver')): break
                    if not profiles[name]['running']: break
                    time.sleep(1)

                if is_driver_valid(profiles[name].get('driver')):
                    update_status(f"[{name}] OK ({time.time()-start_t:.1f}s).")
                    time.sleep(1)
                elif not profiles[name]['running']: pass
                else:
                    update_status(f"[{name}] Timeout. Dừng.")
                    stop_profile(name)
            except Exception as e:
                update_status(f"[{name}] Lỗi Batch Start: {e}")
                if profiles.get(name, {}).get('running'): stop_profile(name)
        update_status(f"Hoàn tất khởi động batch ({context_name}).")
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
            if name not in profiles or not profiles[name]['running']: continue
            try:
                update_status(f"[{name}] Đang dừng...")
                stop_profile(name)
                time.sleep(0.5)
            except Exception as e:
                update_status(f"[{name}] Lỗi dừng: {e}")
        update_status(f"Hoàn tất dừng batch ({context_name}).")
        root.after(0, update_profile_list)
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
    if profiles[name].get('session_busy') or is_driver_valid(profiles[name].get('manual_driver')):
        update_status(f"[{name}] Không thể Start khi browser thủ công hoặc thao tác session đang hoạt động.")
        _set_profile_ui(name, status='Lỗi', last_error='Profile đang được sử dụng bởi thao tác session khác')
        return
    
    config = profiles[name]['config']

    duplicate_profile = _find_running_profile_with_same_data_dir(name)
    if duplicate_profile:
        message = f"Chrome profile trùng với hồ sơ đang chạy: {duplicate_profile}"
        _set_profile_ui(name, status='Lỗi', browser='Bị lỗi', last_error=message)
        update_status(f"[{name}] Không thể khởi động: {message}.")
        return

    auto_created = False
    if not os.path.exists(config["folder_path"]):
        try:
            os.makedirs(config["folder_path"], exist_ok=True)
            auto_created = True
        except Exception as e:
            update_status(f"[{name}] Không thể tạo folder video: {e}")

    if not os.path.exists(config["chrome_profile"]):
        try:
            os.makedirs(config["chrome_profile"], exist_ok=True)
            auto_created = True
        except Exception as e:
            update_status(f"[{name}] Không thể tạo chrome profile: {e}")

    if not os.path.exists(config["folder_path"]) or not os.path.exists(config["chrome_profile"]):
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
            if config.get('open_only_when_video', False):
                profiles[name]['watch_started_at'] = time.time()
                h = VideoFolderHandler(name)
                o = Observer()
                o.schedule(h, config["folder_path"], recursive=False)
                o.start()
                profiles[name]['observer'] = o
                _set_profile_ui(name, status='Đang chạy', browser='Chờ video', upload='Chờ video mới')
                update_status(f"[{name}] Chế độ chỉ mở khi có video mới: bỏ qua video cũ, đang chờ video mới.")
                threading.Thread(target=process_video_queue_thread, args=(name,), daemon=True).start()
                return

            ensure_driver(name)
            if name not in profiles or not profiles[name].get('running', False):
                drv = profiles.get(name, {}).get('driver')
                if drv:
                    try: drv.quit()
                    except Exception: pass
                if name in profiles:
                    profiles[name]['driver'] = None
                    kill_stale_chrome_processes(name)
                return

            if is_driver_valid(profiles[name].get('driver')):
                h = VideoFolderHandler(name)
                o = Observer()
                o.schedule(h, config["folder_path"], recursive=False)
                o.start()
                profiles[name]['observer'] = o
                profiles[name]['watch_started_at'] = time.time()
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

def close_profile_browser(name):
    if name not in profiles:
        return
    drv = profiles[name].get('driver')
    profiles[name]['driver'] = None
    if drv:
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
    
    if name not in profiles or not profiles[name]['running']: return
    
    # Đánh dấu dừng trước để queue thread không cố restart watchdog
    _set_profile_ui(name, status='Đang dừng', browser='Đang đóng', upload='Đang dừng')
    profiles[name]['running'] = False
    drv = profiles[name].get('driver')
    profiles[name]['driver'] = None
    ob = profiles[name].get('observer')
    profiles[name]['observer'] = None
    
    if ob:
        try:
            ob.stop()
            ob.join()
        except Exception: pass
    
    if drv:
        try:
            _export_live_cookies_to_config(drv, name)
            drv.get(TIKTOK_BASE_URL)
            time.sleep(0.5)
        except Exception:
            pass
        try: drv.quit()
        except Exception: pass
    running_profiles.discard(name)
    
    kill_stale_chrome_processes(name)
    
    _set_profile_ui(name, status='Đã dừng', browser='Chưa mở', upload='Chờ video')
    update_status(f"[{name}] Đã dừng.")
    root.after(0, update_profile_list)

# =========================
# CRUD Actions
# =========================
def create_project():
    if not _license_guard(): return
    dlg = ctk.CTkToplevel(root)
    dlg.title("Tạo dự án")
    dlg.geometry("300x150")
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
    dlg.geometry("300x150")
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
    dlg.title("Thêm hồ sơ")
    dlg.geometry("600x850")
    
    scroll_frame = ctk.CTkScrollableFrame(dlg, width=580, height=620)
    scroll_frame.pack(fill='both', expand=True, padx=10, pady=(10, 0))
    
    ctk.CTkLabel(scroll_frame, text="Tên:").pack(pady=2)
    e_name = ctk.CTkEntry(scroll_frame, width=400)
    e_name.pack(pady=2)
    
    ctk.CTkLabel(scroll_frame, text="Thư mục video:").pack(pady=2)
    e_folder = ctk.CTkEntry(scroll_frame, width=400)
    e_folder.pack()
    ctk.CTkButton(scroll_frame, text="...", width=50, command=lambda: e_folder.insert(0, filedialog.askdirectory())).pack()
    
    ctk.CTkLabel(scroll_frame, text="Chrome User Data:").pack(pady=2)
    e_chrome = ctk.CTkEntry(scroll_frame, width=400)
    e_chrome.pack()
    ctk.CTkButton(scroll_frame, text="...", width=50, command=lambda: e_chrome.insert(0, filedialog.askdirectory())).pack()
    
    ctk.CTkLabel(scroll_frame, text="Cookie:").pack(pady=2)
    e_cookie = ctk.CTkEntry(scroll_frame, width=400)
    e_cookie.pack()
    
    ctk.CTkLabel(scroll_frame, text="ID TikTok:").pack(pady=2)
    e_tiktok_id = ctk.CTkEntry(scroll_frame, width=400)
    e_tiktok_id.pack()
    
    # --- PROXY UI ---
    v_use_proxy = ctk.BooleanVar(scroll_frame, value=False)
    ctk.CTkCheckBox(scroll_frame, text="Sử dụng Proxy", variable=v_use_proxy).pack(pady=(10, 2))
    
    ctk.CTkLabel(scroll_frame, text="Proxy (IP:Port:User:Pass):").pack(pady=2)
    e_proxy = ctk.CTkEntry(scroll_frame, width=400)
    e_proxy.pack()
    
    ctk.CTkLabel(scroll_frame, text="Limit/Ngày (0=No):").pack(pady=2)
    e_limit = ctk.CTkEntry(scroll_frame, width=400)
    e_limit.insert(0, "0")
    e_limit.pack()
    
    v_head = ctk.BooleanVar(scroll_frame, value=True)
    ctk.CTkCheckBox(scroll_frame, text="Headless", variable=v_head).pack(pady=5)

    ctk.CTkLabel(scroll_frame, text="Thiết bị kiểm thử:").pack(pady=(8, 2))
    v_device = StringVar(scroll_frame, value='desktop')
    ctk.CTkComboBox(scroll_frame, values=['desktop', 'pixel', 'iphone_x', 'custom'], variable=v_device).pack(pady=2)
    ctk.CTkLabel(scroll_frame, text="User-Agent tùy chỉnh (để trống nếu dùng preset):").pack(pady=2)
    e_custom_ua = ctk.CTkEntry(scroll_frame, width=400)
    e_custom_ua.pack(pady=2)
    ctk.CTkLabel(scroll_frame, text="WebRTC:").pack(pady=(8, 2))
    v_webrtc = StringVar(scroll_frame, value='controlled')
    ctk.CTkComboBox(scroll_frame, values=['controlled', 'block'], variable=v_webrtc).pack(pady=2)
    v_protection = ctk.BooleanVar(scroll_frame, value=True)
    ctk.CTkCheckBox(scroll_frame, text="Bảo vệ Canvas/WebGL/AudioContext", variable=v_protection).pack(pady=5)

    v_open_only = ctk.BooleanVar(scroll_frame, value=False)
    ctk.CTkCheckBox(scroll_frame, text="Chỉ mở khi có video mới", variable=v_open_only).pack(pady=5)
    
    v_proj = StringVar(scroll_frame, value='Mặc định')
    ctk.CTkComboBox(scroll_frame, values=list(projects.keys()), variable=v_proj).pack(pady=5)

    def save():
        nm = e_name.get().strip()
        if not nm or nm in profiles:
            messagebox.showerror("Lỗi", "Tên không hợp lệ")
            return
        fd = e_folder.get().strip()
        cp = e_chrome.get().strip()
        pj = v_proj.get()
        try: lm = int(e_limit.get().strip())
        except: lm = 0
        if lm < 0: lm = 0
        
        if not fd or not cp or pj not in projects:
            messagebox.showerror("Lỗi", "Thiếu thông tin")
            return
            
        fp_seed = nm + e_cookie.get() + str(time.time_ns())
        fingerprint = _generate_fingerprint(seed=fp_seed)
        fingerprint = apply_device_preset(fingerprint, v_device.get())
        if v_device.get() == 'custom' and e_custom_ua.get().strip():
            fingerprint['user_agent'] = e_custom_ua.get().strip()
        fingerprint['webrtc_policy'] = v_webrtc.get()
        fingerprint['fingerprint_protection'] = v_protection.get()
        fingerprint = ensure_fingerprint_defaults(fingerprint, seed=fp_seed)
        profiles[nm] = {
            'config': {
                "folder_path": fd, 
                "chrome_profile": cp, 
                "cookie_str": e_cookie.get(),
                "tiktok_id": _normalize_tiktok_id(e_tiktok_id.get()),
                "proxy_string": e_proxy.get().strip(),
                "use_proxy": v_use_proxy.get(),
                "headless": v_head.get(), 
                "open_only_when_video": v_open_only.get(),
                "max_uploads_per_day": lm,
                "fingerprint": fingerprint,
                "stats_today": 0,
                "stats_yesterday": 0,
                "stats_date": datetime.now().strftime('%Y-%m-%d')
            },
            'queue': queue.Queue(), 'observer': None, 'driver': None, 'running': False,
            'processed_files': set(), 'last_event_time': {}, 'uploading': False, 'project': pj,
            'uploads_today_count': 0,
            'uploads_yesterday_count': 0,
            'uploads_today_date': datetime.now().strftime('%Y-%m-%d')
        }
        projects[pj].add(nm)
        save_configs()
        dlg.destroy()
    ctk.CTkButton(dlg, text="Lưu", command=save).pack(pady=10)

# --- BATCH ADD FUNCTION (NEW) ---
def batch_add_profiles():
    if not _license_guard(): return
    
    dlg = ctk.CTkToplevel(root)
    dlg.title("Thêm hàng loạt (Batch Add)")
    dlg.geometry("600x500")
    dlg.grab_set() 
    
    ctk.CTkLabel(dlg, text="Nhập dữ liệu: Tên|Cookie|Proxy|ID TikTok (Mỗi dòng 1 profile)", font=("", 13, "bold")).pack(pady=5)
    
    txt_input = ctk.CTkTextbox(dlg, width=550, height=350)
    txt_input.pack(pady=5)
    txt_input.focus_set()

    BASE_DATA_DIR = app_base_dir() / "Auto_Data"
    
    def process_batch():
        raw_data = txt_input.get("1.0", "end").strip()
        if not raw_data:
            return

        lines = raw_data.split('\n')
        added_count = 0
        skipped_count = 0
        
        if 'Mặc định' not in projects:
            projects['Mặc định'] = set()

        for line in lines:
            line = line.strip()
            if not line: continue
            
            parts = line.split('|', 3)
            p_name = parts[0].strip()
            if not p_name:
                skipped_count += 1
                continue

            if p_name in profiles:
                update_status(f"[Batch] Bỏ qua {p_name} (Đã tồn tại).")
                skipped_count += 1
                continue

            p_cookie = parts[1].strip() if len(parts) > 1 else ""
            p_proxy = parts[2].strip() if len(parts) > 2 else ""
            p_tiktok_id = _normalize_tiktok_id(parts[3]) if len(parts) > 3 else ""
            
            safe_foldername = "".join([c for c in p_name if c.isalnum() or c in (' ', '-', '_')]).strip()
            if not safe_foldername: safe_foldername = f"Profile_{uuid.uuid4().hex[:8]}"

            profile_root = os.path.join(BASE_DATA_DIR, safe_foldername)
            video_dir = os.path.join(profile_root, "Video")
            chrome_dir = os.path.join(profile_root, "Profile")

            try:
                os.makedirs(video_dir, exist_ok=True)
                os.makedirs(chrome_dir, exist_ok=True)
            except Exception as e:
                update_status(f"[Batch] Lỗi tạo folder {p_name}: {e}")
                skipped_count += 1
                continue

            fp_seed = p_name + p_cookie + str(time.time_ns())
            fingerprint = _generate_fingerprint(seed=fp_seed)
            config = {
                "folder_path": video_dir,
                "chrome_profile": chrome_dir,
                "cookie_str": p_cookie,
                "proxy_string": p_proxy,
                "use_proxy": True if p_proxy else False,
                "headless": True,
                "open_only_when_video": False,
                "tiktok_id": p_tiktok_id,
                "max_uploads_per_day": 3,
                "fingerprint": fingerprint,
                "stats_today": 0,
                "stats_yesterday": 0,
                "stats_date": datetime.now().strftime('%Y-%m-%d')
            }

            profiles[p_name] = {
                'config': config,
                'queue': queue.Queue(), 'observer': None, 'driver': None, 'running': False,
                'processed_files': set(), 'last_event_time': {}, 'uploading': False, 
                'project': 'Mặc định', 
                'uploads_today_count': 0, 
                'uploads_yesterday_count': 0,
                'uploads_today_date': datetime.now().strftime('%Y-%m-%d')
            }
            projects['Mặc định'].add(p_name)
            added_count += 1

        save_configs()
        messagebox.showinfo("Hoàn tất", f"Đã thêm: {added_count}\nBỏ qua/Lỗi: {skipped_count}")
        dlg.destroy()

    ctk.CTkButton(dlg, text="Xử lý & Thêm", command=process_batch, fg_color="#16a34a", hover_color="#15803d").pack(pady=15)
# --------------------------------

def edit_profile():
    if not _license_guard(): return
    sel = tree.selection()
    if not sel: return
    nm = tree.item(sel[0])['values'][0]
    if profiles[nm].get('running') or profiles[nm].get('session_busy') or is_driver_valid(profiles[nm].get('manual_driver')):
        messagebox.showwarning('Sửa hồ sơ', 'Hãy Stop profile và đóng browser trước khi sửa.')
        return
    cfg = profiles[nm]['config']
    
    dlg = ctk.CTkToplevel(root)
    dlg.title("Sửa hồ sơ")
    dlg.geometry("600x800")
    
    scroll_frame = ctk.CTkScrollableFrame(dlg, width=580, height=520)
    scroll_frame.pack(fill='both', expand=True, padx=10, pady=(10, 0))
    
    ctk.CTkLabel(scroll_frame, text=f"Tên: {nm}").pack(pady=5)
    
    ctk.CTkLabel(scroll_frame, text="Thư mục video:").pack(pady=2)
    e_folder = ctk.CTkEntry(scroll_frame, width=400)
    e_folder.insert(0, cfg["folder_path"])
    e_folder.pack()
    ctk.CTkButton(scroll_frame, text="...", width=50, command=lambda: e_folder.insert(0, filedialog.askdirectory())).pack()
    
    ctk.CTkLabel(scroll_frame, text="Chrome User Data:").pack(pady=2)
    e_chrome = ctk.CTkEntry(scroll_frame, width=400)
    e_chrome.insert(0, cfg["chrome_profile"])
    e_chrome.pack()
    ctk.CTkButton(scroll_frame, text="...", width=50, command=lambda: e_chrome.insert(0, filedialog.askdirectory())).pack()
    
    ctk.CTkLabel(scroll_frame, text="Cookie:").pack(pady=2)
    e_cookie = ctk.CTkEntry(scroll_frame, width=400)
    e_cookie.insert(0, cfg.get("cookie_str", ""))
    e_cookie.pack()
    
    ctk.CTkLabel(scroll_frame, text="ID TikTok:").pack(pady=2)
    e_tiktok_id = ctk.CTkEntry(scroll_frame, width=400)
    e_tiktok_id.insert(0, cfg.get("tiktok_id", ""))
    e_tiktok_id.pack()
    
    # --- PROXY UI ---
    v_use_proxy = ctk.BooleanVar(scroll_frame, value=cfg.get("use_proxy", False))
    ctk.CTkCheckBox(scroll_frame, text="Sử dụng Proxy", variable=v_use_proxy).pack(pady=(10, 2))
    
    ctk.CTkLabel(scroll_frame, text="Proxy (IP:Port:User:Pass):").pack(pady=2)
    e_proxy = ctk.CTkEntry(scroll_frame, width=400)
    e_proxy.insert(0, cfg.get("proxy_string", ""))
    e_proxy.pack()
    
    ctk.CTkLabel(scroll_frame, text="Limit/Ngày:").pack(pady=2)
    e_limit = ctk.CTkEntry(scroll_frame, width=400)
    e_limit.insert(0, str(cfg.get("max_uploads_per_day", 0)))
    e_limit.pack()
    
    v_head = ctk.BooleanVar(scroll_frame, value=cfg.get("headless", True))
    ctk.CTkCheckBox(scroll_frame, text="Headless", variable=v_head).pack(pady=5)

    current_fp = ensure_fingerprint_defaults(cfg.get('fingerprint', {}), seed=nm + cfg.get('cookie_str', ''))
    ctk.CTkLabel(scroll_frame, text="Thiết bị kiểm thử:").pack(pady=(8, 2))
    v_device = StringVar(scroll_frame, value=current_fp.get('device_preset', 'desktop'))
    ctk.CTkComboBox(scroll_frame, values=['desktop', 'pixel', 'iphone_x', 'custom'], variable=v_device).pack(pady=2)
    ctk.CTkLabel(scroll_frame, text="User-Agent tùy chỉnh (chỉ dùng cho Custom):").pack(pady=2)
    e_custom_ua = ctk.CTkEntry(scroll_frame, width=400)
    if current_fp.get('device_preset') == 'custom':
        e_custom_ua.insert(0, current_fp.get('user_agent', ''))
    e_custom_ua.pack(pady=2)
    ctk.CTkLabel(scroll_frame, text="WebRTC:").pack(pady=(8, 2))
    v_webrtc = StringVar(scroll_frame, value=current_fp.get('webrtc_policy', 'controlled'))
    ctk.CTkComboBox(scroll_frame, values=['controlled', 'block'], variable=v_webrtc).pack(pady=2)
    v_protection = ctk.BooleanVar(scroll_frame, value=current_fp.get('fingerprint_protection', True))
    ctk.CTkCheckBox(scroll_frame, text="Bảo vệ Canvas/WebGL/AudioContext", variable=v_protection).pack(pady=5)
    geo = current_fp.get('geolocation') or {}
    geo_label = ctk.CTkLabel(
        scroll_frame,
        text=f"GeoIP: {current_fp.get('timezone', 'chưa tra')} | "
             f"{geo.get('latitude', '-')} / {geo.get('longitude', '-')}",
    )
    geo_label.pack(pady=2)

    def refresh_geo():
        proxy_data = parse_proxy_string(e_proxy.get().strip()) if v_use_proxy.get() else None
        if not proxy_data:
            geo_label.configure(text="GeoIP: cần proxy hợp lệ")
            return
        if _refresh_profile_geoip(nm, cfg, proxy_data, force=True):
            save_configs()
            updated = cfg.get('fingerprint', {})
            updated_geo = updated.get('geolocation') or {}
            geo_label.configure(
                text=f"GeoIP: {updated.get('timezone', 'chưa tra')} | "
                     f"{updated_geo.get('latitude', '-')} / {updated_geo.get('longitude', '-')}",
            )
    ctk.CTkButton(scroll_frame, text="Làm mới GeoIP", command=refresh_geo).pack(pady=4)

    v_open_only = ctk.BooleanVar(scroll_frame, value=cfg.get("open_only_when_video", False))
    ctk.CTkCheckBox(scroll_frame, text="Chỉ mở khi có video mới", variable=v_open_only).pack(pady=5)
    
    def save():
        try: lm = int(e_limit.get().strip())
        except: lm = 0
        cfg.update({
            "folder_path": e_folder.get().strip(),
            "chrome_profile": e_chrome.get().strip(),
            "cookie_str": e_cookie.get(),
            "tiktok_id": _normalize_tiktok_id(e_tiktok_id.get()),
            "proxy_string": e_proxy.get().strip(),
            "use_proxy": v_use_proxy.get(),
            "headless": v_head.get(),
            "open_only_when_video": v_open_only.get(),
            "max_uploads_per_day": max(0, lm)
        })
        fingerprint = apply_device_preset(current_fp, v_device.get())
        if v_device.get() == 'custom' and e_custom_ua.get().strip():
            fingerprint['user_agent'] = e_custom_ua.get().strip()
        fingerprint['webrtc_policy'] = v_webrtc.get()
        fingerprint['fingerprint_protection'] = v_protection.get()
        cfg['fingerprint'] = ensure_fingerprint_defaults(
            fingerprint,
            seed=nm + cfg.get('cookie_str', ''),
        )
        save_configs()
        dlg.destroy()
    ctk.CTkButton(dlg, text="Lưu", command=save).pack(pady=10)

def rename_profile():
    if not _license_guard(): return
    sel = tree.selection()
    if not sel: return
    old = tree.item(sel[0])['values'][0]
    if profiles[old]['running'] or profiles[old].get('session_busy') or is_driver_valid(profiles[old].get('manual_driver')):
        messagebox.showerror("Lỗi", "Hãy dừng hồ sơ và đóng browser trước")
        return
    dlg = ctk.CTkToplevel(root)
    dlg.title("Đổi tên")
    dlg.geometry("300x150")
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
        save_configs()
        dlg.destroy()
    ctk.CTkButton(dlg, text="Lưu", command=save).pack(pady=10)

def delete_profile():
    if not _license_guard(): return
    sel = tree.selection()
    if not sel: return
    nm = tree.item(sel[0])['values'][0]
    if profiles[nm]['running'] or profiles[nm].get('session_busy') or is_driver_valid(profiles[nm].get('manual_driver')):
        messagebox.showerror("Lỗi", "Hãy dừng hồ sơ và đóng browser trước")
        return
    ok = messagebox.askyesno("Xác nhận xoá hồ sơ",
        f"Bạn có chắc muốn xoá hồ sơ '{nm}' khỏi cấu hình?\n\n"
        "Thao tác này chỉ xoá hồ sơ khỏi danh sách app.\n"
        "Không xoá thư mục video, Chrome profile hoặc dữ liệu trên ổ đĩa.")
    if not ok: return
    p = profiles[nm].get('project')
    if p in projects: projects[p].discard(nm)
    del profiles[nm]
    save_configs()
    update_status(f"[UI] Đã xoá hồ sơ '{nm}'.")

def open_browser():
    if not _license_guard(): return
    sel = tree.selection()
    if not sel: return
    nm = tree.item(sel[0])['values'][0]
    cfg = profiles[nm]['config']
    if profiles[nm].get('running') or profiles[nm].get('uploading') or profiles[nm].get('session_busy'):
        messagebox.showwarning('Mở Chrome', 'Hãy Stop profile trước khi mở browser thủ công.')
        return
    if is_driver_valid(profiles[nm].get('manual_driver')):
        messagebox.showwarning('Mở Chrome', 'Browser của profile này đang mở.')
        return
    
    update_status(f"[{nm}] Kiểm tra cập nhật Driver...")
    _set_profile_ui(nm, browser='Đang mở', last_error='')
    try:
        seleniumwire_options = {}
        proxy_data = None
        if cfg.get('use_proxy', False):
            _set_profile_ui(nm, proxy='Đang kiểm tra')
            proxy_data = parse_proxy_string(cfg.get('proxy_string', ''))
            if proxy_data:
                seleniumwire_options = _build_seleniumwire_options(proxy_data)
                if DEBUG_SELENIUM_WIRE:
                    mode = "Selenium Wire debug"
                elif proxy_data.get('user') and proxy_data.get('pass') and _using_orbita_browser():
                    mode = "Orbita proxy auth"
                elif proxy_data.get('user') and proxy_data.get('pass'):
                    mode = "Chrome proxy extension"
                else:
                    mode = "Chrome native proxy"
                update_status(f"[{nm}] [DEBUG] Config Proxy ({mode}): {proxy_data['ip']}")

        geo_changed = _refresh_profile_geoip(nm, cfg, proxy_data)
        if geo_changed:
            save_configs()
        opt = _build_fast_chrome_options(cfg, block_images=False, force_visible=True)

        proxy_ext_dir = _apply_chrome_proxy_options(opt, cfg, proxy_data)
        if proxy_ext_dir:
            if proxy_ext_dir == "orbita-proxy-auth":
                update_status(f"[{nm}] [DEBUG] Đã cấu hình Orbita proxy auth")
            else:
                update_status(f"[{nm}] [DEBUG] Đã nạp proxy extension: {proxy_ext_dir}")
        
        driver = None
        path = None
        for launch_attempt in range(2):
            try:
                path = _ensure_profile_driver(nm)
                svc = Service(path)
                chrome_path = _find_preferred_chrome_executable()
                update_status(f"[{nm}] [DEBUG] Browser ({_browser_mode_label()}): {chrome_path or 'không tìm thấy'}")
                driver_module = _active_webdriver_module()
                if DEBUG_SELENIUM_WIRE:
                    driver = driver_module.Chrome(service=svc, options=opt, seleniumwire_options=seleniumwire_options)
                else:
                    driver = driver_module.Chrome(service=svc, options=opt)
                break
            except Exception as e:
                if launch_attempt == 0 and _is_driver_version_mismatch_error(e):
                    chrome_path, chrome_version, chrome_major = _get_chrome_version()
                    driver_version, driver_major = _get_chromedriver_version(path)
                    update_status(
                        f"[{nm}] Phát hiện ChromeDriver lệch phiên bản Chrome. "
                        f"Browser={chrome_version or chrome_path}, driver={driver_version or path}. Đang tải lại driver..."
                    )
                    with driver_install_lock:
                        _invalidate_chromedriver_cache("driver/browser version mismatch")
                        try:
                            _profile_driver_path(nm).unlink(missing_ok=True)
                        except Exception:
                            pass
                    continue
                raise
        driver.implicitly_wait(0)
        driver.set_page_load_timeout(PAGELOAD_TIMEOUT)
        driver.set_script_timeout(SCRIPT_TIMEOUT)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        _inject_fingerprint_js(driver, nm)
        _enable_aux_resource_blocking(driver, nm)
        _set_profile_ui(nm, browser='Đã mở')
        
        # --- VERIFY PROXY IP ---
        if proxy_data:
            update_status(f"[{nm}] [DEBUG] Đang check IP...")
            try:
                is_match, current_ip = verify_proxy_ip(driver, proxy_data['ip'])
                if not is_match:
                    driver.refresh()
                    _wait_document_ready(driver, timeout=3.0)
                    is_match, current_ip = verify_proxy_ip(driver, proxy_data['ip'])
                if not is_match:
                    _set_profile_ui(nm, proxy='Sai IP', last_error=f"Proxy sai IP: {current_ip}")
                    update_status(f"[{nm}] LỖI PROXY: IP THỰC TẾ ({current_ip}) != PROXY ({proxy_data['ip']}).")
                    driver.quit()
                    messagebox.showerror("Lỗi Proxy", f"IP không khớp: {current_ip} != {proxy_data['ip']}")
                    return
                else:
                    _set_profile_ui(nm, proxy=f"OK: {current_ip}")
                    update_status(f"[{nm}] Proxy OK: {current_ip}")
            except Exception as e:
                _set_profile_ui(nm, proxy='Proxy lỗi', last_error=str(e))
                update_status(f"[{nm}] Lỗi check Proxy: {e}")
                try: driver.quit()
                except: pass
                messagebox.showerror("Lỗi Proxy", str(e))
                return
        
        _prepare_tiktok_cookies(driver, nm, cfg, require_upload_ready=False)
        driver.get(TIKTOK_BASE_URL)
        profiles[nm]['manual_driver'] = driver
        _set_profile_ui(nm, browser='Chrome đang mở')
        messagebox.showinfo("Info", "Đóng trình duyệt khi xong.")
        
        def _wait_close():
            while True:
                try:
                    if not driver.window_handles: break
                except: break
                time.sleep(0.5)
            try: driver.quit()
            except: pass
            profiles[nm]['manual_driver'] = None
            _set_profile_ui(nm, browser='Đã đóng', login='Chờ lấy cookie')
            update_status(f"[{nm}] Browser thủ công đã đóng. Bấm 'Lấy Cookie TikTok' trong menu chuột phải để đồng bộ session.")
        threading.Thread(target=_wait_close, daemon=True).start()
    except Exception as e:
        _set_profile_ui(nm, browser='Bị lỗi', last_error=str(e))
        messagebox.showerror("Lỗi", str(e))

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
def on_closing():
    try:
        youtube_monitor.stop_monitor()
    except Exception as e:
        update_status(f"[YouTube] Lỗi dừng monitor khi đóng app: {e}")
    stop_all_in_project()
    root.after(2000, root.destroy)

def change_license_key():
    global LICENSE_OK, LICENSE_KEY, LICENSE_INFO
    LICENSE_OK = False
    LICENSE_KEY = None
    LICENSE_INFO = {}
    _set_ui_enabled(False)
    _license_dialog(on_success=lambda: _set_ui_enabled(True))

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
            driver = profiles[profile_name]['driver']
            artifacts = app_base_dir() / 'temp_dl' / 'upload_test_verify'
            artifacts.mkdir(parents=True, exist_ok=True)
            screenshot_path = artifacts / f'round_{round_number:02d}.png'
            text_path = artifacts / f'round_{round_number:02d}.txt'
            body_text = driver.execute_script("return document.body ? document.body.innerText : '';") or ''
            text_path.write_text(body_text, encoding='utf-8')
            driver.save_screenshot(str(screenshot_path))
            print(f"UPLOAD_VERIFY_ARTIFACT={json.dumps({'text': str(text_path), 'screenshot': str(screenshot_path)}, ensure_ascii=True)}", flush=True)
            finish(True, 'đã chụp trang quản lý nội dung')
        except Exception as error:
            finish(False, f'lỗi chụp trang quản lý nội dung: {error}')

    def open_content_page():
        try:
            driver = profiles[profile_name]['driver']
            driver.get('https://www.tiktok.com/tiktokstudio/content')
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
        if profile and profile.get('running') and is_driver_valid(driver) and _has_upload_page_signal(driver):
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
root.title("TikTok Auto Uploader (Pro Optimized)")
root.geometry("1380x920")
root.minsize(1180, 760)
root.configure(fg_color="#f3f4f6")

selected_project_var = StringVar(master=root)
filter_var = StringVar(master=root, value="")
theme_var = StringVar(master=root, value="System")
scale_var = StringVar(master=root, value="100%")
header_total_label = StringVar(master=root, value="0")
header_running_label = StringVar(master=root, value="0")
header_project_label = StringVar(master=root, value=ALL_OPTION)

configure_ttk_styles()

ui_state = {
    'selected_project_var': selected_project_var,
    'filter_var': filter_var,
    'scale_var': scale_var,
    'header_total_label': header_total_label,
    'header_running_label': header_running_label,
    'header_project_label': header_project_label,
}

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
    'delete_profile': delete_profile,
    'rename_profile': rename_profile,
    'assign_to_project': assign_to_project,
    'show_statistics_board': show_statistics_board,
    'open_browser': open_browser,
    'get_tiktok_cookies': get_tiktok_cookies,
    'reset_fingerprint': reset_fingerprint,
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
    'sort_tree': _treeview_sort_column,
    'youtube_monitor': youtube_monitor_handlers,
    'activity': activity_handlers,
}
ui_widgets = build_dashboard(root, ui_state, ui_handlers)

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

def _start_youtube_monitor_safe():
    def _run():
        try:
            ok, msg = youtube_monitor.start_monitor()
            update_status(f"[YouTube] {msg}")
        except Exception as e:
            update_status(f"[YouTube] Auto-start lỗi: {e}")
    threading.Thread(target=_run, daemon=True).start()

selected_project_var.trace('w', update_profile_list)
filter_var.trace('w', update_profile_list)
scale_var.trace('w', _apply_scale)

def _on_tree_right_click(event):
    iid = tree.identify_row(event.y)
    if not iid: return
    if iid not in tree.selection():
        tree.selection_set(iid)
    ctx_menu.post(event.x_root, event.y_root)

tree.bind("<Button-3>", _on_tree_right_click)

def _tick():
    # Cập nhật UI
    _refresh_status_bar()
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

require_license_then_boot()
_run_auto6_watcher_test_from_env()
_run_single_upload_test_from_env()
root.protocol("WM_DELETE_WINDOW", on_closing)
root.mainloop()
