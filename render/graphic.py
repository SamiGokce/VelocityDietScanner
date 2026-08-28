"""The 1080x1920 still frame: full-bleed black-and-white photo + three lines.

Deliberate properties, all of them spec requirements:

  * the photo fills the frame -- no border, letterbox or negative space;
  * it is desaturated and contrast-boosted, never colourised;
  * a gradient (transparent to ~18% black) darkens the lower part of the frame
    so white type stays legible over any photo, without a flat black bar;
  * the only text is HAPPY {n}TH BIRTHDAY / NAME / {YEAR} - PRESENT.  No
    credit line, no watermark, no attribution -- that lives in the YouTube
    description.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from common.config import RenderCfg
from render.fonts import load_font
from render.text import (OverlayText, draw_tracked, line_height, overlay_lines,
                         text_width, tracking_px, wrap_to_width)

log = logging.getLogger(__name__)

WHITE = (255, 255, 255, 255)


class RenderError(RuntimeError):
    pass


@dataclass
class Block:
    """A laid-out text block, measured before anything is drawn."""
    top_font_size: int
    name_font_size: int
    name_lines: list[str]
    height: int


# --- image treatment -------------------------------------------------------

def to_black_and_white(image: Image.Image, cfg: RenderCfg) -> Image.Image:
    """Desaturate and increase contrast.  Never colourise."""
    grey = ImageOps.grayscale(image)
    grey = ImageOps.autocontrast(grey, cutoff=1)
    if cfg.contrast != 1.0:
        grey = ImageEnhance.Contrast(grey).enhance(cfg.contrast)
    if cfg.brightness != 1.0:
        grey = ImageEnhance.Brightness(grey).enhance(cfg.brightness)
    if cfg.sharpen:
        grey = grey.filter(ImageFilter.UnsharpMask(radius=2, percent=90, threshold=3))
    return grey.convert("RGB")


def cover_crop(image: Image.Image, width: int, height: int, anchor_y: float = 0.35) -> Image.Image:
    """Scale-and-crop so the photo covers the whole canvas (no letterboxing).

    `anchor_y` biases the vertical crop towards the top of the source, where
    faces usually are: 0 keeps the top edge, 0.5 centres, 1 keeps the bottom.
    """
    src_w, src_h = image.size
    if src_w == 0 or src_h == 0:
        raise RenderError("source image has zero size")
    scale = max(width / src_w, height / src_h)
    new_size = (max(width, int(round(src_w * scale))), max(height, int(round(src_h * scale))))
    resized = image.resize(new_size, Image.LANCZOS)
    left = int(round((resized.width - width) / 2))
    top = int(round((resized.height - height) * min(max(anchor_y, 0.0), 1.0)))
    return resized.crop((left, top, left + width, top + height))


def vignette_mask(width: int, height: int, start: float, opacity: float) -> Image.Image:
    """A vertical alpha ramp: transparent above `start`, `opacity` at the bottom.

    Smoothstep rather than a linear ramp, so the top of the gradient blends
    into the photo with no visible edge.
    """
    column = Image.new("L", (1, height), 0)
    pixels = column.load()
    first = int(height * start)
    span = max(1, height - 1 - first)
    peak = int(round(255 * max(0.0, min(1.0, opacity))))
    for y in range(first, height):
        t = (y - first) / span
        pixels[0, y] = int(round(peak * (t * t * (3 - 2 * t))))
    return column.resize((width, height), Image.BILINEAR)


def apply_vignette(image: Image.Image, cfg: RenderCfg) -> Image.Image:
    mask = vignette_mask(image.width, image.height, cfg.vignette_start, cfg.vignette_opacity)
    shade = Image.new("RGB", image.size, (0, 0, 0))
    return Image.composite(shade, image, mask)


# --- type layout -----------------------------------------------------------

def layout_block(text: OverlayText, cfg: RenderCfg, max_width: float) -> Block:
    """Find the largest name size that fits, and measure the whole block."""
    small_font = load_font(str(cfg.font_small), cfg.small_size, cfg.font_small_variation)
    small_tracking = tracking_px(small_font, cfg.tracking_small)
    if text_width(small_font, text.top, small_tracking) > max_width:
        log.debug("small line wider than the text column; it will be tight")

    name_lines: list[str] | None = None
    size = cfg.name_max_size
    for candidate_size in range(cfg.name_max_size, cfg.name_min_size - 1, -2):
        font = load_font(str(cfg.font_display), candidate_size, cfg.font_display_variation)
        tracking = tracking_px(font, cfg.tracking_name)
        # One line is always preferred; two lines only if the name is long.
        for max_lines in (1, 2):
            lines = wrap_to_width(text.name, font, tracking, max_width, max_lines=max_lines)
            if lines:
                name_lines, size = lines, candidate_size
                break
        if name_lines:
            break

    if not name_lines:
        # Very long name: fall back to the minimum size over up to three lines.
        size = cfg.name_min_size
        font = load_font(str(cfg.font_display), size, cfg.font_display_variation)
        tracking = tracking_px(font, cfg.tracking_name)
        name_lines = wrap_to_width(text.name, font, tracking, max_width, max_lines=3) \
            or [text.name]

    name_font = load_font(str(cfg.font_display), size, cfg.font_display_variation)
    name_line_h = int(line_height(name_font) * 1.02)
    height = (
        line_height(small_font)
        + cfg.gap_above_name
        + name_line_h * len(name_lines)
        + cfg.gap_below_name
        + line_height(small_font)
    )
    return Block(cfg.small_size, size, name_lines, height)


def draw_overlay(canvas: Image.Image, text: OverlayText, cfg: RenderCfg) -> Image.Image:
    """Draw the three lines with a soft drop shadow, in the bottom third."""
    max_width = canvas.width - 2 * cfg.side_margin
    block = layout_block(text, cfg, max_width)

    small_font = load_font(str(cfg.font_small), block.top_font_size, cfg.font_small_variation)
    name_font = load_font(str(cfg.font_display), block.name_font_size, cfg.font_display_variation)
    small_tracking = tracking_px(small_font, cfg.tracking_small)
    name_tracking = tracking_px(name_font, cfg.tracking_name)
    name_line_h = int(line_height(name_font) * 1.02)

    bottom = canvas.height - cfg.block_bottom_margin
    y = bottom - block.height
    lowest_allowed_top = int(canvas.height * 2 / 3) - line_height(small_font)
    if y < lowest_allowed_top:
        log.debug("text block is taller than the bottom third (top at y=%d)", y)

    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    centre_x = canvas.width / 2

    draw_tracked(draw, (centre_x, y), text.top, small_font, small_tracking, WHITE)
    y += line_height(small_font) + cfg.gap_above_name
    for line in block.name_lines:
        draw_tracked(draw, (centre_x, y), line, name_font, name_tracking, WHITE)
        y += name_line_h
    y += cfg.gap_below_name
    draw_tracked(draw, (centre_x, y), text.bottom, small_font, small_tracking, WHITE)

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    alpha = layer.getchannel("A").filter(ImageFilter.GaussianBlur(cfg.shadow_blur))
    alpha = alpha.point(lambda v: int(v * cfg.shadow_opacity))
    shadow.putalpha(alpha)
    shadow = shadow.transform(
        shadow.size, Image.AFFINE, (1, 0, -cfg.shadow_offset, 0, 1, -cfg.shadow_offset)
    )

    out = canvas.convert("RGBA")
    out = Image.alpha_composite(out, shadow)
    out = Image.alpha_composite(out, layer)
    return out.convert("RGB")


# --- the whole frame -------------------------------------------------------

def render_frame(photo: Image.Image, full_name: str, age_turning: int,
                 birth_year: int, cfg: RenderCfg) -> Image.Image:
    text = overlay_lines(full_name, age_turning, birth_year)
    canvas = cover_crop(photo, cfg.width, cfg.height, cfg.crop_anchor_y)
    canvas = to_black_and_white(canvas, cfg)
    canvas = apply_vignette(canvas, cfg)
    return draw_overlay(canvas, text, cfg)


def render_to_file(photo_path: str | Path, destination: str | Path, full_name: str,
                   age_turning: int, birth_year: int, cfg: RenderCfg) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(photo_path) as photo:
        photo.load()
        if photo.mode in ("P", "RGBA", "LA"):
            photo = photo.convert("RGB")
        frame = render_frame(photo, full_name, age_turning, birth_year, cfg)
    frame.save(destination, format="PNG", optimize=True)
    return destination
