# Fonts

Run `python scripts/get_fonts.py` to populate this directory. It downloads:

| Family | Licence | Role |
| --- | --- | --- |
| **Cinzel** | SIL OFL 1.1 | default display face — the closest open face to Trajan |
| **Playfair Display** | SIL OFL 1.1 | alternative display face, higher contrast |
| **Cormorant Garamond** | SIL OFL 1.1 | alternative, lighter and more literary |

All three are free for commercial use, including in monetised video. The OFL
text is downloaded alongside each family (`OFL-*.txt`).

**Trajan is not used and must not be added here** — it is a proprietary Adobe
typeface and licensing it for video output is a separate, paid matter.

The TTFs themselves are gitignored (they are large and freely re-downloadable);
`scripts/get_fonts.py` is the reproducible way to get them back.

To switch faces, point `render.fonts.display` in `config.yaml` at another file,
e.g. `assets/fonts/PlayfairDisplay[wght].ttf`.
