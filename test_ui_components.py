"""
test_ui_components.py - Unit tests for ui_components.py
"""

import time
import unittest

from ui_components import (
    ToastEvent,
    ToastManager,
    calculate_summary_counts,
    redact_proxy_string,
)


class TestUIComponents(unittest.TestCase):
    def test_redact_proxy_string(self):
        """Proxy passwords must be masked into ***."""
        self.assertEqual(redact_proxy_string("1.2.3.4:8080:myuser:secret123"), "1.2.3.4:8080:myuser:***")
        self.assertEqual(redact_proxy_string("1.2.3.4:8080"), "1.2.3.4:8080")
        self.assertEqual(redact_proxy_string(""), "")
        self.assertEqual(redact_proxy_string(None), "")

    def test_toast_event_redaction(self):
        """ToastEvent automatically masks proxy credentials in message."""
        evt = ToastEvent(message="Proxy 1.2.3.4:8080:alice:secretpass checked OK", level="SUCCESS")
        self.assertNotIn("secretpass", evt.message)
        self.assertIn("1.2.3.4:8080:alice:***", evt.message)

    def test_calculate_summary_counts_empty(self):
        """Empty profiles dict returns zero counts."""
        res = calculate_summary_counts({})
        self.assertEqual(res, {"total": 0, "running": 0, "cookie_live": 0, "errors": 0})

    def test_calculate_summary_counts_project_and_status(self):
        """Summary model computes total, running, cookie_live, and errors correctly."""
        now = time.time()
        sample_profiles = {
            "prof_1": {
                "status": "running",
                "config": {
                    "project_name": "Project_A",
                    "tiktok_account": "user_one",
                    "session_auth_state": "live",
                    "session_verified_at": now - 100,  # fresh
                },
            },
            "prof_2": {
                "status": "error",
                "config": {
                    "project_name": "Project_A",
                    "tiktok_account": "user_two",
                    "session_auth_state": "live",
                    "session_verified_at": now - 90000,  # expired TTL (86400s)
                },
            },
            "prof_3": {
                "status": "stopped",
                "config": {
                    "project_name": "Project_B",
                    "tiktok_account": "user_three",
                    "session_auth_state": "unknown",
                },
            },
        }

        # Filter Project_A
        res_a = calculate_summary_counts(sample_profiles, active_project="Project_A", ttl_seconds=86400, current_timestamp=now)
        self.assertEqual(res_a["total"], 2)
        self.assertEqual(res_a["running"], 1)
        self.assertEqual(res_a["cookie_live"], 1)  # Only prof_1 is fresh
        self.assertEqual(res_a["errors"], 1)       # prof_2 is error

        # Filter All
        res_all = calculate_summary_counts(sample_profiles, active_project="Tất cả", ttl_seconds=86400, current_timestamp=now)
        self.assertEqual(res_all["total"], 3)
        self.assertEqual(res_all["running"], 1)
        self.assertEqual(res_all["cookie_live"], 1)
        self.assertEqual(res_all["errors"], 1)

    def test_calculate_summary_counts_search_filter(self):
        """Search text matches name, tiktok_id, or proxy."""
        sample_profiles = {
            "us_alpha": {
                "status": "stopped",
                "config": {
                    "project_name": "Mặc định",
                    "tiktok_account": "us_trend",
                    "proxy_string": "104.28.1.1:8080",
                },
            },
            "de_beta": {
                "status": "stopped",
                "config": {
                    "project_name": "Mặc định",
                    "tiktok_account": "de_clip",
                    "proxy_string": "185.22.4.9:8080",
                },
            },
        }
        res_search = calculate_summary_counts(sample_profiles, active_project="Tất cả", filter_text="trend")
        self.assertEqual(res_search["total"], 1)

        res_proxy_search = calculate_summary_counts(sample_profiles, active_project="Tất cả", filter_text="185.22")
        self.assertEqual(res_proxy_search["total"], 1)


if __name__ == "__main__":
    unittest.main()
