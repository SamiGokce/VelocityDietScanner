"""Append-only JSONL log of every person the pipeline did *not* use.

Nothing is ever dropped silently: no open-licensed image, alive-status
mismatch, notability below threshold, render failure -- each one lands here
with enough context to re-check it by hand.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reason codes (stable strings -- grep-friendly)
NO_IMAGE_CLAIM = "no_p18_image_claim"
NO_OPEN_LICENSE = "no_open_licensed_image"
IMAGE_FETCH_FAILED = "image_metadata_fetch_failed"
ALIVE_MISMATCH = "alive_status_mismatch"
ALIVE_UNVERIFIED = "alive_status_unverified"
BELOW_THRESHOLD = "notability_below_threshold"
NOT_SELECTED = "not_selected_for_day"
NO_ENGLISH_ARTICLE = "no_english_wikipedia_article"
RENDER_FAILED = "render_failed"
UPLOAD_FAILED = "upload_failed"


class ReviewLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, reason: str, *, name: str | None = None,
               wikidata_id: str | None = None, target_date: str | None = None,
               **extra: Any) -> None:
        entry: dict[str, Any] = {
            "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "reason": reason,
            "wikidata_id": wikidata_id,
            "name": name,
            "target_date": target_date,
        }
        entry.update(extra)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out
