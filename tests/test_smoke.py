"""Tests that run without any audio hardware, so CI can check every platform."""

import sys

import pytest

from tbone_recorder import config, devices, engine


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
