import json
import os
import re
import shutil
import time

from datetime import datetime, timezone
from pathlib import Path
from selenium.common.exceptions import InvalidSessionIdException, TimeoutException


def parse_cookie(cookie_str):
    if not cookie_str:
        return None
    try:
        cookies = json.loads(cookie_str)
        if not isinstance(cookies, list):
            raise ValueError("Cookie JSON phải là danh sách")
        for cookie in cookies:
            if 'domain' in cookie and cookie['domain'].startswith('.'):
                cookie['domain'] = cookie['domain'][1:]
        return cookies
    except json.JSONDecodeError:
        cookies = []
        expiry_future = int(datetime.now().timestamp()) + 30 * 86400
        for cookie in cookie_str.split(";"):
            cookie = cookie.strip()
            if not cookie:
                continue
            try:
                name, value = cookie.split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": "tiktok.com",
                    "path": "/",
                    "expiry": expiry_future,
                })
            except ValueError:
                continue
        if not cookies:
            raise ValueError("Không tìm thấy cookie hợp lệ")
        return cookies


def parse_proxy_string(proxy_str):
    if not proxy_str:
        return None
    clean_str = proxy_str.replace("http://", "").replace("https://", "").strip()
    parts = clean_str.split(':')
    if len(parts) == 2:
        return {
            'ip': parts[0],
            'port': parts[1],
            'user': '',
            'pass': ''
        }
    if len(parts) >= 4:
        return {
            'ip': parts[0],
            'port': parts[1],
            'user': parts[2],
            'pass': parts[3]
        }
    return None


def verify_proxy_ip(driver, expected_ip):
    services = [
        "http://ipv4.icanhazip.com",
        "http://httpbin.org/ip",
        "http://checkip.amazonaws.com"
    ]
    ip_pattern = r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
    for url in services:
        try:
            driver.set_page_load_timeout(15)
            driver.get(url)
            time.sleep(1)
            content = driver.find_element("tag name", "body").text.strip()
            match = re.search(ip_pattern, content)
            if match:
                current_ip = match.group(0)
                return current_ip == expected_ip, current_ip
        except Exception:
            continue
    return False, "Không xác định"


def is_driver_valid(driver):
    if not driver:
        return False
    try:
        _ = driver.current_url
        return True
    except (InvalidSessionIdException, Exception):
        return False


def normalize_profile_path(path):
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(str(path or "").strip().strip('"'))))
    except Exception:
        return str(path or "").strip().lower()


def process_uses_profile(cmdline, profile_path):
    target = normalize_profile_path(profile_path)
    if not target:
        return False
    for arg in cmdline or []:
        text = str(arg or "").strip().strip('"')
        if "user-data-dir=" not in text.lower():
            continue
        value = text.split("=", 1)[1].strip().strip('"')
        if normalize_profile_path(value) == target:
            return True
    return False


def classify_webdriver_error(error):
    text = str(error or '').lower()
    if isinstance(error, InvalidSessionIdException) or 'invalid session id' in text:
        return 'invalid_session'
    if 'no such window' in text or 'target window already closed' in text:
        return 'window_closed'
    if 'tab crashed' in text or ('renderer' in text and 'crash' in text):
        return 'renderer_crash'
    if any(token in text for token in ('disconnected', 'not connected to devtools', 'chrome not reachable')):
        return 'browser_disconnected'
    if isinstance(error, TimeoutException):
        return 'timeout'
    return 'webdriver_error'


def profile_driver_path(chrome_profile, configured_path=''):
    expected = Path(chrome_profile).parent / 'Driver' / 'chromedriver.exe'
    configured = str(configured_path or '').strip()
    if configured and normalize_profile_path(configured) == normalize_profile_path(expected):
        return Path(configured)
    return expected


def clear_profile_directory(directory):
    path = Path(directory).resolve()
    if path.name.lower() != 'profile' or path.parent == path:
        raise ValueError(f'Đường dẫn User Data không an toàn để làm sạch: {path}')
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except FileNotFoundError:
                pass


def is_file_stable(path, checks, interval):
    try:
        prev = -1
        for _ in range(checks):
            cur = os.path.getsize(path)
            if cur == 0 or (cur != prev and prev != -1):
                prev = cur
                time.sleep(interval)
                continue
            prev = cur
            time.sleep(interval)
        cur = os.path.getsize(path)
        return cur > 0 and cur == prev
    except Exception:
        return False


def copy_video_atomically(source, destination):
    """Copy fully to a non-video staging path before exposing the final file."""
    src = Path(source)
    dst = Path(destination)
    if not src.is_file():
        raise FileNotFoundError(f"Không tìm thấy video nguồn: {src}")
    if src.resolve() == dst.resolve():
        raise ValueError("Video nguồn và đích không được trùng nhau")
    dst.parent.mkdir(parents=True, exist_ok=True)
    staging = dst.with_name(f".{dst.name}.part")
    if staging.exists():
        staging.unlink()
    try:
        shutil.copyfile(src, staging)
        os.utime(staging, None)
        os.replace(staging, dst)
    finally:
        try:
            staging.unlink(missing_ok=True)
        except Exception:
            pass
    return dst


def clean_chrome_lock_files(profile_path):
    try:
        files_to_remove = ["SingletonLock", "SingletonSocket", "SingletonCookie"]
        for fname in files_to_remove:
            f_path = os.path.join(profile_path, fname)
            if os.path.exists(f_path):
                try:
                    os.remove(f_path)
                except Exception:
                    pass

            f_path_default = os.path.join(profile_path, "Default", fname)
            if os.path.exists(f_path_default):
                try:
                    os.remove(f_path_default)
                except Exception:
                    pass
    except Exception:
        pass
