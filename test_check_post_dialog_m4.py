# -*- coding: utf-8 -*-
"""
test_check_post_dialog_m4.py - Kiểm thử Mốc 4: UI Minh Bạch 3 Trạng Thái & Chống Race Condition.
Tuân thủ nghiêm ngặt AGENTS.md:
1. Hiển thị 3 trạng thái phân minh trên Treeview (🔴 Trùng lặp, ✅ Không trùng, ⚠️ Chưa xác định).
2. Bảo toàn phương trình thống kê Header & Status Summary:
   DUPLICATE + CLEAR + UNKNOWN = Total posts.
3. Chống race condition (Run Guard): Hủy kết quả cũ khi refresh hoặc chuyển lượt chạy.
4. Hiển thị thông tin đối soát chi tiết khi chọn dòng và kích hoạt nút Bài Liên Quan đúng nghiệp vụ.
"""

import unittest
from unittest.mock import MagicMock, patch
import customtkinter as ctk

from check_features.ui.check_post_dialog import CheckPostDialog
from check_features.models import AwemePost, DedupResult, LivePostSnapshot


def run_thread_sync(target=None, args=(), kwargs=None, *other_args, **other_kwargs):
    """Helper chạy worker thread đồng bộ và xử lý after(0, ...) ngay lập tức trong test."""
    kwargs = kwargs or {}
    t = MagicMock()
    def fake_start():
        if target:
            target(*args, **kwargs)
    t.start = fake_start
    return t


