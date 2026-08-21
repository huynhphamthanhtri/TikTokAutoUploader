"""Owned ngrok agent lifecycle.

Runs a dedicated ngrok agent for this app's YouTube monitor and records its
ownership in an atomic JSON file. The agent is identified by a unique config
file and an explicit API port so it can be stopped without ever calling the
global ``ngrok.kill()`` (which would terminate external ngrok processes).

Safety contract:
- The agent process is only terminated when the recorded PID is alive, its
  creation time matches the record, and its executable path matches the record.
- Processes that do not match the record are never killed.
"""
import json
import os
import re
import socket
import threading
import time
import uuid
from pathlib import Path

import psutil
from pyngrok import conf as ngconf
from pyngrok import ngrok

from . import ngrok_helper

_OWNERSHIP_LOCK = threading.RLock()
_API_PORT_LOCK = threading.Lock()
_API_PORT = 0
_PATHS_INIT = False

CONFIG_DIR = None
AGENT_CONFIG_YML = None
OWNERSHIP_JSON = None


def _init_paths():
    global _PATHS_INIT, CONFIG_DIR, AGENT_CONFIG_YML, OWNERSHIP_JSON
    if _PATHS_INIT:
        return
    root = Path(__file__).resolve().parent.parent
    CONFIG_DIR = root / "youtube_monitor_ngrok"
    AGENT_CONFIG_YML = CONFIG_DIR / "agent.yml"
    OWNERSHIP_JSON = CONFIG_DIR / "ownership.json"
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    _PATHS_INIT = True


_init_paths()


def _log(message):
    try:
        from .core import log
        log(message)
    except Exception:
        pass


def _ngrok_config_candidates():
    """Candidate ngrok config paths (standard user config locations)."""
    paths = []
    try:
        default_path = ngconf.get_default().config_path
        if default_path:
            paths.append(Path(str(default_path)))
    except Exception:
        pass
    try:
        d = getattr(ngconf, "DEFAULT_NGROK_CONFIG_PATH", None)
        if d:
            paths.append(Path(str(d)))
    except Exception:
        pass
    home = Path(os.environ.get("USERPROFILE", "") or str(Path.home()))
    local = Path(os.environ.get("LOCALAPPDATA", "")) or (home / "AppData" / "Local")
    base = [
        local / "ngrok" / "ngrok.yml",
        home / "AppData" / "Local" / "ngrok" / "ngrok.yml",
        home / ".config" / "ngrok" / "ngrok.yml",
        home / ".ngrok2" / "ngrok.yml",
    ]
    paths.extend(base)
    seen = set()
    out = []
    for p in paths:
        try:
            key = os.path.normcase(str(p))
        except Exception:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _read_authtoken_from_file(path):
    """Read a top-level ``authtoken`` field from an ngrok config file safely.

    Uses YAML if available, falling back to a simple line regex. Never raises.
    """
    try:
        if not path:
            return None
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    try:
        import yaml
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            token = data.get("authtoken")
            if isinstance(token, str) and token.strip():
                return token.strip()
    except Exception:
        pass
    try:
        m = re.search(r"(?m)^\s*authtoken:\s*(\S+)", text)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return None


def _resolve_auth_token():
    """Resolve the ngrok authtoken. Returns (token, source).

    Sources, in priority order: NGROK_AUTHTOKEN env var, then a top-level
    authtoken in a standard ngrok user config file. Never reads app config.
    """
    token = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if token:
        return token, "environment"
    for path in _ngrok_config_candidates():
        token = _read_authtoken_from_file(path)
        if token:
            return token, "user_config"
    return "", "none"


def validate_auth_ready():
    """Check an ngrok authtoken is resolvable WITHOUT starting any process.

    Returns (ok, message). Does not call ngrok.connect or start a process.
    """
    token, source = _resolve_auth_token()
    if not token:
        return False, (
            "Ngrok chưa được xác thực. Cần cấu hình NGROK_AUTHTOKEN "
            "hoặc chạy 'ngrok config add-authtoken <token>'."
        )
    return True, f"Ngrok authtoken sẵn sàng (nguồn: {source})"


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def reserve_api_port():
    global _API_PORT
    with _API_PORT_LOCK:
        if _API_PORT == 0:
            _API_PORT = _find_free_port()
        return _API_PORT


