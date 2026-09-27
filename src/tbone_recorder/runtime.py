"""Where a running server publishes how to reach it.

The interface picks a free port and a fresh token on every run, so anything
that wants to talk to a server it did not start itself has no way to guess
either.  A running server therefore drops a small file next to the config
saying where it is, and removes it on the way out.

The chief reader is the next launch.  Starting the app twice used to mean a
second server fighting the first for the microphone -- or, from the macOS app
bundle, no second launch at all: LaunchServices treats the bundle as already
running and tries to activate an app that has no event loop to activate,
which Finder reports as "the application is not open anymore".  Finding the
first server and opening a tab onto it is the whole point of this file.

The token is part of that, which means it is on disk rather than only in the
terminal that printed it.  The file is written 0600 in the user's own
Application Support directory, so it is no more reachable than the recordings
themselves; anything already running as this user could drive the microphone
directly in any case.  It is deliberately *not* put anywhere world-readable,
and it is removed when the server exits.

Only one server can be the published one: a second instance (started with
--new-instance, or racing the first before it published) overwrites the file,
and the older server keeps working but stops being discoverable.  That matches
how the interface is used -- one at a time.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
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


def _load() -> dict | None:
    """Whatever the file says, without judging whether it is still true."""
    try:
        data = json.loads(state_path().read_text("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("url") or not data.get("token"):
        return None
    return data


def read() -> dict | None:
    """The published server, or None if there isn't a live one."""
    data = _load()
    if data is None or not pid_alive(int(data.get("pid") or 0)):
        return None
    return data


def live_server(timeout: float = 1.5) -> dict | None:
    """The published server, proved to be answering, or None.

    A live pid is not enough.  On Windows ``pid_alive`` cannot check at all,
    pids get recycled, and a server killed outright never reaches ``clear``,
    so the entry routinely outlives the server that wrote it.  Asking the
    server itself is the only real test.

    An entry nothing answers is deleted here.  That matters more than it
    looks: leaving it would send every later launch to a server that is not
    there, so one crash would break starting the app until someone found the
    file and removed it by hand.
    """
    data = _load()
    if data is None:
        return None
    if pid_alive(int(data.get("pid") or 0)) and _responds(data, timeout):
        return data
    _discard(data)
    return None


def _responds(data: dict, timeout: float) -> bool:
    port = int(data.get("port") or 0)
    if port <= 0:
        return False
    # Addressed by port rather than by the stored URL: the server refuses any
    # Host header that isn't loopback, and this is the one urllib will send.
    token = urllib.parse.quote(str(data.get("token") or ""))
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/status?t={token}", timeout=timeout
        ):
            return True
    except urllib.error.HTTPError:
        # Being turned away is still an answer -- a server is listening, it
        # just no longer likes this token.  Don't delete a running server.
        return True
    except Exception:
        return False


def _discard(expected: dict) -> None:
    """Drop an entry just shown to be dead, unless it changed under us."""
    try:
        current = _load()
        if current is None:
            return
        if (current.get("pid"), current.get("port")) != (
            expected.get("pid"),
            expected.get("port"),
        ):
            return  # a newer server published while we were probing it
        state_path().unlink()
    except Exception:
        pass


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
