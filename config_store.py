import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from browser_environment import ensure_fingerprint_defaults
from account_io import ACCOUNT_DEFAULTS

_CONFIG_SAVE_LOCK = threading.RLock()

# Metadata trạng thái xác thực session độc lập với migration_state.
# Chỉ là cache/audit/UI: mỗi lần Start vẫn live-verify persistent profile.
SESSION_AUTH_DEFAULTS = {
    'session_auth_state': 'unknown',
    'session_source': '',
    'session_verified_at': '',
    'session_verified_profile_path': '',
    'session_verified_proxy_key': '',
    'session_last_failure_at': '',
    'session_last_failure_reason': '',
    'manual_login_pending': False,
    'proxy_change_classification': '',
    'proxy_environment_warning': '',
    'proxy_environment_changed_at': '',
    'proxy_previous_exit_ip': '',
    'proxy_environment_history': [],
}

# Ownership browser profile riêng cho mỗi tài khoản (tránh checkpoint).
# account_uuid là định danh bất biến, không phụ thuộc tên hiển thị.
ACCOUNT_OWNERSHIP_DEFAULTS = {
    'account_uuid': '',
    'profile_schema_version': 1,
    'profile_owner_state': 'unverified',
    'profile_created_at': '',
    'profile_isolation_state': 'unknown',
}


def _normalize_browser_config(config):
    normalized = dict(config)
    normalized.setdefault('legacy_chrome_profile', normalized.get('chrome_profile', ''))
    normalized.setdefault('browser_profile_path', '')
    normalized.setdefault('browser_engine', 'patchright')
    normalized.setdefault('migration_state', 'pending')
    for key, default in ACCOUNT_DEFAULTS.items():
        normalized.setdefault(key, default)
    for key, default in SESSION_AUTH_DEFAULTS.items():
        normalized.setdefault(key, default)
    for key, default in ACCOUNT_OWNERSHIP_DEFAULTS.items():
        normalized.setdefault(key, default)
    return normalized


def build_configs_payload(profiles, projects):
    export_profiles = {}
    for name, prof in profiles.items():
        config = _normalize_browser_config(prof.get('config', {}))
        config['stats_today'] = prof.get('uploads_today_count', 0)
        config['stats_yesterday'] = prof.get('uploads_yesterday_count', 0)
        config['stats_date'] = prof.get('uploads_today_date', '')
        config['project'] = prof.get('project', 'Mặc định')
        export_profiles[name] = config

    return {
        'profiles': export_profiles,
        'projects': {name: list(projs) for name, projs in projects.items()}
    }


def _rotate_backups(config_path: Path, max_backups: int = 3):
    """Rotate config backups: .bak -> .bak.1 -> .bak.2 -> .bak.3."""
    try:
        if config_path.exists() and config_path.stat().st_size == 0:
            return
    except Exception:
        return
    base_bak = config_path.with_name(f"{config_path.name}.bak")
    for i in range(max_backups, 0, -1):
        cur_bak = config_path.with_name(f"{config_path.name}.bak.{i}")
        if i > 1:
            prev_bak = config_path.with_name(f"{config_path.name}.bak.{i - 1}")
        else:
            prev_bak = base_bak
        if not prev_bak.exists():
            continue
        try:
            if cur_bak.exists():
                cur_bak.unlink(missing_ok=True)
            shutil.copy2(prev_bak, cur_bak)
        except Exception:
            continue
    try:
        shutil.copy2(config_path, base_bak)
    except Exception:
        pass


