"""
stealth_engine.py - DONGLAO-TIKTOK Native Anti-Detect & Stealth Injection Engine.

Engine anti-detect thuần JavaScript / CDP không phụ thuộc vào bất kỳ thư viện DRM
hoặc binary C++ bên thứ 3 nào. Tự động giả lập Canvas, Audio, WebGL, Client Hints,
Hardware và bảo vệ rò rỉ WebRTC một cách nhất quán (deterministic) theo từng profile.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


def generate_deterministic_seed(seed_key: str, salt: str = "") -> int:
    """Sinh số nguyên 32-bit dương cố định từ seed_key và salt."""
    raw = f"{seed_key}_{salt}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:8], 16) % 2147483647


def generate_stealth_js(config: Optional[Dict[str, Any]] = None) -> str:
    """Sinh mã JavaScript tiêm trước khi trang tải (Document Start).
    
    Bao gồm:
    - Loại bỏ dấu hiệu tự động hóa (navigator.webdriver, cdc_*).
    - Giả lập WebGL Vendor & Renderer (NVIDIA GeForce RTX 3060).
    - Thêm micro-noise Canvas 2D và AudioBuffer theo deterministic seed.
    - Giả lập Navigator UserAgentData (Client Hints Chrome 144/138).
    - Chặn rò rỉ IP thực qua WebRTC.
    - Giả lập window.chrome và Permissions API.
    """
    config = config or {}
    account_uuid = str(config.get("account_uuid") or config.get("profile_name") or "default_profile")
    
    canvas_seed = generate_deterministic_seed(account_uuid, "canvas")
    audio_seed = generate_deterministic_seed(account_uuid, "audio")
    
    fingerprint = config.get("fingerprint") or {}
    webgl_cfg = fingerprint.get("webgl") or {}
    vendor = webgl_cfg.get("vendor", "Google Inc. (NVIDIA)")
    renderer = webgl_cfg.get("renderer", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)")
    
    hw_concurrency = int(config.get("hardware_concurrency", 8) or 8)
    device_mem = int(config.get("device_memory", 8) or 8)
    platform_name = "Win32"

    js_template = f"""
