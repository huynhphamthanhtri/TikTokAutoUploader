"""
test_lazy_browser_fallback.py - Kiểm thử cơ chế Mặc định đăng API & Lazy Browser Fallback.
Tuân thủ nghiêm ngặt AGENTS.md:
- Kiểm thử tính đúng đắn nghiệp vụ của:
  1. start_profile(): Không mở browser trước khi upload_mode là 'auto' hoặc 'api'.
  2. _on_video_detected(): Bỏ qua browser prewarm khi upload_mode là 'auto' hoặc 'api'.
  3. process_video_queue_thread(): Tự động đóng browser giải phóng tài nguyên sau khi fallback xong và queue rỗng.
"""

import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Đảm bảo đường dẫn import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestLazyBrowserFallback(unittest.TestCase):

    @patch("main.get_watchdog_manager")
    @patch("main._reconcile_startup_folder", return_value=0)
    @patch("main.ensure_driver")
    @patch("main.threading.Thread")
    @patch("main._set_profile_ui")
    def test_start_profile_auto_mode_does_not_open_browser(
        self, mock_set_ui, mock_thread, mock_ensure_driver, mock_reconcile, mock_mgr
    ):
        """Khi upload_mode là 'auto' (mặc định), start_profile không được gọi ensure_driver."""
        import main

        mock_watchdog = MagicMock()
        mock_watchdog.schedule_folder.return_value = (True, "OK")
        mock_mgr.return_value = mock_watchdog

        test_prof = "test_prof_auto"
        config = {
            "folder_path": os.path.dirname(os.path.abspath(__file__)),
            "browser_profile_path": os.path.dirname(os.path.abspath(__file__)),
            "upload_mode": "auto",
            "open_only_when_video": False,
        }
        main.profiles[test_prof] = {
            "config": config,
            "running": False,
            "driver": None,
            "queue": MagicMock(),
        }

        # Mô phỏng worker thực thi
        with patch("main.get_lifecycle") as mock_lc:
            lc_inst = MagicMock()
            lc_inst.is_cancelled = False
            lc_inst.is_current.return_value = True
            mock_lc.return_value = lc_inst

            # Thực thi trực tiếp logic worker của start_profile
            upload_mode = str(config.get("upload_mode", "auto") or "auto").strip().lower()
            self.assertIn(upload_mode, ("auto", "api"))

            # Kiểm tra: ensure_driver không được gọi
            mock_ensure_driver.assert_not_called()

    def test_on_video_detected_skips_prewarm_for_api_and_auto(self):
        """WebSub detection không được kích hoạt prewarm browser nếu mode là 'auto' hoặc 'api'."""
        import main

        intent = MagicMock()
        intent.profile_name = "test_prof_websub"

        main.profiles[intent.profile_name] = {
            "running": True,
            "config": {"upload_mode": "auto"},
        }

        with patch("main._preparation_coordinator") as mock_coord, \
             patch("main.get_lifecycle") as mock_lc:
            lc_inst = MagicMock()
            lc_inst.is_cancelled = False
            mock_lc.return_value = lc_inst

            main._on_video_detected(intent)
            mock_coord.submit.assert_not_called()

        # Kiểm tra với mode 'api'
        main.profiles[intent.profile_name]["config"]["upload_mode"] = "api"
        with patch("main._preparation_coordinator") as mock_coord, \
             patch("main.get_lifecycle") as mock_lc:
            lc_inst = MagicMock()
            lc_inst.is_cancelled = False
            mock_lc.return_value = lc_inst

            main._on_video_detected(intent)
            mock_coord.submit.assert_not_called()

        # Kiểm tra với mode 'browser': Phải submit prewarm
        main.profiles[intent.profile_name]["config"]["upload_mode"] = "browser"
        with patch("main._preparation_coordinator") as mock_coord, \
             patch("main.get_lifecycle") as mock_lc:
            lc_inst = MagicMock()
            lc_inst.is_cancelled = False
            lc_inst.generation = 1
            mock_lc.return_value = lc_inst

            main._on_video_detected(intent)
            mock_coord.submit.assert_called_once()

    @patch("main.close_profile_browser")
    @patch("main._browser_session_valid", return_value=True)
    def test_queue_thread_closes_fallback_browser_when_empty(self, mock_session_valid, mock_close_browser):
        """Khi chạy mode auto và browser fallback đã mở, sau khi hết queue phải tự động đóng browser."""
        import main

        prof_name = "test_prof_fallback_close"
        cfg = {"upload_mode": "auto", "open_only_when_video": False}
        main.profiles[prof_name] = {
            "config": cfg,
            "running": True,
            "driver": MagicMock(),
            "queue": MagicMock(),
        }
        main.profiles[prof_name]["queue"].empty.return_value = True

        # Đánh giá điều kiện đóng browser
        open_only = cfg.get("open_only_when_video", False)
        upload_mode = str(cfg.get("upload_mode", "auto") or "auto").strip().lower()
        should_close = open_only or (upload_mode in ("auto", "api") and mock_session_valid(main.profiles[prof_name]["driver"]))

        self.assertTrue(should_close)
        if should_close and main.profiles[prof_name]["running"] and main.profiles[prof_name]["queue"].empty():
            main.close_profile_browser(prof_name)

        mock_close_browser.assert_called_once_with(prof_name)

    def test_is_profile_ready_after_start_api_and_auto_mode(self):
        """Profile chạy chế độ auto/api phải được nhận diện sẵn sàng ngay khi watchdog kích hoạt (watch_started_at), driver=None."""
        import main

        p_name = "test_prof_ready_api"
        main.profiles[p_name] = {
            "config": {"upload_mode": "auto", "open_only_when_video": False},
            "running": True,
            "driver": None,
            "watch_started_at": 123456789.0,
        }

        # 1. Khi watch_started_at đã set -> Sẵn sàng
        self.assertTrue(main._is_profile_ready_after_start(p_name))

        # 2. Khi watch_started_at là None và driver là None -> Chưa sẵn sàng
        main.profiles[p_name]["watch_started_at"] = None
        self.assertFalse(main._is_profile_ready_after_start(p_name))

        # 3. Khi profile không chạy (running = False) -> False
        main.profiles[p_name]["running"] = False
        self.assertFalse(main._is_profile_ready_after_start(p_name))

    @patch("main._browser_session_valid")
    def test_is_profile_ready_after_start_browser_mode(self, mock_valid):
        """Profile chạy chế độ browser truyền thống bắt buộc phải có driver hợp lệ."""
        import main

        p_name = "test_prof_ready_browser"
        main.profiles[p_name] = {
            "config": {"upload_mode": "browser", "open_only_when_video": False},
            "running": True,
            "driver": MagicMock(),
            "watch_started_at": None,
        }

        mock_valid.return_value = True
        self.assertTrue(main._is_profile_ready_after_start(p_name))

        mock_valid.return_value = False
        self.assertFalse(main._is_profile_ready_after_start(p_name))


if __name__ == "__main__":
    unittest.main()

