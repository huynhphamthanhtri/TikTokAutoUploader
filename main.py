import os
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
)
from config_store import (
    build_configs_payload,
    save_configs_file,
    load_configs_file,
    normalize_loaded_config,
    build_runtime_profiles,
)
import youtube_monitor
from youtube_monitor.activity import append_activity, clear_activity_log, get_activity_logs, get_activity_mtime, get_activity_stats, lookup_download
from version import __version__ as CURRENT_VERSION, APP_NAME, GITHUB_REPO_OWNER, GITHUB_REPO_NAME
from updater import GitHubReleaseUpdater, get_current_version
from updater_config import load_updater_config, save_updater_config
import logging
import sys
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
UPLOAD_PHASE_STALL_TIMEOUT = 180
UPLOAD_HARD_TIMEOUT = 420
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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
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
    return {
        "user_agent": ua,
        "window_width": w,
        "window_height": h,
        "lang": lang,
        "hardware_concurrency": cores,
        "webgl_vendor": webgl_vendor,
        "webgl_renderer": webgl_renderer,
        "canvas_noise": canvas_noise,
    }

def _apply_fingerprint_to_options(chrome_options, fp):
    if fp.get("user_agent"):
        chrome_options.add_argument(f"user-agent={fp['user_agent']}")
    w = fp.get("window_width", 1920)
    h = fp.get("window_height", 1080)
    chrome_options.add_argument(f"--window-size={w},{h}")
    lang = fp.get("lang", "en-US")
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
        js = FINGERPRINT_JS
        js = js.replace('{CANVAS_NOISE}', str(fp.get('canvas_noise', 0.001)))
        js = js.replace('{WEBGL_VENDOR}', fp.get('webgl_vendor', 'Google Inc. (Intel)'))
        js = js.replace('{WEBGL_RENDERER}', fp.get('webgl_renderer', 'Intel Iris OpenGL Engine'))
        js = js.replace('{HARDWARE_CONCURRENCY}', str(fp.get('hardware_concurrency', 4)))
        js = js.replace('{LANG}', fp.get('lang', 'en-US').replace("'", ""))
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": js})
        update_status(f"[{profile_name}] [DEBUG] Đã inject fingerprint JS")
    except Exception as e:
        update_status(f"[{profile_name}] [WARN] Lỗi inject fingerprint: {e}")

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

