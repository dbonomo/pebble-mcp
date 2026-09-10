#!/usr/bin/env python3
"""Regenerate ``PALETTE_DATA`` for pebble_mcp/palette.py from vendored truth.

Run from the repo root:  ``python pebble-mcp/scripts/gen_palette.py``

Reads the three ground-truth palette files under ``assets/palettes/`` and emits
the ``(GColor name, uncorrected hex, sunlight-corrected hex)`` table in the
canonical ``.act`` order, ready to paste into palette.py. The golden test
``tests/test_palette.py`` performs the same parse and asserts the embedded
table still matches, so any drift fails CI without needing to rerun this by
hand.

Sources:
  * pebble_colors_64.act              -- Photoshop color table: canonical order.
  * pebble_colors_uncorrected.aseprite -- raw palette (SDK-accurate) values.
  * pebble_colors_sunlight.aseprite    -- sunlight-corrected display values.
  * GColor names                       -- official SDK Color Definitions docs
                                          (developer.repebble.com).
"""

from __future__ import annotations

import struct
from pathlib import Path

# Official SDK GColor names keyed by 6-digit uppercase hex. Note: the SDK
# assigns 00FF55 -> GColorMalachite and 00FFAA -> GColorMediumSpringGreen
# (DESIGN.md mislabels 00FF55 as MediumSpringGreen).
NAMES = {
    "000000": "GColorBlack",
    "000055": "GColorOxfordBlue",
    "0000AA": "GColorDukeBlue",
    "0000FF": "GColorBlue",
    "005500": "GColorDarkGreen",
    "005555": "GColorMidnightGreen",
    "0055AA": "GColorCobaltBlue",
    "0055FF": "GColorBlueMoon",
    "00AA00": "GColorIslamicGreen",
    "00AA55": "GColorJaegerGreen",
    "00AAAA": "GColorTiffanyBlue",
    "00AAFF": "GColorVividCerulean",
    "00FF00": "GColorGreen",
    "00FF55": "GColorMalachite",
    "00FFAA": "GColorMediumSpringGreen",
    "00FFFF": "GColorCyan",
    "550000": "GColorBulgarianRose",
    "550055": "GColorImperialPurple",
    "5500AA": "GColorIndigo",
    "5500FF": "GColorElectricUltramarine",
    "555500": "GColorArmyGreen",
    "555555": "GColorDarkGray",
    "5555AA": "GColorLiberty",
    "5555FF": "GColorVeryLightBlue",
    "55AA00": "GColorKellyGreen",
    "55AA55": "GColorMayGreen",
    "55AAAA": "GColorCadetBlue",
    "55AAFF": "GColorPictonBlue",
    "55FF00": "GColorBrightGreen",
    "55FF55": "GColorScreaminGreen",
    "55FFAA": "GColorMediumAquamarine",
    "55FFFF": "GColorElectricBlue",
    "AA0000": "GColorDarkCandyAppleRed",
    "AA0055": "GColorJazzberryJam",
    "AA00AA": "GColorPurple",
    "AA00FF": "GColorVividViolet",
    "AA5500": "GColorWindsorTan",
    "AA5555": "GColorRoseVale",
    "AA55AA": "GColorPurpureus",
    "AA55FF": "GColorLavenderIndigo",
    "AAAA00": "GColorLimerick",
    "AAAA55": "GColorBrass",
    "AAAAAA": "GColorLightGray",
    "AAAAFF": "GColorBabyBlueEyes",
    "AAFF00": "GColorSpringBud",
    "AAFF55": "GColorInchworm",
    "AAFFAA": "GColorMintGreen",
    "AAFFFF": "GColorCeleste",
    "FF0000": "GColorRed",
    "FF0055": "GColorFolly",
    "FF00AA": "GColorFashionMagenta",
    "FF00FF": "GColorMagenta",
    "FF5500": "GColorOrange",
    "FF5555": "GColorSunsetOrange",
    "FF55AA": "GColorBrilliantRose",
    "FF55FF": "GColorShockingPink",
    "FFAA00": "GColorChromeYellow",
    "FFAA55": "GColorRajah",
    "FFAAAA": "GColorMelon",
    "FFAAFF": "GColorRichBrilliantLavender",
    "FFFF00": "GColorYellow",
    "FFFF55": "GColorIcterine",
    "FFFFAA": "GColorPastelYellow",
    "FFFFFF": "GColorWhite",
}


def parse_act(path: Path) -> list[str]:
    """First N RGB triplets of a Photoshop .act color table (N from trailer)."""
    data = path.read_bytes()
    count = struct.unpack(">H", data[768:770])[0] if len(data) >= 770 else 256
    return [f"{data[i * 3]:02X}{data[i * 3 + 1]:02X}{data[i * 3 + 2]:02X}" for i in range(count)]


def parse_aseprite_palette(path: Path) -> list[tuple[int, int, int, int]]:
    """RGBA entries from an Aseprite new-palette chunk (0x2019)."""
    data = path.read_bytes()
    p = 128 + 16  # file header + first frame header
    while p < len(data) - 6:
        csize, ctype = struct.unpack("<IH", data[p : p + 6])
        if ctype == 0x2019:
            cdata = data[p + 6 : p + csize]
            _size, first, last = struct.unpack("<III", cdata[:12])
            q, out = 20, []  # 12 header + 8 reserved bytes
            for _ in range(first, last + 1):
                (flags,) = struct.unpack("<H", cdata[q : q + 2])
                q += 2
                r, g, b, a = cdata[q], cdata[q + 1], cdata[q + 2], cdata[q + 3]
                q += 4
                if flags & 1:  # has a name string
                    (nlen,) = struct.unpack("<H", cdata[q : q + 2])
                    q += 2 + nlen
                out.append((r, g, b, a))
            return out
        p += csize
    raise ValueError("no palette chunk found")


def build_table(assets: Path) -> list[tuple[str, str, str]]:
    order = parse_act(assets / "pebble_colors_64.act")[:64]
    unc = parse_aseprite_palette(assets / "pebble_colors_uncorrected.aseprite")
    sun = parse_aseprite_palette(assets / "pebble_colors_sunlight.aseprite")
    # Real palette entries carry alpha=255; padding slots are alpha=0. The two
    # files share one layout, so they are index-aligned.
    corrected: dict[str, str] = {}
    for u, s in zip(unc, sun, strict=True):
        if u[3] == 255:
            corrected[f"{u[0]:02X}{u[1]:02X}{u[2]:02X}"] = f"{s[0]:02X}{s[1]:02X}{s[2]:02X}"
    if len(corrected) != 64 or set(corrected) != set(NAMES):
        raise ValueError("aseprite palette does not reconcile with the 64-color name set")
    return [(NAMES[h], h, corrected[h]) for h in order]


def main() -> None:
    assets = Path(__file__).resolve().parents[1] / "assets" / "palettes"
    print("PALETTE_DATA = [")
    for name, h, corrected in build_table(assets):
        print(f'    ("{name}", "{h}", "{corrected}"),')
    print("]")


if __name__ == "__main__":
    main()
