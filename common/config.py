"""Load config.yaml into a typed object, with ${ENV_VAR} expansion.

Secrets (OAuth client secret path, refresh-token path, contact email) are
written as ${VAR} placeholders in config.yaml and resolved from the process
environment or a local .env file, so nothing sensitive is committed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .dates import parse_date

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")
REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when configuration is missing or self-contradictory."""


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader -- existing environment always wins."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else "")
        return ENV_PATTERN.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _resolve(p: str | os.PathLike[str]) -> Path:
    """Resolve a config path relative to the repo root unless absolute."""
    path = Path(str(p)).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path)


@dataclass(frozen=True)
class Schedule:
    start_date: date
    days: int
    per_day_min: int
    per_day_max: int


@dataclass(frozen=True)
class Paths:
    database: Path
    review_log: Path
    graphics_dir: Path
    videos_dir: Path
    image_cache_dir: Path
    curated_list: Path


@dataclass(frozen=True)
class Sourcing:
    min_sitelinks: int
    candidate_limit: int
    pageviews_days: int
    allowed_licenses: tuple[str, ...]
    user_agent: str
    request_delay_seconds: float
    max_retries: int
    sparql_endpoint: str
    require_english_article: bool


@dataclass(frozen=True)
class RenderCfg:
    width: int
    height: int
    crop_anchor_y: float
    contrast: float
    brightness: float
    sharpen: bool
    vignette_start: float
    vignette_opacity: float
    font_display: Path
    font_display_variation: str | None
    font_small: Path
    font_small_variation: str | None
    name_max_size: int
    name_min_size: int
    small_size: int
    tracking_name: float
    tracking_small: float
    gap_above_name: int
    gap_below_name: int
    block_bottom_margin: int
    side_margin: int
    shadow_offset: int
    shadow_blur: int
    shadow_opacity: float


@dataclass(frozen=True)
class VideoCfg:
    enabled: bool
    duration_seconds: float
    fps: int
    zoom: float
    audio_track_path: str
    audio_fade_in: float
    audio_fade_out: float
    crf: int
    preset: str
    ffmpeg_binary: str


@dataclass(frozen=True)
class YouTubeCfg:
    client_secrets_path: Path
    token_path: Path
    uploads_per_day: int
    privacy_status: str
    category_id: str
    made_for_kids: bool
    title_template: str
    description_template: str
    tags: tuple[str, ...]
    max_retries: int
    retry_base_delay: float


