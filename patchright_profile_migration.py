"""Safe state tracking for migration to an app-owned Patchright profile."""

import json
import os
import shutil
import stat
import tempfile
from enum import Enum
from pathlib import Path


class MigrationState(str, Enum):
    PENDING = "pending"
    CREATED = "created"
    COOKIES_IMPORTED = "cookies_imported"
    LOGIN_VERIFIED = "login_verified"
    UPLOAD_VERIFIED = "upload_verified"
    LEGACY_CLEANUP_PENDING = "legacy_cleanup_pending"
    COMPLETED = "completed"


STATES = tuple(state.value for state in MigrationState)
OWNERSHIP_MARKER = ".patchright-profile-owned"
STATE_FILE = ".patchright-migration.json"
_MARKER_VERSION = "patchright-profile-v1"
_ORDER = {state: index for index, state in enumerate(STATES)}


def _is_link_or_reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _canonical(path, *, must_exist=False):
    candidate = Path(path).expanduser().absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        if current.exists() or current.is_symlink():
            if _is_link_or_reparse(current):
                raise ValueError("Symlink or reparse paths are not allowed: {}".format(current))
    try:
        return candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise ValueError("Cannot resolve path {}: {}".format(candidate, exc)) from exc


def _strict_child(path, parent):
    try:
        return path != parent and os.path.commonpath((str(path), str(parent))) == str(parent)
    except ValueError:
        return False


def derive_patchright_profile_path(legacy_profile):
    """Derive the fixed Patchright sibling for a legacy directory named Profile."""
    legacy = _canonical(legacy_profile)
    if legacy.name.casefold() != "profile":
        raise ValueError("Legacy profile directory must be named Profile")
    if legacy == Path(legacy.anchor):
        raise ValueError("A filesystem root cannot be a legacy profile")
    return legacy.with_name("Profile-Patchright")


def _validate_layout(legacy_profile, managed_root, *, legacy_must_exist=True):
    managed = _canonical(managed_root, must_exist=True)
    legacy = _canonical(legacy_profile, must_exist=legacy_must_exist)
    target = derive_patchright_profile_path(legacy)
    if not managed.is_dir() or not _strict_child(legacy, managed) or not _strict_child(target, managed):
        raise ValueError("Profile paths must be strictly inside the managed root")
    if legacy_must_exist and not legacy.is_dir():
        raise ValueError("Legacy profile is not a directory")
    if target.parent != legacy.parent or target.name != "Profile-Patchright":
        raise ValueError("Patchright profile must be the fixed legacy sibling")
    return legacy, target


