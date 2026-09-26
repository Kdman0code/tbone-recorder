# t.bone recorder

A small recording interface for the **t.bone SC 500 USB** microphone (and any
other USB or built-in audio input). It runs on macOS and Windows, installs with
one command, and opens a local web page with a level meter, a record button and
your recordings.

The mic is class-compliant, so there are no drivers to install on either
platform — plug it in and it shows up.

```
┌─ INPUT LEVEL ────────────────────────────────┐
│ L ████████████▌         ░░░░░░░░   -18.4 dB  │
│ R ████████████▏         ░░░░░░░░   -18.9 dB  │
│   -60   -48   -36   -24   -12  -6   0 dB     │
└──────────────────────────────────────────────┘
   ● Record      00:12.4   mono · 24-bit
```

## Install

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/Kdman0code/tbone-recorder/main/install.sh | bash
```

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/Kdman0code/tbone-recorder/main/install.ps1 | iex"
```

Both scripts install [uv](https://docs.astral.sh/uv/) if it is missing. uv
downloads its own Python, so you do not need Python installed first. Open a new
terminal afterwards so the new `tbone-rec` command is on your PATH.

<details>
<summary>Other ways to install</summary>

```bash
# with uv, directly
uv tool install git+https://github.com/Kdman0code/tbone-recorder

# without git installed
uv tool install https://github.com/Kdman0code/tbone-recorder/archive/refs/heads/main.zip

# with pipx
pipx install git+https://github.com/Kdman0code/tbone-recorder

# from a clone, into a virtualenv
git clone https://github.com/Kdman0code/tbone-recorder
cd tbone-recorder
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/tbone-rec
```
</details>

## Use

```bash
tbone-rec
```

It picks the t.bone automatically, starts the level meter and opens the
interface in your browser. Press **Record** (or hit the space bar) to start, and
again to stop. Files land in `~/Music/tbone-recordings` as WAV.

Whenever it picks the t.bone, it also makes it your **system default
microphone** -- on macOS and Windows -- so every other app (Zoom, voice
memos, whatever) uses it too, the same way plugging in a USB headset usually
takes over as default. Pass `--no-set-default` to leave your OS default
alone and only use the t.bone inside this app.

That only happens while `tbone-rec` is running, though. To have it happen
automatically every time you plug the mic in -- login item, no interface,
nothing to remember to start -- run:

```bash
tbone-rec --install-watcher
```

This installs a small background process (a macOS `launchd` agent / a
Windows Scheduled Task, started now and again at every login) that watches
for the t.bone connecting and switches your OS default input to it, then
gets out of the way -- it doesn't fight you if you switch to something else
afterwards, only reacting the next time the mic disconnects and reconnects.
`tbone-rec --watch` runs the same loop in the foreground first, if you want
to see it work before installing it. `tbone-rec --uninstall-watcher` removes
it.

| Option | What it does |
| --- | --- |
| `tbone-rec` | start the interface |
| `tbone-rec --list-devices` | show every input device and which one looks like the t.bone |
| `tbone-rec --check` | record for three seconds and report whether audio actually arrives |
| `tbone-rec --device 2` | use a specific device (index or part of its name) |
| `tbone-rec --samplerate 96000` | set the sample rate |
| `tbone-rec --outdir ~/Recordings` | save somewhere else |
| `tbone-rec --port 8765` | pin the HTTP port |
| `tbone-rec --no-browser` | do not open a browser |
| `tbone-rec --no-set-default` | don't touch the OS default microphone |
| `tbone-rec --watch` | foreground loop: switch the OS default whenever the t.bone connects |
| `tbone-rec --install-watcher` | do the above automatically, forever, starting at login |
| `tbone-rec --uninstall-watcher` | remove the login item |

Your device, format and folder choices are remembered between runs.

### Recording format

Mono, 24-bit, 48 kHz by default. The SC 500 has a **single capsule**, so both
channels of its stereo stream carry the same signal — recording mono halves the
file size and loses nothing. Stereo is there if you want it, and "mono source"
lets you take the left channel, the right one, or a mix.

### Monitoring

Plug headphones into the jack on the microphone itself. That path is analogue
and has no latency, which is what you want while recording. The app does not
route audio to your speakers, precisely to avoid feedback.

## If something is wrong

**The meter does not move.**

On macOS, microphone access is granted per application, and a blocked app
receives *digital silence* rather than an error — so this looks like a bug in
the recorder rather than a permission problem. Open **System Settings → Privacy
& Security → Microphone** and enable access for whatever you launched
`tbone-rec` from (Terminal, iTerm, VS Code), then restart that app. On Windows
the equivalent is **Settings → Privacy & security → Microphone → Let desktop
apps access your microphone**.

Run `tbone-rec --check` to confirm; it tells you plainly whether any audio is
arriving and prints the permission hint if not.

Note that this also applies to *background* processes: a detached process that
macOS has not granted microphone access to will open the stream successfully and
then receive nothing. Run `tbone-rec` in a normal terminal window.

**The mic is not in the list.** Unplug and replug it, then press Reconnect. On
Windows, the same microphone appears once per audio subsystem (WASAPI, MME,
DirectSound); the app prefers WASAPI and hides the duplicates.

**Recording says "dropouts".** The disk or CPU could not keep up. Close other
apps, or record to a local disk rather than a network share.

**The level is too low or clipping.** Use the gain knob on the mic. Aim for
peaks around −12 dB; the meter turns amber there and red near 0 dB, where the
audio would distort.

## How it works

- `devices.py` — enumerates inputs, collapses the per-host-API duplicates
  Windows reports, and identifies the t.bone. On macOS the mic reports itself
  only as "Microphone", so the manufacturer (`Thomann`) is read from
  CoreAudio via `system_profiler` instead of guessing from the name.
- `engine.py` — one long-lived PortAudio input stream. The audio callback only
  copies each block and hands it off; a writer thread owns the file, so disk
  hiccups cannot stall the audio thread.
- `server.py` — a loopback-only HTTP server. It requires a per-run token and
  rejects non-loopback `Host` headers, so a web page you happen to have open
  cannot reach your microphone.
- `system_audio.py` — makes the t.bone the OS default input: CoreAudio via
  `ctypes` on macOS, the undocumented `IPolicyConfig` interface (via `pycaw`)
  on Windows. Best-effort; recording works even if the OS refuses.
- `watcher.py` / `autostart.py` — the login item behind `--install-watcher`:
  a loop that only enumerates devices (never opens a stream) and reacts to
  the t.bone connecting, installed as a `launchd` agent on macOS or a
  Scheduled Task on Windows.
- `web/` — the interface. Meters stream over Server-Sent Events.

Built on [PortAudio](http://www.portaudio.com/) via
[sounddevice](https://python-sounddevice.readthedocs.io/), and
[libsndfile](http://libsndfile.github.io/libsndfile/) via
[soundfile](https://python-soundfile.readthedocs.io/).

## Licence

MIT — see [LICENSE](LICENSE).
