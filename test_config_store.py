import copy
import unittest

from config_store import build_configs_payload, build_runtime_profiles


class ConfigStoreTests(unittest.TestCase):
    def test_old_config_migrates_to_pending_patchright_schema(self):
        loaded = {
            'legacy': {
                'chrome_profile': r'C:\Auto_Data\legacy\Profile',
                'cookie_str': 'session=value',
                'custom_setting': {'enabled': True},
            }
        }
        original = copy.deepcopy(loaded)

        profile = build_runtime_profiles(loaded)['legacy']
        config = profile['config']

        self.assertEqual(config['legacy_chrome_profile'], loaded['legacy']['chrome_profile'])
        self.assertEqual(config['browser_profile_path'], '')
        self.assertEqual(config['browser_engine'], 'patchright')
        self.assertEqual(config['migration_state'], 'pending')
        self.assertEqual(config['custom_setting'], {'enabled': True})
        self.assertEqual(loaded, original)
        for key in ('driver', 'manual_driver', 'automation_session', 'manual_session'):
            self.assertIsNone(profile[key])
        self.assertFalse(profile['session_busy'])

    def test_existing_browser_fields_are_preserved_and_persisted(self):
        config = {
            'chrome_profile': r'C:\Auto_Data\current\Profile',
            'legacy_chrome_profile': r'D:\Legacy\Profile',
            'browser_profile_path': r'C:\Auto_Data\current\Profile-Patchright',
            'browser_engine': 'patchright',
            'migration_state': 'login_verified',
            'unrelated': ['keep', 'unchanged'],
        }
        profile = {
            'config': config,
            'uploads_today_count': 2,
            'uploads_yesterday_count': 1,
            'uploads_today_date': '2026-08-13',
            'project': 'Project A',
        }
        original = copy.deepcopy(profile)

        exported = build_configs_payload({'existing': profile}, {'Project A': {'existing'}})
        saved = exported['profiles']['existing']

        for key, value in config.items():
            self.assertEqual(saved[key], value)
        self.assertEqual(saved['stats_today'], 2)
        self.assertEqual(saved['stats_yesterday'], 1)
        self.assertEqual(saved['stats_date'], '2026-08-13')
        self.assertEqual(saved['project'], 'Project A')
        self.assertEqual(profile, original)

    def test_old_config_migrates_account_fields_to_defaults(self):
        loaded = {
            'legacy': {
                'chrome_profile': r'C:\Auto_Data\legacy\Profile',
                'cookie_str': 'session=value',
            }
        }
        original = copy.deepcopy(loaded)

        config = build_runtime_profiles(loaded)['legacy']['config']

        self.assertEqual(config['email'], '')
        self.assertEqual(config['password'], '')
        self.assertEqual(config['auth2fa'], '')
        self.assertEqual(config['passmail'], '')
        self.assertEqual(config['mail_backup'], '')
        self.assertEqual(config['pass_mail_backup'], '')
        self.assertEqual(config['note'], '')
        self.assertEqual(config['proxy_type'], 'http')
        self.assertEqual(config['tiktok_id'], '')
        self.assertEqual(config['cookie_str'], 'session=value')
        self.assertEqual(loaded, original)

    def test_old_config_migrates_ownership_fields_to_defaults(self):
        loaded = {
            'legacy': {
                'chrome_profile': r'C:\Auto_Data\legacy\Profile',
            }
        }
        original = copy.deepcopy(loaded)

        config = build_runtime_profiles(loaded)['legacy']['config']

        self.assertEqual(config['account_uuid'], '')
        self.assertEqual(config['profile_schema_version'], 1)
        self.assertEqual(config['profile_owner_state'], 'unverified')
        self.assertEqual(config['profile_created_at'], '')
        self.assertEqual(config['profile_isolation_state'], 'unknown')
        self.assertEqual(loaded, original)

    def test_existing_ownership_fields_are_preserved(self):
        loaded = {
            'acc': {
                'chrome_profile': r'C:\Auto_Data\acc\Profile',
                'account_uuid': 'uuid-abc',
                'profile_owner_state': 'verified',
            }
        }
        config = build_runtime_profiles(loaded)['acc']['config']
        self.assertEqual(config['account_uuid'], 'uuid-abc')
        self.assertEqual(config['profile_owner_state'], 'verified')
        self.assertEqual(config['profile_schema_version'], 1)

    def test_existing_account_fields_are_preserved(self):
        loaded = {
            'acc': {
                'chrome_profile': r'C:\Auto_Data\acc\Profile',
                'email': 'a@x.com',
                'password': 'pw',
                'tiktok_id': 'user1',
                'proxy_string': '1.2.3.4:80',
                'cookie_str': 'k=v',
                'auth2fa': 'secret',
                'passmail': 'pm',
                'mail_backup': 'b@x.com',
                'pass_mail_backup': 'pbm',
                'note': 'ghi',
                'proxy_type': 'socks5',
            }
        }
        config = build_runtime_profiles(loaded)['acc']['config']

        self.assertEqual(config['email'], 'a@x.com')
        self.assertEqual(config['password'], 'pw')
        self.assertEqual(config['tiktok_id'], 'user1')
        self.assertEqual(config['proxy_string'], '1.2.3.4:80')
        self.assertEqual(config['cookie_str'], 'k=v')
        self.assertEqual(config['auth2fa'], 'secret')
        self.assertEqual(config['passmail'], 'pm')
        self.assertEqual(config['mail_backup'], 'b@x.com')
        self.assertEqual(config['pass_mail_backup'], 'pbm')
        self.assertEqual(config['note'], 'ghi')
        self.assertEqual(config['proxy_type'], 'socks5')

    def test_migration_is_idempotent(self):
        loaded = {
            'acc': {
                'chrome_profile': r'C:\Auto_Data\acc\Profile',
                'cookie_str': 'k=v',
            }
        }
        first = build_runtime_profiles(loaded)['acc']['config']
        second_pass = build_runtime_profiles({'acc': dict(first)})['acc']['config']
        for key in ('email', 'password', 'auth2fa', 'passmail', 'mail_backup',
                    'pass_mail_backup', 'note', 'proxy_type', 'tiktok_id'):
            self.assertEqual(second_pass[key], first[key])

    def test_old_config_migrates_session_auth_metadata_to_defaults(self):
        loaded = {
            'legacy': {
                'chrome_profile': r'C:\Auto_Data\legacy\Profile',
                'cookie_str': 'session=value',
            }
        }
        original = copy.deepcopy(loaded)

        config = build_runtime_profiles(loaded)['legacy']['config']

        self.assertEqual(config['session_auth_state'], 'unknown')
        self.assertEqual(config['session_source'], '')
        self.assertEqual(config['session_verified_at'], '')
        self.assertEqual(config['session_verified_profile_path'], '')
        self.assertEqual(config['session_verified_proxy_key'], '')
        self.assertEqual(config['session_last_failure_at'], '')
        self.assertEqual(config['session_last_failure_reason'], '')
        self.assertFalse(config['manual_login_pending'])
        self.assertEqual(loaded, original)

    def test_existing_session_auth_metadata_is_preserved(self):
        loaded = {
            'acc': {
                'chrome_profile': r'C:\Auto_Data\acc\Profile',
                'session_auth_state': 'verified',
                'session_source': 'manual_login',
                'session_verified_at': '2026-08-14T00:00:00+00:00',
                'session_verified_proxy_key': 'abc123',
                'session_last_failure_at': '',
                'session_last_failure_reason': '',
                'manual_login_pending': True,
            }
        }
        config = build_runtime_profiles(loaded)['acc']['config']

        self.assertEqual(config['session_auth_state'], 'verified')
        self.assertEqual(config['session_source'], 'manual_login')
        self.assertEqual(config['session_verified_at'], '2026-08-14T00:00:00+00:00')
        self.assertEqual(config['session_verified_proxy_key'], 'abc123')
        self.assertTrue(config['manual_login_pending'])


if __name__ == '__main__':
    unittest.main()
