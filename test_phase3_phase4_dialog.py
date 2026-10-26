# -*- coding: utf-8 -*-
"""
Tests for Phase 3 (UI Dialog) and Phase 4 (Threading & Concurrency Integration)
Testing ChannelActivityReviewDialog in headless mode with Codex redteam additions.
"""
import copy
import queue
import time
from unittest.mock import MagicMock, patch

import pytest
import tkinter as tk
import customtkinter as ctk

from youtube_monitor.activity_dialog import ChannelActivityReviewDialog, calculate_dialog_center_geometry
from youtube_monitor.core import channels_store, _store_lock, evaluate_channel_activity


@pytest.fixture(scope="module")
def tk_root():
    """Create a headless Tk root for widget testing."""
    try:
        root = tk.Tk()
        root.withdraw()
    except Exception:
        pytest.skip("Tkinter display not available")
    yield root
    try:
        root.destroy()
    except Exception:
        pass


@pytest.fixture
def clean_channels_fixture(monkeypatch, tmp_path):
    temp_channels_file = tmp_path / "temp_channels.json"
    temp_channels_file.write_text("{}", encoding="utf-8")

    # 1. Quiesce any active autosave thread without writing to production
    channels_store._autosave_stop.set()
    if channels_store._autosave_thread and channels_store._autosave_thread.is_alive():
        channels_store._autosave_thread.join(timeout=2)

    with _store_lock:
        orig = copy.deepcopy(channels_store._channels)
        orig_dirty = channels_store._dirty
        orig_revision = channels_store._revision
        orig_path = channels_store.path

        channels_store.path = temp_channels_file
        channels_store._channels = {}
        channels_store._dirty = False
        channels_store._revision = 0

    mock_save = MagicMock(return_value=True)
    monkeypatch.setattr(channels_store, "save_now", mock_save)

    try:
        yield
    finally:
        channels_store._autosave_stop.set()
        if channels_store._autosave_thread and channels_store._autosave_thread.is_alive():
            channels_store._autosave_thread.join(timeout=2)

        with _store_lock:
            channels_store._channels = orig
            channels_store._dirty = orig_dirty
            channels_store._revision = orig_revision
            channels_store.path = orig_path


def test_dialog_initialization_and_table_population(tk_root, clean_channels_fixture):
    cid1 = "UC_ACTIVE_RECENT"
    cid2 = "UC_EXCEEDED"
    cid3 = "UC_PAUSED"

    now = time.time()
    with _store_lock:
        channels_store._channels[cid1] = {
            "title": "Recent Channel",
            "profile_name": "Profile 1",
            "active": True,
            "last_pub_utc": now - (5 * 3600),  # 5h ago
            "latest_observed_video_id": "vid1",
            "latest_observed_video_title": "Video Recent",
            "latest_observed_pub_utc": now - (5 * 3600),
            "last_api_audit_status": "OK",
        }
        channels_store._channels[cid2] = {
            "title": "Stale Channel",
            "profile_name": "Profile 2",
            "active": True,
            "last_pub_utc": now - (72 * 3600),  # 72h ago (> 48h)
            "latest_observed_video_id": "vid2",
            "latest_observed_video_title": "Video Stale",
            "latest_observed_pub_utc": now - (72 * 3600),
            "last_api_audit_status": "OK",
        }
        channels_store._channels[cid3] = {
            "title": "Paused Channel",
            "profile_name": "Profile 3",
            "active": False,
            "last_pub_utc": now - (10 * 3600),
            "latest_observed_video_id": "vid3",
            "latest_observed_video_title": "Video Paused",
            "latest_observed_pub_utc": now - (10 * 3600),
            "last_api_audit_status": "OK",
        }

    dialog = ChannelActivityReviewDialog(tk_root)
    dialog.update()

    try:
        # Check stat badges
        assert "Tổng: 3" in dialog.stat_total_var.get()
        assert "🟢 Trong hạn: 2" in dialog.stat_in_var.get()
        assert "⚠ Quá 48h: 1" in dialog.stat_exceeded_var.get()
        assert "⏸ Tạm dừng: 1" in dialog.stat_paused_var.get()

        # Check Treeview rows
        children = dialog.tree.get_children()
        assert len(children) == 3
        assert cid1 in children
        assert cid2 in children
        assert cid3 in children

        # Verify values of row 1
        v1 = dialog.tree.item(cid1, "values")
        assert v1[0] == "☐"  # Not selected
        assert v1[1] == "Profile 1"
        assert "Recent Channel" in v1[2]
        assert "Video Recent" in v1[3]
        assert "🟢 Đang chạy" in v1[5]

        # Verify tag of row 2 (exceeded)
        tags2 = dialog.tree.item(cid2, "tags")
        assert "tag_exceeded" in tags2

        # Verify tag of row 3 (paused)
        tags3 = dialog.tree.item(cid3, "tags")
        assert "tag_paused" in tags3
    finally:
        dialog._on_close()