@dataclass(frozen=True)
class Config:
    schedule: Schedule
    paths: Paths
    sourcing: Sourcing
    render: RenderCfg
    video: VideoCfg
    youtube: YouTubeCfg
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    # -- validation helpers -------------------------------------------------
    def require_audio_track(self) -> Path:
        """Return the user-supplied music track, or fail loudly.

        There is no bundled or sample audio fallback on purpose: silently
        muxing in a track the user has not cleared is exactly the copyright
        problem this project is built to avoid.
        """
        raw = (self.video.audio_track_path or "").strip()
        if not raw:
            raise ConfigError(
                "video.audio_track_path is empty.\n"
                "Supply your own royalty-free instrumental (YouTube Audio Library, "
                "a 'no copyright' track you have cleared, or a licensed music "
                "library), drop it in assets/audio/, and set video.audio_track_path "
                "in config.yaml.\n"
                "This pipeline will never fall back to a bundled or commercially "
                "released track -- including the one you may have had in mind."
            )
        path = _resolve(raw)
        if not path.is_file():
            raise ConfigError(f"video.audio_track_path does not exist: {path}")
        return path

    def ensure_dirs(self) -> None:
        for d in (
            self.paths.database.parent,
            self.paths.review_log.parent,
            self.paths.graphics_dir,
            self.paths.videos_dir,
            self.paths.image_cache_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    cfg_path = _resolve(path or os.environ.get("BIRTHDAY_CONFIG", "config.yaml"))
    _load_dotenv(REPO_ROOT / ".env")
    if not cfg_path.is_file():
        raise ConfigError(f"config file not found: {cfg_path}")
    data = _expand(yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {})

    sch = data.get("schedule", {})
    per_day_min = int(sch.get("per_day_min", 3))
    per_day_max = int(sch.get("per_day_max", 5))
    if per_day_min < 1 or per_day_max < per_day_min:
        raise ConfigError("schedule.per_day_min/per_day_max are inconsistent")

    p = data.get("paths", {})
    s = data.get("sourcing", {})
    r = data.get("render", {})
    v = data.get("video", {})
    y = data.get("youtube", {})
    fonts = r.get("fonts", {})

    vignette_opacity = float(r.get("vignette_opacity", 0.18))
    if not 0.0 <= vignette_opacity <= 1.0:
        raise ConfigError("render.vignette_opacity must be between 0 and 1")
    vignette_start = float(r.get("vignette_start", 0.60))
    if not 0.0 <= vignette_start < 1.0:
        raise ConfigError("render.vignette_start must be between 0 and 1")

    return Config(
        schedule=Schedule(
            start_date=parse_date(sch.get("start_date", "today")),
            days=int(sch.get("days", 90)),
            per_day_min=per_day_min,
            per_day_max=per_day_max,
        ),
        paths=Paths(
            database=_resolve(p.get("database", "data/birthdays.sqlite3")),
            review_log=_resolve(p.get("review_log", "data/review_log.jsonl")),
            graphics_dir=_resolve(p.get("graphics_dir", "output/graphics")),
            videos_dir=_resolve(p.get("videos_dir", "output/videos")),
            image_cache_dir=_resolve(p.get("image_cache_dir", "output/cache/images")),
            curated_list=_resolve(p.get("curated_list", "data/curated_notable.csv")),
        ),
        sourcing=Sourcing(
            min_sitelinks=int(s.get("min_sitelinks", 40)),
            candidate_limit=int(s.get("candidate_limit", 250)),
            pageviews_days=int(s.get("pageviews_days", 90)),
            allowed_licenses=tuple(s.get("allowed_licenses", ["cc0", "public-domain", "cc-by", "cc-by-sa"])),
            user_agent=str(s.get("user_agent") or "").strip(),
            request_delay_seconds=float(s.get("request_delay_seconds", 1.0)),
            max_retries=int(s.get("max_retries", 5)),
            sparql_endpoint=str(s.get("sparql_endpoint", "https://query.wikidata.org/sparql")),
            require_english_article=bool(s.get("require_english_article", True)),
        ),
        render=RenderCfg(
            width=int(r.get("width", 1080)),
            height=int(r.get("height", 1920)),
            crop_anchor_y=float(r.get("crop_anchor_y", 0.35)),
            contrast=float(r.get("contrast", 1.25)),
            brightness=float(r.get("brightness", 1.0)),
            sharpen=bool(r.get("sharpen", True)),
            vignette_start=vignette_start,
            vignette_opacity=vignette_opacity,
            font_display=_resolve(fonts.get("display", "assets/fonts/Cinzel[wght].ttf")),
            font_display_variation=fonts.get("display_variation") or None,
            font_small=_resolve(fonts.get("small", fonts.get("display", "assets/fonts/Cinzel[wght].ttf"))),
            font_small_variation=fonts.get("small_variation") or None,
            name_max_size=int(r.get("name_max_size", 132)),
            name_min_size=int(r.get("name_min_size", 56)),
            small_size=int(r.get("small_size", 38)),
            tracking_name=float(r.get("tracking_name", 0.06)),
            tracking_small=float(r.get("tracking_small", 0.34)),
            gap_above_name=int(r.get("gap_above_name", 46)),
            gap_below_name=int(r.get("gap_below_name", 135)),
            block_bottom_margin=int(r.get("block_bottom_margin", 190)),
            side_margin=int(r.get("side_margin", 90)),
            shadow_offset=int(r.get("shadow_offset", 4)),
            shadow_blur=int(r.get("shadow_blur", 12)),
            shadow_opacity=float(r.get("shadow_opacity", 0.55)),
        ),
        video=VideoCfg(
            enabled=bool(v.get("enabled", True)),
            duration_seconds=float(v.get("duration_seconds", 20)),
            fps=int(v.get("fps", 30)),
            zoom=float(v.get("zoom", 1.12)),
            audio_track_path=str(v.get("audio_track_path") or ""),
            audio_fade_in=float(v.get("audio_fade_in", 1.0)),
            audio_fade_out=float(v.get("audio_fade_out", 2.0)),
            crf=int(v.get("crf", 20)),
            preset=str(v.get("preset", "medium")),
            ffmpeg_binary=str(v.get("ffmpeg_binary", "ffmpeg")),
        ),
        youtube=YouTubeCfg(
            client_secrets_path=_resolve(y.get("client_secrets_path", "client_secret.json")),
            token_path=_resolve(y.get("token_path", "youtube_token.json")),
            uploads_per_day=int(y.get("uploads_per_day", 3)),
            privacy_status=str(y.get("privacy_status", "private")).lower(),
            category_id=str(y.get("category_id", "24")),
            made_for_kids=bool(y.get("made_for_kids", False)),
            title_template=str(y.get("title_template", "{full_name} — Happy Birthday")),
            description_template=str(y.get("description_template", "")),
            tags=tuple(y.get("tags", [])),
            max_retries=int(y.get("max_retries", 5)),
            retry_base_delay=float(y.get("retry_base_delay", 5.0)),
        ),
        raw=data,
    )
