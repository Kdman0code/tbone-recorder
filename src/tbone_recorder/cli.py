"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import autostart
from . import config as cfg_mod
from . import devices as devices_mod
from . import runtime
from . import system_audio
from . import watcher as watcher_mod
from .engine import EngineError, RecorderEngine
from .server import serve

__version__ = "1.0.0"


def _fmt_db(value: float) -> str:
    return "  -inf" if value <= -119.5 else f"{value:6.1f}"


def cmd_list_devices() -> int:
    devs = devices_mod.list_input_devices()
    if not devs:
        print("No audio input devices found.")
        return 1
    print(f"{'idx':>4}  {'ch':>2}  {'default Hz':>10}  host API        name")
    print("-" * 72)
    for dev in devs:
        marks = []
        if dev.get("manufacturer"):
            marks.append(dev["manufacturer"])
        if dev["likely_tbone"]:
            marks.append("t.bone match")
        if dev["is_default"]:
            marks.append("system default")
        suffix = f"   [{', '.join(marks)}]" if marks else ""
        print(
            f"{dev['index']:>4}  {dev['channels']:>2}  {dev['default_samplerate']:>10}  "
            f"{dev['host_api']:<15} {dev['name']}{suffix}"
        )
        if dev.get("also_on"):
            print(f"{'':>4}  {'':>2}  {'':>10}  also via: {', '.join(sorted(set(dev['also_on'])))}")
    return 0


def cmd_check(device: int | None, samplerate: int, seconds: float = 3.0) -> int:
    """Open the device, measure levels, and say plainly whether audio arrives."""
    devs = devices_mod.list_input_devices()
    if device is None:
        device = devices_mod.pick_default_device(devs)
    if device is None:
        print("No audio input devices found.")
        return 1
    info = devices_mod.describe_device(device)
    print(f"Device {device}: {info['name'] if info else '?'} "
          f"({info['host_api'] if info else '?'})")

    engine = RecorderEngine(Path.home())
    try:
        engine.open(device, samplerate)
    except EngineError as exc:
        print(f"FAILED to open: {exc}")
        return 1

    print(f"Listening for {seconds:.0f}s -- make some noise...")
    deadline = time.time() + seconds
    worst = -999.0
    while time.time() < deadline:
        time.sleep(0.25)
        for ch, lvl in enumerate(engine.status()["levels"]):
            worst = max(worst, lvl["peak"])
        bars = " | ".join(
            f"ch{ch}: {_fmt_db(l['rms'])} dB rms {_fmt_db(l['peak'])} dB peak"
            for ch, l in enumerate(engine.status()["levels"])
        )
        print(f"\r{bars}   ", end="", flush=True)
    print()
    st = engine.status()
    engine.close()

    if worst <= -119.5:
        print("\nNo signal at all -- every sample was exactly zero.")
        hint = devices_mod.permission_hint()
        if hint:
            print(f"\n{hint}")
        return 2
    print(f"\nSignal present (loudest peak {worst:.1f} dBFS).")
    if st["clipped"]:
        print("Warning: the input clipped. Turn the mic gain down.")
    if worst < -40:
        print("Level is very low -- turn the gain up, or move closer to the mic.")
    return 0


def cmd_hand_off(existing: dict, args: argparse.Namespace) -> int:
    """Point at the server that is already running instead of starting one."""
    # Rebuilt from the port rather than read from the file: loopback is the
    # only Host the server will accept, so this address always works.
    url = f"http://127.0.0.1:{existing['port']}/?t={existing['token']}"
    print(f"t.bone recorder is already running (pid {existing['pid']}).")
    print(f"  interface  {url}")
    if existing.get("outdir"):
        print(f"  saving to  {existing['outdir']}")
    ignored = [
        flag
        for flag, value in (
            ("--device", args.device),
            ("--samplerate", args.samplerate),
            ("--outdir", args.outdir),
            ("--port", args.port),
        )
        if value
    ]
    if ignored:
        print(f"  note       {', '.join(ignored)} ignored by the running server;")
        print("             quit it first, or pass --new-instance.")
    if not args.no_browser:
        webbrowser.open(url)
    return 0


def cmd_watch() -> int:
    """Foreground loop: make the t.bone the OS default whenever it connects.

    This is what the login item (see --install-watcher) runs in the
    background; running it directly is how to try it out first.
    """
    if not system_audio.is_supported():
        print("Setting the OS default input isn't supported on this platform.")
        return 1
    print(f"Watching for the t.bone... (log: {watcher_mod.log_path()})")
    print("Press Ctrl+C to stop.\n")
    try:
        watcher_mod.run()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_install_watcher() -> int:
    result = autostart.install()
    print(result.message)
    return 0 if result.ok else 1


