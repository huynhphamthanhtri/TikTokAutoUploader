# -*- coding: utf-8 -*-
"""
Kiểm thử cho Kho Tài Khoản YouTube (Cookie Pool), sửa lỗi cắt ngang Fallback
('The page needs to be reloaded'), tối ưu tải video ngắn và dọn dẹp UI cũ.

Tuân thủ 100% Rule 14: Mọi file & cấu hình dùng tmp_path, không chạm dữ liệu thật.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from youtube_monitor.cookie_pool import (
    STATUS_COOLDOWN,
    STATUS_EXPIRED,
    STATUS_READY,
    YouTubeCookiePoolManager,
)
from youtube_monitor import core as yt_core
from youtube_monitor.login_browser import YouTubeLoginBrowser


def _make_dummy_cookie_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSAPISID\tdummy_val\n",
        encoding="utf-8",
    )
    return path


def test_cookie_pool_legacy_migration_and_crud(tmp_path):
    legacy = _make_dummy_cookie_file(tmp_path / "youtube_cookies.txt")
    mgr = YouTubeCookiePoolManager(
        pool_file=tmp_path / "pool.json",
        cookies_dir=tmp_path / "cookies",
        profiles_dir=tmp_path / "profiles",
        legacy_cookies_file=legacy,
        cookie_validator=yt_core.validate_youtube_cookie_file,
    )

    accs = mgr.list_accounts()
    assert len(accs) == 1
    assert accs[0]["label"] == "YT_MacDinh"
    assert accs[0]["status"] == STATUS_READY

    # Add second account & import .txt
    ext_txt = _make_dummy_cookie_file(tmp_path / "acc2.txt")
    ok, _msg, imported = mgr.import_cookie_file(ext_txt, label="YT_Acc_02")
    assert ok is True
    assert imported["status"] == STATUS_READY

    # Attach proxy
    assert mgr.set_account_proxy(imported["account_id"], "1.2.3.4:8080:u:p") is True
    assert mgr.get_account(imported["account_id"])["proxy"] == "1.2.3.4:8080:u:p"

    # Remove account
    assert mgr.remove_account(imported["account_id"], delete_files=True) is True
    assert len(mgr.list_accounts()) == 1


def test_cookie_pool_lru_rotation_and_cooldown_recovery(tmp_path):
    mgr = YouTubeCookiePoolManager(
        pool_file=tmp_path / "pool.json",
        cookies_dir=tmp_path / "cookies",
        profiles_dir=tmp_path / "profiles",
        cookie_validator=yt_core.validate_youtube_cookie_file,
    )

    ids = []
    for i in range(1, 4):
        src = _make_dummy_cookie_file(tmp_path / f"c{i}.txt")
        ok, _, acc = mgr.import_cookie_file(src, label=f"Acc_{i}")
        assert ok
        ids.append(acc["account_id"])

    # Pick 1: Acc_1 gets picked first & marked used at t=1000
    p1 = mgr.pick_accounts_for_download(limit=2, now=1000.0)
    assert p1[0]["account_id"] == ids[0]

    # Next pick at t=1001: Acc_2 should now be first (LRU)
    p2 = mgr.pick_accounts_for_download(limit=2, now=1001.0)
    assert p2[0]["account_id"] == ids[1]

    # Put Acc_3 into cooldown for 300s at t=1002
    mgr.mark_cooldown(ids[2], seconds=300, reason="HTTP 403", now=1002.0)
    counts = mgr.get_summary_counts(now=1050.0)
    assert counts["ready"] == 2
    assert counts["cooldown"] == 1

    # At t=1305 (past cooldown_until=1302), Acc_3 automatically returns to READY
    counts_after = mgr.get_summary_counts(now=1305.0)
    assert counts_after["ready"] == 3
    assert counts_after["cooldown"] == 0

    # Mark Acc_1 expired
    mgr.mark_expired(ids[0], reason="Sign in required")
    assert mgr.get_summary_counts(now=1305.0)["expired"] == 1


def test_attempt_plan_never_aborts_on_reload_error_and_rotates_cookies(tmp_path):
    """Tái hiện lỗi thực tế trong log:
    Attempt 1 & 2 bị 403, Attempt 3 bị 'The page needs to be reloaded.' (FAILURE_RETRY).
    Đảm bảo _select_next_attempt KHÔNG trả về None mà chuyển tiếp sang Cookie tiếp theo và nhánh không cookie!
    """
    mgr = YouTubeCookiePoolManager(
        pool_file=tmp_path / "pool.json",
        cookies_dir=tmp_path / "cookies",
        profiles_dir=tmp_path / "profiles",
        cookie_validator=yt_core.validate_youtube_cookie_file,
    )
    for i in range(1, 4):
        src = _make_dummy_cookie_file(tmp_path / f"pool_{i}.txt")
        mgr.import_cookie_file(src, label=f"Pool_{i}")

    with patch.object(yt_core, "get_cookie_pool_manager", return_value=mgr), \
         patch.object(yt_core, "get_config", return_value={"youtube_cookie_policy": "primary"}), \
         patch.object(yt_core, "_ytdlp_alternate_client", return_value="mweb"):
        attempts = yt_core._build_attempt_plan("TestProfile")

    names = [a.name for a in attempts]
    assert "pool-cookie-1" in names
    assert "pool-cookie-2" in names
    assert "pool-cookie-3" in names
    assert "direct-primary" in names
    assert "direct-alt-client" in names

    # Giả lập lỗi 'The page needs to be reloaded.' -> phân loại FAILURE_RETRY
    reload_err = Exception("ERROR: [youtube] YpcXsjWqIbs: The page needs to be reloaded.")
    cls = yt_core._classify_failure(reload_err)
    assert cls == yt_core.FAILURE_RETRY

    # Từ bất kỳ cookie attempt nào gặp FAILURE_RETRY, _select_next_attempt phải đi tiếp tới hết chuỗi
    visited = [0]
    cur = 0
    while True:
        nxt = yt_core._select_next_attempt(attempts, cur, cls)
        if nxt is None:
            break
        visited.append(nxt)
        cur = nxt

    visited_names = [attempts[i].name for i in visited]
    assert "pool-cookie-2" in visited_names
    assert "pool-cookie-3" in visited_names
    assert "direct-primary" in visited_names
    assert "direct-alt-client" in visited_names


def test_login_browser_message_cleaned_of_bell_notification(tmp_path):
    fake_exe = tmp_path / "chrome.exe"
    fake_exe.write_bytes(b"exe")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None

    browser = YouTubeLoginBrowser(
        profile_dir=tmp_path / "prof",
        executable_resolver=lambda: str(fake_exe),
        process_factory=lambda *a, **kw: fake_proc,
    )
    ok, msg = browser.open()
    assert ok is True
    assert "bật chuông" not in msg.lower()


def test_ui_cookie_pool_widgets_and_cleanup(tmp_path):
    import tkinter as tk
    from youtube_monitor.ui import YouTubeMonitorView

    mgr = YouTubeCookiePoolManager(
        pool_file=tmp_path / "pool.json",
        cookies_dir=tmp_path / "cookies",
        profiles_dir=tmp_path / "profiles",
        cookie_validator=yt_core.validate_youtube_cookie_file,
    )
    _make_dummy_cookie_file(tmp_path / "c1.txt")
    mgr.import_cookie_file(tmp_path / "c1.txt", label="YT_Test_01")

    root = tk.Tk()
    root.withdraw()
    try:
        with patch.object(yt_core, "get_cookie_pool_manager", return_value=mgr):
            view = YouTubeMonitorView(root, handlers={"get_cookies_file": lambda: ""})
            assert hasattr(view, "btn_open_cookie_pool")
            assert hasattr(view, "btn_quick_add_yt_cookie")
            assert not hasattr(view, "cookie_entry")
            view._refresh_cookie_pool_summary()
            assert "Sẵn sàng: 1" in view.yt_pool_summary_var.get()
            view.destroy()
    finally:
        root.destroy()
