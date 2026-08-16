# Hướng Dẫn Kỹ Thuật: Tích Hợp Anti-Detect Chuẩn Orbita 144 Cho VIBE_AUTO_UPLOAD-LP

> Tài liệu phân tích và hướng dẫn kỹ thuật chi tiết nhằm nâng cấp **VIBE_AUTO_UPLOAD-LP** sở hữu toàn bộ năng lực **Anti-Detect cấp C++ Binary** tương đương hoặc vượt trội so với công cụ **TikTok Manager (v2.4.4 - huynhthang.com)**.

---

## 1. Bản chất công nghệ Anti-Detect của TikTok Manager

Qua việc reverse-engineer file `out/main/index.jsc`, `security.js`, cơ sở dữ liệu `profiles.db` và nhân trình duyệt tại:
`C:\Users\huynh\AppData\Roaming\tiktokmanager\Chrome-bin\144.0.7559.96\chrome.dll`

### 1.1 Cơ chế hoạt động thực tế

TikTok Manager **không dùng JavaScript Stealth Injection** (`puppeteer-extra-plugin-stealth` hay hook DOM bằng JS) vì các script bảo mật thế hệ mới của ByteDance (`byted_acrawler.js`, WebAssembly VM) dễ dàng phát hiện qua:
- Lỗ hổng `Proxy` traps và kiểm tra `Function.prototype.toString`.
- Sai lệch thời gian thực thi (Microtask timing execution delay).
- Thuộc tính `navigator.webdriver` bị lộ trong Web Worker hoặc iframe cross-origin.

Thay vào đó, TikTok Manager sử dụng **Orbita 144 Core (Chromium 144.0.7559.96 đã được patch C++)**:
1. Trình duyệt được khởi chạy cùng cờ `--ht-auto` (hoặc tự động nạp file cấu hình trong User Data Dir).
2. Khi tiến trình Chromium khởi động, `chrome.dll` trực tiếp đọc file cấu hình JSON mang tên **`data.huynhthang`** (hoặc `data.orbita`) nằm tại thư mục gốc của Profile.
3. Các hook tầng C++ (Blink Rendering Engine & V8 Engine) can thiệp trực tiếp vào các hàm hệ thống, trả về `[native code]` 100% tự nhiên.

---

## 2. Đặc Tả File Cấu Hình Anti-Detect (`data.huynhthang` / `data.orbita`)

Để VIBE_AUTO_UPLOAD-LP kích hoạt toàn bộ tính năng chống phát hiện của nhân Orbita 144, mỗi Profile phải được sinh một file JSON cấu hình với cấu trúc chi tiết như sau:

