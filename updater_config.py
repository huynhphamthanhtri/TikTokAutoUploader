import json
import os
import threading
from pathlib import Path


def _config_dir():
    if getattr(__import__("sys"), "frozen", False):
        import sys
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_PATH = _config_dir() / "updater_config.json"
_CONFIG_LOCK = threading.RLock()

DEFAULTS = {
    "repo_owner": "",
    "repo_name": "",
    "github_token": "",
    "auto_check": True,
    "last_check_epoch": 0,
    "skip_version": "",
    "remind_after_epoch": 0,
}


def load_updater_config():
    with _CONFIG_LOCK:
        cfg = dict(DEFAULTS)
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
            except Exception:
                pass
        return cfg


def save_updater_config(cfg):
    merged = dict(DEFAULTS)
    merged.update(cfg or {})
    with _CONFIG_LOCK:
        temp_path = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, CONFIG_PATH)
            return True
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return False


def update_updater_config(**changes):
    with _CONFIG_LOCK:
        config = load_updater_config()
        config.update(changes)
        if not save_updater_config(config):
            raise OSError(f"Không thể lưu cấu hình cập nhật: {CONFIG_PATH}")
        return config


def is_configured(cfg):
    return bool(cfg.get("repo_owner", "").strip() and cfg.get("repo_name", "").strip())


def mask_token(token):
    t = str(token or "").strip()
    if len(t) <= 8:
        return t
    return t[:4] + "****" + t[-4:]
