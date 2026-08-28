# 90-Day Birthday Content Automation

Builds a 90-day database of **living** public figures' birthdays, renders a
branded black-and-white 9:16 graphic/video for each, and uploads 3–5 per day to
YouTube on a schedule.

```
scripts/  Wikidata + Commons sourcing and notability ranking
render/   Pillow still frame + ffmpeg Ken Burns video
upload/   YouTube Data API v3 upload
common/   config, SQLite storage, ordinals, review log
data/     the generated database and the review log
assets/   fonts (downloaded) and your own audio track
```

---

## The rules this pipeline will not break

These are load-bearing, not stylistic. Each one is enforced in code and covered
by a test.

| Rule | Where it is enforced |
| --- | --- |
| Only CC0 / public-domain / CC BY / CC BY-SA images from Wikimedia Commons. Unknown or NC/ND licences are **rejected**, never assumed. | `scripts/commons.py`, `tests/test_licensing.py` |
| No pixelated photos: anything needing more than `max_upscale` enlargement to fill the frame is skipped during sourcing. | `scripts/commons.py`, `tests/test_image_quality.py` |
| Attribution is **required** for CC BY / CC BY-SA — and appears **only in the YouTube description**, never on the frame. | `upload/upload_daily.py` refuses a description template without `{attribution}`; `tests/test_overlay_text.py` captures every string the renderer draws |
| No commercial music. `video.audio_track_path` must be a track you supply; empty means a loud failure, never a fallback. | `common/config.py:require_audio_track`, `tests/test_video.py` |
| Open fonts only (Cinzel / Cormorant / Playfair Display), never Trajan. | `scripts/get_fonts.py`, `config.yaml` |
| Never a birth–death range. Always `{BIRTH YEAR} – PRESENT`. | `common/ordinals.py:year_line` — there is no code path that emits a range |
| Ordinal suffixes: 11th/12th/13th are always "TH". | `common/ordinals.py`, exhaustively tested for ages 1–122 |
| Alive status is verified twice, and disagreements are flagged for a human — never auto-resolved. | `scripts/wikidata.py` (Wikidata) + `scripts/alive_check.py` (English Wikipedia) |
| Every skipped person is logged, never silently dropped. | `common/review_log.py` → `data/review_log.jsonl` |

### A note on going credit-free

Attribution sits in the description because the pipeline uses the full
CC-BY/CC-BY-SA/CC0/PD pool. If you ever want *no* credit anywhere — not even in
the description — you must narrow `sourcing.allowed_licenses` to
`[cc0, public-domain]`. That is a one-line config change, and it will shrink the
pool of eligible people substantially on most days. This project does not do
that by default.

---

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/get_fonts.py                 # Cinzel + Playfair + Cormorant (OFL)
cp .env.example .env                         # then edit: contact email, secrets

python main.py run --dry-run --days 7        # build the database, render nothing
python main.py status                        # eyeball what was sourced
python main.py export --out data/preview.csv # spot-check in a spreadsheet

