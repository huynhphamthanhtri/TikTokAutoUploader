"""
test_unified_delivery_pipeline.py - Regression tests for the Unified Delivery Registry
and the at-most-once enqueue semantics shared by Fast Path, Watchdog and startup scan.
"""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from browser_lifecycle import get_lifecycle, remove_lifecycle
from watchdog_service import (
    DeliveryOutcome,
    DeliveryRegistry,
    DeliveryState,
    TOMBSTONE_OUTCOMES,
    canonical_path,
    get_delivery_registry,
)


class TestUnifiedDeliveryRegistry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"x" * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def _registry(self, persist=False):
        ledger = None
        if persist:
            ledger = self.root / "delivery_ledger.json"
        return DeliveryRegistry(ledger_path=ledger)

    def test_claim_exactly_once(self):
        reg = self._registry()
        ok1, rec1, r1 = reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
        self.assertTrue(ok1)
        self.assertEqual(rec1.state, DeliveryState.CLAIMED)
        ok2, rec2, r2 = reg.claim_delivery(self.video, "P", 1, "WATCHDOG_EVENT")
        self.assertFalse(ok2)
        self.assertEqual(r2, "already_claimed")

    def test_race_fast_path_and_watchdog_enqueue_once(self):
        reg = self._registry()
        enqueued = []

        def fast_path_try():
            ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
            if ok:
                reg.transition_delivery(self.video, DeliveryState.ENQUEUED)
                enqueued.append("FAST_PATH")

        def watchdog_try():
            ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "WATCHDOG_EVENT")
            if ok:
                reg.transition_delivery(self.video, DeliveryState.ENQUEUED)
                enqueued.append("WATCHDOG")

        import threading
        threads = [threading.Thread(target=fast_path_try), threading.Thread(target=watchdog_try)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(enqueued), 1)
        rec = reg.get_delivery(self.video)
        self.assertEqual(rec.state, DeliveryState.ENQUEUED)

    def test_enqueue_rollback_releases_claim(self):
        reg = self._registry()
        ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
        self.assertTrue(ok)
        # queue.put() throws -> rollback
        reg.release_delivery(self.video, error_code="ENQUEUE_FAILED", error_detail="queue failed")
        rec = reg.get_delivery(self.video)
        self.assertEqual(rec.state, DeliveryState.DISCOVERED)
        # now a retry can claim again
        ok2, rec2, _ = reg.claim_delivery(self.video, "P", 1, "WATCHDOG_EVENT")
        self.assertTrue(ok2)

    def test_stale_generation_rejection_and_recovery(self):
        reg = self._registry()
        ok, rec, _ = reg.claim_delivery(self.video, "P", 5, "FAST_PATH")
        self.assertTrue(ok)
        reg.transition_delivery(self.video, DeliveryState.ENQUEUED)
        # profile restarted: current generation now 6 -> stale item is reclaimable
        ok, reason = reg.is_eligible_for_startup(self.video, "P", 6)
        self.assertTrue(ok)
        self.assertEqual(reason, "stale_generation")
        # recover stale claims with new generation map
        recovered = reg.recover_stale_claims(ttl_seconds=0, profile_generations={"P": 6})
        self.assertGreaterEqual(recovered, 1)
        rec = reg.get_delivery(self.video)
        self.assertEqual(rec.state, DeliveryState.DISCOVERED)

    def test_tombstone_blocks_reclaim(self):
        reg = self._registry()
        ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
        self.assertTrue(ok)
        reg.complete_delivery(self.video, DeliveryOutcome.POST_UNCERTAIN, post_dispatched=True)
        rec = reg.get_delivery(self.video)
        self.assertEqual(rec.state, DeliveryState.TERMINAL)
        self.assertEqual(rec.outcome, DeliveryOutcome.POST_UNCERTAIN)
        self.assertTrue(rec.post_dispatched)
        ok2, rec2, r2 = reg.claim_delivery(self.video, "P", 2, "WATCHDOG_EVENT")
        self.assertFalse(ok2)
        self.assertEqual(r2, "tombstone")
        ok3, reason = reg.is_eligible_for_startup(self.video, "P", 2)
        self.assertFalse(ok3)
        self.assertEqual(reason, "tombstone")

    def test_posted_tombstone_preserved_on_release(self):
        reg = self._registry()
        ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
        reg.complete_delivery(self.video, DeliveryOutcome.POSTED, post_dispatched=True)
        reg.release_delivery(self.video)
        rec = reg.get_delivery(self.video)
        self.assertEqual(rec.state, DeliveryState.TERMINAL)
        self.assertEqual(rec.outcome, DeliveryOutcome.POSTED)

    def test_retryable_terminal_reclaim(self):
        reg = self._registry()
        ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
        reg.complete_delivery(self.video, DeliveryOutcome.FAILED_SAFE)
        ok2, rec2, r2 = reg.claim_delivery(self.video, "P", 2, "WATCHDOG_EVENT")
        self.assertTrue(ok2)
        self.assertEqual(r2, "reclaimed")
        self.assertEqual(rec2.state, DeliveryState.CLAIMED)

    def test_idempotency_key_channel_video(self):
        reg = self._registry()
        ok1, rec1, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH",
                                          channel_id="UC123", youtube_video_id="abc123")
        self.assertTrue(ok1)
        ok2, rec2, r2 = reg.claim_delivery(self.video, "P", 1, "WATCHDOG_EVENT",
                                           channel_id="UC123", youtube_video_id="abc123")
        self.assertFalse(ok2)
        self.assertEqual(rec1.delivery_id, rec2.delivery_id)
        self.assertIn("UC123", rec1.delivery_id)
        self.assertIn("abc123", rec1.delivery_id)

    def test_mark_waiting_profile_then_reclaim(self):
        reg = self._registry()
        rec = reg.mark_waiting_profile(self.video, "P", channel_id="UC1", youtube_video_id="v1")
        self.assertEqual(rec.state, DeliveryState.WAITING_PROFILE)
        ok, rec2, r2 = reg.claim_delivery(self.video, "P", 3, "WATCHDOG_STARTUP",
                                          channel_id="UC1", youtube_video_id="v1")
        self.assertTrue(ok)
        self.assertEqual(rec2.state, DeliveryState.CLAIMED)

    def test_path_reuse_different_file_after_terminal(self):
        reg = self._registry()
        ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
        self.assertTrue(ok)
        reg.complete_delivery(self.video, DeliveryOutcome.POST_UNCERTAIN, post_dispatched=True)
        # same path now holds a different file -> should become a NEW delivery
        os.utime(self.video, (time.time() + 2, time.time() + 2))
        original_id = rec.delivery_id
        ok2, rec2, r2 = reg.claim_delivery(self.video, "P", 2, "WATCHDOG_EVENT")
        self.assertTrue(ok2)
        self.assertNotEqual(original_id, rec2.delivery_id)

    def test_ledger_persists_and_restores(self):
        ledger = self.root / "delivery_ledger.json"
        reg = DeliveryRegistry(ledger_path=ledger)
        ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH",
                                        channel_id="UC1", youtube_video_id="v99")
        self.assertTrue(ok)
        reg.complete_delivery(self.video, DeliveryOutcome.POSTED, post_dispatched=True)
        self.assertTrue(ledger.exists())

        reg2 = DeliveryRegistry(ledger_path=ledger)
        rec2 = reg2.get_delivery(self.video)
        self.assertIsNotNone(rec2)
        self.assertEqual(rec2.state, DeliveryState.TERMINAL)
        self.assertEqual(rec2.outcome, DeliveryOutcome.POSTED)
        self.assertTrue(rec2.post_dispatched)
        # tombstone survives restart -> no re-enqueue
        ok3, _, r3 = reg2.claim_delivery(self.video, "P", 2, "WATCHDOG_STARTUP")
        self.assertFalse(ok3)
        self.assertEqual(r3, "tombstone")

    def test_ledger_backup_recovery(self):
        ledger = self.root / "delivery_ledger.json"
        reg = DeliveryRegistry(ledger_path=ledger)
        reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
        reg.complete_delivery(self.video, DeliveryOutcome.POSTED, post_dispatched=True)
        self.assertTrue(ledger.exists())

        # corrupt the primary ledger; loader must fall back to the last-good .bak
        ledger.write_text("{broken json", encoding="utf-8")
        reg2 = DeliveryRegistry(ledger_path=ledger)
        rec = reg2.get_delivery(self.video)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.profile_name, "P")
        self.assertEqual(rec.canonical_path, canonical_path(self.video))
        self.assertIn(rec.state, (DeliveryState.CLAIMED, DeliveryState.TERMINAL))

    def test_transition_validation(self):
        reg = self._registry()
        ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
        # invalid: CLAIMED -> PROCESSING is not allowed (must go ENQUEUED)
        self.assertFalse(reg.transition_delivery(self.video, DeliveryState.PROCESSING))
        self.assertTrue(reg.transition_delivery(self.video, DeliveryState.ENQUEUED))
        self.assertTrue(reg.transition_delivery(self.video, DeliveryState.PROCESSING))
        self.assertTrue(reg.transition_delivery(self.video, DeliveryState.POST_DISPATCHED))
        self.assertTrue(reg.complete_delivery(self.video, DeliveryOutcome.POSTED, post_dispatched=True))

    def test_recover_does_not_touch_post_dispatched(self):
        reg = self._registry()
        ok, rec, _ = reg.claim_delivery(self.video, "P", 1, "FAST_PATH")
        reg.transition_delivery(self.video, DeliveryState.ENQUEUED)
        reg.transition_delivery(self.video, DeliveryState.PROCESSING)
        reg.transition_delivery(self.video, DeliveryState.POST_DISPATCHED)
        # never recovered even with stale generation
        recovered = reg.recover_stale_claims(ttl_seconds=0, profile_generations={"P": 99})
        self.assertEqual(recovered, 0)
        rec = reg.get_delivery(self.video)
        self.assertEqual(rec.state, DeliveryState.POST_DISPATCHED)

    def test_canonical_normalization(self):
        p1 = str(self.video)
        p2 = str(self.video).upper()
        self.assertEqual(canonical_path(p1), canonical_path(p2))


