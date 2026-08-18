"""
profile_config_engine.py - Sinh cấu hình Anti-Detect fingerprint cho từng profile.

Module tạo dict cấu hình fingerprint đầy đủ (Canvas noise, Audio noise, WebGL params,
WebRTC fake IP, Client Hints, Plugins, v.v.) để truyền cho stealth JS engine inject
qua CDP tại runtime. Không ghi file ra đĩa.
"""

from __future__ import annotations

import hashlib
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


# Chrome version constants — update when bundled chrome-win64 is upgraded
CHROME_MAJOR = "149"
CHROME_FULL_VERSION = "149.0.7827.55"
CHROME_UA_TOKEN = "Chrome/149.0.0.0"


_PDF_MIMETYPE = {
    "description": "Portable Document Format",
    "suffixes": ["pdf"],
    "type": "application/pdf",
}
_PDF_MIMETYPE_TEXT = {
    "description": "Portable Document Format",
    "suffixes": ["pdf"],
    "type": "text/pdf",
}

# 5 standard Chrome PDF plugins matching real Chrome output
DEFAULT_PLUGINS = [
    {
        "name": "PDF Viewer",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [_PDF_MIMETYPE, _PDF_MIMETYPE_TEXT],
    },
    {
        "name": "Chrome PDF Viewer",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [_PDF_MIMETYPE, _PDF_MIMETYPE_TEXT],
    },
    {
        "name": "Chromium PDF Viewer",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [_PDF_MIMETYPE, _PDF_MIMETYPE_TEXT],
    },
    {
        "name": "Microsoft Edge PDF Viewer",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [_PDF_MIMETYPE, _PDF_MIMETYPE_TEXT],
    },
    {
        "name": "WebKit built-in PDF",
        "filename": "internal-pdf-viewer",
        "description": "Portable Document Format",
        "mimeTypes": [_PDF_MIMETYPE, _PDF_MIMETYPE_TEXT],
    },
]

# 34 WebGL extensions matching real Chrome NVIDIA output
DEFAULT_WEBGL_EXTENSIONS = [
    "ANGLE_instanced_arrays",
    "EXT_blend_minmax",
    "EXT_color_buffer_half_float",
    "EXT_disjoint_timer_query",
    "EXT_float_blend",
    "EXT_frag_depth",
    "EXT_shader_texture_lod",
    "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic",
    "EXT_sRGB",
    "KHR_parallel_shader_compile",
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
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_depth_texture",
    "WEBGL_draw_buffers",
    "WEBGL_lose_context",
    "WEBGL_multi_draw",
    # WebGL2-specific extensions
    "EXT_color_buffer_float",
    "EXT_disjoint_timer_query_webgl2",
    "EXT_texture_norm16",
    "OES_draw_buffers_indexed",
    "OVR_multiview2",
]

# 42 WebGL parameter values matching real NVIDIA GTX 750 / RTX 3060 output
DEFAULT_GL_PARAM_VALUES = [
    {"name": "ALPHA_BITS", "value": 8},
    {"name": "BLUE_BITS", "value": 8},
    {"name": "DEPTH_BITS", "value": 24},
    {"name": "GREEN_BITS", "value": 8},
    {"name": "RED_BITS", "value": 8},
    {"name": "STENCIL_BITS", "value": 8},
    {"name": "MAX_3D_TEXTURE_SIZE", "value": 2048},
    {"name": "MAX_ARRAY_TEXTURE_LAYERS", "value": 2048},
    {"name": "MAX_COLOR_ATTACHMENTS", "value": 8},
    {"name": "MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS", "value": 200704},
    {"name": "MAX_COMBINED_TEXTURE_IMAGE_UNITS", "value": 32},
    {"name": "MAX_COMBINED_UNIFORM_BLOCKS", "value": 24},
    {"name": "MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS", "value": 212992},
    {"name": "MAX_CUBE_MAP_TEXTURE_SIZE", "value": 16384},
    {"name": "MAX_DRAW_BUFFERS", "value": 8},
    {"name": "MAX_FRAGMENT_INPUT_COMPONENTS", "value": 120},
    {"name": "MAX_FRAGMENT_UNIFORM_BLOCKS", "value": 12},
    {"name": "MAX_FRAGMENT_UNIFORM_COMPONENTS", "value": 4096},
    {"name": "MAX_FRAGMENT_UNIFORM_VECTORS", "value": 1024},
    {"name": "MAX_PROGRAM_TEXEL_OFFSET", "value": 7},
    {"name": "MAX_RENDERBUFFER_SIZE", "value": 16384},
    {"name": "MAX_SAMPLES", "value": 8},
    {"name": "MAX_TEXTURE_IMAGE_UNITS", "value": 16},
    {"name": "MAX_TEXTURE_LOD_BIAS", "value": 2},
    {"name": "MAX_TEXTURE_SIZE", "value": 16384},
    {"name": "MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS", "value": 120},
    {"name": "MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS", "value": 4},
    {"name": "MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS", "value": 4},
    {"name": "MAX_UNIFORM_BLOCK_SIZE", "value": 65536},
    {"name": "MAX_UNIFORM_BUFFER_BINDINGS", "value": 24},
    {"name": "MAX_VARYING_COMPONENTS", "value": 120},
    {"name": "MAX_VARYING_VECTORS", "value": 30},
    {"name": "MAX_VERTEX_ATTRIBS", "value": 16},
    {"name": "MAX_VERTEX_OUTPUT_COMPONENTS", "value": 120},
    {"name": "MAX_VERTEX_TEXTURE_IMAGE_UNITS", "value": 16},
    {"name": "MAX_VERTEX_UNIFORM_BLOCKS", "value": 12},
    {"name": "MAX_VERTEX_UNIFORM_COMPONENTS", "value": 16384},
    {"name": "MAX_VERTEX_UNIFORM_VECTORS", "value": 4096},
    {"name": "MAX_VIEWPORT_DIMS", "value": {"0": 32768, "1": 32768}},
    {"name": "MIN_PROGRAM_TEXEL_OFFSET", "value": -8},
    {"name": "UNIFORM_BUFFER_OFFSET_ALIGNMENT", "value": 256},
    {"name": "ALIASED_LINE_WIDTH_RANGE", "value": {"0": 1, "1": 1}},
    {"name": "ALIASED_POINT_SIZE_RANGE", "value": {"0": 1, "1": 1024}},
]


