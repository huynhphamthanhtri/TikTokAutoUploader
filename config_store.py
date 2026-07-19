import json
from datetime import datetime

from browser_environment import ensure_fingerprint_defaults


def build_configs_payload(profiles, projects):
    export_profiles = {}
    for name, prof in profiles.items():
        prof['config']['stats_today'] = prof['uploads_today_count']
        prof['config']['stats_yesterday'] = prof.get('uploads_yesterday_count', 0)
        prof['config']['stats_date'] = prof['uploads_today_date']
        prof['config']['project'] = prof.get('project', 'Mặc định')
        export_profiles[name] = prof['config']

    return {
        'profiles': export_profiles,
        'projects': {name: list(projs) for name, projs in projects.items()}
    }


def save_configs_file(config_path, payload):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)


def load_configs_file(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_loaded_config(raw_configs):
    if 'profiles' in raw_configs and 'projects' in raw_configs:
        return raw_configs['profiles'], raw_configs['projects']
    return raw_configs, {'Mặc định': list(raw_configs.keys())}


def build_runtime_profiles(loaded_profiles):
    runtime_profiles = {}
    current_date_obj = datetime.now().date()
    current_date_str = current_date_obj.strftime('%Y-%m-%d')

    for name, prof_config in loaded_profiles.items():
        prof_config = dict(prof_config)
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
