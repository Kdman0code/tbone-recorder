"""MP3 export for finished takes.

WAV stays the primary format -- it is what podcast editors (Descript,
Audacity, Hindenburg, Adobe Podcast) want for lossless cutting. This module
exists for the other half of the workflow: mobile/social editors (CapCut,
InShot, Instagram's own tools) and quick uploads that choke on a large 24-bit
WAV but take an MP3 without complaint.

Shells out to ffmpeg rather than pulling in a Python encoder: libmp3lame via
ffmpeg is the de facto standard encoder and most users doing audio work
already have ffmpeg on their machine.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# A detached/backgrounded launch can inherit a PATH that lacks Homebrew's
# prefix even though an interactive shell would find ffmpeg fine -- fall back
# to the well-known install locations before giving up.
_FALLBACK_PATHS = (
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
)

# libmp3lame's hard ceiling; the engine can record up to 192 kHz.
_MP3_MAX_RATE = 48000

# Podcast/voice-memo norm: small enough for a quick upload, clean enough that
# re-cutting the clip afterward doesn't introduce audible artifacts.
_BITRATE = "128k"


class ExportError(RuntimeError):
    pass


def find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in _FALLBACK_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def export_mp3(src: Path, ffmpeg: str | None = None, timeout: float = 180.0) -> Path:
    """Encode src (a finished WAV) to an MP3 sibling and return its path."""
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        raise ExportError(
            "ffmpeg is not installed. Install it (e.g. `brew install ffmpeg` "
            "on macOS) to enable MP3 export."
        )
    dest = src.with_suffix(".mp3")
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i", str(src),
                "-ar", str(_MP3_MAX_RATE),
                "-b:a", _BITRATE,
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExportError(f"ffmpeg timed out encoding {src.name}") from exc
    except OSError as exc:
        raise ExportError(f"Could not run ffmpeg: {exc}") from exc

    if result.returncode != 0 or not dest.is_file():
        last_line = (result.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        raise ExportError(f"ffmpeg failed on {src.name}: {last_line[0]}")
    return dest
