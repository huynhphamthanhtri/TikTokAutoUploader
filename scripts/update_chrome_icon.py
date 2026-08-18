"""
update_chrome_icon.py - Utility to replace the Windows PE icon of chrome.exe with icon.ico.
"""

import ctypes
import os
import shutil
import struct
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, Optional


def update_executable_icon(
    exe_path: Path,
    ico_path: Path,
    backup: bool = True,
    group_names: Any = "IDR_MAINFRAME",
) -> bool:
    """Replace the main application icon in a Windows PE executable/DLL using an .ico file."""
    if not sys.platform == "win32":
        raise OSError("This tool only supports Windows OS.")

    exe_path = Path(exe_path).resolve()
    ico_path = Path(ico_path).resolve()

    if not exe_path.exists() or not exe_path.is_file():
        raise FileNotFoundError(f"Target executable not found: {exe_path}")
    if not ico_path.exists() or not ico_path.is_file():
        raise FileNotFoundError(f"Icon file not found: {ico_path}")

    # Read .ico bytes
    with open(ico_path, "rb") as f:
        ico_bytes = f.read()

    reserved, ico_type, count = struct.unpack_from("<HHH", ico_bytes, 0)
    if ico_type != 1 or count <= 0:
        raise ValueError(f"Invalid ICO file: type={ico_type}, count={count}")

    # Build GRPICONDIR + GRPICONDIRENTRY structures
    grp_header = bytearray(struct.pack("<HHH", reserved, ico_type, count))
    images = []
    entry_offset = 6
    for i in range(count):
        w, h, colors, res, planes, bpp, size, offset = struct.unpack_from(
            "<BBBBHHII", ico_bytes, entry_offset
        )
        entry_offset += 16
        image_data = ico_bytes[offset : offset + size]
        icon_id = i + 1
        images.append((icon_id, image_data))
        grp_header.extend(
            struct.pack("<BBBBHHIH", w, h, colors, res, planes, bpp, size, icon_id)
        )

    # Optional backup
    if backup:
        backup_file = exe_path.with_name(f"{exe_path.name}.original_backup")
        if not backup_file.exists():
            shutil.copyfile(exe_path, backup_file)

    kernel32 = ctypes.windll.kernel32
    kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
    kernel32.UpdateResourceW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.WORD,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.UpdateResourceW.restype = wintypes.BOOL
    kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    kernel32.EndUpdateResourceW.restype = wintypes.BOOL

    RT_ICON = wintypes.LPCWSTR(3)
    RT_GROUP_ICON = wintypes.LPCWSTR(14)
    LANG_NEUTRAL = 0

    hUpdate = kernel32.BeginUpdateResourceW(str(exe_path), False)
    if not hUpdate:
        err = ctypes.GetLastError()
        raise ctypes.WinError(err, f"BeginUpdateResourceW failed on {exe_path}")

    try:
        # Write RT_ICON entries
        for icon_id, img_data in images:
            p_data = ctypes.create_string_buffer(img_data)
            res = kernel32.UpdateResourceW(
                hUpdate,
                RT_ICON,
                wintypes.LPCWSTR(icon_id),
                LANG_NEUTRAL,
                p_data,
                len(img_data),
            )
            if not res:
                err = ctypes.GetLastError()
                raise ctypes.WinError(err, f"UpdateResourceW RT_ICON {icon_id} failed")

        # Write RT_GROUP_ICON entries
        p_grp = ctypes.create_string_buffer(bytes(grp_header))
        target_groups = group_names if isinstance(group_names, (list, tuple)) else [group_names]
        for gname in target_groups:
            target_id = wintypes.LPCWSTR(gname) if isinstance(gname, int) else gname
            res = kernel32.UpdateResourceW(
                hUpdate,
                RT_GROUP_ICON,
                target_id,
                LANG_NEUTRAL,
                p_grp,
                len(grp_header),
            )
            if not res:
                err = ctypes.GetLastError()
                raise ctypes.WinError(err, f"UpdateResourceW RT_GROUP_ICON {gname} failed")

        ok = kernel32.EndUpdateResourceW(hUpdate, False)
        if not ok:
            err = ctypes.GetLastError()
            raise ctypes.WinError(err, f"EndUpdateResourceW failed")

        return True
    except Exception:
        kernel32.EndUpdateResourceW(hUpdate, True)
        raise


def patch_orbita_144_icon(app_base: Optional[Path] = None) -> bool:
    """Find and patch chrome.exe, chrome.dll and helper binaries with icon.ico."""
    base = Path(app_base or Path(__file__).resolve().parent.parent)
    ico_path = base / "assets" / "donglao_browser_icon.ico"
    if not ico_path.exists():
        ico_path = base / "icon.ico"
    if not ico_path.exists():
        return False

    targets = [
        # Main chrome.exe executables
        (base / "Browser" / "donglao-browser-144" / "chrome.exe", ["IDR_MAINFRAME", 1]),
        (base / "Browser" / "orbita-browser-144" / "chrome.exe", ["IDR_MAINFRAME", 1]),
        (base / "Browser" / "ht-browser-144" / "chrome.exe", ["IDR_MAINFRAME", 1]),
        (base / "Browser" / "ht-browser-144" / "htbrowser.exe", ["IDR_MAINFRAME", 1]),
        # chrome.dll inside Chromium version directory
        (base / "Browser" / "donglao-browser-144" / "144.0.7559.96" / "chrome.dll", [101, 102, 103, 1]),
        (base / "Browser" / "orbita-browser-144" / "144.0.7559.96" / "chrome.dll", [101, 102, 103, 1]),
        (base / "Browser" / "ht-browser-144" / "144.0.7559.96" / "chrome.dll", [101, 102, 103, 1]),
        # PWA launcher helper
        (base / "Browser" / "donglao-browser-144" / "144.0.7559.96" / "chrome_pwa_launcher.exe", [1]),
        (base / "Browser" / "orbita-browser-144" / "144.0.7559.96" / "chrome_pwa_launcher.exe", [1]),
    ]

    patched = False
    for path, gnames in targets:
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            try:
                update_executable_icon(path, ico_path, backup=True, group_names=gnames)
                patched = True
            except Exception as e:
                print(f"[WARN] Failed to patch {path.name}: {e}")
    return patched


if __name__ == "__main__":
    success = patch_orbita_144_icon()
    if success:
        print("[SUCCESS] Da doi thanh cong logo icon cua Browser Engine (EXE + DLL)!")
    else:
        print("[FAILED] Khong tim thay binary hoac icon.ico.")


