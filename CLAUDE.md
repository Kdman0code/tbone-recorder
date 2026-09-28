# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`tbone-rec` is a cross-platform (macOS/Windows, Linux best-effort) recording
interface for the t.bone SC 500 USB microphone and other USB/built-in audio
inputs. It's a Python package (`src/tbone_recorder`) with a small
vanilla-JS/HTML frontend (`src/tbone_recorder/web`) served over a local-only
HTTP server — there is no build step or bundler for the frontend.

## Commands

```bash
# install for development (editable + test deps)
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# run the whole suite
.venv/bin/pytest tests -q

# run a single test
.venv/bin/pytest tests/test_smoke.py::test_mono_downmix_selects_channel -q

# run the app itself
.venv/bin/tbone-rec
.venv/bin/tbone-rec --list-devices
.venv/bin/tbone-rec --check          # 3s recording, reports whether audio actually arrives
```

CI (`.github/workflows/ci.yml`) runs on macOS, Windows, and Linux across Python
3.10/3.12: install the package, run `tbone-rec --version`, then `pytest tests`.
Linux CI additionally installs `libportaudio2` since the sounddevice wheel
doesn't bundle PortAudio there. Tests must run without any real audio hardware
or a connected t.bone — CI runners have neither.

## Architecture

Everything lives under `src/tbone_recorder/`, one module per concern:

- **`cli.py`** — argparse entry point (`tbone-rec`). Dispatches to one-shot
  commands (`--list-devices`, `--check`, `--watch`, `--install-watcher`), or
  else opens the device, starts the HTTP server, and opens a browser tab.
- **`devices.py`** — enumerates PortAudio input devices, collapses the
  per-host-API duplicates Windows reports (prefers WASAPI), and scores
  candidates to guess which one is the t.bone. On macOS the mic only
  identifies itself as "Microphone", so `platform_hints.py` shells out to
  `system_profiler` to get the manufacturer ("Thomann") from CoreAudio.
- **`engine.py`** (`RecorderEngine`) — owns one long-lived PortAudio input
  stream. The audio callback (real-time thread) only copies each block and
  hands it to a queue; a separate writer thread owns the WAV file, so disk
  I/O hiccups never stall the audio callback. Also computes the dBFS level
  meters consumed by the SSE stream.
- **`server.py`** — a loopback-only `ThreadingHTTPServer`. Every request
  (except serving `index.html`'s own auth check) requires a per-run token
  passed as `?t=` or `X-Auth-Token`, and the `Host` header must match
  loopback — this stops DNS rebinding attacks from a webpage you have open
  reaching your microphone. Meters stream to the browser over SSE
  (`/api/stream`, ~12 Hz). Keep new endpoints behind `_authorised()`.
- **`config.py`** — persisted settings (device, format, output folder) in the
  OS's per-user config dir. Devices are remembered **by name**, not index,
  because PortAudio renumbers indices whenever anything is plugged/unplugged.
- **`system_audio.py`** — best-effort: makes the t.bone the OS-level default
  input device. CoreAudio via `ctypes` on macOS; the undocumented
  `IPolicyConfig` COM interface (via `pycaw`) on Windows. Never raises —
  recording works even if this fails.
- **`watcher.py`** / **`autostart.py`** — a background poll loop
  (`tbone-rec --watch`) that notices the t.bone connecting and re-asserts it
  as OS default, acting only on the connect transition (so it doesn't fight
  you if you switch inputs afterward). `autostart.py` installs this as a
  login item (`--install-watcher`): a launchd plist on macOS, a Scheduled
  Task on Windows, always invoking `sys.executable -m tbone_recorder --watch`
  since login-time launchers don't source a shell PATH.
- **`runtime.py`** — publishes the running server's URL/token/pid to a
  `runtime.json` file (0600 perms) so out-of-process tools (e.g. a future
  menu-bar indicator) can find a server they didn't start.
- **`export.py`** — shells out to `ffmpeg` (checking Homebrew/system fallback
  paths, since a backgrounded launch can have a stripped PATH) to produce an
  MP3 sibling of a finished WAV, for mobile/social workflows that choke on
  large 24-bit WAV files.
- **`web/`** — the frontend: plain HTML/CSS/JS, no framework or build step.
  Talks to the server via the `/api/*` endpoints and the SSE stream.

### Key invariants to preserve

- Recording format defaults to mono/24-bit/48kHz. The SC 500 is a single
  capsule mic, so its "stereo" stream is the same signal on both channels —
  mono halves file size for free. `mono_source` (`left`/`right`/`mix`) picks
  how the stereo input is downmixed.
- The audio callback in `engine.py` must stay allocation-light and never
  block on I/O; all file writing happens on the separate writer thread.
- Anything that talks to platform audio subsystems (`system_audio.py`,
  `autostart.py`) must degrade gracefully and never raise — these are
  best-effort conveniences, not core functionality, and are exercised in CI
  on real macOS/Windows runners with no real device present.
- Device matching in `devices.py`/`config.py` is name-based, never
  index-based.
