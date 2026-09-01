"""Regression tests for analytics cache merging."""

import unittest
from unittest.mock import MagicMock, patch

import main


class TestAnalyticsCacheMerge(unittest.TestCase):
    def setUp(self):
        self.profile_name = "__analytics_cache_test__"
        self.previous = main.monetization_cache.get(self.profile_name)
        main.monetization_cache[self.profile_name] = {
            "balance": 12.5,
            "analytics": {"state": "SUCCESS", "follower_count": 100, "views_30d": 50},
        }

    def tearDown(self):
        if self.previous is None:
            main.monetization_cache.pop(self.profile_name, None)
        else:
            main.monetization_cache[self.profile_name] = self.previous

    def test_failure_retains_last_known_good_metrics(self):
        main._merge_analytics_result(
            self.profile_name,
            {"state": "NETWORK_ERROR", "follower_count": None, "views_30d": None, "error_code": "TIMEOUT"},
        )

        snapshot = main.monetization_cache[self.profile_name]
        self.assertEqual(snapshot["balance"], 12.5)
        self.assertEqual(snapshot["analytics"]["follower_count"], 100)
        self.assertEqual(snapshot["analytics"]["views_30d"], 50)
        self.assertEqual(snapshot["analytics"]["state"], "NETWORK_ERROR")

    def test_cache_requires_successful_views_and_future_expiry(self):
        main.monetization_cache[self.profile_name]["analytics"].update({"views_state": "SUCCESS", "fresh_until": 9_999_999_999})
        self.assertTrue(main._analytics_cache_is_fresh(self.profile_name))

        main.monetization_cache[self.profile_name]["analytics"].update({"fresh_until": 0})
        self.assertFalse(main._analytics_cache_is_fresh(self.profile_name))

    def test_monetization_table_renders_legacy_follower_without_name_error(self):
        class FakeTree:
            def __init__(self):
                self.rows = []

            def get_children(self):
                return ()

            def delete(self, _item):
                pass

            def insert(self, _parent, _position, **kwargs):
                self.rows.append(kwargs)

        class FakeVar:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        tree = FakeTree()
        profile_name = "__legacy_follower_table_test__"
        original_snapshot = main.monetization_cache.get(profile_name)
        try:
            main.monetization_cache[profile_name] = {"follower_count": 67}
            with patch.object(main, "profiles", {profile_name: {"config": {}, "ui": {}}}), patch.object(main, "ui_widgets", {"monetization_tree": tree}), patch.object(main, "selected_project_var", FakeVar(main.ALL_OPTION)), patch.object(main, "filter_var", FakeVar("")), patch.object(main, "mono_active_filter_chip_var", FakeVar("ALL")):
                main._update_monetization_table()
            self.assertEqual(tree.rows[0]["values"][5], "67")
        finally:
            if original_snapshot is None:
                main.monetization_cache.pop(profile_name, None)
            else:
                main.monetization_cache[profile_name] = original_snapshot

    def test_analytics_worker_waits_for_client_result_without_short_future_timeout(self):
        profile_name = "__analytics_worker_timeout_test__"
        future = MagicMock()
        future.result.return_value = {"state": "SUCCESS", "views_state": "SUCCESS", "follower_state": "SUCCESS", "views_30d": 1, "follower_count": 1}
        executor = MagicMock()
        executor.__enter__.return_value.submit.return_value = future
        original_snapshot = main.monetization_cache.get(profile_name)
        try:
            with patch.object(main, "profiles", {profile_name: {"config": {}}}), \
                 patch.object(main, "ThreadPoolExecutor", return_value=executor), \
                 patch.object(main, "fetch_profile_analytics"), \
                 patch.object(main, "_save_monetization_cache"), \
                 patch.object(main, "update_status"), \
                 patch.object(main, "_update_monetization_table"), \
                 patch.object(main, "update_profile_list"), \
                 patch.object(main, "root") as mock_root, \
                 patch.object(main, "toast_manager") as mock_toast, \
                 patch.object(main, "mono_status_var"):
                main._do_fetch_analytics_worker([profile_name])
            future.result.assert_called_once_with()
            self.assertIn(profile_name, main.monetization_cache)
            mock_toast.enqueue.assert_called()
            mock_root.after.assert_called()
        finally:
            if original_snapshot is None:
                main.monetization_cache.pop(profile_name, None)
            else:
                main.monetization_cache[profile_name] = original_snapshot

    def test_merge_discards_legacy_crp_zero_as_raw_views(self):
        main.monetization_cache[self.profile_name]["analytics"] = {
            "calculation_source": "CRP_DASHBOARD_OVERVIEW",
            "views_30d": 0,
            "videos_30d": 0,
        }
        main._merge_analytics_result(
            self.profile_name,
            {"state": "PARTIAL", "views_state": "NOT_AVAILABLE", "calculation_source": "CRP_QUALIFIED_VIEWS", "crp_qualified_views_30d": 0},
        )

        analytics = main.monetization_cache[self.profile_name]["analytics"]
        self.assertNotIn("views_30d", analytics)
        self.assertNotIn("videos_30d", analytics)
        self.assertEqual(analytics["crp_qualified_views_30d"], 0)

    def test_resolve_profile_name_normalizes_numeric_tree_value(self):
        with patch.object(main, "profiles", {"10": {"config": {}}}):
            self.assertEqual(main._resolve_profile_name(10), "10")
            self.assertIsNone(main._resolve_profile_name("missing"))


if __name__ == "__main__":
    unittest.main()