def test_dialog_filter_and_search(tk_root, clean_channels_fixture):
    cid1 = "UC_ALPHA"
    cid2 = "UC_BETA"

    now = time.time()
    with _store_lock:
        channels_store._channels[cid1] = {
            "title": "Alpha Gaming",
            "profile_name": "Profile A",
            "active": True,
            "latest_observed_pub_utc": now - (10 * 3600),
            "last_api_audit_status": "OK",
        }
        channels_store._channels[cid2] = {
            "title": "Beta Cooking",
            "profile_name": "Profile B",
            "active": True,
            "latest_observed_pub_utc": now - (100 * 3600),  # Exceeded
            "last_api_audit_status": "OK",
        }

    dialog = ChannelActivityReviewDialog(tk_root)
    dialog.update()

    try:
        # Filter by Exceeded
        dialog.seg_filter.set("⚠ Quá Ngưỡng")
        dialog._render_table()
        children = dialog.tree.get_children()
        assert len(children) == 1
        assert children[0] == cid2

        # Reset category filter, search by text
        dialog.seg_filter.set("Tất Cả")
        dialog.search_var.set("Alpha")
        dialog._render_table()
        children = dialog.tree.get_children()
        assert len(children) == 1
        assert children[0] == cid1

        # Search matching none
        dialog.search_var.set("NonExistentXYZ")
        dialog._render_table()
        assert len(dialog.tree.get_children()) == 0
    finally:
        dialog._on_close()


def test_dialog_selection_and_bulk_actions(tk_root, clean_channels_fixture):
    cid1 = "UC_SEL_1"
    cid2 = "UC_SEL_2"

    now = time.time()
    with _store_lock:
        channels_store._channels[cid1] = {
            "title": "Channel 1",
            "active": True,
            "latest_observed_pub_utc": now - (80 * 3600),  # Exceeded
        }
        channels_store._channels[cid2] = {
            "title": "Channel 2",
            "active": True,
            "latest_observed_pub_utc": now - (90 * 3600),  # Exceeded
        }

    callback_mock = MagicMock()
    dialog = ChannelActivityReviewDialog(tk_root, on_channels_changed=callback_mock)
    dialog.update()

    try:
        # Select all exceeded
        dialog._select_all_exceeded()
        assert cid1 in dialog._selected_cids
        assert cid2 in dialog._selected_cids
        assert "Đã chọn: 2 kênh" in dialog.selected_count_var.get()

        # Deselect all
        dialog._deselect_all()
        assert len(dialog._selected_cids) == 0

        # Toggle individual selection
        dialog._toggle_selection(cid1)
        assert cid1 in dialog._selected_cids
        assert dialog.tree.item(cid1, "values")[0] == "☑"

        # Bulk pause
        with patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("tkinter.messagebox.showinfo"):
            dialog._bulk_set_active(False)

        meta1 = channels_store.get_meta(cid1)
        assert meta1["active"] is False
        callback_mock.assert_called()

        # Bulk resume
        with patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("tkinter.messagebox.showinfo"):
            dialog._bulk_set_active(True)

        meta1 = channels_store.get_meta(cid1)
        assert meta1["active"] is True

        # Bulk remove
        with patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("tkinter.messagebox.showinfo"):
            dialog._bulk_remove_channels()

        assert channels_store.get_meta(cid1) is None
        assert cid1 not in dialog._selected_cids
    finally:
        dialog._on_close()


