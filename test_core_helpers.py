import unittest
import tempfile
from pathlib import Path

from selenium.common.exceptions import InvalidSessionIdException, TimeoutException

from core_helpers import (
    classify_webdriver_error,
    clear_profile_directory,
    copy_video_atomically,
    is_driver_valid,
    normalize_profile_path,
    process_uses_profile,
    profile_driver_path,
)


class CoreHelperTests(unittest.TestCase):
    def test_profile_path_matching_ignores_case_and_trailing_separator(self):
        self.assertEqual(
            normalize_profile_path(r'C:\Users\Admin\Profile1'),
            normalize_profile_path('c:\\users\\admin\\Profile1\\'),
        )

    def test_process_uses_profile_handles_quoted_argument(self):
        command = [r'chrome.exe', r'--user-data-dir="C:\Users\Admin\Profile1"']
        self.assertTrue(process_uses_profile(command, r'C:\Users\Admin\Profile1'))
        self.assertFalse(process_uses_profile(command, r'C:\Users\Admin\Profile2'))

    def test_classifies_session_errors(self):
        self.assertEqual(classify_webdriver_error(InvalidSessionIdException('invalid session id')), 'invalid_session')
        self.assertEqual(classify_webdriver_error(Exception('tab crashed')), 'renderer_crash')
        self.assertEqual(classify_webdriver_error(TimeoutException('timed out')), 'timeout')
        self.assertEqual(classify_webdriver_error(Exception('other webdriver failure')), 'webdriver_error')

    def test_driver_validity_is_false_for_missing_or_closed_driver(self):
        self.assertFalse(is_driver_valid(None))
        self.assertFalse(is_driver_valid(object()))

    def test_profile_driver_path_is_sibling_of_user_data(self):
        path = profile_driver_path(r'C:\Auto_Data\AUTO 1\Profile')
        self.assertEqual(path, Path(r'C:\Auto_Data\AUTO 1\Driver\chromedriver.exe'))
        stale = profile_driver_path(r'C:\Auto_Data\AUTO 1\Profile', r'C:\Shared\chromedriver.exe')
        self.assertEqual(stale, Path(r'C:\Auto_Data\AUTO 1\Driver\chromedriver.exe'))

    def test_clear_profile_directory_preserves_root_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / 'Profile'
            (profile / 'Default').mkdir(parents=True)
            (profile / 'Default' / 'Cookies').write_text('data', encoding='utf-8')
            (profile / 'Preferences').write_text('data', encoding='utf-8')
            clear_profile_directory(profile)
            self.assertTrue(profile.is_dir())
            self.assertEqual(list(profile.iterdir()), [])

    def test_clear_profile_directory_rejects_unexpected_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                clear_profile_directory(Path(temp_dir) / 'Video')

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
