"""Download the open-licensed display fonts into assets/fonts/.

Cinzel, Cormorant Garamond and Playfair Display are all SIL Open Font License
1.1 -- free for commercial use, including in monetised video.  They stand in
for Trajan, which is proprietary and must not be used here.

    python scripts/get_fonts.py           # all three
    python scripts/get_fonts.py --only Cinzel
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# jsDelivr mirrors the google/fonts repository; raw.githubusercontent is the
# fallback for networks that block the CDN (and vice versa).
SOURCES = (
    "https://cdn.jsdelivr.net/gh/google/fonts@main/{path}",
    "https://raw.githubusercontent.com/google/fonts/main/{path}",
)

FONTS = {
    "Cinzel": "ofl/cinzel/Cinzel%5Bwght%5D.ttf",
    "PlayfairDisplay": "ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "CormorantGaramond": "ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf",
}

LICENSES = {
    "Cinzel": "ofl/cinzel/OFL.txt",
    "PlayfairDisplay": "ofl/playfairdisplay/OFL.txt",
    "CormorantGaramond": "ofl/cormorantgaramond/OFL.txt",
}


def download(remote_path: str, destination: Path) -> bool:
    for template in SOURCES:
        url = template.format(path=remote_path)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "birthday-pipeline/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            if len(data) < 1024:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            print(f"  {destination.name}  ({len(data):,} bytes)  <- {url}")
            return True
        except Exception as exc:  # noqa: BLE001 - report and try the next mirror
            print(f"  ! {url}: {exc}", file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=sorted(FONTS), help="download a single family")
    args = parser.parse_args(argv)

    wanted = [args.only] if args.only else list(FONTS)
    failures = []
    for family in wanted:
        print(f"{family}:")
        filename = FONTS[family].rsplit("/", 1)[-1].replace("%5B", "[").replace("%5D", "]")
        if not download(FONTS[family], ASSETS / filename):
            failures.append(family)
        download(LICENSES[family], ASSETS / f"OFL-{family}.txt")

    if failures:
        print(
            "\nCould not download: " + ", ".join(failures) +
            "\nDownload them by hand from https://fonts.google.com and drop the "
            "TTFs in assets/fonts/.",
            file=sys.stderr,
        )
        return 1
    print(f"\nFonts are in {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