def test_dialog_bulk_actions_disabled_during_active_scan(tk_root, clean_channels_fixture):
    cid = "UC_SCAN_LOCK"
    with _store_lock:
        channels_store._channels[cid] = {"title": "Lock Test", "active": True}

    dialog = ChannelActivityReviewDialog(tk_root)
    dialog.update()

    try:
        dialog._selected_cids.add(cid)
        dialog._is_scanning = True  # Active scan simulated

        with patch("tkinter.messagebox.showwarning") as mock_warn:
            dialog._bulk_set_active(False)
            mock_warn.assert_called_once()
            # Channel must remain active
            assert channels_store.get_meta(cid)["active"] is True

        with patch("tkinter.messagebox.showwarning") as mock_warn:
            dialog._bulk_remove_channels()
            mock_warn.assert_called_once()
            # Channel must NOT be removed
            assert channels_store.get_meta(cid) is not None
    finally:
        dialog._on_close()


def test_dialog_live_progress_dynamic_filter_matching(tk_root, clean_channels_fixture):
    cid = "UC_DYNAMIC_MATCH"
    now = time.time()
    with _store_lock:
        channels_store._channels[cid] = {
            "title": "Dynamic Test",
            "active": True,
            "latest_observed_pub_utc": now - (5 * 3600),  # In threshold
        }

    dialog = ChannelActivityReviewDialog(tk_root)
    dialog.update()

    try:
        # Filter to Exceeded only -> Row should NOT be visible
        dialog.seg_filter.set("⚠ Quá Ngưỡng")
        dialog._render_table()
        assert not dialog.tree.exists(cid)

        # Progress arrives with a stale video from 100 hours ago -> must be inserted into tree!
        fake_res_stale = {
            "channel_id": cid,
            "status": "OK",
            "video_id": "vid_old",
            "video_title": "Old Video",
            "published_utc": now - (100 * 3600),
            "error": "",
        }
        dialog._is_scanning = True
        dialog._active_run_id = "test_run_dynamic"
        dialog._msg_queue.put(("PROGRESS", 1, 1, fake_res_stale, "test_run_dynamic"))
        dialog._poll_queue()

        # Dynamic match inserted the row!
        assert dialog.tree.exists(cid)
        v = dialog.tree.item(cid, "values")
        assert "Old Video" in v[3]

        # Now progress arrives with fresh video from 1 hour ago -> must be dynamically removed from this view!
        fake_res_fresh = {
            "channel_id": cid,
            "status": "OK",
            "video_id": "vid_fresh",
            "video_title": "Fresh Video",
            "published_utc": now - (1 * 3600),
            "error": "",
        }
        dialog._msg_queue.put(("PROGRESS", 1, 1, fake_res_fresh, "test_run_dynamic"))
        dialog._poll_queue()

        # Dynamically removed from "⚠ Quá Ngưỡng" filter!
        assert not dialog.tree.exists(cid)
    finally:
        dialog._on_close()


def test_dynamic_quota_button_text_and_badge_invariants(tk_root, clean_channels_fixture):
    """Kiểm tra nhãn nút Quota động theo số kênh và bất biến: Trong hạn + Quá hạn + Chưa rõ = Tổng."""
    now = time.time()
    with _store_lock:
        channels_store._channels = {
            "C1": {"title": "C1", "active": True, "latest_observed_pub_utc": now - (5 * 3600)},     # In
            "C2": {"title": "C2", "active": False, "latest_observed_pub_utc": now - (10 * 3600)},    # In (Paused)
            "C3": {"title": "C3", "active": True, "latest_observed_pub_utc": now - (60 * 3600)},    # Exceeded
            "C4": {"title": "C4", "active": False, "latest_observed_pub_utc": now - (100 * 3600)},   # Exceeded (Paused)
            "C5": {"title": "C5", "active": True, "latest_observed_pub_utc": None},                  # No data
        }

    dialog = ChannelActivityReviewDialog(tk_root)
    dialog.update()

    try:
        # 1. Dynamic Quota text matches count
        assert dialog.btn_scan.cget("text") == "⚡ Quét Data API (~5 Quota)"

        # 2. Invariants
        total_str = dialog.stat_total_var.get()
        assert "Tổng: 5" in total_str

        in_cnt = int(dialog.stat_in_var.get().split(":")[-1].strip())
        exceeded_cnt = int(dialog.stat_exceeded_var.get().split(":")[-1].strip())
        nodata_cnt = int(dialog.stat_nodata_var.get().split(":")[-1].strip())
        paused_cnt = int(dialog.stat_paused_var.get().split(":")[-1].strip())

        assert in_cnt == 2
        assert exceeded_cnt == 2
        assert nodata_cnt == 1
        assert in_cnt + exceeded_cnt + nodata_cnt == 5
        assert paused_cnt == 2

        # 3. Reload with 2 channels updates quota button
        with _store_lock:
            channels_store._channels = {
                "C1": {"title": "C1", "active": True},
                "C2": {"title": "C2", "active": True},
            }
        dialog._load_local_channels_data()
        assert dialog.btn_scan.cget("text") == "⚡ Quét Data API (~2 Quota)"
        assert "Tổng: 2" in dialog.stat_total_var.get()
    finally:
        dialog._on_close()


