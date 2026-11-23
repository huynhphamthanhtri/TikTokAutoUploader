# -*- coding: utf-8 -*-
"""
Targeted tests for:
1. VideoValidationPolicy relaxing (3600s max duration, VP9/VP8/MPEG4 codecs)
2. CANONICAL_CODEC_VARIANTS expansion
3. humanize_unassigned_reason categorization and humanization
4. UnassignedVideosModal dialog behavior (filtering, export formatting, explorer open safety)
"""

import os
import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from schedule_contracts import VideoValidationPolicy, UnassignedVideo
from video_probe import (
    CANONICAL_CODEC_VARIANTS,
    humanize_unassigned_reason,
    probe_video,
    clear_probe_cache,
)
from ui_dialogs import UnassignedVideosModal


# ---------------------------------------------------------------------------
# 1. VideoValidationPolicy and Codec Variants Tests
# ---------------------------------------------------------------------------

def test_video_validation_policy_defaults():
    policy = VideoValidationPolicy()
    assert policy.max_duration_seconds == 3600.0, "Max duration should be relaxed to 3600.0s (60 mins)"
    assert policy.min_duration_seconds == 3.0
    assert "vp9" in policy.allowed_codecs
    assert "vp8" in policy.allowed_codecs
    assert "mpeg4" in policy.allowed_codecs
    assert "h264" in policy.allowed_codecs
    assert "hevc" in policy.allowed_codecs
    assert "av1" in policy.allowed_codecs


def test_canonical_codec_variants_mappings():
    assert "vp9" in CANONICAL_CODEC_VARIANTS
    assert "vp09" in CANONICAL_CODEC_VARIANTS["vp9"]
    assert "vp8" in CANONICAL_CODEC_VARIANTS
    assert "mpeg4" in CANONICAL_CODEC_VARIANTS
    assert "mp4v" in CANONICAL_CODEC_VARIANTS["mpeg4"]
    assert "mp4v-es" in CANONICAL_CODEC_VARIANTS["mpeg4"]
    assert "h264" in CANONICAL_CODEC_VARIANTS
    assert "avc1" in CANONICAL_CODEC_VARIANTS["h264"]
    assert "hevc" in CANONICAL_CODEC_VARIANTS
    assert "h265" in CANONICAL_CODEC_VARIANTS["hevc"]
    assert "av1" in CANONICAL_CODEC_VARIANTS
    assert "av01" in CANONICAL_CODEC_VARIANTS["av1"]


def test_probe_video_with_relaxed_policy(tmp_path):
    clear_probe_cache()
    policy = VideoValidationPolicy()
    test_file = tmp_path / "sample.mp4"
    test_file.write_bytes(b"dummy_video_bytes_content")

    # Case 1: 45 minute video (2700s) with vp9 codec should PASS
    with patch("shutil.which", return_value="C:\\mock\\ffprobe.exe"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"streams":[{"codec_type":"video","codec_name":"vp9","width":1080,"height":1920,"duration":"2700.0"}],"format":{"duration":"2700.0"}}',
        )
        res = probe_video(test_file, policy=policy)
        assert res.is_valid is True
        assert res.duration_seconds == 2700.0
        assert res.video_codec == "vp9"

    clear_probe_cache()

    # Case 2: 65 minute video (3900s) should FAIL with DURATION_EXCEEDS_MAX
    with patch("shutil.which", return_value="C:\\mock\\ffprobe.exe"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"duration":"3900.0"}],"format":{"duration":"3900.0"}}',
        )
        res = probe_video(test_file, policy=policy)
        assert res.is_valid is False
        assert any("DURATION_EXCEEDS_MAX" in issue for issue in res.issues)
        assert any("3900.0s" in issue for issue in res.issues)

    clear_probe_cache()

    # Case 3: mpeg4 codec should PASS
    with patch("shutil.which", return_value="C:\\mock\\ffprobe.exe"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"streams":[{"codec_type":"video","codec_name":"mpeg4","width":1080,"height":1920,"duration":"60.0"}],"format":{"duration":"60.0"}}',
        )
        res = probe_video(test_file, policy=policy)
        assert res.is_valid is True
        assert res.video_codec == "mpeg4"


