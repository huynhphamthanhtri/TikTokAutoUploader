"""
scripts/browser_engine_patcher.py - Safe, idempotent patching of Dong Lao browser engines.

This module is a maintenance/release tool only. It is NEVER imported by the
application and is NEVER executed by CI during a release. The workflow only
*verifies* the already-built artifact.

Safety rules enforced here:

* All patching happens on a staging copy, never on the source engine directory.
* A binary is only modified when its bytes at ``LICENSE_PATCH_OFFSET`` exactly
  match ``LICENSE_EXPECTED_BYTES`` (or are already the NOP sled).
* A ``.original_backup`` file is created next to each patched binary (once) so
  the change is reversible.
* Files are written atomically (temp file + ``os.replace``).
* Branding replacements only swap UTF-16LE strings with the exact same byte
  length, so PE section layout is preserved.
"""

import argparse
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

LICENSE_PATCH_OFFSET = 0x58BCD0C
LICENSE_EXPECTED_BYTES = bytes((0x0F, 0x84, 0x1E, 0x04, 0x00, 0x00))
LICENSE_NOPPED_BYTES = b"\x90" * 6
MIN_DLL_SIZE = 200 * 1024 * 1024
MIN_EXE_SIZE = 1024 * 1024

BRANDING_PAIRS = (
    ("@huynhthang", "DONGLAO-APP"),
    ("HT Browser", "DONGLAO144"),
)