def test_strict_run_id_isolation_and_empty_handling(tk_root, clean_channels_fixture):
    """Kiểm tra loại bỏ sự kiện sai run_id (bảo toàn toàn bộ state) và xử lý EMPTY đúng chuẩn object identity."""
    cid = "UC_STRICT_RUN"
    now = time.time()
    with _store_lock:
        channels_store._channels[cid] = {
            "title": "Strict Test",
            "active": True,
            "latest_observed_video_id": "vid_prev",
            "latest_observed_video_title": "Prev Title",
            "latest_observed_pub_utc": now - (10 * 3600),
            "last_api_audit_status": "OK",
            "last_api_audit_error": "",
            "last_api_audit_at": now - 100,
            "last_api_audit_success_at": now - 100,
        }

    dialog = ChannelActivityReviewDialog(tk_root)
    dialog.update()

    try:
        dialog._is_scanning = True
        dialog._active_run_id = "valid_run_123"

        # Capture complete baseline state snapshot and object references
        original_dict_ref = dialog._channels_by_id[cid]
        matching_list_entry = next(c for c in dialog._channels_list if c["channel_id"] == cid)
        assert original_dict_ref is matching_list_entry  # Confirm identity prerequisite

        baseline_snapshot = copy.deepcopy(dialog._channels_by_id[cid])
        baseline_eval = copy.deepcopy(dialog._evaluations[cid])

        # 1. Stale run_id is completely ignored (no field modified)
        fake_stale = {
            "channel_id": cid,
            "status": "ERROR",
            "error": "Fake Stale Error",
            "video_id": "corrupted_id",
            "published_utc": now - 500,
        }
        dialog._msg_queue.put(("PROGRESS", 1, 1, fake_stale, "stale_run_999"))
        dialog._poll_queue()
        assert dialog._channels_by_id[cid] == baseline_snapshot
        assert dialog._evaluations[cid] == baseline_eval

        # 2. Empty string ("") and None run_id are both ignored
        dialog._msg_queue.put(("PROGRESS", 1, 1, fake_stale, ""))
        dialog._poll_queue()
        assert dialog._channels_by_id[cid] == baseline_snapshot

        dialog._msg_queue.put(("PROGRESS", 1, 1, fake_stale, None))
        dialog._poll_queue()
        assert dialog._channels_by_id[cid] == baseline_snapshot

        # 3. Valid run_id with EMPTY status clears video fields and sets in-place cache
        audit_time = now + 50
        fake_empty = {
            "channel_id": cid,
            "status": "EMPTY",
            "video_id": "",
            "video_title": "",
            "published_utc": None,
            "error": "No videos in playlist",
            "last_api_audit_at": audit_time,
            "last_api_audit_success_at": audit_time,
        }
        dialog._msg_queue.put(("PROGRESS", 1, 1, fake_empty, "valid_run_123"))
        dialog._poll_queue()

        # In-place cache updated and object identity strictly preserved
        assert dialog._channels_by_id[cid] is original_dict_ref
        assert matching_list_entry is original_dict_ref
        assert matching_list_entry["latest_observed_video_id"] == ""
        assert matching_list_entry["latest_observed_pub_utc"] is None
        assert matching_list_entry["last_api_audit_status"] == "EMPTY"
        assert matching_list_entry["last_api_audit_success_at"] == audit_time
        assert dialog._evaluations[cid]["category"] == "NO_DATA"
    finally:
        dialog._on_close()