# --- KHÓA AN TOÀN CHO DRIVER (FIX LỖI ACCESS DENIED) ---
driver_install_lock = threading.Lock()
GLOBAL_DRIVER_PATH = None  # Biến lưu đường dẫn driver để tránh cài đặt lại nhiều lần
GLOBAL_CHROME_PATH = None
GLOBAL_BROWSER_MODE = None
GLOBAL_IS_ORBITA = None
PROXY_OK_CACHE = {}
LOCAL_DRIVER_CACHE_DIR = app_base_dir() / "temp_dl" / "driver_cache"
LOCAL_CHROMEDRIVER_PATH = LOCAL_DRIVER_CACHE_DIR / "chromedriver.exe"
LOCAL_DRIVER_METADATA_PATH = LOCAL_DRIVER_CACHE_DIR / "metadata.json"
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
        return
    try:
        live_cookies = driver.get_cookies()
        if live_cookies:
            cookie_json = json.dumps(live_cookies, ensure_ascii=False)
            profiles[profile_name]['config']['cookie_str'] = cookie_json
            _save_cookie_injection_metadata(profile_name, cookie_json)
            save_configs()
    except Exception:
        pass

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
            if 'fingerprint' not in prof.get('config', {}):
                seed = name + prof.get('config', {}).get('cookie_str', '') + str(time.time_ns())
                prof['config']['fingerprint'] = _generate_fingerprint(seed=seed)
            profiles[name] = prof

        projects.clear()
        projects.update({k: set(v) for k, v in loaded_projects.items()})
        if 'Mặc định' not in projects: projects['Mặc định'] = set()
        
        update_project_dropdown()
        selected_project_var.set(ALL_OPTION)
        update_profile_list()
    except FileNotFoundError:
        projects['Mặc định'] = set()
        update_project_dropdown()
        selected_project_var.set(ALL_OPTION)

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
    def __init__(self, profile_name): self.profile_name = profile_name
    def on_created(self, event):
        if event.is_directory: return
        file_path = event.src_path
        if not file_path.lower().endswith(VIDEO_EXTENSIONS): return
        try: file_size = os.path.getsize(file_path)
        except Exception: file_size = 0
        if file_size > MAX_FILE_SIZE or file_size == 0:
            time.sleep(FILE_STABLE_INTERVAL)
            try: file_size = os.path.getsize(file_path)
            except Exception: pass
            if file_size > MAX_FILE_SIZE or file_size == 0:
                update_status(f"[{self.profile_name}] Kích thước video không hợp lệ.")
                return
        current_time = time.time()
        last_time = profiles[self.profile_name]['last_event_time'].get(file_path, 0)
        if current_time - last_time < 0.8: return
        profiles[self.profile_name]['last_event_time'][file_path] = current_time
        if not is_file_stable(file_path, FILE_STABLE_CHECKS, FILE_STABLE_INTERVAL):
            time.sleep(FILE_STABLE_CHECKS * FILE_STABLE_INTERVAL)
        try:
            config = profiles[self.profile_name]['config']
            if config.get('open_only_when_video', False):
                watch_started_at = profiles[self.profile_name].get('watch_started_at', 0)
                file_mtime = os.path.getmtime(file_path)
                if file_mtime <= watch_started_at:
                    update_status(f"[{self.profile_name}] Bỏ qua video cũ: {Path(file_path).name}")
                    return
        except Exception:
            pass
        if FAST_MODE: logging.warning(f"[{self.profile_name}] Phát hiện video mới.")
        _set_profile_ui(self.profile_name, upload='Có video mới')
        update_status(f"[{self.profile_name}] Phát hiện video mới: {Path(file_path).name}")
        profiles[self.profile_name]['queue'].put(file_path)

# =========================
# Selenium Driver (Optimized with Selenium Wire)
# =========================
# ------------------------------

def _build_fast_chrome_options(config, block_images=True, force_visible=False):
    chrome_options = Options()
    chrome_binary = _find_bundled_chrome_executable()
    if chrome_binary:
        chrome_options.binary_location = chrome_binary
    chrome_options.add_argument(f"--user-data-dir={config['chrome_profile']}")
    
    fp = config.get('fingerprint', _generate_fingerprint(config.get('cookie_str', '')))
    _apply_fingerprint_to_options(chrome_options, fp)
    
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
    target_dir = str(profiles[profile_name]['config']['chrome_profile']).lower()
    killed_count = 0
    
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if not proc.is_running(): continue
                name = proc.info['name'].lower()
                cmdline = proc.info['cmdline']

                if name in ('chrome.exe', 'chromedriver.exe', 'chrome', 'chromedriver'):
                    if cmdline:
                        cmd_str = " ".join(cmdline).lower()
                        if f"user-data-dir={target_dir}" in cmd_str:
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
                _timing_log(profile_name, "proxy_config", step_start)
            else:
                _set_profile_ui(profile_name, proxy='Tắt')
            
            # --- FIX: SINGLETON DRIVER PATH ---
            step_start = time.perf_counter()
            bundled_driver = _find_bundled_chromedriver_executable()
            if bundled_driver:
                GLOBAL_DRIVER_PATH = bundled_driver
            elif GLOBAL_DRIVER_PATH is None:
                with driver_install_lock:
                    if GLOBAL_DRIVER_PATH is None:
                        GLOBAL_DRIVER_PATH = resolve_chromedriver_path()
            
            driver_path = GLOBAL_DRIVER_PATH
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

            if _is_proxy_init_error(e):
                _set_profile_ui(profile_name, status='Lỗi', browser='Bị lỗi', last_error=str(e))
                update_status(f"[{profile_name}] Lỗi proxy, không retry: {e}")
                raise

            if _is_driver_version_mismatch_error(e) and not driver_refreshed_for_mismatch:
                driver_refreshed_for_mismatch = True
                chrome_path, chrome_version, chrome_major = _get_chrome_version()
                driver_version, driver_major = _get_chromedriver_version(GLOBAL_DRIVER_PATH)
                update_status(
                    f"[{profile_name}] Phát hiện ChromeDriver lệch phiên bản Chrome. "
                    f"Browser={chrome_version or chrome_path}, driver={driver_version or GLOBAL_DRIVER_PATH}. Đang tải lại driver..."
                )
                with driver_install_lock:
                    _invalidate_chromedriver_cache("driver/browser version mismatch")
                    GLOBAL_DRIVER_PATH = resolve_chromedriver_path()
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
                raise Exception("TikTok chuyển về trang đăng nhập. Cookie/session không hợp lệ hoặc đã hết hạn.")
            if _has_upload_page_signal(driver):
                return
            time.sleep(0.2)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-e2e='select_video_container']")))
        except TimeoutException:
            if "/login" in (driver.current_url or "").lower():
                _set_profile_ui(profile_name, login='Cần đăng nhập lại', last_error='TikTok yêu cầu đăng nhập lại')
                raise Exception("TikTok chuyển về trang đăng nhập. Cookie/session không hợp lệ hoặc đã hết hạn.")
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
    _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=5)
    if _has_visible_tiktok_modal(driver):
        raise Exception('Có popup TikTok chưa xử lý, chưa bấm Đăng để tránh click sai.')
    try:
        ActionChains(driver).move_to_element(post_button).click().perform()
        return
    except Exception as e:
        update_status(f"[{profile_name}] [DEBUG] Click nút Đăng bằng ActionChains lỗi, thử JS click: {e}")
        _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=5)
        if _has_visible_tiktok_modal(driver):
            raise Exception('Có popup TikTok chưa xử lý trước JS click Đăng.')
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", post_button)
        time.sleep(0.2)
        driver.execute_script("arguments[0].click();", post_button)

