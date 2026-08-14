"""Safe, reportable maintenance for Chrome user-data directories."""

import json
import os
import shutil
import stat
import time
from enum import Enum
from pathlib import Path


class MaintenanceMode(str, Enum):
    QUICK = "quick"
    SESSION = "session"
    FULL = "full"


QUICK = MaintenanceMode.QUICK.value
SESSION = MaintenanceMode.SESSION.value
FULL = MaintenanceMode.FULL.value
MODES = (QUICK, SESSION, FULL)

OWNERSHIP_MARKER = ".browser_maintenance_owned"
_MARKER_CONTENT = "browser-maintenance-owned-v1\n"
_PATCHRIGHT_OWNERSHIP_MARKER = ".patchright-profile-owned"
_PATCHRIGHT_STATE_FILE = ".patchright-migration.json"
_PATCHRIGHT_MARKER_FORMAT = "patchright-profile-v1"
_PATCHRIGHT_STATES = (
    "pending",
    "created",
    "cookies_imported",
    "login_verified",
    "upload_verified",
    "legacy_cleanup_pending",
    "completed",
)
DEFAULT_STALE_LOCK_AGE_SECONDS = 24 * 60 * 60

_ROOT_CACHE_DIRS = (
    "DawnCache",
    "GraphiteDawnCache",
    "GrShaderCache",
    "ShaderCache",
)
_PROFILE_CACHE_DIRS = (
    "Cache",
    "Code Cache",
    "DawnCache",
    "GPUCache",
    "GraphiteDawnCache",
    "GrShaderCache",
    "Media Cache",
    os.path.join("Network", "Cache"),
    os.path.join("Network", "Code Cache"),
)
_SESSION_DIRS = (
    "IndexedDB",
    "Local Storage",
    "Service Worker",
    "Session Storage",
    "WebStorage",
)
_SINGLETON_FILES = ("SingletonCookie", "SingletonLock", "SingletonSocket")


