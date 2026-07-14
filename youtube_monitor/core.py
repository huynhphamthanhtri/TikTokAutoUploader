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
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, request
from pyngrok import conf as ngconf
from pyngrok import ngrok
from yt_dlp import YoutubeDL
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .activity import append_activity, remember_download


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
CONFIG_DEFAULTS = {
    "api_keys": [],
    "ngrok_port": NGROK_PORT_DEFAULT,
    "workers": 8,
    "max_video_minutes": 0,
    "auto_start": True,
    "cookies_file": "",
    "proxy_rotation": True,
}

DOWNLOADS_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

log_queue = queue.Queue()
download_queue = queue.Queue()
websub_payload_queue = queue.Queue()
stop_event = threading.Event()
_state_lock = threading.RLock()
_store_lock = threading.RLock()
_encode_sem = threading.Semaphore(os.cpu_count() or 4)
_flask_started = False
_monitor_started = False
_workers_threads = []
_processor_thread = None
_resubscriber_thread = None
_proxy_pool = []
_proxy_by_profile = {}
_proxy_rr_index = 0
_proxy_lock = threading.Lock()
public_callback_url = None
last_error = ""
downloaded_today = 0
downloaded_today_date = datetime.now().strftime("%Y-%m-%d")


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
    with open(CONFIG_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)


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
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with _store_lock:
                data = self._serialize()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            self._dirty = False
        except Exception as e:
            log(f"[Channels] Save lỗi: {e}")

    def start_autosave(self):
        if self._autosave_thread and self._autosave_thread.is_alive():
            return
        self._autosave_stop.clear()
        def loop():
            while not self._autosave_stop.is_set():
                time.sleep(5)
                if self._dirty:
                    self.save_now()
        self._autosave_thread = threading.Thread(target=loop, daemon=True)
        self._autosave_thread.start()

    def stop_autosave(self):
        self._autosave_stop.set()
        if self._autosave_thread:
            self._autosave_thread.join(timeout=2)
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

    def remove_channel(self, cid):
        with _store_lock:
            self._channels.pop(cid, None)
            self._dirty = True

    def set_folder(self, cid, folder, profile_name=""):
        with _store_lock:
            if cid in self._channels:
                self._channels[cid]["folder"] = folder
                if profile_name:
                    self._channels[cid]["profile_name"] = profile_name
                self._dirty = True

    def toggle_active(self, cid):
        with _store_lock:
            if cid not in self._channels:
                return None
            self._channels[cid]["active"] = not bool(self._channels[cid].get("active", True))
            self._dirty = True
            return self._channels[cid]["active"]

    def toggle_process_short(self, cid):
        with _store_lock:
            if cid not in self._channels:
                return None
            self._channels[cid]["process_short"] = not bool(self._channels[cid].get("process_short", True))
            self._dirty = True
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
            return True

    def mark_seen_only(self, cid, vid):
        with _store_lock:
            meta = self._channels.get(cid)
            if meta:
                meta.setdefault("seen", set()).add(vid)
                self._dirty = True

    def update_watermark(self, cid, pub_epoch):
        if pub_epoch is None:
            return
        with _store_lock:
            meta = self._channels.get(cid)
            if meta and (meta.get("last_pub_utc") is None or pub_epoch > meta.get("last_pub_utc")):
                meta["last_pub_utc"] = pub_epoch
                self._dirty = True

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
    if request.method == "GET":
        return request.args.get("hub.challenge", ""), 200
    data = request.data.decode("utf-8", errors="ignore")
    websub_payload_queue.put((data, datetime.now(timezone.utc).isoformat()))
    return "", 200


def _parse_websub_xml(xml_text):
    vids = re.findall(r"<yt:videoId>(.*?)</yt:videoId>", xml_text)
    chans = re.findall(r"<yt:channelId>(.*?)</yt:channelId>", xml_text)
    publisheds = re.findall(r"<published>(.*?)</published>", xml_text)
    return [(vid, chans[i] if i < len(chans) else None, publisheds[i] if i < len(publisheds) else None) for i, vid in enumerate(vids)]