def write_ownership(record):
    """Atomically persist the ownership record (temp + fsync + os.replace)."""
    _init_paths()
    with _OWNERSHIP_LOCK:
        tmp = OWNERSHIP_JSON.with_name(f"{OWNERSHIP_JSON.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, OWNERSHIP_JSON)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def read_ownership():
    _init_paths()
    try:
        with open(OWNERSHIP_JSON, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def clear_ownership():
    _init_paths()
    with _OWNERSHIP_LOCK:
        try:
            OWNERSHIP_JSON.unlink(missing_ok=True)
        except Exception:
            pass


def _agent_pyngrok_config():
    """Build a dedicated PyngrokConfig so pyngrok tracks this agent separately."""
    bin_path = ngrok_helper.get_ngrok_bin_path()
    return ngconf.PyngrokConfig(
        ngrok_path=bin_path,
        config_path=str(AGENT_CONFIG_YML),
        startup_timeout=30,
    )


def start_owned_agent(port, callback_instance_id, monitor_generation):
    """Start the dedicated ngrok agent with an owned tunnel.

    Returns (ok, payload) where payload is either an error message or a dict
    with keys: public_url, api_url, agent_pid, agent_create_time, agent_exe,
    config_path, tunnel_id, target_port, callback_instance_id,
    monitor_generation, owner_uuid.
    """
    _init_paths()
    bin_path = ngrok_helper.get_ngrok_bin_path()
    if not bin_path:
        ok, msg = ngrok_helper.ensure_ngrok()
        if ok:
            bin_path = ngrok_helper.get_ngrok_bin_path()
        if not bin_path:
            return False, f"Chưa có ngrok binary: {msg}"

    agent_api_port = reserve_api_port()
    record = {
        "owner_uuid": uuid.uuid4().hex,
        "app_pid": os.getpid(),
        "agent_pid": None,
        "agent_create_time": None,
        "agent_exe": str(bin_path),
        "config_path": str(AGENT_CONFIG_YML),
        "agent_api_url": f"http://127.0.0.1:{agent_api_port}",
        "tunnel_id": None,
        "public_url": None,
        "target_port": port,
        "callback_instance_id": callback_instance_id,
        "monitor_generation": monitor_generation,
    }
    try:
        write_ownership(record)
    except Exception as e:
        return False, f"Không ghi được ownership record: {e}"

    cfg = _agent_pyngrok_config()
    token, _auth_source = _resolve_auth_token()
    if not token:
        return False, (
            "Ngrok chưa được xác thực. Cần cấu hình NGROK_AUTHTOKEN "
            "hoặc chạy 'ngrok config add-authtoken <token>'."
        )
    try:
        ngrok.set_auth_token(token, pyngrok_config=cfg)
    except Exception as e:
        _log(f"[Ngrok] Không ghi authtoken vào config: {e}")
        return False, "Ngrok authtoken bị từ chối khi ghi config."
    try:
        tunnel = ngrok.connect(
            port,
            "http",
            pyngrok_config=cfg,
            name=f"youtube-monitor-{uuid.uuid4().hex[:8]}",
        )
        public_url = (tunnel.public_url or "").rstrip("/")
        if not public_url:
            return False, "ngrok không trả về public URL"
    except Exception as e:
        msg = str(e)
        if "4018" in msg or "not authenticated" in msg.lower() or "authentication failed" in msg.lower():
            return False, "Ngrok authtoken bị từ chối (ERR_NGROK_4018)."
        return False, f"ngrok.connect lỗi: {msg}"

    proc = _find_agent_process(cfg)
    record = read_ownership()
    record["tunnel_id"] = str(getattr(tunnel, "name", "") or "")
    record["public_url"] = public_url
    record["agent_api_url"] = f"http://127.0.0.1:{agent_api_port}"
    if proc is not None:
        record["agent_pid"] = proc.pid
        try:
            record["agent_create_time"] = proc.create_time()
        except Exception:
            pass
        try:
            record["agent_exe"] = str(proc.exe())
        except Exception:
            pass
    try:
        write_ownership(record)
    except Exception as e:
        return False, f"Không ghi được ownership record: {e}"

    return True, {
        "public_url": public_url,
        "api_url": record["agent_api_url"],
        "agent_pid": record.get("agent_pid"),
        "agent_create_time": record.get("agent_create_time"),
        "agent_exe": record.get("agent_exe"),
        "config_path": record.get("config_path"),
        "tunnel_id": record.get("tunnel_id"),
        "target_port": port,
        "callback_instance_id": callback_instance_id,
        "monitor_generation": monitor_generation,
        "owner_uuid": record.get("owner_uuid"),
    }


def _agent_api_url():
    return f"http://127.0.0.1:{_API_PORT}"


def _find_agent_process(cfg):
    """Locate the live ngrok agent process for this app's config."""
    try:
        ngrok_proc = ngrok.get_ngrok_process(pyngrok_config=cfg)
        if ngrok_proc is not None and ngrok_proc.proc is not None:
            return ngrok_proc.proc
    except Exception:
        pass
    return None


def _record_matches_process(record):
    """Verify a live process matches the ownership record exactly."""
    pid = record.get("agent_pid")
    if not pid:
        return False
    try:
        proc = psutil.Process(int(pid))
        if not proc.is_running():
            return False
        if record.get("agent_create_time"):
            try:
                if abs(proc.create_time() - float(record["agent_create_time"])) > 5:
                    return False
            except Exception:
                return False
        if record.get("agent_exe"):
            try:
                if os.path.normcase(str(proc.exe())) != os.path.normcase(str(record["agent_exe"])):
                    return False
            except Exception:
                return False
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def stop_owned_agent():
    """Stop only the ngrok agent recorded as owned by this app.

    Never kills external/unknown ngrok processes. Returns (ok, message)."""
    record = read_ownership()
    if not record:
        return True, "Không có ngrok agent do app quản lý"
    if not _record_matches_process(record):
        clear_ownership()
        return True, "Ngrok agent không còn thuộc sở hữu app; không kill."
    pid = int(record["agent_pid"])
    try:
        ngrok.disconnect(record.get("public_url") or "", pyngrok_config=_agent_pyngrok_config())
    except Exception:
        pass
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
    except psutil.NoSuchProcess:
        pass
    except Exception:
        return False, f"Không thể dừng ngrok agent PID {pid}"
    clear_ownership()
    return True, f"Đã dừng ngrok agent PID {pid} (thuộc sở hữu app)"


def owned_agent_alive():
    record = read_ownership()
    return _record_matches_process(record), record


def tunnel_public_url():
    record = read_ownership()
    if record and record.get("public_url"):
        return record["public_url"]
    return None


def agent_api_url():
    record = read_ownership()
    if record and record.get("agent_api_url"):
        return record["agent_api_url"]
    return _agent_api_url()