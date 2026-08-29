# Audio

**Put your own royalty-free instrumental here**, then set it in `config.yaml`:

```yaml
video:
  audio_track_path: assets/audio/your-track.mp3
  audio_start_offset: 30      # skip the quiet intro; land on the main phrase
  audio_loudness_lufs: -14    # match YouTube's playback target
```

Nothing is bundled and nothing is downloaded automatically. While
`audio_track_path` is empty, video rendering fails with an explicit error
rather than falling back to any default track.

## The register this format wants

Slow, minor-key, sparse piano or strings — the Einaudi/Max Richter/Ólafur
Arnalds lane. Search terms that land on it in a music library: *emotional
piano*, *cinematic melancholy*, *sad piano*, *reflective strings*,
*contemplative*, *nostalgic*. In the YouTube Audio Library, filter Genre →
*Cinematic* or *Classical* and Mood → *Sad* or *Dramatic*.

**A note on going full funeral.** Tracks tagged *funeral*, *memorial*,
*requiem*, *elegy* or *RIP* carry a specific signal, and these posts are about
people who are alive. The same reasoning that made this project write
`1979 – PRESENT` instead of `1979 – 2026` applies to the soundtrack: a memorial
cue under a black-and-white portrait reads as a death announcement faster than
any caption can correct it, and the comments will say so. Melancholy and
reflective gets you the same emotional weight without the misread. If you do
want the heavier end, pick pieces described as *elegiac* or *solemn* rather than
*funeral march* or *requiem*, and lean on the title and description doing the
celebratory work.

## Where to get a track you can actually use

- **YouTube Audio Library** (YouTube Studio → Audio Library) — free, cleared for
  use on YouTube, filterable by mood. Check whether the track requires
  attribution; if it does, add the credit to `youtube.description_template`.
- **A licensed library** — Epidemic Sound, Artlist, Musicbed, PremiumBeat. Keep
  the licence receipt.
- **Creative Commons instrumentals** — Free Music Archive, ccMixter. Read the
  specific licence: NonCommercial tracks are not suitable for a monetised
  channel, and CC BY tracks need a credit in the description.
- **Public-domain classical recordings** — Musopen has recordings released under
  CC0/PD. Note that a *composition* being public domain does not make every
  *recording* of it free; check the recording's own licence.

## Practical settings for a slow piece

These pieces usually open with 20–40 seconds of near-silence, and a 16-second
clip cut from the head of the file is mostly nothing. Play the track, find the
moment it opens up, and put that timestamp in `audio_start_offset`. A short
track is looped automatically to fill the clip, and the loop restarts from the
offset, so the quiet intro never reappears mid-video.

`audio_loudness_lufs: -14` matters more than it sounds: soft piano recordings
often sit well below a pop master, and without normalisation the Shorts are
near-inaudible on a phone next to everything else in the feed.

## What not to use

Do not use a commercially released recording — including the piano piece this
kind of montage is usually cut to. Content ID will find it, and the result is a
claim, demonetisation, or a strike on the channel. The one-off cost of a
licensed track is much lower than the cost of losing the channel.