python main.py render --no-video --limit 5   # a handful of stills to look at
python main.py render                        # everything, with video
python main.py upload --dry-run              # what today would post
python main.py upload                        # actually post
```

`--dry-run` on `run` is the spec's data-only mode: it produces the database and
stops before any graphics are generated.

### Running one step at a time

`main.py` is a thin wrapper; these are equivalent and are what cron calls:

```bash
python -m scripts.fetch_birthdays --days 90
python -m render.generate --date 2026-09-01
python -m upload.upload_daily --limit 3
```

---

## 1. Data pipeline

For each of the 90 days:

1. **Query Wikidata** (`scripts/wikidata.py`), in two passes. The *candidate*
   query asks only for Q-ids and sitelink counts of humans born on that
   month/day with day-level date precision and at least
   `sourcing.min_sitelinks` Wikipedia editions; anyone carrying *any*
   death-implying statement (P570 including deprecated statements,
   place/cause/manner of death, place of burial) is excluded there. The
   *detail* query then fetches labels, photos, articles and occupations for the
   top `sourcing.detail_pool` of them plus your curated list, with the subjects
   bound up front so it stays cheap. Splitting it this way is what keeps the
   day inside the query service's 60-second budget — one combined query that
   also joins 67 occupation values does not fit, and times out.
2. **Rank** (`scripts/pageviews.py`) the shortlist by average daily English
   Wikipedia pageviews over a trailing window, with a small bounded bonus for
   sitelink count so someone globally famous but quiet this month is not buried.
3. **Verify** each candidate in rank order: an openly licensed, *high enough
   resolution* Commons photo (`scripts/commons.py`) *and* an independent alive
   check against English Wikipedia (`scripts/alive_check.py`). The first 3–5 who
   pass are the day's selection; everyone else is written to the review log with
   a reason. The whole shortlist's photo metadata is fetched in one batched
   Commons request rather than one call per person.

### Photo quality

Commons' P18 images are bimodal. A measured sample of one day's twenty
candidates: five were 2300–2700px press photos, seven were middling
(1000–1500px), and the rest were thumbnails — the smallest was 149×224. Filling
a 1080×1920 frame from the bottom of that range means inventing 90% of the
pixels, which is exactly the mush you don't want.

So resolution is a **sourcing** filter, not a render-time one. The test is not
raw pixel count but `max_upscale` — how much the photo must be enlarged to
cover the frame after cropping to 9:16:

```
upscale = max(1080 / width, 1920 / height)
```

A 4000×1200 panorama has more pixels than the canvas and still fails, because
only 1200px of height are available for a 1920px-tall frame. Rejecting a soft
photo during sourcing means the next-ranked person takes the slot, so the day
still fills; rejecting it at render time would leave the day short.

Measured pass rates on that same twenty-candidate pool:

| `max_upscale` | candidates passing | look |
| --- | --- | --- |
| 1.00 | 30% | never enlarged at all — strictest |
| **1.25** (default) | **45%** | slight enlargement, still crisp |
| 1.40 | 55% | softness becomes visible on faces |
| 2.00 | 70% | visibly pixelated photos get through |

With a 45-candidate detail pool per day, 45% is a comfortable margin over the
3–5 slots. Raise `max_upscale` only if days start coming up short — the review
log tells you (`image_resolution_too_low`), and `min_image_width` /
`min_image_height` are an absolute floor underneath it.

Downloads then ask Commons for *exactly* enough pixels, computed from the
original's stored dimensions — a fixed thumbnail width throws away detail on
landscape sources, where height is the binding constraint — and the video's
photo layer is rendered at `render.supersample` (2× = 2160×3840) so the Ken
Burns push reveals real detail instead of enlarging a downsampled frame.

Rows land in SQLite (`data/birthdays.sqlite3`) with the columns the spec asks
for — `full_name, birthday, birth_year, age_turning, category, image_url,
image_license, image_attribution, alive_verified, graphic_status, notes` — plus
bookkeeping (`video_path`, `upload_status`, `youtube_video_id`, `pageviews`, …).
`birthday` is the date the person is *featured*; `birth_date` is their date of
birth.

**Statuses.** `graphic_status` is `Pending` → `Ready` (rendered) or `Failed`;
`Needs Review` means the alive check did not come back clean, and those rows are
never rendered and never uploaded. `alive_verified` is `yes`, `mismatch` (the
two sources disagree) or `unverified` (no positive confirmation).

### Curated notability list

`data/curated_notable.csv` (one `Q-id,name,notes` per line) lets you force people
in below the sitelink threshold. They are considered before the ranked pool.

### When Wikidata is slow

The public query service enforces a 60-second budget and occasionally goes into
"1 request per minute" mode during an outage. The client handles this: it honours
`Retry-After`, backs off exponentially, retries the candidate query in
birth-year bands if it times out (their union is the same set of people), and
caches every day's raw result under `output/cache/sparql/` so a re-run costs
nothing. A full 90-day fetch is a long job — run it once, overnight
if need be. If the service is down for a while you can point
`sourcing.sparql_endpoint` at a mirror (e.g. `https://qlever.cs.uni-freiburg.de/api/wikidata`);
the query declares its own prefixes so it is portable.

