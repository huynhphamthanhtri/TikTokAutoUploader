"""Account -> browser profile ownership: per-account isolation to reduce
checkpoint risk.

Each account gets an immutable ``account_uuid``. Browser profiles are owned
by that uuid, never shared across accounts. This module is pure and testable:
it never touches the UI or filesystem.
"""

import os
import uuid as uuid_module
from pathlib import Path

from core_helpers import parse_proxy_string


def generate_account_uuid():
    return uuid_module.uuid4().hex


def normalize_path(path):
    value = str(path or '').strip()
    if not value:
        return ''
    try:
        return os.path.normcase(os.path.abspath(str(Path(value).expanduser().resolve())))
    except OSError:
        return os.path.normcase(os.path.abspath(str(Path(value).expanduser())))


def derived_patchright_path(legacy_profile):
    """Patchright sibling derived from a legacy directory named Profile."""
    if not legacy_profile:
        return ''
    legacy = Path(legacy_profile)
    return str(legacy.with_name('Profile-Patchright'))


def build_account_entry(account_name, config):
    legacy = normalize_path(config.get('chrome_profile', ''))
    return {
        'account_name': account_name,
        'account_uuid': str(config.get('account_uuid', '') or ''),
        'legacy_path': legacy,
        'derived_patchright_path': normalize_path(
            derived_patchright_path(config.get('chrome_profile', ''))
        ),
        'persisted_patchright_path': normalize_path(config.get('browser_profile_path', '')),
        'migration_state': str(config.get('migration_state', '') or ''),
    }


def build_profile_inventory(profiles):
    """Build a read-only inventory keyed by account name."""
    inventory = {}
    for name, profile in (profiles or {}).items():
        config = profile.get('config', {}) if isinstance(profile, dict) else {}
        inventory[name] = build_account_entry(name, config)
    return inventory


def _add_conflict(conflicts, entry):
    conflicts.append(entry)


def detect_profile_conflicts(inventory):
    """Return a list of conflict descriptors for shared or mismatched paths.

    Each descriptor has a ``type``; callers map types to user messages.
    """
    conflicts = []
    entries = list((inventory or {}).items())
    by_legacy = {}
    by_patchright = {}
    for name, entry in entries:
        if entry['legacy_path']:
            by_legacy.setdefault(entry['legacy_path'], []).append(name)
        for key in ('derived_patchright_path', 'persisted_patchright_path'):
            value = entry[key]
            if value:
                by_patchright.setdefault(value, []).append(name)

    for path, names in by_legacy.items():
        if len(names) > 1:
            _add_conflict(conflicts, {'type': 'shared_legacy', 'path': path, 'names': names})

    for name, entry in entries:
        if entry['legacy_path'] and entry['legacy_path'] in by_patchright:
            owners = [other for other in by_patchright[entry['legacy_path']] if other != name]
            if owners:
                _add_conflict(conflicts, {
                    'type': 'legacy_is_patchright',
                    'path': entry['legacy_path'],
                    'names': [name] + owners,
                })

    for path, names in by_patchright.items():
        unique = sorted(set(names))
        if len(unique) > 1:
            _add_conflict(conflicts, {
                'type': 'shared_patchright',
                'path': path,
                'names': unique,
            })

    for name, entry in entries:
        derived = entry['derived_patchright_path']
        persisted = entry['persisted_patchright_path']
        if derived and persisted and derived != persisted:
            _add_conflict(conflicts, {
                'type': 'path_mismatch',
                'name': name,
                'derived': derived,
                'persisted': persisted,
            })

    seen_pairs = set()
    all_paths = []
    for name, entry in entries:
        for key in ('legacy_path', 'derived_patchright_path', 'persisted_patchright_path'):
            value = entry[key]
            if value:
                all_paths.append((value, name))
    for index in range(len(all_paths)):
        for other in range(index + 1, len(all_paths)):
            first, first_name = all_paths[index]
            second, second_name = all_paths[other]
            pair = tuple(sorted((first, second)))
            if pair in seen_pairs:
                continue
            if first == second:
                continue
            nested = (
                first.startswith(second + os.sep)
                or second.startswith(first + os.sep)
            )
            if nested and first_name != second_name:
                seen_pairs.add(pair)
                _add_conflict(conflicts, {
                    'type': 'nested_path',
                    'names': [first_name, second_name],
                    'path_a': first,
                    'path_b': second,
                })

    return conflicts


