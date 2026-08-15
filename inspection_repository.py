"""SQLite repository for redacted TikTok capability snapshots."""

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app_paths import inspection_database_path


SCHEMA_VERSION = 1
_FORBIDDEN_KEYS = {
    "authorization",
    "cookie",
    "csrf",
    "device_id",
    "document_number",
    "full_name",
    "id_number",
    "mstoken",
    "password",
    "proxy_password",
    "sessionid",
    "signature",
    "token",
    "verify_url",
    "x-bogus",
}


def _plain(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _assert_redacted(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_KEYS:
                raise ValueError("forbidden snapshot key: {}".format(path + str(key)))
            _assert_redacted(item, path + str(key) + ".")
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_redacted(item, path)


class InspectionRepository:
    def __init__(self, path=None):
        self.path = Path(path or inspection_database_path())

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL").fetchone()
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS collection_run (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_uuid TEXT NOT NULL,
                    profile_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    error_category TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS capability_snapshot (
                    account_uuid TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    state TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    adapter_version INTEGER NOT NULL,
                    schema_hash TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (account_uuid, capability)
                );
                CREATE TABLE IF NOT EXISTS endpoint_observation (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_uuid TEXT NOT NULL,
                    endpoint_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    schema_hash TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_collection_account_time
                    ON collection_run(account_uuid, finished_at DESC);
                CREATE INDEX IF NOT EXISTS idx_observation_endpoint_time
                    ON endpoint_observation(endpoint_id, observed_at DESC);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()
        finally:
            connection.close()

    def save_capabilities(self, account_uuid, profile_name, capabilities, state="SUCCESS"):
        self.initialize()
        account_uuid = str(account_uuid or "").strip()
        if not account_uuid:
            raise ValueError("account_uuid is required")
        checked_at = datetime.now(timezone.utc).isoformat()
        results = tuple(getattr(capabilities, "results", capabilities or ()))
        connection = self._connect()
        try:
            started_at = checked_at
            for result in results:
                payload = _plain(getattr(result, "value", None))
                _assert_redacted(payload)
                payload_json = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                warnings_json = json.dumps(list(getattr(result, "warnings", ()) or ()), ensure_ascii=True)
                capability = str(getattr(result, "capability", "") or "")
                result_state = getattr(getattr(result, "state", ""), "value", getattr(result, "state", ""))
                result_checked_at = str(getattr(result, "checked_at", "") or checked_at)
                adapter_version = int(getattr(result, "adapter_version", 1) or 1)
                schema = str(getattr(result, "schema_hash", "") or "")
                endpoint_id = str(getattr(result, "endpoint_id", "") or "")
                connection.execute(
                    """INSERT OR REPLACE INTO capability_snapshot
                       (account_uuid, capability, state, checked_at, adapter_version,
                        schema_hash, payload_json, warnings_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (account_uuid, capability, str(result_state), result_checked_at,
                     adapter_version, schema, payload_json, warnings_json),
                )
                if endpoint_id:
                    connection.execute(
                        """INSERT INTO endpoint_observation
                           (account_uuid, endpoint_id, state, schema_hash, observed_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (account_uuid, endpoint_id, str(result_state), schema, result_checked_at),
                    )
            connection.execute(
                """INSERT INTO collection_run
                   (account_uuid, profile_name, started_at, finished_at, state)
                   VALUES (?, ?, ?, ?, ?)""",
                (account_uuid, str(profile_name or ""), started_at, checked_at, str(state)),
            )
            connection.commit()
        finally:
            connection.close()

    def latest(self, account_uuid):
        self.initialize()
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT capability, state, checked_at, adapter_version, schema_hash,
                          payload_json, warnings_json
                   FROM capability_snapshot WHERE account_uuid=? ORDER BY capability""",
                (str(account_uuid or ""),),
            ).fetchall()
        finally:
            connection.close()
        return {
            row[0]: {
                "state": row[1],
                "checked_at": row[2],
                "adapter_version": row[3],
                "schema_hash": row[4],
                "payload": json.loads(row[5]),
                "warnings": json.loads(row[6]),
            }
            for row in rows
        }
