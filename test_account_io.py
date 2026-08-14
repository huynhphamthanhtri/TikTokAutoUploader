import unittest

from account_io import (
    DEFAULT_FIELDS,
    DEFAULT_FORMAT,
    LEGACY_FORMAT,
    apply_update_to_config,
    build_record,
    masked_record,
    parse_data_into_records,
    parse_format,
    parse_rows,
    plan_import,
    record_from_config,
    serialize_records,
)


class ParseFormatTests(unittest.TestCase):
    def test_default_format_parses(self):
        self.assertEqual(parse_format(DEFAULT_FORMAT), list(DEFAULT_FIELDS))

    def test_custom_order_and_subset(self):
        fields = parse_format('email|password|cookie|proxy|name')
        self.assertEqual(fields, ['email', 'password', 'cookie', 'proxy', 'name'])

    def test_legacy_format_alias(self):
        fields = parse_format(LEGACY_FORMAT)
        self.assertEqual(fields, ['name', 'cookie', 'proxy', 'idTiktok'])

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValueError):
            parse_format('name|nonexistent')

    def test_duplicate_field_rejected(self):
        with self.assertRaises(ValueError):
            parse_format('name|name|email')

    def test_empty_format_rejected(self):
        with self.assertRaises(ValueError):
            parse_format('   ')


class ParseRowsTests(unittest.TestCase):
    def test_simple_rows(self):
        rows = parse_rows('a|b|c\n1|2|3')
        self.assertEqual([cells for _, cells in rows], [['a', 'b', 'c'], ['1', '2', '3']])

    def test_pipe_inside_quotes(self):
        rows = parse_rows('account|"pass|word"|mail@x.com')
        self.assertEqual(rows[0][1], ['account', 'pass|word', 'mail@x.com'])

    def test_escaped_quotes(self):
        rows = parse_rows('note|"say ""hi"""')
        self.assertEqual(rows[0][1], ['note', 'say "hi"'])

    def test_cookie_json_is_not_mistreated_as_quoted(self):
        cookie = '[{"name":"sessionid","value":"abc","domain":"tiktok.com"}]'
        rows = parse_rows(f'a|{cookie}|b')
        self.assertEqual(rows[0][1], ['a', cookie, 'b'])

    def test_unicode_preserved(self):
        rows = parse_rows('tên|mật khẩu|ghi chú ơi')
        self.assertEqual(rows[0][1], ['tên', 'mật khẩu', 'ghi chú ơi'])

    def test_multiline_quoted_field(self):
        rows = parse_rows('note|"line1\nline2"|tail')
        self.assertEqual(rows[0][1], ['note', 'line1\nline2', 'tail'])

    def test_blank_lines_skipped_by_parse_data(self):
        records, errors = parse_data_into_records('a|b\n\n\nc|d', ['name', 'email'])
        self.assertEqual(len(records), 2)
        self.assertEqual(errors, [])


class BuildRecordTests(unittest.TestCase):
    def test_full_record(self):
        fields = list(DEFAULT_FIELDS)
        values = ['acc1', 'a@x.com', 'pw', 'user1', '1.2.3.4:80', 'k=v', 'secret', 'pm', 'b@x.com', 'pbm', 'ghi']
        record = build_record(fields, values)
        self.assertEqual(record['name'], 'acc1')
        self.assertEqual(record['email'], 'a@x.com')
        self.assertEqual(record['password'], 'pw')
        self.assertEqual(record['tiktok_id'], 'user1')
        self.assertEqual(record['proxy_string'], '1.2.3.4:80')
        self.assertEqual(record['cookie_str'], 'k=v')
        self.assertEqual(record['auth2fa'], 'secret')
        self.assertEqual(record['passmail'], 'pm')
        self.assertEqual(record['mail_backup'], 'b@x.com')
        self.assertEqual(record['pass_mail_backup'], 'pbm')
        self.assertEqual(record['note'], 'ghi')

    def test_empty_fields_are_omitted(self):
        record = build_record(['name', 'email', 'password'], ['acc', '', 'pw'])
        self.assertNotIn('email', record)
        self.assertEqual(record['password'], 'pw')

    def test_missing_columns_are_padded(self):
        record = build_record(['name', 'email', 'password'], ['acc'])
        self.assertEqual(record['name'], 'acc')
        self.assertNotIn('email', record)


class RoundTripTests(unittest.TestCase):
    def test_export_import_round_trip(self):
        record = {
            'name': 'acc1',
            'email': 'a@x.com',
            'password': 'pa|ss"word',
            'tiktok_id': 'user1',
            'proxy_string': '1.2.3.4:80:u:p',
            'cookie_str': '[{"name":"sessionid","value":"abc","domain":"tiktok.com"}]',
            'auth2fa': 'secret',
            'passmail': 'pm',
            'mail_backup': 'b@x.com',
            'pass_mail_backup': 'pbm',
            'note': 'ghi\nchú',
        }
        text = serialize_records(list(DEFAULT_FIELDS), [record])
        records, errors = parse_data_into_records(text, list(DEFAULT_FIELDS))
        self.assertEqual(errors, [])
        self.assertEqual(records[0], record)

    def test_multiple_records_round_trip(self):
        records = [
            {'name': 'a', 'email': 'a@x.com', 'password': 'pw1'},
            {'name': 'b', 'email': 'b@x.com', 'password': 'pw2', 'note': 'n'},
        ]
        fields = ['name', 'email', 'password', 'note']
        text = serialize_records(fields, records)
        parsed, errors = parse_data_into_records(text, fields)
        self.assertEqual(errors, [])
        self.assertEqual(parsed, records)


class PlanImportTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {'name': 'new1', 'email': 'a@x.com'},
            {'name': 'existing', 'email': 'b@x.com'},
        ]

    def test_policy_skip(self):
        plans = plan_import(self.records, ['existing'], 'skip')
        self.assertEqual([p['action'] for p in plans], ['add', 'skip'])

    def test_policy_update(self):
        plans = plan_import(self.records, ['existing'], 'update')
        self.assertEqual([p['action'] for p in plans], ['add', 'update'])

    def test_policy_error(self):
        plans = plan_import(self.records, ['existing'], 'error')
        self.assertEqual([p['action'] for p in plans], ['add', 'error'])

    def test_duplicate_within_batch_second_is_skipped_by_default(self):
        records = [
            {'name': 'new1', 'email': 'a@x.com'},
            {'name': 'new1', 'email': 'b@x.com'},
        ]
        plans = plan_import(records, [], 'skip')
        self.assertEqual([p['action'] for p in plans], ['add', 'skip'])

    def test_duplicate_within_batch_second_is_error_policy(self):
        records = [
            {'name': 'new1', 'email': 'a@x.com'},
            {'name': 'new1', 'email': 'b@x.com'},
        ]
        plans = plan_import(records, [], 'error')
        self.assertEqual([p['action'] for p in plans], ['add', 'error'])


class RecordFromConfigTests(unittest.TestCase):
    def test_maps_internal_keys(self):
        config = {
            'email': 'a@x.com',
            'password': 'pw',
            'tiktok_id': 'user1',
            'proxy_string': 'p:80',
            'cookie_str': 'k=v',
            'auth2fa': 's',
            'passmail': 'pm',
            'mail_backup': 'b@x.com',
            'pass_mail_backup': 'pbm',
            'note': 'n',
        }
        record = record_from_config('acc1', config)
        self.assertEqual(record['name'], 'acc1')
        self.assertEqual(record['tiktok_id'], 'user1')
        self.assertEqual(record['cookie_str'], 'k=v')
        self.assertEqual(record['mail_backup'], 'b@x.com')

    def test_missing_keys_default_empty(self):
        record = record_from_config('acc1', {'cookie_str': 'k=v'})
        self.assertEqual(record['name'], 'acc1')
        self.assertEqual(record['cookie_str'], 'k=v')
        self.assertEqual(record['email'], '')
        self.assertEqual(record['password'], '')


class ApplyUpdateTests(unittest.TestCase):
    def test_empty_fields_keep_old_values(self):
        config = {'email': 'old@x.com', 'password': 'oldpw', 'tiktok_id': 'u1', 'cookie_str': 'k=v'}
        record = {'name': 'acc', 'email': 'new@x.com', 'password': ''}
        updated = apply_update_to_config(config, record)
        self.assertEqual(updated['email'], 'new@x.com')
        self.assertEqual(updated['password'], 'oldpw')
        self.assertEqual(updated['tiktok_id'], 'u1')

    def test_name_never_overwrites(self):
        config = {'name_field': 'x', 'email': 'a@x.com'}
        record = {'name': 'other', 'email': 'b@x.com'}
        updated = apply_update_to_config(config, record)
        self.assertEqual(updated['name_field'], 'x')
        self.assertEqual(updated['email'], 'b@x.com')

    def test_use_proxy_and_proxy_type_derived(self):
        config = {'use_proxy': False, 'proxy_type': 'http'}
        updated = apply_update_to_config(config, {'proxy_string': '1.2.3.4:80'}, 'socks5')
        self.assertTrue(updated['use_proxy'])
        self.assertEqual(updated['proxy_type'], 'socks5')

    def test_no_proxy_keeps_existing_type(self):
        config = {'use_proxy': True, 'proxy_type': 'socks5', 'proxy_string': '1.2.3.4:80'}
        updated = apply_update_to_config(config, {'email': 'a@x.com'}, 'http')
        self.assertEqual(updated['proxy_type'], 'socks5')

    def test_original_config_not_mutated(self):
        config = {'email': 'old@x.com', 'password': 'oldpw'}
        before = dict(config)
        apply_update_to_config(config, {'email': 'new@x.com'})
        self.assertEqual(config, before)


class MaskedRecordTests(unittest.TestCase):
    def test_sensitive_fields_masked(self):
        record = {'name': 'a', 'password': 'secret', 'cookie_str': 'k=v', 'note': 'n'}
        masked = masked_record(record)
        self.assertEqual(masked['password'], '***')
        self.assertEqual(masked['cookie_str'], '***')
        self.assertEqual(masked['name'], 'a')
        self.assertEqual(masked['note'], 'n')

    def test_long_values_partially_masked(self):
        record = {'password': 'a_very_long_password'}
        masked = masked_record(record)
        self.assertEqual(masked['password'], 'a_v***')


if __name__ == '__main__':
    unittest.main()