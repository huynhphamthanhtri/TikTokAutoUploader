"""
watchdog_service.py - Shared Single-Observer Watchdog Registry, Unified Delivery Pipeline & Bounded Stability Pool.

Features:
- Single shared `Observer` across all profiles, eliminating thread explosion.
- Unified Delivery Registry: idempotency-keyed record of every video handed to the
  TikTok queue (Fast Path, Watchdog events, startup reconciliation, manual).
- Lifecycle Generation verification at event reception, stability processing, and queue enqueueing.
- Explicit folder ownership policy to prevent accidental video routing collisions.
- Bounded file stability pool (ThreadPoolExecutor max_workers=3) with path deduplication.
- Crash recovery: Automatically restarts observer and reschedules active watches.
- At-most-once enqueue semantics per session; persistent ledger for restart-safe dedupe.
"""

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    FileSystemEventHandler = object
    Observer = None

LEDGER_DIR = Path(os.environ.get("BK_DELIVERY_LEDGER_DIR", "")) if os.environ.get("BK_DELIVERY_LEDGER_DIR") else None


class DeliveryState(str, Enum):
    DISCOVERED = "DISCOVERED"
    WAITING_PROFILE = "WAITING_PROFILE"
    CLAIMED = "CLAIMED"
    ENQUEUED = "ENQUEUED"
    PROCESSING = "PROCESSING"
    POST_DISPATCHED = "POST_DISPATCHED"
    TERMINAL = "TERMINAL"


class DeliveryOutcome(str, Enum):
    POSTED = "POSTED"
    FAILED_SAFE = "FAILED_SAFE"
    PREPARED = "PREPARED"
    POST_UNCERTAIN = "POST_UNCERTAIN"
    CANCELLED_SAFE = "CANCELLED_SAFE"
    CANCELLED_UNCERTAIN = "CANCELLED_UNCERTAIN"
    REJECTED = "REJECTED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"


# Terminal outcomes that MUST NOT be auto-enqueued again (potential duplicate post risk).
TOMBSTONE_OUTCOMES = {
    DeliveryOutcome.POSTED,
    DeliveryOutcome.POST_UNCERTAIN,
    DeliveryOutcome.CANCELLED_UNCERTAIN,
    DeliveryOutcome.REJECTED,
}

# Terminal outcomes that are safe to reclaim later through explicit action/startup policy.
RETRYABLE_OUTCOMES = {
    DeliveryOutcome.FAILED_SAFE,
    DeliveryOutcome.PREPARED,
    DeliveryOutcome.CANCELLED_SAFE,
    DeliveryOutcome.LOGIN_REQUIRED,
}

_VALID_STATE_TRANSITIONS = {
    DeliveryState.DISCOVERED: {DeliveryState.CLAIMED, DeliveryState.WAITING_PROFILE, DeliveryState.TERMINAL},
    DeliveryState.WAITING_PROFILE: {DeliveryState.CLAIMED, DeliveryState.TERMINAL},
    DeliveryState.CLAIMED: {DeliveryState.ENQUEUED, DeliveryState.DISCOVERED, DeliveryState.TERMINAL},
    DeliveryState.ENQUEUED: {DeliveryState.PROCESSING, DeliveryState.DISCOVERED, DeliveryState.TERMINAL},
    DeliveryState.PROCESSING: {DeliveryState.POST_DISPATCHED, DeliveryState.DISCOVERED, DeliveryState.TERMINAL},
    DeliveryState.POST_DISPATCHED: {DeliveryState.TERMINAL},
    DeliveryState.TERMINAL: set(),
}


@dataclass(frozen=True)
class QueueItem:
    """Generation-tagged queue item consumed by the upload worker."""

    path: str
    profile_name: str
    lifecycle_generation: int
    source: str
    delivery_id: str
    enqueued_at: float


