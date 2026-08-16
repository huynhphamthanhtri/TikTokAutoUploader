"""
profile_config_engine.py - Sinh cấu hình Anti-Detect Native cho Orbita Browser Core.

Module tạo file cấu hình `data.orbita` và `data.huynhthang` cho từng profile Chromium / Orbita
được nạp tự động qua C++ binary hooks (Canvas noise, Audio noise, WebGL renderer, WebRTC fake public IP).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional


def generate_deterministic_seed(account_uuid: str, salt: str = "") -> int:
    """Sinh số nguyên 32-bit dương cố định từ account_uuid (Deterministic Seed).
    
    Cùng một account_uuid và salt sẽ luôn tạo ra một seed giống nhau,
    giúp giữ tính ổn định tuyệt đối của Canvas và Audio qua nhiều phiên làm việc.
    """
    raw = f"{account_uuid}_{salt}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    # Lấy 8 ký tự hex đầu tiên để tạo số nguyên 32-bit (giới hạn trong khoảng dương)
    return int(digest[:8], 16) % 2147483647


def generate_orbita_profile_config(
    account_uuid: str,
    proxy_info: Optional[Dict[str, Any]] = None,
    geoip_info: Optional[Dict[str, Any]] = None,
    user_agent: Optional[str] = None,
    hardware_concurrency: int = 8,
    device_memory: int = 8,
    profile_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Tạo cấu trúc cấu hình đầy đủ chuẩn Orbita 144 / data.huynhthang."""
    canvas_seed = generate_deterministic_seed(account_uuid, "canvas")
    audio_seed = generate_deterministic_seed(account_uuid, "audio")
    
    # Mặc định thông số vị trí từ GeoIP hoặc fallback
    tz_name = "America/New_York"
    lat, lon = 40.7128, -74.0060
    fake_ip = "127.0.0.1"
    
    if geoip_info:
        tz_name = geoip_info.get("timezone", tz_name) or tz_name
        try:
            if "latitude" in geoip_info and geoip_info["latitude"] is not None:
                lat = float(geoip_info["latitude"])
        except (ValueError, TypeError):
            pass
        try:
            if "longitude" in geoip_info and geoip_info["longitude"] is not None:
                lon = float(geoip_info["longitude"])
        except (ValueError, TypeError):
            pass
        fake_ip = geoip_info.get("ip", fake_ip) or fake_ip

    ua = user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    )

    config: Dict[str, Any] = {
        "profile_name": str(account_uuid),
        "license_key": "ORBITA_CORE_ENABLED",
        "canvas": {
            "noiseEnabled": True,
            "noiseSeed": canvas_seed,
        },
        "audio": {
            "noiseEnabled": True,
            "noiseSeed": audio_seed,
        },
        "extensions": [],
        "webgl": {
            "noiseEnabled": True,
            "vendor": "Google Inc. (NVIDIA)",
            "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "maxAnisotropy": 16,
            "maxTextureSize": 16384,
            "maxViewportDims": [16384, 16384],
            "glParamValues": [
                {"name": "ALPHA_BITS", "value": 8},
                {"name": "BLUE_BITS", "value": 8},
                {"name": "DEPTH_BITS", "value": 24},
                {"name": "GREEN_BITS", "value": 8},
                {"name": "RED_BITS", "value": 8},
                {"name": "STENCIL_BITS", "value": 8},
                {"name": "MAX_TEXTURE_SIZE", "value": 16384},
            ],
            "extensions": [
                "ANGLE_instanced_arrays",
                "EXT_blend_minmax",
                "EXT_color_buffer_half_float",
                "EXT_float_blend",
                "EXT_frag_depth",
                "EXT_shader_texture_lod",
                "EXT_texture_compression_bptc",
                "EXT_texture_compression_rgtc",
                "EXT_texture_filter_anisotropic",
                "OES_element_index_uint",
                "OES_fbo_render_mipmap",
                "OES_standard_derivatives",
                "OES_texture_float",
                "OES_texture_float_linear",
                "OES_texture_half_float",
                "OES_texture_half_float_linear",
                "OES_vertex_array_object",
                "WEBGL_color_buffer_float",
                "WEBGL_compressed_texture_s3tc",
                "WEBGL_debug_renderer_info",
                "WEBGL_debug_shaders",
                "WEBGL_depth_texture",
                "WEBGL_draw_buffers",
                "WEBGL_lose_context",
                "WEBGL_multi_draw",
            ],
        },
        "webGpu": {
            "enabled": True,
            "adapterInfo": {
                "vendor": "nvidia",
                "architecture": "ampere",
                "device": "RTX 3060",
                "description": "NVIDIA GeForce RTX 3060",
            },
            "features": [],
            "limits": {},
        },
        "webrtc": {
            "disableWebRTC": False,
            "fakePublicIP": fake_ip,
        },
        "clientHints": {
            "brands": [
                {"brand": "Chromium", "version": "144"},
                {"brand": "Google Chrome", "version": "144"},
                {"brand": "Not-A.Brand", "version": "24"},
            ],
            "fullVersion": "144.0.7559.96",
            "fullVersionList": [
                {"brand": "Chromium", "version": "144.0.7559.96"},
                {"brand": "Google Chrome", "version": "144.0.7559.96"},
                {"brand": "Not-A.Brand", "version": "24.0.0.0"},
            ],
            "platform": "Windows",
            "platformVersion": "15.0.0",
            "architecture": "x86",
            "bitness": "64",
            "model": "",
            "mobile": False,
            "wow64": False,
        },
        "navigator": {
            "userAgent": ua,
            "appVersion": ua.replace("Mozilla/", ""),
            "platform": "Win32",
            "languages": ["en-US", "en"],
            "hardwareConcurrency": hardware_concurrency,
            "deviceMemory": device_memory,
            "maxTouchPoints": 0,
            "doNotTrack": None,
            "vendor": "Google Inc.",
        },
        "screen": {
            "width": 1920,
            "height": 1080,
            "availWidth": 1920,
            "availHeight": 1040,
            "colorDepth": 24,
            "pixelDepth": 24,
            "devicePixelRatio": 1,
            "isExtended": False,
            "isExtendedOverride": False,
        },
        "timezone": {
            "name": tz_name,
        },
        "geoLocation": {
            "mode": "manual",
            "latitude": lat,
            "longitude": lon,
            "accuracy": 15,
        },
        "fonts": {
            "availableFonts": [
                "Arial", "Arial Black", "Bahnschrift", "Calibri", "Cambria", "Cambria Math",
                "Candara", "Comic Sans MS", "Consolas", "Constantia", "Corbel", "Courier New",
                "Ebrima", "Franklin Gothic Medium", "Gabriola", "Gadugi", "Georgia",
                "Impact", "Ink Free", "Javanese Text", "Leelawadee UI", "Lucida Console",
                "Lucida Sans Unicode", "Malgun Gothic", "Marlett", "Microsoft Himalaya",
                "Microsoft JhengHei", "Microsoft New Tai Lue", "Microsoft PhagsPa",
                "Microsoft Sans Serif", "Microsoft Tai Le", "Microsoft YaHei", "Microsoft Yi Baiti",
                "MingLiU-ExtB", "Mongolian Baiti", "MS Gothic", "MS PGothic", "MV Boli",
                "Myanmar Text", "Nirmala UI", "Palatino Linotype", "Segoe MDL2 Assets",
                "Segoe Print", "Segoe Script", "Segoe UI", "Segoe UI Historic", "Segoe UI Emoji",
                "Segoe UI Symbol", "SimSun", "Sitka", "Sylfaen", "Symbol", "Tahoma",
                "Times New Roman", "Trebuchet MS", "Verdana", "Webdings", "Wingdings", "Yu Gothic",
            ]
        },
        "mediaDevices": {
            "audioInputs": 1,
            "audioOutputs": 1,
            "videoInputs": 0,
        },
        "plugins": {
            "override": True,
            "list": [
                {"name": "PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"}
            ],
        },
        "license_key": os.environ.get("VIBE_ORBITA_LICENSE_KEY", ""),
        "profile_name": str(profile_name or account_uuid),
        "proxy": proxy_info or {},
    }
    return config


def write_profile_config_files(profile_dir: str, config: Dict[str, Any]) -> None:
    """Ghi cấu hình profile vào thư mục Profile.
    
    Ghi đồng thời `data.huynhthang` và `data.orbita` để tương thích với các phiên bản
    Orbita/Chromium patched core khác nhau.
    """
    if not profile_dir:
        return
    os.makedirs(profile_dir, exist_ok=True)
    for filename in ("data.huynhthang", "data.orbita"):
        target_path = os.path.join(profile_dir, filename)
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