def conflict_account_names(conflicts):
    """All account names touched by any conflict, preserving order."""
    names = []
    seen = set()
    for conflict in conflicts:
        for name in conflict.get('names', []):
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def ensure_account_uuid(config):
    """Assign an account_uuid when missing. Returns the uuid."""
    current = str(config.get('account_uuid', '') or '').strip()
    if current:
        return current
    account_uuid = generate_account_uuid()
    config['account_uuid'] = account_uuid
    config['profile_owner_state'] = 'unverified'
    return account_uuid


def invalidate_session_auth(config, reason=''):
    """Invalidate verified session metadata after identity/proxy changes.

    Does not delete ``cookie_str``; the saved cookie remains as fallback.
    """
    config['session_auth_state'] = 'unknown'
    config['session_source'] = ''
    config['session_verified_at'] = ''
    config['session_verified_profile_path'] = ''
    config['session_verified_proxy_key'] = ''
    config['session_last_failure_at'] = ''
    config['session_last_failure_reason'] = str(reason or '')[:200]
    config['manual_login_pending'] = False
    config['profile_owner_state'] = config.get('profile_owner_state', 'unverified')


def session_proxy_key(config):
    """Proxy identity hash without the password (never persisted/logged)."""
    if not config.get('use_proxy', False):
        return 'direct'
    proxy_string = str(config.get('proxy_string', '') or '').strip()
    if not proxy_string:
        return 'invalid'
    proxy_type = str(config.get('proxy_type', 'http') or 'http').strip().lower() or 'http'
    proxy_data = parse_proxy_string(proxy_string)
    if not proxy_data:
        return 'invalid'
    identity = '{}|{}|{}|{}'.format(
        proxy_type,
        proxy_data.get('ip', ''),
        proxy_data.get('port', ''),
        proxy_data.get('user', ''),
    )
    import hashlib
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


class ProfileLeaseError(RuntimeError):
    """Raised when profile is locked by another process."""
    pass


class ProfileLease:
    """Acquires and holds an exclusive OS-level file lock on a profile directory."""

    def __init__(self, profile_path, account_uuid=''):
        self.profile_path = normalize_path(profile_path)
        self.account_uuid = str(account_uuid or '')
        self.lock_file_path = os.path.join(self.profile_path, '.profile_lease.lock') if self.profile_path else ''
        self._file_obj = None
        self._locked = False

    def acquire(self):
        if not self.lock_file_path:
            return False
        os.makedirs(self.profile_path, exist_ok=True)
        try:
            self._file_obj = open(self.lock_file_path, 'a+', encoding='utf-8')
            if os.name == 'nt':
                import msvcrt
                self._file_obj.seek(0)
                msvcrt.locking(self._file_obj.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            import json, time
            self._file_obj.seek(0)
            self._file_obj.truncate()
            metadata = {
                'pid': os.getpid(),
                'account_uuid': self.account_uuid,
                'acquired_at': time.time(),
            }
            self._file_obj.write(json.dumps(metadata))
            self._file_obj.flush()
            self._locked = True
            return True
        except (OSError, IOError) as exc:
            if self._file_obj:
                try:
                    self._file_obj.close()
                except Exception:
                    pass
                self._file_obj = None
            self._locked = False
            raise ProfileLeaseError(f'Profile {self.profile_path} is currently in use by another process') from exc

    def release(self):
        if self._locked and self._file_obj:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self._file_obj.seek(0)
                    msvcrt.locking(self._file_obj.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._file_obj.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._file_obj.close()
            except Exception:
                pass
            self._file_obj = None
            self._locked = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()