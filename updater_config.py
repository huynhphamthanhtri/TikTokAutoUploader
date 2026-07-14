import json
import os
from pathlib import Path


def _config_dir():
    if getattr(__import__("sys"), "frozen", False):
        import sys
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_PATH = _config_dir() / "updater_config.json"

DEFAULTS = {
    "repo_owner": "",
    "repo_name": "",
    "github_token": "",
    "auto_check": True,
    "last_check_epoch": 0,
    "skip_version": "",
}


def load_updater_config():
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
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def is_configured(cfg):
    return bool(cfg.get("repo_owner", "").strip() and cfg.get("repo_name", "").strip())


def mask_token(token):
    t = str(token or "").strip()
    if len(t) <= 8:
        return t
    return t[:4] + "****" + t[-4:]
