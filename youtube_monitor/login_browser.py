"""Open one persistent, user-controlled browser for YouTube sign-in."""

import os
import subprocess
import threading
import time
from pathlib import Path


YOUTUBE_LOGIN_URL = "https://www.youtube.com/account"


class YouTubeLoginBrowser:
    def __init__(self, profile_dir, executable_resolver=None, process_factory=None):
        self.profile_dir = Path(profile_dir)
        self._resolve = executable_resolver or self._default_resolver
        self._popen = process_factory or subprocess.Popen
        self._process = None
        self._lock = threading.Lock()

    @staticmethod
    def _default_resolver():
        # Interactive Google sign-in should use a normal installed browser.
        # Bundled anti-detect engines may require their automation runtime and
        # can crash when launched as a standalone executable.
        candidates = [
            Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        import browser_patchright_glue as browser_glue
        return browser_glue.resolve_browser_executable(profile_name="YouTube Login")

    def is_open(self):
        process = self._process
        return process is not None and process.poll() is None

    def open(self):
        with self._lock:
            if self.is_open():
                return True, "Browser đăng nhập YouTube đang mở."
            executable = self._resolve()
            if not executable or not Path(executable).is_file():
                return False, "Không tìm thấy Chrome/browser để đăng nhập YouTube."
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            args = [
                str(executable),
                f"--user-data-dir={self.profile_dir}",
                "--profile-directory=Default",
                "--new-window",
                "--no-first-run",
                YOUTUBE_LOGIN_URL,
            ]
            try:
                self._process = self._popen(args, shell=False)
            except Exception as exc:
                self._process = None
                return False, f"Không mở được browser YouTube: {exc}"
            # Popen success only means Windows accepted the command. Detect an
            # immediate browser crash before telling the user it opened.
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                exit_code = self._process.poll()
                if exit_code is not None:
                    self._process = None
                    return False, f"Browser YouTube thoát ngay khi khởi động (mã {exit_code})."
                time.sleep(0.1)
            return True, "Đã mở browser riêng. Hãy đăng nhập YouTube và bật chuông kênh ở chế độ Tất cả."
