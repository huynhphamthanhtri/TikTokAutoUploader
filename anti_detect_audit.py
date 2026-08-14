"""Anti-detect audit: compare Patchright browser signals against real Chrome.

Opens a headed Patchright session through a proxy and evaluates a JS probe to
collect fingerprint/leak signals. Prints a JSON report flagging signals that
look non-native (webdriver, CDP artifacts, mismatched timezone/locale/UA...).

Usage:
    python anti_detect_audit.py --proxy IP:PORT:USER:PASS [--headed] [--url https://...]

Run from the repo root with the project venv python (patchright is installed
there). The report is printed as JSON and also written to anti_detect_report.json.
"""

import argparse
import json
import time
from pathlib import Path

import browser_patchright_glue as glue
from core_helpers import parse_proxy_string
from browser_environment import resolve_geoip, proxy_cache_key

PROBE_SCRIPT = r"""
() => {
  const out = {};

  // CDP / automation leaks
  out.navigator_webdriver = navigator.webdriver === true || navigator.webdriver;
  out.navigator_languages = navigator.languages;
  out.navigator_platform = navigator.platform;
  out.user_agent = navigator.userAgent;
  out.user_agent_data = (() => {
    if (!navigator.userAgentData) return null;
    try {
      return {
        brands: navigator.userAgentData.brands,
        platform: navigator.userAgentData.platform,
        mobile: navigator.userAgentData.mobile,
      };
    } catch (e) { return { error: String(e) }; }
  })();
  out.chrome = (() => {
    try { return window.chrome && window.chrome.runtime ? !!window.chrome.runtime.id : !!window.chrome; }
    catch (e) { return false; }
  })();

  // Window / viewport
  out.inner_size = [window.innerWidth, window.innerHeight];
  out.screen = [screen.width, screen.height];
  out.device_pixel_ratio = window.devicePixelRatio;
  out.outer_size = [window.outerWidth, window.outerHeight];

  // Hardware signals
  out.hardware_concurrency = navigator.hardwareConcurrency || null;
  out.device_memory = navigator.deviceMemory || null;
  out.max_touch_points = navigator.maxTouchPoints || 0;

  // Timezone / locale / geolocation consistency
  out.timezone_offset = new Date().getTimezoneOffset();
  out.intl_timezone = (() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; }
    catch (e) { return ""; }
  })();
  out.locale = navigator.language;
  out.accept_language = navigator.language;

  // WebGL
  out.webgl = (() => {
    try {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
      if (!gl) return null;
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      return {
        vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
        renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      };
    } catch (e) { return { error: String(e) }; }
  })();

  // Canvas fingerprint (stable hash)
  out.canvas_hash = (() => {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = 220; canvas.height = 40;
      const ctx = canvas.getContext('2d');
      ctx.textBaseline = 'top';
      ctx.font = '14px Arial';
      ctx.fillStyle = '#f60';
      ctx.fillRect(0, 0, 220, 40);
      ctx.fillStyle = '#069';
      ctx.fillText('TikTokAutoUploader-audit', 5, 5);
      const data = canvas.toDataURL();
      let hash = 0;
      for (let i = 0; i < data.length; i++) {
        hash = ((hash << 5) - hash + data.charCodeAt(i)) | 0;
      }
      return String(hash);
    } catch (e) { return { error: String(e) }; }
  })();

  // WebRTC public IP leak
  out.webrtc = (() => {
    return new Promise((resolve) => {
      if (typeof RTCPeerConnection === 'undefined') { resolve(null); return; }
      const pc = new RTCPeerConnection({ iceServers: [] });
      let ips = [];
      pc.createDataChannel('');
      pc.onicecandidate = (e) => {
        if (!e.candidate) { pc.close(); resolve(ips); return; }
        try {
          const parts = (e.candidate.candidate || '').split(' ');
          const idx = parts.indexOf('typ');
          if (idx > 0 && parts[idx - 1] === 'host') {
            ips.push(parts[4]);
          }
        } catch (err) {}
      };
      pc.createOffer().then((offer) => pc.setLocalDescription(offer)).catch((e) => { pc.close(); resolve([]); });
      setTimeout(() => { pc.close(); resolve(ips); }, 2000);
    });
  })();
  return out;
}
"""


def build_config(proxy_string, headed):
    proxy_data = parse_proxy_string(proxy_string) if proxy_string else None
    cfg = {
        "chrome_profile": "",
        "browser_profile_path": "",
        "use_proxy": bool(proxy_data),
        "proxy_string": proxy_string,
        "proxy_type": "http",
        "headless": not headed,
        "fingerprint": {"lang": "en-US", "device_preset": "desktop"},
    }
    return cfg


def main():
    parser = argparse.ArgumentParser(description="Anti-detect audit")
    parser.add_argument("--proxy", default="", help="IP:PORT:USER:PASS")
    parser.add_argument("--headed", action="store_true", help="run headed (default headless)")
    parser.add_argument("--url", default="https://www.tiktok.com", help="page to visit")
    args = parser.parse_args()

    cfg = build_config(args.proxy, args.headed)
    token = None
    report = {"proxy": args.proxy, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        glue.ensure_patchright_profile(cfg)
        token = glue.open_session(cfg, profile_name="anti-detect-audit")
        glue.navigate(token, args.url)
        time.sleep(3)
        result = glue.page_evaluate(token, PROBE_SCRIPT, timeout=30)
        report["signals"] = result
        if args.proxy:
            proxy_data = parse_proxy_string(args.proxy)
            if proxy_data:
                try:
                    geo = resolve_geoip(proxy_data, timeout=10)
                    report["geo"] = geo
                    report["proxy_hash"] = proxy_cache_key(proxy_data)
                except Exception as error:
                    report["geo_error"] = str(error)
        _flag_anomalies(report)
    finally:
        if token:
            try:
                token.quit()
            except Exception:
                pass

    print(json.dumps(report, indent=2, ensure_ascii=False))
    Path("anti_detect_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if report.get("anomalies"):
        print("ANOMALIES:")
        for item in report["anomalies"]:
            print("  -", item)


def _flag_anomalies(report):
    anomalies = []
    signals = report.get("signals") or {}
    if not isinstance(signals, dict):
        return
    if signals.get("navigator_webdriver"):
        anomalies.append("navigator.webdriver == true (CDP/automation leak)")
    if not signals.get("chrome"):
        anomalies.append("window.chrome missing (headless/stealth inconsistency)")
    try:
        tz_offset = int(signals.get("timezone_offset"))
    except (TypeError, ValueError):
        tz_offset = None
    geo = report.get("geo") or {}
    if geo.get("timezone") and tz_offset is not None:
        import datetime as dt
        current = dt.datetime.now(dt.timezone.utc).astimezone()
        current_offset = current.utcoffset().total_seconds() / 60
        if tz_offset != -current_offset:
            anomalies.append(
                "timezone offset mismatch: page=%s vs system=%s (geo=%s)"
                % (tz_offset, -current_offset, geo.get("timezone"))
            )
    report["anomalies"] = anomalies


if __name__ == "__main__":
    main()