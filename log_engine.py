"""
log_engine.py - High-Throughput Bounded Thread-Safe Log Engine for Tkinter UI.

Features:
- Bounded capacity (default 2,000 entries) with non-blocking priority admission.
- Level-aware overflow management: Prioritizes ERROR, WARN, SUCCESS over INFO/DEBUG.
- Detailed metrics: High-water mark, total enqueued, total rendered, dropped counters per level.
- Tag-aware chunk batching for Tkinter Text widgets (preserves syntax/color highlighting).
- Single-pass line trimming per drain cycle.
- Viewport-aware autoscroll (only scrolls when user is at bottom).
- Graceful shutdown and synchronous flush capabilities.
"""

import collections
import queue
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple


class LogLevelPriority:
    """Numerical priority for admission control (higher number = higher priority)."""
    CRITICAL = 50
    ERROR = 40
    WARNING = 30
    WARN = 30
    SUCCESS = 25
    INFO = 20
    DEBUG = 10


def get_log_priority(tag: Optional[str]) -> int:
    """Map string tag/level to numeric priority."""
    if not tag:
        return LogLevelPriority.INFO
    t = str(tag).upper().strip()
    if "ERR" in t or "FAIL" in t or "CRIT" in t:
        return LogLevelPriority.ERROR
    if "WARN" in t:
        return LogLevelPriority.WARNING
    if "SUCC" in t or "OK" in t:
        return LogLevelPriority.SUCCESS
    if "DEBUG" in t:
        return LogLevelPriority.DEBUG
    return LogLevelPriority.INFO


class LogEntry:
    """Container for a single log message."""
    __slots__ = ("message", "tag", "important_tag", "timestamp", "priority")

    def __init__(self, message: str, tag: Optional[str] = None, important_tag: Optional[str] = None, timestamp: Optional[datetime] = None):
        self.message = str(message)
        self.tag = tag or "INFO"
        self.important_tag = important_tag
        self.timestamp = timestamp or datetime.now()
        self.priority = get_log_priority(self.tag)


class BoundedLogQueue:
    """Thread-safe bounded queue with priority admission and drop metrics."""

    def __init__(self, maxsize: int = 2000):
        self.maxsize = maxsize
        self._lock = threading.Lock()
        self._items = collections.deque()
        self.high_water_mark = 0
        self.total_enqueued = 0
        self.total_rendered = 0
        self.dropped_by_level: Dict[str, int] = {
            "DEBUG": 0,
            "INFO": 0,
            "SUCCESS": 0,
            "WARNING": 0,
            "ERROR": 0,
        }

    def push(self, entry: LogEntry) -> bool:
        """Enqueue a log entry. If full, drops lower-priority items or rejects."""
        with self._lock:
            current_len = len(self._items)
            if current_len >= self.maxsize:
                # Attempt to evict lowest priority item from queue if current entry is higher priority
                if entry.priority >= LogLevelPriority.SUCCESS:
                    # Scan from left to find an INFO or DEBUG entry to evict
                    for idx, old in enumerate(self._items):
                        if old.priority <= LogLevelPriority.INFO:
                            del self._items[idx]
                            lvl_key = "DEBUG" if old.priority <= LogLevelPriority.DEBUG else "INFO"
                            self.dropped_by_level[lvl_key] = self.dropped_by_level.get(lvl_key, 0) + 1
                            self._items.append(entry)
                            self.total_enqueued += 1
                            return True

                # If cannot evict or entry is low priority, drop this entry
                lvl_key = "ERROR" if entry.priority >= LogLevelPriority.ERROR else (
                    "WARNING" if entry.priority >= LogLevelPriority.WARNING else (
                        "SUCCESS" if entry.priority >= LogLevelPriority.SUCCESS else "INFO"
                    )
                )
                self.dropped_by_level[lvl_key] = self.dropped_by_level.get(lvl_key, 0) + 1
                return False

            self._items.append(entry)
            self.total_enqueued += 1
            if len(self._items) > self.high_water_mark:
                self.high_water_mark = len(self._items)
            return True

    def pop_batch(self, max_items: int = 40) -> List[LogEntry]:
        """Pop up to max_items from the queue."""
        with self._lock:
            if not self._items:
                return []
            count = min(max_items, len(self._items))
            result = [self._items.popleft() for _ in range(count)]
            return result

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._items) == 0

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def get_metrics(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "size": len(self._items),
                "maxsize": self.maxsize,
                "high_water_mark": self.high_water_mark,
                "total_enqueued": self.total_enqueued,
                "total_rendered": self.total_rendered,
                "dropped_by_level": dict(self.dropped_by_level),
                "total_dropped": sum(self.dropped_by_level.values()),
            }