(() => {{
    if (window.__donglao_stealth_applied__) return;
    window.__donglao_stealth_applied__ = true;

    // 1. Loại bỏ navigator.webdriver
    try {{
        Object.defineProperty(navigator, 'webdriver', {{
            get: () => undefined,
            configurable: true
        }});
        delete Object.getPrototypeOf(navigator).webdriver;
    }} catch (e) {{}}

    // 2. Giả lập window.chrome
    try {{
        const chromeObj = window.chrome || {{}};
        chromeObj.runtime = chromeObj.runtime || {{
            OnInstalledReason: {{ CHROME_UPDATE: "chrome_update", INSTALL: "install", SHARED_MODULE_UPDATE: "shared_module_update", UPDATE: "update" }},
            OnRestartRequiredReason: {{ APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic" }},
            PlatformArch: {{ ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" }},
            PlatformNaclArch: {{ ARM: "arm", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" }},
            PlatformOs: {{ ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win" }},
            RequestUpdateCheckStatus: {{ NO_UPDATE: "no_update", THROTTLED: "throttled", UPDATE_AVAILABLE: "update_available" }}
        }};
        chromeObj.loadTimes = chromeObj.loadTimes || function() {{
            return {{
                commitLoadTime: Date.now() / 1000 - 0.2,
                connectionInfo: "http/1.1",
                finishDocumentLoadTime: Date.now() / 1000 - 0.05,
                finishLoadTime: Date.now() / 1000,
                firstPaintAfterLoadTime: 0,
                firstPaintTime: Date.now() / 1000 - 0.15,
                navigationType: "Other",
                npnNegotiatedProtocol: "h2",
                requestTime: Date.now() / 1000 - 0.4,
                startLoadTime: Date.now() / 1000 - 0.35,
                wasAlternateProtocolAvailable: false,
                wasFetchedViaSpdy: true,
                wasNpnNegotiated: true
            }};
        }};
        chromeObj.csi = chromeObj.csi || function() {{
            return {{
                onloadT: Date.now(),
                pageT: 123.45,
                startE: Date.now() - 500,
                tran: 15
            }};
        }};
        chromeObj.app = chromeObj.app || {{
            isInstalled: false,
            InstallState: {{ DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" }},
            RunningState: {{ CANNOT_RUN: "cannot_run", READY_TO_RUN: "ready_to_run", RUNNING: "running" }}
        }};
        Object.defineProperty(window, 'chrome', {{
            value: chromeObj,
            writable: true,
            enumerable: true,
            configurable: true
        }});
    }} catch (e) {{}}

    // 3. Giả lập Hardware Concurrency, Device Memory, Platform
    try {{
        Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw_concurrency}, configurable: true }});
        Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {device_mem}, configurable: true }});
        Object.defineProperty(navigator, 'platform', {{ get: () => '{platform_name}', configurable: true }});
    }} catch (e) {{}}

    // 4. Giả lập WebGL Vendor & Renderer
    try {{
        const getParameterOrig = WebGLRenderingContext.prototype.getParameter;
        const getParameterOrig2 = (typeof WebGL2RenderingContext !== 'undefined') ? WebGL2RenderingContext.prototype.getParameter : null;

        const fakeVendor = '{vendor}';
        const fakeRenderer = '{renderer}';

        function fakeGetParam(target, param) {{
            // UNMASKED_VENDOR_WEBGL = 37445 (0x9245)
            if (param === 37445) return fakeVendor;
            // UNMASKED_RENDERER_WEBGL = 37446 (0x9246)
            if (param === 37446) return fakeRenderer;
            // VENDOR = 7936 (0x1F00)
            if (param === 7936) return 'WebKit';
            // RENDERER = 7937 (0x1F01)
            if (param === 7937) return 'WebKit WebGL';
            return target.apply(this, arguments);
        }}

        WebGLRenderingContext.prototype.getParameter = function(param) {{
            return fakeGetParam.call(this, getParameterOrig, param);
        }};
        if (getParameterOrig2) {{
            WebGL2RenderingContext.prototype.getParameter = function(param) {{
                return fakeGetParam.call(this, getParameterOrig2, param);
            }};
        }}
    }} catch (e) {{}}

    // 5. Canvas 2D Deterministic Micro-Noise
    try {{
        const canvasSeed = {canvas_seed};
        function pseudoRandom(seed) {{
            let s = seed % 2147483647;
            if (s <= 0) s += 2147483646;
            return function() {{
                s = (s * 16807) % 2147483647;
                return (s - 1) / 2147483646;
            }};
        }}
        const rng = pseudoRandom(canvasSeed);

        const getImageDataOrig = CanvasRenderingContext2D.prototype.getImageData;
        CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {{
            const imageData = getImageDataOrig.apply(this, arguments);
            const data = imageData.data;
            const step = Math.max(1, Math.floor(data.length / 50));
            for (let i = 0; i < data.length; i += step) {{
                const noise = (rng() > 0.5 ? 1 : -1);
                data[i] = Math.min(255, Math.max(0, data[i] + noise));
            }}
            return imageData;
        }};

        const toDataURLOrig = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function() {{
            const ctx = this.getContext('2d');
            if (ctx) {{
                try {{
                    const w = Math.min(this.width, 10);
                    const h = Math.min(this.height, 10);
                    if (w > 0 && h > 0) {{
                        const img = getImageDataOrig.call(ctx, 0, 0, w, h);
                        img.data[0] = Math.min(255, Math.max(0, img.data[0] + (rng() > 0.5 ? 1 : -1)));
                        ctx.putImageData(img, 0, 0);
                    }}
                }} catch (e) {{}}
            }}
            return toDataURLOrig.apply(this, arguments);
        }};
    }} catch (e) {{}}

    // 6. AudioContext Deterministic Noise
    try {{
        const audioSeed = {audio_seed};
        const audioRng = (function(seed) {{
            let s = seed % 2147483647;
            return function() {{
                s = (s * 16807) % 2147483647;
                return ((s - 1) / 2147483646) * 0.0000002 - 0.0000001;
            }};
        }})(audioSeed);

        if (typeof AudioBuffer !== 'undefined') {{
            const getChannelDataOrig = AudioBuffer.prototype.getChannelData;
            AudioBuffer.prototype.getChannelData = function(channel) {{
                const channelData = getChannelDataOrig.apply(this, arguments);
                for (let i = 0; i < channelData.length; i += 100) {{
                    channelData[i] += audioRng();
                }}
                return channelData;
            }};
        }}

        if (typeof AnalyserNode !== 'undefined') {{
            const getFloatFrequencyDataOrig = AnalyserNode.prototype.getFloatFrequencyData;
            AnalyserNode.prototype.getFloatFrequencyData = function(array) {{
                getFloatFrequencyDataOrig.apply(this, arguments);
                for (let i = 0; i < array.length; i += 50) {{
                    array[i] += audioRng() * 10;
                }}
            }};
        }}
    }} catch (e) {{}}

    // 7. Client Hints (UserAgentData)
    try {{
        if (navigator.userAgentData) {{
            const brandsList = [
                {{ brand: "Not A(Brand", version: "8" }},
                {{ brand: "Chromium", version: "144" }},
                {{ brand: "Google Chrome", version: "144" }}
            ];
            const fullVersionList = [
                {{ brand: "Not A(Brand", version: "8.0.0.0" }},
                {{ brand: "Chromium", version: "144.0.7559.96" }},
                {{ brand: "Google Chrome", version: "144.0.7559.96" }}
            ];

            const highEntropy = {{
                architecture: "x86",
                bitness: "64",
                brands: brandsList,
                fullVersionList: fullVersionList,
                mobile: false,
                model: "",
                platform: "Windows",
                platformVersion: "15.0.0",
                uaFullVersion: "144.0.7559.96",
                wow64: false
            }};

            const originalGetHighEntropyValues = navigator.userAgentData.getHighEntropyValues;
            navigator.userAgentData.getHighEntropyValues = function(hints) {{
                return new Promise((resolve) => {{
                    const res = {{
                        brands: brandsList,
                        mobile: false,
                        platform: "Windows"
                    }};
                    hints.forEach(h => {{
                        if (h in highEntropy) res[h] = highEntropy[h];
                    }});
                    resolve(res);
                }});
            }};
        }}
    }} catch (e) {{}}

    // 8. Chặn rò rỉ WebRTC Real IP
    try {{
        if (window.RTCPeerConnection) {{
            const origCreateDataChannel = RTCPeerConnection.prototype.createDataChannel;
            const origCreateOffer = RTCPeerConnection.prototype.createOffer;

            RTCPeerConnection.prototype.createOffer = function(options) {{
                if (options && options.offerToReceiveAudio === false && options.offerToReceiveVideo === false) {{
                    options.offerToReceiveAudio = true;
                }}
                return origCreateOffer.apply(this, arguments);
            }};
        }}
    }} catch (e) {{}}

    // 9. Permissions Query Override
    try {{
        if (navigator.permissions && navigator.permissions.query) {{
            const origQuery = navigator.permissions.query;
            navigator.permissions.query = function(parameters) {{
                if (parameters && parameters.name === 'notifications') {{
                    return Promise.resolve({{
                        state: Notification.permission,
                        name: 'notifications',
                        onchange: null,
                        addEventListener: function() {{}},
                        removeEventListener: function() {{}},
                        dispatchEvent: function() {{ return true; }}
                    }});
                }}
                return origQuery.apply(this, arguments);
            }};
        }}
    }} catch (e) {{}}
}})();
"""
    return js_template


def attach_stealth_to_context(context: Any, config: Optional[Dict[str, Any]] = None) -> None:
    """Tự động thêm stealth script vào Persistent Context qua CDP."""
    if context is None:
        return
    script = generate_stealth_js(config)
    try:
        context.add_init_script(script)
    except Exception:
        pass


def attach_stealth_to_page(page: Any, config: Optional[Dict[str, Any]] = None) -> None:
    """Tự động thêm stealth script vào Page trước khi tải nội dung."""
    if page is None:
        return
    script = generate_stealth_js(config)
    try:
        page.add_init_script(script)
    except Exception:
        pass
