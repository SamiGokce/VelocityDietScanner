# Audio

**Put your own royalty-free instrumental here**, then set it in `config.yaml`:

```yaml
video:
  audio_track_path: assets/audio/your-track.mp3
```

Nothing is bundled and nothing is downloaded automatically. While
`audio_track_path` is empty, video rendering fails with an explicit error
rather than falling back to any default track.

## Where to get a track you can actually use

- **YouTube Audio Library** (YouTube Studio → Audio Library) — free, cleared for
  use on YouTube. Check whether the track requires attribution; if it does, add
  the credit to `youtube.description_template`.
- **A licensed library** — Epidemic Sound, Artlist, Musicbed, PremiumBeat. Keep
  the licence receipt.
- **Creative Commons instrumentals** — Free Music Archive, ccMixter. Read the
  specific licence: NonCommercial tracks are not suitable for a monetised
  channel, and CC BY tracks need a credit in the description.

## What not to use

Do not use a commercially released recording — including the piano piece this
kind of montage is usually cut to. Content ID will find it, and the result is a
claim, demonetisation, or a strike on the channel. The one-off cost of a
licensed track is much lower than the cost of losing the channel.