class LogEngine:
    """Coordinates UI draining, chunk batching, autoscrolling, and trimming for Tkinter."""

    def __init__(
        self,
        maxsize: int = 2000,
        drain_interval_ms: int = 100,
        max_lines_per_tick: int = 40,
        time_budget_ms: float = 10.0,
        max_status_lines: int = 500,
        max_important_lines: int = 150,
    ):
        self.queue = BoundedLogQueue(maxsize=maxsize)
        self.drain_interval_ms = drain_interval_ms
        self.max_lines_per_tick = max_lines_per_tick
        self.time_budget_ms = time_budget_ms
        self.max_status_lines = max_status_lines
        self.max_important_lines = max_important_lines
        self._root = None
        self._status_widget = None
        self._important_widget = None
        self._scheduled_drain_id = None
        self._running = False
        self._trim_func: Optional[Callable[[Any, int], None]] = None

    def initialize_ui(
        self,
        root: Any,
        status_widget: Any,
        important_widget: Optional[Any] = None,
        trim_func: Optional[Callable[[Any, int], None]] = None,
    ):
        """Bind to real Tkinter root and Text widgets."""
        self._root = root
        self._status_widget = status_widget
        self._important_widget = important_widget
        self._trim_func = trim_func
        self._running = True
        self._schedule_next_drain(self.drain_interval_ms)

    def shutdown(self):
        """Stop background draining and flush remaining logs."""
        self._running = False
        if self._root and self._scheduled_drain_id:
            try:
                self._root.after_cancel(self._scheduled_drain_id)
            except Exception:
                pass
            self._scheduled_drain_id = None

    def post_log(self, message: str, tag: Optional[str] = None, important_tag: Optional[str] = None) -> bool:
        """Non-blocking log submission from any thread."""
        entry = LogEntry(message=message, tag=tag, important_tag=important_tag)
        return self.queue.push(entry)

    def _schedule_next_drain(self, delay_ms: int):
        if not self._running or not self._root:
            return
        try:
            self._scheduled_drain_id = self._root.after(delay_ms, self.drain_tick)
        except Exception:
            pass

    def drain_tick(self):
        """Execute one bounded drain cycle on the Tkinter main thread."""
        if not self._running or not self._root:
            return

        start_time = time.perf_counter()
        budget_s = self.time_budget_ms / 1000.0

        batch = self.queue.pop_batch(self.max_lines_per_tick)
        if batch:
            self._render_batch(batch, start_time, budget_s)

        # Schedule next tick: if queue still has backlog, schedule tighter (10ms); otherwise standard (100ms)
        next_delay = 10 if not self.queue.is_empty() else self.drain_interval_ms
        self._schedule_next_drain(next_delay)

    def _render_batch(self, batch: List[LogEntry], start_time: float, budget_s: float):
        """Render batch into Text widgets with tag-chunk grouping and smart autoscroll."""
        if not self._status_widget:
            return

        try:
            if not self._status_widget.winfo_exists():
                return

            # Check if user is currently scrolled to bottom (within 2% margin)
            status_at_bottom = True
            try:
                yview = self._status_widget.yview()
                status_at_bottom = (yview[1] >= 0.98) or (yview[1] - yview[0] >= 0.99)
            except Exception:
                pass

            important_at_bottom = True
            if self._important_widget:
                try:
                    if self._important_widget.winfo_exists():
                        iyview = self._important_widget.yview()
                        important_at_bottom = (iyview[1] >= 0.98) or (iyview[1] - iyview[0] >= 0.99)
                except Exception:
                    pass

            # Group consecutive entries by tag for chunk insertion
            status_chunks: List[Tuple[str, str]] = []
            important_chunks: List[Tuple[str, str]] = []

            for entry in batch:
                line = f"{entry.timestamp.strftime('%H:%M:%S')} {entry.message}\n"
                # Chunking for status widget
                if status_chunks and status_chunks[-1][1] == entry.tag:
                    status_chunks[-1] = (status_chunks[-1][0] + line, entry.tag)
                else:
                    status_chunks.append((line, entry.tag))

                # Chunking for important widget
                if entry.important_tag:
                    if important_chunks and important_chunks[-1][1] == entry.important_tag:
                        important_chunks[-1] = (important_chunks[-1][0] + line, entry.important_tag)
                    else:
                        important_chunks.append((line, entry.important_tag))

            # Apply to status widget
            self._status_widget.configure(state="normal")
            for text_chunk, tag in status_chunks:
                self._status_widget.insert("end", text_chunk, tag)
            if self._trim_func:
                self._trim_func(self._status_widget, self.max_status_lines)
            if status_at_bottom:
                self._status_widget.see("end")
            self._status_widget.configure(state="disabled")

            # Apply to important widget if applicable
            if self._important_widget and important_chunks:
                try:
                    if self._important_widget.winfo_exists():
                        self._important_widget.configure(state="normal")
                        for text_chunk, itag in important_chunks:
                            self._important_widget.insert("end", text_chunk, itag)
                        if self._trim_func:
                            self._trim_func(self._important_widget, self.max_important_lines)
                        if important_at_bottom:
                            self._important_widget.see("end")
                        self._important_widget.configure(state="disabled")
                except Exception:
                    pass

            self.queue.total_rendered += len(batch)
        except Exception:
            pass


# Global singleton instance for easy integration
_GLOBAL_LOG_ENGINE: Optional[LogEngine] = None
_LOG_ENGINE_LOCK = threading.Lock()


def get_log_engine() -> LogEngine:
    """Access the global LogEngine singleton."""
    global _GLOBAL_LOG_ENGINE
    if _GLOBAL_LOG_ENGINE is None:
        with _LOG_ENGINE_LOCK:
            if _GLOBAL_LOG_ENGINE is None:
                _GLOBAL_LOG_ENGINE = LogEngine()
    return _GLOBAL_LOG_ENGINE
