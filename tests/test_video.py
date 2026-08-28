"""Video muxing: the Ken Burns command, and the audio guard."""

from pathlib import Path

import pytest

from common.config import ConfigError, load_config
from dataclasses import replace
from render.video import build_command


def test_missing_audio_track_fails_loudly():
    cfg = load_config()
    with pytest.raises(ConfigError) as excinfo:
        cfg.require_audio_track()
    message = str(excinfo.value)
    assert "audio_track_path" in message
    assert "royalty-free" in message


def test_configured_audio_track_must_exist(tmp_path):
    cfg = load_config()
    cfg = replace(cfg, video=replace(cfg.video, audio_track_path=str(tmp_path / "nope.mp3")))
    with pytest.raises(ConfigError, match="does not exist"):
        cfg.require_audio_track()


def test_existing_audio_track_resolves(tmp_path):
    track = tmp_path / "track.mp3"
    track.write_bytes(b"not really audio")
    cfg = load_config()
    cfg = replace(cfg, video=replace(cfg.video, audio_track_path=str(track)))
    assert cfg.require_audio_track() == track


def test_ffmpeg_command_shape():
    command = build_command(
        "ffmpeg", Path("frame.png"), Path("music.mp3"), Path("out.mp4"),
        duration=20, fps=30, zoom=1.12, crf=20, preset="medium",
        width=1080, height=1920, fade_in=1.0, fade_out=2.0,
    )
    joined = " ".join(command)
    assert "libx264" in joined
    assert "-pix_fmt yuv420p" in joined
    assert "s=1080x1920" in joined            # vertical 9:16 output
    assert "zoompan" in joined                 # the Ken Burns move
    assert "-t 20.000" in joined
    assert "music.mp3" in joined
    assert "-stream_loop -1" in joined         # short tracks loop to length
    assert "+faststart" in joined


def test_duration_stays_in_the_shorts_range():
    cfg = load_config()
    assert 15 <= cfg.video.duration_seconds <= 30
