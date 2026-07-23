import hashlib
import os
import re
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path

import requests


_ffmpeg_check_lock = threading.Lock()
_ffmpeg_install_lock = threading.Lock()
_encoder_cache = None


def _app_root():
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


FFMPEG_DIR = _app_root() / "FFmpeg"
FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe"
FFPROBE_EXE = FFMPEG_DIR / "ffprobe.exe"
GYAN_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
GYAN_SHA256_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256"


def _bundled_ffmpeg():
    p = Path(_app_root() / "_internal" / "FFmpeg" / "ffmpeg.exe")
    return p if p.exists() else None


def _bundled_ffprobe():
    p = Path(_app_root() / "_internal" / "FFmpeg" / "ffprobe.exe")
    return p if p.exists() else None


def find_ffmpeg():
    bundled = _bundled_ffmpeg()
    if bundled:
        return bundled
    if FFMPEG_EXE.exists():
        return FFMPEG_EXE
    which = shutil.which("ffmpeg")
    if which:
        return Path(which)
    return None


def find_ffprobe():
    bundled = _bundled_ffprobe()
    if bundled:
        return bundled
    if FFPROBE_EXE.exists():
        return FFPROBE_EXE
    which = shutil.which("ffprobe")
    if which:
        return Path(which)
    return None


def ffmpeg_source():
    if _bundled_ffmpeg():
        return "Bundled"
    if FFMPEG_EXE.exists():
        return "App dir"
    p = shutil.which("ffmpeg")
    if p:
        return "System PATH"
    return ""


def ffmpeg_available():
    return find_ffmpeg() is not None


def ffprobe_available():
    return find_ffprobe() is not None


def ffmpeg_path_str():
    p = find_ffmpeg()
    return str(p) if p else ""


def ffprobe_path_str():
    p = find_ffprobe()
    return str(p) if p else ""