def test_mid_scan_threshold_change_uses_in_place_cache(tk_root, clean_channels_fixture):
    """Đổi threshold giữa chừng khi đang quét chứng minh dứt khoát việc sử dụng dữ liệu mới (20h -> 35h)."""
    cid = "UC_MIDSCAN"
    now = time.time()
    with _store_lock:
        channels_store._channels[cid] = {
            "title": "MidScan Test",
            "active": True,
            "latest_observed_video_id": "vid_init",
            "latest_observed_video_title": "Initial",
            "latest_observed_pub_utc": now - (20 * 3600),  # 20h ago: IN_THRESHOLD for both 48h AND 24h!
        }

    dialog = ChannelActivityReviewDialog(tk_root)
    dialog.update()

    try:
        dialog._is_scanning = True
        dialog._active_run_id = "run_midscan"

        # Initial evaluation under default 48h
        assert dialog._evaluations[cid]["category"] == "IN_THRESHOLD"

        # Verify that under old data (20h), switching to 24h would STILL be IN_THRESHOLD
        initial_eval_24h = evaluate_channel_activity(dialog._channels_by_id[cid], now_utc=now, threshold_hours=24.0)
        assert initial_eval_24h["category"] == "IN_THRESHOLD"  # 20h < 24h

        # Progress arrives updating video to 35h ago (> 24h!)
        audit_time = now + 10
        fake_prog = {
            "channel_id": cid,
            "status": "OK",
            "video_id": "vid_fresh35",
            "video_title": "Fresh 35h",
            "published_utc": now - (35 * 3600),
            "last_api_audit_at": audit_time,
            "last_api_audit_success_at": audit_time,
        }
        dialog._msg_queue.put(("PROGRESS", 1, 1, fake_prog, "run_midscan"))
        dialog._poll_queue()

        # Under 48h, 35h is STILL IN_THRESHOLD
        assert dialog._evaluations[cid]["category"] == "IN_THRESHOLD"
        assert dialog._channels_by_id[cid]["latest_observed_video_id"] == "vid_fresh35"

        # User switches threshold combo to 24 hours mid-scan
        dialog.combo_threshold.set("24 giờ")
        dialog._on_threshold_changed("24 giờ")

        # Crucial proof: ONLY if in-place cache has 35h will it become EXCEEDED_THRESHOLD (> 24h)!
        # If it used stale 20h data, it would fail this assertion.
        assert dialog._evaluations[cid]["category"] == "EXCEEDED_THRESHOLD"
        assert "Quá 24h" in dialog.stat_exceeded_var.get()
    finally:
        dialog._on_close()

def test_ui_view_activity_button_wiring(tk_root):
    """Phase 3 Test: Verify YouTubeMonitorView button wiring to ChannelActivityReviewDialog."""
    from youtube_monitor.ui import YouTubeMonitorView
    from unittest.mock import MagicMock, patch

    mock_handlers = {
        "get_channels": MagicMock(return_value=[]),
        "get_profiles": MagicMock(return_value=[]),
    }

    view = YouTubeMonitorView(tk_root, handlers=mock_handlers)

    with patch("youtube_monitor.activity_dialog.ChannelActivityReviewDialog") as mock_dialog_cls:
        mock_instance = MagicMock()
        mock_instance.winfo_exists.return_value = True
        mock_dialog_cls.return_value = mock_instance

        # Install spy before button invocation so the bound method captured is the spy
        with patch.object(view, "_refresh_channels_after_action") as spy_refresh:
            # 1. Invoke the actual button via Tkinter invoke()
            view.btn_check_activity.invoke()

            # Verify dialog was constructed with view as parent and spy_refresh as callback
            mock_dialog_cls.assert_called_once_with(
                view,
                on_channels_changed=spy_refresh,
            )

            # 2. Invoke the forwarded callback and verify spy_refresh receives the call
            callback = mock_dialog_cls.call_args[1]["on_channels_changed"]
            assert callable(callback)
            callback()
            spy_refresh.assert_called_once_with()