---

## 2. Graphic / video generation

- 1080×1920, full-bleed: the photo is scaled to cover and cropped with a
  face-biased anchor. No border, no letterbox, no negative space.
- Desaturated with autocontrast + a contrast boost, lightly unsharp-masked.
  Never colourised.
- A smoothstep gradient, fully transparent across the top 60%, easing to 18%
  black at the bottom edge — legibility without a flat black bar.
- Three lines, centred, wide-tracked Cinzel, white with a soft drop shadow, in
  the bottom third:
  `HAPPY 57TH BIRTHDAY` / **JACK BLACK** / `1969 – PRESENT`.
  The name auto-fits between `name_max_size` and `name_min_size` and wraps to a
  second line only when it must.
- Video: the frame is rendered as two layers — the photo, and an RGBA layer
  holding the vignette and the type. ffmpeg `zoompan`s the photo (over a 2×
  oversampled copy, so the push-in is smooth rather than stepped) and composites
  the type on top unmoved. Burning the words into the zoomed layer instead would
  scale them up over the shot and push the ends of the lines off the edges.
  Audio is looped to length and faded; output is H.264 + AAC with `+faststart`.

Rendering is decoupled from uploading and per-row: one bad photo marks that row
`Failed` with a note and the batch continues.

**ffmpeg is required for video.** Install it (`brew install ffmpeg`,
`apt install ffmpeg`) or set `video.enabled: false` for stills only.

**Audio.** Put your own royalty-free instrumental in `assets/audio/` and set
`video.audio_track_path`. Sources: the YouTube Audio Library (in YouTube Studio),
a track licensed for commercial use, or a "no copyright" track whose terms you
have actually read. Do not use a commercially released recording — the whole
pipeline is built to keep the channel clear of claims and strikes.

---

## 3. YouTube setup (one-time, done by you, outside the code)

1. **Google Cloud project** — <https://console.cloud.google.com/> → new project.
2. **Enable the API** — APIs & Services → Library → *YouTube Data API v3* → Enable.
3. **OAuth consent screen** — External; add your own Google account as a test
   user. (A channel-owner-only app does not need verification.)
4. **Credentials** — Create Credentials → OAuth client ID → **Desktop app** →
   download the JSON as `client_secret.json` in the repo root (it is gitignored).
5. **Consent flow, once:**
   ```bash
   python -m upload.youtube_auth
   ```
   A browser opens; approve; a token file with a refresh token is written to
   `youtube_token.json` (mode 600, gitignored). **Never commit it.**

For CI, don't ship the file — copy its `refresh_token` into secrets and set
`YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`.

### Uploading

`upload_daily.py` takes the day's highest-ranked `Ready`, alive-verified,
not-yet-posted rows, uploads each with `videos.insert`, and marks them `Posted`
with the returned video id. The description is rendered from
`youtube.description_template`, which **must** contain `{attribution}` — the
script refuses to run otherwise, because that credit is the only place the
CC BY / CC BY-SA condition is met.

Start with `privacy_status: private` (the default) or `unlisted` for the first few
weeks and flip videos to public after a glance in YouTube Studio. That is how you
catch a wrong photo, a bad crop, or an alive-status error before the public does.

### Quota

`videos.insert` is expensive, and published figures for its cost and for the
default daily quota **vary between sources and change over time**. Do not plan a
posting schedule against a number from a blog post — open
*Google Cloud Console → APIs & Services → YouTube Data API v3 → Quotas* and read
your project's current limits before raising `youtube.uploads_per_day`. The
uploader treats quota errors as terminal for the day (retrying does not help
until the window resets at midnight US/Pacific), logs them, and leaves the
remaining rows `Pending` for tomorrow.

