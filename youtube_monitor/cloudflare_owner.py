"""
youtube_monitor/cloudflare_owner.py - Quản lý vòng đời tiến trình Cloudflare Quick Tunnel (cloudflared.exe):
- Khởi chạy ngầm không cửa sổ (CREATE_NO_WINDOW).
- Chống Zombie Process 100% bằng Windows Job Object (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE).
- Đọc stream bất đồng bộ chống deadlock pipe buffer.
- Bóc tách public URL *.trycloudflare.com chính xác.
- Quản lý metadata quyền sở hữu an toàn trong ownership.json.
"""

import collections
import ctypes
from ctypes import wintypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import psutil

from . import cloudflare_helper

_OWNERSHIP_LOCK = threading.RLock()
_JOB_HANDLE = None
_GLOBAL_PROCESS = None
_LOG_BUFFER = collections.deque(maxlen=200)

CONFIG_DIR = None
OWNERSHIP_JSON = None


def _init_paths():
    global CONFIG_DIR, OWNERSHIP_JSON
    root = Path(__file__).resolve().parent.parent
    CONFIG_DIR = root / "youtube_monitor_cloudflare"
    OWNERSHIP_JSON = CONFIG_DIR / "ownership.json"
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


_init_paths()


def _setup_windows_job_object():
    """Tạo Windows Job Object tự động kill tiến trình con khi tiến trình cha đóng."""
    global _JOB_HANDLE
    if sys.platform != "win32" or _JOB_HANDLE is not None:
        return _JOB_HANDLE

    try:
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hJob = kernel32.CreateJobObjectW(None, None)
        if not hJob:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            hJob,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if ok:
            _JOB_HANDLE = hJob
            return _JOB_HANDLE
        else:
            kernel32.CloseHandle(hJob)
            return None
    except Exception:
        return None


def _assign_process_to_job(proc: subprocess.Popen):
    """Gán tiến trình subprocess vào Windows Job Object."""
    if sys.platform != "win32" or not hasattr(proc, "_handle"):
        return
    job = _setup_windows_job_object()
    if job:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.AssignProcessToJobObject(job, proc._handle)
        except Exception:
            pass


def validate_auth_ready() -> Tuple[bool, str]:
    """Cloudflare Quick Tunnel là Zero-Auth (không cần authtoken). Luôn sẵn sàng."""
    return True, "Cloudflare Quick Tunnel sẵn sàng (Zero-Auth, không cần token)"


