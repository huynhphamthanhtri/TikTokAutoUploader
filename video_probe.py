"""
video_probe.py - Dịch vụ kiểm tra chuyên sâu tính hợp lệ của video (Deep Video Validation).

Chịu trách nhiệm:
1. Quét và kiểm tra kỹ thuật video thông qua ffprobe (non-blocking subprocess, có timeout).
2. Trích xuất thời lượng (duration), codec video (h264/hevc), codec audio, resolution, fps.
3. Bộ nhớ đệm (Cache) lưu SIÊU DỮ LIỆU THÔ (Raw Metadata) theo (canonical_path, size_bytes, mtime_ns).
   Chính sách kiểm tra (Policy) được đánh giá độc lập ở mỗi lần gọi (F01).
4. Xử lý thiếu ffprobe chặt chẽ: không tự động cấp is_valid=True khi thiếu công cụ (F02),
   và KHÔNG lưu lỗi thiếu công cụ tạm thời vào cache vĩnh viễn (Round 2 review).
5. Phân tích an toàn JSON với float NaN/Inf, width/height exception, null codec và schema check (F13).
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from schedule_contracts import VideoProbeResult, VideoValidationPolicy

logger = logging.getLogger("VideoProbe")


@dataclass(frozen=True)
class _RawProbeMetadata:
    """Siêu dữ liệu kỹ thuật thô thu thập được từ ổ đĩa và ffprobe trước khi áp dụng policy."""
    canonical_path: str
    size_bytes: int
    mtime_ns: int
    duration_seconds: Optional[float]
    video_codec: Optional[str]
    audio_codec: Optional[str]
    width: Optional[int]
    height: Optional[int]
    pixel_format: Optional[str]
    frame_rate: Optional[float]
    raw_issues: Tuple[str, ...]


_CACHE_LOCK = threading.Lock()
_RAW_METADATA_CACHE: Dict[Tuple[str, int, int], _RawProbeMetadata] = {}


def clear_probe_cache() -> None:
    """Xóa bộ nhớ đệm probe (dùng trong test hoặc khi làm mới hoàn toàn)."""
    with _CACHE_LOCK:
        _RAW_METADATA_CACHE.clear()


def _parse_frame_rate(fps_str: Optional[str]) -> Optional[float]:
    """Chuyển đổi chuỗi fps dạng '30/1' hoặc '29.97' sang float an toàn."""
    if not fps_str or fps_str == "0/0":
        return None
    try:
        if "/" in fps_str:
            val = float(Fraction(fps_str))
        else:
            val = float(fps_str)
        if math.isnan(val) or math.isinf(val) or val <= 0:
            return None
        return val
    except Exception:
        return None


def _get_raw_metadata(
    path: Path | str,
    *,
    timeout_seconds: float = 15.0,
    ffprobe_bin: Optional[str] = None,
) -> _RawProbeMetadata:
    """Thu thập hoặc lấy từ cache siêu dữ liệu kỹ thuật thô của file."""
    p = Path(path)
    try:
        resolved_path = p.resolve()
        canonical_str = str(resolved_path)
    except Exception as e:
        return _RawProbeMetadata(
            canonical_path=str(p),
            size_bytes=0,
            mtime_ns=0,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("CANNOT_RESOLVE_PATH", str(e)),
        )

    if not resolved_path.exists():
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=0,
            mtime_ns=0,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("FILE_NOT_FOUND",),
        )

    if not resolved_path.is_file():
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=0,
            mtime_ns=0,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("NOT_A_FILE",),
        )

    try:
        stat_info = resolved_path.stat()
        size_bytes = stat_info.st_size
        mtime_ns = getattr(stat_info, "st_mtime_ns", int(stat_info.st_mtime * 1e9))
    except Exception as e:
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=0,
            mtime_ns=0,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("STAT_FAILED", str(e)),
        )

    if size_bytes == 0:
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=0,
            mtime_ns=mtime_ns,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("EMPTY_FILE_ZERO_BYTES",),
        )

    cache_key = (canonical_str, size_bytes, mtime_ns)
    with _CACHE_LOCK:
        if cache_key in _RAW_METADATA_CACHE:
            return _RAW_METADATA_CACHE[cache_key]

    # Kiểm tra ffprobe
    bin_path = ffprobe_bin or shutil.which("ffprobe")
    if not bin_path:
        try:
            from youtube_monitor.ffmpeg_helper import find_ffprobe
            found = find_ffprobe()
            if found:
                bin_path = str(found)
        except Exception:
            pass
    if not bin_path:
        # KHÔNG cache lỗi thiếu tool vào _RAW_METADATA_CACHE (tránh lưu vĩnh viễn lỗi môi trường)
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("FFPROBE_NOT_AVAILABLE",),
        )

    cmd = [
        bin_path,
        "-v", "error",
        "-show_error",
        "-show_entries",
        "format=format_name,duration,size:stream=index,codec_type,codec_name,profile,pix_fmt,width,height,avg_frame_rate,duration,sample_rate,channels",
        "-of", "json",
        canonical_str,
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("PROBE_TIMEOUT",),
        )
    except FileNotFoundError:
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("FFPROBE_NOT_AVAILABLE",),
        )
    except Exception as exec_err:
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("PROBE_EXEC_ERROR", str(exec_err)),
        )

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or f"ffprobe returncode {proc.returncode}"
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("CORRUPT_OR_UNREADABLE_MEDIA", err_msg[:200]),
        )

    try:
        data = json.loads(proc.stdout)
    except Exception as parse_err:
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("INVALID_JSON_FROM_FFPROBE", str(parse_err)),
        )

    # Schema validation chặt chẽ (F13)
    if not isinstance(data, dict):
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("INVALID_JSON_ROOT_NOT_DICT",),
        )

    if "error" in data and isinstance(data["error"], (dict, str)):
        err_str = str(data["error"].get("string") if isinstance(data["error"], dict) else data["error"])
        return _RawProbeMetadata(
            canonical_path=canonical_str,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            raw_issues=("FFPROBE_STREAM_ERROR", err_str),
        )

    raw_streams = data.get("streams")
    streams = [s for s in raw_streams if isinstance(s, dict)] if isinstance(raw_streams, list) else []
    format_info = data.get("format") if isinstance(data.get("format"), dict) else {}

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    raw_issues_list: List[str] = []
    if not video_stream:
        raw_issues_list.append("NO_VIDEO_STREAM_FOUND")

    # Kiểm tra kiểu nghiêm ngặt cho codec: Bắt buộc phải là string thực thụ (F13)
    raw_video_codec = video_stream.get("codec_name") if video_stream else None
    video_codec: Optional[str] = None
    if video_stream:
        if isinstance(raw_video_codec, str) and raw_video_codec.strip():
            video_codec = raw_video_codec.strip().lower()
        else:
            raw_issues_list.append("INVALID_OR_MISSING_VIDEO_CODEC_SCHEMA")

    raw_audio_codec = audio_stream.get("codec_name") if audio_stream else None
    audio_codec: Optional[str] = None
    if audio_stream:
        if isinstance(raw_audio_codec, str) and raw_audio_codec.strip():
            audio_codec = raw_audio_codec.strip().lower()
        elif raw_audio_codec is not None:
            raw_issues_list.append("INVALID_AUDIO_CODEC_SCHEMA")

    width: Optional[int] = None
    height: Optional[int] = None
    if video_stream:
        try:
            if "width" in video_stream and video_stream["width"] is not None:
                w_val = int(video_stream["width"])
                if w_val > 0:
                    width = w_val
            if "height" in video_stream and video_stream["height"] is not None:
                h_val = int(video_stream["height"])
                if h_val > 0:
                    height = h_val
        except Exception:
            raw_issues_list.append("INVALID_RESOLUTION_DATA")

        if width is None or height is None:
            raw_issues_list.append("INVALID_OR_MISSING_RESOLUTION")

    # Đảm bảo pixel_format là chuỗi bất biến, không lưu dict/list mutable vào cache (F08)
    raw_pix = video_stream.get("pix_fmt") if video_stream else None
    pixel_format: Optional[str] = str(raw_pix).strip().lower() if isinstance(raw_pix, str) and raw_pix.strip() else None

    frame_rate = _parse_frame_rate(video_stream.get("avg_frame_rate")) if video_stream else None

    raw_duration = format_info.get("duration") or (video_stream.get("duration") if video_stream else None)
    duration_seconds: Optional[float] = None
    if raw_duration:
        try:
            d_val = float(raw_duration)
            if not math.isnan(d_val) and not math.isinf(d_val) and d_val > 0:
                duration_seconds = d_val
            else:
                raw_issues_list.append("DURATION_NAN_OR_NON_POSITIVE")
        except Exception:
            raw_issues_list.append("DURATION_PARSE_FAILED")

    raw_meta = _RawProbeMetadata(
        canonical_path=canonical_str,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        duration_seconds=duration_seconds,
        video_codec=video_codec,
        audio_codec=audio_codec,
        width=width,
        height=height,
        pixel_format=pixel_format,
        frame_rate=frame_rate,
        raw_issues=tuple(raw_issues_list),
    )

    with _CACHE_LOCK:
        _RAW_METADATA_CACHE[cache_key] = raw_meta

    return raw_meta


def probe_video(
    path: Path | str,
    *,
    policy: Optional[VideoValidationPolicy] = None,
    timeout_seconds: float = 15.0,
    ffprobe_bin: Optional[str] = None,
) -> VideoProbeResult:
    """Kiểm tra chuyên sâu một file video bằng ffprobe và áp dụng chính sách validation."""
    try:
        if policy is None:
            policy = VideoValidationPolicy()

        raw = _get_raw_metadata(path, timeout_seconds=timeout_seconds, ffprobe_bin=ffprobe_bin)
        issues: List[str] = list(raw.raw_issues)

        if "FFPROBE_NOT_AVAILABLE" in issues:
            return VideoProbeResult(
                canonical_path=raw.canonical_path,
                size_bytes=raw.size_bytes,
                mtime_ns=raw.mtime_ns,
                duration_seconds=None,
                video_codec=None,
                audio_codec=None,
                width=None,
                height=None,
                pixel_format=None,
                frame_rate=None,
                is_valid=False,
                issues=tuple(issues),
            )

        if raw.size_bytes > policy.max_file_size_bytes:
            issues.append(f"FILE_TOO_LARGE: {raw.size_bytes} > {policy.max_file_size_bytes}")

        if raw.duration_seconds is None or raw.duration_seconds <= 0:
            if "DURATION_NAN_OR_NON_POSITIVE" not in issues and "DURATION_PARSE_FAILED" not in issues:
                issues.append("DURATION_UNKNOWN_OR_ZERO")
        else:
            if raw.duration_seconds < policy.min_duration_seconds:
                issues.append(f"DURATION_BELOW_MIN: {raw.duration_seconds:.1f}s < {policy.min_duration_seconds:.1f}s")
            if raw.duration_seconds > policy.max_duration_seconds:
                issues.append(f"DURATION_EXCEEDS_MAX: {raw.duration_seconds:.1f}s > {policy.max_duration_seconds:.1f}s")

        # Bảng chuẩn hóa codec hợp lệ cho TikTok VOD / Publish
        canonical_codec_variants = {
            "h264": {"h264", "avc", "avc1"},
            "hevc": {"hevc", "h265", "hev1", "hvc1"},
            "av1": {"av1", "av01"},
        }

        if raw.video_codec:
            is_allowed = False
            for allowed in policy.allowed_codecs:
                allowed_norm = allowed.strip().lower()
                valid_tokens = canonical_codec_variants.get(allowed_norm, {allowed_norm})
                if raw.video_codec in valid_tokens:
                    is_allowed = True
                    break
            if not is_allowed:
                issues.append(f"CODEC_NOT_RECOMMENDED: {raw.video_codec} (Khuyến nghị: {','.join(policy.allowed_codecs)})")
        else:
            if "NO_VIDEO_STREAM_FOUND" not in issues and "INVALID_OR_MISSING_VIDEO_CODEC_SCHEMA" not in issues:
                issues.append("MISSING_VIDEO_CODEC")

        if policy.require_audio and not raw.audio_codec:
            issues.append("AUDIO_REQUIRED_BUT_MISSING")

        is_valid = (len(issues) == 0) and (raw.size_bytes > 0) and (raw.duration_seconds is not None)

        return VideoProbeResult(
            canonical_path=raw.canonical_path,
            size_bytes=raw.size_bytes,
            mtime_ns=raw.mtime_ns,
            duration_seconds=raw.duration_seconds,
            video_codec=raw.video_codec,
            audio_codec=raw.audio_codec,
            width=raw.width,
            height=raw.height,
            pixel_format=raw.pixel_format,
            frame_rate=raw.frame_rate,
            is_valid=is_valid,
            issues=tuple(issues),
        )
    except Exception as e:
        logger.error(f"Lỗi không xác định khi probe video {path}: {e}")
        p_str = str(path)
        return VideoProbeResult(
            canonical_path=p_str,
            size_bytes=0,
            mtime_ns=0,
            duration_seconds=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            pixel_format=None,
            frame_rate=None,
            is_valid=False,
            issues=("UNHANDLED_EXCEPTION_IN_PROBE", str(e)),
        )


def batch_probe_videos(
    paths: Sequence[Path | str],
    *,
    policy: Optional[VideoValidationPolicy] = None,
    max_workers: int = 4,
    timeout_seconds: float = 15.0,
) -> List[VideoProbeResult]:
    """Kiểm tra hàng loạt file video đa luồng với thứ tự kết quả bảo toàn."""
    path_list = list(paths)
    if not path_list:
        return []

    results: List[Optional[VideoProbeResult]] = [None] * len(path_list)
    workers = max(1, min(max_workers, len(path_list)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(probe_video, p, policy=policy, timeout_seconds=timeout_seconds): idx
            for idx, p in enumerate(path_list)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                p_str = str(path_list[idx])
                results[idx] = VideoProbeResult(
                    canonical_path=p_str,
                    size_bytes=0,
                    mtime_ns=0,
                    duration_seconds=None,
                    video_codec=None,
                    audio_codec=None,
                    width=None,
                    height=None,
                    pixel_format=None,
                    frame_rate=None,
                    is_valid=False,
                    issues=("UNHANDLED_EXCEPTION_IN_PROBE", str(e)),
                )

    return [r for r in results if r is not None]
