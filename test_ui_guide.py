"""
test_ui_guide.py - Unit tests cho tab Hướng Dẫn Sử Dụng (ui_guide.py).

Phân tách rõ ràng:
1. Pure Logic Tests (CI-safe, không cần display): Kiểm tra schema block dict, kiểm tra cấm thuật ngữ kỹ thuật,
   từ khóa thân thiện, placeholder an toàn, filter pure functions, summary format, navigation allowlist.
2. Desktop Interactive Tests: Kiểm tra khởi tạo widget CTk, accordion toggle, copy handler và navigation callback.
"""

import os
import unittest
from unittest.mock import MagicMock

import customtkinter as ctk

from ui_guide import (
    FILTER_CATEGORIES,
    GUIDE_SECTIONS,
    SAFE_NAVIGATION_TARGETS,
    GuideCardView,
    _copy_to_clipboard,
    build_guide_workspace,
    filter_guide_sections,
    format_guide_summary,
    get_safe_navigation_target,
)

VALID_KINDS = {"intro", "step", "bullet", "note", "warn", "example", "shortcut"}

EXPECTED_TITLES = [
    "1. Bắt Đầu Sử Dụng",
    "2. Thêm Tài Khoản TikTok",
    "3. Đăng Nhập Và Cookie",
    "4. Quản Lý Danh Sách Tài Khoản",
    "5. Đăng Video Tự Động",
    "6. Tải Video Từ YouTube",
    "7. Thu Nhập, KYC Và Thuế",
    "8. Proxy Và Bảo Trì",
    "9. Xử Lý Lỗi Thường Gặp",
]

# Danh sách các thuật ngữ kỹ thuật nội bộ bị cấm trong nội dung hiển thị cho người dùng
BANNED_TECHNICAL_TERMS = [
    "_internal",
    "websub",
    "pubsubhubbub",
    "ngrok",
    "webhook port",
    "ffmpeg",
    "nvenc",
    "gpu encoder",
    "staging",
    "pyinstaller",
    "cdd",
    "poa",
    "youtube_proxy_fallback",
    "netscape",
]


# ==============================================================================
# 1. PURE DATA & LOGIC TESTS (CI-SAFE, ZERO DISPLAY NEEDED)
# ==============================================================================