def read_ownership() -> Dict[str, Any]:
    with _OWNERSHIP_LOCK:
        if not OWNERSHIP_JSON or not OWNERSHIP_JSON.exists():
            return {}
        try:
            with open(OWNERSHIP_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def write_ownership(record: Dict[str, Any]):
    with _OWNERSHIP_LOCK:
        if not CONFIG_DIR:
            _init_paths()
        try:
            tmp_file = OWNERSHIP_JSON.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            os.replace(tmp_file, OWNERSHIP_JSON)
        except Exception:
            pass


def clear_ownership():
    with _OWNERSHIP_LOCK:
        if OWNERSHIP_JSON and OWNERSHIP_JSON.exists():
            try:
                OWNERSHIP_JSON.unlink()
            except Exception:
                pass


def is_agent_alive() -> bool:
    rec = read_ownership()
    pid = rec.get("agent_pid")
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return False
        # Kiểm tra create_time nếu có
        saved_ct = rec.get("agent_create_time")
        if saved_ct and abs(p.create_time() - float(saved_ct)) > 1.0:
            return False
        # Kiểm tra tên tiến trình
        p_name = p.name().lower()
        return "cloudflared" in p_name
    except Exception:
        return False


def stop_owned_agent(status_callback: Optional[Callable[[str], None]] = None) -> bool:
    """Dừng an toàn tiến trình cloudflared được sở hữu bởi ứng dụng."""
    global _GLOBAL_PROCESS
    with _OWNERSHIP_LOCK:
        rec = read_ownership()
        pid = rec.get("agent_pid")
        proc_ref = _GLOBAL_PROCESS

        if proc_ref is not None:
            try:
                proc_ref.terminate()
                proc_ref.wait(timeout=3)
            except Exception:
                try:
                    proc_ref.kill()
                except Exception:
                    pass
            _GLOBAL_PROCESS = None

        if pid:
            try:
                p = psutil.Process(pid)
                if p.is_running() and "cloudflared" in p.name().lower():
                    saved_ct = rec.get("agent_create_time")
                    if not saved_ct or abs(p.create_time() - float(saved_ct)) <= 1.0:
                        p.terminate()
                        try:
                            p.wait(timeout=3)
                        except Exception:
                            p.kill()
            except psutil.NoSuchProcess:
                pass
            except Exception:
                pass

        clear_ownership()
        if status_callback:
            status_callback("[Cloudflare] Đã dừng tiến trình tunnel an toàn.")
        return True


def start_owned_agent(
    local_port: int,
    callback_instance_id: str = "",
    monitor_gen: int = 0,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, Any]:
    """
    Khởi chạy cloudflared.exe tunnel mở port local_port, bóc tách URL trycloudflare.com.
    Returns (ok, payload) với payload = {"public_url": ..., "agent_pid": ...}
    """
    global _GLOBAL_PROCESS
    with _OWNERSHIP_LOCK:
        # Nếu đang có agent cũ chạy, dừng trước khi khởi chạy mới
        if is_agent_alive():
            stop_owned_agent(status_callback)

        exe_path = cloudflare_helper.ensure_cloudflared(status_callback)
        if not exe_path or not exe_path.exists():
            err_msg = "Không tìm thấy hoặc không thể tải binary cloudflared.exe"
            return False, err_msg

        cmd = [str(exe_path), "tunnel", "--url", f"http://127.0.0.1:{local_port}"]

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
            )
        except Exception as e:
            return False, f"Không thể khởi động cloudflared: {e}"

        _assign_process_to_job(proc)
        _GLOBAL_PROCESS = proc

        public_url_found = threading.Event()
        public_url_holder = [None]
        url_regex = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com(?=[/\s]|$)")

        def _drain_stream(stream, is_stderr=False):
            try:
                for line_bytes in iter(stream.readline, b""):
                    try:
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                    except Exception:
                        line = str(line_bytes)
                    if line:
                        _LOG_BUFFER.append(line)
                        if not public_url_found.is_set():
                            match = url_regex.search(line)
                            if match:
                                public_url_holder[0] = match.group(0).rstrip("/")
                                public_url_found.set()
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, True), daemon=True)
        t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, False), daemon=True)
        t_err.start()
        t_out.start()

        # Đợi bóc tách URL với timeout 25s
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            if public_url_found.wait(timeout=0.5):
                break
            if proc.poll() is not None:
                # Tiến trình crash sớm
                stop_owned_agent()
                err_snippet = "\n".join(list(_LOG_BUFFER)[-5:]) if _LOG_BUFFER else "Exit code: " + str(proc.returncode)
                return False, f"cloudflared thoát đột ngột (code={proc.returncode}): {err_snippet}"

        public_url = public_url_holder[0]
        if not public_url:
            stop_owned_agent()
            err_snippet = "\n".join(list(_LOG_BUFFER)[-5:]) if _LOG_BUFFER else "Timeout 25s"
            return False, f"Hết thời gian chờ nhận URL từ Cloudflare (25s): {err_snippet}"

        # Lấy PID và create_time
        pid = proc.pid
        proc_ct = time.time()
        try:
            proc_ct = psutil.Process(pid).create_time()
        except Exception:
            pass

        rec = {
            "agent_pid": pid,
            "agent_create_time": proc_ct,
            "public_url": public_url,
            "local_port": local_port,
            "instance_id": callback_instance_id,
            "created_at": time.time(),
        }
        write_ownership(rec)

        if status_callback:
            status_callback(f"[Cloudflare] Quick Tunnel sẵn sàng: {public_url}")

        return True, {
            "public_url": public_url,
            "agent_pid": pid,
            "status": "running",
        }
