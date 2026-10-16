"""
fast_mp4_meta.py - Trích xuất siêu nhanh metadata MP4 (Width, Height, Duration) thuần Python (< 5ms).

Phân tích trực tiếp cấu trúc hộp ISO/IEC 14496-12 (moov -> mvhd, moov -> trak -> tkhd)
mà không cần gọi ffprobe hay opencv bên ngoài.
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def get_fast_mp4_metadata(video_path: str | Path) -> Dict[str, Any]:
    """Đọc nhanh Width, Height, Duration (giây) từ file MP4."""
    filepath = str(video_path)
    width = 1080
    height = 1920
    duration_sec = 15.0

    if not os.path.isfile(filepath):
        return {"width": width, "height": height, "duration": duration_sec}

    try:
        file_size = os.path.getsize(filepath)
        with open(filepath, "rb") as f:
            while f.tell() < file_size:
                pos = f.tell()
                header = f.read(8)
                if len(header) < 8:
                    break
                size, btype = struct.unpack(">I4s", header)
                if size == 1:
                    size = struct.unpack(">Q", f.read(8))[0]
                elif size == 0:
                    size = file_size - pos

                if btype == b"moov":
                    moov_end = min(file_size, pos + size)
                    while f.tell() < moov_end:
                        cpos = f.tell()
                        cheader = f.read(8)
                        if len(cheader) < 8:
                            break
                        csize, cbtype = struct.unpack(">I4s", cheader)
                        if csize == 1:
                            csize = struct.unpack(">Q", f.read(8))[0]
                        elif csize == 0:
                            csize = moov_end - cpos

                        if cbtype == b"mvhd":
                            ver = struct.unpack(">B", f.read(1))[0]
                            f.seek(3, os.SEEK_CUR)  # flags
                            if ver == 0:
                                f.seek(8, os.SEEK_CUR)  # creation & modification time
                                timescale, dur = struct.unpack(">II", f.read(8))
                            else:
                                f.seek(16, os.SEEK_CUR)
                                timescale, dur = struct.unpack(">IQ", f.read(12))
                            if timescale > 0:
                                duration_sec = round(dur / timescale, 3)

                        elif cbtype == b"trak":
                            trak_end = min(moov_end, cpos + csize)
                            while f.tell() < trak_end:
                                tpos = f.tell()
                                theader = f.read(8)
                                if len(theader) < 8:
                                    break
                                tsize, tbtype = struct.unpack(">I4s", theader)
                                if tsize == 1:
                                    tsize = struct.unpack(">Q", f.read(8))[0]
                                elif tsize == 0:
                                    tsize = trak_end - tpos

                                if tbtype == b"tkhd":
                                    tver = struct.unpack(">B", f.read(1))[0]
                                    f.seek(3, os.SEEK_CUR)
                                    if tver == 0:
                                        f.seek(76, os.SEEK_CUR)
                                    else:
                                        f.seek(88, os.SEEK_CUR)
                                    w_int, _, h_int, _ = struct.unpack(">HHHH", f.read(8))
                                    if w_int > 0 and h_int > 0:
                                        width, height = w_int, h_int
                                f.seek(tpos + tsize)

                        f.seek(cpos + csize)
                    break
                else:
                    f.seek(pos + size)
    except Exception as e:
        logger.debug(f"Không thể đọc MP4 atom nhanh: {e}")

    return {
        "width": width,
        "height": height,
        "duration": duration_sec,
    }
