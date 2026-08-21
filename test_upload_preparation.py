"""Tests for the single-flight UploadPreparationCoordinator."""
import threading
import time
import unittest

from upload_preparation import UploadPreparationCoordinator


class UploadPreparationCoordinatorTests(unittest.TestCase):
    def test_single_flight_does_not_duplicate_inflight_work(self):
        coord = UploadPreparationCoordinator(max_workers=2)
        started = threading.Event()
        release = threading.Event()
        runs = []

        def run_fn(profile_name, generation, cancel_event):
            runs.append((profile_name, generation))
            started.set()
            release.wait(timeout=5)
            return "token-a"

        try:
            f1 = coord.submit("p1", 7, run_fn)
            started.wait(timeout=5)
            f2 = coord.submit("p1", 7, run_fn)
            self.assertIs(f1, f2, "In-flight duplicate for same profile+gen must be deduplicated")
            release.set()
            f1.result(timeout=5)
            self.assertEqual(len(runs), 1)
        finally:
            coord.shutdown()

    def test_await_result_returns_published_token_on_ready(self):
        coord = UploadPreparationCoordinator(max_workers=2)

        def run_fn(profile_name, generation, cancel_event):
            return "ready-token"

        try:
            coord.submit("p1", 7, run_fn)
            token = coord.await_result("p1", 7)
            self.assertEqual(token, "ready-token")
        finally:
            coord.shutdown()

    def test_await_result_returns_none_when_no_prep(self):
        coord = UploadPreparationCoordinator(max_workers=2)
        try:
            self.assertIsNone(coord.await_result("missing", 1))
        finally:
            coord.shutdown()

    def test_await_result_returns_none_on_failure(self):
        coord = UploadPreparationCoordinator(max_workers=2)

        def run_fn(profile_name, generation, cancel_event):
            raise RuntimeError("boom")

        try:
            coord.submit("p1", 7, run_fn)
            self.assertIsNone(coord.await_result("p1", 7))
            state, error, gen = coord.get_state("p1")
            self.assertEqual(state, "FAILED")
            self.assertEqual(gen, 7)
            self.assertIn("boom", error)
        finally:
            coord.shutdown()

    def test_await_result_ignores_other_generation(self):
        coord = UploadPreparationCoordinator(max_workers=2)

        def run_fn(profile_name, generation, cancel_event):
            return "token-gen7"

        try:
            coord.submit("p1", 7, run_fn)
            self.assertIsNone(coord.await_result("p1", 8))
            self.assertEqual(coord.await_result("p1", 7), "token-gen7")
        finally:
            coord.shutdown()

    def test_new_submit_after_failure_allows_retry(self):
        coord = UploadPreparationCoordinator(max_workers=2)
        attempts = {"n": 0}

        def run_fn(profile_name, generation, cancel_event):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("first fail")
            return "ok-token"

        try:
            coord.submit("p1", 7, run_fn)
            self.assertIsNone(coord.await_result("p1", 7))
            coord.submit("p1", 7, run_fn)
            self.assertEqual(coord.await_result("p1", 7), "ok-token")
            self.assertEqual(attempts["n"], 2)
        finally:
            coord.shutdown()

    def test_cancel_profile_sets_cancel_flag(self):
        coord = UploadPreparationCoordinator(max_workers=2)
        started = threading.Event()
        saw_cancel = threading.Event()

        def run_fn(profile_name, generation, cancel_event):
            started.set()
            while not cancel_event.is_set() and not saw_cancel.is_set():
                time.sleep(0.01)
            saw_cancel.set()
            raise RuntimeError("cancelled")

        try:
            coord.submit("p1", 7, run_fn)
            started.wait(timeout=5)
            coord.cancel_profile("p1")
            self.assertTrue(saw_cancel.wait(timeout=5))
        finally:
            coord.shutdown()


if __name__ == "__main__":
    unittest.main()