import json
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import requests

from packaging.version import Version, InvalidVersion

from version import __version__, APP_NAME, RELEASE_ASSET_PREFIX
from updater_config import load_updater_config, update_updater_config, is_configured


DEFAULT_RELEASE_NOTES_VI = (
    "Bản cập nhật này bao gồm các cải thiện về độ ổn định, hiệu năng "
    "và trải nghiệm sử dụng."
)


def normalize_release_notes(notes, max_length=6000):
    """Convert trusted release Markdown to concise text suitable for Tk widgets."""
    text = str(notes or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return DEFAULT_RELEASE_NOTES_VI
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>\n]+>", "", text)
    text = text.replace("```", "")
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"^[*-]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return DEFAULT_RELEASE_NOTES_VI
    if len(text) > max_length:
        text = text[:max_length].rstrip() + "\n\n…"
    return text


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
        if not isinstance(release, dict):
            return {"has_update": False, "error": "GitHub trả dữ liệu release không hợp lệ."}
        tag = (release.get("tag_name") or "").strip()
        has, latest_str, current_str = self.compare_versions(tag)
        release_metadata = {
            "release_name": str(release.get("name") or tag or "Bản cập nhật"),
            "release_notes": normalize_release_notes(release.get("body")),
            "release_url": str(release.get("html_url") or ""),
            "published_at": str(release.get("published_at") or ""),
        }
        if latest_str is None or current_str is None:
            return {
                "has_update": False,
                "error": f"Tag release không hợp lệ: {tag or '(trống)'}",
                **release_metadata,
            }
        if not has:
            return {
                "has_update": False,
                "latest_version": latest_str,
                "current_version": current_str,
                "current": True,
                "error": None,
                **release_metadata,
            }
        asset = self.find_windows_asset(release)
        if not asset:
            return {
                "has_update": True,
                "latest_version": latest_str,
                "current_version": current_str,
                "error": f"Không tìm thấy file .zip cho Windows trong release {tag}",
                "asset_url": None,
                **release_metadata,
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
            **release_metadata,
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
        extract_dir.mkdir(parents=True, exist_ok=True)
        root = extract_dir.resolve()
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                target = (root / member.filename).resolve()
                if os.path.commonpath([str(root), str(target)]) != str(root):
                    raise RuntimeError(f"Gói cập nhật chứa đường dẫn không an toàn: {member.filename}")
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
            "title Cập nhật TikTok Auto Uploader\n"
            "setlocal\n"
            "set \"BACKED_INTERNAL=0\"\n"
            "set \"BACKED_EXE=0\"\n"
            "echo Đang chờ ứng dụng đóng...\n"
            "timeout /t 3 /nobreak >nul\n"
            "echo Đang thay thế tệp ứng dụng...\n"
            f'set "APP_DIR=%~dp0"\n'
            f'set "NEW_DIR={extracted_app_dir.resolve()}"\n'
            f'if exist "%APP_DIR%_internal_backup" rmdir /s /q "%APP_DIR%_internal_backup"\n'
            f'if exist "%APP_DIR%{APP_NAME}.exe.backup" del "%APP_DIR%{APP_NAME}.exe.backup"\n'
            f'if not exist "%APP_DIR%_internal" goto rollback\n'
            f'if not exist "%APP_DIR%{APP_NAME}.exe" goto rollback\n'
            f'rename "%APP_DIR%_internal" "_internal_backup" || goto rollback\n'
            f'set "BACKED_INTERNAL=1"\n'
            f'rename "%APP_DIR%{APP_NAME}.exe" "{APP_NAME}.exe.backup" || goto rollback\n'
            f'set "BACKED_EXE=1"\n'
            f'xcopy /s /e /y /i "%NEW_DIR%\\_internal" "%APP_DIR%_internal\\" >nul || goto rollback\n'
            f'copy /y "%NEW_DIR%\\{APP_NAME}.exe" "%APP_DIR%{APP_NAME}.exe" >nul || goto rollback\n'
            f'if not exist "%APP_DIR%_internal" goto rollback\n'
            f'if not exist "%APP_DIR%{APP_NAME}.exe" goto rollback\n'
            f'echo Đang khởi động phiên bản mới...\n'
            f'start "" "%APP_DIR%{APP_NAME}.exe" || goto rollback\n'
            f'timeout /t 5 /nobreak >nul\n'
            f'rmdir /s /q "%APP_DIR%_internal_backup" 2>nul\n'
            f'del "%APP_DIR%{APP_NAME}.exe.backup" 2>nul\n'
            f'del "%APP_DIR%update_error.txt" 2>nul\n'
            f'del "%~f0"\n'
            f'exit /b 0\n'
            f':rollback\n'
            f'echo Không thể cài đặt bản cập nhật. Đang khôi phục phiên bản cũ...\n'
            f'if "%BACKED_EXE%"=="1" (\n'
            f'  del "%APP_DIR%{APP_NAME}.exe" 2>nul\n'
            f'  rename "%APP_DIR%{APP_NAME}.exe.backup" "{APP_NAME}.exe" 2>nul\n'
            f')\n'
            f'if "%BACKED_INTERNAL%"=="1" (\n'
            f'  rmdir /s /q "%APP_DIR%_internal" 2>nul\n'
            f'  rename "%APP_DIR%_internal_backup" "_internal" 2>nul\n'
            f')\n'
            f'echo Cập nhật thất bại. Ứng dụng đã khôi phục phiên bản trước.> "%APP_DIR%update_error.txt"\n'
            f'if exist "%APP_DIR%{APP_NAME}.exe" start "" "%APP_DIR%{APP_NAME}.exe"\n'
            f'del "%~f0"\n'
            f'exit /b 1\n'
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
    config = load_updater_config()
    if not config.get("auto_check", True):
        return None
    if int(config.get("remind_after_epoch", 0) or 0) > int(time.time()):
        return None
    skip = str(config.get("skip_version", "")).strip()

    def _run():
        try:
            updater = GitHubReleaseUpdater(app_root, repo_owner, repo_name, token=token)
            result = updater.check_update()
            update_updater_config(last_check_epoch=int(time.time()))
            latest = result.get("latest_version", "")
            if result.get("has_update") and latest and skip == latest:
                return
            if schedule:
                schedule(lambda: _background_check_callback(result, on_update, on_error, on_current))
            else:
                _background_check_callback(result, on_update, on_error, on_current)
        except Exception as error:
            if on_error:
                message = str(error)
                callback = lambda message=message: on_error(message)
                if schedule:
                    schedule(callback)
                else:
                    callback()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
