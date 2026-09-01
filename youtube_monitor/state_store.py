import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


TERMINAL_STATES = {"BASELINE_IGNORED", "DOWNLOADED", "SKIPPED_PERMANENT", "TERMINAL"}
RECOVERABLE_STATES = {"DISCOVERED", "QUEUED_DOWNLOAD", "DOWNLOADING", "RETRY_WAIT"}


class VideoStateStore:
    """Durable, atomic discovery/retry state for the YouTube monitor."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitor_sessions (
                    session_id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    stopped_at REAL
                );
                CREATE TABLE IF NOT EXISTS video_states (
                    channel_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    published_at REAL,
                    first_session_id TEXT NOT NULL,
                    session_started_at REAL NOT NULL,
                    detected_at REAL NOT NULL,
                    detection_source TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_retry_at REAL,
                    published_iso TEXT,
                    detected_iso TEXT,
                    last_error_class TEXT,
                    last_error_detail TEXT,
                    final_path TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(channel_id, video_id)
                );
                CREATE INDEX IF NOT EXISTS idx_video_states_recovery
                ON video_states(state, next_retry_at);
                """
            )

    def begin_session(self, started_at=None):
        started_at = float(started_at if started_at is not None else time.time())
        session_id = uuid.uuid4().hex
        with self._lock, self._connection() as conn:
            conn.execute(
                "INSERT INTO monitor_sessions(session_id, started_at) VALUES(?, ?)",
                (session_id, started_at),
            )
        return session_id, started_at

    def end_session(self, session_id, stopped_at=None):
        if not session_id:
            return
        with self._lock, self._connection() as conn:
            conn.execute(
                "UPDATE monitor_sessions SET stopped_at=? WHERE session_id=?",
                (float(stopped_at if stopped_at is not None else time.time()), session_id),
            )

    def register_detection(self, channel_id, video_id, published_at, session_id,
                           session_started_at, source, published_iso="", detected_iso="",
                           baseline=False):
        now = time.time()
        # Session start time is diagnostic data, not a freshness boundary. A video
        # published while the app was closed must remain recoverable after restart.
        # Only the explicit first-time channel seed is allowed to create a baseline.
        state = "BASELINE_IGNORED" if baseline else "DISCOVERED"
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT state, detection_source FROM video_states WHERE channel_id=? AND video_id=?",
                (channel_id, video_id),
            ).fetchone()
            if row:
                sources = {part for part in str(row["detection_source"] or "").split("+") if part}
                sources.add(str(source or "UNKNOWN"))
                conn.execute(
                    "UPDATE video_states SET detection_source=?, updated_at=? WHERE channel_id=? AND video_id=?",
                    ("+".join(sorted(sources)), now, channel_id, video_id),
                )
                return False, row["state"]
            conn.execute(
                """INSERT INTO video_states(
                    channel_id, video_id, published_at, first_session_id, session_started_at,
                    detected_at, detection_source, state, published_iso, detected_iso, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (channel_id, video_id, published_at, session_id, session_started_at,
                 now, source, state, published_iso, detected_iso, now),
            )
            return state == "DISCOVERED", state

    def transition(self, channel_id, video_id, state, **fields):
        allowed = {
            "attempts", "next_retry_at", "last_error_class", "last_error_detail",
            "final_path", "published_iso", "detected_iso",
        }
        values = {k: v for k, v in fields.items() if k in allowed}
        values["state"] = state
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{key}=?" for key in values)
        params = list(values.values()) + [channel_id, video_id]
        with self._lock, self._connection() as conn:
            conn.execute(
                f"UPDATE video_states SET {assignments} WHERE channel_id=? AND video_id=?",
                params,
            )

    def recoverable(self, now=None, include_future=False):
        now = float(now if now is not None else time.time())
        placeholders = ",".join("?" for _ in RECOVERABLE_STATES)
        with self._lock, self._connection() as conn:
            due_clause = "" if include_future else "AND (next_retry_at IS NULL OR next_retry_at<=?)"
            params = (*sorted(RECOVERABLE_STATES),) if include_future else (*sorted(RECOVERABLE_STATES), now)
            rows = conn.execute(
                f"""SELECT * FROM video_states
                    WHERE state IN ({placeholders}) {due_clause}
                    ORDER BY detected_at""",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def counts(self):
        with self._lock, self._connection() as conn:
            return {
                row["state"]: row["n"]
                for row in conn.execute("SELECT state, COUNT(*) AS n FROM video_states GROUP BY state")
            }