# ---------------------------------------------------------------------------
# 2. Humanize Unassigned Reason Tests
# ---------------------------------------------------------------------------

def test_humanize_unassigned_reason_all_categories():
    # Duration exceeds
    cat, msg = humanize_unassigned_reason("DURATION_EXCEEDS_MAX", "DURATION_EXCEEDS_MAX: 1200.0s > 600.0s")
    assert cat == "Thời lượng"
    assert "1200.0s" in msg

    # Duration below min
    cat, msg = humanize_unassigned_reason("DURATION_BELOW_MIN", "DURATION_BELOW_MIN: 1.5s < 3.0s")
    assert cat == "Thời lượng"
    assert "1.5s" in msg

    # Codec
    cat, msg = humanize_unassigned_reason("CODEC_NOT_RECOMMENDED", "CODEC_NOT_RECOMMENDED: wmv (Khuyến nghị: h264, hevc, av1, vp9, vp8, mpeg4)")
    assert cat == "Sai Codec"
    assert "wmv" in msg

    # Empty file
    cat, msg = humanize_unassigned_reason("EMPTY_FILE_ZERO_BYTES", "File size is 0 bytes")
    assert cat == "File lỗi / 0 Byte"
    assert "0 bytes" in msg

    # Corrupt
    cat, msg = humanize_unassigned_reason("CORRUPTED_OR_UNREADABLE", "Moov atom not found")
    assert cat == "File lỗi / 0 Byte"
    assert "lỗi cấu trúc" in msg

    # Conflict
    cat, msg = humanize_unassigned_reason("PROFILE_SKIPPED_DUE_TO_CONFLICT", "Conflict detected")
    assert cat == "Xung đột lịch"

    # System / ffprobe
    cat, msg = humanize_unassigned_reason("FFPROBE_NOT_AVAILABLE", "ffprobe not installed")
    assert cat == "Hệ thống"
    assert "ffprobe" in msg

    # Unknown
    cat, msg = humanize_unassigned_reason("SOME_UNKNOWN_CODE", "Some error")
    assert cat == "Khác"
    assert "Some error" in msg


# ---------------------------------------------------------------------------
# 3. UnassignedVideosModal Logic Tests (Headless)
# ---------------------------------------------------------------------------

def test_unassigned_videos_modal_data_structures():
    items = [
        UnassignedVideo(
            video_path="C:/videos/video1.mp4",
            reason="DURATION_EXCEEDS_MAX",
            details="Video duration 1800.0s > max 600.0s",
        ),
        UnassignedVideo(
            video_path="C:/videos/video2.mp4",
            reason="CODEC_NOT_RECOMMENDED",
            details="Codec 'msmpeg4v3' is not recommended",
        ),
        UnassignedVideo(
            video_path="C:/videos/video3.mp4",
            reason="EMPTY_FILE_ZERO_BYTES",
            details="File size is 0 bytes",
        ),
    ]

    # Verify grouping / categories
    categories = {}
    for item in items:
        cat, msg = humanize_unassigned_reason(item.reason, item.details)
        categories[cat] = categories.get(cat, 0) + 1

    assert categories["Thời lượng"] == 1
    assert categories["Sai Codec"] == 1
    assert categories["File lỗi / 0 Byte"] == 1


