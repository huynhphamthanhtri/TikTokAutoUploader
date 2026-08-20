"""
test_log_engine.py - Unit tests for BoundedLogQueue and LogEngine.
"""

import unittest
from datetime import datetime
from unittest.mock import MagicMock

from log_engine import (
    BoundedLogQueue,
    LogEngine,
    LogEntry,
    LogLevelPriority,
    get_log_priority,
)


class TestLogEngine(unittest.TestCase):

    def test_log_priority_mapping(self):
        self.assertEqual(get_log_priority("ERROR"), LogLevelPriority.ERROR)
        self.assertEqual(get_log_priority("tag_warning"), LogLevelPriority.WARNING)
        self.assertEqual(get_log_priority("SUCCESS"), LogLevelPriority.SUCCESS)
        self.assertEqual(get_log_priority("DEBUG"), LogLevelPriority.DEBUG)
        self.assertEqual(get_log_priority("INFO"), LogLevelPriority.INFO)
        self.assertEqual(get_log_priority(None), LogLevelPriority.INFO)

    def test_bounded_queue_capacity_and_eviction(self):
        q = BoundedLogQueue(maxsize=3)
        # Push 3 INFO entries
        self.assertTrue(q.push(LogEntry("info 1", "INFO")))
        self.assertTrue(q.push(LogEntry("info 2", "INFO")))
        self.assertTrue(q.push(LogEntry("info 3", "INFO")))
        self.assertEqual(q.size(), 3)
        self.assertEqual(q.high_water_mark, 3)

        # Push 4th entry with ERROR priority -> should evict one INFO entry
        self.assertTrue(q.push(LogEntry("error 1", "ERROR")))
        self.assertEqual(q.size(), 3)
        self.assertEqual(q.dropped_by_level["INFO"], 1)

        # Push another INFO entry -> cannot evict because all are either INFO/ERROR and queue full
        # It should drop the incoming INFO entry
        result = q.push(LogEntry("info 4", "INFO"))
        self.assertFalse(result)
        self.assertEqual(q.dropped_by_level["INFO"], 2)

    def test_pop_batch(self):
        q = BoundedLogQueue(maxsize=10)
        for i in range(5):
            q.push(LogEntry(f"msg {i}", "INFO"))

        batch = q.pop_batch(max_items=3)
        self.assertEqual(len(batch), 3)
        self.assertEqual(q.size(), 2)

        batch2 = q.pop_batch(max_items=10)
        self.assertEqual(len(batch2), 2)
        self.assertTrue(q.is_empty())

    def test_metrics_collection(self):
        q = BoundedLogQueue(maxsize=5)
        q.push(LogEntry("test 1", "INFO"))
        q.push(LogEntry("test 2", "ERROR"))
        metrics = q.get_metrics()
        self.assertEqual(metrics["size"], 2)
        self.assertEqual(metrics["maxsize"], 5)
        self.assertEqual(metrics["total_enqueued"], 2)
        self.assertEqual(metrics["high_water_mark"], 2)

    def test_log_engine_drain_tick_with_mock_widgets(self):
        engine = LogEngine(maxsize=10, max_lines_per_tick=5)
        mock_root = MagicMock()
        mock_status = MagicMock()
        mock_status.winfo_exists.return_value = True
        mock_status.yview.return_value = (0.0, 1.0)
        mock_trim = MagicMock()

        engine.initialize_ui(
            root=mock_root,
            status_widget=mock_status,
            trim_func=mock_trim,
        )

        engine.post_log("line 1", tag="INFO")
        engine.post_log("line 2", tag="INFO")
        engine.post_log("line 3", tag="ERROR")

        engine.drain_tick()

        mock_status.configure.assert_called()
        self.assertTrue(mock_status.insert.called)
        mock_trim.assert_called_with(mock_status, 500)
        self.assertEqual(engine.queue.total_rendered, 3)

        engine.shutdown()


if __name__ == "__main__":
    unittest.main()
