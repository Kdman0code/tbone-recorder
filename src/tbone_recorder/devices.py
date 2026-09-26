"""Input-device discovery.

PortAudio reports one entry per (device, host API) pair, so on Windows the same
physical microphone shows up three or four times (MME, DirectSound, WASAPI,
WDM-KS).  Listing those verbatim makes the picker look broken, so we rank host
APIs and collapse duplicates down to the best one per device name.

The t.bone SC 500 is class-compliant and does not report its brand through
PortAudio -- on macOS it is called plain "Microphone".  Never match on
"t.bone"; use the candidate substrings below and always fall back to letting
the user choose.
"""

from __future__ import annotations

import sys

import sounddevice as sd

from . import platform_hints

# Ordered best-first.  WASAPI gives low latency and correct device names on
# Windows; MME truncates names to 31 characters and adds latency.
_HOST_API_RANK = {
    "Windows WASAPI": 0,
    "Windows WDM-KS": 1,
    "Windows DirectSound": 2,
    "MME": 3,
    "Core Audio": 0,
    "ALSA": 0,
    "JACK Audio Connection Kit": 1,
    "OSS": 4,
}

# Substrings that suggest a t.bone / Thomann USB mic, best guess first.  The
# generic USB entries are last so a real match always wins.
_CANDIDATES = (
    "t.bone",
    "tbone",
    "thomann",
    "sc 500",
    "sc500",
    "sc 420",
    "sc420",
    "sc 450",
    "usb microphone",
    "usb audio",
    "usb-audio",
)


def _host_api_rank(name: str) -> int:
    return _HOST_API_RANK.get(name, 9)


# Manufacturer strings that mean "this really is the t.bone".  Thomann is the
# house brand owner, so this is authoritative where the OS reports it.
_BRAND_MAKERS = ("thomann", "t.bone", "tbone")


def _match_score(name: str, manufacturer: str | None = None) -> int:
    """Lower is a better t.bone candidate; 999 means no match at all.

    A manufacturer match outranks every name heuristic: on macOS the mic is
    called plain "Microphone" and would otherwise score no better than the
    built-in one.
    """
    if manufacturer:
        low_man = manufacturer.lower()
        if any(brand in low_man for brand in _BRAND_MAKERS):
            return -1
    low = name.lower()
    for i, needle in enumerate(_CANDIDATES):
        if needle in low:
            return i
    return 999


def list_input_devices() -> list[dict]:
    """Return selectable input devices, duplicates across host APIs collapsed."""
    try:
        devices = sd.query_devices()
        host_apis = sd.query_hostapis()
    except Exception as exc:  # PortAudio failed to initialise at all
        raise RuntimeError(f"Could not query audio devices: {exc}") from exc

    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None

    makers = platform_hints.manufacturers()

    found: list[dict] = []
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) < 1:
            continue
        api_name = host_apis[dev["hostapi"]]["name"]
        name = (dev["name"] or f"Device {idx}").strip()
        manufacturer = makers.get(name.lower())
        found.append(
            {
                "index": idx,
                "name": name,
                "manufacturer": manufacturer,
                "host_api": api_name,
                "channels": int(dev["max_input_channels"]),
                "default_samplerate": int(dev.get("default_samplerate") or 48000),
                "is_default": idx == default_in,
                "_api_rank": _host_api_rank(api_name),
                "_match": _match_score(name, manufacturer),
            }
        )

    # Collapse per-host-API duplicates of the same physical device, keeping the
    # best-ranked host API.  Only meaningful on Windows, harmless elsewhere.
    best: dict[str, dict] = {}
    for dev in found:
        key = dev["name"].lower()
        current = best.get(key)
        if current is None or dev["_api_rank"] < current["_api_rank"]:
            if current is not None:
                dev["also_on"] = current.get("also_on", []) + [current["host_api"]]
            best[key] = dev
        else:
            current.setdefault("also_on", []).append(dev["host_api"])

    result = sorted(best.values(), key=lambda d: (d["_match"], d["_api_rank"], d["index"]))
    for dev in result:
        dev["likely_tbone"] = dev["_match"] < 999
        dev.pop("_api_rank", None)
        dev.pop("_match", None)
    return result


def pick_default_device(devices: list[dict] | None = None) -> int | None:
    """Best guess at the t.bone, else the system default input, else first."""
    devices = devices if devices is not None else list_input_devices()
    if not devices:
        return None
    for dev in devices:
        if dev["likely_tbone"]:
            return dev["index"]
    for dev in devices:
        if dev["is_default"]:
            return dev["index"]
    return devices[0]["index"]


def describe_device(index: int) -> dict | None:
    for dev in list_input_devices():
        if dev["index"] == index:
            return dev
    return None


def supported_samplerates(index: int, channels: int = 1) -> list[int]:
    """Sample rates the device actually accepts, probed rather than assumed.

    C-Media based mics often cap out well below what the UI would like to
    offer, so ask PortAudio instead of hardcoding 48 kHz.
    """
    rates = []
    for rate in (44100, 48000, 88200, 96000, 192000):
        try:
            sd.check_input_settings(device=index, channels=channels, samplerate=rate)
            rates.append(rate)
        except Exception:
            continue
    return rates or [48000]


def permission_hint() -> str | None:
    """Platform-specific reason a stream might deliver pure silence."""
    if sys.platform == "darwin":
        return (
            "macOS blocks microphone access per application. If the meter stays "
            "flat, open System Settings > Privacy & Security > Microphone and "
            "enable access for the app you launched this from (Terminal, iTerm, "
            "or your IDE), then restart it."
        )
    if sys.platform.startswith("win"):
        return (
            "If the meter stays flat, open Settings > Privacy & security > "
            "Microphone and make sure microphone access is on for desktop apps."
        )
    return None
