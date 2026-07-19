import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests


GEOIP_URL = "https://ipwho.is/"
TIKTOK_ORIGINS = ("https://www.tiktok.com",)
WEBRTC_POLICIES = ("controlled", "block")

DEVICE_PRESETS = {
    "desktop": {
        "label": "Desktop Chrome 123",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        "platform": "Windows",
        "mobile": False,
        "touch": False,
        "width": 1366,
        "height": 768,
        "device_scale_factor": 1,
        "user_agent_metadata": {
            "brands": [
                {"brand": "Chromium", "version": "123"},
                {"brand": "Not:A-Brand", "version": "8"},
            ],
            "fullVersionList": [
                {"brand": "Chromium", "version": "123.0.6312.59"},
                {"brand": "Not:A-Brand", "version": "8.0.0.0"},
            ],
            "platform": "Windows",
            "platformVersion": "10.0.0",
            "architecture": "x86",
            "model": "",
            "mobile": False,
            "bitness": "64",
            "wow64": False,
        },
    },
    "pixel": {
        "label": "Google Pixel",
        "user_agent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36"
        ),
        "platform": "Android",
        "mobile": True,
        "touch": True,
        "width": 412,
        "height": 915,
        "device_scale_factor": 2.625,
        "user_agent_metadata": {
            "brands": [
                {"brand": "Chromium", "version": "123"},
                {"brand": "Not:A-Brand", "version": "8"},
            ],
            "fullVersionList": [
                {"brand": "Chromium", "version": "123.0.6312.59"},
                {"brand": "Not:A-Brand", "version": "8.0.0.0"},
            ],
            "platform": "Android",
            "platformVersion": "13.0.0",
            "architecture": "",
            "model": "Pixel 7",
            "mobile": True,
            "bitness": "",
            "wow64": False,
        },
    },
    "iphone_x": {
        "label": "iPhone X (visual)",
        "user_agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
            "Mobile/15E148 Safari/604.1"
        ),
        "platform": "iPhone",
        "mobile": True,
        "touch": True,
        "width": 375,
        "height": 812,
        "device_scale_factor": 3,
        "user_agent_metadata": None,
    },
    "custom": {
        "label": "Custom",
        "mobile": False,
        "touch": False,
        "width": 1366,
        "height": 768,
        "device_scale_factor": 1,
        "user_agent_metadata": None,
    },
}


