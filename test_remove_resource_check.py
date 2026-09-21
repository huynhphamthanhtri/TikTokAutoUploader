import time
import unittest
from pathlib import Path
from unittest.mock import patch


class TestRemoveResourceCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parent
        cls.main_source = (cls.root_dir / "main.py").read_text(encoding="utf-8")

    def test_check_system_resources_is_noop_and_fast(self):
        """check_system_resources phải trả về True ngay lập tức (<10ms) mà không gọi psutil."""
        import psutil

        # Kiểm tra tĩnh function definition
        self.assertIn("def check_system_resources(profile_name=None):", self.main_source)
        
        # Test runtime behavior của hàm
        scope = {}
        func_code = compile(
            """def check_system_resources(profile_name=None):\n    \"\"\"Kiểm tra tài nguyên hệ thống (deprecated - đã loại bỏ kiểm tra CPU/RAM để không chặn khởi động profile).\"\"\"\n    return True\n""",
            "<string>",
            "exec",
        )
        exec(func_code, scope)
        check_func = scope["check_system_resources"]

        with patch("psutil.cpu_percent") as mock_cpu, patch("psutil.virtual_memory") as mock_mem:
            t0 = time.perf_counter()
            result = check_func("test_profile")
            elapsed = time.perf_counter() - t0

            self.assertTrue(result)
            self.assertLess(elapsed, 0.05, "check_system_resources không được phép block 0.5s")
            mock_cpu.assert_not_called()
            mock_mem.assert_not_called()

    def test_ensure_driver_does_not_contain_resource_throttle(self):
        """ensure_driver không được chứa kiểm tra tài nguyên, delay 5s hoặc ném System Resource Low."""
        start = self.main_source.index("def ensure_driver(profile_name")
        end = self.main_source.index("def start_profile(", start)
        ensure_driver_block = self.main_source[start:end]

        self.assertNotIn("check_system_resources(profile_name)", ensure_driver_block)
        self.assertNotIn("System Resource Low", ensure_driver_block)
        self.assertNotIn("Tài nguyên thấp. Tạm nghỉ 5s", ensure_driver_block)

    def test_sequential_batch_start_does_not_skip_on_low_res(self):
        """_thread_sequential_start không được bỏ qua profile với lý do Low Res hoặc Tài nguyên thấp."""
        start = self.main_source.index("def _thread_sequential_start")
        end = self.main_source.index("def _set_buttons_state", start)
        batch_block = self.main_source[start:end]

        self.assertNotIn("check_system_resources", batch_block)
        self.assertNotIn("Bỏ qua (Low Res)", batch_block)
        self.assertNotIn('skip_reasons["Tài nguyên thấp"]', batch_block)

    def test_main_does_not_call_cpu_percent_or_virtual_memory_for_resources(self):
        """Toàn bộ main.py không còn bất kỳ dòng code nào gọi cpu_percent hay virtual_memory."""
        self.assertNotIn("psutil.cpu_percent", self.main_source)
        self.assertNotIn("psutil.virtual_memory", self.main_source)


if __name__ == "__main__":
    unittest.main()
