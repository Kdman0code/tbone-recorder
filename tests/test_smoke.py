"""Tests that run without any audio hardware, so CI can check every platform."""

import sys

import pytest

import shutil

from tbone_recorder import autostart, config, devices, engine, export, system_audio, watcher


def test_dbfs_scale():
    assert engine._dbfs(1.0) == pytest.approx(0.0, abs=0.01)
    assert engine._dbfs(0.5) == pytest.approx(-6.02, abs=0.05)
    assert engine._dbfs(0.0) == engine._SILENCE
    # Never returns -inf, which would break JSON serialisation of the meters.
    assert engine._dbfs(1e-20) == engine._SILENCE


def test_match_score_prefers_manufacturer_over_name():
    """The SC 500 is called plain "Microphone" on macOS; only the
    manufacturer distinguishes it from the built-in mic."""
    generic = devices._match_score("Microphone", None)
    thomann = devices._match_score("Microphone", "Thomann")
    builtin = devices._match_score("MacBook Pro Microphone", "Apple Inc.")
    assert thomann < generic
    assert thomann < builtin


def test_match_score_name_fallback():
    assert devices._match_score("t.bone SC 500 USB", None) < 999
    assert devices._match_score("Microphone (USB Audio Device)", None) < 999
    assert devices._match_score("Line In", None) == 999


def test_host_api_ranking_prefers_wasapi():
    """Windows lists one entry per audio subsystem; WASAPI is the good one."""
    assert devices._host_api_rank("Windows WASAPI") < devices._host_api_rank("MME")
    assert devices._host_api_rank("Windows WASAPI") < devices._host_api_rank(
        "Windows DirectSound"
    )
    assert devices._host_api_rank("Core Audio") == 0


def test_list_input_devices_runs():
    """CI runners have no sound card; this must not raise."""
    result = devices.list_input_devices()
    assert isinstance(result, list)
    for dev in result:
        assert {"index", "name", "host_api", "channels"} <= set(dev)


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "config.json")
    config.save({"samplerate": 96000, "subtype": "PCM_16", "bogus": 1})
    loaded = config.load()
    assert loaded["samplerate"] == 96000
    assert loaded["subtype"] == "PCM_16"
    assert "bogus" not in loaded


def test_config_survives_corrupt_file(tmp_path, monkeypatch):
    bad = tmp_path / "config.json"
    bad.write_text("{not json at all")
    monkeypatch.setattr(config, "config_path", lambda: bad)
    assert config.load()["samplerate"] == config.DEFAULTS["samplerate"]


def test_resolve_device_by_name_not_index():
    """Indices move when USB devices are replugged; names are the stable key."""
    devs = [
        {"index": 4, "name": "Microphone", "host_api": "Core Audio"},
        {"index": 9, "name": "Other", "host_api": "Core Audio"},
    ]
    cfg = {"device_name": "microphone", "device_host_api": "Core Audio"}
    assert config.resolve_device(cfg, devs) == 4
    assert config.resolve_device({"device_name": "gone"}, devs) is None


def test_mono_downmix_selects_channel():
    np = pytest.importorskip("numpy")
    eng = engine.RecorderEngine.__new__(engine.RecorderEngine)
    eng.record_channels = 1
    block = np.array([[0.5, 0.1], [0.7, 0.3]], dtype="float32")

    eng.mono_source = "left"
    assert eng._resolve_write_block(block).ravel().tolist() == pytest.approx([0.5, 0.7])
    eng.mono_source = "right"
    assert eng._resolve_write_block(block).ravel().tolist() == pytest.approx([0.1, 0.3])
    eng.mono_source = "mix"
    assert eng._resolve_write_block(block).ravel().tolist() == pytest.approx([0.3, 0.5])

    # Stereo request passes the block through untouched.
    eng.record_channels = 2
    assert eng._resolve_write_block(block).shape == (2, 2)


def test_permission_hint_mentions_the_right_settings_panel():
    hint = devices.permission_hint()
    if sys.platform in ("darwin",) or sys.platform.startswith("win"):
        assert hint and "Microphone" in hint


def test_is_supported_matches_known_platforms(monkeypatch):
    monkeypatch.setattr(system_audio.sys, "platform", "darwin")
    assert system_audio.is_supported()
    monkeypatch.setattr(system_audio.sys, "platform", "win32")
    assert system_audio.is_supported()
    monkeypatch.setattr(system_audio.sys, "platform", "linux")
    assert not system_audio.is_supported()


def test_set_default_input_unsupported_platform_is_safe(monkeypatch):
    """Never raises, even where there's no implementation at all."""
    monkeypatch.setattr(system_audio.sys, "platform", "linux")
    result = system_audio.set_default_input("Microphone", "Thomann")
    assert result.ok is False
    assert "supported" in result.message.lower()