@dataclass
class DeliveryRecord:
    delivery_id: str
    canonical_path: str
    profile_name: str
    channel_id: Optional[str]
    youtube_video_id: Optional[str]
    file_size: int
    file_mtime_ns: int
    claimed_by: str
    lifecycle_generation: int
    state: DeliveryState
    outcome: Optional[DeliveryOutcome]
    post_dispatched: bool
    claimed_at: float
    updated_at: float
    error_code: Optional[str] = None
    error_detail: Optional[str] = None

    def as_dict(self):
        d = asdict(self)
        d["state"] = self.state.value if self.state else None
        d["outcome"] = self.outcome.value if self.outcome else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DeliveryRecord":
        return cls(
            delivery_id=d.get("delivery_id", ""),
            canonical_path=d.get("canonical_path", ""),
            profile_name=d.get("profile_name", ""),
            channel_id=d.get("channel_id"),
            youtube_video_id=d.get("youtube_video_id"),
            file_size=int(d.get("file_size", 0) or 0),
            file_mtime_ns=int(d.get("file_mtime_ns", 0) or 0),
            claimed_by=d.get("claimed_by", ""),
            lifecycle_generation=int(d.get("lifecycle_generation", 0) or 0),
            state=DeliveryState(d["state"]) if d.get("state") else DeliveryState.DISCOVERED,
            outcome=DeliveryOutcome(d["outcome"]) if d.get("outcome") else None,
            post_dispatched=bool(d.get("post_dispatched", False)),
            claimed_at=float(d.get("claimed_at", 0) or 0),
            updated_at=float(d.get("updated_at", 0) or 0),
            error_code=d.get("error_code"),
            error_detail=d.get("error_detail"),
        )


def canonical_path(path: str | Path) -> str:
    """Return normalized, lowercase, absolute path string on Windows."""
    try:
        resolved = Path(path).expanduser().resolve()
        norm = os.path.normcase(str(resolved))
        return norm
    except Exception:
        return str(path).lower().replace("/", "\\")