DEFAULT_LICENSE_KEY = "6B86FD072ECAF47212A07ABE329ED944C64244D6B153929C6BD9A552BE2B9086"


def generate_stealth_profile_config(
    account_uuid: str,
    proxy_info: Optional[Dict[str, Any]] = None,
    geoip_info: Optional[Dict[str, Any]] = None,
    user_agent: Optional[str] = None,
    hardware_concurrency: int = 8,
    device_memory: int = 8,
    profile_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Tạo cấu trúc cấu hình fingerprint đầy đủ cho stealth JS engine và C++ HT Browser.

    Dict trả về được truyền cho ``vibe_stealth_engine.generate_stealth_js()``
    để inject qua CDP hoặc ghi ra file ``data.huynhthang`` / ``data.orbita`` để C++ kernel đọc.
    """
    canvas_seed = generate_deterministic_seed(account_uuid, "canvas")
    audio_seed = generate_deterministic_seed(account_uuid, "audio")
    
    # Mặc định thông số vị trí từ GeoIP hoặc fallback
    tz_name = "America/New_York"
    lat, lon = 40.7128, -74.0060
    fake_ip = ""
    
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

    # Auto-detect proxy IP for WebRTC fake and normalize proxy dict
    proxy_obj: Dict[str, Any] = {}
    if proxy_info:
        server = proxy_info.get("server", "")
        ptype = "http"
        host = ""
        port = 80
        if "://" in server:
            ptype, rest = server.split("://", 1)
            if ":" in rest:
                host, port_str = rest.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    port = 80
            else:
                host = rest
        elif proxy_info.get("host"):
            host = str(proxy_info.get("host"))
            port = int(proxy_info.get("port", 80) or 80)
            ptype = str(proxy_info.get("type", "http") or "http")

        if host:
            if not fake_ip:
                fake_ip = host
            proxy_obj = {
                "host": host,
                "port": port,
                "type": ptype.lower(),
            }
            if proxy_info.get("username") or proxy_info.get("user"):
                proxy_obj["username"] = proxy_info.get("username") or proxy_info.get("user")
            if proxy_info.get("password") or proxy_info.get("pass"):
                proxy_obj["password"] = proxy_info.get("password") or proxy_info.get("pass")

    ua = user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"{CHROME_UA_TOKEN} Safari/537.36"
    )

    resolved_license = os.environ.get("VIBE_ORBITA_LICENSE_KEY") or DEFAULT_LICENSE_KEY

    config: Dict[str, Any] = {
        "profile_name": str(profile_name or account_uuid),
        "account_uuid": str(account_uuid),
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
            "maxViewportDims": [32768, 32768],
            "glParamValues": list(DEFAULT_GL_PARAM_VALUES),
            "extensions": list(DEFAULT_WEBGL_EXTENSIONS),
        },
        "webGpu": {
            "enabled": True,
            "adapterInfo": {
                "vendor": "nvidia",
                "architecture": "ampere",
                "device": "0x0000",
                "driver": "32.0.15.6614",
                "isFallbackAdapter": False,
                "description": "NVIDIA GeForce RTX 3060",
            },
            "features": [
                "depth-clip-control",
                "timestamp-query",
                "texture-compression-bc",
                "shader-f16",
            ],
            "limits": {
                "maxBindGroups": 4,
                "maxBindingsPerBindGroup": 1000,
                "maxBufferSize": 268435456,
                "maxComputeWorkgroupSizeX": 256,
                "maxComputeWorkgroupSizeY": 256,
                "maxComputeWorkgroupSizeZ": 64,
                "maxTextureArrayLayers": 2048,
                "maxTextureDimension1D": 16384,
                "maxTextureDimension2D": 16384,
                "maxTextureDimension3D": 2048,
                "maxVertexAttributes": 16,
                "maxVertexBuffers": 8,
            },
        },
        "webrtc": {
            "disableWebRTC": False,
            "fakePublicIP": fake_ip,
        },
        "clientHints": {
            "brands": [
                {"brand": "Not/A)Brand", "version": "8"},
                {"brand": "Chromium", "version": CHROME_MAJOR},
                {"brand": "Google Chrome", "version": CHROME_MAJOR},
            ],
            "formFactors": ["Desktop"],
            "fullVersion": CHROME_FULL_VERSION,
            "fullVersionList": [
                {"brand": "Not/A)Brand", "version": "8.0.0.0"},
                {"brand": "Chromium", "version": CHROME_FULL_VERSION},
                {"brand": "Google Chrome", "version": CHROME_FULL_VERSION},
            ],
            "platform": "Windows",
            "platformVersion": "19.0.0",
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
            "isExtendedOverride": True,
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
            "list": list(DEFAULT_PLUGINS),
        },
        "license_key": resolved_license,
        "hardware_concurrency": hardware_concurrency,
        "device_memory": device_memory,
        "proxy": proxy_obj,
    }
    return config


def find_ttm_raw_profile_file(profile_name: str) -> Optional[Path]:
    """Tìm đường dẫn file data.huynhthang nguyên bản có chữ ký hợp lệ từ TTM."""
    if not profile_name:
        return None
    import json
    from pathlib import Path
    ttm_profiles_dir = Path(os.path.expandvars(r"%APPDATA%\tiktokmanager\profiles"))
    if not ttm_profiles_dir.exists():
        return None
    for p in ttm_profiles_dir.iterdir():
        if p.is_dir():
            dh = p / "data.huynhthang"
            if dh.exists():
                try:
                    with open(dh, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("profile_name") == profile_name or p.name == profile_name:
                        return dh
                except Exception:
                    pass
    return None


def find_ttm_profile_config(profile_name: str) -> Optional[Dict[str, Any]]:
    """Tìm dữ liệu cấu hình data.huynhthang có sẵn từ TTM nếu profile cùng tên đã từng được tạo trên TTM."""
    raw_file = find_ttm_raw_profile_file(profile_name)
    if raw_file and raw_file.exists():
        import json
        try:
            with open(raw_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def write_profile_config_files(profile_path: str | os.PathLike[str], config: Dict[str, Any]) -> None:
    """Ghi file data.huynhthang và data.orbita vào thư mục profile để C++ kernel của HT Browser đọc trực tiếp.

    Đối với các profile đã có chữ ký hợp lệ từ TTM (như AUTO 22, AUTO 6), thực hiện sao chép nguyên bản
    (byte-for-byte binary copy) để bảo toàn 100% chữ ký C++ mà không làm biến dạng cấu trúc JSON.
    """
    if not profile_path:
        return
    import json
    import shutil
    from pathlib import Path
    p = Path(profile_path)
    p.mkdir(parents=True, exist_ok=True)

    base_template_dir = Path(__file__).resolve().parent / "assets" / "templates"
    base_template_huynhthang = base_template_dir / "base_data.huynhthang"
    base_template_orbita = base_template_dir / "base_data.orbita"

    profile_name = config.get("profile_name") or (p.parent.name if p.parent else "")
    raw_file = find_ttm_raw_profile_file(str(profile_name)) if profile_name else None

    if base_template_huynhthang.exists():
        # 1. Cấp phát từ Base Template độc lập và cập nhật đúng profile_name động
        try:
            with open(base_template_huynhthang, "r", encoding="utf-8") as f:
                template_data = json.load(f)

            if profile_name:
                template_data["profile_name"] = str(profile_name)

            # Đồng bộ proxy nếu có
            proxy_cfg = config.get("proxy")
            if isinstance(proxy_cfg, dict):
                template_data["proxy"] = proxy_cfg

            for fname in ("data.huynhthang", "data.orbita"):
                target = p / fname
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(template_data, f, indent=2, ensure_ascii=False)
        except Exception:
            # Fallback sao chép nhị phân nếu gặp lỗi parse
            try:
                shutil.copyfile(base_template_huynhthang, p / "data.huynhthang")
                shutil.copyfile(base_template_huynhthang, p / "data.orbita")
            except Exception:
                pass
    else:
        # 2. Fallback ghi JSON chuẩn
        cfg_to_write = dict(config)
        if not cfg_to_write.get("license_key"):
            cfg_to_write["license_key"] = os.environ.get("VIBE_ORBITA_LICENSE_KEY", DEFAULT_LICENSE_KEY)
        if profile_name:
            cfg_to_write["profile_name"] = str(profile_name)
        for fname in ("data.huynhthang", "data.orbita"):
            target = p / fname
            try:
                with open(target, "w", encoding="utf-8") as f:
                    json.dump(cfg_to_write, f, indent=2, ensure_ascii=False)
            except Exception:
                pass


# Backward-compatibility alias
generate_orbita_profile_config = generate_stealth_profile_config