def cmd_uninstall_watcher() -> int:
    result = autostart.uninstall()
    print(result.message)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tbone-rec",
        description="Recording interface for the t.bone USB microphone.",
    )
    parser.add_argument("--version", action="version", version=f"tbone-recorder {__version__}")
    parser.add_argument("--list-devices", action="store_true", help="show input devices and exit")
    parser.add_argument(
        "--check",
        action="store_true",
        help="record briefly and report whether any audio arrives, then exit",
    )
    parser.add_argument(
        "--device",
        help="device index, or part of its name (default: remembered, else auto-detected)",
    )
    parser.add_argument("--samplerate", type=int, help="sample rate in Hz (default 48000)")
    parser.add_argument("--outdir", help="where to save recordings")
    parser.add_argument("--port", type=int, default=0, help="HTTP port (default: pick a free one)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument(
        "--new-instance",
        action="store_true",
        help="start a second server even if one is already running",
    )
    parser.add_argument(
        "--no-set-default",
        action="store_true",
        help="don't make the t.bone the OS default microphone when it's the one in use",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="run in the foreground, making the t.bone the OS default mic whenever it connects",
    )
    parser.add_argument(
        "--install-watcher",
        action="store_true",
        help="run --watch automatically at every login (macOS launchd / Windows Scheduled Task)",
    )
    parser.add_argument(
        "--uninstall-watcher",
        action="store_true",
        help="remove the login item installed by --install-watcher",
    )
    args = parser.parse_args(argv)

    if args.list_devices:
        return cmd_list_devices()
    if args.install_watcher:
        return cmd_install_watcher()
    if args.uninstall_watcher:
        return cmd_uninstall_watcher()
    if args.watch:
        return cmd_watch()

    # Launching twice -- double-clicking the icon again, usually -- should
    # show you the server you already have rather than starting a second one
    # to fight it for the microphone.  --check is exempt: it is a diagnostic
    # and has to open the device itself to mean anything.
    if not args.new_instance and not args.check:
        existing = runtime.live_server()
        if existing:
            return cmd_hand_off(existing, args)

    cfg = cfg_mod.load()
    if args.outdir:
        cfg["outdir"] = str(Path(args.outdir).expanduser())
    samplerate = args.samplerate or int(cfg.get("samplerate") or 48000)

    devs = devices_mod.list_input_devices()
    device: int | None = None
    if args.device:
        if args.device.isdigit():
            device = int(args.device)
        else:
            needle = args.device.lower()
            for dev in devs:
                if needle in dev["name"].lower():
                    device = dev["index"]
                    break
            if device is None:
                print(f"No input device matching {args.device!r}. Try --list-devices.")
                return 1
    if device is None:
        device = cfg_mod.resolve_device(cfg, devs)
    if device is None:
        device = devices_mod.pick_default_device(devs)

    if args.check:
        return cmd_check(device, samplerate)

    outdir = Path(cfg["outdir"]).expanduser()
    try:
        engine = RecorderEngine(outdir)
    except Exception:
        # A remembered folder can be gone by the next run -- an unplugged
        # external drive, a deleted directory -- so fall back rather than
        # refusing to start.
        fallback = cfg_mod.default_outdir()
        print(f"Cannot write to {outdir}; using {fallback} instead.")
        outdir = fallback
        cfg["outdir"] = str(fallback)
        cfg_mod.save(cfg)
        engine = RecorderEngine(outdir)

    opened_error = None
    if device is not None:
        try:
            engine.open(device, samplerate)
        except EngineError as exc:
            opened_error = str(exc)

    httpd, token = serve(engine, cfg, port=args.port)
    host, port = httpd.server_address[0], httpd.server_address[1]
    url = f"http://{host}:{port}/?t={token}"
    runtime.publish(url, port, token, outdir)

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    info = devices_mod.describe_device(device) if device is not None else None
    print(f"t.bone recorder {__version__}")
    if info:
        state = "listening" if engine.is_open else "not opened"
        print(f"  input      {info['name']} ({info['host_api']}) -- {state}")
    else:
        print("  input      none detected -- pick one in the browser")
    if opened_error:
        print(f"  warning    {opened_error}")

    if info and info["likely_tbone"] and not args.no_set_default and system_audio.is_supported():
        result = system_audio.set_default_input(info["name"], info.get("manufacturer"))
        print(f"  OS default {result.message}")
    print(f"  saving to  {outdir}")
    print(f"  interface  {url}")
    print("\nPress Ctrl+C to stop.\n", flush=True)

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        # First, so a launch racing this shutdown stops being pointed at a
        # server that is on its way out.  A kill skips this entirely, which
        # is why live_server re-checks over HTTP instead of trusting the file.
        runtime.clear()
        if engine.recording:
            saved = engine.stop_recording()
            if saved:
                print(f"Saved {saved}")
        engine.close()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
