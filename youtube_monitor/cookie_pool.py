# -*- coding: utf-8 -*-
"""
youtube_monitor/cookie_pool.py - Quản lý Kho Tài Khoản YouTube (Multi-Account Cookie Pool).

Hỗ trợ:
- Đăng nhập & lưu trữ nhiều profile Chrome độc lập + trích xuất cookie tự động.
- Xoay vòng tài khoản theo LRU (Least-Recently-Used) thread-safe giữa các worker.
- Cách ly Cooldown tạm thời (mặc định 300s) khi dính HTTP 403 / Reload, và đánh dấu EXPIRED khi hết hạn.
- Gắn Proxy tùy chọn cho từng tài khoản YouTube.
- Kế thừa tự động file youtube_cookies.txt cũ nếu pool chưa có dữ liệu.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


STATUS_READY = "READY"
STATUS_COOLDOWN = "COOLDOWN"
STATUS_EXPIRED = "EXPIRED"
STATUS_NO_COOKIE = "NO_COOKIE"


class YouTubeCookiePoolManager:
    """Quản lý pool nhiều tài khoản YouTube cookie cho việc tải video ổn định & xoay vòng."""

    def __init__(
        self,
        pool_file: str | Path,
        cookies_dir: str | Path,
        profiles_dir: str | Path,
        legacy_cookies_file: Optional[str | Path] = None,
        cookie_validator: Optional[Callable[[str | Path], Tuple[bool, str]]] = None,
    ):
        self.pool_file = Path(pool_file)
        self.cookies_dir = Path(cookies_dir)
        self.profiles_dir = Path(profiles_dir)
        self.legacy_cookies_file = Path(legacy_cookies_file) if legacy_cookies_file else None
        self._validator = cookie_validator
        self._lock = threading.RLock()
        self._accounts: List[Dict[str, Any]] = []
        self._load_and_migrate()

    def _ensure_dirs(self) -> None:
        self.pool_file.parent.mkdir(parents=True, exist_ok=True)
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def _save_unlocked(self) -> None:
        self._ensure_dirs()
        tmp_path = self.pool_file.with_suffix(".tmp")
        payload = {"version": 1, "accounts": self._accounts}
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.pool_file)

    def _has_valid_cookie_file(self, path_str: str) -> Tuple[bool, str]:
        if not path_str:
            return False, "Chưa có file cookie"
        p = Path(path_str)
        if not p.is_file() or p.stat().st_size <= 0:
            return False, "File cookie không tồn tại hoặc rỗng"
        if self._validator is not None:
            return self._validator(p)
        return True, "OK"

    def _load_and_migrate(self) -> None:
        with self._lock:
            self._accounts = []
            if self.pool_file.is_file():
                try:
                    with open(self.pool_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    raw_accs = data.get("accounts", []) if isinstance(data, dict) else []
                    for item in raw_accs:
                        if isinstance(item, dict) and item.get("account_id"):
                            self._accounts.append(dict(item))
                except Exception:
                    self._accounts = []

            # Kế thừa tự động file youtube_cookies.txt cũ nếu pool đang trống
            if not self._accounts and self.legacy_cookies_file and self.legacy_cookies_file.is_file():
                try:
                    if self.legacy_cookies_file.stat().st_size > 0:
                        self._ensure_dirs()
                        acc_id = "yt_default"
                        dest_cookie = self.cookies_dir / f"{acc_id}.txt"
                        if not dest_cookie.exists():
                            shutil.copy2(self.legacy_cookies_file, dest_cookie)
                        ok, reason = self._has_valid_cookie_file(str(dest_cookie))
                        self._accounts.append({
                            "account_id": acc_id,
                            "label": "YT_MacDinh",
                            "cookies_path": str(dest_cookie),
                            "profile_dir": str(self.profiles_dir / acc_id),
                            "proxy": "",
                            "status": STATUS_READY if ok else STATUS_EXPIRED,
                            "cooldown_until": 0.0,
                            "last_used_at": 0.0,
                            "fail_count": 0,
                            "success_count": 0,
                            "last_error": "" if ok else reason,
                        })
                        self._save_unlocked()
                except Exception:
                    pass

    def list_accounts(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Trả về bản sao danh sách tài khoản (tự động mở khóa các tài khoản hết hạn cooldown)."""
        curr = time.time() if now is None else float(now)
        with self._lock:
            changed = False
            for acc in self._accounts:
                if acc.get("status") == STATUS_COOLDOWN and curr >= float(acc.get("cooldown_until") or 0):
                    ok, _ = self._has_valid_cookie_file(acc.get("cookies_path", ""))
                    acc["status"] = STATUS_READY if ok else STATUS_NO_COOKIE
                    acc["cooldown_until"] = 0.0
                    changed = True
            if changed:
                self._save_unlocked()
            return [dict(a) for a in self._accounts]

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for acc in self._accounts:
                if acc.get("account_id") == account_id:
                    return dict(acc)
            return None

    def add_account(self, label: Optional[str] = None, proxy: str = "") -> Dict[str, Any]:
        """Tạo mới 1 tài khoản YouTube trong Pool với thư mục Chrome profile & cookie riêng."""
        with self._lock:
            self._ensure_dirs()
            idx = len(self._accounts) + 1
            clean_label = (label or "").strip()
            if not clean_label:
                existing_labels = {a.get("label", "") for a in self._accounts}
                while f"YT_Acc_{idx:02d}" in existing_labels:
                    idx += 1
                clean_label = f"YT_Acc_{idx:02d}"

            slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", clean_label).strip("_").lower() or "yt_acc"
            acc_id = f"{slug}_{uuid.uuid4().hex[:6]}"
            cookie_path = self.cookies_dir / f"{acc_id}.txt"
            prof_dir = self.profiles_dir / acc_id
            prof_dir.mkdir(parents=True, exist_ok=True)

            acc = {
                "account_id": acc_id,
                "label": clean_label,
                "cookies_path": str(cookie_path),
                "profile_dir": str(prof_dir),
                "proxy": str(proxy or "").strip(),
                "status": STATUS_NO_COOKIE,
                "cooldown_until": 0.0,
                "last_used_at": 0.0,
                "fail_count": 0,
                "success_count": 0,
                "last_error": "",
            }
            self._accounts.append(acc)
            self._save_unlocked()
            return dict(acc)

    def remove_account(self, account_id: str, delete_files: bool = True) -> bool:
        with self._lock:
            target = None
            for i, acc in enumerate(self._accounts):
                if acc.get("account_id") == account_id:
                    target = self._accounts.pop(i)
                    break
            if target is None:
                return False
            self._save_unlocked()

        if delete_files:
            try:
                cp = Path(target.get("cookies_path", ""))
                if cp.is_file():
                    cp.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                pd = Path(target.get("profile_dir", ""))
                if pd.is_dir():
                    shutil.rmtree(pd, ignore_errors=True)
            except Exception:
                pass
        return True

    def set_account_proxy(self, account_id: str, proxy: str) -> bool:
        with self._lock:
            for acc in self._accounts:
                if acc.get("account_id") == account_id:
                    acc["proxy"] = str(proxy or "").strip()
                    self._save_unlocked()
                    return True
            return False

    def mark_cookie_extracted(self, account_id: str, ok: bool, message: str = "") -> bool:
        """Cập nhật trạng thái sau khi trích xuất cookie từ browser hoặc kiểm tra live."""
        with self._lock:
            for acc in self._accounts:
                if acc.get("account_id") == account_id:
                    if ok:
                        acc["status"] = STATUS_READY
                        acc["cooldown_until"] = 0.0
                        acc["fail_count"] = 0
                        acc["last_error"] = ""
                    else:
                        has_file = Path(acc.get("cookies_path", "")).is_file()
                        acc["status"] = STATUS_EXPIRED if has_file else STATUS_NO_COOKIE
                        acc["last_error"] = str(message or "Cookie không hợp lệ")[:200]
                    self._save_unlocked()
                    return True
            return False

    def import_cookie_file(self, source_path: str | Path, label: Optional[str] = None, account_id: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Nhập file cookie .txt từ bên ngoài vào Pool (tạo mới hoặc ghi đè tài khoản chỉ định)."""
        src = Path(source_path)
        if not src.is_file() or src.stat().st_size <= 0:
            return False, "File cookie nguồn không tồn tại hoặc rỗng.", None
        if self._validator is not None:
            ok, reason = self._validator(src)
            if not ok:
                return False, f"File cookie không hợp lệ: {reason}", None

        with self._lock:
            if account_id:
                acc = next((a for a in self._accounts if a.get("account_id") == account_id), None)
                if acc is None:
                    return False, "Không tìm thấy tài khoản đích.", None
            else:
                acc_dict = self.add_account(label=label or src.stem)
                acc = next(a for a in self._accounts if a["account_id"] == acc_dict["account_id"])

            dest = Path(acc["cookies_path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            acc["status"] = STATUS_READY
            acc["cooldown_until"] = 0.0
            acc["fail_count"] = 0
            acc["last_error"] = ""
            self._save_unlocked()
            return True, f"Đã nhập cookie thành công cho {acc['label']}.", dict(acc)

    def pick_accounts_for_download(self, limit: int = 3, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Chọn tối đa `limit` tài khoản READY theo thứ tự LRU (ít dùng nhất gần đây)."""
        curr = time.time() if now is None else float(now)
        with self._lock:
            changed = False
            ready: List[Dict[str, Any]] = []
            for acc in self._accounts:
                if acc.get("status") == STATUS_COOLDOWN and curr >= float(acc.get("cooldown_until") or 0):
                    ok, _ = self._has_valid_cookie_file(acc.get("cookies_path", ""))
                    acc["status"] = STATUS_READY if ok else STATUS_NO_COOKIE
                    acc["cooldown_until"] = 0.0
                    changed = True
                if acc.get("status") == STATUS_READY:
                    ok, reason = self._has_valid_cookie_file(acc.get("cookies_path", ""))
                    if ok:
                        ready.append(acc)
                    else:
                        acc["status"] = STATUS_NO_COOKIE
                        acc["last_error"] = reason
                        changed = True

            ready.sort(key=lambda a: (float(a.get("last_used_at") or 0.0), int(a.get("fail_count") or 0)))
            picked = ready[:max(1, int(limit))]
            if picked:
                # Cập nhật last_used_at cho acc ưu tiên đầu tiên để worker khác kế tiếp chọn acc khác
                picked[0]["last_used_at"] = curr
                changed = True
            if changed:
                self._save_unlocked()
            return [dict(a) for a in picked]

    def mark_success(self, account_id: str, now: Optional[float] = None) -> None:
        curr = time.time() if now is None else float(now)
        with self._lock:
            for acc in self._accounts:
                if acc.get("account_id") == account_id:
                    acc["status"] = STATUS_READY
                    acc["cooldown_until"] = 0.0
                    acc["last_used_at"] = curr
                    acc["fail_count"] = 0
                    acc["success_count"] = int(acc.get("success_count") or 0) + 1
                    acc["last_error"] = ""
                    self._save_unlocked()
                    return

    def mark_cooldown(self, account_id: str, seconds: int = 300, reason: str = "", now: Optional[float] = None) -> None:
        curr = time.time() if now is None else float(now)
        with self._lock:
            for acc in self._accounts:
                if acc.get("account_id") == account_id:
                    acc["status"] = STATUS_COOLDOWN
                    acc["cooldown_until"] = curr + max(30, int(seconds))
                    acc["last_used_at"] = curr
                    acc["fail_count"] = int(acc.get("fail_count") or 0) + 1
                    if reason:
                        acc["last_error"] = str(reason)[:200]
                    self._save_unlocked()
                    return

    def mark_expired(self, account_id: str, reason: str = "") -> None:
        with self._lock:
            for acc in self._accounts:
                if acc.get("account_id") == account_id:
                    acc["status"] = STATUS_EXPIRED
                    acc["cooldown_until"] = 0.0
                    acc["fail_count"] = int(acc.get("fail_count") or 0) + 1
                    acc["last_error"] = str(reason or "Cookie hết hạn hoặc yêu cầu xác minh")[:200]
                    self._save_unlocked()
                    return

    def get_summary_counts(self, now: Optional[float] = None) -> Dict[str, int]:
        accs = self.list_accounts(now=now)
        counts = {"ready": 0, "cooldown": 0, "expired": 0, "no_cookie": 0, "total": len(accs)}
        for a in accs:
            st = a.get("status", STATUS_NO_COOKIE)
            if st == STATUS_READY:
                counts["ready"] += 1
            elif st == STATUS_COOLDOWN:
                counts["cooldown"] += 1
            elif st == STATUS_EXPIRED:
                counts["expired"] += 1
            else:
                counts["no_cookie"] += 1
        return counts