def _has_blocking_post_modal(driver):
    try:
        return bool(driver.execute_script(
            """
            const text = document.body ? document.body.innerText.toLowerCase() : '';
            const blocking = [
              'couldn\'t upload', 'could not upload', 'failed to upload', 'upload failed',
              'something went wrong', 'try again', 'vi phạm', 'lỗi', 'không thể đăng'
            ];
            return blocking.some(item => text.includes(item));
            """
        ))
    except Exception:
        return False

def _wait_post_submission_confirmed(driver, profile_name, short_name, timeout=25):
    end_time = time.time() + timeout
    success_markers = [
        'your video has been posted', 'video has been posted', 'post has been uploaded',
        'content under review', 'under review', 'đã đăng', 'đang xét duyệt'
    ]
    success_urls = ('/tiktokstudio/content', '/creator-center/content', '/manage')

    while time.time() < end_time:
        if not is_driver_valid(driver):
            raise WebDriverException('Mất kết nối trình duyệt khi chờ TikTok xác nhận đăng video')
        _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=2)
        if _has_blocking_post_modal(driver):
            raise Exception('TikTok hiển thị popup/lỗi sau khi bấm Đăng. Chưa xác nhận đăng thành công.')
        try:
            current_url = (driver.current_url or '').lower()
            body_text = driver.execute_script("return document.body ? document.body.innerText.toLowerCase() : '';") or ''
            if any(marker in body_text for marker in success_markers):
                update_status(f"[{profile_name}] TikTok đã xác nhận đăng/xử lý video: {short_name}")
                return True
            if any(marker in current_url for marker in success_urls) and '/upload' not in current_url:
                update_status(f"[{profile_name}] TikTok đã chuyển sang trang quản lý nội dung sau khi đăng: {short_name}")
                return True
        except Exception as e:
            if isinstance(e, WebDriverException):
                raise
        time.sleep(1.0)

    raise TimeoutException(f"Chưa thấy TikTok xác nhận đăng thành công sau {timeout}s: {short_name}")

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
                _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=2)
                last_content_check_ts = now

            _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=1)
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

            if idle_for >= UPLOAD_STALL_TIMEOUT:
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
    try:
        file_name = Path(video_path).name
        short_name = shorten_filename(file_name)
        ensure_driver(profile_name)
        driver = profiles[profile_name]['driver']
        request_context = _prepare_request_assist_context(profile_name, driver)
        stall_retry_used = False
        recovered_after_failure = False
        max_attempts = RETRY_COUNT + 1  # chỉ thêm 1 lần tải lại nhanh khi phát hiện treo
        for attempt in range(1, max_attempts + 1):
            post_clicked = False
            try:
                update_status(f"[{profile_name}] Đang đăng: {short_name}")
                _set_profile_ui(profile_name, upload='Đang tải video', last_error='')
                if not _ensure_upload_container_ready(driver, quick_only=True):
                    _ensure_upload_container_ready(driver, quick_only=False)
                try:
                    file_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file'][accept*='video'], input[type='file']")))
                except TimeoutException:
                    _safe_open_upload_page(driver, profile_name)
                    file_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file'][accept*='video'], input[type='file']")))
                file_input.send_keys(video_path)
                _set_profile_ui(profile_name, upload='Đã chọn video')
                update_status(f"[{profile_name}] [DEBUG] Đã đưa video vào trình tải lên của TikTok: {short_name}")
                time.sleep(UPLOAD_POST_SENDKEYS_SETTLE_SECONDS)
                _dismiss_cancel_upload_popup(driver, profile_name)
                _dismiss_cancel_buttons_best_effort(driver)
                _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=5)
                # -------------------------------------------------------------------

                ready, ready_reason, ready_post_button = _watch_upload_until_ready_or_stalled(
                    driver,
                    profile_name,
                    file_name,
                    request_start_index=request_context.get('start_index', 0) if request_context else 0,
                )
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
                _dismiss_known_tiktok_popups_fast(driver, profile_name, max_rounds=5)
                _click_post_button(driver, profile_name, post_button)
                post_clicked = True
                _set_profile_ui(profile_name, upload='Đã gửi lệnh đăng')
                update_status(f"[{profile_name}] Đã gửi lệnh đăng: {short_name}")

                _set_profile_ui(profile_name, upload='Chờ TikTok xác nhận')
                _wait_post_submission_confirmed(driver, profile_name, short_name)

                try: os.remove(video_path)
                except Exception: pass
                
                if not profiles[profile_name]['config'].get('open_only_when_video', False) and not _ensure_upload_container_ready(driver, quick_only=True):
                    _safe_open_upload_page(driver, profile_name)
                _capture_request_trace(profile_name, driver, file_name, request_context, outcome='success')
                _export_live_cookies_to_config(driver, profile_name)
                return True
            except (InvalidSessionIdException, WebDriverException):
                if post_clicked:
                    last_error = 'driver_session_lost_after_post'
                    _set_profile_ui(profile_name, browser='Mất kết nối', upload='Chưa xác nhận', last_error='Mất kết nối sau khi bấm Đăng')
                    update_status(f"[{profile_name}] Trình duyệt mất kết nối sau khi bấm Đăng, không retry video này để tránh đăng trùng: {short_name}")
                    _recover_upload_page_after_failure(profile_name, driver, last_error, force_reopen=True)
                    recovered_after_failure = True
                    break
                last_error = 'driver_session_lost'
                _set_profile_ui(profile_name, browser='Mất kết nối', upload='Đăng lỗi', last_error='Mất kết nối trình duyệt')
                update_status(f"[{profile_name}] Mất kết nối khi đăng. Kết nối lại...")
                _force_reopen_driver(profile_name, last_error)
                driver = profiles[profile_name]['driver']
                continue
            except Exception as e:
                last_error = str(e)
                if "File not found" in str(e):
                    _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=str(e))
                    trace_path = _capture_request_trace(profile_name, driver, file_name, request_context, outcome='file_not_found')
                    _append_failed_upload_log(profile_name, file_name, str(e), trace_path=trace_path, outcome='file_not_found')
                    return False
                if "Video bị treo trước khi nút Đăng sẵn sàng" in str(e) and not stall_retry_used:
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
        try:
            trace_path = _capture_request_trace(profile_name, driver, file_name, request_context, outcome='fatal')
            _append_failed_upload_log(profile_name, file_name, str(e), trace_path=trace_path, outcome='fatal')
        except Exception:
            pass
        update_status(f"[{profile_name}] Lỗi nghiêm trọng: {e}")
        _set_profile_ui(profile_name, upload='Đăng lỗi', last_error=str(e))
        return False
    finally:
        profiles[profile_name]['uploading'] = False

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
            tag = f"v{__version__}"
            base_url = f"https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/download/{tag}"
            total = len(missing)
            for i, (local_name, info) in enumerate(missing.items()):
                _update_status(f"Đang tải {local_name}...", (i + 0.1) / total)
                asset_name = info["asset"].format(version=__version__)
                url = f"{base_url}/{asset_name}"

                if info["type"] == "zip_dir":
                    temp_dir = app_base_dir() / "temp_dl" / "resources"
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    zip_path = temp_dir / asset_name
                    requests.get(url, stream=True, timeout=30).raise_for_status()
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