```json
{
  "profile_name": "PROFILE_UUID_HERE",
  "license_key": "ORBITA_CORE_ENABLED",
  "canvas": {
    "noiseEnabled": true,
    "noiseSeed": 4829104
  },
  "audio": {
    "noiseEnabled": true,
    "noiseSeed": 9182371
  },
  "webgl": {
    "noiseEnabled": true,
    "vendor": "Google Inc. (NVIDIA)",
    "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    "maxAnisotropy": 16,
    "maxTextureSize": 16384,
    "maxViewportDims": [16384, 16384],
    "glParamValues": {},
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
      "WEBGL_multi_draw"
    ]
  },
  "webGpu": {
    "enabled": true,
    "adapterInfo": {
      "vendor": "nvidia",
      "architecture": "ampere",
      "device": "RTX 3060",
      "description": "NVIDIA GeForce RTX 3060"
    },
    "features": [],
    "limits": {}
  },
  "webrtc": {
    "disableWebRTC": false,
    "fakePublicIP": "104.28.198.42"
  },
  "clientHints": {
    "brands": [
      {"brand": "Chromium", "version": "144"},
      {"brand": "Google Chrome", "version": "144"},
      {"brand": "Not-A.Brand", "version": "24"}
    ],
    "fullVersion": "144.0.7559.96",
    "fullVersionList": [
      {"brand": "Chromium", "version": "144.0.7559.96"},
      {"brand": "Google Chrome", "version": "144.0.7559.96"},
      {"brand": "Not-A.Brand", "version": "24.0.0.0"}
    ],
    "platform": "Windows",
    "platformVersion": "15.0.0",
    "architecture": "x86",
    "bitness": "64",
    "model": "",
    "mobile": false,
    "wow64": false
  },
  "navigator": {
    "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "appVersion": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "platform": "Win32",
    "languages": ["en-US", "en"],
    "hardwareConcurrency": 8,
    "deviceMemory": 8,
    "maxTouchPoints": 0,
    "doNotTrack": null,
    "vendor": "Google Inc."
  },
  "screen": {
    "width": 1920,
    "height": 1080,
    "availWidth": 1920,
    "availHeight": 1040,
    "colorDepth": 24,
    "pixelDepth": 24,
    "devicePixelRatio": 1,
    "isExtended": false,
    "isExtendedOverride": false
  },
  "timezone": {
    "name": "America/New_York"
  },
  "geoLocation": {
    "mode": "manual",
    "latitude": 40.7128,
    "longitude": -74.0060,
    "accuracy": 15
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
      "Times New Roman", "Trebuchet MS", "Verdana", "Webdings", "Wingdings", "Yu Gothic"
    ]
  },
  "mediaDevices": {
    "audioInputs": 1,
    "audioOutputs": 1,
    "videoInputs": 0
  },
  "plugins": {
    "override": true,
    "list": [
      {"name": "PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"}
    ]
  },
  "proxy": {
    "type": "http",
    "host": "127.0.0.1",
    "port": 8080,
    "username": "",
    "password": ""
  }
}
```

---

## 3. Phân Tích Kỹ Thuật Các Mô-đun Chống Phát Hiện Tầng C++

### 3.1 Can thiệp Canvas 2D (`canvasNoise`)
- **Nguyên lý:** ByteDance render một chuỗi ký tự ẩn trên thẻ `<canvas>` 2D với font đặc biệt và lấy dữ liệu bằng `ctx.getImageData()` hoặc `canvas.toDataURL()`.
- **Cơ chế Orbita:** Hàm `SkCanvas::onDraw` trong thư viện đồ họa Skia của Chromium nhận seed từ `canvas.noiseSeed`. Với mỗi tọa độ pixel `(x, y)`, hàm băm pseudo-random sẽ thay đổi giá trị RGBA một lượng cực nhỏ ($\pm 1$ LSB).
- **Tính ưu việt:** 
  - Hash Canvas thay đổi hoàn toàn giữa các Profile khác nhau.
  - Nhưng trong **cùng một Profile**, cùng một tọa độ `(x, y)` luôn cho ra đúng một giá trị pixel cố định (Deterministic Noise), giúp vượt qua bài kiểm tra "Canvas Stability Check" của hệ thống chống bot.

### 3.2 Can thiệp WebGL & Card đồ họa (`webglNoise` & Renderer Spoofing)
- **Nguyên lý:** Trang web truy vấn thông số `UNMASKED_RENDERER_WEBGL` và băm shader precision.
- **Cơ chế Orbita:**
  - Can thiệp hàm C++ `WebGLRenderingContextBase::getParameter` trả về chuỗi `renderer` và `vendor` định cấu hình sẵn.
  - Can thiệp hàm tính toán shader (Fragment Shader) để đưa thêm sai số dấu phẩy động siêu nhỏ theo `noiseEnabled`, tránh bị phân loại vào nhóm máy ảo headless hoặc RenderDoc.

### 3.3 Can thiệp Âm Thanh (`audioNoise`)
- **Nguyên lý:** Tạo `OfflineAudioContext`, truyền sóng âm qua bộ lọc nén `DynamicsCompressorNode` và đo phổ âm thanh đầu ra.
- **Cơ chế Orbita:** Can thiệp trực tiếp vào C++ `AudioBuffer::copyFromChannel` và `AudioBuffer::getChannelData`. Trả về mảng float32 đã được thêm một lượng nhiễu trắng nhỏ theo `audio.noiseSeed`.