class TestGuideSectionsSchemaAndContent(unittest.TestCase):
    """Kiểm tra cấu trúc dữ liệu GUIDE_SECTIONS và tính chuẩn xác của nội dung."""

    def test_has_all_9_sections_with_exact_titles(self):
        titles = [s.get("title") for s in GUIDE_SECTIONS]
        self.assertEqual(len(GUIDE_SECTIONS), 9)
        for expected in EXPECTED_TITLES:
            self.assertIn(expected, titles, f"Thiếu mục hướng dẫn: {expected}")

    def test_each_section_has_required_metadata(self):
        for section in GUIDE_SECTIONS:
            self.assertTrue(section.get("id"), "Mỗi mục phải có trường id")
            self.assertTrue(section.get("tag"), "Mỗi mục phải có trường tag")
            self.assertTrue(section.get("icon"), "Mỗi mục phải có trường icon")
            self.assertTrue(section.get("title"), "Mỗi mục phải có trường title")
            self.assertTrue(section.get("subtitle"), "Mỗi mục phải có trường subtitle")
            blocks = section.get("blocks", [])
            self.assertGreater(len(blocks), 0, f"Mục '{section.get('title')}' không được rỗng")

    def test_blocks_are_dicts_with_valid_kind_and_text(self):
        for section in GUIDE_SECTIONS:
            for block in section.get("blocks", []):
                self.assertIsInstance(block, dict, f"Block phải là dict trong '{section.get('title')}': {block}")
                kind = block.get("kind")
                text = block.get("text")
                self.assertIn(kind, VALID_KINDS, f"Kiểu block không hợp lệ: {kind} trong '{section.get('title')}'")
                self.assertIsInstance(text, str, f"Text phải là string trong '{section.get('title')}'")
                self.assertTrue(text.strip(), f"Text rỗng trong '{section.get('title')}'")

                # Block example phải có copy_text
                if kind == "example":
                    copy_text = block.get("copy_text")
                    self.assertIsInstance(copy_text, str)
                    self.assertTrue(copy_text.strip(), f"Example block thiếu copy_text trong '{section.get('title')}'")

                # Block shortcut phải có target hợp lệ trong allowlist
                if kind == "shortcut":
                    target = block.get("target")
                    self.assertIn(target, SAFE_NAVIGATION_TARGETS, f"Shortcut target '{target}' không nằm trong allowlist an toàn")

    def test_no_banned_technical_jargon_in_user_content(self):
        """Bảo đảm 100% nội dung hiển thị cho người dùng không chứa thuật ngữ kỹ thuật ngầm."""
        for section in GUIDE_SECTIONS:
            sec_title = section.get("title", "")
            sec_subtitle = section.get("subtitle", "")
            for term in BANNED_TECHNICAL_TERMS:
                self.assertNotIn(term, sec_title.lower(), f"Phát hiện thuật ngữ cấm '{term}' trong title '{sec_title}'")
                self.assertNotIn(term, sec_subtitle.lower(), f"Phát hiện thuật ngữ cấm '{term}' trong subtitle '{sec_subtitle}'")

            for block in section.get("blocks", []):
                text = str(block.get("text", "")).lower()
                copy_text = str(block.get("copy_text", "")).lower()
                for term in BANNED_TECHNICAL_TERMS:
                    self.assertNotIn(term, text, f"Phát hiện thuật ngữ cấm '{term}' trong block text của '{sec_title}': {text}")
                    self.assertNotIn(term, copy_text, f"Phát hiện thuật ngữ cấm '{term}' trong copy_text của '{sec_title}': {copy_text}")

    def test_contains_essential_user_facing_terminology(self):
        """Bảo đảm các từ khóa quan trọng mà người dùng nhìn thấy trên giao diện đều xuất hiện."""
        all_user_text = " ".join(
            f"{s.get('title')} {s.get('subtitle')} " + " ".join(b.get('text', '') for b in s.get('blocks', []))
            for s in GUIDE_SECTIONS
        )
        essential_terms = [
            "đăng nhập",
            "Cookie Die",
            "Cookie Sống",
            "Login/Mở trình duyệt",
            "Proxy",
            "Kiểm Tra Proxy",
            "YouTube",
            "KYC",
            "video",
            "yt-dlp",
            "Start",
            "License Key",
        ]
        for term in essential_terms:
            self.assertIn(term.lower(), all_user_text.lower(), f"Thiếu từ khóa thân thiện người dùng: '{term}'")

    def test_example_placeholders_are_safe_and_sanitized(self):
        """Bảo đảm mẫu copy không chứa mật khẩu hoặc token trông như thật."""
        for section in GUIDE_SECTIONS:
            for block in section.get("blocks", []):
                if block.get("kind") == "example":
                    copy_text = block.get("copy_text", "")
                    self.assertNotIn("MatKhau123", copy_text)
                    self.assertNotIn("2FA_KEY", copy_text)
                    self.assertNotIn("user:pass", copy_text)
                    # Phải chứa placeholder an toàn rõ ràng
                    self.assertTrue(
                        "<MAT_KHAU>" in copy_text or "IP:Port" in copy_text,
                        f"Mẫu copy không chuẩn placeholder an toàn: {copy_text}",
                    )


