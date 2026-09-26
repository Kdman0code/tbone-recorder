"""Persisted settings.

Device *indices* are not stable -- PortAudio renumbers when anything is
plugged or unplugged -- so the remembered device is stored by name and
resolved back to an index at startup.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "tbone-recorder"

DEFAULTS = {
    "device_name": None,
    "device_host_api": None,
    "samplerate": 48000,
    "channels": 1,
    "subtype": "PCM_24",
    "mono_source": "left",
    "outdir": None,
}


def config_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def default_outdir() -> Path:
    music = Path.home() / "Music"
    base = music if music.is_dir() else Path.home()
    return base / "tbone-recordings"


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        raw = json.loads(config_path().read_text("utf-8"))
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception:
        # A corrupt config must never stop the app from starting.
        pass
    if not cfg.get("outdir"):
        cfg["outdir"] = str(default_outdir())
    return cfg


def save(cfg: dict) -> None:
    try:
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        keep = {k: v for k, v in cfg.items() if k in DEFAULTS}
        tmp = d / "config.json.tmp"
        tmp.write_text(json.dumps(keep, indent=2), "utf-8")
        tmp.replace(config_path())
    except Exception:
        pass


def resolve_device(cfg: dict, devices: list[dict]) -> int | None:
    """Map the remembered device name back onto a current device index."""
    name = (cfg.get("device_name") or "").lower()
    if not name:
        return None
    api = cfg.get("device_host_api")
    for dev in devices:
        if dev["name"].lower() == name and dev["host_api"] == api:
            return dev["index"]
    for dev in devices:
        if dev["name"].lower() == name:
            return dev["index"]
    return None