BACKUP_SUFFIX = ".original_backup"
EXCLUDED_SUFFIXES = (BACKUP_SUFFIX,)
EXCLUDED_NAMES = ("chrome_icon_test.exe", "debug.log")


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def read_at(path: Path, offset: int, length: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(length)


def _write_with_backup(path: Path, data: bytes) -> Path:
    """Write ``data`` to ``path`` atomically, backing up the original once."""
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copyfile(path, backup)
    tmp = path.with_name(path.name + ".tmp.patch")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return backup


def ensure_staging_outside(source_dir: Path, staging_dir: Path) -> None:
    """Raise unless ``staging_dir`` is outside ``source_dir``."""
    source_dir = Path(source_dir).resolve()
    staging_dir = Path(staging_dir).resolve()
    if staging_dir == source_dir or source_dir in staging_dir.parents:
        raise ValueError(
            f"staging directory must be outside the source directory: {staging_dir}"
        )


def make_staging_copy(
    source_dir: Path,
    staging_dir: Path,
    excluded_suffixes: Optional[List[str]] = None,
    excluded_names: Optional[List[str]] = None,
) -> Path:
    """Copy ``source_dir`` into ``staging_dir`` (never inside the source).

    The staging directory must be outside the source directory so a tool bug
    can never clobber the pristine engine.
    """
    source_dir = Path(source_dir).resolve()
    staging_dir = Path(staging_dir).resolve()
    ensure_staging_outside(source_dir, staging_dir)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    suffixes = list(excluded_suffixes) if excluded_suffixes is not None else list(EXCLUDED_SUFFIXES)
    names = list(excluded_names) if excluded_names is not None else list(EXCLUDED_NAMES)
    ignore = shutil.ignore_patterns(*[f"*{s}" for s in suffixes], *names)
    shutil.copytree(source_dir, staging_dir, ignore=ignore)
    return staging_dir


def patch_license_nops(dll_path: Path, min_size: int = MIN_DLL_SIZE) -> Dict:
    """Replace the license/HMAC check branch with a 6-byte NOP sled.

    Returns a report dict with ``state`` in {'patched', 'already'}. Raises
    ``ValueError`` when the offset bytes are unexpected, leaving the file
    untouched.
    """
    dll_path = Path(dll_path)
    if not dll_path.exists():
        raise FileNotFoundError(f"chrome.dll not found: {dll_path}")
    size = dll_path.stat().st_size
    if size < min_size:
        raise ValueError(f"chrome.dll too small to be a real engine binary: {size} bytes")

    curr = read_at(dll_path, LICENSE_PATCH_OFFSET, len(LICENSE_NOPPED_BYTES))
    if curr == LICENSE_NOPPED_BYTES:
        return {"file": str(dll_path), "state": "already", "offset_bytes": curr.hex(" ").upper()}
    if curr != LICENSE_EXPECTED_BYTES:
        raise ValueError(
            f"Unexpected bytes at 0x{LICENSE_PATCH_OFFSET:X}: {curr.hex(' ').upper()} "
            f"(expected {LICENSE_EXPECTED_BYTES.hex(' ').upper()})"
        )

    with open(dll_path, "rb") as f:
        data = bytearray(f.read())
    data[LICENSE_PATCH_OFFSET:LICENSE_PATCH_OFFSET + len(LICENSE_NOPPED_BYTES)] = LICENSE_NOPPED_BYTES
    backup = _write_with_backup(dll_path, bytes(data))
    return {
        "file": str(dll_path),
        "state": "patched",
        "offset_bytes": LICENSE_NOPPED_BYTES.hex(" ").upper(),
        "backup": str(backup),
    }


def verify_license_nops(dll_path: Path) -> bool:
    if not dll_path.exists():
        return False
    return read_at(dll_path, LICENSE_PATCH_OFFSET, len(LICENSE_NOPPED_BYTES)) == LICENSE_NOPPED_BYTES


def patch_branding(file_path: Path) -> Dict:
    """Replace same-length UTF-16LE branding strings in a binary.

    Reports per-pair occurrence counts. The file is written only when at least
    one replacement happened. A missing source string is reported, not silently
    treated as success.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"binary not found: {file_path}")

    with open(file_path, "rb") as f:
        data = bytearray(f.read())

    pairs = []
    changed = False
    for src, dst in BRANDING_PAIRS:
        src_bytes = src.encode("utf-16le")
        dst_bytes = dst.encode("utf-16le")
        if len(src_bytes) != len(dst_bytes):
            raise ValueError(f"Branding pair length mismatch: {src!r} -> {dst!r}")
        count = data.count(src_bytes)
        if count:
            data = bytearray(data.replace(src_bytes, dst_bytes))
            changed = True
        pairs.append({"source": src, "dest": dst, "count": count})

    backup = None
    if changed:
        backup = _write_with_backup(file_path, bytes(data))
    return {
        "file": str(file_path),
        "applied": changed,
        "pairs": pairs,
        "backup": str(backup) if backup else None,
    }


def verify_branding(file_path: Path) -> List[Dict]:
    file_path = Path(file_path)
    if not file_path.exists():
        return []
    data = file_path.read_bytes()
    result = []
    for src, _dst in BRANDING_PAIRS:
        result.append({"source": src, "count": data.count(src.encode("utf-16le"))})
    return result


def _assert_within(path: Path, root: Path) -> None:
    path = Path(path).resolve()
    root = Path(root).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError(f"{path} is outside the allowed root {root}")


def patch_engine_dir(
    engine_dir: Path,
    allowed_root: Path,
    *,
    patch_license: bool = True,
    do_branding: bool = True,
    min_dll_size: int = MIN_DLL_SIZE,
) -> Dict:
    """Patch one engine directory under ``allowed_root`` (a staging root).

    Refuses to touch anything outside ``allowed_root``, so the pristine
    ``Browser/`` tree in the workspace can never be modified.
    """
    engine_dir = Path(engine_dir)
    _assert_within(engine_dir, allowed_root)

    report = {"engine": str(engine_dir)}
    chrome_exe = engine_dir / "chrome.exe"
    chrome_dll = engine_dir / "144.0.7559.96" / "chrome.dll"

    if patch_license:
        report["license"] = patch_license_nops(chrome_dll, min_size=min_dll_size)
    if do_branding:
        report["branding"] = {
            "chrome.exe": patch_branding(chrome_exe) if chrome_exe.exists() else None,
            "chrome.dll": patch_branding(chrome_dll) if chrome_dll.exists() else None,
        }
    return report


def parse_common_args(argv, description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--source", required=True, help="source Browser root or engine dir")
    parser.add_argument("--staging", required=True, help="staging root OUTSIDE the source")
    return parser


def _verify_engine_report(report: Dict) -> List[str]:
    problems = []
    license_state = report.get("license", {}).get("state")
    if license_state not in ("patched", "already"):
        problems.append("license NOP patch missing")
    for which, item in (report.get("branding") or {}).items():
        if item is None:
            continue
        if not item.get("applied"):
            missing = [p["source"] for p in item["pairs"] if p["count"] == 0]
            problems.append(f"{which}: no source branding strings found ({missing})")
    return problems