def test_set_default_input_missing_device_is_safe():
    """A device that doesn't exist must fail cleanly, not raise or hang --
    this runs for real on macOS and Windows CI runners, so it also doubles
    as a smoke test of the platform-specific lookup code with no matches."""
    if not system_audio.is_supported():
        pytest.skip("not supported on this platform")
    result = system_audio.set_default_input("Definitely Not A Real Device", "Nobody")
    assert result.ok is False


def test_export_mp3_missing_ffmpeg_raises_cleanly(tmp_path):
    """No ffmpeg on the box must be a clear error, not a crash."""
    src = tmp_path / "take.wav"
    src.write_bytes(b"not real audio, ffmpeg should never be invoked on it")
    with pytest.raises(export.ExportError, match="ffmpeg"):
        export.export_mp3(src, ffmpeg=None if shutil.which("ffmpeg") else "/no/such/ffmpeg")


def test_export_mp3_destination_is_derived_not_taken_from_caller(tmp_path):
    """The output path must always be src's own name with a .mp3 suffix --
    never something an untrusted caller could redirect."""
    src = tmp_path / "2026-09-27_10-00-00_take.wav"
    assert src.with_suffix(".mp3") == tmp_path / "2026-09-27_10-00-00_take.mp3"


def test_export_mp3_encodes_a_real_wav(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available on this runner")
    sf = pytest.importorskip("soundfile")
    np = pytest.importorskip("numpy")
    src = tmp_path / "take.wav"
    sf.write(str(src), np.zeros(4800, dtype="float32"), 48000, subtype="PCM_16")

    dest = export.export_mp3(src)

    assert dest == src.with_suffix(".mp3")
    assert dest.is_file() and dest.stat().st_size > 0


def test_disconnect_midrecording_clears_recording_state():
    """Unplugging mid-take must not leave the UI showing a live recording."""
    eng = engine.RecorderEngine.__new__(engine.RecorderEngine)
    eng._stream = object()          # a stream we did not close ourselves
    eng.recording = True
    eng.record_path = __import__("pathlib").Path("/tmp/take.wav")
    eng.record_started_at = 1.0
    eng.levels = [{"rms": -3.0, "peak": -2.0, "hold": -1.0}]
    eng.error = None

    eng._on_finished()

    assert eng.recording is False
    assert eng.record_path is None
    assert "take.wav" in eng.error and "kept" in eng.error
    # Meters must not keep showing the last live level.
    assert eng.levels[0]["peak"] == engine._SILENCE


def test_watcher_acts_only_on_the_connect_transition(monkeypatch):
    """Once the t.bone is default, switching away yourself must not get
    fought on every poll -- only the next unplug/replug reasserts it."""
    calls = []
    monkeypatch.setattr(
        watcher.system_audio,
        "set_default_input",
        lambda name, manufacturer=None: (calls.append(name) or system_audio.SetDefaultResult(True, "ok")),
    )
    tbone = {"name": "Microphone", "manufacturer": "Thomann", "likely_tbone": True}
    other = {"name": "MacBook Pro Microphone", "manufacturer": "Apple Inc.", "likely_tbone": False}

    present, message = watcher._tick(False, [other, tbone])
    assert present is True and message is not None
    assert calls == ["Microphone"]

    # Still connected on the next poll: no repeat action.
    present, message = watcher._tick(present, [other, tbone])
    assert present is True and message is None
    assert calls == ["Microphone"]

    # Unplugged: no action, just tracks the state.
    present, message = watcher._tick(present, [other])
    assert present is False and message is None
    assert calls == ["Microphone"]

    # Replugged: acts again.
    present, message = watcher._tick(present, [other, tbone])
    assert present is True and message is not None
    assert calls == ["Microphone", "Microphone"]


def test_autostart_unsupported_platform_is_safe(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    assert not autostart.is_supported()
    assert autostart.install().ok is False
    assert autostart.uninstall().ok is False


def test_autostart_macos_install_writes_plist_and_calls_launchctl(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    monkeypatch.setattr(autostart.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(autostart.cfg_mod, "config_dir", lambda: tmp_path / "config")

    calls = []

    class FakeProc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    result = autostart.install()
    assert result.ok is True
    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.tbone-recorder.watcher.plist"
    assert plist_path.is_file()
    assert "tbone_recorder" in plist_path.read_text("utf-8")
    assert any(c[:2] == ["launchctl", "load"] for c in calls if len(c) >= 2)

    result = autostart.uninstall()
    assert result.ok is True
    assert not plist_path.exists()


def test_autostart_windows_install_uses_schtasks(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(autostart.cfg_mod, "config_dir", lambda: __import__("pathlib").Path("C:/cfg"))

    calls = []

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    result = autostart.install()
    assert result.ok is True
    create_call = next(c for c in calls if "/Create" in c)
    assert "tbone_recorder" in " ".join(create_call)
    assert "--watch" in " ".join(create_call)
