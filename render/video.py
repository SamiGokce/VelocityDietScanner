"""Turn a rendered frame into a 15-30s vertical MP4 with a slow Ken Burns push.

The photo layer is what moves.  The type and vignette are overlaid afterwards
as a fixed RGBA layer, because zooming a frame with the words already burnt in
scales them up and pushes them off the edges over the course of the shot.

ffmpeg does the work (zoompan + H.264).  moviepy would also do -- it is a
wrapper around the same encoder -- but shelling out avoids a heavy dependency
and gives exact control over the filter graph.

The audio track is whatever the user put in `video.audio_track_path`.  If that
is empty the render fails loudly: no bundled sample, no "default" music, and
never a commercially released track.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from common.config import Config

log = logging.getLogger(__name__)


class VideoError(RuntimeError):
    pass


def ensure_ffmpeg(binary: str = "ffmpeg") -> str:
    path = shutil.which(binary)
    if not path:
        raise VideoError(
            f"{binary!r} not found on PATH. Install ffmpeg (macOS: `brew install ffmpeg`, "
            "Debian/Ubuntu: `sudo apt install ffmpeg`, Windows: https://ffmpeg.org/download.html) "
            "or set video.enabled: false to produce stills only."
        )
    return path


def build_command(ffmpeg: str, frame: Path, audio: Path, destination: Path,
                  *, duration: float, fps: int, zoom: float, crf: int, preset: str,
                  width: int, height: int, fade_in: float, fade_out: float,
                  overlay: Path | None = None, supersample: int = 2) -> list[str]:
    """Assemble the ffmpeg invocation (separated out so tests can inspect it)."""
    total_frames = max(1, int(round(duration * fps)))
    # zoompan works on a supersampled frame: oversampling keeps the slow
    # push-in smooth instead of stepping pixel by pixel.  When the photo layer
    # was already rendered at this size (the normal case) the scale is a no-op
    # and no detail is thrown away and re-invented.
    factor = max(1, int(supersample))
    over_w, over_h = width * factor, height * factor
    zoom_step = (max(1.0001, zoom) - 1.0) / total_frames
    fade_out_start = max(0.0, duration - max(0.1, fade_out))
    zoom_chain = (
        f"scale={over_w}:{over_h}:flags=lanczos,"
        f"zoompan=z='min(zoom+{zoom_step:.6f},{zoom:.4f})':d={total_frames}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
    )
    audio_filter = (
        f"afade=t=in:st=0:d={max(0.0, fade_in):.2f},"
        f"afade=t=out:st={fade_out_start:.2f}:d={max(0.1, fade_out):.2f}"
    )
    inputs = ["-loop", "1", "-i", str(frame)]
    if overlay is not None:
        inputs += ["-loop", "1", "-i", str(overlay)]
        audio_index = 2
        graph = (
            f"[0:v]{zoom_chain}[bg];"
            f"[bg][1:v]overlay=0:0:shortest=0[composed];"
            f"[composed]format=yuv420p[v];"
        )
    else:
        audio_index = 1
        graph = f"[0:v]{zoom_chain},format=yuv420p[v];"
    inputs += ["-stream_loop", "-1", "-i", str(audio)]  # loop short tracks to length

    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", graph + f"[{audio_index}:a]{audio_filter}[a]",
        "-map", "[v]", "-map", "[a]",
        "-t", f"{duration:.3f}",
        "-r", str(fps),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-shortest",
        str(destination),
    ]


def render_video(frame_path: str | Path, destination: str | Path, cfg: Config,
                 overlay_path: str | Path | None = None) -> Path:
    """Mux a frame and the configured audio into an MP4.

    With `overlay_path`, `frame_path` is the photo layer (zoomed) and the
    overlay is composited on top unmoved.  Without it the whole frame zooms,
    which is fine for a still that carries no type.
    """
    frame = Path(frame_path)
    if not frame.is_file():
        raise VideoError(f"frame not found: {frame}")
    overlay = Path(overlay_path) if overlay_path else None
    if overlay is not None and not overlay.is_file():
        raise VideoError(f"overlay not found: {overlay}")
    audio = cfg.require_audio_track()          # raises ConfigError when unset
    ffmpeg = ensure_ffmpeg(cfg.video.ffmpeg_binary)

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = build_command(
        ffmpeg, frame, audio, destination,
        duration=cfg.video.duration_seconds,
        fps=cfg.video.fps,
        zoom=cfg.video.zoom,
        crf=cfg.video.crf,
        preset=cfg.video.preset,
        width=cfg.render.width,
        height=cfg.render.height,
        fade_in=cfg.video.audio_fade_in,
        fade_out=cfg.video.audio_fade_out,
        overlay=overlay,
        supersample=cfg.render.supersample,
    )
    log.debug("ffmpeg: %s", " ".join(command))
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoError(
            f"ffmpeg failed ({result.returncode}) for {frame.name}:\n{result.stderr.strip()[:1200]}"
        )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise VideoError(f"ffmpeg produced no output for {frame.name}")
    return destination
