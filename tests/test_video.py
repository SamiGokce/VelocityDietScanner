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


def test_overlay_is_composited_after_the_zoom_not_before():
    """The type must not be part of the Ken Burns move, or it drifts off-frame."""
    command = build_command(
        "ffmpeg", Path("photo.png"), Path("music.mp3"), Path("out.mp4"),
        duration=20, fps=30, zoom=1.12, crf=20, preset="medium",
        width=1080, height=1920, fade_in=1.0, fade_out=2.0,
        overlay=Path("type.png"),
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "type.png" in " ".join(command)
    assert graph.index("zoompan") < graph.index("overlay=0:0"), \
        "zoompan must run on the photo before the type is laid over it"
    # the audio input shifts to index 2 once there are two image inputs
    assert "[2:a]" in graph


def test_without_an_overlay_the_whole_frame_zooms():
    command = build_command(
        "ffmpeg", Path("frame.png"), Path("music.mp3"), Path("out.mp4"),
        duration=20, fps=30, zoom=1.12, crf=20, preset="medium",
        width=1080, height=1920, fade_in=1.0, fade_out=2.0,
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "overlay=" not in graph
    assert "[1:a]" in graph


# --- settings for slow, quiet, cinematic tracks ----------------------------

def _graph(command):
    return command[command.index("-filter_complex") + 1]


def base_command(**overrides):
    kwargs = dict(
        duration=20, fps=30, zoom=1.12, crf=20, preset="medium",
        width=1080, height=1920, fade_in=2.0, fade_out=3.0,
    )
    kwargs.update(overrides)
    return build_command(
        "ffmpeg", Path("frame.png"), Path("music.mp3"), Path("out.mp4"), **kwargs
    )


def test_loudness_is_normalised_before_the_fades():
    """Normalising after fading would re-scale the fade shape."""
    graph = _graph(base_command(loudness_lufs=-14.0))
    assert "loudnorm=I=-14.0:TP=-1.5:LRA=11" in graph
    assert graph.index("loudnorm") < graph.index("afade=t=in")


def test_loudness_normalisation_can_be_switched_off():
    assert "loudnorm" not in _graph(base_command(loudness_lufs=None))


def test_start_offset_seeks_into_the_track_before_the_input():
    """-ss must precede -i to seek the input rather than the output."""
    command = base_command(start_offset=42.5)
    assert "-ss" in command
    assert command.index("-ss") < command.index("music.mp3")
    assert command[command.index("-ss") + 1] == "42.50"


def test_no_offset_means_no_seek_argument():
    assert "-ss" not in base_command(start_offset=0.0)


def test_the_track_still_loops_when_seeking_into_it():
    """A 40s piece started at 30s must still fill a 20s clip."""
    command = base_command(start_offset=30.0)
    assert command.index("-stream_loop") < command.index("-ss")


def test_the_clip_does_not_open_on_silence():
    """Offset and fade must not gang up to recreate a quiet opening.

    The configured track begins with 1.43s of digital silence. Seeking past it
    only helps if the fade-in does not then spend two seconds getting back to
    full level -- which is exactly what the first batch of videos did.
    """
    video = load_config().video
    assert video.audio_start_offset > 0, "seek past the track's silent head"
    assert video.audio_fade_in <= 1.0, "a long fade undoes the offset"
    assert video.audio_fade_out >= 2.0, "but the ending should still be graceful"


def test_force_rerenders_rows_that_are_already_ready():
    """Changing a render setting is not a failure, but still needs a redo."""
    import inspect

    from render.generate import Renderer
    assert "force" in inspect.signature(Renderer.run).parameters