class DeliveryRegistry:
    """Unified delivery state machine with optional persistent ledger.

    Idempotency key:
      1. profile_name + channel_id + youtube_video_id (when YouTube metadata known)
      2. profile_name + canonical_path + size + mtime_ns   (fallback)
    """

    def __init__(self, ledger_path: Optional[Path] = None, save_on_mutation: bool = True):
        self._lock = threading.RLock()
        self._records: Dict[str, DeliveryRecord] = {}
        self._path_index: Dict[str, str] = {}
        self._ledger_path = Path(ledger_path) if ledger_path else None
        self._save_on_mutation = save_on_mutation
        self._load()

    # ------------------------------------------------------------------ persistence

    def _load(self):
        if self._ledger_path is None or not self._ledger_path.exists():
            return
        data = None
        try:
            import json
            data = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except Exception:
            bak = Path(str(self._ledger_path) + ".bak")
            if bak.exists():
                try:
                    import json
                    data = json.loads(bak.read_text(encoding="utf-8"))
                except Exception:
                    data = None
        if not isinstance(data, dict):
            return
        records = data.get("records") or []
        for item in records:
            try:
                rec = DeliveryRecord.from_dict(item)
            except Exception:
                continue
            if not rec.delivery_id:
                continue
            self._records[rec.delivery_id] = rec
            self._path_index[rec.canonical_path] = rec.delivery_id

    def save(self) -> bool:
        """Atomically persist the ledger. No-op when not configured."""
        if self._ledger_path is None or not self._save_on_mutation:
            return True
        try:
            import json
            payload = {
                "version": 1,
                "records": [r.as_dict() for r in self._records.values()],
            }
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._ledger_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            if self._ledger_path.exists():
                try:
                    bak = Path(str(self._ledger_path) + ".bak")
                    if bak.exists():
                        bak.unlink(missing_ok=True)
                    os.replace(self._ledger_path, bak)
                except OSError:
                    pass
            os.replace(tmp, self._ledger_path)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ helpers

    def _file_identity(self, path: str) -> Tuple[int, int]:
        try:
            st = os.stat(path)
            return int(st.st_size), int(st.st_mtime_ns)
        except OSError:
            return 0, 0

    def _delivery_id(self, profile_name: str, canonical: str, size: int, mtime_ns: int,
                     channel_id: Optional[str] = None, youtube_video_id: Optional[str] = None) -> str:
        if channel_id and youtube_video_id:
            return f"yt:{profile_name}:{channel_id}:{youtube_video_id}"
        return f"file:{profile_name}:{canonical}:{size}:{mtime_ns}"

    # ------------------------------------------------------------------ queries

    def get_delivery(self, path: str | Path) -> Optional[DeliveryRecord]:
        c = canonical_path(path)
        with self._lock:
            did = self._path_index.get(c)
            if did is None:
                return None
            return self._records.get(did)

    def get_delivery_by_id(self, delivery_id: str) -> Optional[DeliveryRecord]:
        with self._lock:
            return self._records.get(delivery_id)

    def records_for_profile(self, profile_name: str) -> List[DeliveryRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.profile_name == profile_name]

    def all_records(self) -> List[DeliveryRecord]:
        with self._lock:
            return list(self._records.values())

    # ------------------------------------------------------------------ operations

    def discover_delivery(self, path: str | Path, profile_name: str, generation: int = 0,
                          source: str = "MANUAL", channel_id: Optional[str] = None,
                          youtube_video_id: Optional[str] = None) -> Tuple[bool, DeliveryRecord, str]:
        """Register/refresh a delivery record without claiming it.

        Returns (True, record, reason) when the delivery may proceed toward claim,
        (False, record, reason) when it is blocked by an active or tombstoned delivery.
        """
        c = canonical_path(path)
        size, mtime_ns = self._file_identity(str(path))
        did = self._delivery_id(profile_name, c, size, mtime_ns, channel_id, youtube_video_id)
        with self._lock:
            rec = self._records.get(did)
            path_rec = self._records.get(self._path_index.get(c))
            if rec is None and path_rec is not None:
                rec = path_rec
            now = time.time()
            if rec is not None:
                if rec.state == DeliveryState.TERMINAL:
                    if rec.outcome in TOMBSTONE_OUTCOMES:
                        same_identity = (
                            rec.canonical_path == c
                            and rec.file_size == size
                            and rec.file_mtime_ns == mtime_ns
                        )
                        if rec.delivery_id == did or same_identity:
                            return False, rec, "tombstone"
                        # Path reused by a genuinely different file -> new delivery below.
                        rec = None
                    else:
                        return True, rec, "retryable_terminal"
                if rec is not None:
                    if rec.state in (DeliveryState.ENQUEUED, DeliveryState.PROCESSING, DeliveryState.POST_DISPATCHED):
                        return False, rec, "already_in_progress"
                    # DISCOVERED / WAITING_PROFILE / CLAIMED are reclaimable by a claim attempt
                    rec.updated_at = now
                    self._path_index[c] = did
                    self.save()
                    return True, rec, "exists_active"
            rec = DeliveryRecord(
                delivery_id=did,
                canonical_path=c,
                profile_name=profile_name,
                channel_id=channel_id,
                youtube_video_id=youtube_video_id,
                file_size=size,
                file_mtime_ns=mtime_ns,
                claimed_by=source,
                lifecycle_generation=generation,
                state=DeliveryState.DISCOVERED,
                outcome=None,
                post_dispatched=False,
                claimed_at=now,
                updated_at=now,
            )
            self._records[did] = rec
            self._path_index[c] = did
            self.save()
            return True, rec, "created"

    def mark_waiting_profile(self, path: str | Path, profile_name: str, source: str = "FAST_PATH",
                             channel_id: Optional[str] = None, youtube_video_id: Optional[str] = None) -> DeliveryRecord:
        """Record that a video was finalized while its target profile was not running."""
        ok, rec, reason = self.discover_delivery(path, profile_name, 0, source, channel_id, youtube_video_id)
        if not ok and rec.state == DeliveryState.TERMINAL and rec.outcome in TOMBSTONE_OUTCOMES:
            return rec
        with self._lock:
            if rec.state not in (DeliveryState.WAITING_PROFILE, DeliveryState.TERMINAL):
                self._transition_unlocked(rec, DeliveryState.WAITING_PROFILE)
            rec.updated_at = time.time()
            self.save()
        return rec

    def claim_delivery(self, path: str | Path, profile_name: str, generation: int,
                       source: str, channel_id: Optional[str] = None,
                       youtube_video_id: Optional[str] = None) -> Tuple[bool, DeliveryRecord, str]:
        """Atomically claim a delivery for enqueue. Returns True exactly once per active claim."""
        c = canonical_path(path)
        size, mtime_ns = self._file_identity(str(path))
        did = self._delivery_id(profile_name, c, size, mtime_ns, channel_id, youtube_video_id)
        with self._lock:
            rec = self._records.get(did)
            path_rec = self._records.get(self._path_index.get(c))
            if rec is None and path_rec is not None:
                rec = path_rec
            now = time.time()
            if rec is not None:
                if rec.state == DeliveryState.TERMINAL:
                    if rec.outcome in TOMBSTONE_OUTCOMES:
                        same_identity = (
                            rec.canonical_path == c
                            and rec.file_size == size
                            and rec.file_mtime_ns == mtime_ns
                        )
                        if rec.delivery_id == did or same_identity:
                            return False, rec, "tombstone"
                        # Path reused by a different file -> treat as a new delivery.
                        rec = None
                    else:
                        # retryable terminal -> reclaim with a fresh record identity
                        rec.delivery_id = did
                        rec.canonical_path = c
                        rec.profile_name = profile_name
                        rec.channel_id = channel_id
                        rec.youtube_video_id = youtube_video_id
                        rec.file_size = size
                        rec.file_mtime_ns = mtime_ns
                        rec.claimed_by = source
                        rec.lifecycle_generation = generation
                        rec.state = DeliveryState.CLAIMED
                        rec.outcome = None
                        rec.post_dispatched = False
                        rec.claimed_at = now
                        rec.updated_at = now
                        rec.error_code = None
                        rec.error_detail = None
                        self._path_index[c] = did
                        self.save()
                        return True, rec, "reclaimed"
                if rec is not None:
                    if rec.state in (DeliveryState.ENQUEUED, DeliveryState.PROCESSING, DeliveryState.POST_DISPATCHED):
                        return False, rec, "already_in_progress"
                    if rec.state == DeliveryState.CLAIMED:
                        return False, rec, "already_claimed"
                    # DISCOVERED / WAITING_PROFILE -> CLAIMED
                    rec.delivery_id = did
                    rec.canonical_path = c
                    rec.profile_name = profile_name
                    rec.channel_id = channel_id
                    rec.youtube_video_id = youtube_video_id
                    rec.file_size = size
                    rec.file_mtime_ns = mtime_ns
                    rec.claimed_by = source
                    rec.lifecycle_generation = generation
                    rec.state = DeliveryState.CLAIMED
                    rec.outcome = None
                    rec.post_dispatched = False
                    rec.claimed_at = now
                    rec.updated_at = now
                    rec.error_code = None
                    rec.error_detail = None
                    self._path_index[c] = did
                    self.save()
                    return True, rec, "claimed"
            rec = DeliveryRecord(
                delivery_id=did,
                canonical_path=c,
                profile_name=profile_name,
                channel_id=channel_id,
                youtube_video_id=youtube_video_id,
                file_size=size,
                file_mtime_ns=mtime_ns,
                claimed_by=source,
                lifecycle_generation=generation,
                state=DeliveryState.CLAIMED,
                outcome=None,
                post_dispatched=False,
                claimed_at=now,
                updated_at=now,
            )
            self._records[did] = rec
            self._path_index[c] = did
            self.save()
            return True, rec, "claimed"

    def _transition_unlocked(self, rec: DeliveryRecord, new_state: DeliveryState) -> bool:
        allowed = _VALID_STATE_TRANSITIONS.get(rec.state, set())
        if new_state not in allowed:
            return False
        rec.state = new_state
        rec.updated_at = time.time()
        return True

    def transition_delivery(self, path: str | Path, new_state: DeliveryState,
                            error_code: Optional[str] = None, error_detail: Optional[str] = None) -> bool:
        with self._lock:
            rec = self.get_delivery(path)
            if rec is None:
                return False
            if not self._transition_unlocked(rec, new_state):
                return False
            if error_code:
                rec.error_code = error_code
            if error_detail is not None:
                rec.error_detail = error_detail
            rec.updated_at = time.time()
            self.save()
            return True

    def release_delivery(self, path: str | Path, error_code: Optional[str] = None,
                         error_detail: Optional[str] = None) -> bool:
        """Release an active claim back to DISCOVERED so it can be re-claimed.
        No-op on terminal/tombstoned records (they must stay terminal)."""
        with self._lock:
            rec = self.get_delivery(path)
            if rec is None:
                return False
            if rec.state == DeliveryState.TERMINAL:
                return True
            if rec.state in (DeliveryState.DISCOVERED, DeliveryState.WAITING_PROFILE):
                return True
            if not self._transition_unlocked(rec, DeliveryState.DISCOVERED):
                return False
            if error_code:
                rec.error_code = error_code
            if error_detail is not None:
                rec.error_detail = error_detail
            rec.updated_at = time.time()
            self.save()
            return True

    def complete_delivery(self, path: str | Path, outcome: DeliveryOutcome,
                          post_dispatched: Optional[bool] = None,
                          error_code: Optional[str] = None, error_detail: Optional[str] = None) -> bool:
        """Move a delivery to TERMINAL with the given outcome. Tombstone when unsafe."""
        with self._lock:
            rec = self.get_delivery(path)
            if rec is None:
                return False
            if rec.state == DeliveryState.TERMINAL:
                if rec.outcome in TOMBSTONE_OUTCOMES:
                    return True
            if rec.state == DeliveryState.POST_DISPATCHED or rec.state in (
                DeliveryState.CLAIMED, DeliveryState.ENQUEUED, DeliveryState.PROCESSING,
            ):
                rec.state = DeliveryState.TERMINAL
                rec.outcome = outcome
                if post_dispatched is not None:
                    rec.post_dispatched = bool(post_dispatched)
                if error_code:
                    rec.error_code = error_code
                if error_detail is not None:
                    rec.error_detail = error_detail
                rec.updated_at = time.time()
                self.save()
                return True
            return False

    def recover_stale_claims(self, ttl_seconds: float = 3600.0,
                             profile_generations: Optional[Dict[str, int]] = None) -> int:
        """Reset stale CLAIMED/ENQUEUED (and generation-stale PROCESSING) records so they
        can be picked up again. Never recovers POST_DISPATCHED. Returns number recovered."""
        recovered = 0
        now = time.time()
        with self._lock:
            for rec in list(self._records.values()):
                if rec.state in (DeliveryState.CLAIMED, DeliveryState.ENQUEUED):
                    stale = (now - rec.updated_at) > ttl_seconds
                    gen_stale = profile_generations is not None and rec.lifecycle_generation != profile_generations.get(rec.profile_name)
                    if stale or gen_stale:
                        if self._transition_unlocked(rec, DeliveryState.DISCOVERED):
                            recovered += 1
                elif rec.state == DeliveryState.PROCESSING and profile_generations is not None:
                    if rec.lifecycle_generation != profile_generations.get(rec.profile_name):
                        if self._transition_unlocked(rec, DeliveryState.DISCOVERED):
                            recovered += 1
            if recovered:
                self.save()
        return recovered

    def is_eligible_for_startup(self, path: str | Path, profile_name: str, generation: int) -> Tuple[bool, str]:
        """Decide whether a file already present in a folder should be enqueued at profile start."""
        with self._lock:
            rec = self.get_delivery(path)
            if rec is None:
                return True, "no_record"
            if rec.state == DeliveryState.TERMINAL:
                if rec.outcome in TOMBSTONE_OUTCOMES:
                    return False, "tombstone"
                return True, "retryable_terminal"
            if rec.state in (DeliveryState.DISCOVERED, DeliveryState.WAITING_PROFILE, DeliveryState.CLAIMED):
                return True, "reclaimable"
            if rec.state in (DeliveryState.ENQUEUED, DeliveryState.PROCESSING, DeliveryState.POST_DISPATCHED):
                if rec.lifecycle_generation == generation:
                    return False, "already_active_current_gen"
                return True, "stale_generation"
            return True, "reclaimable"


