import json
import os
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import requests

from packaging.version import Version, InvalidVersion

from version import __version__, APP_NAME, RELEASE_ASSET_PREFIX
from updater_config import load_updater_config, save_updater_config, is_configured


class GitHubReleaseUpdater:
    API_BASE = "https://api.github.com"

    def __init__(self, app_root, repo_owner, repo_name, token="", log_func=None):
        self.app_root = Path(app_root)
        self.repo_owner = str(repo_owner or "").strip()
        self.repo_name = str(repo_name or "").strip()
        self.token = str(token or "").strip()
        self.log = log_func or (lambda msg: None)

    def _api_headers(self):
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _log(self, msg):
        try:
            self.log(msg)
        except Exception:
            pass

    def get_latest_release(self):
        url = f"{self.API_BASE}/repos/{self.repo_owner}/{self.repo_name}/releases/latest"
        resp = requests.get(url, headers=self._api_headers(), timeout=15)
        if resp.status_code == 404:
            return None, "Không tìm thấy release nào."
        if resp.status_code == 401:
            return None, "Token GitHub không hợp lệ hoặc hết hạn."
        if resp.status_code != 200:
            return None, f"GitHub API lỗi {resp.status_code}: {resp.reason}"
        try:
            return resp.json(), None
        except Exception as e:
            return None, f"Lỗi parse response: {e}"

    def find_windows_asset(self, release):
        if not release or not isinstance(release, dict):
            return None
        for asset in (release.get("assets") or []):
            name = (asset.get("name") or "").lower()
            if name.startswith(RELEASE_ASSET_PREFIX.lower()) and name.endswith(".zip"):
                if asset.get("browser_download_url"):
                    return asset
        return None

    def compare_versions(self, latest_tag):
        tag = str(latest_tag or "").strip().lstrip("v")
        try:
            latest_v = Version(tag)
            current_v = Version(__version__)
        except InvalidVersion:
            return False, None, None
        if latest_v > current_v:
            return True, str(latest_v), str(current_v)
        return False, str(latest_v), str(current_v)

    def check_update(self):
        release, error = self.get_latest_release()
        if error:
            return {"has_update": False, "error": error}
        tag = (release.get("tag_name") or "").strip()
        has, latest_str, current_str = self.compare_versions(tag)
        if not has:
            return {
                "has_update": False,
                "latest_version": latest_str,
                "current_version": current_str,
                "current": True,
                "error": None,
            }
        asset = self.find_windows_asset(release)
        if not asset:
            return {
                "has_update": True,
                "latest_version": latest_str,
                "current_version": current_str,
                "error": f"Không tìm thấy file .zip cho Windows trong release {tag}",
                "asset_url": None,
            }
        return {
            "has_update": True,
            "latest_version": latest_str,
            "current_version": current_str,
            "tag": tag,
            "asset_name": asset.get("name"),
            "asset_url": asset.get("browser_download_url"),
            "asset_size": asset.get("size", 0),
            "error": None,
        }

    def download_asset(self, asset_url, dest_file, progress_callback=None):
        headers = self._api_headers()
        headers["Accept"] = "application/octet-stream"
        resp = requests.get(asset_url, headers=headers, stream=True, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Tải về thất bại: HTTP {resp.status_code}")
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_file, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        progress_callback(downloaded / total)
        return dest_file

    def extract_update(self, zip_path, extract_dir):
        shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        return extract_dir

    def validate_package(self, extracted_dir):
        exe_path = extracted_dir / f"{APP_NAME}.exe"
        internal_path = extracted_dir / "_internal"
        return exe_path.exists() and internal_path.is_dir()

    def write_update_script(self, extracted_app_dir):
        script_path = self.app_root / "update.bat"
        content = (
            "@echo off\n"
            "chcp 65001 >nul\n"
            "title Updating TikTok Auto Uploader...\n"
            "echo Waiting for app to exit...\n"
            "timeout /t 3 /nobreak >nul\n"
            "echo Replacing files...\n"
            f'set "APP_DIR=%~dp0"\n'
            f'set "NEW_DIR={extracted_app_dir.resolve()}"\n'
            f'if exist "%APP_DIR%_internal_backup" rmdir /s /q "%APP_DIR%_internal_backup"\n'
            f'if exist "%APP_DIR%{APP_NAME}.exe.backup" del "%APP_DIR%{APP_NAME}.exe.backup"\n'
            f'rename "%APP_DIR%_internal" "_internal_backup" 2>nul\n'
            f'rename "%APP_DIR%{APP_NAME}.exe" "{APP_NAME}.exe.backup" 2>nul\n'
            f'xcopy /s /e /y "%NEW_DIR%\\_internal" "%APP_DIR%_internal\\" >nul\n'
            f'copy /y "%NEW_DIR%\\{APP_NAME}.exe" "%APP_DIR%{APP_NAME}.exe" >nul\n'
            f'echo Starting app...\n'
            f'start "" "%APP_DIR%{APP_NAME}.exe"\n'
            f'echo Cleaning up...\n'
            f'timeout /t 2 /nobreak >nul\n'
            f'rmdir /s /q "%APP_DIR%_internal_backup" 2>nul\n'
            f'del "%APP_DIR%{APP_NAME}.exe.backup" 2>nul\n'
            f'del "%~f0"\n'
        )
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(content)
        return script_path

    def launch_update(self, script_path):
        subprocess.Popen(
            [str(script_path)],
            shell=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )


def get_current_version():
    return __version__


def _background_check_callback(result, on_update, on_error, on_current):
    if not result:
        return
    err = result.get("error")
    if err:
        if on_error:
            on_error(err)
        return
    if result.get("current"):
        if on_current:
            on_current(result.get("current_version", "?"))
        return
    if result.get("has_update") and on_update:
        on_update(result)


def run_background_check(repo_owner, repo_name, token, app_root, on_update, on_error, on_current, schedule=None):
    cfg = load_updater_config()
    now_epoch = int(time.time())
    last_check = int(cfg.get("last_check_epoch", 0))
    skip = str(cfg.get("skip_version", "")).strip()
    if not is_configured(cfg):
        return
    if now_epoch - last_check < 21600:
        return
    cfg["last_check_epoch"] = now_epoch
    save_updater_config(cfg)

    def _run():
        try:
            updater = GitHubReleaseUpdater(app_root, repo_owner, repo_name, token=token)
            result = updater.check_update()
            latest = result.get("latest_version", "")
            if skip == latest and not result.get("current"):
                return
            if schedule:
                schedule(lambda: _background_check_callback(result, on_update, on_error, on_current))
            else:
                _background_check_callback(result, on_update, on_error, on_current)
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