def iso_to_epoch(value):
    try:
        value = value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def websub_processor_worker():
    log("[WebSub] Processor started")
    while not stop_event.is_set():
        try:
            data, detected_utc_iso = websub_payload_queue.get(timeout=1)
        except queue.Empty:
            continue
        now_epoch = time.time()
        for vid, chan, published in _parse_websub_xml(data):
            if not chan:
                continue
            pub_epoch = iso_to_epoch(published) if published else None
            if channels_store.should_reject_by_watermark(chan, pub_epoch, WATERMARK_SLACK_MINUTES * 60):
                channels_store.mark_seen_only(chan, vid)
                continue
            if pub_epoch is not None and now_epoch - pub_epoch > MAX_ACCEPTABLE_AGE_HOURS * 3600:
                channels_store.mark_seen_only(chan, vid)
                channels_store.update_watermark(chan, pub_epoch)
                continue
            if channels_store.get_active_and_unseen_guard(chan, vid):
                download_queue.put((chan, vid, published or None, detected_utc_iso))
                channels_store.update_watermark(chan, pub_epoch)
                log(f"[WebSub] Enqueue {vid}@{chan}")
        channels_store._dirty = True
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


def build_final_path_from_title(out_folder, title, video_id, ext=".mp4"):
    base = sanitize_filename(title)
    for candidate in [Path(out_folder) / f"{base}{ext}", Path(out_folder) / f"{base} - {video_id[:8]}{ext}"]:
        if not candidate.exists():
            return str(candidate)
    for i in range(2, 1000):
        candidate = Path(out_folder) / f"{base} ({i}){ext}"
        if not candidate.exists():
            return str(candidate)
    return str(Path(out_folder) / f"{base} - {uuid.uuid4().hex[:6]}{ext}")


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