### 3.4 Bảo vệ Chống Rò Rỉ IP Qua WebRTC (`webrtc.fakePublicIP`)
- **Nguyên lý:** WebRTC STUN protocol gửi gói UDP ra ngoài proxy để tìm public candidate, làm lộ IP thật của đường truyền internet (Local IP & ISP IP).
- **Cơ chế Orbita:**
  - Can thiệp vào tầng C++ `PeerConnection` và `IceCandidate`.
  - Thay thế trực tiếp IP xuất hiện trong SDP Candidate bằng chuỗi `fakePublicIP` (được cấu hình bằng đúng IP của Proxy).
  - Vô hiệu hóa việc thu thập Host Candidate chứa địa chỉ IP mạng nội bộ (mDNS).

### 3.5 Đồng bộ Client Hints & HTTP/2 Header
- **Nguyên lý:** Trình duyệt hiện đại gửi các header `sec-ch-ua`, `sec-ch-ua-platform`, `sec-ch-ua-bitness`. Nếu JS `navigator.userAgentData` trả về Windows 64-bit nhưng network stack gửi header macOS hoặc Linux, tài khoản sẽ bị gắn cờ bất thường ngay lập tức.
- **Cơ chế Orbita:** Đồng bộ hóa cấu hình `clientHints` trực tiếp vào `net::URLRequestHttpJob` và `network::ResourceRequest` của Chromium, đảm bảo 100% các request mạng và thuộc tính JS khớp nhau từng byte.

---

## 4. Kiến Trúc Tích Hợp Vào VIBE_AUTO_UPLOAD-LP

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                      KIẾN TRÚC BROWSER ENGINE v2 (VIBE_AUTO_UPLOAD)                    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                    MÔ-ĐUN: `profile_config_engine.py`                            │  │
│  │  • Nhận diện Account UUID -> Sinh Deterministic Seeds (Canvas/Audio)             │  │
│  │  • Tích hợp GeoIP -> Timezone, Latitude/Longitude, WebRTC Fake Public IP         │  │
│  │  • Tạo & Ghi đè file `data.huynhthang` / `data.orbita` vào thư mục Profile       │  │
│  └────────────────────────────────────────┬─────────────────────────────────────────┘  │
│                                           │                                            │
│                                           ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │                    MÔ-ĐUN: `profile_ownership.py` (Cross-Process Lease)          │  │
│  │  • Lock file hệ điều hành (`msvcrt.locking` / `.lock_lease`)                     │  │
│  │  • Lưu trữ PID + Process Timestamp, ngăn chặn xung đột mở cùng lúc               │  │
│  └────────────────────────────────────────┬─────────────────────────────────────────┘  │
│                                           │                                            │
│                                           ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │              ORBITA 144 BROWSER RUNTIME (`browser_patchright_glue.py`)            │  │
│  │  • Ưu tiên Binary: `Browser/orbita-browser-144/chrome.exe`                        │  │
│  │  • Cờ khởi chạy: `--ht-auto --disable-session-crashed-bubble --user-data-dir`    │  │
│  │  • Patchright 1.61.2 CDP Controller: Điều khiển luồng Upload TikTok Studio       │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Hướng Dẫn Triển Khai Mã Nguồn Chi Tiết

### Bước 1: Tạo Module `profile_config_engine.py`

Module này chịu trách nhiệm sinh file cấu hình `data.huynhthang` (hoặc `data.orbita`) mỗi khi chuẩn bị khởi chạy Profile:

