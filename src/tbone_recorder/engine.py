"""Capture engine: one long-lived input stream, recording taps into it.

The meter runs whenever a device is open, not only while recording, so you can
set your gain before hitting record.  Recording just starts diverting the same
blocks into a file.

Threading contract: the PortAudio callback does nothing but copy the block and
hand it off.  Writing to disk from the callback causes dropouts that are
miserable to diagnose, so a writer thread owns the file exclusively.
"""

from __future__ import annotations

import datetime as _dt
import math
import queue
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SUBTYPES = {
    "PCM_16": "16-bit WAV",
    "PCM_24": "24-bit WAV",
    "FLOAT": "32-bit float WAV",
}

MONO_SOURCES = ("left", "right", "mix")

_SILENCE = -120.0


def _dbfs(value: float) -> float:
    if value <= 1e-7:
        return _SILENCE
    return max(_SILENCE, 20.0 * math.log10(min(value, 4.0)))


class EngineError(RuntimeError):
    pass


class RecorderEngine:
    def __init__(self, outdir: Path):
        self.outdir = Path(outdir)
        self.outdir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._stream: sd.InputStream | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=512)
        self._writer: threading.Thread | None = None
        self._meter_thread: threading.Thread | None = None
        self._stop_meter = threading.Event()

        # Single-slot handoff for the meter; the callback overwrites it freely.
        self._latest_block: np.ndarray | None = None

        self.device: int | None = None
        self.samplerate: int = 48000
        self.stream_channels: int = 1
        self.blocksize: int = 1024

        self.recording: bool = False
        self.record_path: Path | None = None
        self.record_started_at: float | None = None
        self.frames_written: int = 0
        self.record_channels: int = 1
        self.mono_source: str = "left"
        self.subtype: str = "PCM_24"

        self.levels: list[dict] = []
        self.clipped: bool = False
        self.overflows: int = 0
        self.error: str | None = None
        self._opened_at: float | None = None
        self._all_zero_since: float | None = time.time()
        self._last_block_at: float | None = None
        self.silent_seconds: float = 0.0

    # ---------------------------------------------------------------- stream

    def open(self, device: int, samplerate: int, blocksize: int = 1024) -> None:
        """Open (or reopen) the monitoring stream on the given device."""
        with self._lock:
            if self.recording:
                raise EngineError("Stop the recording before switching device.")
            self.close()

            info = sd.query_devices(device)
            max_in = int(info["max_input_channels"])
            if max_in < 1:
                raise EngineError(f"{info['name']!r} has no input channels.")
            # Meter up to two channels so the user can see which one carries
            # signal; more than that is noise in this UI.
            channels = min(max_in, 2)

            try:
                sd.check_input_settings(
                    device=device, channels=channels, samplerate=samplerate
                )
            except Exception as exc:
                raise EngineError(
                    f"Device does not support {samplerate} Hz / {channels} ch: {exc}"
                ) from exc

            self.error = None
            self.clipped = False
            self.overflows = 0
            self._all_zero_since = time.time()
            self._last_block_at = None
            self._opened_at = time.time()
            self.silent_seconds = 0.0
            self.levels = [
                {"rms": _SILENCE, "peak": _SILENCE, "hold": _SILENCE}
                for _ in range(channels)
            ]

            try:
                stream = sd.InputStream(
                    device=device,
                    channels=channels,
                    samplerate=samplerate,
                    blocksize=blocksize,
                    dtype="float32",
                    callback=self._callback,
                    finished_callback=self._on_finished,
                )
                stream.start()
            except Exception as exc:
                raise EngineError(f"Could not open audio device: {exc}") from exc

            self._stream = stream
            self.device = device
            self.samplerate = samplerate
            self.stream_channels = channels
            self.blocksize = blocksize

            self._stop_meter.clear()
            self._meter_thread = threading.Thread(
                target=self._meter_loop, name="meter", daemon=True
            )
            self._meter_thread.start()

    def close(self) -> None:
        with self._lock:
            if self.recording:
                self.stop_recording()
            self._stop_meter.set()
            stream, self._stream = self._stream, None
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            self.device = None
            self._latest_block = None

    @property
    def is_open(self) -> bool:
        return self._stream is not None and self._stream.active

    # -------------------------------------------------------------- callback

    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        if status:
            if getattr(status, "input_overflow", False):
                self.overflows += 1
        block = indata.copy()
        self._latest_block = block
        self._last_block_at = time.time()
        if self.recording:
            try:
                self._queue.put_nowait(block)
            except queue.Full:
                # Disk cannot keep up.  Drop rather than block the audio thread.
                self.overflows += 1

    def _on_finished(self) -> None:
        # Fires when PortAudio tears the stream down on its own, e.g. the USB
        # mic was unplugged mid-session.
        # Only an involuntary stop is an error; close() clears _stream first.
        if self._stream is not None:
            self.error = "Audio stream stopped (device disconnected?)."
            # Clearing the flag lets the writer thread drain and close the file,
            # so whatever was captured before the unplug is still a valid WAV.
            self.recording = False
            if self.record_path is not None:
                self.error = (
                    f"Audio stream stopped (device disconnected?). "
                    f"{self.record_path.name} was closed and kept."
                )
                self.record_path = None
                self.record_started_at = None
            # Zero the meters: leaving the last block's levels on screen makes a
            # dead input look like a live one.
            for lvl in self.levels:
                lvl["rms"] = lvl["peak"] = lvl["hold"] = _SILENCE
            self._latest_block = None

    # ----------------------------------------------------------------- meter

    def _meter_loop(self) -> None:
        decay = 0.6  # dB per 50 ms tick -> 12 dB/s peak-hold fallback
        while not self._stop_meter.is_set():
            block = self._latest_block
            # A stream that opens but never calls back is just as broken as one
            # delivering silence -- on macOS a missing mic permission can look
            # like either -- so age the counter from open() until audio lands.
            if block is None:
                started = getattr(self, "_opened_at", None)
                if started is not None:
                    self.silent_seconds = time.time() - started
            if block is not None and block.size:
                nonzero = False
                for ch in range(min(block.shape[1], len(self.levels))):
                    data = block[:, ch]
                    peak = float(np.max(np.abs(data)))
                    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
                    if peak > 0.0:
                        nonzero = True
                    if peak >= 0.999:
                        self.clipped = True
                    lvl = self.levels[ch]
                    lvl["rms"] = _dbfs(rms)
                    lvl["peak"] = _dbfs(peak)
                    # Linear dB decay; multiplying dB values would move the
                    # wrong way once a peak goes above 0 dBFS.
                    hold = lvl["hold"]
                    lvl["hold"] = (
                        lvl["peak"]
                        if lvl["peak"] >= hold
                        else max(_SILENCE, hold - decay)
                    )
                now = time.time()
                last = self._last_block_at
                if last is not None and now - last > 1.0:
                    # Blocks stopped arriving entirely; treat as silence.
                    self._all_zero_since = self._all_zero_since or last
                    nonzero = False
                if nonzero:
                    self._all_zero_since = None
                    self.silent_seconds = 0.0
                elif self._all_zero_since is None:
                    self._all_zero_since = now
                if self._all_zero_since is not None:
                    self.silent_seconds = now - self._all_zero_since
            self._stop_meter.wait(0.05)

    # ------------------------------------------------------------- recording

    def _resolve_write_block(self, block: np.ndarray) -> np.ndarray:
        if self.record_channels >= block.shape[1]:
            return block
        # Down to mono from a 2-channel stream.  "left" is the safe default:
        # many single-capsule USB mics duplicate the capsule across both
        # channels, and a few leave one channel dead -- mixing those would cost
        # 6 dB for no benefit.
        if self.mono_source == "mix":
            return block.mean(axis=1, keepdims=True)
        idx = 1 if self.mono_source == "right" and block.shape[1] > 1 else 0
        return block[:, idx : idx + 1]

    def start_recording(
        self,
        channels: int = 1,
        subtype: str = "PCM_24",
        mono_source: str = "left",
        name: str | None = None,
    ) -> Path:
        with self._lock:
            if not self.is_open:
                raise EngineError("No audio device is open.")
            if self.recording:
                raise EngineError("Already recording.")
            if subtype not in SUBTYPES:
                raise EngineError(f"Unknown format {subtype!r}.")
            if mono_source not in MONO_SOURCES:
                mono_source = "left"

            channels = max(1, min(int(channels), self.stream_channels))
            stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            safe = "".join(c for c in (name or "") if c.isalnum() or c in " -_").strip()
            filename = f"{stamp}{'_' + safe.replace(' ', '-') if safe else ''}.wav"
            path = self.outdir / filename

            try:
                handle = sf.SoundFile(
                    str(path),
                    mode="w",
                    samplerate=self.samplerate,
                    channels=channels,
                    subtype=subtype,
                    format="WAV",
                )
            except Exception as exc:
                raise EngineError(f"Could not create {path.name}: {exc}") from exc

            # Drain anything stale before we start counting.
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break

            self.record_channels = channels
            self.subtype = subtype
            self.mono_source = mono_source
            self.record_path = path
            self.frames_written = 0
            self.clipped = False
            self.record_started_at = time.time()
            self.recording = True

            self._writer = threading.Thread(
                target=self._writer_loop, args=(handle,), name="writer", daemon=True
            )
            self._writer.start()
            return path

    def _writer_loop(self, handle: sf.SoundFile) -> None:
        try:
            while True:
                try:
                    block = self._queue.get(timeout=0.2)
                except queue.Empty:
                    if not self.recording:
                        break
                    continue
                if block is None:
                    break
                try:
                    handle.write(self._resolve_write_block(block))
                    self.frames_written += len(block)
                except Exception as exc:
                    self.error = f"Write failed: {exc}"
                    break
        finally:
            try:
                handle.close()
            except Exception:
                pass

    def stop_recording(self) -> Path | None:
        with self._lock:
            if not self.recording:
                return None
            self.recording = False
            path = self.record_path
            writer = self._writer

        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if writer is not None:
            writer.join(timeout=5.0)

        with self._lock:
            self._writer = None
            self.record_started_at = None
            self.record_path = None
        return path

    # ---------------------------------------------------------------- status

    def status(self) -> dict:
        elapsed = (
            self.frames_written / self.samplerate if self.samplerate else 0.0
        )
        return {
            "open": self.is_open,
            "device": self.device,
            "samplerate": self.samplerate,
            "stream_channels": self.stream_channels,
            "recording": self.recording,
            "record_path": str(self.record_path) if self.record_path else None,
            "record_name": self.record_path.name if self.record_path else None,
            "elapsed": elapsed,
            "frames": self.frames_written,
            "channels": self.record_channels,
            "subtype": self.subtype,
            "mono_source": self.mono_source,
            "levels": [dict(l) for l in self.levels],
            "clipped": self.clipped,
            "overflows": self.overflows,
            "silent_seconds": round(self.silent_seconds, 1),
            "error": self.error,
        }