class TestGuidePureLogic(unittest.TestCase):
    """Kiểm tra các hàm logic thuần: filter_guide_sections, format_guide_summary, get_safe_navigation_target."""

    def test_get_safe_navigation_target(self):
        self.assertEqual(get_safe_navigation_target("profiles"), "profiles")
        self.assertEqual(get_safe_navigation_target("youtube"), "youtube")
        self.assertEqual(get_safe_navigation_target("monetization"), "monetization")
        self.assertEqual(get_safe_navigation_target("PROFILES"), "profiles")
        self.assertIsNone(get_safe_navigation_target("hacker_tab"))
        self.assertIsNone(get_safe_navigation_target(""))
        self.assertIsNone(get_safe_navigation_target(None))

    def test_format_guide_summary(self):
        self.assertEqual(format_guide_summary(9, 9), "Đang xem 9/9 hướng dẫn")
        self.assertEqual(format_guide_summary(9, 3), "Đang xem 3/9 hướng dẫn")
        self.assertEqual(format_guide_summary(9, 0), "Đang xem 0/9 hướng dẫn")

    def test_filter_guide_sections_all(self):
        results = filter_guide_sections(GUIDE_SECTIONS, query="", category_tag="all")
        self.assertEqual(len(results), len(GUIDE_SECTIONS))

    def test_filter_guide_sections_by_category(self):
        start_sections = filter_guide_sections(GUIDE_SECTIONS, query="", category_tag="start")
        self.assertEqual(len(start_sections), 1)
        self.assertEqual(start_sections[0]["id"], "start")

        accounts_sections = filter_guide_sections(GUIDE_SECTIONS, query="", category_tag="accounts")
        self.assertEqual(len(accounts_sections), 3)  # Thêm tài khoản, Cookie, Quản lý tài khoản

        errors_sections = filter_guide_sections(GUIDE_SECTIONS, query="", category_tag="errors")
        self.assertEqual(len(errors_sections), 1)
        self.assertEqual(errors_sections[0]["id"], "troubleshooting")

    def test_filter_guide_sections_by_query(self):
        # Query "proxy"
        res_proxy = filter_guide_sections(GUIDE_SECTIONS, query="proxy", category_tag="all")
        self.assertGreaterEqual(len(res_proxy), 2)

        # Query "xác minh bot"
        res_bot = filter_guide_sections(GUIDE_SECTIONS, query="xác minh bot", category_tag="all")
        self.assertGreaterEqual(len(res_bot), 1)

        # Query không tồn tại
        res_empty = filter_guide_sections(GUIDE_SECTIONS, query="chuoi_khong_ton_tai_xyz_12345", category_tag="all")
        self.assertEqual(len(res_empty), 0)

    def test_filter_guide_sections_combined_query_and_category(self):
        # Category "accounts" + query "cookie"
        res = filter_guide_sections(GUIDE_SECTIONS, query="cookie", category_tag="accounts")
        self.assertGreaterEqual(len(res), 1)
        for s in res:
            self.assertEqual(s["tag"], "accounts")

    def test_filter_guide_sections_immutability(self):
        original_len = len(GUIDE_SECTIONS)
        _ = filter_guide_sections(GUIDE_SECTIONS, query="test", category_tag="proxy")
        self.assertEqual(len(GUIDE_SECTIONS), original_len)


# ==============================================================================
# 2. INTERACTIVE & DESKTOP RENDERING TESTS
# ==============================================================================

@unittest.skipIf(os.environ.get("CI", "").lower() == "true", "Interactive Tk rendering is verified on desktop runner")
class TestGuideDesktopInteractive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = ctk.CTk()
            cls.root.withdraw()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def test_build_guide_workspace_renders_successfully(self):
        if not self.root:
            self.skipTest("Tkinter display not available")
        mock_nav = MagicMock()
        frame = ctk.CTkFrame(self.root)
        scroll = build_guide_workspace(frame, on_navigate=mock_nav)
        self.assertIsNotNone(scroll)
        self.assertIsInstance(scroll, ctk.CTkScrollableFrame)
        frame.destroy()

    def test_guide_card_view_accordion_behavior(self):
        if not self.root:
            self.skipTest("Tkinter display not available")
        frame = ctk.CTkFrame(self.root)
        section = GUIDE_SECTIONS[0]
        card = GuideCardView(frame, section, initially_expanded=False)

        # Initial state: collapsed
        self.assertFalse(card.is_expanded)
        self.assertFalse(bool(card.body.winfo_manager()))

        # Toggle to expanded
        card._on_toggle_clicked()
        self.assertTrue(card.is_expanded)
        self.assertTrue(bool(card.body.winfo_manager()))

        # Toggle to collapsed
        card._on_toggle_clicked()
        self.assertFalse(card.is_expanded)
        self.assertFalse(bool(card.body.winfo_manager()))

        frame.destroy()

    def test_copy_to_clipboard_handler_with_mock(self):
        mock_widget = MagicMock()
        mock_feedback = MagicMock()
        mock_feedback.winfo_exists.return_value = True

        ok = _copy_to_clipboard(mock_widget, "Sample Text", mock_feedback)
        self.assertTrue(ok)
        mock_widget.clipboard_clear.assert_called_once()
        mock_widget.clipboard_append.assert_called_once_with("Sample Text")
        mock_feedback.configure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