def _write_json_atomic(path, payload):
    descriptor, temporary_name = tempfile.mkstemp(prefix=".migration-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, sort_keys=True)
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


def _marker_payload(legacy, target, account_id=None):
    payload = {
        "format": _MARKER_VERSION,
        "legacy_profile": str(legacy),
        "patchright_profile": str(target),
    }
    if account_id:
        payload["account_id"] = str(account_id)
    return payload


def _load_owned(target):
    target = _canonical(target, must_exist=True)
    if not target.is_dir() or target.name != "Profile-Patchright":
        raise ValueError("Invalid Patchright profile directory")
    marker = target / OWNERSHIP_MARKER
    state_file = target / STATE_FILE
    try:
        marker_data = json.loads(marker.read_text(encoding="ascii"))
        state_data = json.loads(state_file.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Patchright profile ownership metadata is invalid") from exc
    if marker_data.get("format") != _MARKER_VERSION:
        raise ValueError("Patchright profile ownership marker is invalid")
    if marker_data.get("patchright_profile") != str(target):
        raise ValueError("Patchright ownership marker path does not match")
    state = state_data.get("state")
    history = state_data.get("history")
    if state not in STATES or not isinstance(history, list) or not history or history[-1] != state:
        raise ValueError("Patchright migration state is invalid")
    if any(item not in STATES for item in history):
        raise ValueError("Patchright migration history is invalid")
    if state_data.get("legacy_profile") != marker_data.get("legacy_profile"):
        raise ValueError("Patchright migration metadata does not match")
    return target, marker_data, state_data


def create_patchright_profile(legacy_profile, managed_root, account_id=None):
    """Create an empty owned sibling and initialize migration state.

    Existing owned profiles are returned for safe resume. Existing unowned
    directories are never adopted or modified.
    """
    legacy, target = _validate_layout(legacy_profile, managed_root)
    if target.exists():
        loaded_target, marker, _state = _load_owned(target)
        if marker.get("legacy_profile") != str(legacy):
            raise ValueError("Owned profile belongs to a different legacy profile")
        if account_id and marker.get("account_id") and marker.get("account_id") != str(account_id):
            raise ValueError("Owned profile belongs to a different account")
        return loaded_target

    target.mkdir()
    marker = target / OWNERSHIP_MARKER
    state_file = target / STATE_FILE
    try:
        with marker.open("x", encoding="ascii", newline="\n") as stream:
            json.dump(_marker_payload(legacy, target, account_id=account_id), stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _write_json_atomic(state_file, {
            "legacy_profile": str(legacy),
            "state": MigrationState.CREATED.value,
            "history": [MigrationState.PENDING.value, MigrationState.CREATED.value],
        })
    except Exception:
        for child in (state_file, marker):
            try:
                child.unlink()
            except OSError:
                pass
        try:
            target.rmdir()
        except OSError:
            pass
        raise
    return target


def profile_owner_id(patchright_profile):
    """Return the account_id bound to the owned profile, or None."""
    _target, marker, _state = _load_owned(patchright_profile)
    return marker.get("account_id") or None


def set_profile_owner(patchright_profile, account_id):
    """Bind (or re-bind) an account_id to an owned profile marker."""
    target, marker, _state = _load_owned(patchright_profile)
    marker["account_id"] = str(account_id or "")
    _write_json_atomic(target / OWNERSHIP_MARKER, marker)
    return dict(marker)


def migration_status(patchright_profile):
    """Return a detached copy of the persisted migration record."""
    _target, _marker, state = _load_owned(patchright_profile)
    return {"legacy_profile": state["legacy_profile"], "state": state["state"], "history": list(state["history"])}


def advance_migration(patchright_profile, new_state):
    """Advance exactly one state; repeated calls for the current state are safe."""
    target, _marker, record = _load_owned(patchright_profile)
    requested = new_state.value if isinstance(new_state, MigrationState) else str(new_state)
    if requested not in STATES:
        raise ValueError("Unknown migration state: {}".format(requested))
    current = record["state"]
    if requested == current:
        return migration_status(target)
    if _ORDER[requested] != _ORDER[current] + 1:
        raise ValueError("Invalid migration transition: {} -> {}".format(current, requested))
    record["state"] = requested
    record["history"].append(requested)
    _write_json_atomic(target / STATE_FILE, record)
    return migration_status(target)


def mark_profile_login_verified(patchright_profile, note=""):
    """Mark a clean profile as login_verified without a cookie import step.

    The automated login environment flow logs in through a real browser
    session (no cookie import), so the ``cookies_imported`` transition is
    skipped. The persisted history is rewritten to a valid chain ending at
    ``login_verified`` so the record always validates against :data:`STATES`.
    """
    target, _marker, record = _load_owned(patchright_profile)
    current = record["state"]
    if current == MigrationState.LOGIN_VERIFIED.value:
        return migration_status(target)
    if current != MigrationState.CREATED.value:
        raise ValueError(
            "Only a created profile can be marked login_verified, got {}".format(current)
        )
    record["state"] = MigrationState.LOGIN_VERIFIED.value
    record["history"] = [
        MigrationState.PENDING.value,
        MigrationState.CREATED.value,
        MigrationState.LOGIN_VERIFIED.value,
    ]
    if note:
        record["note"] = str(note)
    _write_json_atomic(target / STATE_FILE, record)
    return migration_status(target)


def cleanup_legacy_profile(
    patchright_profile,
    managed_root,
    *,
    explicit_confirmation=False,
):
    """Delete the legacy profile only after upload verification and confirmation."""
    target, marker, record = _load_owned(patchright_profile)
    if explicit_confirmation is not True:
        raise PermissionError("Legacy cleanup requires explicit_confirmation=True")
    if record["state"] not in (
        MigrationState.UPLOAD_VERIFIED.value,
        MigrationState.LEGACY_CLEANUP_PENDING.value,
    ):
        raise RuntimeError("Legacy cleanup is only allowed after upload_verified")

    legacy_path = Path(marker["legacy_profile"])
    legacy, expected_target = _validate_layout(
        legacy_path,
        managed_root,
        legacy_must_exist=legacy_path.exists(),
    )
    if target != expected_target or record["legacy_profile"] != str(legacy):
        raise ValueError("Migration paths do not match the owned profile metadata")

    if record["state"] == MigrationState.UPLOAD_VERIFIED.value:
        advance_migration(target, MigrationState.LEGACY_CLEANUP_PENDING)
    if legacy.exists():
        # This is an approved removal of the whole legacy profile, not a DB copy.
        shutil.rmtree(legacy)
    return advance_migration(target, MigrationState.COMPLETED)
