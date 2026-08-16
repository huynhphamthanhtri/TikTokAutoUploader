import unittest

from profile_ownership import (
    build_account_entry,
    build_profile_inventory,
    conflict_account_names,
    derived_patchright_path,
    detect_profile_conflicts,
    ensure_account_uuid,
    generate_account_uuid,
    invalidate_session_auth,
    normalize_path,
    session_proxy_key,
)


class AccountUuidTests(unittest.TestCase):
    def test_generate_account_uuid_is_hex_and_unique(self):
        first = generate_account_uuid()
        second = generate_account_uuid()
        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, second)
        int(first, 16)

    def test_ensure_account_uuid_assigns_once_and_preserves(self):
        config = {}
        first = ensure_account_uuid(config)
        self.assertEqual(first, config['account_uuid'])
        self.assertEqual(config['profile_owner_state'], 'unverified')
        second = ensure_account_uuid(config)
        self.assertEqual(second, first)

    def test_ensure_account_uuid_keeps_existing(self):
        config = {'account_uuid': 'fixed-value'}
        self.assertEqual(ensure_account_uuid(config), 'fixed-value')
        self.assertNotIn('profile_owner_state', config)


class NormalizePathTests(unittest.TestCase):
    def test_normalize_path_empty(self):
        self.assertEqual(normalize_path(''), '')
        self.assertEqual(normalize_path(None), '')
        self.assertEqual(normalize_path('   '), '')

    def test_normalize_path_absolves_relative(self):
        self.assertTrue(normalize_path('Profile'))


class DerivedPathTests(unittest.TestCase):
    def test_derived_patchright_sibling(self):
        self.assertEqual(
            derived_patchright_path(r'C:\Auto_Data\acct1\Profile'),
            r'C:\Auto_Data\acct1\Profile-Patchright',
        )
        self.assertEqual(derived_patchright_path(''), '')


class InventoryTests(unittest.TestCase):
    def test_build_account_entry_fields(self):
        entry = build_account_entry('A', {
            'chrome_profile': r'C:\Data\A\Profile',
            'browser_profile_path': r'C:\Data\A\Profile-Patchright',
            'account_uuid': 'uuid-A',
            'migration_state': 'pending',
        })
        self.assertEqual(entry['account_name'], 'A')
        self.assertEqual(entry['account_uuid'], 'uuid-A')
        self.assertEqual(entry['legacy_path'], normalize_path(r'C:\Data\A\Profile'))
        self.assertEqual(entry['derived_patchright_path'], normalize_path(r'C:\Data\A\Profile-Patchright'))
        self.assertEqual(entry['persisted_patchright_path'], normalize_path(r'C:\Data\A\Profile-Patchright'))
        self.assertEqual(entry['migration_state'], 'pending')

    def test_build_profile_inventory_ignores_non_dict(self):
        inventory = build_profile_inventory({'A': {'config': {'chrome_profile': 'x'}}, 'B': None})
        self.assertIn('A', inventory)
        self.assertIn('B', inventory)


class ConflictDetectionTests(unittest.TestCase):
    def test_shared_legacy_detected(self):
        inventory = build_profile_inventory({
            'A': {'config': {'chrome_profile': r'C:\Shared\Profile'}},
            'B': {'config': {'chrome_profile': r'C:\Shared\Profile'}},
        })
        conflicts = detect_profile_conflicts(inventory)
        types = [c['type'] for c in conflicts]
        self.assertIn('shared_legacy', types)
        shared = next(c for c in conflicts if c['type'] == 'shared_legacy')
        self.assertEqual(sorted(shared['names']), ['A', 'B'])

    def test_shared_patchright_detected(self):
        inventory = build_profile_inventory({
            'A': {'config': {'browser_profile_path': r'C:\Shared\Profile-Patchright'}},
            'B': {'config': {'browser_profile_path': r'C:\Shared\Profile-Patchright'}},
        })
        conflicts = detect_profile_conflicts(inventory)
        shared = [c for c in conflicts if c['type'] == 'shared_patchright']
        self.assertTrue(shared)
        self.assertEqual(sorted(shared[0]['names']), ['A', 'B'])

    def test_legacy_is_patchright_detected(self):
        inventory = build_profile_inventory({
            'A': {'config': {'chrome_profile': r'C:\Data\Profile', 'browser_profile_path': ''}},
            'B': {'config': {'chrome_profile': '', 'browser_profile_path': r'C:\Data\Profile'}},
        })
        conflicts = detect_profile_conflicts(inventory)
        self.assertIn('legacy_is_patchright', [c['type'] for c in conflicts])

    def test_path_mismatch_detected(self):
        inventory = build_profile_inventory({
            'A': {'config': {'chrome_profile': r'C:\Data\Profile',
                             'browser_profile_path': r'C:\Other\Profile-Patchright'}},
        })
        conflicts = detect_profile_conflicts(inventory)
        self.assertIn('path_mismatch', [c['type'] for c in conflicts])

    def test_nested_path_detected(self):
        inventory = build_profile_inventory({
            'A': {'config': {'chrome_profile': r'C:\Root\Profile'}},
            'B': {'config': {'chrome_profile': r'C:\Root\Profile\Sub\Profile'}},
        })
        conflicts = detect_profile_conflicts(inventory)
        self.assertIn('nested_path', [c['type'] for c in conflicts])

    def test_no_conflicts_isolated(self):
        inventory = build_profile_inventory({
            'A': {'config': {'chrome_profile': r'C:\Data\A\Profile'}},
            'B': {'config': {'chrome_profile': r'C:\Data\B\Profile'}},
        })
        self.assertEqual(detect_profile_conflicts(inventory), [])

    def test_conflict_account_names_dedupes(self):
        conflicts = [
            {'type': 'shared_legacy', 'names': ['A', 'B']},
            {'type': 'shared_patchright', 'names': ['B', 'C']},
        ]
        self.assertEqual(conflict_account_names(conflicts), ['A', 'B', 'C'])


