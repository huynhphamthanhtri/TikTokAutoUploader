import csv
import json
import os
import threading
from datetime import datetime
from pathlib import Path


def _app_root():
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = _app_root()
ACTIVITY_LOG = APP_ROOT / "activity_log.csv"
DOWNLOAD_INDEX_JSON = APP_ROOT / "download_index.json"
FIELDNAMES = ["time", "type", "video_name", "video_url", "profile", "status", "detail", "file_path"]
_activity_lock = threading.RLock()


def _ensure_activity_log():
    if ACTIVITY_LOG.exists():
        return
    with open(ACTIVITY_LOG, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()


def _clean(value, max_len=2000):
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text[:max_len]


def append_activity(event_type, video_name="", video_url="", profile="", status="", detail="", file_path=""):
    row = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": _clean(event_type, 80),
        "video_name": _clean(video_name, 500),
        "video_url": _clean(video_url, 500),
        "profile": _clean(profile, 160),
        "status": _clean(status, 40),
        "detail": _clean(detail, 1000),
        "file_path": _clean(file_path, 1000),
    }
    with _activity_lock:
        _ensure_activity_log()
        with open(ACTIVITY_LOG, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(row)
    return row


def _row_matches(row, event_type="", status="", profile="", keyword=""):
    event_type = str(event_type or "").strip()
    status = str(status or "").strip()
    profile = str(profile or "").strip()
    keyword = str(keyword or "").strip().lower()
    if event_type and row.get("type") != event_type:
        return False
    if status and row.get("status") != status:
        return False
    if profile and row.get("profile") != profile:
        return False
    if keyword:
        haystack = " ".join(str(row.get(k, "")) for k in FIELDNAMES).lower()
        if keyword not in haystack:
            return False
    return True


def get_activity_logs(limit=500, event_type="", status="", profile="", keyword=""):
    try:
        limit = max(1, int(limit or 500))
    except Exception:
        limit = 500
    if not ACTIVITY_LOG.exists():
        return []
    with _activity_lock:
        with open(ACTIVITY_LOG, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    rows = [row for row in rows if _row_matches(row, event_type, status, profile, keyword)]
    return rows[-limit:]


def get_activity_stats():
    stats = {
        "total": 0,
        "download_success": 0,
        "download_fail": 0,
        "download_skipped": 0,
        "upload_success": 0,
        "upload_fail": 0,
        "batch_skipped": 0,
    }
    for row in get_activity_logs(limit=100000):
        stats["total"] += 1
        event_type = row.get("type", "")
        status = row.get("status", "")
        if event_type == "youtube_download" and status == "success":
            stats["download_success"] += 1
        elif event_type == "youtube_download" and status == "fail":
            stats["download_fail"] += 1
        elif event_type == "youtube_download" and status == "skipped":
            stats["download_skipped"] += 1
        elif event_type == "tiktok_upload" and status == "success":
            stats["upload_success"] += 1
        elif event_type == "tiktok_upload" and status == "fail":
            stats["upload_fail"] += 1
        elif event_type == "batch_find" and status == "skipped":
            stats["batch_skipped"] += 1
    return stats


def clear_activity_log():
    with _activity_lock:
        with open(ACTIVITY_LOG, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
    return True, "Đã xóa lịch sử video."


def get_activity_mtime():
    try:
        return os.path.getmtime(ACTIVITY_LOG)
    except Exception:
        return 0


def _load_download_index():
    try:
        if DOWNLOAD_INDEX_JSON.exists():
            with open(DOWNLOAD_INDEX_JSON, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_download_index(data):
    tmp = DOWNLOAD_INDEX_JSON.with_suffix(DOWNLOAD_INDEX_JSON.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DOWNLOAD_INDEX_JSON)


def remember_download(file_path, video_id, title="", channel_id="", profile=""):
    if not file_path:
        return
    path = os.path.abspath(str(file_path))
    meta = {
        "video_id": str(video_id or ""),
        "video_url": f"https://youtu.be/{video_id}" if video_id else "",
        "title": str(title or ""),
        "channel_id": str(channel_id or ""),
        "profile": str(profile or ""),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _activity_lock:
        data = _load_download_index()
        data[path] = meta
        _save_download_index(data)


def lookup_download(file_path):
    if not file_path:
        return {}
    target = os.path.abspath(str(file_path))
    with _activity_lock:
        data = _load_download_index()
    if target in data and isinstance(data[target], dict):
        return dict(data[target])
    target_name = os.path.basename(target).lower()
    for path, meta in data.items():
        if os.path.basename(str(path)).lower() == target_name and isinstance(meta, dict):
            return dict(meta)
    return {}