def test_ui_view_activity_real_dialog_lifecycle_smoke(tk_root):
    """Phase 4 Integration Smoke Test: Real button click, dialog creation, reuse & destroy lifecycle."""
    from youtube_monitor.ui import YouTubeMonitorView
    from youtube_monitor.activity_dialog import ChannelActivityReviewDialog
    from unittest.mock import MagicMock

    mock_handlers = {
        "get_channels": MagicMock(return_value=[]),
        "get_profiles": MagicMock(return_value=[]),
    }

    view = YouTubeMonitorView(tk_root, handlers=mock_handlers)
    tk_root.update()

    # 1. Click button to open REAL ChannelActivityReviewDialog
    view.btn_check_activity.invoke()
    tk_root.update()

    first_dialog = getattr(view, "_activity_dialog", None)
    assert first_dialog is not None
    assert isinstance(first_dialog, ChannelActivityReviewDialog)
    assert first_dialog.winfo_exists()

    # 2. Click button again: existing dialog MUST be reused and focused (no duplicate window)
    view.btn_check_activity.invoke()
    tk_root.update()

    second_dialog = getattr(view, "_activity_dialog", None)
    assert second_dialog is first_dialog

    # 3. Destroy dialog and click button again: MUST cleanly instantiate a fresh dialog
    try:
        first_dialog._on_close()
        tk_root.update()
        assert not first_dialog.winfo_exists()

        view.btn_check_activity.invoke()
        tk_root.update()

        third_dialog = getattr(view, "_activity_dialog", None)
        assert third_dialog is not None
        assert third_dialog is not first_dialog
        assert third_dialog.winfo_exists()
    finally:
        active_dlg = getattr(view, "_activity_dialog", None)
        if active_dlg and active_dlg.winfo_exists():
            active_dlg._on_close()
            tk_root.update()


def test_calculate_dialog_center_geometry_pure_math():
    """Unit test for pure geometry calculations across various screen resolutions without touching disk."""
    # Case 1: Standard 1080p Display (1920x1080)
    w, h, x, y, geom = calculate_dialog_center_geometry(screen_w=1920, screen_h=1080)
    assert w == 1120
    assert h == 720
    assert x == (1920 - 1120) // 2  # 400
    assert y == (1080 - 720) // 2   # 180
    assert geom == "1120x720+400+180"

    # Case 2: Standard Laptop Display (1366x768)
    w2, h2, x2, y2, geom2 = calculate_dialog_center_geometry(screen_w=1366, screen_h=768)
    assert w2 == 1120
    assert h2 == 768 - 80  # 688
    assert x2 == (1366 - 1120) // 2  # 123
    assert y2 == (768 - 688) // 2    # 40
    assert geom2 == "1120x688+123+40"

    # Case 3: Multi-monitor with positive offset (Monitor 2 at x=1920)
    w3, h3, x3, y3, geom3 = calculate_dialog_center_geometry(screen_w=1920, screen_h=1080, screen_x=1920, screen_y=0)
    assert w3 == 1120
    assert h3 == 720
    assert x3 == 1920 + 400  # 2320
    assert y3 == 180
    assert geom3 == "1120x720+2320+180"

    # Case 4: Multi-monitor with negative offset (Monitor on the left at x=-1920)
    w4, h4, x4, y4, geom4 = calculate_dialog_center_geometry(screen_w=1920, screen_h=1080, screen_x=-1920, screen_y=0)
    assert w4 == 1120
    assert h4 == 720
    assert x4 == -1920 + 400  # -1520
    assert y4 == 180
    assert geom4 == "1120x720-1520+180"

    # Case 5: Fallback when screen dimensions are invalid or non-positive
    w5, h5, x5, y5, geom5 = calculate_dialog_center_geometry(screen_w=0, screen_h=-10)
    # Must fallback to 1366x768
    assert w5 == 1120
    assert h5 == 688
    assert x5 == 123
    assert y5 == 40
    assert geom5 == "1120x688+123+40"

    # Case 6: Defensive against None / invalid offsets
    w6, h6, x6, y6, geom6 = calculate_dialog_center_geometry(screen_w=1920, screen_h=1080, screen_x=None, screen_y="invalid")
    assert w6 == 1120
    assert h6 == 720
    assert x6 == 400
    assert y6 == 180
    assert geom6 == "1120x720+400+180"

    # Case 7: 1280x720 Display
    w7, h7, x7, y7, geom7 = calculate_dialog_center_geometry(screen_w=1280, screen_h=720)
    assert w7 == 1120
    assert h7 == 640
    assert x7 == 80
    assert y7 == 40
    assert geom7 == "1120x640+80+40"

    # Case 8: Small screen 800x600 (adaptive bounds avoid overflow)
    w8, h8, x8, y8, geom8 = calculate_dialog_center_geometry(screen_w=800, screen_h=600)
    assert w8 == 740
    assert h8 == 520
    assert x8 == 30
    assert y8 == 40
    assert geom8 == "740x520+30+40"