def slowdown_to_min_duration_in_temp(input_path, target_seconds):
    dur = probe_duration_seconds(input_path) or 0
    if dur >= target_seconds - 0.01 or dur <= 0:
        return input_path, []
    factor = max(1.0, target_seconds / dur)
    out_path = str(TEMP_DIR / f"{uuid.uuid4().hex}.slow.mp4")
    has_aud = has_audio_stream(input_path)
    if has_aud:
        atempo = ",".join([f"atempo={t:.5g}" for t in _build_atempo_chain(factor)])
        filters = ["-filter_complex", f"[0:v]setpts={factor:.6f}*PTS,format=yuv420p[v];[0:a]{atempo}[a]", "-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    else:
        filters = ["-filter:v", f"setpts={factor:.6f}*PTS,format=yuv420p", "-an"]
    cmd = ["ffmpeg", "-y", "-i", input_path] + filters + ["-t", str(int(target_seconds)), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-threads", str(max(1, (os.cpu_count() or 4) // 2)), out_path]
    try:
        _encode_sem.acquire()
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if p.returncode == 0 and os.path.exists(out_path):
            try: os.remove(input_path)
            except Exception: pass
            return out_path, [out_path]
        log(f"[FFmpeg] slow-mo lỗi: {p.stderr[:200]}")
    finally:
        try: _encode_sem.release()
        except Exception: pass
    return input_path, []


def loop_to_min_duration_in_temp(input_path, target_seconds):
    dur = probe_duration_seconds(input_path) or 0
    if dur >= target_seconds - 0.01 or dur <= 0:
        return input_path, []
    out_path = str(TEMP_DIR / f"{uuid.uuid4().hex}.loop.mp4")
    list_file = str(TEMP_DIR / f"{uuid.uuid4().hex}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for _ in range(math.ceil(target_seconds / dur)):
            f.write(f"file '{input_path}'\n")
    try:
        _encode_sem.acquire()
        p = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-t", str(int(target_seconds)), out_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if p.returncode == 0 and os.path.exists(out_path):
            try: os.remove(input_path)
            except Exception: pass
            return out_path, [out_path, list_file]
        log(f"[FFmpeg] loop lỗi: {p.stderr[:200]}")
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
    for index, link in enumerate(links, 1):
        if stop_event and stop_event.is_set():
            emit("warn", "Đã dừng batch.")
            return False, "Đã dừng."
        emit("info", f"[{index}/{total}] Tìm video mới nhất: {link}")
        max_seconds = get_max_video_seconds()
        video_id, title_or_error = find_latest_video(link, max_seconds=max_seconds)
        if not video_id:
            emit("error", f"{link}: {title_or_error}")
            append_activity("batch_find", video_name=link, video_url=link, profile=profile_name, status="skipped", detail=title_or_error)
            continue
        if video_id in seen_video_ids:
            emit("warn", f"Bỏ qua trùng video: {video_id}")
            continue
        seen_video_ids.add(video_id)
        emit("info", f"{link} -> {video_id}: {title_or_error}")
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


def download_one(channel_id, video_id, published_iso=None, detected_iso=None, target_folder=None, process_short=None, proxy=None, activity_profile=None):
    global downloaded_today, downloaded_today_date
    meta = channels_store.get_meta(channel_id) or {}
    out_folder = target_folder or meta.get("folder") or str(DOWNLOADS_DIR / channel_id)
    Path(out_folder).mkdir(parents=True, exist_ok=True)
    process_short = meta.get("process_short", True) if process_short is None else bool(process_short)
    url = f"https://youtu.be/{video_id}"
    profile_name = meta.get("profile_name", "")
    activity_profile = activity_profile or profile_name or channel_id
    temp_template = str(TEMP_DIR / f"{video_id}.%(ext)s")
    opts = {
        "format": "bv*[height<=1080][vcodec^=avc]+ba[ext=m4a]/bv*[height<=1080][ext=mp4][vcodec^=avc1]+ba/bv*[height<=1080]+ba/b[height<=1080]/best",
        "outtmpl": temp_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 12,
        "nocheckcertificate": True,
        "windowsfilenames": True,
        "concurrent_fragment_downloads": 8,
        "cachedir": str(TEMP_DIR / ".ydl_cache"),
        "extractor_args": {"youtube": {"skip": ["hls"]}},
        "check_formats": False,
    }
    Path(opts["cachedir"]).mkdir(exist_ok=True)
    cookies = _resolve_cookies_file()
    if cookies:
        opts["cookies"] = cookies
        log("[Cookies] Enabled")
    log(f"[DL] Bắt đầu tải {video_id} direct{' + cookies' if cookies else ''}")
    try:
        info, downloaded_path, skip_reason = _run_ytdlp_download(video_id, url, opts)
    except Exception as e:
        if proxy and (_is_youtube_block_error(e) or _is_proxy_download_error(e)):
            proxy_opts = dict(opts)
            proxy_opts["proxy"] = proxy
            log(f"[Proxy] Direct bị chặn/lỗi, thử {_mask_proxy(proxy)}")
            try:
                info, downloaded_path, skip_reason = _run_ytdlp_download(video_id, url, proxy_opts)
            except Exception as proxy_error:
                log(f"[DL] Tải lỗi {video_id}: {proxy_error}")
                append_activity("youtube_download", video_name=video_id, video_url=url, profile=activity_profile, status="fail", detail=str(proxy_error)[:500])
                if _is_youtube_block_error(proxy_error):
                    log("[DL] Proxy cũng bị chặn. Hãy nạp cookies.txt từ browser login YouTube.")
                return False
        else:
            log(f"[DL] Tải lỗi {video_id}: {e}")
            append_activity("youtube_download", video_name=video_id, video_url=url, profile=activity_profile, status="fail", detail=str(e)[:500])
            return False
    if info is None:
        append_activity("youtube_download", video_name=video_id, video_url=url, profile=activity_profile, status="skipped", detail=skip_reason or "skipped")
        return False
    if not downloaded_path or not os.path.exists(downloaded_path):
        guess = TEMP_DIR / f"{video_id}.mp4"
        downloaded_path = str(guess) if guess.exists() else ""
    if not downloaded_path or not os.path.exists(downloaded_path):
        log(f"[DL] Không tìm thấy file tải về cho {video_id}")
        append_activity("youtube_download", video_name=info.get("title") or video_id, video_url=url, profile=activity_profile, status="fail", detail="Không tìm thấy file tải về")
        return False
    dur = probe_duration_seconds(downloaded_path) or 0
    processed_path, created_paths = downloaded_path, []
    if process_short and dur < MIN_SECONDS:
        if dur >= LOOP_MIN_DURATION:
            processed_path, created_paths = loop_to_min_duration_in_temp(downloaded_path, MIN_SECONDS)
        else:
            processed_path, created_paths = slowdown_to_min_duration_in_temp(downloaded_path, MIN_SECONDS)
    final_path = build_final_path_from_title(out_folder, info.get("title") or video_id, video_id, os.path.splitext(processed_path)[1] or ".mp4")
    try:
        os.replace(processed_path, final_path)
    except Exception:
        shutil.move(processed_path, final_path)
    for path in created_paths:
        if path != final_path:
            try:
                if os.path.exists(path): os.remove(path)
            except Exception:
                pass
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
    return True


def worker_main(worker_id):
    log(f"[Worker-{worker_id}] started")
    use_proxy = bool(get_config().get("proxy_rotation", True))
    while not stop_event.is_set():
        try:
            ch_id, vid_id, published_iso, detected_iso = download_queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            proxy = None
            if use_proxy:
                meta = channels_store.get_meta(ch_id) or {}
                profile_name = meta.get("profile_name", "")
                proxy = _proxy_for_profile(profile_name) or _next_proxy()
                if profile_name and proxy:
                    log(f"[Proxy] {profile_name} -> {_mask_proxy(proxy)}")
            download_one(ch_id, vid_id, published_iso, detected_iso, proxy=proxy)
        except Exception as e:
            log(f"[Worker-{worker_id}] lỗi {e}\n{traceback.format_exc()}")
        finally:
            download_queue.task_done()
    log(f"[Worker-{worker_id}] stopped")


def subscribe_websub(channel_id, callback_url):
    try:
        r = requests.post("https://pubsubhubbub.appspot.com/subscribe", data={"hub.mode": "subscribe", "hub.topic": f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}", "hub.callback": callback_url, "hub.verify": "async"}, timeout=10)
        log(f"[WebSub] Subscribe {channel_id}: {r.status_code}")
    except Exception as e:
        log(f"[WebSub] Subscribe lỗi {channel_id}: {e}")


def _ngrok_bin_path():
    for path in (NGROK_BINARY, Path(_bundled_root()) / "ngrok.exe"):
        if path.exists():
            return str(path)
    return None


def _start_flask_if_needed(port):
    global _flask_started
    if _flask_started:
        return
    def run():
        flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    threading.Thread(target=run, daemon=True).start()
    _flask_started = True
    log(f"[Flask] Started on port {port}")


def _start_ngrok(port):
    global public_callback_url
    token = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if token:
        try: ngrok.set_auth_token(token)
        except Exception as e: log(f"[Ngrok] Token lỗi: {e}")
    ng_bin = _ngrok_bin_path()
    cfg = ngconf.PyngrokConfig(ngrok_path=ng_bin) if ng_bin else ngconf.get_default()
    public = ngrok.connect(port, "http", pyngrok_config=cfg).public_url
    public_callback_url = f"{public}/youtube_callback"
    log(f"[Ngrok] Callback: {public_callback_url}")
    channels_store.subscribe_all(public_callback_url)


def _resubscribe_worker():
    while not stop_event.is_set():
        stop_event.wait(RESUBSCRIBE_INTERVAL_DAYS * 24 * 3600)
        if public_callback_url and not stop_event.is_set():
            channels_store.subscribe_all(public_callback_url)


def start_monitor():
    global _monitor_started, _processor_thread, _resubscriber_thread, last_error, _proxy_pool, _proxy_by_profile, _proxy_rr_index
    with _state_lock:
        if _monitor_started:
            return True, "YouTube Monitor đang chạy."
        stop_event.clear()
        channels_store.load()
        channels_store.start_autosave()
        cfg = get_config()
        if cfg.get("proxy_rotation", True):
            _proxy_by_profile, _proxy_pool = _load_tiktok_proxies()
        else:
            _proxy_by_profile, _proxy_pool = {}, []
            log("[Proxy] Proxy rotation disabled")
        _proxy_rr_index = 0
        workers = max(1, int(cfg.get("workers", 8) or 8))
        _start_flask_if_needed(int(cfg.get("ngrok_port", NGROK_PORT_DEFAULT) or NGROK_PORT_DEFAULT))
        _processor_thread = threading.Thread(target=websub_processor_worker, daemon=True)
        _processor_thread.start()
        for i in range(workers):
            t = threading.Thread(target=worker_main, args=(i + 1,), daemon=True)
            _workers_threads.append(t)
            t.start()
        _resubscriber_thread = threading.Thread(target=_resubscribe_worker, daemon=True)
        _resubscriber_thread.start()
        _monitor_started = True
    try:
        _start_ngrok(int(get_config().get("ngrok_port", NGROK_PORT_DEFAULT) or NGROK_PORT_DEFAULT))
    except Exception as e:
        last_error = str(e)
        log(f"[Ngrok] Auto-start lỗi: {e}")
    return True, "YouTube Monitor đã start."


def stop_monitor():
    global _monitor_started, public_callback_url
    with _state_lock:
        if not _monitor_started:
            return True, "YouTube Monitor chưa chạy."
        stop_event.set()
        channels_store.stop_autosave()
        try: ngrok.kill()
        except Exception: pass
        public_callback_url = None
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
    return {
        "running": _monitor_started,
        "callback_url": public_callback_url or "",
        "channels": len(channels_store.all_items()),
        "queue": download_queue.qsize(),
        "workers": len([t for t in _workers_threads if t.is_alive()]),
        "downloaded_today": downloaded_today,
        "last_error": last_error,
        "api_key_set": bool((cfg.get("api_keys") or [""])[0]),
        "cookies_set": bool(_resolve_cookies_file()),
    }
