"""Single-flight browser preparation coordinator for the prewarm fast path.

The coordinator ensures that, per TikTok profile + lifecycle generation, at most
one browser-open/prepare operation runs at a time. When a WebSub intent fires,
``submit`` schedules a background prepare; the upload path then calls
``await_result`` so it reuses the prepared session instead of opening a second
one.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

REQUESTED = "REQUESTED"
OPENING = "OPENING"
VERIFYING = "VERIFYING"
READY = "READY"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
IDLE = "IDLE"


class _Entry:
    __slots__ = ("profile_name", "generation", "state", "future", "error", "token", "cancel")

    def __init__(self, profile_name, generation):
        self.profile_name = profile_name
        self.generation = generation
        self.state = REQUESTED
        self.future = None
        self.error = ""
        self.token = None
        self.cancel = threading.Event()


class UploadPreparationCoordinator:
    """Tracks at most one in-flight preparation per (profile, generation)."""

    def __init__(self, max_workers=2):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="browser_prep"
        )
        self._lock = threading.Lock()
        self._entries = {}

    def submit(self, profile_name, generation, run_fn):
        """Schedule ``run_fn(profile_name, generation, cancel_event)``.

        ``run_fn`` must return a session token on success (after registering and
        publishing it) and raise on failure. If a preparation is already
        in-flight for the same profile and generation, the existing future is
        returned instead of scheduling a duplicate.

        Returns the Future or None when no work was scheduled.
        """
        with self._lock:
            if self._executor._shutdown:
                return None
            existing = self._entries.get(profile_name)
            if (
                existing is not None
                and existing.generation == generation
                and existing.future is not None
                and not existing.future.done()
            ):
                return existing.future
            entry = _Entry(profile_name, generation)
            future = self._executor.submit(self._worker, entry, run_fn)
            entry.future = future
            self._entries[profile_name] = entry
            return future

    def _worker(self, entry, run_fn):
        with self._lock:
            entry.state = OPENING
        try:
            token = run_fn(entry.profile_name, entry.generation, entry.cancel)
            with self._lock:
                entry.token = token
                entry.state = READY
                entry.error = ""
        except Exception as error:
            with self._lock:
                entry.state = FAILED
                entry.error = str(error)
        return entry

    def await_result(self, profile_name, generation):
        """Block until the in-flight preparation for (profile, generation) ends.

        Returns the session token when the preparation reached READY, otherwise
        None. When no preparation matches, returns None immediately.
        """
        with self._lock:
            entry = self._entries.get(profile_name)
            if entry is None or entry.generation != generation:
                return None
            future = entry.future
        if future is None:
            return None
        try:
            future.result()
        except Exception:
            return None
        with self._lock:
            if entry.state == READY:
                return entry.token
        return None

    def get_state(self, profile_name):
        with self._lock:
            entry = self._entries.get(profile_name)
            if entry is None:
                return IDLE, "", None
            return entry.state, entry.error, entry.generation

    def cancel_profile(self, profile_name):
        with self._lock:
            entry = self._entries.get(profile_name)
            if entry is None:
                return
            entry.cancel.set()

    def shutdown(self):
        with self._lock:
            for entry in self._entries.values():
                entry.cancel.set()
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            if entry.future is not None:
                entry.future.cancel()
        try:
            self._executor.shutdown(wait=False)
        except Exception:
            pass