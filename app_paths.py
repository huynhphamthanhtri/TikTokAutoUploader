"""Writable per-user paths for operational application data."""

import os
from pathlib import Path


APP_DATA_DIRNAME = "TikTokAutoUploader"


def writable_app_data_root(create=True):
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        root = Path(base) / APP_DATA_DIRNAME
    else:
        root = Path.home() / ".tiktok_auto_uploader"
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def inspection_database_path(create_parent=True):
    parent = writable_app_data_root(create=create_parent) / "data"
    if create_parent:
        parent.mkdir(parents=True, exist_ok=True)
    return parent / "inspection.db"
