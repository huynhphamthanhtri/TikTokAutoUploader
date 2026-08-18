"""
test_ui_guide.py - Unit tests cho tab Hướng Dẫn Sử Dụng (ui_guide.py).
"""

import unittest

import customtkinter as ctk

from ui_guide import GUIDE_SECTIONS, build_guide_workspace

VALID_KINDS = {"intro", "step", "bullet", "note", "warn"}

EXPECTED_TITLES = [
    "1. Cài Đặt & Lần Chạy Đầu",
    "2. Thêm Hồ Sơ (Profiles)",
    "3. Cookie & Đăng Nhập",
    "4. Quản Lý Hồ Sơ",
    "5. Đăng Video (Auto Upload)",
    "6. YouTube Monitor & Batch",
    "7. Thu Nhập / KYC",
    "8. Proxy & Bảo Trì",
    "9. Khắc Phục Sự Cố",
]


class TestGuideSectionsData(unittest.TestCase):
    def test_has_all_9_sections(self):
        titles = [s.get("title") for s in GUIDE_SECTIONS]
        for expected in EXPECTED_TITLES:
            self.assertIn(expected, titles, f"Thiếu mục hướng dẫn: {expected}")

    def test_each_section_has_title_icon_and_blocks(self):
        self.assertGreaterEqual(len(GUIDE_SECTIONS), 9)
        for section in GUIDE_SECTIONS:
            self.assertTrue(section.get("title"), "Mỗi mục phải có title")
            self.assertTrue(section.get("icon"), "Mỗi mục phải có icon")
            blocks = section.get("blocks", [])
            self.assertGreater(len(blocks), 0, f"Mục '{section.get('title')}' không được rỗng")

    def test_blocks_have_valid_kind_and_nonempty_text(self):
        for section in GUIDE_SECTIONS:
            for kind, text in section.get("blocks", []):
                self.assertIn(kind, VALID_KINDS, f"Kiểu block không hợp lệ: {kind} trong '{section.get('title')}'")
                self.assertIsInstance(text, str)
                self.assertTrue(text.strip(), f"Block rỗng trong '{section.get('title')}'")

    def test_each_section_has_at_least_one_step(self):
        for section in GUIDE_SECTIONS:
            kinds = [k for k, _ in section.get("blocks", [])]
            self.assertIn("step", kinds, f"Mục '{section.get('title')}' phải có ít nhất 1 bước thực hiện")

    def test_personalized_brand_and_terminology(self):
        all_text = " ".join(
            text for s in GUIDE_SECTIONS for _, text in s.get("blocks", [])
        )
        self.assertIn("DONGLAO Browser 144", all_text)
        self.assertIn("Login/Mở trình duyệt", all_text)
        self.assertIn("Cookie-First", all_text)


class TestBuildGuideWorkspace(unittest.TestCase):
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

    def test_build_guide_workspace_renders(self):
        if not self.root:
            self.skipTest("Tkinter display not available")
        frame = ctk.CTkFrame(self.root)
        scroll = build_guide_workspace(frame)
        self.assertIsNotNone(scroll)
        self.assertIsInstance(scroll, ctk.CTkScrollableFrame)
        frame.destroy()


if __name__ == "__main__":
    unittest.main()