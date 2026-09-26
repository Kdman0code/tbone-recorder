"""Making the mic the operating system's default input device.

Recording *into this app* only ever needed a PortAudio device index -- see
`devices.py`. This module does something different: it tells the OS itself
"use this microphone everywhere" (video calls, voice memos, any other app),
the way plugging in a USB headset usually does automatically. The t.bone is
class-compliant but that only makes it *available*; nothing promotes it to
default.

Neither OS exposes this over a stable, documented API, and neither platform
ships a dependency-free CLI for it:

- macOS: there is no `SwitchAudioSource`-style built-in, so we talk to
  CoreAudio's HAL directly with `ctypes` against the system framework --
  no extra install, matching how the rest of this project avoids drivers.
  `kAudioHardwarePropertyDefaultInputDevice` on the system object is the
  same property System Settings > Sound changes; setting it changes what
  every other app sees as the default input, immediately.
- Windows has no public API for this at all. Every "set default audio
  device" tool -- including the Windows volume mixer itself -- goes through
  `IPolicyConfig::SetDefaultEndpoint`, an interface Microsoft ships but has
  never documented. We use `pycaw`'s binding for it rather than hand-rolling
  the COM vtable, since a wrong method order there fails silently or worse.

Both sides are best-effort: never raise into the caller, and always say in
plain words what happened so the recorder still works if the OS refuses.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass
class SetDefaultResult:
    ok: bool
    message: str


def is_supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("win")


def set_default_input(name: str, manufacturer: str | None = None) -> SetDefaultResult:
    """Make the input device matching `name` (and `manufacturer`, if given)
    the operating system's default microphone.

    Matching mirrors `devices.py`: manufacturer first where the OS reports
    one (macOS calls the mic plain "Microphone"), name otherwise. Never
    raises -- this is a convenience on top of recording, not a requirement
    for it.
    """
    try:
        if sys.platform == "darwin":
            return _darwin_set_default_input(name, manufacturer)
        if sys.platform.startswith("win"):
            return _windows_set_default_input(name, manufacturer)
    except Exception as exc:  # pragma: no cover - defensive, platform APIs vary
        return SetDefaultResult(False, f"Could not set the OS default input: {exc}")
    return SetDefaultResult(False, "Setting the OS default input isn't supported on this platform.")


# --------------------------------------------------------------------- macOS

def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


def _darwin_set_default_input(name: str, manufacturer: str | None) -> SetDefaultResult:
    import ctypes

    core = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    core.AudioObjectGetPropertyData.restype = ctypes.c_int32
    core.AudioObjectSetPropertyData.restype = ctypes.c_int32
    core.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetLength.restype = ctypes.c_long

    class _AOPA(ctypes.Structure):
        _fields_ = [
            ("mSelector", ctypes.c_uint32),
            ("mScope", ctypes.c_uint32),
            ("mElement", ctypes.c_uint32),
        ]

    SYSTEM_OBJECT = 1
    SCOPE_GLOBAL = _fourcc("glob")
    ELEMENT_MAIN = 0
    PROP_DEVICES = _fourcc("dev#")
    PROP_DEFAULT_INPUT = _fourcc("dIn ")
    PROP_NAME = _fourcc("lnam")
    PROP_MANUFACTURER = _fourcc("lmak")
    kCFStringEncodingUTF8 = 0x08000100

    def cfstring_to_str(ptr) -> str | None:
        if not ptr:
            return None
        try:
            length = cf.CFStringGetLength(ptr)
            buf = ctypes.create_string_buffer(length * 4 + 1)
            ok = cf.CFStringGetCString(ptr, buf, len(buf), kCFStringEncodingUTF8)
            return buf.value.decode("utf-8", "replace") if ok else None
        finally:
            cf.CFRelease(ptr)

    def get_cfstring_prop(device_id: int, selector: int) -> str | None:
        addr = _AOPA(selector, SCOPE_GLOBAL, ELEMENT_MAIN)
        ptr = ctypes.c_void_p()
        size = ctypes.c_uint32(ctypes.sizeof(ptr))
        status = core.AudioObjectGetPropertyData(
            device_id, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(ptr)
        )
        return cfstring_to_str(ptr.value and ptr) if status == 0 else None

    def list_device_ids() -> list[int]:
        addr = _AOPA(PROP_DEVICES, SCOPE_GLOBAL, ELEMENT_MAIN)
        size = ctypes.c_uint32(0)
        status = core.AudioObjectGetPropertyDataSize(
            SYSTEM_OBJECT, ctypes.byref(addr), 0, None, ctypes.byref(size)
        )
        if status != 0 or size.value == 0:
            return []
        count = size.value // ctypes.sizeof(ctypes.c_uint32)
        arr = (ctypes.c_uint32 * count)()
        status = core.AudioObjectGetPropertyData(
            SYSTEM_OBJECT, ctypes.byref(addr), 0, None, ctypes.byref(size), arr
        )
        return list(arr) if status == 0 else []

    def get_default_input() -> int:
        addr = _AOPA(PROP_DEFAULT_INPUT, SCOPE_GLOBAL, ELEMENT_MAIN)
        device_id = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(device_id))
        core.AudioObjectGetPropertyData(
            SYSTEM_OBJECT, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(device_id)
        )
        return device_id.value

    def set_default_input(device_id: int) -> int:
        addr = _AOPA(PROP_DEFAULT_INPUT, SCOPE_GLOBAL, ELEMENT_MAIN)
        val = ctypes.c_uint32(device_id)
        return core.AudioObjectSetPropertyData(
            SYSTEM_OBJECT, ctypes.byref(addr), 0, None, ctypes.sizeof(val), ctypes.byref(val)
        )

    needle_name = name.strip().lower()
    needle_man = (manufacturer or "").strip().lower()

    match_id = None
    for device_id in list_device_ids():
        dev_name = (get_cfstring_prop(device_id, PROP_NAME) or "").strip().lower()
        if dev_name != needle_name:
            continue
        if needle_man:
            dev_man = (get_cfstring_prop(device_id, PROP_MANUFACTURER) or "").strip().lower()
            if dev_man != needle_man:
                continue
        match_id = device_id
        break

    if match_id is None:
        return SetDefaultResult(False, f"Couldn't find {name!r} in CoreAudio's device list.")

    previous_id = get_default_input()
    if previous_id == match_id:
        return SetDefaultResult(True, f"{name!r} is already the macOS default input.")

    status = set_default_input(match_id)
    if status != 0:
        return SetDefaultResult(False, f"macOS refused to switch the default input (status {status}).")

    previous_name = get_cfstring_prop(previous_id, PROP_NAME) or "the previous device"
    return SetDefaultResult(True, f"macOS default input set to {name!r} (was {previous_name!r}).")


# ------------------------------------------------------------------- Windows

def _windows_set_default_input(name: str, manufacturer: str | None) -> SetDefaultResult:
    from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
    from pycaw.utils import AudioUtilities

    devices = AudioUtilities.GetAllDevices(
        data_flow=EDataFlow.eCapture.value, device_state=DEVICE_STATE.ACTIVE.value
    )

    needle = name.strip().lower()
    target = None
    for dev in devices:
        friendly = (dev.FriendlyName or "").strip().lower()
        if friendly == needle or needle in friendly or friendly in needle:
            target = dev
            break

    if target is None:
        return SetDefaultResult(False, f"Couldn't find an active capture device named like {name!r}.")

    previous = AudioUtilities.CreateDevice(AudioUtilities.GetMicrophone())
    previous_name = previous.FriendlyName if previous else "the previous device"

    if previous_name == target.FriendlyName:
        return SetDefaultResult(True, f"{target.FriendlyName!r} is already the Windows default microphone.")

    AudioUtilities.SetDefaultDevice(
        target.id, roles=[ERole.eConsole, ERole.eMultimedia, ERole.eCommunications]
    )
    return SetDefaultResult(
        True, f"Windows default microphone set to {target.FriendlyName!r} (was {previous_name!r})."
    )