def _is_reparse(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _check_existing_components(path):
    path = Path(path).absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            continue
        if _is_reparse(current):
            raise ValueError("Reparse or symlink path component is not allowed: {}".format(current))


def _canonical(path, must_exist=False):
    candidate = Path(path).expanduser().absolute()
    _check_existing_components(candidate)
    try:
        return candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError("Cannot resolve path {}: {}".format(candidate, exc)) from exc


def _overlaps(first, second):
    try:
        common = Path(os.path.commonpath((str(first), str(second))))
    except ValueError:
        return False
    return common == first or common == second


def validate_configured_profile_roots(profile_roots):
    """Return canonical profile roots, rejecting duplicates and nesting."""
    roots = [_canonical(path) for path in profile_roots or ()]
    for index, root in enumerate(roots):
        for other in roots[index + 1:]:
            if _overlaps(root, other):
                raise ValueError("Configured profile roots overlap: {} and {}".format(root, other))
    return roots


def validate_maintenance_root(root, forbidden_roots=(), configured_profile_roots=()):
    """Validate and return a canonical, existing Chrome user-data root."""
    canonical = _canonical(root, must_exist=True)
    if not canonical.is_dir():
        raise ValueError("Browser user-data root is not a directory: {}".format(canonical))
    if canonical == Path(canonical.anchor):
        raise ValueError("A filesystem root cannot be maintained: {}".format(canonical))

    forbidden = [_canonical(path) for path in forbidden_roots or ()]
    for protected in forbidden:
        if _overlaps(canonical, protected):
            raise ValueError("Browser root overlaps forbidden root: {}".format(protected))

    configured = validate_configured_profile_roots(configured_profile_roots)
    for other in configured:
        if _overlaps(canonical, other):
            raise ValueError("Browser root overlaps configured profile root: {}".format(other))
    return canonical


def validate_target_path(root, target):
    """Return a canonical target only when it is strictly beneath root."""
    canonical_root = _canonical(root, must_exist=True)
    canonical_target = _canonical(target)
    try:
        beneath = os.path.commonpath((str(canonical_root), str(canonical_target))) == str(canonical_root)
    except ValueError:
        beneath = False
    if not beneath or canonical_target == canonical_root:
        raise ValueError("Maintenance target is not beneath browser root: {}".format(target))
    return canonical_target


def create_owned_root(root):
    """Create a root and ownership marker, but never mark a pre-existing directory."""
    candidate = _canonical(root)
    if candidate == Path(candidate.anchor):
        raise ValueError("A filesystem root cannot be owned by this module")
    if candidate.exists():
        if not candidate.is_dir():
            raise ValueError("Ownership target is not a directory: {}".format(candidate))
        return False
    candidate.mkdir(parents=True)
    marker = candidate / OWNERSHIP_MARKER
    try:
        marker.write_text(_MARKER_CONTENT, encoding="ascii")
    except Exception:
        try:
            candidate.rmdir()
        except OSError:
            pass
        raise
    return True


def adopt_legacy_owned_root(root, managed_parent):
    """Mark a legacy tool-created Profile only when it is inside the managed data root."""
    canonical_root = _canonical(root, must_exist=True)
    canonical_parent = _canonical(managed_parent, must_exist=True)
    if canonical_root.name.lower() != "profile":
        raise ValueError("Only a legacy directory named Profile can be adopted")
    try:
        inside = os.path.commonpath((str(canonical_parent), str(canonical_root))) == str(canonical_parent)
    except ValueError:
        inside = False
    if not inside or canonical_root == canonical_parent:
        raise ValueError("Legacy browser root is outside the managed data root")
    marker = validate_target_path(canonical_root, canonical_root / OWNERSHIP_MARKER)
    if marker.exists():
        if marker.read_text(encoding="ascii") != _MARKER_CONTENT:
            raise ValueError("Existing ownership marker is invalid")
        return False
    marker.write_text(_MARKER_CONTENT, encoding="ascii")
    return True


def _new_report(mode, root):
    return {
        "mode": mode,
        "root": str(Path(root).expanduser().absolute()),
        "removed": [],
        "skipped": [],
        "errors": [],
        "success": False,
    }


def _error(report, path, exc):
    report["errors"].append({"path": str(path), "error": "{}: {}".format(type(exc).__name__, exc)})


def _remove(root, target, report):
    try:
        target = validate_target_path(root, target)
        if not target.exists() and not target.is_symlink():
            return
        if _is_reparse(target):
            raise ValueError("Refusing to remove a reparse point or symlink")
        if target.is_dir():
            shutil.rmtree(str(target))
        else:
            target.unlink()
        report["removed"].append(str(target))
    except Exception as exc:
        _error(report, target, exc)


def _profile_roots(root, report):
    profiles = [root]
    try:
        children = list(root.iterdir())
    except Exception as exc:
        _error(report, root, exc)
        return profiles
    for child in children:
        name = child.name
        if name != "Default" and name not in ("Guest Profile", "System Profile") and not name.startswith("Profile "):
            continue
        try:
            validate_target_path(root, child)
            if _is_reparse(child):
                raise ValueError("Profile directory is a reparse point or symlink")
            if child.is_dir():
                profiles.append(child)
        except Exception as exc:
            _error(report, child, exc)
    return profiles


def _quick(root, report, stale_lock_age_seconds, now):
    profiles = _profile_roots(root, report)
    for relative in _ROOT_CACHE_DIRS:
        _remove(root, root / relative, report)
    for profile in profiles:
        for relative in _PROFILE_CACHE_DIRS:
            _remove(root, profile / relative, report)

    cutoff = now - stale_lock_age_seconds
    for name in _SINGLETON_FILES:
        lock = root / name
        try:
            validate_target_path(root, lock)
            if not lock.exists() and not lock.is_symlink():
                continue
            if _is_reparse(lock):
                raise ValueError("Singleton lock is a reparse point or symlink")
            if not lock.is_file():
                report["skipped"].append({"path": str(lock), "reason": "not a regular file"})
            elif lock.stat().st_mtime <= cutoff:
                _remove(root, lock, report)
            else:
                report["skipped"].append({"path": str(lock), "reason": "Singleton lock is not stale"})
        except Exception as exc:
            _error(report, lock, exc)
    return profiles


def _remove_cookie_files(root, profile, report):
    for parent in (profile, profile / "Network"):
        try:
            if parent != root:
                validate_target_path(root, parent)
            if not parent.exists():
                continue
            if _is_reparse(parent):
                raise ValueError("Cookie parent is a reparse point or symlink")
            for child in parent.iterdir():
                if child.name == "Cookies" or child.name.startswith("Cookies-"):
                    _remove(root, child, report)
        except Exception as exc:
            _error(report, parent, exc)


def _session(root, profiles, report):
    for profile in profiles:
        for relative in _SESSION_DIRS:
            _remove(root, profile / relative, report)
        _remove_cookie_files(root, profile, report)


def _patchright_metadata(root):
    if root.name != "Profile-Patchright":
        raise ValueError("Invalid Patchright profile directory")
    marker = root / _PATCHRIGHT_OWNERSHIP_MARKER
    state_file = root / _PATCHRIGHT_STATE_FILE
    for path in (marker, state_file):
        validate_target_path(root, path)
        if _is_reparse(path) or not path.is_file():
            raise ValueError("Valid Patchright ownership and state metadata are required")
    try:
        marker_data = json.loads(marker.read_text(encoding="ascii"))
        state_data = json.loads(state_file.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Patchright profile metadata is invalid") from exc
    if not isinstance(marker_data, dict) or not isinstance(state_data, dict):
        raise ValueError("Patchright profile metadata must contain JSON objects")
    if marker_data.get("format") != _PATCHRIGHT_MARKER_FORMAT:
        raise ValueError("Patchright ownership marker format is invalid")
    if marker_data.get("patchright_profile") != str(root):
        raise ValueError("Patchright ownership marker path does not match canonical root")

    legacy_value = marker_data.get("legacy_profile")
    if not isinstance(legacy_value, str) or not legacy_value:
        raise ValueError("Patchright legacy profile path is invalid")
    legacy = _canonical(legacy_value)
    if str(legacy) != legacy_value or legacy.name.casefold() != "profile":
        raise ValueError("Patchright legacy profile path is not canonical")
    if legacy.with_name("Profile-Patchright") != root:
        raise ValueError("Patchright profile is not the fixed legacy sibling")
    if state_data.get("legacy_profile") != legacy_value:
        raise ValueError("Patchright migration paths do not match")

    state = state_data.get("state")
    history = state_data.get("history")
    if state not in _PATCHRIGHT_STATES or not isinstance(history, list) or not history or history[-1] != state:
        raise ValueError("Patchright migration state is invalid")
    if any(item not in _PATCHRIGHT_STATES for item in history):
        raise ValueError("Patchright migration history is invalid")
    return marker, state_file


def _full(root, report):
    marker = root / OWNERSHIP_MARKER
    try:
        if marker.exists() or marker.is_symlink():
            validate_target_path(root, marker)
            if _is_reparse(marker) or not marker.is_file():
                raise ValueError("A valid ownership marker is required for full maintenance")
            if marker.read_text(encoding="ascii") != _MARKER_CONTENT:
                raise ValueError("Ownership marker was not created by this module")
            preserved = {marker}
        else:
            preserved = set(_patchright_metadata(root))
        children = list(root.iterdir())
    except Exception as exc:
        _error(report, marker, exc)
        return
    for child in children:
        if child not in preserved:
            _remove(root, child, report)


def maintain_browser(
    root,
    mode,
    *,
    forbidden_roots=(),
    configured_profile_roots=(),
    stale_lock_age_seconds=DEFAULT_STALE_LOCK_AGE_SECONDS,
    now=None
):
    """Run browser maintenance and return a structured, non-throwing report."""
    mode_value = mode.value if isinstance(mode, MaintenanceMode) else str(mode).lower()
    report = _new_report(mode_value, root)
    if mode_value not in MODES:
        _error(report, root, ValueError("Unknown maintenance mode: {}".format(mode)))
        return report
    if stale_lock_age_seconds < 0:
        _error(report, root, ValueError("stale_lock_age_seconds cannot be negative"))
        return report
    try:
        canonical_root = validate_maintenance_root(
            root,
            forbidden_roots=forbidden_roots,
            configured_profile_roots=configured_profile_roots,
        )
        report["root"] = str(canonical_root)
    except Exception as exc:
        _error(report, root, exc)
        return report

    if mode_value == FULL:
        _full(canonical_root, report)
    else:
        profiles = _quick(
            canonical_root,
            report,
            stale_lock_age_seconds,
            time.time() if now is None else now,
        )
        if mode_value == SESSION:
            _session(canonical_root, profiles, report)
    report["success"] = not report["errors"]
    return report


run_browser_maintenance = maintain_browser
