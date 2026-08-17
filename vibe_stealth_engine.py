"""
stealth_engine.py - DONGLAO-TIKTOK Native Anti-Detect & Stealth Injection Engine.

Engine anti-detect thuần JavaScript / CDP không phụ thuộc vào bất kỳ thư viện DRM
hoặc binary C++ bên thứ 3 nào. Tự động giả lập Canvas, Audio, WebGL (42 params, 34 extensions),
Client Hints (Chrome 149, formFactors), Plugins (5 PDF viewers), Hardware và bảo vệ rò rỉ
WebRTC một cách nhất quán (deterministic) theo từng profile.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

# Constants matching profile_config_engine.py
CHROME_MAJOR = "149"
CHROME_FULL_VERSION = "149.0.7827.55"

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
    "EXT_color_buffer_float",
    "EXT_disjoint_timer_query_webgl2",
    "EXT_texture_norm16",
    "OES_draw_buffers_indexed",
    "OVR_multiview2",
]


def generate_deterministic_seed(seed_key: str, salt: str = "") -> int:
    """Sinh số nguyên 32-bit dương cố định từ seed_key và salt."""
    raw = f"{seed_key}_{salt}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:8], 16) % 2147483647


def generate_stealth_js(config: Optional[Dict[str, Any]] = None) -> str:
    """Sinh mã JavaScript tiêm trước khi trang tải (Document Start).
    
    Bao gồm:
    - Loại bỏ dấu hiệu tự động hóa (navigator.webdriver, cdc_*).
    - Giả lập WebGL Vendor, Renderer, 42 GL Parameters và 34 Extensions.
    - Thêm micro-noise Canvas 2D và AudioBuffer theo deterministic seed.
    - Giả lập Navigator UserAgentData (Client Hints Chrome 149, formFactors: Desktop).
    - Chặn rò rỉ IP thực qua WebRTC (sanitization SDP & onicecandidate filter).
    - Giả lập 5 PDF Viewer Plugins và MimeTypes.
    - Giả lập window.chrome và Permissions API.
    """
    config = config or {}
    account_uuid = str(config.get("account_uuid") or config.get("profile_name") or "default_profile")
    
    canvas_seed = generate_deterministic_seed(account_uuid, "canvas")
    audio_seed = generate_deterministic_seed(account_uuid, "audio")
    
    fingerprint = config.get("fingerprint") or {}
    webgl_cfg = config.get("webgl") or fingerprint.get("webgl") or {}
    vendor = webgl_cfg.get("vendor", "Google Inc. (NVIDIA)")
    renderer = webgl_cfg.get("renderer", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)")
    
    # WebGL extensions
    extensions = webgl_cfg.get("extensions") or DEFAULT_WEBGL_EXTENSIONS
    extensions_json = json.dumps(extensions)
    
    # WebRTC fake IP
    webrtc_cfg = config.get("webrtc") or {}
    fake_ip = str(webrtc_cfg.get("fakePublicIP") or config.get("fake_ip") or "").strip()
    
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

    // 4. Giả lập WebGL Vendor, Renderer, 42 GL Params & 34 Extensions
    try {{
        const fakeVendor = '{vendor}';
        const fakeRenderer = '{renderer}';
        const fakeExtensions = {extensions_json};

        const glParamMap = {{
            // Unmasked
            37445: fakeVendor, // UNMASKED_VENDOR_WEBGL
            37446: fakeRenderer, // UNMASKED_RENDERER_WEBGL
            7936: 'WebKit', // VENDOR
            7937: 'WebKit WebGL', // RENDERER
            // Color bits
            3410: 8, // RED_BITS
            3411: 8, // GREEN_BITS
            3412: 8, // BLUE_BITS
            3413: 8, // ALPHA_BITS
            3414: 24, // DEPTH_BITS
            3415: 8, // STENCIL_BITS
            // Texture & Render sizes
            3379: 16384, // MAX_TEXTURE_SIZE
            34076: 16384, // MAX_CUBE_MAP_TEXTURE_SIZE
            34024: 16384, // MAX_RENDERBUFFER_SIZE
            32883: 2048, // MAX_3D_TEXTURE_SIZE
            35071: 2048, // MAX_ARRAY_TEXTURE_LAYERS
            36063: 8, // MAX_COLOR_ATTACHMENTS
            34852: 8, // MAX_DRAW_BUFFERS
            36183: 8, // MAX_SAMPLES
            34930: 16, // MAX_TEXTURE_IMAGE_UNITS
            35660: 16, // MAX_VERTEX_TEXTURE_IMAGE_UNITS
            35661: 32, // MAX_COMBINED_TEXTURE_IMAGE_UNITS
            34045: 2, // MAX_TEXTURE_LOD_BIAS
            34921: 16, // MAX_VERTEX_ATTRIBS
            36347: 4096, // MAX_VERTEX_UNIFORM_VECTORS
            35658: 16384, // MAX_VERTEX_UNIFORM_COMPONENTS
            35371: 12, // MAX_VERTEX_UNIFORM_BLOCKS
            37154: 120, // MAX_VERTEX_OUTPUT_COMPONENTS
            36349: 1024, // MAX_FRAGMENT_UNIFORM_VECTORS
            35657: 4096, // MAX_FRAGMENT_UNIFORM_COMPONENTS
            35373: 12, // MAX_FRAGMENT_UNIFORM_BLOCKS
            37157: 120, // MAX_FRAGMENT_INPUT_COMPONENTS
            36348: 30, // MAX_VARYING_VECTORS
            35659: 120, // MAX_VARYING_COMPONENTS
            35377: 212992, // MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS
            35379: 200704, // MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS
            35374: 24, // MAX_COMBINED_UNIFORM_BLOCKS
            35376: 65536, // MAX_UNIFORM_BLOCK_SIZE
            35375: 24, // MAX_UNIFORM_BUFFER_BINDINGS
            35380: 256, // UNIFORM_BUFFER_OFFSET_ALIGNMENT
            35077: 7, // MAX_PROGRAM_TEXEL_OFFSET
            35076: -8, // MIN_PROGRAM_TEXEL_OFFSET
            35978: 120, // MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS
            35979: 4, // MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS
            35968: 4, // MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS
        }};

        function fakeGetParam(target, param) {{
            if (param in glParamMap) {{
                return glParamMap[param];
            }}
            if (param === 3386) {{ // MAX_VIEWPORT_DIMS
                return new Int32Array([32768, 32768]);
            }}
            if (param === 33902) {{ // ALIASED_LINE_WIDTH_RANGE
                return new Float32Array([1, 1]);
            }}
            if (param === 33901) {{ // ALIASED_POINT_SIZE_RANGE
                return new Float32Array([1, 1024]);
            }}
            return target.apply(this, arguments);
        }}

        const getParameterOrig1 = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {{
            return fakeGetParam.call(this, getParameterOrig1, param);
        }};
        WebGLRenderingContext.prototype.getSupportedExtensions = function() {{
            return fakeExtensions.slice();
        }};

        if (typeof WebGL2RenderingContext !== 'undefined') {{
            const getParameterOrig2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(param) {{
                return fakeGetParam.call(this, getParameterOrig2, param);
            }};
            WebGL2RenderingContext.prototype.getSupportedExtensions = function() {{
                return fakeExtensions.slice();
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

    // 7. Client Hints (UserAgentData Chrome 149 + formFactors: Desktop)
    try {{
        if (navigator.userAgentData) {{
            const brandsList = [
                {{ brand: "Not/A)Brand", version: "8" }},
                {{ brand: "Chromium", version: "{CHROME_MAJOR}" }},
                {{ brand: "Google Chrome", version: "{CHROME_MAJOR}" }}
            ];
            const fullVersionList = [
                {{ brand: "Not/A)Brand", version: "8.0.0.0" }},
                {{ brand: "Chromium", version: "{CHROME_FULL_VERSION}" }},
                {{ brand: "Google Chrome", version: "{CHROME_FULL_VERSION}" }}
            ];

            const highEntropy = {{
                architecture: "x86",
                bitness: "64",
                brands: brandsList,
                formFactors: ["Desktop"],
                fullVersionList: fullVersionList,
                mobile: false,
                model: "",
                platform: "Windows",
                platformVersion: "19.0.0",
                uaFullVersion: "{CHROME_FULL_VERSION}",
                wow64: false
            }};

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

    // 8. Chặn rò rỉ WebRTC Real IP (SDP Sanitization + onicecandidate Filter)
    try {{
        const fakeIP = '{fake_ip}';
        if (window.RTCPeerConnection) {{
            const origCreateOffer = RTCPeerConnection.prototype.createOffer;
            const origCreateAnswer = RTCPeerConnection.prototype.createAnswer;
            const origSetLocalDescription = RTCPeerConnection.prototype.setLocalDescription;

            function sanitizeSDP(sdp) {{
                if (!sdp || typeof sdp.sdp !== 'string') return sdp;
                if (!fakeIP) return sdp;
                // Thay thế địa chỉ IPv4 cục bộ/thực trong SDP bằng fakeIP
                const lines = sdp.sdp.split('\\r\\n').map(line => {{
                    if (line.startsWith('a=candidate:') || line.startsWith('c=IN IP4')) {{
                        return line.replace(/\\b(?:\\d{{1,3}}\\.){{3}}\\d{{1,3}}\\b/g, fakeIP);
                    }}
                    return line;
                }});
                return new RTCSessionDescription({{ type: sdp.type, sdp: lines.join('\\r\\n') }});
            }}

            RTCPeerConnection.prototype.createOffer = function(options) {{
                return origCreateOffer.apply(this, arguments).then(offer => sanitizeSDP(offer));
            }};

            RTCPeerConnection.prototype.createAnswer = function(options) {{
                return origCreateAnswer.apply(this, arguments).then(answer => sanitizeSDP(answer));
            }};

            RTCPeerConnection.prototype.setLocalDescription = function(desc) {{
                return origSetLocalDescription.call(this, sanitizeSDP(desc));
            }};

            // Intercept addEventListener & onicecandidate
            const origAddEventListener = RTCPeerConnection.prototype.addEventListener;
            RTCPeerConnection.prototype.addEventListener = function(type, listener, options) {{
                if (type === 'icecandidate' && typeof listener === 'function' && fakeIP) {{
                    const wrappedListener = function(event) {{
                        if (event && event.candidate && event.candidate.candidate) {{
                            const sanitizedCandidateStr = event.candidate.candidate.replace(
                                /\\b(?:\\d{{1,3}}\\.){{3}}\\d{{1,3}}\\b/g, fakeIP
                            );
                            try {{
                                Object.defineProperty(event.candidate, 'candidate', {{
                                    get: () => sanitizedCandidateStr
                                }});
                                Object.defineProperty(event.candidate, 'address', {{
                                    get: () => fakeIP
                                }});
                            }} catch (err) {{}}
                        }}
                        return listener.apply(this, arguments);
                    }};
                    return origAddEventListener.call(this, type, wrappedListener, options);
                }}
                return origAddEventListener.apply(this, arguments);
            }};
        }}
    }} catch (e) {{}}

    // 9. Giả lập 5 PDF Viewer Plugins & MimeTypes
    try {{
        const mockMimeTypes = [
            {{ type: "application/pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: null }},
            {{ type: "text/pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: null }}
        ];

        const mockPlugins = [
            {{ name: "PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format", length: 2 }},
            {{ name: "Chrome PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format", length: 2 }},
            {{ name: "Chromium PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format", length: 2 }},
            {{ name: "Microsoft Edge PDF Viewer", filename: "internal-pdf-viewer", description: "Portable Document Format", length: 2 }},
            {{ name: "WebKit built-in PDF", filename: "internal-pdf-viewer", description: "Portable Document Format", length: 2 }}
        ];

        const pluginArray = Object.create(PluginArray.prototype);
        mockPlugins.forEach((p, idx) => {{
            const pObj = Object.create(Plugin.prototype);
            Object.defineProperty(pObj, 'name', {{ value: p.name }});
            Object.defineProperty(pObj, 'filename', {{ value: p.filename }});
            Object.defineProperty(pObj, 'description', {{ value: p.description }});
            Object.defineProperty(pObj, 'length', {{ value: p.length }});
            pluginArray[idx] = pObj;
            pluginArray[p.name] = pObj;
        }});
        Object.defineProperty(pluginArray, 'length', {{ value: mockPlugins.length }});
        pluginArray.item = function(index) {{ return this[index] || null; }};
        pluginArray.namedItem = function(name) {{ return this[name] || null; }};
        pluginArray.refresh = function() {{}};

        Object.defineProperty(navigator, 'plugins', {{
            get: () => pluginArray,
            enumerable: true,
            configurable: true
        }});
    }} catch (e) {{}}

    // 10. Permissions Query Override
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
