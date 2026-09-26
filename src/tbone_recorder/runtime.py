"""Where a running server publishes how to reach it.

The interface picks a free port and a fresh token on every run, so anything
that wants to talk to a server it did not start itself -- the macOS menu-bar
indicator, chiefly -- has no way to guess either.  A running server therefore
drops a small file next to the config saying where it is, and removes it on the
way out.

The token is part of that, which means it is on disk rather than only in the
terminal that printed it.  The file is written 0600 in the user's own
Application Support directory, so it is no more reachable than the recordings
themselves; anything already running as this user could drive the microphone
directly in any case.  It is deliberately *not* put anywhere world-readable,
and it is removed when the server exits.

Only one server can be the published one: a second instance overwrites the
file, and the older server keeps working but stops being discoverable.  That
matches how the interface is used -- one at a time.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import config as cfg_mod


def state_path() -> Path:
    return cfg_mod.config_dir() / "runtime.json"


def publish(url: str, port: int, token: str, outdir: str | os.PathLike) -> None:
    """Record this process as the reachable server.  Never raises."""
    payload = {
        "pid": os.getpid(),
        "url": url,
        "port": port,
        "token": token,
        "outdir": str(outdir),
        "started": time.time(),
    }
    try:
        d = cfg_mod.config_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "runtime.json.tmp"
        # 0600 before anything is written, so the token is never briefly readable.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(state_path())
    except Exception:
        # Discoverability is a convenience; it must never stop the server.
        pass


def read() -> dict | None:
    """The published server, or None if there isn't a live one."""
    try:
        data = json.loads(state_path().read_text("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("url") or not data.get("token"):
        return None
    if not pid_alive(int(data.get("pid") or 0)):
        return None
    return data


def clear() -> None:
    """Remove our own entry.  A newer server's entry is left alone."""
    try:
        data = json.loads(state_path().read_text("utf-8"))
        if int(data.get("pid") or 0) != os.getpid():
            return
        state_path().unlink()
    except Exception:
        pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # No cheap probe here, and the file is rewritten by the next server
        # anyway; a stale entry on Windows just fails the HTTP check instead.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # alive, owned by someone else
    return True