# ------------------------------------------------------------------ global singleton

_GLOBAL_DELIVERY_REGISTRY: Optional[DeliveryRegistry] = None
_DELIVERY_REGISTRY_LOCK = threading.Lock()


def get_delivery_registry() -> DeliveryRegistry:
    global _GLOBAL_DELIVERY_REGISTRY
    if _GLOBAL_DELIVERY_REGISTRY is None:
        with _DELIVERY_REGISTRY_LOCK:
            if _GLOBAL_DELIVERY_REGISTRY is None:
                _GLOBAL_DELIVERY_REGISTRY = DeliveryRegistry(ledger_path=LEDGER_DIR)
    return _GLOBAL_DELIVERY_REGISTRY


def configure_delivery_registry(ledger_path: Optional[Path]) -> DeliveryRegistry:
    """Configure the global registry ledger path (idempotent). Call once at startup."""
    global _GLOBAL_DELIVERY_REGISTRY
    with _DELIVERY_REGISTRY_LOCK:
        if _GLOBAL_DELIVERY_REGISTRY is not None:
            if _GLOBAL_DELIVERY_REGISTRY._ledger_path != (Path(ledger_path) if ledger_path else None):
                _GLOBAL_DELIVERY_REGISTRY._ledger_path = Path(ledger_path) if ledger_path else None
                _GLOBAL_DELIVERY_REGISTRY.save()
            return _GLOBAL_DELIVERY_REGISTRY
        _GLOBAL_DELIVERY_REGISTRY = DeliveryRegistry(ledger_path=ledger_path)
        return _GLOBAL_DELIVERY_REGISTRY


