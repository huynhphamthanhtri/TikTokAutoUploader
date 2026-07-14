import json
import os
import re
import time

from datetime import datetime, timezone
from selenium.common.exceptions import InvalidSessionIdException


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
