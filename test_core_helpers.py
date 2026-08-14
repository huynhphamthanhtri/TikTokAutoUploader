import unittest
import tempfile
from pathlib import Path

from core_helpers import (
    copy_video_atomically,
    normalize_profile_path,
    parse_cookie,
    parse_proxy_string,
    process_uses_profile,
)


class CoreHelperTests(unittest.TestCase):
    def test_parse_cookie_supports_json_and_cookie_header(self):
        self.assertEqual(
            parse_cookie('[{"name":"sid","value":"abc","domain":".tiktok.com"}]')[0]['domain'],
            '.tiktok.com',
        )
        self.assertEqual(
            parse_cookie('[{"name":"sid","value":"abc","domain":"tiktok.com"}]')[0]['domain'],
            '.tiktok.com',
        )
        self.assertEqual(
            parse_cookie('[{"name":"sid","value":"abc","domain":"www.tiktok.com"}]')[0]['domain'],
            'www.tiktok.com',
        )
        parsed = parse_cookie('sid=abc; theme=dark')
        self.assertEqual([(cookie['name'], cookie['value']) for cookie in parsed], [('sid', 'abc'), ('theme', 'dark')])
        self.assertEqual(parsed[0]['domain'], '.tiktok.com')

    def test_parse_proxy_string_supports_plain_and_authenticated_proxy(self):
        self.assertEqual(
            parse_proxy_string('127.0.0.1:8080'),
            {'ip': '127.0.0.1', 'port': '8080', 'user': '', 'pass': ''},
        )
        self.assertEqual(
            parse_proxy_string('http://127.0.0.1:8080:user:secret'),
            {'ip': '127.0.0.1', 'port': '8080', 'user': 'user', 'pass': 'secret'},
        )

    def test_empty_profile_path_stays_empty(self):
        self.assertEqual(normalize_profile_path(""), "")

    def test_profile_path_matching_ignores_case_and_trailing_separator(self):
        self.assertEqual(
            normalize_profile_path(r'C:\Users\Admin\Profile1'),
            normalize_profile_path('c:\\users\\admin\\Profile1\\'),
        )

    def test_process_uses_profile_handles_quoted_argument(self):
        command = [r'chrome.exe', r'--user-data-dir="C:\Users\Admin\Profile1"']
        self.assertTrue(process_uses_profile(command, r'C:\Users\Admin\Profile1'))
        self.assertFalse(process_uses_profile(command, r'C:\Users\Admin\Profile2'))

    def test_copy_video_atomically_preserves_source_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / 'source.mp4'
            destination = Path(temp_dir) / 'Video' / 'round-01.mp4'
            source.write_bytes(b'video-data')
            result = copy_video_atomically(source, destination)
            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), b'video-data')
            self.assertEqual(source.read_bytes(), b'video-data')
            self.assertFalse((destination.parent / f'.{destination.name}.part').exists())

if __name__ == '__main__':
    unittest.main()
