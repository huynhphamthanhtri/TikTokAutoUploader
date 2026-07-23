import hmac
import json
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import traceback
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from pyngrok import conf as ngconf
from pyngrok import ngrok
from werkzeug.serving import make_server
from yt_dlp import YoutubeDL
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .activity import append_activity, remember_download
from . import ffmpeg_helper


def _app_root():
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundled_root():
    import sys
    if getattr(sys, "frozen", False):
        internal = Path(sys.executable).resolve().parent / "_internal"
        if internal.exists():
            return internal
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = _app_root()
DOWNLOADS_DIR = APP_ROOT / "downloads"
TEMP_DIR = APP_ROOT / "temp_dl"
CHANNELS_JSON = APP_ROOT / "channels.json"
CHANNEL_CACHE_JSON = APP_ROOT / "channel_cache.json"
CONFIG_JSON = APP_ROOT / "youtube_config.json"
CSV_LOG = APP_ROOT / "downloads_log.csv"
NGROK_BINARY = APP_ROOT / "ngrok.exe"

NGROK_PORT_DEFAULT = 5000
MIN_SECONDS = 62
LOOP_MIN_DURATION = 45
RESUBSCRIBE_INTERVAL_DAYS = 2
MAX_ACCEPTABLE_AGE_HOURS = int(os.environ.get("MAX_ACCEPTABLE_AGE_HOURS", "12"))
WATERMARK_SLACK_MINUTES = int(os.environ.get("WATERMARK_SLACK_MINUTES", "30"))
SEEN_MAX_PER_CHANNEL = 1200
BATCH_SCAN_LIMIT = 50
FORMAT_FAST_720P = (
    "b[height<=720][ext=mp4][vcodec^=avc1]/"
    "b[height<=720][ext=mp4]/"
    "bv*[height<=720][ext=mp4][vcodec^=avc1]+ba[ext=m4a]/"
    "bv*[height<=720][ext=mp4]+ba[ext=m4a]/"
    "b[height<=720]"
)

CONFIG_DEFAULTS = {
    "api_keys": [],
    "ngrok_port": NGROK_PORT_DEFAULT,
    "download_workers": 4,
    "max_video_minutes": 0,
    "auto_start": True,
    "cookies_file": "",
    "proxy_rotation": True,
    "concurrent_fragments": 8,
}

DOWNLOADS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

log_queue = queue.Queue()
download_queue = queue.Queue()
websub_payload_queue = queue.Queue()
stop_event = threading.Event()
_state_lock = threading.RLock()
_store_lock = threading.RLock()
_config_json_lock = threading.RLock()
_encode_sem = threading.Semaphore(1)
_download_sem = threading.Semaphore(CONFIG_DEFAULTS["download_workers"])
_active_downloads = {}
_active_downloads_lock = threading.Lock()
_callback_server = None
_callback_server_thread = None
_callback_port = None
_callback_instance_id = None
_callback_owner_token = None
_monitor_started = False
_monitor_gen = 0
_monitor_gen_lock = threading.Lock()
_all_threads = []
_proxy_pool = []
_proxy_by_profile = {}
_proxy_rr_index = 0
_proxy_lock = threading.Lock()
public_callback_url = None
public_callback_verified = False
last_callback_post_time = None
last_error = ""
downloaded_today = 0
downloaded_today_date = datetime.now().strftime("%Y-%m-%d")

_pending_video_ids = set()
_pending_lock = threading.Lock()
_retry_after = {}
_finalize_lock = threading.Lock()
_retry_lock = threading.Lock()
MAX_RETRIES = 4
RETRY_DELAYS = [0, 15, 45, 120]
RETRY_COOLDOWN = 300

_subscription_status = {}
_subscription_lock = threading.Lock()
_websub_secret_lock = threading.Lock()
_websub_secret_cache = None


def log(message):
    text = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    try:
        log_queue.put_nowait(text)
    except Exception:
        pass


def get_logs(limit=200):
    items = []
    while len(items) < limit:
        try:
            items.append(log_queue.get_nowait())
        except queue.Empty:
            break
    return items


def get_config():
    cfg = dict(CONFIG_DEFAULTS)
    if CONFIG_JSON.exists():
        try:
            with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            cfg.update(data)
        except Exception as e:
            log(f"[Config] Không đọc được youtube_config.json: {e}")
    return cfg


