# -*- coding: utf-8 -*-
"""
check_features.services.video_downloader - Tải video TikTok không watermark qua stream chunk.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Callable, Optional, Tuple
import requests


def download_video_no_watermark(
    url: str,
    output_dir: str,
    aweme_id: str,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
    cancel_flag_func: Optional[Callable[[], bool]] = None,
    timeout: int = 30,
) -> Tuple[bool, str, str]:
    """
    Tải video TikTok không watermark theo chunk vào file tạm, sau đó đổi tên an toàn.
    - progress_callback(downloaded_bytes, total_bytes, percent)
    - Trả về (success, saved_file_path, message)
    """
    if not url:
        return False, "", "URL tải video không tồn tại."

    os.makedirs(output_dir, exist_ok=True)
    temp_file = Path(output_dir) / f"temp_{aweme_id}_{int(time.time())}.mp4"
    final_file = Path(output_dir) / f"tiktok_{aweme_id}.mp4"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://www.tiktok.com/",
    }

    try:
        with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
            if resp.status_code != 200:
                return False, "", f"Server trả về mã lỗi HTTP {resp.status_code}"

            total_len = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(temp_file, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if cancel_flag_func and cancel_flag_func():
                        f.close()
                        if temp_file.exists():
                            temp_file.unlink()
                        return False, "", "Đã hủy tải video theo yêu cầu."

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            percent = (downloaded / total_len * 100) if total_len > 0 else 0.0
                            progress_callback(downloaded, total_len, percent)

        # Đổi tên file tạm thành file chính thức
        if final_file.exists():
            final_file.unlink()
        temp_file.rename(final_file)
        return True, str(final_file), f"Đã tải thành công: {final_file.name}"

    except Exception as e:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        return False, "", f"Lỗi khi tải video: {e}"
