"""Background loop: notice the t.bone connecting and make it the OS default.

This only enumerates devices -- it never opens a PortAudio stream -- so it
can run continuously, independently of (and alongside) the recording
interface, without holding the mic open or fighting another app for it.

It acts on the *connect* transition only, not on every poll: once the
t.bone shows up, it becomes the OS default input, and if you point your OS
elsewhere afterwards, the watcher does not fight you. It only reasserts on
the next unplug/replug (or sleep/wake, or reboot), the same way a USB
headset behaves.

Meant to be run by `autostart.py`'s login item, but `tbone-rec --watch` runs
it in the foreground too, which is how to try it before installing it.
"""

from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path

from . import config as cfg_mod
from . import devices as devices_mod
from . import system_audio

DEFAULT_POLL_SECONDS = 3.0


def _tick(was_present: bool, devs: list[dict]) -> tuple[bool, str | None]:
    """One decision step, kept free of I/O so it's easy to test.

    Returns (is the t.bone present now, a message to log if something
    happened).
    """
    tbone = next((d for d in devs if d["likely_tbone"]), None)
    now_present = tbone is not None
    if now_present and not was_present:
        result = system_audio.set_default_input(tbone["name"], tbone.get("manufacturer"))
        return now_present, result.message
    return now_present, None


def log_path() -> Path:
    return cfg_mod.config_dir() / "watcher.log"


def _file_logger():
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {message}"
        print(line, flush=True)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass  # Logging is a convenience; it must never stop the watcher.

    return log


def run(poll_seconds: float = DEFAULT_POLL_SECONDS, log=None) -> None:
    log = log or _file_logger()
    log(f"watcher started (checking every {poll_seconds:.0f}s)")
    was_present = False
    while True:
        try:
            was_present, message = _tick(was_present, devices_mod.list_input_devices())
            if message:
                log(message)
        except Exception as exc:  # A bad poll must never kill the watcher.
            log(f"watcher error (continuing): {exc}")
        time.sleep(poll_seconds)