def _save_config(cfg):
    merged = dict(CONFIG_DEFAULTS)
    merged.update(cfg or {})
    tmp = CONFIG_JSON.with_name(f"{CONFIG_JSON.name}.{uuid.uuid4().hex}.tmp")
    with _config_json_lock:
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_JSON)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def _resolve_cookies_file():
    cfg = get_config()
    candidates = []
    configured = str(cfg.get("cookies_file") or "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.append(APP_ROOT / "cookies.txt")
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                return str(path)
        except Exception:
            continue
    return None


def get_cookies_file():
    return _resolve_cookies_file() or ""


def set_cookies_file(path):
    path = str(path or "").strip()
    if path and not Path(path).exists():
        return False, "File cookie không tồn tại."
    cfg = get_config()
    cfg["cookies_file"] = path
    _save_config(cfg)
    if path:
        log(f"[Cookies] Đã lưu cookie file: {path}")
        return True, "Đã lưu cookie file."
    log("[Cookies] Đã xóa cookie file cấu hình.")
    return True, "Đã xóa cookie file."


def set_max_video_minutes(minutes):
    try:
        value = int(str(minutes).strip())
    except Exception:
        return False, "Giới hạn phút không hợp lệ."
    if value < 0:
        return False, "Giới hạn phút phải >= 0."
    cfg = get_config()
    cfg["max_video_minutes"] = value
    _save_config(cfg)
    text = "không giới hạn" if value == 0 else f"{value} phút"
    log(f"[Config] Max video: {text}")
    return True, f"Đã lưu giới hạn: {text}."


def _format_duration(seconds):
    try:
        seconds = float(seconds or 0)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return "?"
    minutes = seconds / 60
    return f"{minutes:.1f} phút"


def _parse_proxy_string(proxy_str):
    if not proxy_str:
        return None
    clean = str(proxy_str).replace("http://", "").replace("https://", "").strip()
    parts = clean.split(":")
    if len(parts) == 2:
        return {"ip": parts[0], "port": parts[1], "user": "", "pass": ""}
    if len(parts) >= 4:
        return {"ip": parts[0], "port": parts[1], "user": parts[2], "pass": parts[3]}
    return None


def _proxy_to_url(parsed):
    if not parsed or not parsed.get("ip") or not parsed.get("port"):
        return None
    if parsed.get("user"):
        return f"http://{parsed['user']}:{parsed.get('pass', '')}@{parsed['ip']}:{parsed['port']}"
    return f"http://{parsed['ip']}:{parsed['port']}"


def _mask_proxy(proxy_url):
    if not proxy_url:
        return "direct"
    clean = str(proxy_url).replace("http://", "").replace("https://", "")
    if "@" in clean:
        clean = clean.split("@", 1)[1] + " (auth)"
    return clean


def _try_pending(cid, vid):
    with _pending_lock:
        full_id = f"{cid}:{vid}"
        if full_id in _pending_video_ids:
            return False
        _pending_video_ids.add(full_id)
        return True


def _remove_pending(cid, vid):
    with _pending_lock:
        _pending_video_ids.discard(f"{cid}:{vid}")


def _is_pending(cid, vid):
    with _pending_lock:
        return f"{cid}:{vid}" in _pending_video_ids


def _classify_download_error(error):
    text = str(error).lower()
    permanent_kw = ("members-only", "members only", "copyright", "removed by user", "this video is private", "this video is unavailable", "video unavailable", "copyright claim", "copyright strike", "age-restricted", "age restriction", "sign in to age verify")
    for kw in permanent_kw:
        if kw in text:
            return "permanent"
    if _is_youtube_block_error(error):
        return "retry_block"
    if _is_proxy_download_error(error):
        return "retry_proxy"
    return "retry"


def _schedule_retry(cid, vid, attempt):
    delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_COOLDOWN
    with _retry_lock:
        _retry_after[f"{cid}:{vid}"] = time.time() + delay


def _clear_retry(cid, vid):
    with _retry_lock:
        _retry_after.pop(f"{cid}:{vid}", None)
        _retry_after.pop(f"{cid}:{vid}:attempt", None)
        _retry_after.pop(f"{cid}:{vid}:due", None)


def _get_retry_due(cid, vid):
    with _retry_lock:
        deadline = _retry_after.get(f"{cid}:{vid}")
        if deadline is None:
            return None
        remaining = deadline - time.time()
        return max(0, remaining)


def _load_tiktok_proxies():
    config_path = APP_ROOT / "configs.json"
    proxy_by_profile = {}
    proxy_pool = []
    if not config_path.exists():
        return proxy_by_profile, proxy_pool
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        profiles = data.get("profiles") if isinstance(data, dict) else None
        if not isinstance(profiles, dict):
            profiles = data if isinstance(data, dict) else {}
        for name, prof in profiles.items():
            if not isinstance(prof, dict) or not prof.get("use_proxy"):
                continue
            proxy_url = _proxy_to_url(_parse_proxy_string(prof.get("proxy_string", "")))
            if not proxy_url:
                continue
            proxy_by_profile[str(name)] = proxy_url
            proxy_pool.append(proxy_url)
        proxy_pool = list(dict.fromkeys(proxy_pool))
        if proxy_pool:
            log(f"[Proxy] Loaded {len(proxy_by_profile)} profile proxies, pool={len(proxy_pool)}")
        else:
            log("[Proxy] Không tìm thấy proxy hợp lệ trong profiles")
    except Exception as e:
        log(f"[Proxy] Load lỗi: {e}")
    return proxy_by_profile, proxy_pool


def _next_proxy():
    global _proxy_rr_index
    if not _proxy_pool:
        return None
    with _proxy_lock:
        proxy = _proxy_pool[_proxy_rr_index % len(_proxy_pool)]
        _proxy_rr_index += 1
        return proxy


def _ensure_proxy_pool_loaded():
    global _proxy_pool, _proxy_by_profile
    if _proxy_pool or _proxy_by_profile:
        return
    _proxy_by_profile, _proxy_pool = _load_tiktok_proxies()


def _proxy_for_profile(profile_name):
    if not profile_name:
        return None
    return _proxy_by_profile.get(str(profile_name))


def _is_youtube_block_error(message):
    text = str(message or "").lower()
    keywords = (
        "sign in to confirm", "not a bot", "captcha", "confirm you",
        "too many requests", "429", "rate limit", "temporarily blocked",
    )
    return any(k in text for k in keywords)


def _is_proxy_download_error(message):
    text = str(message or "").lower()
    keywords = (
        "proxy", "407", "tunnel", "cannot connect", "connection refused",
        "connection reset", "connection aborted", "timed out", "timeout",
        "502", "503", "socks", "remote end closed connection",
    )
    return any(k in text for k in keywords) or _is_youtube_block_error(text)


def get_api_key():
    keys = get_config().get("api_keys") or []
    return str(keys[0]).strip() if keys else ""


def get_youtube_client(api_key=None):
    key = (api_key or get_api_key()).strip()
    if not key:
        raise ValueError("Chưa có YouTube Data API key")
    return build("youtube", "v3", developerKey=key)


def check_api_key_validity(api_key):
    try:
        youtube = get_youtube_client(api_key)
        youtube.channels().list(part="id", id="UC_x5XG1OV2P6uZZ5FSM9Ttw").execute()
        return True, "API Key hợp lệ."
    except HttpError as e:
        try:
            data = json.loads(e.content.decode("utf-8"))
            reason = data.get("error", {}).get("errors", [{}])[0].get("reason", "unknown")
        except Exception:
            reason = "unknown"
        if e.resp.status == 403 and reason in ("quotaExceeded", "dailyLimitExceeded"):
            return False, "API Key hợp lệ nhưng đã hết quota."
        return False, f"API Key lỗi: {reason} ({e.resp.status})"
    except Exception as e:
        return False, f"Không kiểm tra được API key: {e}"


def check_and_save_api_key(api_key):
    api_key = str(api_key or "").strip()
    ok, msg = check_api_key_validity(api_key)
    if ok:
        cfg = get_config()
        cfg["api_keys"] = [api_key]
        _save_config(cfg)
        log("[API] Đã lưu YouTube API key.")
    else:
        log(f"[API] {msg}")
    return ok, msg


class ChannelsStore:
    def __init__(self, path):
        self.path = Path(path)
        self._channels = {}
        self._dirty = False
        self._revision = 0
        self._autosave_stop = threading.Event()
        self._autosave_thread = None

    def _serialize(self):
        out = {}
        for cid, meta in self._channels.items():
            out[cid] = {
                "folder": meta.get("folder"),
                "profile_name": meta.get("profile_name", ""),
                "active": bool(meta.get("active", True)),
                "seen": sorted(list(meta.get("seen", set()))),
                "last_pub_utc": meta.get("last_pub_utc"),
                "process_short": bool(meta.get("process_short", True)),
            }
        return out

    def load(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            with _store_lock:
                self._channels = {}
                for cid, meta in data.items():
                    self._channels[cid] = {
                        "folder": meta.get("folder") or str(DOWNLOADS_DIR / cid),
                        "profile_name": meta.get("profile_name", ""),
                        "active": meta.get("active", True),
                        "seen": set(meta.get("seen", [])),
                        "last_pub_utc": meta.get("last_pub_utc"),
                        "process_short": meta.get("process_short", True),
                    }
            log(f"[Channels] Loaded {len(self._channels)} channels.")
        except Exception as e:
            log(f"[Channels] Load lỗi: {e}")

    def save_now(self):
        try:
            tmp = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
            with _store_lock:
                data = self._serialize()
                snapshot_revision = self._revision
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            with _store_lock:
                if self._revision == snapshot_revision:
                    self._dirty = False
            return True
        except Exception as e:
            log(f"[Channels] Save lỗi: {e}")
            return False
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def start_autosave(self):
        if self._autosave_thread and self._autosave_thread.is_alive():
            return
        self._autosave_stop.clear()
        def loop():
            while not self._autosave_stop.is_set():
                self._autosave_stop.wait(5)
                if self._dirty:
                    self.save_now()
        self._autosave_thread = threading.Thread(target=loop, daemon=True)
        self._autosave_thread.start()

    def stop_autosave(self):
        self._autosave_stop.set()
        if self._autosave_thread:
            self._autosave_thread.join(timeout=2)
        if self._dirty:
            self.save_now()

    def all_items(self):
        with _store_lock:
            return {cid: dict(m, seen=set(m.get("seen", set()))) for cid, m in self._channels.items()}

    def add_channel(self, cid, folder, profile_name="", process_short=True):
        with _store_lock:
            self._channels[cid] = {
                "folder": folder,
                "profile_name": profile_name,
                "active": True,
                "seen": set(self._channels.get(cid, {}).get("seen", set())),
                "last_pub_utc": self._channels.get(cid, {}).get("last_pub_utc"),
                "process_short": process_short,
            }
            self._dirty = True
            self._revision += 1

    def remove_channel(self, cid):
        with _store_lock:
            self._channels.pop(cid, None)
            self._dirty = True
            self._revision += 1

    def set_folder(self, cid, folder, profile_name=""):
        with _store_lock:
            if cid in self._channels:
                self._channels[cid]["folder"] = folder
                if profile_name:
                    self._channels[cid]["profile_name"] = profile_name
                self._dirty = True
                self._revision += 1

    def toggle_active(self, cid):
        with _store_lock:
            if cid not in self._channels:
                return None
            self._channels[cid]["active"] = not bool(self._channels[cid].get("active", True))
            self._dirty = True
            self._revision += 1
            return self._channels[cid]["active"]

    def toggle_process_short(self, cid):
        with _store_lock:
            if cid not in self._channels:
                return None
            self._channels[cid]["process_short"] = not bool(self._channels[cid].get("process_short", True))
            self._dirty = True
            self._revision += 1
            return self._channels[cid]["process_short"]

    def get_meta(self, cid):
        with _store_lock:
            meta = self._channels.get(cid)
            return dict(meta) if meta else None

    def get_active_and_unseen_guard(self, cid, vid):
        with _store_lock:
            meta = self._channels.get(cid)
            if not meta or not meta.get("active", True):
                return False
            seen = meta.setdefault("seen", set())
            if vid in seen:
                return False
            seen.add(vid)
            if len(seen) > SEEN_MAX_PER_CHANNEL:
                for old in list(seen)[:len(seen) - SEEN_MAX_PER_CHANNEL]:
                    seen.discard(old)
            self._dirty = True
            self._revision += 1
            return True

    def mark_seen_only(self, cid, vid):
        with _store_lock:
            meta = self._channels.get(cid)
            if meta:
                meta.setdefault("seen", set()).add(vid)
                self._dirty = True
                self._revision += 1

    def update_watermark(self, cid, pub_epoch):
        if pub_epoch is None:
            return
        with _store_lock:
            meta = self._channels.get(cid)
            if meta and (meta.get("last_pub_utc") is None or pub_epoch > meta.get("last_pub_utc")):
                meta["last_pub_utc"] = pub_epoch
                self._dirty = True
                self._revision += 1

    def should_reject_by_watermark(self, cid, pub_epoch, slack_sec):
        if pub_epoch is None:
            return False
        with _store_lock:
            meta = self._channels.get(cid)
            cur = meta.get("last_pub_utc") if meta else None
            return cur is not None and pub_epoch < (cur - slack_sec)

    def subscribe_all(self, cb_url):
        for ch in list(self.all_items().keys()):
            threading.Thread(target=subscribe_websub, args=(ch, cb_url), daemon=True).start()


channels_store = ChannelsStore(CHANNELS_JSON)
channels_store.load()
flask_app = Flask(__name__)


@flask_app.route("/youtube_callback", methods=["GET", "POST"])
def youtube_callback():
    if _callback_owner_token and request.args.get("owner") != _callback_owner_token:
        return "Invalid callback owner", 404
    if request.method == "GET":
        challenge = request.args.get("hub.challenge", "")
        mode = request.args.get("hub.mode", "")
        topic = request.args.get("hub.topic", "")
        if mode == "subscribe" and topic:
            ch_match = re.search(r"channel_id=([^&]+)", topic)
            if ch_match:
                cid = ch_match.group(1)
                lease = request.args.get("hub.lease_seconds", "")
                with _subscription_lock:
                    _subscription_status[cid] = {
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                        "lease_seconds": int(lease) if lease and lease.isdigit() else 0,
                        "mode": mode,
                        "topic": topic,
                    }
                log(f"[WebSub] Verification GET: {cid} verified, lease={lease}s")
        return challenge, 200
    global last_callback_post_time
    payload = request.get_data()
    if len(payload) > MAX_CALLBACK_BODY:
        log(f"[WebSub] Body too large: {len(payload)} bytes, rejected")
        return "Body too large", 413
    sig = request.headers.get("X-Hub-Signature-256", "") or request.headers.get("X-Hub-Signature", "")
    if not _verify_websub_signature(payload, sig):
        log(f"[WebSub] Invalid signature, rejected ({len(payload)} bytes)")
        return "Invalid signature", 401
    data = payload.decode("utf-8", errors="ignore")
    last_callback_post_time = datetime.now(timezone.utc).isoformat()
    websub_payload_queue.put((data, last_callback_post_time))
    log(f"[WebSub] POST verified bytes={len(data)}")
    return "", 200


@flask_app.route("/youtube_health")
def youtube_health():
    return jsonify({
        "ok": True,
        "instance_id": _callback_instance_id or "",
        "port": _callback_port,
        "generation": _get_monitor_gen(),
        "monitor_running": _monitor_started,
        "started_at": getattr(_callback_server, "_started_at", ""),
    })


NS_ATOM = "http://www.w3.org/2005/Atom"
NS_YT = "http://www.youtube.com/xml/schemas/2015"


def _parse_websub_xml(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        log(f"[WebSub] XML parse error: {xml_text[:200]}")
        return []
    entries = []
    for entry in root.findall(f"{{{NS_ATOM}}}entry"):
        vid_elem = entry.find(f"{{{NS_YT}}}videoId")
        chan_elem = entry.find(f"{{{NS_YT}}}channelId")
        pub_elem = entry.find(f"{{{NS_ATOM}}}published")
        vid = vid_elem.text.strip() if vid_elem is not None and vid_elem.text else None
        chan = chan_elem.text.strip() if chan_elem is not None and chan_elem.text else None
        pub = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else None
        if vid and chan:
            entries.append((vid, chan, pub))
    if not entries:
        log(f"[WebSub] No entries found in XML ({len(xml_text)} bytes)")
    return entries


def iso_to_epoch(value):
    try:
        value = value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def websub_processor_worker(run_gen=None):
    log("[WebSub] Processor started")
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log("[WebSub] Generation changed, stopping processor")
            break
        try:
            data, detected_utc_iso = websub_payload_queue.get(timeout=1)
        except queue.Empty:
            continue
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log("[WebSub] Generation changed after dequeue, stopping processor")
            websub_payload_queue.task_done()
            break
        now_epoch = time.time()
        entries = _parse_websub_xml(data)
        if entries:
            log(f"[WebSub] Processing {len(entries)} entries")
        for vid, chan, published in entries:
            if not chan:
                continue
            if run_gen is not None and _get_monitor_gen() != run_gen:
                break
            pub_epoch = iso_to_epoch(published) if published else None
            meta = channels_store.get_meta(chan)
            if not meta or not meta.get("active", True):
                log(f"[WebSub] Skip {vid}: channel {chan} inactive")
                continue
            if pub_epoch is not None and now_epoch - pub_epoch > MAX_ACCEPTABLE_AGE_HOURS * 3600:
                channels_store.mark_seen_only(chan, vid)
                channels_store.update_watermark(chan, pub_epoch)
                log(f"[WebSub] Skip {vid}: too old ({_format_duration(now_epoch - pub_epoch)})")
                continue
            seen = meta.get("seen", set())
            if vid in seen:
                continue
            if _is_pending(chan, vid):
                continue
            if not _try_pending(chan, vid):
                continue
            if channels_store.should_reject_by_watermark(chan, pub_epoch, WATERMARK_SLACK_MINUTES * 60):
                _remove_pending(chan, vid)
                channels_store.mark_seen_only(chan, vid)
                log(f"[WebSub] Skip {vid}: watermark")
                continue
            download_queue.put((chan, vid, published or None, detected_utc_iso))
            channels_store.update_watermark(chan, pub_epoch)
            log(f"[WebSub] Enqueue {vid}@{chan}")
        channels_store._dirty = True
        try:
            websub_payload_queue.task_done()
        except Exception:
            pass
    log("[WebSub] Processor stopped")


def load_channel_cache():
    try:
        with open(CHANNEL_CACHE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_channel_cache(cache):
    try:
        with open(CHANNEL_CACHE_JSON, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"[Cache] Save lỗi: {e}")


def get_channel_id_from_link(link, youtube):
    cache = load_channel_cache()
    link = str(link or "").strip()
    if link in cache:
        return cache[link]
    if link.startswith("@"):
        link = f"https://www.youtube.com/{link}"
    elif not link.startswith("http") and not link.startswith("youtube.com") and not link.startswith("youtu.be"):
        link = f"https://www.youtube.com/@{link}"
    channel_match = re.search(r"youtube\.com/channel/([^\s/?#]+)", link)
    if channel_match:
        cid = channel_match.group(1)
        response = youtube.channels().list(part="contentDetails", id=cid).execute()
        if response.get("items"):
            info = {"id": cid, "playlistId": response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]}
            cache[link] = info
            save_channel_cache(cache)
            return info
    user_match = re.search(r"youtube\.com/(@|c/|user/)([^\s/?#]+)", link)
    if user_match:
        username = urllib.parse.unquote(user_match.group(2))
        response = youtube.channels().list(part="id,contentDetails", forHandle=f"@{username}").execute()
        if not response.get("items"):
            response = youtube.search().list(part="snippet", q=username, type="channel", maxResults=1).execute()
            if response.get("items"):
                cid = response["items"][0]["snippet"]["channelId"]
                response = youtube.channels().list(part="id,contentDetails", id=cid).execute()
        if response.get("items"):
            item = response["items"][0]
            info = {"id": item["id"], "playlistId": item["contentDetails"]["relatedPlaylists"]["uploads"]}
            cache[link] = info
            save_channel_cache(cache)
            return info
    short_match = re.search(r"youtu\.be/([^\s/?#]+)", link)
    if short_match:
        return get_channel_from_video_id(short_match.group(1), youtube)
    watch_match = re.search(r"[?&]v=([^&\s]+)", link)
    if watch_match:
        return get_channel_from_video_id(watch_match.group(1), youtube)
    return None


def get_channel_from_video_id(video_id, youtube):
    response = youtube.videos().list(part="snippet", id=video_id).execute()
    if not response.get("items"):
        return None
    cid = response["items"][0]["snippet"]["channelId"]
    details = youtube.channels().list(part="contentDetails", id=cid).execute()
    if not details.get("items"):
        return None
    return {"id": cid, "playlistId": details["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]}


def sanitize_filename(name, max_length=150):
    name = (name or "video").replace("\n", " ").replace("\r", " ").strip()
    name = name.translate(str.maketrans({ch: " " for ch in r'<>:"/\|?*'}))
    name = re.sub(r"\s+", " ", "".join(ch for ch in name if ord(ch) >= 32)).strip().rstrip(" .")
    return (name[:max_length].rstrip(" .") or "video")


def _choose_final_path_unlocked(out_folder, title, video_id, ext):
    base = sanitize_filename(title)
    for candidate in [Path(out_folder) / f"{base}{ext}", Path(out_folder) / f"{base} - {video_id[:8]}{ext}"]:
        if not candidate.exists():
            return str(candidate)
    for i in range(2, 1000):
        candidate = Path(out_folder) / f"{base} ({i}){ext}"
        if not candidate.exists():
            return str(candidate)
    return str(Path(out_folder) / f"{base} - {uuid.uuid4().hex[:6]}{ext}")


def build_final_path(out_folder, title, video_id, ext):
    with _finalize_lock:
        return _choose_final_path_unlocked(out_folder, title, video_id, ext)


def _finalize_workspace(out_folder):
    workspace = _staging_dir(out_folder) / f"finalize-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _commit_final_file(staged_path, out_folder, title, video_id, ext):
    with _finalize_lock:
        final_path = _choose_final_path_unlocked(out_folder, title, video_id, ext)
        os.replace(staged_path, final_path)
        return final_path


def _detect_container(path):
    out = ffmpeg_helper.run_ffprobe([
        "-v", "error",
        "-show_entries", "format=format_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return (out or "").strip().lower()


def _detect_video_codec(path):
    out = ffmpeg_helper.run_ffprobe([
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return (out or "").strip().lower()


def _detect_audio_codec(path):
    out = ffmpeg_helper.run_ffprobe([
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return (out or "").strip().lower()


MP4_FORMAT_NAMES = {"mp4", "mov,mp4,m4a,3gp,3g2,mj2", "mov"}
COMPATIBLE_VIDEO_CODECS = {"h264", "avc1"}
COMPATIBLE_AUDIO_CODECS = {"aac", "mp4a"}
MUST_TRANSCODE_VIDEO = {"vp9", "vp8", "av1"}
MUST_TRANSCODE_AUDIO = {"opus", "vorbis"}


def _is_mp4_container(container):
    if not container:
        return False
    for name in MP4_FORMAT_NAMES:
        if container == name:
            return True
    return False


def _probe_media(path):
    out = ffmpeg_helper.run_ffprobe([
        "-v", "error",
        "-show_entries", "format=format_name,duration:stream=codec_name,codec_type",
        "-of", "json",
        str(path),
    ])
    if not out:
        return None
    try:
        data = json.loads(out)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    container = (fmt.get("format_name") or "").strip().lower()
    vcodec = ""
    acodec = ""
    duration = fmt.get("duration")
    for s in streams:
        st = (s.get("codec_type") or "").strip()
        if st == "video" and not vcodec:
            vcodec = (s.get("codec_name") or "").strip().lower()
        elif st == "audio" and not acodec:
            acodec = (s.get("codec_name") or "").strip().lower()
    return {
        "container": container,
        "vcodec": vcodec,
        "acodec": acodec,
        "duration": float(duration) if duration else None,
    }


def _finalize_video(input_path, out_folder, title, video_id):
    input_path = str(input_path)
    workspace = _finalize_workspace(out_folder)
    probe = _probe_media(input_path)
    if not probe:
        shutil.rmtree(workspace, ignore_errors=True)
        raise RuntimeError(f"Không probe được media {video_id}")

    container = probe["container"]
    vcodec = probe["vcodec"]
    acodec = probe["acodec"]
    duration = probe["duration"]

    log(f"[Finalize] {video_id}: container={container} vcodec={vcodec} acodec={acodec} dur={duration}")

    needs_transcode_v = vcodec in MUST_TRANSCODE_VIDEO
    needs_transcode_a = bool(acodec and acodec in MUST_TRANSCODE_AUDIO)
    is_compat_v = vcodec in COMPATIBLE_VIDEO_CODECS
    is_compat_a = not acodec or acodec in COMPATIBLE_AUDIO_CODECS
    is_mp4 = _is_mp4_container(container)

    try:
        if is_mp4 and is_compat_v and is_compat_a and not needs_transcode_v and not needs_transcode_a:
            staged_path = str(workspace / "direct.mp4")
            shutil.copy2(input_path, staged_path)
            _probe_output(staged_path, video_id)
            final_path = _commit_final_file(staged_path, out_folder, title, video_id, ".mp4")
            os.remove(input_path)
            return final_path

        if is_compat_v and is_compat_a and not needs_transcode_v and not needs_transcode_a:
            remux_path = str(workspace / "remux.mp4")
            p, err = ffmpeg_helper.run_ffmpeg([
                "-y", "-i", str(input_path),
                "-c:v", "copy", "-c:a", "copy",
                "-movflags", "+faststart",
                remux_path,
            ])
            if p and p.returncode == 0 and Path(remux_path).exists():
                _probe_output(remux_path, video_id)
                final_path = _commit_final_file(remux_path, out_folder, title, video_id, ".mp4")
                os.remove(input_path)
                return final_path
            log(f"[Finalize] Remux thất bại {video_id}: {err[:200]}")

        log(f"[Finalize] Transcode {video_id}: vcodec={vcodec} acodec={acodec}")

        out_path = str(workspace / "transcode.mp4")
        encoder = _pick_video_encoder()
        has_aud = bool(acodec)
        cmd = ["-y", "-i", str(input_path)]
        if not has_aud:
            cmd += ["-an"]
        cmd += encoder + ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        if has_aud:
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += [out_path]
        p, err = ffmpeg_helper.run_ffmpeg(cmd)
        if p and p.returncode == 0 and Path(out_path).exists():
            _probe_output(out_path, video_id)
            final_path = _commit_final_file(out_path, out_folder, title, video_id, ".mp4")
            os.remove(input_path)
            return final_path

        if encoder[1] != "libx264":
            ffmpeg_helper.invalidate_encoder_cache()
            log(f"[Finalize] GPU transcode thất bại {video_id}, fallback CPU: {err[:200]}")
            cmd2 = ["-y", "-i", str(input_path)]
            if not has_aud:
                cmd2 += ["-an"]
            cmd2 += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            if has_aud:
                cmd2 += ["-c:a", "aac", "-b:a", "128k"]
            cmd2 += [out_path]
            p2, err2 = ffmpeg_helper.run_ffmpeg(cmd2)
            if p2 and p2.returncode == 0 and Path(out_path).exists():
                _probe_output(out_path, video_id)
                final_path = _commit_final_file(out_path, out_folder, title, video_id, ".mp4")
                os.remove(input_path)
                return final_path
            log(f"[Finalize] CPU fallback also failed {video_id}: {err2[:200]}")

        log(f"[Finalize] Transcode thất bại {video_id}: {err[:200]}")
        raise RuntimeError(f"Không thể finalize video {video_id}: transcode thất bại")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _probe_output(path, video_id):
    probe = _probe_media(path)
    if not probe:
        raise RuntimeError(f"Output probe failed {video_id}")
    if not _is_mp4_container(probe["container"]):
        raise RuntimeError(f"Output không phải MP4 {video_id}: {probe['container']}")
    if probe["vcodec"] not in COMPATIBLE_VIDEO_CODECS:
        raise RuntimeError(f"Output codec {probe['vcodec']} không phải H.264 {video_id}")
    if probe["acodec"] and probe["acodec"] not in COMPATIBLE_AUDIO_CODECS:
        raise RuntimeError(f"Output audio {probe['acodec']} không phải AAC {video_id}")
    if probe.get("duration") is None or probe["duration"] <= 0:
        raise RuntimeError(f"Output duration không hợp lệ {video_id}: {probe.get('duration')}")
    log(f"[Finalize] Output verified {video_id}: {probe['container']} v={probe['vcodec']} a={probe['acodec']} dur={probe['duration']}")


def append_csv_log(channel_id, video_id, published_iso, detected_iso, saved_path):
    header_needed = not CSV_LOG.exists()
    with open(CSV_LOG, "a", encoding="utf-8") as f:
        if header_needed:
            f.write("channel_id,video_id,published_utc,detected_utc,saved_path\n")
        f.write(f'{channel_id},{video_id},{published_iso or ""},{detected_iso or ""},"{saved_path}"\n')


def probe_duration_seconds(path):
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(p.stdout.strip()) if p.returncode == 0 else None
    except Exception:
        return None


def has_audio_stream(path):
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return p.returncode == 0 and bool(p.stdout.strip())
    except Exception:
        return False


def _build_atempo_chain(playback_factor):
    tempo = 1.0 / max(1e-9, playback_factor)
    chain = []
    while tempo < 0.5:
        chain.append(0.5)
        tempo /= 0.5
    while tempo > 2.0:
        chain.append(2.0)
        tempo /= 2.0
    chain.append(max(0.5, min(2.0, tempo)))
    return chain


def _pick_video_encoder():
    enc = ffmpeg_helper.detect_gpu_encoder()
    if enc == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    return ["-c:v", enc, "-preset", "p7" if enc == "h264_nvenc" else "medium" if enc == "h264_qsv" else "balanced" if enc == "h264_amf" else "medium"]


def slowdown_to_min_duration_in_temp(input_path, target_seconds):
    dur = ffmpeg_helper.probe_duration(input_path) or 0
    if dur >= target_seconds - 0.01 or dur <= 0:
        return input_path, []
    factor = max(1.0, target_seconds / dur)
    out_path = str(TEMP_DIR / f"{uuid.uuid4().hex}.slow.mp4")
    has_aud = ffmpeg_helper.has_audio(input_path)
    if has_aud:
        atempo = ",".join([f"atempo={t:.5g}" for t in _build_atempo_chain(factor)])
        filters = ["-filter_complex", f"[0:v]setpts={factor:.6f}*PTS,format=yuv420p[v];[0:a]{atempo}[a]", "-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    else:
        filters = ["-filter:v", f"setpts={factor:.6f}*PTS,format=yuv420p", "-an"]
    encoder = _pick_video_encoder()
    cmd = ["ffmpeg", "-y", "-i", input_path] + filters + ["-t", str(int(target_seconds))] + encoder + ["-pix_fmt", "yuv420p", "-threads", "2", out_path]
    try:
        _encode_sem.acquire()
        p, err = ffmpeg_helper.run_ffmpeg(cmd[1:])
        if p and p.returncode == 0 and os.path.exists(out_path):
            try: os.remove(input_path)
            except Exception: pass
            return out_path, [out_path]
        if p is None:
            log(f"[FFmpeg] slow-mo lỗi (binary): {err}")
        else:
            log(f"[FFmpeg] slow-mo lỗi: {err[:200]}")
        if encoder[1] != "libx264":
            ffmpeg_helper.invalidate_encoder_cache()
            enc = "libx264"
            cmd2 = ["ffmpeg", "-y", "-i", input_path] + filters + ["-t", str(int(target_seconds)), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-threads", "2", out_path]
            p2, err2 = ffmpeg_helper.run_ffmpeg(cmd2[1:])
            if p2 and p2.returncode == 0 and os.path.exists(out_path):
                try: os.remove(input_path)
                except Exception: pass
                return out_path, [out_path]
            log(f"[FFmpeg] CPU fallback also failed: {err2[:200]}")
    finally:
        try: _encode_sem.release()
        except Exception: pass
    return input_path, []


def loop_to_min_duration_in_temp(input_path, target_seconds):
    dur = ffmpeg_helper.probe_duration(input_path) or 0
    if dur >= target_seconds - 0.01 or dur <= 0:
        return input_path, []
    out_path = str(TEMP_DIR / f"{uuid.uuid4().hex}.loop.mp4")
    list_file = str(TEMP_DIR / f"{uuid.uuid4().hex}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for _ in range(math.ceil(target_seconds / dur)):
            f.write(f"file '{input_path}'\n")
    try:
        _encode_sem.acquire()
        p, err = ffmpeg_helper.run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-t", str(int(target_seconds)), out_path])
        if p and p.returncode == 0 and os.path.exists(out_path):
            try: os.remove(input_path)
            except Exception: pass
            return out_path, [out_path, list_file]
        log(f"[FFmpeg] loop lỗi: {err[:200]}")
    finally:
        try: _encode_sem.release()
        except Exception: pass
    return input_path, [list_file]


def _extract_video_id(value):
    value = str(value or "").strip()
    patterns = [r"youtu\.be/([^\s/?#]+)", r"[?&]v=([^&\s]+)", r"youtube\.com/shorts/([^\s/?#]+)"]
    for pattern in patterns:
        m = re.search(pattern, value)
        if m:
            return m.group(1)
    return value if re.match(r"^[A-Za-z0-9_-]{8,}$", value) else ""


def _normalize_channel_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if _extract_video_id(value):
        return value
    if value.startswith("@"):
        return f"https://www.youtube.com/{value}/videos"
    if not value.startswith("http"):
        return f"https://www.youtube.com/@{value}/videos"
    clean = value.rstrip("/")
    if any(token in clean for token in ("/videos", "/shorts/", "watch?v=", "youtu.be/")):
        return clean
    return clean + "/videos"


def _fetch_video_duration(video_id, retries=2):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extractor_args": {"youtube": {"skip": ["hls"]}},
    }
    cookies = _resolve_cookies_file()
    if cookies:
        opts["cookies"] = cookies
    last_error = None
    for attempt in range(max(1, int(retries or 1))):
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"https://youtu.be/{video_id}", download=False)
            return float((info or {}).get("duration") or 0)
        except Exception as e:
            last_error = e
            if attempt + 1 < max(1, int(retries or 1)):
                time.sleep(0.5)
    raise last_error


def find_latest_video(channel_link, max_seconds=0, scan_limit=BATCH_SCAN_LIMIT):
    url = _normalize_channel_url(channel_link)
    if not url:
        return None, "Link kênh trống."
    video_id = _extract_video_id(url)
    if video_id:
        if max_seconds > 0:
            try:
                duration = _fetch_video_duration(video_id)
                if duration > max_seconds:
                    return None, f"Video URL dài {_format_duration(duration)} > giới hạn {_format_duration(max_seconds)}"
            except Exception as e:
                return None, f"Không kiểm tra được độ dài video: {e}"
        return video_id, "Video URL"
    max_seconds = max(0, int(max_seconds or 0))
    playlistend = max(1, int(scan_limit or BATCH_SCAN_LIMIT)) if max_seconds > 0 else 1
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": playlistend,
        "noplaylist": False,
        "extractor_args": {"youtube": {"skip": ["hls"]}},
    }
    cookies = _resolve_cookies_file()
    if cookies:
        opts["cookies"] = cookies
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = list((info or {}).get("entries") or [])
        if not entries:
            return None, "Không tìm thấy video trên kênh."
        for entry in entries:
            entry = entry or {}
            video_id = entry.get("id") or _extract_video_id(entry.get("url", ""))
            if not video_id:
                continue
            title = entry.get("title") or video_id
            if max_seconds <= 0:
                return video_id, title
            duration = entry.get("duration")
            if not duration:
                try:
                    duration = _fetch_video_duration(video_id)
                except Exception as e:
                    log(f"[Batch] Bỏ qua {video_id}: không xác minh được độ dài sau 2 lần ({e})")
                    continue
            try:
                duration = float(duration or 0)
            except Exception:
                duration = 0
            if duration > 0 and duration <= max_seconds:
                log(f"[Batch] Chọn {video_id}: {title} ({_format_duration(duration)})")
                return video_id, f"{title} ({_format_duration(duration)})"
            if duration > max_seconds:
                log(f"[Batch] Bỏ qua {video_id}: {_format_duration(duration)} > giới hạn {_format_duration(max_seconds)}")
        return None, f"Không tìm thấy video <= {_format_duration(max_seconds)} trong {playlistend} video gần nhất."
    except Exception as e:
        return None, str(e)


def _discover_channel_latest(link, profile_name="", max_seconds=0):
    video_id, title_or_error = find_latest_video(link, max_seconds=max_seconds)
    return link, video_id, title_or_error


def batch_download_latest(channel_links, target_folder, profile_name="", progress_callback=None, stop_event=None):
    links = [str(link or "").strip() for link in (channel_links or []) if str(link or "").strip()]
    if not links:
        return False, "Danh sách kênh trống."
    if not target_folder:
        return False, "Chưa chọn thư mục đích."
    Path(target_folder).mkdir(parents=True, exist_ok=True)
    if get_config().get("proxy_rotation", True):
        _ensure_proxy_pool_loaded()
    seen_video_ids = set()
    ok_count = 0
    total = len(links)

    def emit(kind, message):
        log(f"[Batch] {message}")
        if progress_callback:
            try:
                progress_callback(kind, message)
            except Exception:
                pass

    emit("info", f"Bắt đầu batch: {total} kênh")
    max_seconds = get_max_video_seconds()
    discovered = {}
    discovery_pool = []
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, total)) as pool:
        futures = {}
        for link in links:
            f = pool.submit(_discover_channel_latest, link, profile_name, max_seconds)
            futures[f] = link
        for future in concurrent.futures.as_completed(futures):
            if stop_event and stop_event.is_set():
                for f in futures:
                    f.cancel()
                break
            link = futures[future]
            try:
                _, video_id, title_or_error = future.result()
                if video_id:
                    discovered[link] = (video_id, title_or_error)
                    emit("info", f"{link}: {video_id}")
                else:
                    emit("error", f"{link}: {title_or_error}")
                    append_activity("batch_find", video_name=link, video_url=link, profile=profile_name, status="skipped", detail=title_or_error)
            except Exception as e:
                emit("error", f"{link}: {e}")

    if stop_event and stop_event.is_set():
        emit("warn", "Đã dừng batch.")
        return False, "Đã dừng."

    for link, (video_id, title_or_error) in discovered.items():
        if stop_event and stop_event.is_set():
            break
        if video_id in seen_video_ids:
            emit("warn", f"Bỏ qua trùng video: {video_id}")
            continue
        seen_video_ids.add(video_id)
        emit("info", f"Đang tải {video_id}: {title_or_error}")
        proxy = None
        if get_config().get("proxy_rotation", True):
            proxy = _proxy_for_profile(profile_name) or _next_proxy()
        ok = download_one(
            f"BATCH_{video_id}",
            video_id,
            target_folder=target_folder,
            process_short=True,
            proxy=proxy,
            activity_profile=profile_name,
        )
        if ok:
            ok_count += 1
            emit("success", f"OK {video_id}")
        else:
            emit("error", f"FAIL {video_id}")
    message = f"Xong: {ok_count}/{total}"
    emit("done", message)
    return True, message


def get_max_video_seconds():
    try:
        return max(0, int(get_config().get("max_video_minutes", 0))) * 60
    except Exception:
        return 0


def _run_ytdlp_download(video_id, url, opts):
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        limit_sec = get_max_video_seconds()
        duration = float(info.get("duration") or 0)
        if limit_sec > 0 and duration > limit_sec:
            reason = f"duration {_format_duration(duration)} > giới hạn {_format_duration(limit_sec)}"
            log(f"[DL] Bỏ qua {video_id}: {reason}")
            return None, None, reason
        fmt_id = info.get("format_id", "?")
        fmt_h = info.get("height", "?")
        fmt_ext = info.get("ext", "?")
        fmt_vcodec = info.get("vcodec", "?")
        requested = info.get("requested_formats")
        if requested:
            log(f"[DL] Format {video_id}: video={requested[0].get('format_id','?')} {requested[0].get('height','?')}p {requested[0].get('ext','?')} + audio={requested[1].get('format_id','?')} {requested[1].get('ext','?')}")
        else:
            log(f"[DL] Format {video_id}: {fmt_id} {fmt_h}p {fmt_ext} {fmt_vcodec}")
        ydl.process_ie_result(info, download=True)
        downloaded_path = info.get("requested_downloads", [{}])[0].get("filepath") if info.get("requested_downloads") else ydl.prepare_filename(info)
    return info, downloaded_path, ""


def _claim_download(video_id):
    with _active_downloads_lock:
        if video_id in _active_downloads:
            return False
        _active_downloads[video_id] = threading.current_thread().ident
        return True


def _release_download(video_id):
    with _active_downloads_lock:
        _active_downloads.pop(video_id, None)


def _staging_dir(target_folder):
    p = Path(target_folder).parent / ".youtube_tmp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def download_one(channel_id, video_id, published_iso=None, detected_iso=None, target_folder=None, process_short=None, proxy=None, activity_profile=None):
    global downloaded_today, downloaded_today_date
    t_start = time.perf_counter()
    if not _claim_download(video_id):
        log(f"[DL] Bỏ qua {video_id}: đang tải")
        return False
    _permanent = False
    try:
        meta = channels_store.get_meta(channel_id) or {}
        out_folder = target_folder or meta.get("folder") or str(DOWNLOADS_DIR / channel_id)
        Path(out_folder).mkdir(parents=True, exist_ok=True)
        process_short = meta.get("process_short", True) if process_short is None else bool(process_short)
        url = f"https://youtu.be/{video_id}"
        profile_name = meta.get("profile_name", "")
        activity_profile = activity_profile or profile_name or channel_id
        staging = _staging_dir(out_folder)
        dl_uuid = uuid.uuid4().hex[:8]
        dl_staging = staging / f"{video_id}-{dl_uuid}"
        dl_staging.mkdir(parents=True, exist_ok=True)
        temp_template = str(dl_staging / f"%(title).100s.%(ext)s")

        cfg = get_config()
        cf = max(1, int(cfg.get("concurrent_fragments", 8)))
        opts = {
            "format": FORMAT_FAST_720P,
            "outtmpl": temp_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 12,
            "nocheckcertificate": True,
            "windowsfilenames": True,
            "concurrent_fragment_downloads": cf,
            "cachedir": str(staging / ".ydl_cache"),
            "extractor_args": {"youtube": {"skip": ["hls"]}},
            "check_formats": False,
            "merge_output_format": "mp4",
        }
        Path(opts["cachedir"]).mkdir(exist_ok=True)
        cookies = _resolve_cookies_file()
        if cookies:
            opts["cookies"] = cookies
            log("[Cookies] Enabled")

        t_meta = time.perf_counter()
        log(f"[DL] Bắt đầu tải {video_id}")
        wait_s = 0.0
        dl_s = 0.0
        info = None
        downloaded_path = None
        skip_reason = ""
        try:
            with _download_sem:
                wait_s = time.perf_counter() - t_meta
                t_dl_start = time.perf_counter()
                info, downloaded_path, skip_reason = _run_ytdlp_download(video_id, url, opts)
                dl_s = time.perf_counter() - t_dl_start
        except Exception as e:
            err_class = _classify_download_error(e)
            if err_class == "permanent":
                _permanent = True
                log(f"[DL] Permanent error {video_id}: {e}")
                append_activity("youtube_download", video_name=video_id, video_url=url, profile=activity_profile, status="fail", detail=str(e)[:500])
                return False
            if proxy and (_is_youtube_block_error(e) or _is_proxy_download_error(e)):
                proxy_opts = dict(opts)
                proxy_opts["proxy"] = proxy
                log(f"[Proxy] Direct bị chặn/lỗi, thử {_mask_proxy(proxy)}")
                try:
                    with _download_sem:
                        wait_s = time.perf_counter() - t_meta
                        t_dl_start = time.perf_counter()
                        info, downloaded_path, skip_reason = _run_ytdlp_download(video_id, url, proxy_opts)
                        dl_s = time.perf_counter() - t_dl_start
                except Exception as proxy_error:
                    err_class2 = _classify_download_error(proxy_error)
                    if err_class2 == "permanent":
                        _permanent = True
                    log(f"[DL] Tải lỗi {video_id}: {proxy_error}")
                    append_activity("youtube_download", video_name=video_id, video_url=url, profile=activity_profile, status="fail", detail=str(proxy_error)[:500])
                    if _is_youtube_block_error(proxy_error):
                        log("[DL] Proxy cũng bị chặn. Hãy nạp cookies.txt từ browser login YouTube.")
                    return False
            else:
                err_class = _classify_download_error(e)
                if err_class == "permanent":
                    _permanent = True
                log(f"[DL] Tải lỗi {video_id}: {e}")
                append_activity("youtube_download", video_name=video_id, video_url=url, profile=activity_profile, status="fail", detail=str(e)[:500])
                return False
        if info is None:
            if "duration" in (skip_reason or "").lower() or "limit" in (skip_reason or "").lower():
                _permanent = True
            append_activity("youtube_download", video_name=video_id, video_url=url, profile=activity_profile, status="skipped", detail=skip_reason or "skipped")
            return False
        if not downloaded_path or not os.path.exists(downloaded_path):
            candidates = list(dl_staging.glob("*.mp4"))
            downloaded_path = str(candidates[0]) if candidates else ""
        if not downloaded_path or not os.path.exists(downloaded_path):
            log(f"[DL] Không tìm thấy file tải về cho {video_id}")
            append_activity("youtube_download", video_name=info.get("title") or video_id, video_url=url, profile=activity_profile, status="fail", detail="Không tìm thấy file tải về")
            _permanent = True
            return False

        dur = float(info.get("duration") or 0)
        needs_probe = dur < MIN_SECONDS or dur < 1
        t_process_start = time.perf_counter()
        processed_path, created_paths = downloaded_path, []
        if process_short and dur < MIN_SECONDS:
            if needs_probe:
                dur = ffmpeg_helper.probe_duration(downloaded_path) or 0
            if dur >= LOOP_MIN_DURATION:
                processed_path, created_paths = loop_to_min_duration_in_temp(downloaded_path, MIN_SECONDS)
            else:
                processed_path, created_paths = slowdown_to_min_duration_in_temp(downloaded_path, MIN_SECONDS)
        t_process_end = time.perf_counter()
        process_s = t_process_end - t_process_start

        t_move = time.perf_counter()
        try:
            final_path = _finalize_video(processed_path, out_folder, info.get("title") or video_id, video_id)
        except Exception as e:
            log(f"[DL] Finalize lỗi {video_id}: {e}")
            append_activity("youtube_download", video_name=info.get("title") or video_id, video_url=url, profile=activity_profile, status="fail", detail=str(e)[:500])
            return False
        t_move_end = time.perf_counter()
        move_s = t_move_end - t_move

        for path in created_paths:
            if path != final_path:
                try:
                    if os.path.exists(path): os.remove(path)
                except Exception:
                    pass

        total_s = time.perf_counter() - t_start
        size_mb = 0
        try: size_mb = os.path.getsize(final_path) / (1024 * 1024)
        except Exception: pass
        speed = f"{size_mb / max(0.1, dl_s):.1f}MB/s" if dl_s > 0 else "?"
        meta_s = t_meta - t_start
        log(f"[DL] {video_id} metadata={meta_s:.1f}s wait={wait_s:.1f}s download={dl_s:.1f}s process={process_s:.1f}s move={move_s:.2f}s total={total_s:.1f}s speed={speed}")

        append_csv_log(channel_id, video_id, published_iso, detected_iso or datetime.now(timezone.utc).isoformat(), final_path)
        title = info.get("title") or video_id
        append_activity("youtube_download", video_name=title, video_url=url, profile=activity_profile, status="success", detail=f"channel={channel_id}", file_path=final_path)
        remember_download(final_path, video_id=video_id, title=title, channel_id=channel_id, profile=activity_profile)
        today = datetime.now().strftime("%Y-%m-%d")
        if downloaded_today_date != today:
            downloaded_today_date = today
            downloaded_today = 0
        downloaded_today += 1
        log(f"[DL] Đã lưu: {final_path}")
        channels_store.mark_seen_only(channel_id, video_id)
        _remove_pending(channel_id, video_id)
        _clear_retry(channel_id, video_id)
        return True
    finally:
        _release_download(video_id)
        if _permanent:
            channels_store.mark_seen_only(channel_id, video_id)
            _remove_pending(channel_id, video_id)
            _clear_retry(channel_id, video_id)
        try:
            if 'dl_staging' in locals() and Path(dl_staging).is_dir():
                shutil.rmtree(dl_staging, ignore_errors=True)
        except Exception:
            pass


def worker_main(worker_id, run_gen=None):
    log(f"[Worker-{worker_id}] started")
    use_proxy = bool(get_config().get("proxy_rotation", True))
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log(f"[Worker-{worker_id}] Generation changed, stopping")
            break
        try:
            ch_id, vid_id, published_iso, detected_iso = download_queue.get(timeout=1)
        except queue.Empty:
            continue
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log(f"[Worker-{worker_id}] Generation changed after dequeue, stopping")
            download_queue.task_done()
            break
        try:
            proxy = None
            if use_proxy:
                meta = channels_store.get_meta(ch_id) or {}
                profile_name = meta.get("profile_name", "")
                proxy = _proxy_for_profile(profile_name) or _next_proxy()
                if profile_name and proxy:
                    log(f"[Proxy] {profile_name} -> {_mask_proxy(proxy)}")
            ok = download_one(ch_id, vid_id, published_iso, detected_iso, proxy=proxy)
            meta_seen = (channels_store.get_meta(ch_id) or {}).get("seen", set())
            if not ok and _is_pending(ch_id, vid_id) and vid_id not in meta_seen:
                with _retry_lock:
                    attempt_key = f"{ch_id}:{vid_id}:attempt"
                    attempt = _retry_after.get(attempt_key, 0)
                    if attempt < MAX_RETRIES:
                        _retry_after[attempt_key] = attempt + 1
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        _retry_after[f"{ch_id}:{vid_id}:due"] = time.time() + delay
                        log(f"[Worker-{worker_id}] {vid_id}: retry {attempt+1}/{MAX_RETRIES} sau {delay}s")
                    else:
                        log(f"[Worker-{worker_id}] {vid_id}: exhausted retries ({MAX_RETRIES}/{MAX_RETRIES}), giving up")
                        _retry_after.pop(attempt_key, None)
                        _remove_pending(ch_id, vid_id)
                        cooldown_key = f"{ch_id}:{vid_id}:cooldown"
                        _retry_after[cooldown_key] = time.time() + RETRY_COOLDOWN
        except Exception as e:
            log(f"[Worker-{worker_id}] lỗi {e}\n{traceback.format_exc()}")
        finally:
            download_queue.task_done()
    log(f"[Worker-{worker_id}] stopped")


def _retry_maintainer(run_gen=None):
    log("[Retry] Maintainer started")
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log("[Retry] Generation changed, stopping")
            break
        now = time.time()
        to_requeue = []
        now_expired = []
        with _retry_lock:
            for key, due in list(_retry_after.items()):
                if key.endswith(":due") and due <= now:
                    parts = key.rsplit(":", 2)
                    if len(parts) == 3:
                        ch_id, vid_id = parts[0], parts[1]
                        attempt_key = f"{ch_id}:{vid_id}:attempt"
                        attempt = _retry_after.get(attempt_key, 0)
                        if attempt <= MAX_RETRIES:
                            to_requeue.append((ch_id, vid_id, attempt))
                            _retry_after.pop(key, None)
                        else:
                            _retry_after.pop(attempt_key, None)
                            _retry_after.pop(key, None)
                            _remove_pending(ch_id, vid_id)
                            cooldown_key = f"{ch_id}:{vid_id}:cooldown"
                            _retry_after[cooldown_key] = time.time() + RETRY_COOLDOWN
                    continue
                if key.endswith(":cooldown") and due <= now:
                    now_expired.append(key)
        for key in now_expired:
            with _retry_lock:
                _retry_after.pop(key, None)

        for ch_id, vid_id, attempt in to_requeue:
            if _is_pending(ch_id, vid_id) and not stop_event.is_set():
                download_queue.put((ch_id, vid_id, None, datetime.now(timezone.utc).isoformat()))
                log(f"[Retry] Re-enqueue {vid_id} (attempt {attempt+1}/{MAX_RETRIES})")
        stop_event.wait(2)
    log("[Retry] Maintainer stopped")


def subscribe_websub(channel_id, callback_url):
    try:
        secret = _get_websub_secret()
        data = {
            "hub.mode": "subscribe",
            "hub.topic": f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}",
            "hub.callback": callback_url,
            "hub.verify": "async",
        }
        if secret:
            data["hub.secret"] = secret
        r = requests.post("https://pubsubhubbub.appspot.com/subscribe", data=data, timeout=10)
        with _subscription_lock:
            _subscription_status.setdefault(channel_id, {})
            _subscription_status[channel_id]["requested_at"] = datetime.now(timezone.utc).isoformat()
            _subscription_status[channel_id]["last_status"] = r.status_code
        log(f"[WebSub] Subscribe {channel_id}: {r.status_code}")
    except Exception as e:
        with _subscription_lock:
            _subscription_status.setdefault(channel_id, {})
            _subscription_status[channel_id]["requested_at"] = datetime.now(timezone.utc).isoformat()
            _subscription_status[channel_id]["last_error"] = str(e)
        log(f"[WebSub] Subscribe lỗi {channel_id}: {e}")


def _ngrok_bin_path():
    for path in (NGROK_BINARY, Path(_bundled_root()) / "ngrok.exe"):
        if path.exists():
            return str(path)
    return None


def _start_callback_server(preferred_port):
    global _callback_server, _callback_server_thread, _callback_port, _callback_instance_id, _callback_owner_token
    if _callback_server is not None:
        return True, _callback_port
    port = preferred_port
    last_error = None
    for attempt in range(3):
        try:
            server = make_server("0.0.0.0", port, flask_app, threaded=True)
            actual_port = server.server_address[1] if hasattr(server, "server_address") else port
            _callback_server = server
            _callback_port = actual_port
            _callback_instance_id = uuid.uuid4().hex[:8]
            _callback_owner_token = uuid.uuid4().hex
            server._started_at = datetime.now(timezone.utc).isoformat()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            _callback_server_thread = thread
            time.sleep(0.15)
            try:
                resp = requests.get(f"http://127.0.0.1:{actual_port}/youtube_health", timeout=2)
                if resp.status_code == 200:
                    log(f"[Callback] Server OK on :{actual_port}, instance={_callback_instance_id}")
                    return True, actual_port
            except Exception:
                pass
            log(f"[Callback] Server started on :{actual_port} but health check failed, retrying")
            try:
                server.shutdown()
            except Exception:
                pass
            _callback_server = None
            _callback_server_thread = None
            _callback_port = None
            _callback_instance_id = None
            _callback_owner_token = None
            last_error = "Health check failed"
            port = 0
        except Exception as e:
            err_text = str(e).lower()
            if "address already in use" in err_text or "errno 10048" in err_text or "errno 98" in err_text:
                log(f"[Callback] Port {port} in use, trying fallback port")
                port = 0
            else:
                log(f"[Callback] Failed to bind :{port}: {e}")
                last_error = str(e)
                port = 0
    return False, last_error or "Cannot bind callback server"


def _stop_callback_server():
    global _callback_server, _callback_server_thread, _callback_port, _callback_instance_id, _callback_owner_token
    try:
        if _callback_server:
            _callback_server.shutdown()
    except Exception:
        pass
    _callback_server = None
    _callback_server_thread = None
    _callback_port = None
    _callback_instance_id = None
    _callback_owner_token = None


def _verify_ngrok_tunnel(ngrok_url):
    challenge = f"verify_{uuid.uuid4().hex[:12]}"
    try:
        resp = requests.get(
            f"{ngrok_url}/youtube_callback?owner={_callback_owner_token}&hub.challenge={challenge}",
            timeout=10
        )
        if resp.status_code == 200 and resp.text.strip() == challenge:
            log("[Ngrok] Tunnel verified")
            return True
        else:
            log(f"[Ngrok] Tunnel verification failed: status={resp.status_code}")
            return False
    except Exception as e:
        log(f"[Ngrok] Tunnel verification error: {e}")
        return False


def _start_ngrok(port):
    global public_callback_url, public_callback_verified
    token = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if token:
        try:
            ngrok.set_auth_token(token)
        except Exception as e:
            log(f"[Ngrok] Token lỗi: {e}")
    ng_bin = _ngrok_bin_path()
    ngcfg = ngconf.PyngrokConfig(ngrok_path=ng_bin) if ng_bin else ngconf.get_default()
    tunnel = ngrok.connect(port, "http", pyngrok_config=ngcfg)
    ngrok_url = tunnel.public_url.rstrip("/")
    public_callback_url = f"{ngrok_url}/youtube_callback?owner={_callback_owner_token}"
    log(f"[Ngrok] Callback: {public_callback_url}")
    if _verify_ngrok_tunnel(ngrok_url):
        public_callback_verified = True
        return True
    else:
        try:
            ngrok.disconnect(ngrok_url)
        except Exception:
            pass
        try:
            ngrok.kill()
        except Exception:
            pass
        public_callback_url = None
        public_callback_verified = False
        return False


def _resubscribe_worker(run_gen=None):
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            break
        stop_event.wait(RESUBSCRIBE_INTERVAL_DAYS * 24 * 3600)
        if public_callback_url and not stop_event.is_set():
            channels_store.subscribe_all(public_callback_url)


def _polling_worker(run_gen=None):
    log("[Polling] Worker started")
    poll_interval = 900
    while not stop_event.is_set():
        if run_gen is not None and _get_monitor_gen() != run_gen:
            log("[Polling] Generation changed, stopping")
            break
        if not public_callback_verified:
            poll_interval = 120
        else:
            poll_interval = 900
        with _subscription_lock:
            has_unverified = any(s.get("verified_at") is None for s in _subscription_status.values())
        if has_unverified:
            poll_interval = min(poll_interval, 120)
        try:
            cfg = get_config()
            keys = cfg.get("api_keys") or []
            if not keys or not keys[0]:
                stop_event.wait(poll_interval)
                continue
            youtube = get_youtube_client(keys[0])
            active_channels = [k for k, v in channels_store.all_items().items() if v.get("active")]
            for cid in active_channels:
                if stop_event.is_set():
                    break
                meta = channels_store.get_meta(cid) or {}
                playlist_id = _get_uploads_playlist_id(cid, youtube)
                if not playlist_id:
                    continue
                try:
                    response = youtube.playlistItems().list(
                        part="snippet,contentDetails",
                        playlistId=playlist_id,
                        maxResults=5
                    ).execute()
                except Exception as e:
                    log(f"[Polling] API lỗi {cid}: {e}")
                    break
                seen = meta.get("seen", set())
                for item in response.get("items", []):
                    vid = item.get("contentDetails", {}).get("videoId") or item.get("snippet", {}).get("resourceId", {}).get("videoId")
                    if not vid or vid in seen or _is_pending(cid, vid):
                        continue
                    published = item.get("snippet", {}).get("publishedAt", "")
                    pub_epoch = iso_to_epoch(published) if published else None
                    if pub_epoch and time.time() - pub_epoch > MAX_ACCEPTABLE_AGE_HOURS * 3600:
                        channels_store.mark_seen_only(cid, vid)
                        continue
                    if _try_pending(cid, vid):
                        download_queue.put((cid, vid, published or None, datetime.now(timezone.utc).isoformat()))
                        log(f"[Polling] Enqueue {vid}@{cid}")
                time.sleep(0.3)
        except Exception as e:
            log(f"[Polling] Lỗi: {e}")
        stop_event.wait(poll_interval)
    log("[Polling] Worker stopped")


def _get_uploads_playlist_id(channel_id, youtube):
    try:
        response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
        items = response.get("items", [])
        if items:
            return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception:
        pass
    return None


WEBSECRET_KEY = "websub_secret"
MAX_CALLBACK_BODY = 1 * 1024 * 1024


def _get_websub_secret():
    global _websub_secret_cache
    with _websub_secret_lock:
        if _websub_secret_cache:
            return _websub_secret_cache
        try:
            cfg = get_config()
            secret = cfg.get(WEBSECRET_KEY, "")
            if secret and len(secret) >= 16:
                _websub_secret_cache = secret
                return secret
            import hashlib
            secret = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
            cfg[WEBSECRET_KEY] = secret
            _save_config(cfg)
            _websub_secret_cache = secret
            log("[WebSub] Generated new secret")
            return secret
        except Exception as e:
            log(f"[WebSub] Secret error: {e}")
            return ""


def _verify_websub_signature(payload_bytes, signature_header):
    if not signature_header:
        return False
    secret = _get_websub_secret()
    if not secret:
        return False
    sig = signature_header.strip()
    if sig.startswith("sha256="):
        algo = "sha256"
        provided = sig[len("sha256="):]
    elif sig.startswith("sha1="):
        algo = "sha1"
        provided = sig[len("sha1="):]
    else:
        return False
    expected = hmac.new(secret.encode(), payload_bytes, algo).hexdigest()
    return hmac.compare_digest(expected, provided.lower())


def _get_monitor_gen():
    with _monitor_gen_lock:
        return _monitor_gen


def _join_all_threads(timeout=5):
    deadline = time.time() + timeout
    threads = list(_all_threads)
    for t in threads:
        if t.is_alive():
            remaining = max(0.05, deadline - time.time())
            if remaining <= 0:
                break
            t.join(timeout=remaining)


def _live_monitor_threads():
    return [t for t in list(_all_threads) if t.is_alive()]


def _add_thread(t):
    _all_threads.append(t)


def start_monitor():
    global _monitor_started, _monitor_gen, last_error, _proxy_pool, _proxy_by_profile, _proxy_rr_index, _download_sem, public_callback_url, public_callback_verified
    with _state_lock:
        if _monitor_started:
            ok, msg = get_monitor_health()
            if ok:
                return True, "YouTube Monitor đang chạy."
            log("[Monitor] State says started but unhealthy, force stopping")
            _force_stop()
        live_threads = _live_monitor_threads()
        if live_threads:
            return False, f"Monitor cũ chưa dừng hết ({len(live_threads)} thread còn sống)."
        with _monitor_gen_lock:
            _monitor_gen += 1
            run_gen = _monitor_gen
        stop_event.clear()
        _all_threads[:] = [t for t in _all_threads if t.is_alive()]
        channels_store.load()
        channels_store.start_autosave()
        cfg = get_config()
        if cfg.get("proxy_rotation", True):
            _proxy_by_profile, _proxy_pool = _load_tiktok_proxies()
        else:
            _proxy_by_profile, _proxy_pool = {}, []
            log("[Proxy] Proxy rotation disabled")
        _proxy_rr_index = 0
        if "workers" in cfg and "download_workers" not in cfg:
            cfg["download_workers"] = max(1, int(cfg.pop("workers", 8) // 2 or 4))
            _save_config(cfg)
        workers = max(1, int(cfg.get("download_workers", 4) or 4))
        _download_sem = threading.Semaphore(workers)
        ngrok_port = int(cfg.get("ngrok_port", NGROK_PORT_DEFAULT) or NGROK_PORT_DEFAULT)
        ok, port_or_err = _start_callback_server(ngrok_port)
        if not ok:
            last_error = f"Callback server: {port_or_err}"
            log(f"[Monitor] {last_error}")
            channels_store.stop_autosave()
            return False, last_error
        if not _get_websub_secret():
            _stop_callback_server()
            channels_store.stop_autosave()
            last_error = "Không tạo được WebSub secret"
            log(f"[Monitor] {last_error}")
            return False, last_error
        t = threading.Thread(target=websub_processor_worker, args=(run_gen,), daemon=True)
        _add_thread(t)
        t.start()
        for i in range(workers):
            t = threading.Thread(target=worker_main, args=(i + 1, run_gen), daemon=True)
            _add_thread(t)
            t.start()
        t = threading.Thread(target=_retry_maintainer, args=(run_gen,), daemon=True)
        _add_thread(t)
        t.start()
        t = threading.Thread(target=_resubscribe_worker, args=(run_gen,), daemon=True)
        _add_thread(t)
        t.start()
        ngrok_ok = False
        try:
            ngrok_ok = _start_ngrok(_callback_port)
        except Exception as e:
            last_error = f"Ngrok: {e}"
            log(f"[Ngrok] Start lỗi: {e}")
        if not ngrok_ok:
            _stop_callback_server()
            stop_event.set()
            _join_all_threads()
            channels_store.stop_autosave()
            last_error = "Ngrok tunnel không hoạt động"
            log(f"[Monitor] {last_error}")
            return False, last_error
        channels_store.subscribe_all(public_callback_url)
        t = threading.Thread(target=_polling_worker, args=(run_gen,), daemon=True)
        _add_thread(t)
        t.start()
        _monitor_started = True
        return True, "YouTube Monitor đã start."


def get_monitor_health():
    if not _monitor_started:
        return False, "Monitor chưa chạy"
    if _callback_port:
        try:
            resp = requests.get(f"http://127.0.0.1:{_callback_port}/youtube_health", timeout=2)
            if resp.status_code == 200:
                return True, "OK"
        except Exception:
            pass
    return False, "Callback server không phản hồi"


def _force_stop():
    global _monitor_started, _callback_server, _callback_server_thread, _callback_port, _callback_instance_id, public_callback_url, public_callback_verified
    stop_event.set()
    _stop_callback_server()
    try:
        ngrok.kill()
    except Exception:
        pass
    _join_all_threads(timeout=3)
    if _live_monitor_threads():
        log("[Monitor] Force stop còn thread sống, giữ state để tránh restart đè generation")
        return
    public_callback_url = None
    public_callback_verified = False
    _active_downloads.clear()
    _pending_video_ids.clear()
    _retry_after.clear()
    _monitor_started = False


def stop_monitor():
    global _monitor_started, public_callback_url, public_callback_verified
    with _state_lock:
        if not _monitor_started:
            return True, "YouTube Monitor chưa chạy."
        stop_event.set()
        channels_store.stop_autosave()
        _stop_callback_server()
        try:
            ngrok.kill()
        except Exception:
            pass
        _join_all_threads(timeout=5)
        if _live_monitor_threads():
            return False, f"YouTube Monitor chưa dừng hết ({len(_live_monitor_threads())} thread còn sống)."
        public_callback_url = None
        public_callback_verified = False
        _active_downloads.clear()
        _pending_video_ids.clear()
        _retry_after.clear()
        _all_threads.clear()
        _monitor_started = False
        log("[Monitor] Stopped")
    return True, "YouTube Monitor đã dừng."


def add_channel_for_profile(channel_input, profile_name, folder_path):
    youtube = get_youtube_client()
    info = get_channel_id_from_link(channel_input, youtube)
    if not info or not info.get("id"):
        return False, "Không lấy được Channel ID."
    channels_store.add_channel(info["id"], folder_path, profile_name=profile_name, process_short=True)
    channels_store.save_now()
    if public_callback_url:
        subscribe_websub(info["id"], public_callback_url)
    log(f"[Channel] Added {info['id']} -> {profile_name}")
    return True, info["id"]


def download_test_video(video_input, profile_name, folder_path):
    video_id = _extract_video_id(video_input)
    if not video_id:
        return False, "Video URL/ID không hợp lệ."
    def run():
        proxy = None
        if get_config().get("proxy_rotation", True):
            _ensure_proxy_pool_loaded()
            proxy = _proxy_for_profile(profile_name) or _next_proxy()
            if proxy:
                log(f"[Proxy] {profile_name} -> {_mask_proxy(proxy)}")
        ok = download_one(f"TEST_{profile_name}", video_id, None, datetime.now(timezone.utc).isoformat(), target_folder=folder_path, process_short=True, proxy=proxy, activity_profile=profile_name)
        log(f"[Test] {'OK' if ok else 'FAIL'} {video_id} -> {profile_name}")
    threading.Thread(target=run, daemon=True).start()
    return True, f"Đã đưa test video {video_id} vào queue tải."


def get_channels():
    items = []
    for cid, meta in channels_store.all_items().items():
        items.append({
            "channel_id": cid,
            "folder": meta.get("folder", ""),
            "profile_name": meta.get("profile_name", ""),
            "active": bool(meta.get("active", True)),
            "process_short": bool(meta.get("process_short", True)),
            "seen_count": len(meta.get("seen", set())),
            "last_pub_utc": meta.get("last_pub_utc"),
        })
    return items


def remove_channel(channel_id):
    channels_store.remove_channel(channel_id)
    channels_store.save_now()
    return True, "Đã xóa channel."


def set_channel_profile(channel_id, profile_name, folder_path):
    channels_store.set_folder(channel_id, folder_path, profile_name=profile_name)
    channels_store.save_now()
    return True, "Đã cập nhật profile đích."


def toggle_channel_active(channel_id):
    value = channels_store.toggle_active(channel_id)
    channels_store.save_now()
    return True, f"Active={value}"


def toggle_channel_short(channel_id):
    value = channels_store.toggle_process_short(channel_id)
    channels_store.save_now()
    return True, f"Short={value}"


def get_status():
    cfg = get_config()
    healthy, health_msg = get_monitor_health()
    subs_ok = 0
    with _subscription_lock:
        total_subs = len(_subscription_status)
        subs_ok = sum(1 for s in _subscription_status.values() if s.get("verified_at"))
    return {
        "running": _monitor_started,
        "healthy": healthy,
        "health_msg": health_msg,
        "callback_url": public_callback_url or "",
        "callback_port": _callback_port,
        "callback_verified": public_callback_verified,
        "last_callback_post": last_callback_post_time,
        "channels": len(channels_store.all_items()),
        "queue": download_queue.qsize(),
        "workers": len([t for t in _all_threads if t.is_alive()]),
        "downloaded_today": downloaded_today,
        "last_error": last_error,
        "api_key_set": bool((cfg.get("api_keys") or [""])[0]),
        "cookies_set": bool(_resolve_cookies_file()),
        "download_workers": max(1, int(cfg.get("download_workers", 4) or 4)),
        "subscriptions_total": total_subs,
        "subscriptions_ok": subs_ok,
        "subscriptions_degraded": total_subs - subs_ok,
        "pending": len([v for v in _pending_video_ids if not stop_event.is_set()]),
    }