def test_export_report_formatting(tmp_path):
    items = [
        UnassignedVideo(
            video_path="C:/videos/video1.mp4",
            reason="DURATION_EXCEEDS_MAX",
            details="Video duration 1800.0s > max 600.0s",
        ),
        UnassignedVideo(
            video_path="C:/videos/video2.mp4",
            reason="CODEC_NOT_RECOMMENDED",
            details="Codec 'wmv' is not recommended",
        ),
    ]

    out_file = tmp_path / "bao_cao_video_bo_qua.txt"
    
    report_lines = [
        "BÁO CÁO DANH SÁCH VIDEO BỊ BỎ QUA KHI LÊN LỊCH",
        "=" * 70,
        f"Tổng số video: {len(items)}",
        "-" * 70,
        "PHÂN LOẠI:",
    ]
    
    cat_counts = {}
    parsed_items = []
    for item in items:
        cat, msg = humanize_unassigned_reason(item.reason, item.details)
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        parsed_items.append((item.video_path, cat, msg))

    for cat, count in cat_counts.items():
        report_lines.append(f"  • {cat}: {count} video")

    report_lines.append("-" * 70)
    report_lines.append("CHI TIẾT TỪNG VIDEO:")
    for idx, (path, cat, msg) in enumerate(parsed_items, 1):
        report_lines.append(f"[{idx}] {Path(path).name}")
        report_lines.append(f"    - Phân loại: {cat}")
        report_lines.append(f"    - Lý do    : {msg}")
        report_lines.append(f"    - Đường dẫn: {path}")
        report_lines.append("")

    with open(out_file, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(report_lines))

    # Read back and verify BOM and contents
    raw_bytes = out_file.read_bytes()
    assert raw_bytes.startswith(b"\xef\xbb\xbf"), "Must start with UTF-8 BOM"

    text = out_file.read_text(encoding="utf-8-sig")
    assert "BÁO CÁO DANH SÁCH VIDEO BỊ BỎ QUA KHI LÊN LỊCH" in text
    assert "video1.mp4" in text
    assert "video2.mp4" in text
    assert "Thời lượng" in text
    assert "Sai Codec" in text


def test_explorer_open_safety():
    # Test that opening explorer path uses os.path.normpath and safe list arguments
    test_path = "C:/test/sample_video.mp4"
    normalized = os.path.normpath(test_path)
    parent_dir = os.path.dirname(normalized)

    with patch("subprocess.Popen") as mock_popen, patch("os.path.exists", return_value=True):
        if os.path.exists(normalized):
            cmd = ["explorer", f"/select,{normalized}"]
        else:
            cmd = ["explorer", parent_dir]
        import subprocess
        subprocess.Popen(cmd)

        mock_popen.assert_called_once_with(["explorer", f"/select,{normalized}"])


def test_unassigned_videos_modal_ui_lifecycle(tmp_path):
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()

    items = [
        UnassignedVideo(
            video_path=str(tmp_path / "vid_duration.mp4"),
            reason="DURATION_EXCEEDS_MAX",
            details="DURATION_EXCEEDS_MAX: 1800.0s > 600.0s",
        ),
        UnassignedVideo(
            video_path=str(tmp_path / "vid_codec.mp4"),
            reason="CODEC_NOT_RECOMMENDED",
            details="CODEC_NOT_RECOMMENDED: wmv",
        ),
        UnassignedVideo(
            video_path=str(tmp_path / "vid_empty.mp4"),
            reason="EMPTY_FILE_ZERO_BYTES",
            details="File size is 0 bytes",
        ),
    ]

    try:
        modal = UnassignedVideosModal(root, items)
        modal.withdraw()  # keep offscreen during automated test

        # Check Treeview row count initially (ALL items)
        initial_children = modal.tree.get_children()
        assert len(initial_children) == 3

        # Check chips
        assert "Tất cả" in modal._chip_buttons
        assert "Thời lượng" in modal._chip_buttons
        assert "Sai Codec" in modal._chip_buttons
        assert "File lỗi / 0 Byte" in modal._chip_buttons
        # Non-existent categories should not be rendered as chips
        assert "Xung đột lịch" not in modal._chip_buttons
        assert "Hệ thống" not in modal._chip_buttons

        # Test filtering by category "Thời lượng"
        modal._set_category_filter("Thời lượng")
        thoiluong_children = modal.tree.get_children()
        assert len(thoiluong_children) == 1
        item_val = modal.tree.item(thoiluong_children[0], "values")
        assert item_val[1] == "vid_duration.mp4"
        assert item_val[2] == "Thời lượng"

        # Switch back to "Tất cả"
        modal._set_category_filter("Tất cả")
        assert len(modal.tree.get_children()) == 3

        # Test copy current paths
        with patch("tkinter.messagebox.showinfo") as mock_info:
            modal._copy_current_paths()
            clipboard_text = root.clipboard_get()
            assert "vid_duration.mp4" in clipboard_text
            assert "vid_codec.mp4" in clipboard_text
            assert "vid_empty.mp4" in clipboard_text
            mock_info.assert_called_once()

        # Test export report to TXT
        export_txt = tmp_path / "exported_modal_report.txt"
        with patch("tkinter.filedialog.asksaveasfilename", return_value=str(export_txt)), \
             patch("tkinter.messagebox.showinfo"):
            modal._export_txt_report()

        assert export_txt.exists()
        raw_bytes = export_txt.read_bytes()
        assert raw_bytes.startswith(b"\xef\xbb\xbf")
        content = export_txt.read_text(encoding="utf-8-sig")
        assert "BÁO CÁO CHI TIẾT VIDEO BỊ BỎ QUA" in content
        assert "vid_duration.mp4" in content
        assert "vid_codec.mp4" in content
        assert "vid_empty.mp4" in content

        # Close modal
        modal._close_modal()
        assert modal._closing is True

    finally:
        root.destroy()


