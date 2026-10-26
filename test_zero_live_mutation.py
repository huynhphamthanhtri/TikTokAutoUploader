# -*- coding: utf-8 -*-
"""
Verification of Rule 14: Zero Live Data Mutation.
Asserts that channels.json, youtube_config.json, configs.json, and download_index.json
are NEVER mutated during test executions, whether operations use mocked or real save_now
under path redirection.
"""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from youtube_monitor.core import (
    channels_store,
    _store_lock,
    set_channel_active,
    remove_channel,
)

BASE_DIR = Path(__file__).resolve().parent

PROD_FILES = [
    BASE_DIR / "channels.json",
    BASE_DIR / "youtube_config.json",
    BASE_DIR / "configs.json",
    BASE_DIR / "download_index.json",
]


def get_file_fingerprints():
    fps = {}
    for p in PROD_FILES:
        if p.exists():
            stat = p.stat()
            content = p.read_bytes()
            fps[p] = (stat.st_mtime_ns, hashlib.sha256(content).hexdigest())
    return fps


def test_production_files_zero_mutation_under_mocked_operations(monkeypatch, tmp_path):
    initial_fps = get_file_fingerprints()

    # Isolated temporary store
    temp_channels = tmp_path / "temp_channels.json"
    temp_channels.write_text("{}", encoding="utf-8")

    # Quiesce any active autosave thread
    channels_store._autosave_stop.set()
    if channels_store._autosave_thread and channels_store._autosave_thread.is_alive():
        channels_store._autosave_thread.join(timeout=3)
        assert not channels_store._autosave_thread.is_alive()

    with _store_lock:
        orig_channels = copy.deepcopy(channels_store._channels)
        orig_dirty = channels_store._dirty
        orig_revision = channels_store._revision
        orig_path = channels_store.path

        channels_store.path = temp_channels
        channels_store._channels = {}
        channels_store._dirty = False
        channels_store._revision = 0

    mock_save = MagicMock(return_value=True)
    monkeypatch.setattr(channels_store, "save_now", mock_save)

    try:
        # Perform mutations on store
        channels_store._channels["UC_MOCK_TEST"] = {"title": "Mock", "active": True}
        set_channel_active("UC_MOCK_TEST", False)
        remove_channel("UC_MOCK_TEST")
    finally:
        with _store_lock:
            channels_store._channels = orig_channels
            channels_store._dirty = orig_dirty
            channels_store._revision = orig_revision
            channels_store.path = orig_path

    # Verify that production files were strictly unchanged
    final_fps = get_file_fingerprints()
    for p in PROD_FILES:
        if p in initial_fps:
            assert initial_fps[p][0] == final_fps[p][0], f"mtime_ns mutated for {p}"
            assert initial_fps[p][1] == final_fps[p][1], f"sha256 mutated for {p}"


def test_production_files_zero_mutation_under_real_save_now_redirect(tmp_path):
    """Verify that unmocked save_now() writes ONLY to the redirected temp path, never production."""
    initial_fps = get_file_fingerprints()

    temp_channels = tmp_path / "isolated_channels.json"
    temp_channels.write_text("{}", encoding="utf-8")

    # Quiesce autosave
    channels_store._autosave_stop.set()
    if channels_store._autosave_thread and channels_store._autosave_thread.is_alive():
        channels_store._autosave_thread.join(timeout=3)
        assert not channels_store._autosave_thread.is_alive()

    with _store_lock:
        orig_channels = copy.deepcopy(channels_store._channels)
        orig_dirty = channels_store._dirty
        orig_revision = channels_store._revision
        orig_path = channels_store.path

        channels_store.path = temp_channels
        channels_store._channels = {"UC_ISOLATED": {"title": "Isolated Channel", "active": True}}
        channels_store._dirty = True
        channels_store._revision = 1

    try:
        # Real unmocked save_now writing to temp_channels
        ok = channels_store.save_now()
        assert ok is True
        assert temp_channels.exists()
        saved_data = json.loads(temp_channels.read_text(encoding="utf-8"))
        assert "UC_ISOLATED" in saved_data
    finally:
        with _store_lock:
            channels_store._channels = orig_channels
            channels_store._dirty = orig_dirty
            channels_store._revision = orig_revision
            channels_store.path = orig_path

    # Verify production files were completely untouched
    final_fps = get_file_fingerprints()
    for p in PROD_FILES:
        if p in initial_fps:
            assert initial_fps[p][0] == final_fps[p][0], f"mtime_ns mutated for {p}"
            assert initial_fps[p][1] == final_fps[p][1], f"sha256 mutated for {p}"