class TestFastPathCoordinatorIntegration(unittest.TestCase):
    """Integration tests exercising the real coordinator (main.enqueue_video) with the
    global delivery registry and the real ProfileLifecycle - i.e. the exact wiring used
    by YouTube Fast Path, the shared watchdog and the startup reconciliation scan."""

    def setUp(self):
        import watchdog_service as ws
        with ws._DELIVERY_REGISTRY_LOCK:
            ws._GLOBAL_DELIVERY_REGISTRY = None
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        ws.configure_delivery_registry(self.root / "ledger.json")
        self.profile = "TEST_PROFILE"
        import main
        self.main = main
        self.queue = None
        main.profiles[self.profile] = {
            'running': False,
            'queue': self.queue,
            'config': {},
        }
        lc = get_lifecycle(self.profile)
        lc.begin()  # gen=1, uncancelled
        self.generation = lc.generation

    def tearDown(self):
        import main
        main.profiles.pop(self.profile, None)
        try:
            remove_lifecycle(self.profile)
        except Exception:
            pass
        import watchdog_service as ws
        with ws._DELIVERY_REGISTRY_LOCK:
            ws._GLOBAL_DELIVERY_REGISTRY = None
        self.tmp.cleanup()

    def _start_profile(self, gen=None):
        import queue as q
        self.queue = q.Queue()
        self.main.profiles[self.profile]['queue'] = self.queue
        self.main.profiles[self.profile]['running'] = True
        if gen is not None:
            lc = get_lifecycle(self.profile)
            if lc.generation != gen:
                lc.begin() if lc.generation < gen else None
        return self.queue

    def test_enqueue_places_tagged_item_and_transitions_state(self):
        self._start_profile()
        video = self.root / "tagged.mp4"
        video.write_bytes(b"data")
        ok, reason = self.main.enqueue_video(
            self.profile, str(video), source="FAST_PATH",
            channel_id="UC_X", youtube_video_id="vid_1", title="t",
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "enqueued")
        item = self.queue.get_nowait()
        from watchdog_service import QueueItem as QI
        self.assertIsInstance(item, QI)
        self.assertEqual(item.lifecycle_generation, get_lifecycle(self.profile).generation)
        self.assertEqual(item.source, "FAST_PATH")
        rec = get_delivery_registry().get_delivery(video)
        self.assertEqual(rec.state, DeliveryState.ENQUEUED)
        self.assertTrue(rec.post_dispatched is not True)
        # second producer is rejected (at-most-once)
        ok2, reason2 = self.main.enqueue_video(
            self.profile, str(video), source="WATCHDOG_EVENT",
            channel_id="UC_X", youtube_video_id="vid_1",
        )
        self.assertFalse(ok2)
        self.assertIn(reason2, ("already_in_progress", "already_claimed"))
        self.assertTrue(self.queue.empty())

    def test_waiting_profile_keeps_file_then_consumes(self):
        video = self.root / "waiting.mp4"
        video.write_bytes(b"data")
        ok, reason = self.main.enqueue_video(self.profile, str(video), source="FAST_PATH")
        self.assertFalse(ok)
        self.assertEqual(reason, "waiting_profile")
        rec = get_delivery_registry().get_delivery(video)
        self.assertEqual(rec.state, DeliveryState.WAITING_PROFILE)
        self.assertEqual(rec.profile_name, self.profile)
        # profile starts -> startup scan adopts it
        self._start_profile()
        adopted = self.main._reconcile_startup_folder(
            self.profile, str(self.root), get_lifecycle(self.profile).generation
        )
        self.assertEqual(adopted, 1)
        self.assertFalse(self.queue.empty())

    def test_profile_missing_marks_waiting(self):
        video = self.root / "orphan.mp4"
        video.write_bytes(b"data")
        ok, reason = self.main.enqueue_video("GHOST_PROFILE", str(video), source="FAST_PATH")
        self.assertFalse(ok)
        self.assertEqual(reason, "profile_missing")
        rec = get_delivery_registry().get_delivery(video)
        self.assertEqual(rec.state, DeliveryState.WAITING_PROFILE)
        self.assertEqual(rec.profile_name, "GHOST_PROFILE")

    def test_complete_delivery_maps_post_uncertain_tombstone(self):
        self._start_profile()
        video = self.root / "uncertain.mp4"
        video.write_bytes(b"data")
        ok, _ = self.main.enqueue_video(self.profile, str(video), source="FAST_PATH")
        self.assertTrue(ok)
        result = SimpleNamespace(outcome="post_uncertain", post_dispatched=True, message="timeout")
        self.main._complete_delivery_from_upload(str(video), result, None, True)
        rec = get_delivery_registry().get_delivery(video)
        self.assertEqual(rec.state, DeliveryState.TERMINAL)
        self.assertEqual(rec.outcome, DeliveryOutcome.POST_UNCERTAIN)
        self.assertTrue(rec.post_dispatched)
        # not auto-retried even if the file reappears
        ok2, _ = self.main.enqueue_video(self.profile, str(video), source="WATCHDOG_EVENT")
        self.assertFalse(ok2)

    def test_complete_delivery_maps_prepared_retryable(self):
        self._start_profile()
        video = self.root / "prepared.mp4"
        video.write_bytes(b"data")
        ok, _ = self.main.enqueue_video(self.profile, str(video), source="FAST_PATH")
        self.assertTrue(ok)
        result = SimpleNamespace(outcome="prepared", post_dispatched=False, message="dry-run ok")
        self.main._complete_delivery_from_upload(str(video), result, None, True)
        rec = get_delivery_registry().get_delivery(video)
        self.assertEqual(rec.outcome, DeliveryOutcome.PREPARED)
        self.assertFalse(rec.post_dispatched)

    def test_complete_delivery_maps_failed_safe_on_exception(self):
        self._start_profile()
        video = self.root / "failed.mp4"
        video.write_bytes(b"data")
        ok, _ = self.main.enqueue_video(self.profile, str(video), source="FAST_PATH")
        self.assertTrue(ok)
        self.main._complete_delivery_from_upload(str(video), None, "boom", False)
        rec = get_delivery_registry().get_delivery(video)
        self.assertEqual(rec.outcome, DeliveryOutcome.FAILED_SAFE)
        self.assertIn("boom", rec.error_detail)

    def test_reconcile_adopts_only_known_origin(self):
        self._start_profile()
        known = self.root / "known.mp4"
        unknown = self.root / "manual.mp4"
        known.write_bytes(b"k")
        unknown.write_bytes(b"m")
        original_lookup = self.main.lookup_download
        self.main.lookup_download = lambda path: {
            "channel_id": "UC_K",
            "video_id": "vid_k",
            "title": "known",
        } if str(path) == str(known) else None
        try:
            adopted = self.main._reconcile_startup_folder(
                self.profile, str(self.root), get_lifecycle(self.profile).generation
            )
        finally:
            self.main.lookup_download = original_lookup
        self.assertEqual(adopted, 1)
        self.assertEqual(get_delivery_registry().get_delivery(known).state, DeliveryState.ENQUEUED)
        self.assertIsNone(get_delivery_registry().get_delivery(unknown))
        enqueued = [item.path for item in list(self.queue.queue)]
        self.assertIn(str(known), enqueued)
        self.assertNotIn(str(unknown), enqueued)

    def test_consumer_skips_stale_generation_item(self):
        import queue as q
        self.queue = q.Queue()
        self.main.profiles[self.profile]['queue'] = self.queue
        self.main.profiles[self.profile]['running'] = True
        lc = get_lifecycle(self.profile)
        lc.begin()  # gen=2
        current_gen = lc.generation
        video = self.root / "stale.mp4"
        video.write_bytes(b"data")
        ok, _ = self.main.enqueue_video(self.profile, str(video), source="FAST_PATH")
        self.assertTrue(ok)
        stale_item = self.queue.get()
        # profile restarts -> generation bumps, the queued item is now stale
        lc.begin()  # gen=3
        self.queue.put(stale_item)
        thread = threading.Thread(target=self.main.process_video_queue_thread, args=(self.profile,), daemon=True)
        thread.start()
        deadline = time.time() + 5
        released = False
        while time.time() < deadline:
            rec = get_delivery_registry().get_delivery(video)
            if rec is not None and rec.state == DeliveryState.DISCOVERED:
                released = True
                break
            time.sleep(0.05)
        self.main.profiles[self.profile]['running'] = False
        thread.join(timeout=5)
        self.assertTrue(released, "stale item must be released for re-claim")
        self.assertEqual(stale_item.lifecycle_generation, current_gen)
        self.assertGreater(get_lifecycle(self.profile).generation, current_gen)


if __name__ == "__main__":
    unittest.main()