def _run_background_update_check():
    from updater import run_background_check as _bg_check

    def _on_update(result):
        latest = result.get("latest_version", "?")
        current = result.get("current_version", "?")
        if messagebox.askyesno(
            "Cập nhật",
            f"Phiên bản mới v{latest} (hiện tại v{current}).\nTải và cập nhật ngay?",
        ):
            _do_download_update(result)

    _bg_check(GITHUB_REPO_OWNER, GITHUB_REPO_NAME, "",
              app_base_dir(),
              on_update=_on_update,
              on_error=lambda err: None,
              on_current=lambda ver: None,
              schedule=root.after)


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
            root.after(0, lambda: _on_result(result))
        except Exception as e:
            root.after(0, lambda: _on_error(str(e)))

    threading.Thread(target=_run, daemon=True).start()


def _show_update_available_dialog(result):
    latest = result.get("latest_version", "?")
    current = result.get("current_version", "?")

    running_count = sum(1 for n in running_profiles if profiles.get(n, {}).get("running"))
    msg = f"Phiên bản mới: v{latest} (hiện tại: v{current})."
    if running_count > 0:
        msg += f"\nSẽ dừng {running_count} profile trước khi cập nhật."

    if not messagebox.askyesno("Có bản cập nhật", msg + "\n\nCập nhật ngay?"):
        return

    if running_count > 0:
        update_status("[Update] Đang dừng profiles...")
        _stop_all_profiles()

    _do_download_update(result)