def save_configs_file(config_path, payload, allow_truncate: bool = False):
    """Save configs payload to disk with rotating backups and truncation safety guards."""
    if isinstance(config_path, str):
        config_path = Path(config_path)
    if isinstance(payload, dict):
        incoming_profiles = payload.get('profiles', {})
    else:
        incoming_profiles = {}
    incoming_count = len(incoming_profiles)

    if config_path.exists() and config_path.stat().st_size > 0 and not allow_truncate:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            if isinstance(existing_data, dict):
                existing_profiles = existing_data.get('profiles', {})
            else:
                existing_profiles = {}
            existing_count = len(existing_profiles)

            if existing_count >= 3 and incoming_count <= 1:
                scratch_dir = config_path.parent / 'scratch'
                scratch_dir.mkdir(exist_ok=True)
                quarantine_file = scratch_dir / f"quarantined_configs_{uuid.uuid4().hex[:8]}.json"
                with open(quarantine_file, "w", encoding="utf-8") as qf:
                    json.dump(payload, qf, indent=4, ensure_ascii=False)
                err_msg = (
                    f"[DATA PROTECTION] Blocked unsafe truncation of {config_path.name}: attempted to save "
                    f"{incoming_count} profiles over {existing_count} existing profiles! Quarantined to "
                    f"{quarantine_file.name}. Set allow_truncate=True to force."
                )
                logging.error(err_msg)
                raise RuntimeError(err_msg)
        except json.JSONDecodeError:
            pass

    _rotate_backups(config_path)

    tmp = config_path.with_name(f"{config_path.name}.{uuid.uuid4().hex}.tmp")
    with _CONFIG_SAVE_LOCK:
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, config_path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def load_configs_file(config_path):
    """Load configs from file with automatic fallback recovery if corrupted or missing."""
    if isinstance(config_path, str):
        config_path = Path(config_path)
    if config_path.exists() and config_path.stat().st_size > 0:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logging.warning(
                f"[CONFIG LOAD] Primary file {config_path} corrupted: {e}, attempting backup recovery..."
            )

    candidates = [
        config_path.with_name(f"{config_path.name}.bak"),
        config_path.with_name(f"{config_path.name}.bak.1"),
        config_path.with_name(f"{config_path.name}.bak.2"),
    ]
    if config_path.parent.exists():
        candidates.extend(sorted(config_path.parent.glob(f"{config_path.name}.login-backup-*.json"), reverse=True))
    for cand in candidates:
        if not cand.exists() or cand.stat().st_size <= 0:
            continue
        try:
            with open(cand, "r", encoding="utf-8") as f:
                recovered = json.load(f)
            if isinstance(recovered, dict) and len(recovered.get('profiles', {})) > 0:
                logging.info(
                    f"[CONFIG AUTO-RECOVERY] Successfully recovered "
                    f"{len(recovered.get('profiles', {}))} profiles from backup: {cand.name}"
                )
                try:
                    shutil.copy2(cand, config_path)
                    return recovered
                except Exception:
                    continue
        except Exception:
            continue
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError(f"Config file not found: {config_path}")


def normalize_loaded_config(raw_configs):
    if 'profiles' in raw_configs and 'projects' in raw_configs:
        return raw_configs['profiles'], raw_configs['projects']
    return raw_configs, {'Mặc định': list(raw_configs.keys())}


def build_runtime_profiles(loaded_profiles):
    runtime_profiles = {}
    current_date_obj = datetime.now().date()
    current_date_str = current_date_obj.strftime('%Y-%m-%d')

    for name, prof_config in loaded_profiles.items():
        prof_config = _normalize_browser_config(prof_config)
        prof_config['fingerprint'] = ensure_fingerprint_defaults(
            prof_config.get('fingerprint', {}),
            seed=name + str(prof_config.get('cookie_str', '')),
        )
        project = prof_config.pop('project', 'Mặc định')
        headless = prof_config.pop('headless', True)
        max_uploads = prof_config.pop('max_uploads_per_day', 0)
        use_proxy = prof_config.pop('use_proxy', False)
        proxy_string = prof_config.pop('proxy_string', "")
        prof_config.pop('proxy_username', None)
        prof_config.pop('proxy_password', None)

        saved_date_str = prof_config.get('stats_date', '')
        saved_today = prof_config.get('stats_today', 0)
        saved_yesterday = prof_config.get('stats_yesterday', 0)

        final_today = 0
        final_yesterday = saved_yesterday

        if saved_date_str:
            try:
                saved_date_obj = datetime.strptime(saved_date_str, '%Y-%m-%d').date()
                delta_days = (current_date_obj - saved_date_obj).days

                if delta_days == 0:
                    final_today = saved_today
                    final_yesterday = saved_yesterday
                elif delta_days == 1:
                    final_yesterday = saved_today
                    final_today = 0
                else:
                    final_yesterday = 0
                    final_today = 0
            except Exception:
                final_today = 0
                final_yesterday = 0

        runtime_profiles[name] = {
            'config': {
                **prof_config,
                'headless': headless,
                'max_uploads_per_day': max_uploads,
                'use_proxy': use_proxy,
                'proxy_string': proxy_string,
                'stats_today': final_today,
                'stats_yesterday': final_yesterday,
                'stats_date': current_date_str
            },
            'queue': None,
            'observer': None,
            'driver': None,
            'manual_driver': None,
            'automation_session': None,
            'manual_session': None,
            'session_busy': False,
            'running': False,
            'processed_files': set(),
            'last_event_time': {},
            'uploading': False,
            'project': project,
            'uploads_today_count': final_today,
            'uploads_yesterday_count': final_yesterday,
            'uploads_today_date': current_date_str
        }

    return runtime_profiles