class TestCheckPostDialogM4(unittest.TestCase):
    """Kiểm thử Mốc 4 cho CheckPostDialog."""

    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.mock_cfg = {
            "name": "NEW 64",
            "tiktok_id": "user2513527685201",
            "cookie_str": "sessionid=test_cookie_64",
        }

    def test_three_states_displayed_in_treeview(self):
        """Kiểm tra Treeview hiển thị chính xác 3 trạng thái phân minh và áp dụng đúng tag màu."""
        dlg = CheckPostDialog(self.root, "NEW 64", self.mock_cfg, autoload=False)

        # 1. Post DUPLICATE
        p_dup = AwemePost(
            aweme_id="7699321502767893782",
            desc="Video trung lap",
            group_id="7699321502767893000",
            dedup_result=DedupResult(
                aweme_id="7699321502767893782",
                group_id="7699321502767893000",
                state="DUPLICATE",
                reason="CONFIRMED_DUPLICATE",
                source="ttm_sqlite",
            ),
        )

        # 2. Post CLEAR
        p_clear = AwemePost(
            aweme_id="7699077508527967510",
            desc="Video khong trung",
            group_id="7699077508527967510",
            dedup_result=DedupResult(
                aweme_id="7699077508527967510",
                group_id="7699077508527967510",
                state="CLEAR",
                reason="MATCHED_ORIGINAL",
                source="feed_api",
            ),
        )

        # 3. Post UNKNOWN
        p_unk = AwemePost(
            aweme_id="7699000000000000099",
            desc="Video chua ro",
            dedup_result=DedupResult(
                aweme_id="7699000000000000099",
                state="UNKNOWN",
                reason="NO_EVIDENCE",
                source="none",
            ),
        )

        dlg._insert_row(1, p_dup)
        dlg._insert_row(2, p_clear)
        dlg._insert_row(3, p_unk)

        # Kiểm tra values của từng dòng
        val_dup = dlg.tree.item("7699321502767893782")["values"]
        tags_dup = dlg.tree.item("7699321502767893782")["tags"]
        assert "🔴 Trùng (76993215..)" in str(val_dup[9])
        assert "dup" in tags_dup

        val_clear = dlg.tree.item("7699077508527967510")["values"]
        tags_clear = dlg.tree.item("7699077508527967510")["tags"]
        assert "✅ Không trùng" in str(val_clear[9])
        assert "ok" in tags_clear

        val_unk = dlg.tree.item("7699000000000000099")["values"]
        tags_unk = dlg.tree.item("7699000000000000099")["tags"]
        assert "⚠️ Chưa xác định" in str(val_unk[9])
        assert "unknown" in tags_unk

        dlg.destroy()

    def test_conservation_equation_in_header_and_summary(self):
        """Kiểm tra phương trình bảo toàn thống kê: DUPLICATE + CLEAR + UNKNOWN = Total posts."""
        dlg = CheckPostDialog(self.root, "NEW 64", self.mock_cfg, autoload=False)

        posts = []
        # 3 Duplicates
        for i in range(3):
            posts.append(AwemePost(
                aweme_id=f"769900000000000001{i}",
                group_id="7699000000000000000",
                dedup_result=DedupResult(aweme_id=f"769900000000000001{i}", group_id="7699000000000000000", state="DUPLICATE", reason="CONFIRMED_DUPLICATE"),
            ))
        # 5 Clear
        for i in range(5):
            aid = f"769900000000000002{i}"
            posts.append(AwemePost(
                aweme_id=aid,
                group_id=aid,
                dedup_result=DedupResult(aweme_id=aid, group_id=aid, state="CLEAR", reason="MATCHED_ORIGINAL"),
            ))
        # 2 Unknown
        for i in range(2):
            posts.append(AwemePost(
                aweme_id=f"769900000000000003{i}",
                dedup_result=DedupResult(aweme_id=f"769900000000000003{i}", state="UNKNOWN", reason="NO_EVIDENCE"),
            ))

        mock_snapshot = LivePostSnapshot(
            posts=posts,
            source="live_studio",
            fetched_at=1792600000,
            completeness="COMPLETE",
            run_id="test_run_conserv",
        )

        with patch("threading.Thread", side_effect=run_thread_sync), \
             patch.object(dlg.client, "fetch_live_snapshot", return_value=mock_snapshot), \
             patch("check_features.services.duplicate_resolver.DuplicateResolver.resolve_batch"):
            dlg._fetch_page_async(cursor=0, append=False)
            dlg.update()

        tot = len(dlg._posts)
        assert tot == 10

        summary_text = dlg.lbl_status_summary.cget("text")
        assert "Tổng số: 10 bài" in summary_text
        assert "🔴 Trùng: 3" in summary_text
        assert "✅ Không trùng: 5" in summary_text
        assert "⚠️ Chưa xác định: 2" in summary_text

        # Xác nhận tổng bảo toàn tuyệt đối 100%
        dup_cnt = 3
        clear_cnt = 5
        unk_cnt = 2
        assert dup_cnt + clear_cnt + unk_cnt == tot

        meta_text = dlg.lbl_meta_sub.cget("text")
        assert "Đã tải 10 video" in meta_text
        assert "LIVE Creator Studio" in meta_text
        assert "Đầy đủ" in meta_text

        dlg.destroy()

    def test_run_guard_race_condition_protection(self):
        """Kiểm tra cơ chế Run Guard bỏ qua kết quả của lượt chạy cũ khi có phiên mới hơn."""
        dlg = CheckPostDialog(self.root, "NEW 64", self.mock_cfg, autoload=False)

        # Giả lập 2 phiên chạy với 2 kết quả khác nhau
        post_old = [AwemePost(aweme_id="7699000000000000001", desc="Old Post")]
        post_new = [AwemePost(aweme_id="7699000000000000002", desc="New Post")]

        snap_old = LivePostSnapshot(posts=post_old, source="live_studio", run_id="run_old")
        snap_new = LivePostSnapshot(posts=post_new, source="live_studio", run_id="run_new")

        # Bắt đầu lượt chạy cũ
        dlg._current_run_id = "run_new"  # Người dùng đã bấm Refresh sang run_new

        # Giả lập worker cũ cố gắng commit kết quả của run_old
        with patch.object(dlg.client, "fetch_live_snapshot", return_value=snap_old):
            # Tạo commit UI với run_id cũ
            def _fake_commit_old():
                if dlg._current_run_id != "run_old":
                    return  # Bị chặn bởi Run Guard!
                dlg._posts.extend(post_old)

            _fake_commit_old()

        # Xác nhận kết quả cũ không lọt vào danh sách
        assert len(dlg._posts) == 0

        # Worker mới commit với đúng run_new
        def _fake_commit_new():
            if dlg._current_run_id != "run_new":
                return
            dlg._posts.extend(post_new)
            dlg._posts_by_id["7699000000000000002"] = post_new[0]

        _fake_commit_new()
        assert len(dlg._posts) == 1
        assert dlg._posts[0].aweme_id == "7699000000000000002"

        dlg.destroy()

    def test_tree_selection_updates_detail_and_enables_dup_button(self):
        """Kiểm tra khi người dùng chọn một dòng, thanh trạng thái hiển thị chi tiết và nút Bài Liên Quan bật/tắt đúng."""
        dlg = CheckPostDialog(self.root, "NEW 64", self.mock_cfg, autoload=False)

        p_dup = AwemePost(
            aweme_id="7699321502767893782",
            desc="Dup post",
            group_id="7699321502767893000",
            dedup_result=DedupResult(
                aweme_id="7699321502767893782",
                group_id="7699321502767893000",
                state="DUPLICATE",
                reason="CONFIRMED_DUPLICATE",
                source="ttm_sqlite",
            ),
        )
        p_clear = AwemePost(
            aweme_id="7699077508527967510",
            desc="Clear post",
            group_id="7699077508527967510",
            dedup_result=DedupResult(
                aweme_id="7699077508527967510",
                group_id="7699077508527967510",
                state="CLEAR",
                reason="MATCHED_ORIGINAL",
                source="feed_api",
            ),
        )

        dlg._posts_by_id["7699321502767893782"] = p_dup
        dlg._posts_by_id["7699077508527967510"] = p_clear
        dlg._insert_row(1, p_dup)
        dlg._insert_row(2, p_clear)

        conservation_stats = "Tổng số: 2 bài | 🔴 Trùng: 1 | ✅ Không trùng: 1 | ⚠️ Chưa xác định: 0"
        dlg.lbl_status_summary.configure(text=conservation_stats)

        # 1. Chọn dòng Duplicate (Codex Finding 2: lbl_status_summary được bảo toàn, chi tiết vào lbl_selected_detail)
        dlg.tree.selection_set("7699321502767893782")
        dlg._on_tree_select()

        assert dlg.btn_open_dup.cget("state") == "normal"
        # Thống kê tổng thể không bị mất khi chọn dòng!
        assert dlg.lbl_status_summary.cget("text") == conservation_stats

        detail_dup = dlg.lbl_selected_detail.cget("text")
        assert "🔴 TRÙNG LẶP [ID: 7699321502767893782]" in detail_dup
        assert "7699321502767893000" in detail_dup
        assert "ttm_sqlite" in detail_dup

        # 2. Chọn dòng Clear
        dlg.tree.selection_set("7699077508527967510")
        dlg._on_tree_select()

        assert dlg.btn_open_dup.cget("state") == "disabled"
        assert dlg.lbl_status_summary.cget("text") == conservation_stats

        detail_clear = dlg.lbl_selected_detail.cget("text")
        assert "✅ KHÔNG TRÙNG [ID: 7699077508527967510]" in detail_clear
        assert "feed_api" in detail_clear

        # 3. Bỏ chọn (Deselect)
        dlg.tree.selection_remove("7699077508527967510")
        dlg._on_tree_select()

        assert dlg.btn_open_dup.cget("state") == "disabled"
        assert dlg.lbl_status_summary.cget("text") == conservation_stats
        assert "Chọn một video" in dlg.lbl_selected_detail.cget("text")

        dlg.destroy()