```python
"""
profile_config_engine.py - Sinh cấu hình Anti-Detect Native cho Orbita Browser Core.
"""

import hashlib
import json
import os
from typing import Any, Dict, Optional


def _generate_seed_from_uuid(account_uuid: str, salt: str = "") -> int:
    """Sinh số nguyên 32-bit dương cố định từ account_uuid (Deterministic Seed)."""
    raw = f"{account_uuid}_{salt}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:8], 16) % 2147483647


def generate_orbita_profile_config(
    account_uuid: str,
    proxy_info: Optional[Dict[str, Any]] = None,
    geoip_info: Optional[Dict[str, Any]] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Tạo cấu trúc cấu hình đầy đủ chuẩn Orbita 144."""
    canvas_seed = _generate_seed_from_uuid(account_uuid, "canvas")
    audio_seed = _generate_seed_from_uuid(account_uuid, "audio")
    
    # Mặc định thông số vị trí từ GeoIP
    tz_name = "America/New_York"
    lat, lon = 40.7128, -74.0060
    fake_ip = "127.0.0.1"
    
    if geoip_info:
        tz_name = geoip_info.get("timezone", tz_name)
        lat = float(geoip_info.get("latitude", lat))
        lon = float(geoip_info.get("longitude", lon))
        fake_ip = geoip_info.get("ip", fake_ip)

    ua = user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    )

    config = {
        "profile_name": account_uuid,
        "license_key": "ORBITA_CORE_ENABLED",
        "canvas": {
            "noiseEnabled": True,
            "noiseSeed": canvas_seed,
        },
        "audio": {
            "noiseEnabled": True,
            "noiseSeed": audio_seed,
        },
        "webgl": {
            "noiseEnabled": True,
            "vendor": "Google Inc. (NVIDIA)",
            "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "maxAnisotropy": 16,
            "maxTextureSize": 16384,
            "maxViewportDims": [16384, 16384],
            "glParamValues": {},
            "extensions": [],
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
            "hardwareConcurrency": 8,
            "deviceMemory": 8,
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
                "Arial", "Calibri", "Cambria", "Consolas", "Courier New",
                "Georgia", "Impact", "Segoe UI", "Tahoma", "Times New Roman", "Verdana"
            ]
        },
        "mediaDevices": {
            "audioInputs": 1,
            "audioOutputs": 1,
            "videoInputs": 0,
        },
        "proxy": proxy_info or {},
    }
    return config


def write_profile_config_files(profile_dir: str, config: Dict[str, Any]) -> None:
    """Ghi file cấu hình vào profile dir để cả 2 định dạng `data.huynhthang` và `data.orbita` đều nhận diện."""
    os.makedirs(profile_dir, exist_ok=True)
    for filename in ["data.huynhthang", "data.orbita"]:
        target_path = os.path.join(profile_dir, filename)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
```

---

### Bước 2: Nâng Cấp Binary Trình Duyệt (`Browser/orbita-browser-144`)

Sao chép toàn bộ gói `Chrome-bin` phiên bản `144.0.7559.96` vào thư mục `Browser/orbita-browser-144` của VIBE_AUTO_UPLOAD-LP.

Cập nhật `browser_patchright_glue.py`:
- Thêm đường dẫn `Browser/orbita-browser-144/chrome.exe` vào danh sách ưu tiên hàng đầu trong hàm `get_custom_browser_executable_path()`.
- Thêm cờ `--ht-auto` và `--disable-session-crashed-bubble` vào mảng arguments của Patchright.
- Tự động gọi `write_profile_config_files()` trước khi khởi tạo browser context.

---

## 6. Bảng Kiểm Tra Đánh Giá Mức Độ An Toàn (Anti-Detect Test Checklist)

Sau khi tích hợp, tiến hành kiểm tra trên các trang đo lường vân tay trình duyệt uy tín:

| Trang kiểm tra | Mục tiêu đánh giá | Kết quả mong đợi |
|---|---|---|
| **`browserleaks.com/canvas`** | Đánh giá Canvas Fingerprint | 100% Unique Signature, Hash không bị trùng với máy khác |
| **`browserleaks.com/webrtc`** | Kiểm tra rò rỉ WebRTC | IP trả về khớp 100% với Proxy Exit IP, không lộ IP nội bộ |
| **`browserleaks.com/webgl`** | Kiểm tra WebGL & GPU | Card đồ họa hiển thị ANGLE NVIDIA Direct3D11 chuẩn Windows |
| **`browserscan.net`** | Tổng điểm Anti-Bot Score | Điểm số đạt 98–100% (Không phát hiện dấu hiệu tự động hóa) |
| **`tiktok.com/tiktokstudio`** | Tải video & Duy trì Session | Upload video thành công, session duy trì xuyên suốt |

---

*Tài liệu này là cẩm nang kỹ thuật chính thức để nâng cấp hệ thống Anti-Detect cho VIBE_AUTO_UPLOAD-LP.*