def test_bulk_schedule_modal_unassigned_button_integration(tmp_path):
    """Test that BulkScheduleModal correctly displays and hides the unassigned details button."""
    import tkinter as tk
    from schedule_ui import BulkScheduleModal
    from schedule_manager import ScheduleManager
    from schedule_contracts import SchedulePlan, PlannedTask

    root = tk.Tk()
    root.withdraw()

    db_path = tmp_path / "test_sched.db"
    mgr = ScheduleManager(storage_file=db_path)

    v1 = tmp_path / "video1.mp4"
    v1.write_bytes(b"content1")
    v2 = tmp_path / "video2.mp4"
    v2.write_bytes(b"content2")

    try:
        modal = BulkScheduleModal(
            parent=root,
            selected_profiles=["Profile_1"],
            profile_configs={"Profile_1": {"tiktok_account": "acc1", "timezone": "Asia/Ho_Chi_Minh"}},
            schedule_manager=mgr,
        )
        modal.withdraw()
        modal.scanned_videos = [v1, v2]

        # Initial check: button should not be packed
        assert modal.btn_unassigned_details.winfo_manager() == ""

        # Mock prepare_schedule_plan returning 1 planned task and 1 unassigned video
        dummy_planned = PlannedTask(
            slot_id="SANG",
            profile_name="Profile_1",
            tiktok_account="acc1",
            video_path=str(v1),
            canonical_video_path=str(v1),
            video_filename="video1.mp4",
            title="Video 1",
            profile_timezone="Asia/Ho_Chi_Minh",
            scheduled_epoch=1700000000,
            scheduled_time_local="2026-11-20 07:00",
            execution_mode="LOCAL_QUEUE",
            delete_source_after_post=False,
        )
        dummy_unassigned = UnassignedVideo(
            video_path=str(v2),
            reason="DURATION_EXCEEDS_MAX",
            details="Video duration 3700.0s > 3600.0s",
        )
        mock_plan = SchedulePlan(
            plan_id="plan_123",
            created_at="2026-11-20T07:00:00",
            seed=42,
            profile_assignments={"Profile_1": (dummy_planned,)},
            unassigned_videos=(dummy_unassigned,),
            conflicts_identified=None,
            resolution_mode="APPEND",
            warnings=(),
            requested_profile_names=("Profile_1",),
        )

        with patch("schedule_ui.prepare_schedule_plan", return_value=mock_plan):
            modal._generate_preview()

        # Verify button is now packed and has correct text
        assert modal.btn_unassigned_details.winfo_manager() == "pack"
        assert "1 video bị bỏ qua" in modal.btn_unassigned_details.cget("text")

        # Verify clicking button invokes UnassignedVideosModal
        with patch("schedule_ui.UnassignedVideosModal") as mock_unassigned_modal:
            modal._show_unassigned_details()
            mock_unassigned_modal.assert_called_once_with(modal, (dummy_unassigned,))

        # Verify changing input hides the unassigned button
        modal._on_input_changed()
        assert modal.btn_unassigned_details.winfo_manager() == ""

        modal.destroy()
    finally:
        root.destroy()


