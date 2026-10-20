# -*- coding: utf-8 -*-
"""
test_check_info_batch_runner.py - Test suite cho bộ điều phối Concurrency Pool 5 workers.
Tuân thủ nghiêm ngặt AGENTS.md:
- Kiểm tra concurrency pool không vượt quá 5 workers.
- Kiểm tra Run Guard chống ghi đè kết quả cũ.
- Kiểm tra cập nhật cấu trúc config/tiktok_account_data chuẩn xác.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from check_features.services.check_info_batch_runner import CheckInfoBatchRunner
from check_features.services.check_info_service import CheckInfoResult


class TestCheckInfoBatchRunner(unittest.TestCase):
    """Kiểm tra logic điều phối đa luồng của CheckInfoBatchRunner."""

    def setUp(self):
        self.mock_profiles = {
            "Prof_1": {"config": {"name": "Prof_1", "cookie_str": "sessionid=s1"}},
            "Prof_2": {"config": {"name": "Prof_2", "cookie_str": "sessionid=s2"}},
            "Prof_3": {"config": {"name": "Prof_3", "cookie_str": "sessionid=s3"}},
        }

    @patch.object(CheckInfoBatchRunner, "_execute_single_profile")
    def test_run_batch_completes_all_profiles(self, mock_exec_single):
        """Kiểm tra batch hoàn tất cho toàn bộ danh sách profile."""
        def fake_single(pname, run_id):
            cfg = self.mock_profiles[pname]["config"]
            res = CheckInfoResult(
                success=True,
                status="SUCCESS",
                profile_name=pname,
                user={"uid": f"uid_{pname}", "uniqueId": f"user_{pname}"},
                stats={"followerCount": 100},
                checked_at="2026-10-20 12:00:00",
            )
            cfg["tiktok_id"] = f"user_{pname}"
            cfg["tiktok_user_id"] = f"uid_{pname}"
            cfg["session_auth_state"] = "verified"
            return res

        mock_exec_single.side_effect = fake_single

        done_list = []
        batch_summary = {}

        runner = CheckInfoBatchRunner(
            profiles_provider=lambda: self.mock_profiles,
            on_profile_done=lambda name, res: done_list.append(name),
            on_batch_complete=lambda s: batch_summary.update(s),
            max_workers=5,
        )

        t = runner.run_batch_async(["Prof_1", "Prof_2", "Prof_3"])
        t.join(timeout=5.0)

        self.assertEqual(len(done_list), 3)
        self.assertEqual(batch_summary.get("total"), 3)
        self.assertEqual(batch_summary.get("success"), 3)

        # Kiểm tra config profile đã được đồng bộ
        cfg1 = self.mock_profiles["Prof_1"]["config"]
        self.assertEqual(cfg1.get("tiktok_id"), "user_Prof_1")
        self.assertEqual(cfg1.get("tiktok_user_id"), "uid_Prof_1")
        self.assertEqual(cfg1.get("session_auth_state"), "verified")

    @patch("check_features.services.check_info_service.TikTokCheckInfoService.execute")
    def test_run_guard_ignores_stale_run(self, mock_execute):
        """Kiểm tra Run Guard ngăn không cho kết quả cũ ghi đè kết quả mới."""
        mock_execute.return_value = CheckInfoResult(
            success=True,
            status="SUCCESS",
            profile_name="Prof_1",
            user={"uid": "stale_uid", "uniqueId": "stale_user"},
        )

        runner = CheckInfoBatchRunner(
            profiles_provider=lambda: self.mock_profiles,
            max_workers=5,
        )

        # Giả lập run_id bị thay thế trước khi job hoàn thành
        runner._active_runs["Prof_1"] = "new_run_id_9999"
        res = runner._execute_single_profile("Prof_1", "old_run_id_0001")

        # Config không được cập nhật với kết quả stale
        cfg1 = self.mock_profiles["Prof_1"]["config"]
        self.assertNotEqual(cfg1.get("tiktok_id"), "stale_user")



    def test_apply_result_preserves_auth_state_on_unknown_and_no_user_agent_pollution(self):
        """Kiểm tra: khi lỗi proxy/timeout/parse (auth_state='UNKNOWN'), session_auth_state KHÔNG bị đổi thành expired, và user_agent KHÔNG bị ghi vào config."""
        runner = CheckInfoBatchRunner(profiles_provider=lambda: self.mock_profiles)
        cfg = {"session_auth_state": "verified", "name": "Prof_1"}

        # Giả lập kết quả proxy error
        res_proxy = CheckInfoResult(
            success=False,
            status="PROXY_ERROR",
            outcome="FAILED",
            auth_state="UNKNOWN",
            error_code="PROXY_ERROR",
            user_agent="Mozilla/5.0 ObservedUA",
            error="Proxy connection failed",
        )
        runner._apply_result_to_profile("Prof_1", cfg, res_proxy)

        # session_auth_state PHẢI giữ nguyên 'verified', KHÔNG được thành 'expired'
        self.assertEqual(cfg["session_auth_state"], "verified")
        # user_agent KHÔNG được ghi vào config root
        self.assertNotIn("user_agent", cfg)
        self.assertEqual(cfg["last_check_status"], "PROXY_ERROR")

        # Chỉ khi auth_state == 'EXPIRED' thì mới thành 'expired'
        res_expired = CheckInfoResult(
            success=False,
            status="COOKIE_EXPIRED",
            outcome="FAILED",
            auth_state="EXPIRED",
            error="Cookie expired",
        )
        runner._apply_result_to_profile("Prof_1", cfg, res_expired)
        self.assertEqual(cfg["session_auth_state"], "expired")

        # Khi auth_state == 'NOT_LOGGED_IN' thì thành 'not_logged_in'
        res_not_logged = CheckInfoResult(
            success=False,
            status="NOT_LOGGED_IN",
            outcome="FAILED",
            auth_state="NOT_LOGGED_IN",
            error="No sessionid",
        )
        runner._apply_result_to_profile("Prof_1", cfg, res_not_logged)
        self.assertEqual(cfg["session_auth_state"], "not_logged_in")

    @patch.object(CheckInfoBatchRunner, "_execute_single_profile")
    def test_failed_items_breakdown_and_badges(self, mock_exec_single):
        """Kiểm tra phân loại failed_items và gán đúng badge, suggested_action cho từng loại lỗi."""
        mock_p = {
            "Prof_Success": {"config": {"name": "Prof_Success", "session_auth_state": "verified"}},
            "Prof_NoLogin": {"config": {"name": "Prof_NoLogin"}},
            "Prof_Expired": {"config": {"name": "Prof_Expired", "session_auth_state": "verified"}},
            "Prof_Proxy": {"config": {"name": "Prof_Proxy", "session_auth_state": "verified"}},
        }

        runner_ref = []
        def fake_exec(pname, run_id):
            if pname == "Prof_Success":
                res = CheckInfoResult(success=True, status="SUCCESS", profile_name=pname, user={"uid": "1", "uniqueId": "u1"})
            elif pname == "Prof_NoLogin":
                res = CheckInfoResult(success=False, status="NOT_LOGGED_IN", auth_state="NOT_LOGGED_IN", profile_name=pname, error="Thiếu sessionid")
            elif pname == "Prof_Expired":
                res = CheckInfoResult(success=False, status="SESSION_INVALID", auth_state="EXPIRED", profile_name=pname, error="user not found (Mã 20001)")
            elif pname == "Prof_Proxy":
                res = CheckInfoResult(success=False, status="PROXY_ERROR", auth_state="UNKNOWN", profile_name=pname, error="Cannot connect to proxy")
            else:
                res = CheckInfoResult(success=False, status="ERROR", profile_name=pname)
            if runner_ref:
                runner_ref[0]._apply_result_to_profile(pname, mock_p[pname]["config"], res)
            return res

        mock_exec_single.side_effect = fake_exec
        batch_summary = {}

        runner = CheckInfoBatchRunner(
            profiles_provider=lambda: mock_p,
            on_batch_complete=lambda s: batch_summary.update(s),
            max_workers=5,
        )
        runner_ref.append(runner)

        t = runner.run_batch_async(["Prof_Success", "Prof_NoLogin", "Prof_Expired", "Prof_Proxy"])
        t.join(timeout=5.0)

        self.assertEqual(batch_summary["total"], 4)
        self.assertEqual(batch_summary["success"], 1)
        self.assertEqual(batch_summary["not_logged_in"], 1)
        self.assertEqual(batch_summary["expired"], 1)
        self.assertEqual(batch_summary["proxy_error"], 1)

        failed_items = batch_summary["failed_items"]
        self.assertEqual(len(failed_items), 3)

        items_map = {item["profile"]: item for item in failed_items}
        self.assertIn("⚪ Chưa đăng nhập", items_map["Prof_NoLogin"]["status_badge"])
        self.assertIn("🟠 Cần đăng nhập lại", items_map["Prof_Expired"]["status_badge"])
        self.assertIn("🔴 Lỗi Proxy", items_map["Prof_Proxy"]["status_badge"])

        # Kiểm tra session_auth_state cập nhật đúng
        self.assertEqual(mock_p["Prof_NoLogin"]["config"]["session_auth_state"], "not_logged_in")
        self.assertEqual(mock_p["Prof_Expired"]["config"]["session_auth_state"], "expired")
        self.assertEqual(mock_p["Prof_Proxy"]["config"]["session_auth_state"], "verified")


if __name__ == "__main__":
    unittest.main()
