"""
browser_engine_manager.py - Browser Engine Manager for HT Browser (Orbita 144) & Multi-Brand Engines.

Provides:
- Local engine status and discovery across bundled & custom aliases.
- Remote manifest checking for browser engine updates.
- Streaming chunk-by-chunk download with progress callbacks and cancellation.
- SHA-256 checksum verification.
- Safe atomic zip extraction into the Browser/ directory.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Any

import requests

DEFAULT_ENGINE_NAME = "donglao-browser-144"
FALLBACK_ENGINE_NAME = "orbita-browser-144"


def _app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_browser_root_dir(app_base: Optional[Path] = None) -> Path:
    root = Path(app_base or _app_base_dir())
    b_dir = root / "Browser"
    if b_dir.exists():
        return b_dir
    internal_b_dir = root / "_internal" / "Browser"
    if internal_b_dir.exists():
        return internal_b_dir
    return b_dir


def get_local_engine_info(app_base: Optional[Path] = None) -> Dict[str, Any]:
    """Scan the Browser directory and return status of installed browser engines."""
    b_root = get_browser_root_dir(app_base)
    installed_engines: List[Dict[str, Any]] = []
    has_ht_144 = False
    primary_executable: Optional[str] = None

    candidate_names = [
        "donglao-browser-144",
        "donglao-browser",
        "ht-browser-144",
        "orbita-browser-144",
        "chrome-win64",
        "orbita-browser-123",
    ]

    for name in candidate_names:
        dir_path = b_root / name
        if dir_path.exists() and dir_path.is_dir():
            exe_candidates = [
                dir_path / "chrome.exe",
                dir_path / "htbrowser.exe",
                dir_path / "donglao.exe",
            ]
            found_exe = None
            for exe in exe_candidates:
                if exe.exists() and exe.is_file() and exe.stat().st_size > 0:
                    found_exe = str(exe.resolve())
                    break

            if found_exe:
                total_bytes = sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())
                is_144 = "144" in name or (dir_path / "144.0.7559.96").exists()
                if is_144:
                    has_ht_144 = True
                    if not primary_executable:
                        primary_executable = found_exe

                installed_engines.append({
                    "name": name,
                    "directory": str(dir_path.resolve()),
                    "executable": found_exe,
                    "size_mb": round(total_bytes / (1024 * 1024), 2),
                    "is_antidetect_144": is_144,
                })

    return {
        "browser_root": str(b_root.resolve()),
        "has_ht_144": has_ht_144,
        "primary_executable": primary_executable,
        "installed_engines": installed_engines,
    }


def compute_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def download_file_with_progress(
    url: str,
    dest_path: Path,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    chunk_size: int = 65536,
    timeout: int = 30,
) -> Path:
    """Download a file with streaming chunk progress and cancellation support."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest_path.with_name(f"{dest_path.name}.downloading")
    
    resp = requests.get(url, stream=True, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Download failed with HTTP {resp.status_code}: {resp.reason}")

    total_bytes = int(resp.headers.get("content-length", 0))
    downloaded_bytes = 0
    start_time = time.time()
    last_speed_check = start_time
    last_speed_bytes = 0
    speed_mbps = 0.0

    try:
        with open(temp_dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if cancel_event and cancel_event.is_set():
                    raise RuntimeError("Download cancelled by user.")
                if chunk:
                    f.write(chunk)
                    downloaded_bytes += len(chunk)
                    
                    now = time.time()
                    if now - last_speed_check >= 0.5:
                        duration = now - last_speed_check
                        bytes_diff = downloaded_bytes - last_speed_bytes
                        speed_mbps = (bytes_diff / (1024 * 1024)) / duration if duration > 0 else 0.0
                        last_speed_check = now
                        last_speed_bytes = downloaded_bytes

                    if progress_callback:
                        progress_callback(downloaded_bytes, total_bytes, speed_mbps)

        if temp_dest.exists():
            os.replace(temp_dest, dest_path)
        return dest_path
    except Exception:
        if temp_dest.exists():
            try:
                temp_dest.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def extract_engine_zip_atomic(
    zip_path: Path,
    target_engine_dir: Path,
    expected_sha256: Optional[str] = None,
) -> Path:
    """Safely verify checksum and extract an engine zip archive atomically into target_engine_dir."""
    if not zip_path.exists():
        raise FileNotFoundError(f"Archive not found: {zip_path}")

    if expected_sha256:
        computed = compute_sha256(zip_path)
        if computed != expected_sha256.lower().strip():
            raise ValueError(
                f"Checksum mismatch: expected {expected_sha256.lower().strip()}, got {computed}"
            )

    parent_dir = target_engine_dir.parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    temp_extract = Path(tempfile.mkdtemp(prefix=".engine-extract-", dir=str(parent_dir)))

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                extracted_path = (temp_extract / member.filename).resolve()
                if os.path.commonpath([str(temp_extract.resolve()), str(extracted_path)]) != str(temp_extract.resolve()):
                    raise RuntimeError(f"Unsafe path in zip: {member.filename}")
            zf.extractall(temp_extract)

        extracted_root = temp_extract
        children = list(temp_extract.iterdir())
        if len(children) == 1 and children[0].is_dir():
            single = children[0]
            engine_executables = ("chrome.exe", "htbrowser.exe", "donglao.exe")
            if any((single / exe).exists() for exe in engine_executables):
                extracted_root = single
            else:
                nested = [p for p in single.iterdir() if p.is_dir() and any((p / exe).exists() for exe in engine_executables)]
                if len(nested) == 1:
                    extracted_root = nested[0]

        backup_dir = None
        if target_engine_dir.exists():
            backup_dir = target_engine_dir.with_name(f"{target_engine_dir.name}.old.{int(time.time())}")
            os.replace(target_engine_dir, backup_dir)

        if extracted_root == temp_extract:
            os.replace(temp_extract, target_engine_dir)
        else:
            shutil.move(str(extracted_root), str(target_engine_dir))
            shutil.rmtree(temp_extract, ignore_errors=True)

        if backup_dir and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

        return target_engine_dir
    except Exception:
        if temp_extract.exists():
            shutil.rmtree(temp_extract, ignore_errors=True)
        raise


def check_remote_engine_manifest(manifest_url: str, timeout: int = 15) -> Dict[str, Any]:
    """Fetch remote manifest for browser engine release information."""
    resp = requests.get(manifest_url, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch manifest: HTTP {resp.status_code}")
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid manifest payload format")
    return {
        "version": str(data.get("version", "")).strip(),
        "download_url": str(data.get("download_url", "")).strip(),
        "sha256": str(data.get("sha256", "")).strip().lower(),
        "size_bytes": int(data.get("size_bytes", 0)),
        "release_notes": str(data.get("release_notes", "")).strip(),
    }