def test_pagination_uses_api_cursor_and_has_more_from_snapshot():
    """Codex Finding 1: Phân trang UI dùng trực tiếp next_cursor và has_more của API snapshot, không suy diễn từ số bài."""
    root = ctk.CTk()
    root.withdraw()
    cfg = {"name": "NEW 64", "tiktok_id": "user2513527685201"}
    dlg = CheckPostDialog(root, "NEW 64", cfg, autoload=False)

    # Giả lập 20 bài nhưng API trả next_cursor = 45 (do server lọc hoặc nhảy cursor)
    posts_p1 = [AwemePost(aweme_id=f"76990000000000000{i:02d}", desc=f"Video {i}") for i in range(20)]
    snap_p1 = LivePostSnapshot(
        posts=posts_p1,
        source="live_studio",
        completeness="PARTIAL",
        next_cursor=45,  # Khác biệt hoàn toàn với len(posts) = 20
        has_more=True,
    )

    with patch("threading.Thread", side_effect=run_thread_sync), \
         patch.object(dlg.client, "fetch_live_snapshot", return_value=snap_p1), \
         patch("check_features.services.duplicate_resolver.DuplicateResolver.resolve_batch"):
        dlg._fetch_page_async(cursor=0, append=False)
        dlg.update()

    # Xác nhận UI nhận đúng next_cursor = 45 và has_more = True từ snapshot API
    assert dlg._current_cursor == 45
    assert dlg._has_more is True
    assert dlg.btn_load_more.cget("state") == "normal"
    assert len(dlg._posts) == 20

    # Trang 2: Snapshot báo has_more = False và next_cursor = 70
    posts_p2 = [AwemePost(aweme_id=f"7699000000000000{i+20:02d}", desc=f"Video {i+20}") for i in range(8)]
    snap_p2 = LivePostSnapshot(
        posts=posts_p2,
        source="live_studio",
        completeness="COMPLETE",
        next_cursor=70,
        has_more=False,
    )

    with patch("threading.Thread", side_effect=run_thread_sync), \
         patch.object(dlg.client, "fetch_live_snapshot", return_value=snap_p2), \
         patch("check_features.services.duplicate_resolver.DuplicateResolver.resolve_batch"):
        dlg._fetch_page_async(cursor=45, append=True)
        dlg.update()

    assert dlg._current_cursor == 70
    assert dlg._has_more is False
    assert dlg.btn_load_more.cget("state") == "disabled"
    assert len(dlg._posts) == 28

    dlg.destroy()
    root.destroy()
