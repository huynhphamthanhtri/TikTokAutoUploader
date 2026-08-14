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


if __name__ == '__main__':
    unittest.main()
