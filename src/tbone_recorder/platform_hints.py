"""Extra device metadata that PortAudio does not expose.

PortAudio only gives us a device name, and the t.bone SC 500 reports itself on
macOS as the gloriously unhelpful "Microphone".  CoreAudio does know the
manufacturer ("Thomann"), so on macOS we ask system_profiler and use that to
identify the mic reliably instead of guessing from the name.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

_CACHE: dict[str, str] = {}
_CACHE_AT = 0.0
_CACHE_TTL = 10.0
_LOCK = threading.Lock()


def _macos_manufacturers() -> dict[str, str]:
    """Map lowercased device name -> manufacturer, via system_profiler."""
    try:
        proc = subprocess.run(
            ["system_profiler", "SPAudioDataType", "-json"],
            capture_output=True,
            timeout=8,
            text=True,
        )
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return {}

    out: dict[str, str] = {}

    def walk(items):
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("_name")
            man = item.get("coreaudio_device_manufacturer")
            if isinstance(name, str) and isinstance(man, str):
                out.setdefault(name.strip().lower(), man.strip())
            for value in item.values():
                if isinstance(value, list):
                    walk(value)

    walk(data.get("SPAudioDataType", []))
    return out


def manufacturers(force: bool = False) -> dict[str, str]:
    """Device name (lowercased) -> manufacturer. Empty where unsupported."""
    global _CACHE, _CACHE_AT
    if sys.platform != "darwin":
        return {}
    with _LOCK:
        now = time.time()
        if force or not _CACHE or now - _CACHE_AT > _CACHE_TTL:
            _CACHE = _macos_manufacturers()
            _CACHE_AT = now
        return dict(_CACHE)


def manufacturer_for(name: str) -> str | None:
    return manufacturers().get((name or "").strip().lower())