# ------------------------------------------------------------------ legacy compatibility shims

def claim_video_path(path: str | Path) -> bool:
    """Back-compat dedupe gate. Routes through the unified registry."""
    ok, _rec, _reason = get_delivery_registry().claim_delivery(path, "", 0, "LEGACY")
    return ok


def is_video_claimed(path: str | Path) -> bool:
    rec = get_delivery_registry().get_delivery(path)
    if rec is None:
        return False
    return rec.state in (DeliveryState.CLAIMED, DeliveryState.ENQUEUED,
                         DeliveryState.PROCESSING, DeliveryState.POST_DISPATCHED)


class WatchToken:
    """Tracks active registration of a folder watch for a specific profile and generation."""
    __slots__ = ("profile_name", "raw_path", "canonical_path", "generation", "watch_handle", "is_active")

    def __init__(self, profile_name: str, raw_path: str, canonical_path: str, generation: int, watch_handle: Any = None):
        self.profile_name = profile_name
        self.raw_path = raw_path
        self.canonical_path = canonical_path
        self.generation = generation
        self.watch_handle = watch_handle
        self.is_active = True

    def invalidate(self):
        self.is_active = False


class SharedVideoFolderHandler(FileSystemEventHandler):
    """Event handler forwarding filesystem creations to SharedWatchdogManager."""

    def __init__(self, manager: "SharedWatchdogManager"):
        super().__init__()
        self.manager = manager

    def on_created(self, event):
        if event.is_directory:
            return
        self.manager.handle_file_event(event.src_path, is_move=False)

    def on_moved(self, event):
        if event.is_directory:
            return
        self.manager.handle_file_event(event.dest_path, is_move=True)


