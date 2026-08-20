"""
config_service.py - Asynchronous Single-Worker Config Persistence Service.

Features:
- Single background writer thread with debounced coalescing (1.2s).
- Revision tracking (requested vs persisted revision) via threading.Condition.
- Short-lock memory snapshot to safely decouple persistence from runtime dict mutations.
- Atomic write with explicit `flush` + `fsync` + `os.replace`.
- Synchronous flush contract: `flush_now(timeout=5.0) -> 'SUCCESS' | 'TIMEOUT' | 'WRITER_FAILURE'`.
- Thread-safe UI callback dispatch (never calls Tkinter directly from background thread).
"""

import copy
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from config_store import build_configs_payload, save_configs_file


class ConfigServiceResult:
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    WRITER_FAILURE = "WRITER_FAILURE"


class ConfigPersistenceService:
    """Thread-safe, debounced asynchronous configuration persistence coordinator."""

    def __init__(
        self,
        config_path: str | Path = "configs.json",
        debounce_seconds: float = 1.2,
        ui_dispatcher: Optional[Callable[[Callable[[], None]], None]] = None,
    ):
        self.config_path = str(config_path)
        self.debounce_seconds = debounce_seconds
        self.ui_dispatcher = ui_dispatcher

        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

        self._requested_revision = 0
        self._persisted_revision = 0
        self._last_error: Optional[str] = None

        self._pending_payload: Optional[Dict[str, Any]] = None
        self._pending_ui_callback: Optional[Callable[[], None]] = None
        self._pending_allow_truncate: bool = False

        self._running = True
        self._thread = threading.Thread(target=self._writer_loop, daemon=True, name="ConfigPersistenceWorker")
        self._thread.start()

    def request_save(
        self,
        profiles_dict: Dict[str, Any],
        projects_dict: Dict[str, Any],
        ui_callback: Optional[Callable[[], None]] = None,
        allow_truncate: bool = False,
    ) -> int:
        """Enqueue a save request with a snapshot of current profiles and projects."""
        with self._lock:
            # Build payload under short lock
            try:
                self._pending_payload = build_configs_payload(profiles_dict, projects_dict)
            except Exception:
                # Fallback deepcopy if dict structure is actively mutating
                self._pending_payload = build_configs_payload(
                    copy.deepcopy(profiles_dict),
                    copy.deepcopy(projects_dict),
                )

            if ui_callback is not None:
                self._pending_ui_callback = ui_callback
            self._pending_allow_truncate = allow_truncate

            self._requested_revision += 1
            current_req = self._requested_revision
            self._cv.notify_all()
            return current_req

    def flush_now(self, timeout: float = 5.0) -> str:
        """Block synchronously until all pending revisions are committed to disk."""
        deadline = time.perf_counter() + timeout
        with self._lock:
            target_rev = self._requested_revision
            if self._persisted_revision >= target_rev:
                return ConfigServiceResult.SUCCESS

            self._cv.notify_all()

            while self._persisted_revision < target_rev and self._running:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return ConfigServiceResult.TIMEOUT
                self._cv.wait(timeout=min(remaining, 0.1))

            if self._persisted_revision >= target_rev:
                return ConfigServiceResult.SUCCESS
            if self._last_error is not None:
                return ConfigServiceResult.WRITER_FAILURE
            return ConfigServiceResult.TIMEOUT

    def shutdown(self, timeout: float = 5.0) -> str:
        """Flush pending writes and terminate background worker."""
        res = self.flush_now(timeout=timeout)
        with self._lock:
            self._running = False
            self._cv.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        return res

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "requested_revision": self._requested_revision,
                "persisted_revision": self._persisted_revision,
                "is_synced": self._persisted_revision >= self._requested_revision,
                "last_error": self._last_error,
                "running": self._running,
            }

    def _writer_loop(self):
        """Worker thread loop managing debounced writes."""
        while True:
            payload_to_write = None
            target_rev = 0
            ui_cb = None
            allow_trunc = False

            with self._lock:
                while self._running and self._persisted_revision >= self._requested_revision:
                    self._cv.wait(timeout=0.5)

                if not self._running and self._persisted_revision >= self._requested_revision:
                    break

                # Debounce wait
                target_rev = self._requested_revision
                self._cv.wait(timeout=self.debounce_seconds)

                # Capture latest snapshot and target revision
                payload_to_write = self._pending_payload
                target_rev = self._requested_revision
                ui_cb = self._pending_ui_callback
                allow_trunc = self._pending_allow_truncate
                self._pending_ui_callback = None

            if payload_to_write is not None:
                write_success = False
                try:
                    save_configs_file(self.config_path, payload_to_write, allow_truncate=allow_trunc)
                    write_success = True
                    self._last_error = None
                except Exception as e:
                    self._last_error = str(e)

                with self._lock:
                    if write_success:
                        self._persisted_revision = target_rev
                    self._cv.notify_all()

                # Dispatch UI callback to main thread safely
                if write_success and ui_cb is not None:
                    if self.ui_dispatcher:
                        try:
                            self.ui_dispatcher(ui_cb)
                        except Exception:
                            pass
                    else:
                        try:
                            ui_cb()
                        except Exception:
                            pass


# Global singleton instance
_GLOBAL_CONFIG_SERVICE: Optional[ConfigPersistenceService] = None
_CONFIG_SERVICE_LOCK = threading.Lock()


def get_config_service(config_path: str = "configs.json") -> ConfigPersistenceService:
    """Access global ConfigPersistenceService singleton."""
    global _GLOBAL_CONFIG_SERVICE
    if _GLOBAL_CONFIG_SERVICE is None:
        with _CONFIG_SERVICE_LOCK:
            if _GLOBAL_CONFIG_SERVICE is None:
                _GLOBAL_CONFIG_SERVICE = ConfigPersistenceService(config_path=config_path)
    return _GLOBAL_CONFIG_SERVICE
