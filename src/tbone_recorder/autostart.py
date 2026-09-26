"""Running the watcher automatically at every login.

Neither OS has one command for "run this at login": macOS wants a launchd
property list in ~/Library/LaunchAgents, Windows wants a Scheduled Task.
Both are pointed at `sys.executable -m tbone_recorder --watch` -- the exact
interpreter this package is installed into -- rather than the `tbone-rec`
console script, since launchd and Task Scheduler do not source a shell
profile and cannot be relied on to see the same PATH a terminal would.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import config as cfg_mod
from .system_audio import SetDefaultResult

_LABEL = "com.tbone-recorder.watcher"
_TASK_NAME = "tbone-recorder watcher"


def is_supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("win")


def install() -> SetDefaultResult:
    try:
        if sys.platform == "darwin":
            return _macos_install()
        if sys.platform.startswith("win"):
            return _windows_install()
    except Exception as exc:  # pragma: no cover - defensive, platform APIs vary
        return SetDefaultResult(False, f"Could not install the login item: {exc}")
    return SetDefaultResult(False, "Auto-start isn't supported on this platform.")


def uninstall() -> SetDefaultResult:
    try:
        if sys.platform == "darwin":
            return _macos_uninstall()
        if sys.platform.startswith("win"):
            return _windows_uninstall()
    except Exception as exc:  # pragma: no cover - defensive, platform APIs vary
        return SetDefaultResult(False, f"Could not remove the login item: {exc}")
    return SetDefaultResult(False, "Auto-start isn't supported on this platform.")


# --------------------------------------------------------------------- macOS

def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def _macos_install() -> SetDefaultResult:
    log_file = cfg_mod.config_dir() / "watcher.log"
    plist_path = _plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>tbone_recorder</string>
        <string>--watch</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ProcessType</key><string>Background</string>
    <key>StandardOutPath</key><string>{log_file}</string>
    <key>StandardErrorPath</key><string>{log_file}</string>
</dict>
</plist>
"""
    plist_path.write_text(plist, "utf-8")

    # Unload first in case an older copy is already loaded; errors here just
    # mean it wasn't loaded yet, which is fine.
    subprocess.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True)
    proc = subprocess.run(
        ["launchctl", "load", "-w", str(plist_path)], capture_output=True, text=True
    )
    if proc.returncode != 0:
        return SetDefaultResult(
            False, f"Wrote {plist_path} but launchctl load failed: {proc.stderr.strip()}"
        )
    return SetDefaultResult(
        True, f"Installed and started. Runs at every login from now on (log: {log_file})."
    )


def _macos_uninstall() -> SetDefaultResult:
    plist_path = _plist_path()
    subprocess.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True)
    existed = plist_path.exists()
    if existed:
        plist_path.unlink()
    return SetDefaultResult(True, "Removed." if existed else "Wasn't installed; nothing to remove.")


# ------------------------------------------------------------------- Windows

def _windows_pythonw() -> str:
    """Prefer pythonw.exe alongside the running interpreter: no console
    window flashes at login. Falls back to the interpreter actually running
    this process if there is no windowed twin next to it."""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else sys.executable


def _windows_install() -> SetDefaultResult:
    python = _windows_pythonw()
    proc = subprocess.run(
        [
            "schtasks", "/Create", "/SC", "ONLOGON",
            "/TN", _TASK_NAME,
            "/TR", f'"{python}" -m tbone_recorder --watch',
            "/RL", "LIMITED",
            "/F",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return SetDefaultResult(
            False, f"schtasks failed: {(proc.stderr or proc.stdout).strip()}"
        )
    subprocess.run(["schtasks", "/Run", "/TN", _TASK_NAME], capture_output=True)
    log_file = cfg_mod.config_dir() / "watcher.log"
    return SetDefaultResult(
        True, f"Installed and started. Runs at every login from now on (log: {log_file})."
    )


def _windows_uninstall() -> SetDefaultResult:
    proc = subprocess.run(
        ["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").lower()
        if "cannot find" in stderr or "does not exist" in stderr:
            return SetDefaultResult(True, "Wasn't installed; nothing to remove.")
        return SetDefaultResult(False, f"schtasks failed: {proc.stderr.strip()}")
    return SetDefaultResult(True, "Removed.")