class SessionInvalidationTests(unittest.TestCase):
    def _verified_config(self):
        return {
            'account_uuid': 'uuid',
            'cookie_str': 'session=abc',
            'session_auth_state': 'verified',
            'session_source': 'manual_login',
            'session_verified_at': '2026-01-01T00:00:00Z',
            'session_verified_profile_path': r'C:\Data\A\Profile-Patchright',
            'session_verified_proxy_key': 'hash',
            'session_last_failure_at': '',
            'session_last_failure_reason': '',
            'manual_login_pending': False,
        }

    def test_invalidate_resets_verified_fields_keeps_cookie(self):
        config = self._verified_config()
        invalidate_session_auth(config, 'Đổi cookie')
        self.assertEqual(config['session_auth_state'], 'unknown')
        self.assertEqual(config['session_source'], '')
        self.assertEqual(config['session_verified_at'], '')
        self.assertEqual(config['session_verified_profile_path'], '')
        self.assertEqual(config['session_verified_proxy_key'], '')
        self.assertEqual(config['session_last_failure_reason'], 'Đổi cookie')
        self.assertFalse(config['manual_login_pending'])
        self.assertEqual(config['cookie_str'], 'session=abc')

    def test_invalidate_truncates_reason(self):
        config = self._verified_config()
        invalidate_session_auth(config, 'x' * 500)
        self.assertEqual(len(config['session_last_failure_reason']), 200)


class SessionProxyKeyTests(unittest.TestCase):
    def test_direct_without_proxy(self):
        self.assertEqual(session_proxy_key({'use_proxy': False}), 'direct')

    def test_invalid_proxy(self):
        self.assertEqual(session_proxy_key({'use_proxy': True, 'proxy_string': 'not-a-proxy'}), 'invalid')

    def test_deterministic_and_password_free(self):
        base = {'use_proxy': True, 'proxy_string': '1.2.3.4:8080:user:secretpass'}
        key = session_proxy_key(base)
        self.assertEqual(key, session_proxy_key(base))
        self.assertEqual(len(key), 64)
        self.assertNotIn('secretpass', key)
        different = session_proxy_key({'use_proxy': True, 'proxy_string': '1.2.3.4:8080:user2:secretpass'})
        self.assertNotEqual(key, different)

    def test_socks5_vs_http_differ(self):
        http_key = session_proxy_key({'use_proxy': True, 'proxy_string': '1.2.3.4:8080:user:pass', 'proxy_type': 'http'})
        socks_key = session_proxy_key({'use_proxy': True, 'proxy_string': '1.2.3.4:8080:user:pass', 'proxy_type': 'socks5'})
        self.assertNotEqual(http_key, socks_key)


class ProfileLeaseTests(unittest.TestCase):
    def test_profile_lease_acquire_and_release(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            from profile_ownership import ProfileLease, ProfileLeaseError
            lease1 = ProfileLease(tmp_dir, 'uuid-test-1')
            self.assertTrue(lease1.acquire())

            # Attempting second lease on same directory should raise ProfileLeaseError
            lease2 = ProfileLease(tmp_dir, 'uuid-test-2')
            with self.assertRaises(ProfileLeaseError):
                lease2.acquire()

            # After releasing lease1, lease2 should be able to acquire
            lease1.release()
            self.assertTrue(lease2.acquire())
            lease2.release()

    def test_profile_lease_context_manager(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            from profile_ownership import ProfileLease, ProfileLeaseError
            with ProfileLease(tmp_dir, 'uuid-ctx-1'):
                with self.assertRaises(ProfileLeaseError):
                    with ProfileLease(tmp_dir, 'uuid-ctx-2'):
                        pass


if __name__ == '__main__':
    unittest.main()