def _do_download_update(result):
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

    def _progress(ratio):
        try:
            progress.set(ratio)
            dlg.update_idletasks()
        except Exception:
            pass

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
                root.after(0, lambda: _done(False, "File tải về không hợp lệ (thiếu exe hoặc _internal)."))
                return

            script = updater.write_update_script(extract_dir)
            root.after(0, lambda: _done(True, "Sẵn sàng cập nhật. Ứng dụng sẽ tự động đóng và khởi động lại."))
            root.after(2000, lambda: updater.launch_update(script))
            root.after(2500, root.destroy)
        except Exception as e:
            root.after(0, lambda: _done(False, str(e)))

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
    
    if name not in profiles or profiles[name]['running']: return
    
    config = profiles[name]['config']

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
    dlg.geometry("600x750")
    
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
        profiles[nm] = {
            'config': {
                "folder_path": fd, 
                "chrome_profile": cp, 
                "cookie_str": e_cookie.get(),
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
    
    ctk.CTkLabel(dlg, text="Nhập dữ liệu: Tên|Cookie|Proxy (Mỗi dòng 1 profile)", font=("", 13, "bold")).pack(pady=5)
    
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
            
            parts = line.split('|')
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
    cfg = profiles[nm]['config']
    
    dlg = ctk.CTkToplevel(root)
    dlg.title("Sửa hồ sơ")
    dlg.geometry("600x650")
    
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

    v_open_only = ctk.BooleanVar(scroll_frame, value=cfg.get("open_only_when_video", False))
    ctk.CTkCheckBox(scroll_frame, text="Chỉ mở khi có video mới", variable=v_open_only).pack(pady=5)
    
    def save():
        try: lm = int(e_limit.get().strip())
        except: lm = 0
        cfg.update({
            "folder_path": e_folder.get().strip(),
            "chrome_profile": e_chrome.get().strip(),
            "cookie_str": e_cookie.get(),
            "proxy_string": e_proxy.get().strip(),
            "use_proxy": v_use_proxy.get(),
            "headless": v_head.get(),
            "open_only_when_video": v_open_only.get(),
            "max_uploads_per_day": max(0, lm)
        })
        save_configs()
        dlg.destroy()
    ctk.CTkButton(dlg, text="Lưu", command=save).pack(pady=10)

def rename_profile():
    if not _license_guard(): return
    sel = tree.selection()
    if not sel: return
    old = tree.item(sel[0])['values'][0]
    if profiles[old]['running']:
        messagebox.showerror("Lỗi", "Hãy dừng hồ sơ trước")
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
    if profiles[nm]['running']:
        messagebox.showerror("Lỗi", "Hãy dừng hồ sơ trước")
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
    global GLOBAL_DRIVER_PATH
    sel = tree.selection()
    if not sel: return
    nm = tree.item(sel[0])['values'][0]
    cfg = profiles[nm]['config']
    
    update_status(f"[{nm}] Kiểm tra cập nhật Driver...")
    _set_profile_ui(nm, browser='Đang mở', last_error='')
    try:
        opt = _build_fast_chrome_options(cfg, block_images=False, force_visible=True)
        
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

        proxy_ext_dir = _apply_chrome_proxy_options(opt, cfg, proxy_data)
        if proxy_ext_dir:
            if proxy_ext_dir == "orbita-proxy-auth":
                update_status(f"[{nm}] [DEBUG] Đã cấu hình Orbita proxy auth")
            else:
                update_status(f"[{nm}] [DEBUG] Đã nạp proxy extension: {proxy_ext_dir}")
        
        driver = None
        for launch_attempt in range(2):
            try:
                bundled_driver = _find_bundled_chromedriver_executable()
                if bundled_driver:
                    GLOBAL_DRIVER_PATH = bundled_driver
                elif GLOBAL_DRIVER_PATH is None:
                    with driver_install_lock:
                        if GLOBAL_DRIVER_PATH is None:
                            GLOBAL_DRIVER_PATH = resolve_chromedriver_path()
                path = GLOBAL_DRIVER_PATH
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
                    driver_version, driver_major = _get_chromedriver_version(GLOBAL_DRIVER_PATH)
                    update_status(
                        f"[{nm}] Phát hiện ChromeDriver lệch phiên bản Chrome. "
                        f"Browser={chrome_version or chrome_path}, driver={driver_version or GLOBAL_DRIVER_PATH}. Đang tải lại driver..."
                    )
                    with driver_install_lock:
                        _invalidate_chromedriver_cache("driver/browser version mismatch")
                        GLOBAL_DRIVER_PATH = resolve_chromedriver_path()
                    continue
                raise
        driver.implicitly_wait(0)
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
            _set_profile_ui(nm, browser='Đã đóng')
        threading.Thread(target=_wait_close, daemon=True).start()
    except Exception as e:
        _set_profile_ui(nm, browser='Bị lỗi', last_error=str(e))
        messagebox.showerror("Lỗi", str(e))

def _wait_and_close_driver(driver, name):
    pass 

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
    if os.environ.get('AUTO6_WATCHER_TEST') != '1':
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
    'change_license_key': change_license_key,
    'check_update': check_update_clicked,
    'clear_failed_uploads_panel': clear_failed_uploads_panel,
    'cleanup_failed_videos': cleanup_failed_videos,
    'start_selected_batch': start_selected_batch,
    'stop_selected_batch': stop_selected_batch,
    'start_all_in_project': start_all_in_project,
    'stop_all_in_project': stop_all_in_project,
    'copy_folder_path': copy_folder_path,
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

require_license_then_boot()
_run_auto6_watcher_test_from_env()
root.protocol("WM_DELETE_WINDOW", on_closing)
root.mainloop()
