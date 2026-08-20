"""
taskbar_manager.py - Windows Taskbar Window Isolation & AUMID Management.

Provides:
- Unique AppUserModelID (AUMID) assignment per profile window via Win32 Shell API.
- Prevents Windows Taskbar from grouping/collapsing different browser profiles into a single icon stack.
- Allows each browser profile to appear as a distinct, standalone item on the taskbar.
"""

import ctypes
import os
import sys
import threading
import time
from typing import List, Optional

if sys.platform == "win32":
    from ctypes import wintypes, Structure, c_wchar_p, c_ushort, c_ulong, byref, c_void_p, cast, POINTER

    class PROPVARIANT(Structure):
        _fields_ = [
            ("vt", c_ushort),
            ("wReserved1", c_ushort),
            ("wReserved2", c_ushort),
            ("wReserved3", c_ushort),
            ("pwszVal", c_wchar_p),
        ]

    class PROPERTYKEY(Structure):
        _fields_ = [
            ("fmtid_Data1", c_ulong),
            ("fmtid_Data2", c_ushort),
            ("fmtid_Data3", c_ushort),
            ("fmtid_Data4", ctypes.c_ubyte * 8),
            ("pid", c_ulong),
        ]

    # PKEY_AppUserModel_ID GUID: {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, PID: 5
    PKEY_AppUserModel_ID = PROPERTYKEY(
        0x9F4C2855,
        0x9F79,
        0x4B39,
        (ctypes.c_ubyte * 8)(0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3),
        5,
    )

    # IPropertyStore GUID: {886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}
    class GUID(Structure):
        _fields_ = [
            ("Data1", c_ulong),
            ("Data2", c_ushort),
            ("Data3", c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    IID_IPropertyStore = GUID(
        0x886d8eeb,
        0x8cf2,
        0x4446,
        (ctypes.c_ubyte * 8)(0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99),
    )


def set_window_app_user_model_id(hwnd: int, app_id: str) -> bool:
    """Set the explicit Application User Model ID (AUMID) on a specific window HWND."""
    if sys.platform != "win32" or not hwnd:
        return False

    try:
        shell32 = ctypes.windll.shell32
        p_store = c_void_p()
        hr = shell32.SHGetPropertyStoreForWindow(
            wintypes.HWND(hwnd),
            byref(IID_IPropertyStore),
            byref(p_store),
        )
        if hr != 0 or not p_store:
            return False

        pv = PROPVARIANT()
        pv.vt = 31  # VT_LPWSTR
        pv.pwszVal = str(app_id)

        vtbl_ptr = cast(p_store, POINTER(POINTER(c_void_p)))
        vtbl = vtbl_ptr.contents

        # SetValue (method index 6)
        SetValueFunc = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))
        set_val = SetValueFunc(vtbl[6])
        hr_set = set_val(p_store, byref(PKEY_AppUserModel_ID), byref(pv))

        # Commit (method index 7)
        CommitFunc = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p)
        commit = CommitFunc(vtbl[7])
        hr_commit = commit(p_store)

        # Release (method index 2)
        ReleaseFunc = ctypes.WINFUNCTYPE(c_ulong, c_void_p)
        rel = ReleaseFunc(vtbl[2])
        rel(p_store)

        return hr_set == 0 and hr_commit == 0
    except Exception:
        return False


def find_hwnds_for_pids(pids: List[int]) -> List[int]:
    """Find all visible top-level window HWNDs created by given process IDs."""
    if sys.platform != "win32" or not pids:
        return []

    pid_set = set(pids)
    hwnds = []

    def _enum_cb(hwnd, lparam):
        wpid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, byref(wpid))
        if wpid.value in pid_set and ctypes.windll.user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    try:
        ctypes.windll.user32.EnumWindows(EnumProc(_enum_cb), 0)
    except Exception:
        pass
    return hwnds


def isolate_profile_windows(profile_name: str, pids: Optional[List[int]] = None) -> int:
    """Isolate taskbar grouping for windows of a profile by setting a unique AUMID.
    STRICTLY targets only browser processes inside the application's Browser/ directory
    and NEVER modifies system or personal Google Chrome windows.
    """
    if sys.platform != "win32":
        return 0

    clean_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(profile_name or "default"))
    unique_aumid = f"DONGLAO.Profile.{clean_name}"

    target_pids = list(pids) if pids else []
    if not target_pids:
        try:
            import psutil
            from browser_engine_manager import get_browser_root_dir
            b_root = str(get_browser_root_dir().resolve()).lower()

            for pr in psutil.process_iter(["pid", "name", "exe"]):
                exe_path = str(pr.info.get("exe") or "").lower()
                if not exe_path or not b_root:
                    continue
                # Explicit safety guard: NEVER touch system or user personal Chrome
                if "program files" in exe_path or "google\\chrome" in exe_path:
                    continue
                # Only match processes originating from our Browser directory
                if b_root in exe_path:
                    if f"donglao_{clean_name}.exe" in exe_path or f"donglao" in exe_path or "144.0" in exe_path:
                        target_pids.append(pr.info["pid"])
        except Exception:
            pass

    hwnds = find_hwnds_for_pids(target_pids)
    applied_count = 0
    for hwnd in hwnds:
        if set_window_app_user_model_id(hwnd, unique_aumid):
            applied_count += 1
    return applied_count


def schedule_taskbar_isolation(profile_name: str, retries: int = 3, interval: float = 0.8) -> None:
    """Run window isolation asynchronously in a background thread to catch newly spawned browser windows."""
    if sys.platform != "win32":
        return

    def _worker():
        for _ in range(retries):
            time.sleep(interval)
            try:
                isolated = isolate_profile_windows(profile_name)
                if isolated > 0:
                    break
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True, name=f"TaskbarIsolator-{profile_name}")
    t.start()