def detect_gpu_encoder():
    global _encoder_cache
    if _encoder_cache is not None:
        return _encoder_cache
    with _ffmpeg_check_lock:
        if _encoder_cache is not None:
            return _encoder_cache
        exe = find_ffmpeg()
        if not exe:
            _encoder_cache = "libx264"
            return _encoder_cache
        try:
            p = subprocess.run(
                [str(exe), "-encoders"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            encoders = p.stdout + p.stderr
            for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
                if enc in encoders:
                    _encoder_cache = enc
                    return _encoder_cache
        except Exception:
            pass
        _encoder_cache = "libx264"
        return _encoder_cache


def invalidate_encoder_cache():
    global _encoder_cache
    _encoder_cache = None


def _verify_exe(path):
    if not path or not path.exists():
        return False
    try:
        if path.stat().st_size == 0:
            return False
    except Exception:
        return False
    try:
        p = subprocess.run(
            [str(path), "-version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return p.returncode == 0
    except Exception:
        return False


def check_ffmpeg():
    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()
    if not ffmpeg:
        return False, "Không tìm thấy ffmpeg", ""
    if not ffprobe:
        return False, "Không tìm thấy ffprobe", ""
    if not _verify_exe(ffmpeg):
        return False, "ffmpeg không chạy được", ""
    if not _verify_exe(ffprobe):
        return False, "ffprobe không chạy được", ""
    src = ffmpeg_source()
    return True, f"FFmpeg sẵn sàng ({src})", src


def _validate_sha256_format(hex_str):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", hex_str):
        raise RuntimeError(f"SHA-256 không đúng định dạng (cần 64 ký tự hex)")


def _fetch_sha256(sha256_url):
    sr = requests.get(sha256_url, timeout=10)
    sr.raise_for_status()
    hex_str = sr.text.strip().split()[0]
    _validate_sha256_format(hex_str)
    return hex_str.lower()


def _hash_stream(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def _download_with_sha256(url, sha256_url, dest_path, progress_callback=None):
    if progress_callback:
        progress_callback("Đang tải...", 0.1)
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    part_path = dest_path.with_suffix(dest_path.suffix + ".part")
    try:
        with open(part_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        if progress_callback:
            progress_callback("Đang xác minh...", 0.6)
        sha256_expected = _fetch_sha256(sha256_url)
        actual = _hash_stream(part_path)
        if actual != sha256_expected:
            part_path.unlink(missing_ok=True)
            raise RuntimeError(f"Lỗi xác minh FFmpeg: SHA-256 không khớp")
        os.replace(part_path, dest_path)
        if progress_callback:
            progress_callback("Đã tải xong", 0.7)
    except Exception:
        part_path.unlink(missing_ok=True)
        raise


def download_ffmpeg(progress_callback=None):
    with _ffmpeg_install_lock:
        return _download_ffmpeg_locked(progress_callback)


def _download_ffmpeg_locked(progress_callback=None):
    temp_dir = _app_root() / "temp_dl" / "ffmpeg_dl"
    temp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = temp_dir / "FFmpeg.zip"

    if progress_callback:
        progress_callback("Đang tải FFmpeg...", 0.05)

    _download_with_sha256(GYAN_URL, GYAN_SHA256_URL, zip_path, progress_callback)

    if progress_callback:
        progress_callback("Đang giải nén FFmpeg...", 0.7)

    extract_temp = temp_dir / "extract"
    shutil.rmtree(extract_temp, ignore_errors=True)
    extract_temp.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        root_dir = extract_temp.resolve()
        for name in zf.namelist():
            target = (root_dir / name).resolve()
            if os.path.commonpath([str(root_dir), str(target)]) != str(root_dir):
                shutil.rmtree(extract_temp, ignore_errors=True)
                raise RuntimeError(f"ZIP path traversal detected: {name}")
        zf.extractall(extract_temp)

    exe_src = None
    probe_src = None
    for f in extract_temp.rglob("ffmpeg.exe"):
        exe_src = f
        break
    for f in extract_temp.rglob("ffprobe.exe"):
        probe_src = f
        break

    if not exe_src or not probe_src:
        shutil.rmtree(extract_temp, ignore_errors=True)
        raise RuntimeError("Không tìm thấy ffmpeg.exe hoặc ffprobe.exe trong gói FFmpeg")

    if progress_callback:
        progress_callback("Đang cài đặt FFmpeg...", 0.9)

    dest_dir = _app_root() / "FFmpeg"
    dest_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = temp_dir / "install_staging"
    shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_exe = staging_dir / "ffmpeg.exe"
    staging_probe = staging_dir / "ffprobe.exe"
    shutil.copy2(exe_src, staging_exe)
    shutil.copy2(probe_src, staging_probe)

    if not _verify_exe(staging_exe):
        shutil.rmtree(staging_dir, ignore_errors=True)
        if progress_callback:
            progress_callback("ffmpeg không chạy được", 0.0)
        raise RuntimeError("Bản sao ffmpeg không chạy được")
    if not _verify_exe(staging_probe):
        shutil.rmtree(staging_dir, ignore_errors=True)
        if progress_callback:
            progress_callback("ffprobe không chạy được", 0.0)
        raise RuntimeError("Bản sao ffprobe không chạy được")

    old_exe = dest_dir / "ffmpeg.exe"
    old_probe = dest_dir / "ffprobe.exe"
    old_exe_backup = dest_dir / "ffmpeg.exe.bak"
    old_probe_backup = dest_dir / "ffprobe.exe.bak"
    has_old = old_exe.exists() or old_probe.exists()
    old_exe_backup.unlink(missing_ok=True)
    old_probe_backup.unlink(missing_ok=True)
    if old_exe.exists():
        shutil.move(str(old_exe), str(old_exe_backup))
    if old_probe.exists():
        shutil.move(str(old_probe), str(old_probe_backup))
    try:
        shutil.copy2(staging_exe, old_exe)
        shutil.copy2(staging_probe, old_probe)
        if not _verify_exe(old_exe) or not _verify_exe(old_probe):
            raise RuntimeError("Xác minh sau cài đặt thất bại")
        old_exe_backup.unlink(missing_ok=True)
        old_probe_backup.unlink(missing_ok=True)
    except Exception:
        if old_exe_backup.exists():
            shutil.move(str(old_exe_backup), str(old_exe))
        if old_probe_backup.exists():
            shutil.move(str(old_probe_backup), str(old_probe))
        elif not has_old:
            old_exe.unlink(missing_ok=True)
            old_probe.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(extract_temp, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
    invalidate_encoder_cache()

    if progress_callback:
        progress_callback("FFmpeg đã sẵn sàng", 1.0)


def ensure_ffmpeg(progress_callback=None):
    ok, msg, src = check_ffmpeg()
    if ok:
        return True, msg
    try:
        download_ffmpeg(progress_callback)
        ok2, msg2, _ = check_ffmpeg()
        return ok2, msg2
    except Exception as e:
        return False, str(e).strip() or "Lỗi không xác định"


def run_ffprobe(args):
    exe = find_ffprobe()
    if not exe:
        return None
    try:
        p = subprocess.run(
            [str(exe)] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:
        return None


def run_ffmpeg(args, timeout=300):
    exe = find_ffmpeg()
    if not exe:
        return None, "FFmpeg not found"
    try:
        p = subprocess.run(
            [str(exe)] + args,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return p, p.stderr[:500] if p.returncode != 0 else ""
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, str(e)


def probe_duration(path):
    out = run_ffprobe([
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return float(out) if out else None


def has_audio(path):
    out = run_ffprobe([
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        str(path),
    ])
    return bool(out)