---

## 4. Scheduling

**cron** (recommended — it runs where your rendered files and audio live):

```cron
# 09:15 daily: post the day's videos
15 9 * * *  cd /path/to/VelocityDietScanner && .venv/bin/python -m upload.upload_daily >> logs/upload.log 2>&1
# 03:00 Sundays: extend the window and render ahead
0  3 * * 0  cd /path/to/VelocityDietScanner && .venv/bin/python main.py run --days 90 >> logs/pipeline.log 2>&1
```

**GitHub Actions** — `.github/workflows/daily_upload.yml` is included and runs
the whole fetch → render → upload chain on a schedule. Read the comments at the
top of that file before enabling it: it needs your OAuth secrets *and* an audio
track, which is deliberately not in the repository.

---

## 5. Configuration

Everything lives in `config.yaml`; `${VAR}` placeholders are filled from the
environment or a local `.env`. The settings worth knowing:

| Key | Meaning |
| --- | --- |
| `schedule.start_date` / `days` | the window (`today` + 90 by default) |
| `schedule.per_day_min` / `per_day_max` | 3–5 people per day; short days are logged |
| `sourcing.user_agent` | **put a real contact address here** — Wikimedia throttles anonymous bots |
| `sourcing.min_sitelinks` | fame threshold; lower = more people, more obscure |
| `sourcing.detail_pool` | how many of a day's candidates get the detail query |
| `sourcing.max_upscale` | photo-quality gate; 1.0 never enlarges, 1.25 is the default |
| `sourcing.min_image_width` / `min_image_height` | absolute resolution floor |
| `render.supersample` | render the video's photo layer above canvas size (2×) |
| `sourcing.sparql_endpoint` | swap in a mirror when the public service is degraded |
| `sourcing.allowed_licenses` | narrow to `[cc0, public-domain]` to go credit-free |
| `render.vignette_start` / `vignette_opacity` | where the gradient starts, and how dark it gets |
| `render.name_max_size` / `small_size` | the type hierarchy (name ≈ 3.5× the small lines) |
| `video.audio_track_path` | **your** royalty-free track; empty = hard failure |
| `youtube.privacy_status` | `private` / `unlisted` / `public` |
| `youtube.uploads_per_day` | check your quota before raising this |

---

## 6. Reviewing what got skipped

`data/review_log.jsonl` is append-only, one JSON object per line:

```bash
python -c "import json;[print(e['reason'], '-', e.get('name'), '-', e.get('detail','')[:80]) for e in map(json.loads, open('data/review_log.jsonl'))]"
```

Reasons: `no_p18_image_claim`, `no_open_licensed_image`, `image_resolution_too_low`,
`no_english_wikipedia_article`,
`alive_status_mismatch`, `alive_status_unverified`, `notability_below_threshold`,
`not_selected_for_day`, `render_failed`, `upload_failed`, `day_underfilled`.

`alive_status_mismatch` is the one to read every time — it means Wikidata and
Wikipedia disagree about whether someone is alive.

---

## 7. Tests

```bash
pytest -q
```

165 tests, no network required. The ones that matter most:
`test_ordinals.py` (every age 1–122), `test_alive_check.py`,
`test_overlay_text.py` (captures every string drawn on a frame),
`test_licensing.py`, `test_image_quality.py` (the resolution gate),
`test_upload_metadata.py` (the credit is always present).

---

## Licence and attribution summary

- **Code**: yours.
- **Fonts**: Cinzel, Cormorant Garamond, Playfair Display — SIL OFL 1.1, free for
  commercial use. Licence files are downloaded alongside the TTFs.
- **Photos**: Wikimedia Commons, CC0 / PD / CC BY / CC BY-SA, credited in every
  video description.
- **Music**: whatever you supply. Nothing is bundled.
