"""Safe quarantine of owned Patchright browser profiles for rollback.

A full browser reset moves the current owned profile into a per-account
quarantine directory. Only the most recent quarantine per account is kept and
it expires after a retention window (default 7 days). Restore moves it back.

Safety rules mirror ``browser_maintenance`` / ``patchright_profile_migration``:
no symlinks/reparse points, no filesystem roots, ownership marker must match
the account, and paths must stay strictly inside the managed root.
"""

import json
import os
import shutil
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_RETAIN_DAYS = 7
QUARANTINE_DIR_NAME = "BrowserQuarantine"
QUARANTINE_PREFIX = "Profile-Patchright-"
MANIFEST_NAME = "quarantine.json"
PATCHRIGHT_OWNERSHIP_MARKER = ".patchright-profile-owned"
MARKER_FORMAT = "patchright-profile-v1"


def _is_reparse(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _canonical(path, *, must_exist=False):
    candidate = Path(path).expanduser().absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        if current.exists() or current.is_symlink():
            if _is_reparse(current):
                raise ValueError("Symlink or reparse paths are not allowed: {}".format(current))
    try:
        return candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError("Cannot resolve path {}".format(candidate)) from exc


def _strict_child(path, parent):
    try:
        return path != parent and os.path.commonpath((str(path), str(parent))) == str(parent)
    except ValueError:
        return False


def _write_json_atomic(path, payload):
    descriptor, temporary_name = tempfile.mkstemp(prefix=".quarantine-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def quarantine_base(profile_path):
    """Directory that holds quarantined profiles for the owning account."""
    profile = _canonical(profile_path)
    if profile == Path(profile.anchor):
        raise ValueError("A filesystem root cannot be quarantined")
    return profile.parent / QUARANTINE_DIR_NAME


def restore_target(quarantine_dir):
    """The fixed original location a quarantine should be restored to."""
    quarantine = _canonical(quarantine_dir)
    if quarantine.name.startswith(QUARANTINE_PREFIX):
        return quarantine.parent.parent / "Profile-Patchright"
    raise ValueError("Quarantine directory name is invalid: {}".format(quarantine.name))


def _load_marker(profile):
    marker = profile / PATCHRIGHT_OWNERSHIP_MARKER
    if not marker.exists() or _is_reparse(marker) or not marker.is_file():
        raise ValueError("Valid Patchright ownership marker is required")
    try:
        marker_data = json.loads(marker.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Patchright ownership marker is invalid") from exc
    if not isinstance(marker_data, dict) or marker_data.get("format") != MARKER_FORMAT:
        raise ValueError("Patchright ownership marker format is invalid")
    return marker_data


def _validate_owned_profile(profile):
    profile = _canonical(profile, must_exist=True)
    if not profile.is_dir() or profile.name != "Profile-Patchright":
        raise ValueError("Invalid Patchright profile directory: {}".format(profile))
    marker_data = _load_marker(profile)
    marker_profile = marker_data.get("patchright_profile")
    if not marker_profile or _canonical(marker_profile) != profile:
        raise ValueError("Patchright ownership marker path does not match")
    return profile, marker_data


def _validate_quarantined_profile(quarantine):
    """Validate a quarantined (renamed) owned profile directory."""
    quarantine = _canonical(quarantine, must_exist=True)
    if not quarantine.is_dir() or not quarantine.name.startswith(QUARANTINE_PREFIX):
        raise ValueError("Invalid quarantined profile directory: {}".format(quarantine))
    marker_data = _load_marker(quarantine)
    return quarantine, marker_data


def quarantine_profile(
    profile_path,
    *,
    account_uuid="",
    profile_name="",
    proxy_environment=None,
    retain_days=DEFAULT_RETAIN_DAYS,
    now=None,
):
    """Move an owned profile into quarantine. Returns (quarantine_dir, manifest)."""
    profile, marker = _validate_owned_profile(profile_path)
    marker_account = marker.get("account_id") or ""
    if account_uuid and marker_account and str(marker_account) != str(account_uuid):
        raise ValueError("Owned profile belongs to a different account")
    if retain_days < 1:
        raise ValueError("retain_days must be >= 1")

    base = quarantine_base(profile)
    if base.exists() and _is_reparse(base):
        raise ValueError("Quarantine base is a reparse point or symlink")
    base.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    stamp = timestamp.replace(":", "").replace("+00:00", "Z").replace("-", "").replace(".", "")
    destination = base / (QUARANTINE_PREFIX + stamp[:15])
    if destination.exists():
        raise ValueError("Quarantine destination already exists: {}".format(destination))

    os.replace(str(profile), str(destination))
    try:
        manifest = {
            "account_uuid": str(account_uuid or marker_account or ""),
            "profile_name": str(profile_name or ""),
            "original_path": str(profile),
            "quarantine_path": str(destination),
            "created_at": timestamp,
            "expires_at": (datetime.fromisoformat(timestamp) + timedelta(days=retain_days)).isoformat(),
            "retain_days": int(retain_days),
            "proxy_environment": proxy_environment or {},
        }
        _write_json_atomic(destination / MANIFEST_NAME, manifest)
    except Exception:
        try:
            os.replace(str(destination), str(profile))
        except OSError:
            pass
        raise
    return destination, manifest


def read_quarantine_manifest(quarantine_dir):
    """Return the manifest of a valid quarantine directory."""
    quarantine = _canonical(quarantine_dir)
    manifest_path = quarantine / MANIFEST_NAME
    if _is_reparse(manifest_path) or not manifest_path.is_file():
        raise ValueError("Quarantine manifest is missing")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Quarantine manifest is invalid") from exc
    if not isinstance(data, dict):
        raise ValueError("Quarantine manifest must be a JSON object")
    manifest_quarantine = data.get("quarantine_path")
    if not manifest_quarantine or _canonical(manifest_quarantine) != quarantine:
        raise ValueError("Quarantine manifest path does not match")
    return data


def list_quarantines(profile_path):
    """Return valid quarantine manifests for an account, newest first."""
    try:
        base = quarantine_base(profile_path)
    except ValueError:
        return []
    if not base.is_dir():
        return []
    found = []
    for child in base.iterdir():
        if not child.name.startswith(QUARANTINE_PREFIX):
            continue
        if not child.is_dir() or _is_reparse(child):
            continue
        try:
            manifest = read_quarantine_manifest(child)
            manifest["_path"] = str(child)
            found.append(manifest)
        except ValueError:
            continue
    found.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return found


def latest_quarantine(profile_path):
    """Most recent valid quarantine manifest or None."""
    found = list_quarantines(profile_path)
    return found[0] if found else None


def cleanup_quarantines(profile_path, *, now=None, retain_days=DEFAULT_RETAIN_DAYS):
    """Remove expired quarantines and keep only the latest per account."""
    if retain_days < 1:
        raise ValueError("retain_days must be >= 1")
    try:
        base = quarantine_base(profile_path)
    except ValueError:
        return []
    if not base.is_dir():
        return []
    removed = []
    manifests = list_quarantines(profile_path)
    current = datetime.now(timezone.utc) if now is None else now

    for index, manifest in enumerate(manifests):
        path = Path(manifest["_path"])
        expires_at = manifest.get("expires_at") or ""
        expired = bool(expires_at) and expires_at < current.isoformat()
        keep_latest = index == 0 and not expired
        if keep_latest:
            continue
        try:
            shutil.rmtree(str(path))
            removed.append(str(path))
        except OSError:
            pass
    try:
        if not any(base.iterdir()):
            base.rmdir()
    except OSError:
        pass
    return removed


def restore_quarantine(quarantine_dir):
    """Move a quarantine back to its original location. Returns the manifest."""
    quarantine = _canonical(quarantine_dir, must_exist=True)
    manifest = read_quarantine_manifest(quarantine)
    target = restore_target(quarantine)
    if manifest.get("original_path") != str(target):
        raise ValueError("Quarantine manifest original path does not match the fixed sibling")
    if target.exists():
        raise ValueError("Target profile already exists: {}".format(target))

    # Validate the quarantined profile is still owned.
    _validate_quarantined_profile(quarantine)

    os.replace(str(quarantine), str(target))
    try:
        restored = manifest
        restored["quarantine_path"] = ""
        _write_json_atomic(target / MANIFEST_NAME, restored)
    except Exception:
        try:
            os.replace(str(target), str(quarantine))
        except OSError:
            pass
        raise
    return restored