def _seed_number(seed, namespace):
    digest = hashlib.sha256(f"{seed}:{namespace}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def ensure_fingerprint_defaults(fingerprint=None, seed=""):
    fp = dict(fingerprint or {})
    stable_seed = str(seed or fp.get("user_agent") or "default")
    preset_name = fp.get("device_preset", "desktop")
    if preset_name not in DEVICE_PRESETS:
        preset_name = "custom"
    preset = DEVICE_PRESETS[preset_name]

    fp.setdefault("device_preset", preset_name)
    if preset_name != "custom":
        # Generated profiles must match the bundled Chromium major version.
        fp["user_agent"] = preset.get("user_agent", DEVICE_PRESETS["desktop"]["user_agent"])
        fp["platform"] = preset.get("platform", "Windows")
        fp["user_agent_metadata"] = preset.get("user_agent_metadata")
    else:
        fp.setdefault("user_agent", DEVICE_PRESETS["desktop"]["user_agent"])
        fp.setdefault("platform", "Windows")
        fp.setdefault("user_agent_metadata", None)
    fp.setdefault("webrtc_policy", "controlled")
    if fp["webrtc_policy"] not in WEBRTC_POLICIES:
        fp["webrtc_policy"] = "controlled"
    fp.setdefault("fingerprint_protection", True)
    fp.setdefault("canvas_noise_seed", _seed_number(stable_seed, "canvas"))
    fp.setdefault("webgl_noise_seed", _seed_number(stable_seed, "webgl"))
    fp.setdefault("audio_noise_seed", _seed_number(stable_seed, "audio"))
    fp.setdefault("audio_noise", 0.0000001)
    return fp


def apply_device_preset(fingerprint, preset_name):
    if preset_name not in DEVICE_PRESETS:
        preset_name = "desktop"
    fp = dict(fingerprint or {})
    preset = DEVICE_PRESETS[preset_name]
    fp["device_preset"] = preset_name
    if preset_name != "custom":
        fp["user_agent"] = preset["user_agent"]
        fp["platform"] = preset["platform"]
        fp["user_agent_metadata"] = preset["user_agent_metadata"]
        fp["window_width"] = preset["width"]
        fp["window_height"] = preset["height"]
    else:
        desktop = DEVICE_PRESETS["desktop"]
        fp["user_agent"] = desktop["user_agent"]
        fp["platform"] = desktop["platform"]
        fp["window_width"] = desktop["width"]
        fp["window_height"] = desktop["height"]
        fp["user_agent_metadata"] = None
    return ensure_fingerprint_defaults(fp)


def proxy_cache_key(proxy_data):
    if not proxy_data:
        return ""
    normalized = "|".join(
        str(proxy_data.get(key, "")).strip()
        for key in ("ip", "port", "user", "pass")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def geo_cache_is_current(fingerprint, proxy_data):
    if not proxy_data or not fingerprint:
        return False
    geo = fingerprint.get("geolocation") or {}
    return bool(
        fingerprint.get("timezone")
        and _valid_coordinates(geo.get("latitude"), geo.get("longitude"))
        and fingerprint.get("geo_proxy_hash") == proxy_cache_key(proxy_data)
    )


def _proxy_url(proxy_data):
    host = str(proxy_data.get("ip", "")).strip()
    port = str(proxy_data.get("port", "")).strip()
    if not host or not port:
        raise ValueError("Proxy thiếu IP hoặc port")
    user = str(proxy_data.get("user", ""))
    password = str(proxy_data.get("pass", ""))
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user or password else ""
    return f"http://{auth}{host}:{port}"


def _valid_coordinates(latitude, longitude):
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _valid_timezone(value):
    text = str(value or "").strip()
    return bool(text == "UTC" or re.fullmatch(r"[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)+", text))


def normalize_geoip_payload(payload, proxy_data):
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise ValueError(str((payload or {}).get("message") or "GeoIP trả dữ liệu không hợp lệ"))
    timezone_data = payload.get("timezone") or {}
    timezone_id = timezone_data.get("id") if isinstance(timezone_data, dict) else timezone_data
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if not _valid_timezone(timezone_id):
        raise ValueError("GeoIP không trả timezone IANA hợp lệ")
    if not _valid_coordinates(latitude, longitude):
        raise ValueError("GeoIP không trả tọa độ hợp lệ")
    return {
        "timezone": str(timezone_id),
        "geolocation": {
            "latitude": float(latitude),
            "longitude": float(longitude),
            "accuracy": 50,
        },
        "geo_exit_ip": str(payload.get("ip") or ""),
        "geo_proxy_hash": proxy_cache_key(proxy_data),
        "geo_resolved_at": datetime.now(timezone.utc).isoformat(),
        "geo_source": "ipwho.is",
    }


def resolve_geoip(proxy_data, timeout=8, request_get=requests.get):
    proxy_url = _proxy_url(proxy_data)
    response = request_get(
        GEOIP_URL,
        proxies={"http": proxy_url, "https": proxy_url},
        headers={"Accept": "application/json", "User-Agent": "TikTokAutoUploader/GeoIP"},
        timeout=timeout,
    )
    response.raise_for_status()
    return normalize_geoip_payload(response.json(), proxy_data)


def chrome_environment_preferences(fingerprint):
    fp = ensure_fingerprint_defaults(fingerprint)
    policy = fp.get("webrtc_policy", "controlled")
    prefs = {
        "webrtc.ip_handling_policy": "disable_non_proxied_udp",
        "webrtc.multiple_routes_enabled": False,
        "webrtc.nonproxied_udp_enabled": False,
    }
    if policy == "block":
        prefs.update({
            "profile.default_content_setting_values.media_stream_mic": 2,
            "profile.default_content_setting_values.media_stream_camera": 2,
        })
    return prefs


def chrome_environment_arguments(fingerprint):
    fp = ensure_fingerprint_defaults(fingerprint)
    args = ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
    if fp.get("webrtc_policy") == "block":
        args.append("--disable-webrtc")
    return args


def device_override(fingerprint):
    fp = ensure_fingerprint_defaults(fingerprint)
    preset = DEVICE_PRESETS.get(fp.get("device_preset"), DEVICE_PRESETS["custom"])
    user_agent = fp.get("user_agent") or DEVICE_PRESETS["desktop"]["user_agent"]
    platform = fp.get("platform") or preset.get("platform") or "Windows"
    return {
        "user_agent": user_agent,
        "platform": platform,
        "metadata": fp.get("user_agent_metadata"),
        "mobile": bool(preset.get("mobile")),
        "touch": bool(preset.get("touch")),
        "width": int(fp.get("window_width") or preset.get("width", 1366)),
        "height": int(fp.get("window_height") or preset.get("height", 768)),
        "device_scale_factor": float(preset.get("device_scale_factor", 1)),
    }


FINGERPRINT_SCRIPT = r"""
(() => {
    if (globalThis.__privacyFingerprintInstalled) return;
    Object.defineProperty(globalThis, '__privacyFingerprintInstalled', { value: true });
    const canvasSeed = __CANVAS_SEED__ >>> 0;
    const webglSeed = __WEBGL_SEED__ >>> 0;
    const audioSeed = __AUDIO_SEED__ >>> 0;
    const audioNoise = __AUDIO_NOISE__;
    const protectionEnabled = __PROTECTION_ENABLED__;
    const webrtcBlocked = __WEBRTC_BLOCKED__;
    const uaDataDisabled = __UA_DATA_DISABLED__;

    const noiseByte = (index, seed) => (((index * 1103515245 + seed) >>> 16) & 1) ? 1 : -1;
    const perturbPixels = (data, seed) => {
        if (!protectionEnabled || !data) return data;
        const stride = Math.max(64, Math.floor(data.length / 128));
        for (let i = seed % stride; i < data.length; i += stride) {
            const delta = data.BYTES_PER_ELEMENT > 1
                ? noiseByte(i, seed) * 0.000001
                : noiseByte(i, seed);
            if (data.BYTES_PER_ELEMENT > 1) data[i] += delta;
            else data[i] = Math.max(0, Math.min(255, data[i] + delta));
        }
        return data;
    };

    const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(...args) {
        const result = originalGetImageData.apply(this, args);
        perturbPixels(result.data, canvasSeed);
        return result;
    };

    const noisyCanvasCopy = canvas => {
        const copy = document.createElement('canvas');
        copy.width = canvas.width;
        copy.height = canvas.height;
        const context = copy.getContext('2d');
        context.drawImage(canvas, 0, 0);
        const pixels = originalGetImageData.call(context, 0, 0, copy.width, copy.height);
        perturbPixels(pixels.data, canvasSeed);
        context.putImageData(pixels, 0, 0);
        return copy;
    };
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(...args) {
        if (!protectionEnabled || !this.width || !this.height) return originalToDataURL.apply(this, args);
        return originalToDataURL.apply(noisyCanvasCopy(this), args);
    };
    const originalToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function(callback, ...args) {
        if (!protectionEnabled || !this.width || !this.height) return originalToBlob.call(this, callback, ...args);
        return originalToBlob.call(noisyCanvasCopy(this), callback, ...args);
    };

    const patchWebGL = prototype => {
        if (!prototype) return;
        const originalGetParameter = prototype.getParameter;
        prototype.getParameter = function(param) {
            if (protectionEnabled && param === 37445) return __WEBGL_VENDOR__;
            if (protectionEnabled && param === 37446) return __WEBGL_RENDERER__;
            return originalGetParameter.call(this, param);
        };
        const originalReadPixels = prototype.readPixels;
        prototype.readPixels = function(...args) {
            const result = originalReadPixels.apply(this, args);
            perturbPixels(args[6], webglSeed);
            return result;
        };
    };
    patchWebGL(globalThis.WebGLRenderingContext && WebGLRenderingContext.prototype);
    patchWebGL(globalThis.WebGL2RenderingContext && WebGL2RenderingContext.prototype);

    const patchAnalyser = prototype => {
        if (!prototype) return;
        for (const method of ['getFloatFrequencyData', 'getFloatTimeDomainData']) {
            const original = prototype[method];
            if (!original) continue;
            prototype[method] = function(array) {
                const result = original.call(this, array);
                if (protectionEnabled && array) {
                    for (let i = audioSeed % 23; i < array.length; i += 23) array[i] += noiseByte(i, audioSeed) * audioNoise;
                }
                return result;
            };
        }
        for (const method of ['getByteFrequencyData', 'getByteTimeDomainData']) {
            const original = prototype[method];
            if (!original) continue;
            prototype[method] = function(array) {
                const result = original.call(this, array);
                if (protectionEnabled) perturbPixels(array, audioSeed);
                return result;
            };
        }
    };
    patchAnalyser(globalThis.AnalyserNode && AnalyserNode.prototype);

    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => __HARDWARE_CONCURRENCY__ });
    Object.defineProperty(navigator, 'languages', { get: () => [__LANG__] });
    Object.defineProperty(navigator, 'language', { get: () => __LANG__ });
    Object.defineProperty(navigator, 'platform', { get: () => __PLATFORM__ });
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    if (uaDataDisabled) Object.defineProperty(navigator, 'userAgentData', { get: () => undefined });

    if (webrtcBlocked) {
        const blocked = function() { throw new DOMException('WebRTC blocked by profile policy', 'NotAllowedError'); };
        Object.defineProperty(globalThis, 'RTCPeerConnection', { value: blocked });
        Object.defineProperty(globalThis, 'webkitRTCPeerConnection', { value: blocked });
        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
                value: () => Promise.reject(new DOMException('Media blocked by profile policy', 'NotAllowedError'))
            });
        }
    }
})();
"""


def build_fingerprint_script(fingerprint):
    fp = ensure_fingerprint_defaults(fingerprint)
    replacements = {
        "__CANVAS_SEED__": str(int(fp["canvas_noise_seed"])),
        "__WEBGL_SEED__": str(int(fp["webgl_noise_seed"])),
        "__AUDIO_SEED__": str(int(fp["audio_noise_seed"])),
        "__AUDIO_NOISE__": repr(float(fp.get("audio_noise", 0.0000001))),
        "__PROTECTION_ENABLED__": json.dumps(bool(fp.get("fingerprint_protection", True))),
        "__WEBRTC_BLOCKED__": json.dumps(fp.get("webrtc_policy") == "block"),
        "__UA_DATA_DISABLED__": json.dumps(fp.get("device_preset") == "iphone_x"),
        "__WEBGL_VENDOR__": json.dumps(fp.get("webgl_vendor", "Google Inc. (Intel)")),
        "__WEBGL_RENDERER__": json.dumps(fp.get("webgl_renderer", "Intel Iris OpenGL Engine")),
        "__HARDWARE_CONCURRENCY__": str(int(fp.get("hardware_concurrency", 4))),
        "__LANG__": json.dumps(fp.get("lang", "en-US")),
        "__PLATFORM__": json.dumps(fp.get("platform", "Windows")),
    }
    script = FINGERPRINT_SCRIPT
    for token, value in replacements.items():
        script = script.replace(token, value)
    return script


def configure_driver_environment(driver, fingerprint, origins=TIKTOK_ORIGINS):
    fp = ensure_fingerprint_defaults(fingerprint)
    device = device_override(fp)
    ua_params = {
        "userAgent": device["user_agent"],
        "acceptLanguage": fp.get("lang", "en-US"),
        "platform": device["platform"],
    }
    if device["metadata"] is not None:
        ua_params["userAgentMetadata"] = device["metadata"]
    driver.execute_cdp_cmd("Network.setUserAgentOverride", ua_params)

    if device["mobile"]:
        driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": device["width"],
            "height": device["height"],
            "deviceScaleFactor": device["device_scale_factor"],
            "mobile": True,
        })
        driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {
            "enabled": device["touch"],
            "maxTouchPoints": 5 if device["touch"] else 0,
        })

    timezone_id = fp.get("timezone")
    if _valid_timezone(timezone_id):
        driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": timezone_id})
    geo = fp.get("geolocation") or {}
    if _valid_coordinates(geo.get("latitude"), geo.get("longitude")):
        driver.execute_cdp_cmd("Emulation.setGeolocationOverride", {
            "latitude": float(geo["latitude"]),
            "longitude": float(geo["longitude"]),
            "accuracy": float(geo.get("accuracy", 50)),
        })
        for origin in origins:
            try:
                driver.execute_cdp_cmd("Browser.grantPermissions", {
                    "origin": origin,
                    "permissions": ["geolocation"],
                })
            except Exception:
                # Some Chromium forks expose geolocation without Browser.grantPermissions.
                pass

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": build_fingerprint_script(fp),
    })
    return device
