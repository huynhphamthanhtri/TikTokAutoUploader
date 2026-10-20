import ipaddress
import json
import os
import re
import shutil
import time
import urllib.parse

from datetime import datetime, timezone
from pathlib import Path


_TIKTOK_DOMAIN = "tiktok.com"


def _normalize_cookie_domain(domain):
    """Keep subdomain cookies usable for the hosts the tool actually opens.

    The tool navigates to ``www.tiktok.com``. A bare registrable domain such
    as ``tiktok.com`` is a host-only cookie that never reaches the ``www``
    subdomain, so it is widened to ``.tiktok.com``. Specific domains such as
    ``www.tiktok.com`` / ``.www.tiktok.com`` are kept untouched."""
    raw = str(domain or "").strip()
    if not raw:
        return raw
    lower = raw.lower()
    if lower == _TIKTOK_DOMAIN:
        return "." + _TIKTOK_DOMAIN
    if lower.startswith("." + _TIKTOK_DOMAIN):
        return raw
    return raw


def parse_cookie(cookie_str):
    if not cookie_str:
        return None
    now_ts = datetime.now().timestamp()
    expiry_future = int(now_ts) + 30 * 86400
    try:
        cookies = json.loads(cookie_str)
        if not isinstance(cookies, list):
            raise ValueError("Cookie JSON phải là danh sách")
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            if 'domain' in cookie:
                cookie['domain'] = _normalize_cookie_domain(cookie['domain'])
            raw_exp = cookie.get('expiry')
            if raw_exp is None:
                raw_exp = cookie.get('expires')
            if raw_exp is None:
                raw_exp = cookie.get('expirationDate')

            if raw_exp is not None:
                try:
                    exp_float = float(raw_exp)
                    if exp_float <= now_ts:
                        cookie['expiry'] = expiry_future
                    else:
                        cookie['expiry'] = exp_float
                except (ValueError, TypeError):
                    cookie['expiry'] = expiry_future
            else:
                cookie['expiry'] = expiry_future
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
                    "domain": "." + _TIKTOK_DOMAIN,
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


def format_requests_proxy(proxy_input, scheme="http"):
    """Chuẩn hóa proxy (string hoặc dict) thành format RFC 3986 cho requests.
    
    Hỗ trợ:
    - "ip:port:user:pass" -> "http://user:pass@ip:port"
    - "ip:port" -> "http://ip:port"
    - "http://user:pass@host:port" -> giữ nguyên
    - Dict {'ip': ..., 'port': ..., 'user': ..., 'pass': ...}
    
    Trả về: {"http": proxy_url, "https": proxy_url} hoặc None nếu proxy không hợp lệ / rỗng.
    """
    if not proxy_input:
        return None
        
    if isinstance(proxy_input, dict):
        ip = str(proxy_input.get("ip") or proxy_input.get("host") or "").strip()
        port = str(proxy_input.get("port") or "").strip()
        user = str(proxy_input.get("user") or proxy_input.get("username") or "").strip()
        pwd = str(proxy_input.get("pass") or proxy_input.get("password") or "").strip()
    else:
        p_str = str(proxy_input).strip()
        if not p_str:
            return None
        if "@" in p_str and any(p_str.startswith(s) for s in ("http://", "https://", "socks5://")):
            return {"http": p_str, "https": p_str}
        parsed = parse_proxy_string(p_str)
        if not parsed:
            return None
        ip = str(parsed.get("ip") or "").strip()
        port = str(parsed.get("port") or "").strip()
        user = str(parsed.get("user") or "").strip()
        pwd = str(parsed.get("pass") or "").strip()

    if not ip or not port:
        return None

    if user and pwd:
        u_enc = urllib.parse.quote(user, safe="")
        p_enc = urllib.parse.quote(pwd, safe="")
        proxy_url = f"{scheme}://{u_enc}:{p_enc}@{ip}:{port}"
    else:
        proxy_url = f"{scheme}://{ip}:{port}"

    return {"http": proxy_url, "https": proxy_url}


def _extract_ip_address(content):
    for candidate in re.findall(r"[0-9A-Fa-f:.]+", str(content or "")):
        try:
            return str(ipaddress.ip_address(candidate.strip(".:") or candidate))
        except ValueError:
            continue
    return None


def normalize_profile_path(path):
    raw = str(path or "").strip().strip('"')
    if not raw:
        return ""
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(raw)))
    except Exception:
        return raw.lower()


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