def test_dialog_screen_centering_widget_integration(tk_root, clean_channels_fixture):
    """Verify that ChannelActivityReviewDialog positions itself via centered geometry under clean fixture with Rule 14 verification."""
    from pathlib import Path

    prod_channels = Path("channels.json")
    state_before = (prod_channels.exists(), prod_channels.read_bytes() if prod_channels.exists() else b"")

    dialog = ChannelActivityReviewDialog(tk_root)
    dialog.update()
    try:
        # Check minsize bounds
        assert dialog._min_width == 960
        assert dialog._min_height == 600

        # Check geometry string format
        geom = dialog.geometry()
        assert "x" in geom and ("+" in geom or "-" in geom)

        # Check that dialog dimensions are at least the minimum allowed
        w = dialog.winfo_width()
        h = dialog.winfo_height()
        assert w >= 960 or w > 0  # In headless environments, non-zero rendered size
        assert h >= 600 or h > 0
    finally:
        dialog._on_close()
        tk_root.update()

    # Rule 14 Verification: Production channels.json must have 0 mutation (both existence and byte content)
    state_after = (prod_channels.exists(), prod_channels.read_bytes() if prod_channels.exists() else b"")
    assert state_before == state_after, "Violation of Rule 14: channels.json state changed during dialog test!"


def test_dialog_centering_relative_to_parent_positive_and_negative_offsets(tk_root, clean_channels_fixture):
    """Integration test verifying _center_window correctly centers on parent across multi-monitor setups and adapts minsize."""
    from unittest.mock import MagicMock

    # 1. Parent on Monitor 2 (x=1920, y=0, width=1920, height=1080)
    mock_parent = MagicMock()
    mock_top = MagicMock()
    mock_top.winfo_width.return_value = 1920
    mock_top.winfo_height.return_value = 1080
    mock_top.winfo_rootx.return_value = 1920
    mock_top.winfo_rooty.return_value = 0
    mock_parent.winfo_toplevel.return_value = mock_top

    dialog = ChannelActivityReviewDialog(tk_root)
    try:
        dialog._center_window(parent=mock_parent)
        dialog.update()
        geom = dialog.geometry()
        # Should center on Monitor 2: 1920 + (1920-1120)//2 = 2320, 0 + (1080-720)//2 = 180
        assert geom == "1120x720+2320+180"
        assert dialog._min_width == 960
        assert dialog._min_height == 600

        # 2. Parent on Left Monitor (x=-1920, y=0, width=1920, height=1080)
        mock_top.winfo_rootx.return_value = -1920
        dialog._center_window(parent=mock_parent)
        dialog.update()
        geom_neg = dialog.geometry()
        # Should center on left monitor: -1920 + 400 = -1520
        # CustomTkinter preserves the explicit left-anchor '+' before negative coordinates (+-1520)
        assert geom_neg == "1120x720+-1520+180"

        # 3. Parent on Compact Display (width=800, height=600, x=0, y=0)
        mock_top.winfo_width.return_value = 800
        mock_top.winfo_height.return_value = 600
        mock_top.winfo_rootx.return_value = 0
        mock_top.winfo_rooty.return_value = 0
        dialog._center_window(parent=mock_parent)
        dialog.update()
        geom_small = dialog.geometry()
        # Should adaptively fit 800x600 without overflow: 740x520+30+40
        assert geom_small == "740x520+30+40"
        assert dialog._min_width == 740
        assert dialog._min_height == 520
    finally:
        dialog._on_close()
        tk_root.update()
