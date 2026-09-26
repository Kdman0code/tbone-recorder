"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import config as cfg_mod
from . import devices as devices_mod
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
    args = parser.parse_args(argv)

    if args.list_devices:
        return cmd_list_devices()

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
        if engine.recording:
            saved = engine.stop_recording()
            if saved:
                print(f"Saved {saved}")
        engine.close()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