class SharedWatchdogManager:
    """Coordinates folder observation, stability checks, and profile queue dispatch."""

    def __init__(
        self,
        max_stability_workers: int = 3,
        file_stable_delay: float = 1.0,
        enqueue_callback: Optional[Callable[[str, str, int], None]] = None,
    ):
        self.file_stable_delay = file_stable_delay
        self.enqueue_callback = enqueue_callback
        self._lock = threading.Lock()

        self._observer: Optional[Any] = None
        self._handler = SharedVideoFolderHandler(self)

        # Registry mappings: canonical_path -> WatchToken
        self._watches_by_path: Dict[str, WatchToken] = {}
        # Mapping: profile_name -> Set[canonical_path]
        self._watches_by_profile: Dict[str, Set[str]] = {}

        # Bounded stability worker pool
        self._executor = ThreadPoolExecutor(max_workers=max_stability_workers, thread_name_prefix="WatchdogStability")
        self._pending_stability_paths: Set[str] = set()

    def start(self):
        """Start the underlying shared Observer."""
        with self._lock:
            if Observer is None:
                return
            if self._observer is None:
                self._observer = Observer()
                self._observer.start()

    def stop(self):
        """Stop observer and shutdown worker pool."""
        with self._lock:
            if self._observer:
                try:
                    self._observer.stop()
                    self._observer.join(timeout=2.0)
                except Exception:
                    pass
                self._observer = None

            for token in self._watches_by_path.values():
                token.invalidate()
            self._watches_by_path.clear()
            self._watches_by_profile.clear()

        self._executor.shutdown(wait=False)

    def is_observer_alive(self) -> bool:
        with self._lock:
            if self._observer is None:
                return False
            try:
                return self._observer.is_alive()
            except Exception:
                return False

    def restart_observer(self) -> bool:
        """Crash recovery: restart the shared observer and re-schedule active watches."""
        with self._lock:
            if Observer is None:
                return False
            old = self._observer
            self._observer = None
            if old:
                try:
                    old.stop()
                    old.join(timeout=2.0)
                except Exception:
                    pass
            try:
                new_obs = Observer()
                new_obs.start()
            except Exception:
                self._observer = old
                return False
            self._observer = new_obs
            recreated = 0
            for token in list(self._watches_by_path.values()):
                if not token.is_active:
                    continue
                try:
                    token.watch_handle = new_obs.schedule(self._handler, token.raw_path, recursive=False)
                    recreated += 1
                except Exception:
                    token.invalidate()
            return True

    def schedule_folder(self, profile_name: str, folder_path: str, generation: int) -> Tuple[bool, str]:
        """Schedule a folder to be watched by the shared observer for a profile."""
        if not folder_path or not os.path.exists(folder_path):
            return False, "Folder path does not exist"

        c_path = canonical_path(folder_path)
        with self._lock:
            # Check for conflict: folder already owned by a different profile
            existing = self._watches_by_path.get(c_path)
            if existing and existing.is_active and existing.profile_name != profile_name:
                return False, f"Folder already watched by profile '{existing.profile_name}'"

            # Ensure observer running
            if self._observer is None and Observer is not None:
                self._observer = Observer()
                self._observer.start()

            # Schedule watch if not already scheduled
            watch_handle = None
            if self._observer is not None:
                try:
                    if not existing or not existing.watch_handle:
                        watch_handle = self._observer.schedule(self._handler, folder_path, recursive=False)
                    else:
                        watch_handle = existing.watch_handle
                except Exception as e:
                    return False, f"Failed to schedule watch: {e}"

            token = WatchToken(profile_name, folder_path, c_path, generation, watch_handle)
            self._watches_by_path[c_path] = token
            self._watches_by_profile.setdefault(profile_name, set()).add(c_path)
            return True, "OK"

    def unschedule_profile(self, profile_name: str):
        """Unschedule all folders watched by a specific profile."""
        with self._lock:
            paths = self._watches_by_profile.pop(profile_name, set())
            for p in paths:
                token = self._watches_by_path.pop(p, None)
                if token:
                    token.invalidate()
                    if self._observer and token.watch_handle:
                        try:
                            self._observer.unschedule(token.watch_handle)
                        except Exception:
                            pass

    def handle_file_event(self, file_path: str, is_move: bool = False):
        """Handle incoming file creation/move event from Watchdog."""
        try:
            p = Path(file_path)
            ext = p.suffix.lower()
            if ext not in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
                return

            c_folder = canonical_path(p.parent)
            c_file = canonical_path(p)

            with self._lock:
                token = self._watches_by_path.get(c_folder)
                if not token or not token.is_active:
                    return

                profile_name = token.profile_name
                gen = token.generation

                # Deduplicate pending stability checks
                if c_file in self._pending_stability_paths:
                    return
                self._pending_stability_paths.add(c_file)

            # Submit to bounded stability pool
            self._executor.submit(self._stability_worker, file_path, c_file, c_folder, profile_name, gen)
        except Exception:
            pass

    def _stability_worker(self, file_path: str, c_file: str, c_folder: str, profile_name: str, generation: int):
        """Verify file size stability before notifying enqueue callback."""
        try:
            last_size = -1
            stable_count = 0
            for _ in range(10):
                # Verify generation token is still active
                with self._lock:
                    token = self._watches_by_path.get(c_folder)
                    if not token or not token.is_active or token.generation != generation:
                        return

                if not os.path.exists(file_path):
                    return

                try:
                    cur_size = os.path.getsize(file_path)
                except OSError:
                    cur_size = -1

                if cur_size > 0 and cur_size == last_size:
                    stable_count += 1
                    if stable_count >= 2:
                        break
                else:
                    stable_count = 0
                    last_size = cur_size

                time.sleep(self.file_stable_delay)

            # Final generation check before enqueue
            with self._lock:
                token = self._watches_by_path.get(c_folder)
                if not token or not token.is_active or token.generation != generation:
                    return

            # The enqueue callback (coordinator) performs the atomic claim + dedupe,
            # so we must not claim here to avoid a double-claim deadlock.
            if self.enqueue_callback:
                self.enqueue_callback(profile_name, file_path, generation)
        finally:
            with self._lock:
                self._pending_stability_paths.discard(c_file)


# Global singleton instance
_GLOBAL_WATCHDOG_MANAGER: Optional[SharedWatchdogManager] = None
_WATCHDOG_LOCK = threading.Lock()


def get_watchdog_manager() -> SharedWatchdogManager:
    """Access global SharedWatchdogManager singleton."""
    global _GLOBAL_WATCHDOG_MANAGER
    if _GLOBAL_WATCHDOG_MANAGER is None:
        with _WATCHDOG_LOCK:
            if _GLOBAL_WATCHDOG_MANAGER is None:
                _GLOBAL_WATCHDOG_MANAGER = SharedWatchdogManager()
    return _GLOBAL_WATCHDOG_MANAGER