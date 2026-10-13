"""The six ink colors of the Spectra 6 panel, and the colors mixed from them.

The renderer only ever draws with the pure colors below, so the image can be
handed to the panel without any dithering (crisp text, no speckles).
"""

from __future__ import annotations

import colorsys
from functools import lru_cache

from PIL import Image, ImageChops

# Order matters: it matches inky's DESATURATED_PALETTE (black, white, yellow,
# red, blue, green), which lets the driver skip dithering entirely.
PURE = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "yellow": (255, 255, 0),
    "red": (255, 0, 0),
    "blue": (0, 0, 255),
    "green": (0, 255, 0),
}
COLOR_NAMES = list(PURE)

# Roughly what the inks look like on the real panel, for the laptop preview.
SIMULATED = {
    "black": (32, 32, 36),
    "white": (226, 224, 214),
    "yellow": (214, 190, 50),
    "red": (168, 46, 42),
    "blue": (42, 62, 128),
    "green": (54, 102, 68),
}

BLACK, WHITE, YELLOW, RED, BLUE, GREEN = (PURE[n] for n in COLOR_NAMES)

# Calendar colors named after Google Calendar's 24-color palette, in the
# order Google shows them (6 columns x 4 rows). The panel can't show them
# exactly, so each one is either a single ink or a 2x2 pattern of inks
# (top-left, top-right, bottom-left, bottom-right), repeated over the fill. From
# normal viewing distance the pattern reads as the blended color.
# name -> (Google's color, single ink or 2x2 inks)
GOOGLE = {
    "radicchio": ("#ad1457", ("black", "red", "red", "black")),
    "tomato": ("#d50000", "red"),
    "citron": ("#e4c441", ("white", "yellow", "yellow", "white")),
    "basil": ("#0b8043", "green"),
    "blueberry": ("#3f51b5", "blue"),
    "grape": ("#8e24aa", ("blue", "red", "red", "blue")),
    "cherry-blossom": ("#d81b60", ("red", "blue", "red", "red")),
    "tangerine": ("#f4511e", ("red", "yellow", "red", "red")),
    "avocado": ("#c0ca33", ("yellow", "green", "yellow", "yellow")),
    "eucalyptus": ("#009688", ("green", "blue", "blue", "green")),
    "lavender": ("#7986cb", ("white", "blue", "blue", "white")),
    "cocoa": ("#795548", ("red", "black", "black", "yellow")),
    "pumpkin": ("#ef6c00", ("yellow", "red", "red", "yellow")),
    "mango": ("#f09300", ("yellow", "red", "yellow", "yellow")),
    "pistachio": ("#7cb342", ("green", "yellow", "yellow", "green")),
    "peacock": ("#039be5", ("blue", "green", "white", "blue")),
    "wisteria": ("#b39ddb", ("red", "white", "white", "blue")),
    "graphite": ("#616161", ("white", "black", "black", "white")),
    "flamingo": ("#e67c73", ("white", "red", "red", "white")),
    "banana": ("#f6bf26", "yellow"),
    "sage": ("#33b679", ("white", "green", "green", "white")),
    "cobalt": ("#4285f4", ("blue", "white", "blue", "blue")),
    "amethyst": ("#9e69af", ("blue", "red", "white", "blue")),
    "birch": ("#a79b8e", ("white", "black", "yellow", "white")),
}

# Older names, still accepted in config.toml and settings.json. The mixes map to
# the Google color drawn with the same pattern, so they look exactly as before.
ALIASES = {
    "red": "tomato",
    "yellow": "banana",
    "blue": "blueberry",
    "green": "basil",
    "orange": "pumpkin",
    "purple": "grape",
    "teal": "eucalyptus",
    "burgundy": "radicchio",
}


def _hex(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _mean(inks: tuple[str, ...]) -> tuple[int, int, int]:
    return tuple(round(sum(SIMULATED[i][k] for i in inks) / len(inks)) for k in range(3))


def _text_ink(inks: tuple[str, ...]) -> tuple[int, int, int]:
    r, g, b = _mean(inks)
    return BLACK if 0.299 * r + 0.587 * g + 0.114 * b > 120 else WHITE


# Patterned colors. While drawing, each uses a unique placeholder color (its
# Google color, never a real ink); apply_mixes() swaps placeholders for the
# pattern once the image is finished.
# name -> (2x2 inks, ink for text drawn on top of it, placeholder)
MIXES = {
    name: (inks, _text_ink(inks), _hex(google))
    for name, (google, inks) in GOOGLE.items()
    if isinstance(inks, tuple)
}

# Ready-made sets of calendar colors that look good together, given to
# calendars in order (wrapping round if there are more calendars than colors).
# The first five of each set are the core; the rest are for extra calendars.
PALETTES = {
    "jewel": ("Deep jewel tones", ["blueberry", "radicchio", "eucalyptus", "mango", "grape",
                                    "basil", "pumpkin", "cobalt"]),
    "bright": ("Bright primaries", ["blueberry", "tomato", "basil", "pumpkin", "grape",
                                    "mango", "eucalyptus", "cherry-blossom"]),
    "vintage": ("Vintage print", ["eucalyptus", "pumpkin", "radicchio", "blueberry", "cocoa",
                                  "mango", "basil", "grape"]),
    "cool": ("Cool with one warm", ["blueberry", "eucalyptus", "basil", "cobalt", "tangerine",
                                    "peacock", "pistachio", "lavender"]),
    "soft": ("Soft (grainier on the panel)", ["lavender", "sage", "flamingo", "mango", "peacock",
                                              "wisteria", "citron", "birch"]),
}
DEFAULT_PALETTE = "jewel"  # for calendars with no color in config.toml

# Holidays are drawn as thin text, which only reads well in a solid ink.
HOLIDAY_NAMES = ["tomato", "blueberry", "basil", "black"]

PICKER_NAMES = list(GOOGLE) + ["black", "white"]  # what the web UI offers for calendars
ALL_COLOR_NAMES = PICKER_NAMES + list(ALIASES)  # everything accepted for calendars
ACCENT_NAMES = ["black", "red", "blue", "green", "yellow", "orange", "purple", "teal", "burgundy"]


def canonical(name: str) -> str:
    """The picker name for any accepted color name."""
    return ALIASES.get(name, name)


def color(name: str) -> tuple[int, int, int]:
    """The color to draw with for a pure ink or a mix."""
    name = canonical(name)
    if name in MIXES:
        return MIXES[name][2]
    return PURE[GOOGLE[name][1] if name in GOOGLE else name]


def default_colors(calendars, palette: str = DEFAULT_PALETTE) -> dict[str, str]:
    """calendar id -> color from a palette, given calendars with .id and .kind."""
    order = PALETTES[palette][1]
    out, i = {}, 0
    for c in calendars:
        if c.kind == "holidays":
            out[c.id] = HOLIDAY_NAMES[0]
        else:
            out[c.id] = order[i % len(order)]
            i += 1
    return out


def solid(name: str) -> tuple[int, int, int]:
    """The single ink closest to a color, for text (patterns break up thin text)."""
    name = canonical(name)
    inks = GOOGLE[name][1] if name in GOOGLE else name
    if isinstance(inks, str):
        return PURE[inks]
    lum = lambda i: sum(SIMULATED[i])
    # The most used ink other than white; on a tie, the darker one.
    return PURE[min((i for i in inks if i != "white"), key=lambda i: (-inks.count(i), lum(i)))]


def text_on(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Readable text color on a fill drawn with color(...)."""
    for _, ink, placeholder in MIXES.values():
        if rgb == placeholder:
            return ink
    return BLACK if rgb in (PURE["yellow"], PURE["white"]) else WHITE


def accent(name: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(color to draw with, color for text drawn on top of it)."""
    rgb = color(name)
    return rgb, text_on(rgb)


def _inks(name: str) -> tuple[str, ...]:
    inks = GOOGLE[name][1] if name in GOOGLE else name
    return inks if isinstance(inks, tuple) else (inks,)


def recipe(name: str) -> str:
    """How a color is made, e.g. "75% red, 25% yellow"."""
    inks = _inks(name)
    if len(set(inks)) == 1:
        return f"solid {inks[0]} ink"
    order = ["red", "yellow", "green", "blue", "black", "white"]
    parts = sorted(set(inks), key=lambda i: (-inks.count(i), order.index(i)))
    if len(parts) == 2 and inks.count(parts[0]) == 2:
        text = f"{parts[0]} + {parts[1]}, half and half"
    else:
        text = ", ".join(f"{inks.count(i) * 25}% {i}" for i in parts)
    return text + ("; grainy up close" if "white" in inks else "")


def _hue_order(name: str) -> tuple:
    r, g, b = (v / 255 for v in _hex(GOOGLE[name][0])) if name in GOOGLE else PURE[name]
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    return (s < 0.15, (h + 0.1) % 1)  # grays last; start at magenta-red


def picker_groups(names: list[str] | None = None) -> list[dict]:
    """Picker colors grouped by how many inks they mix, each group by hue."""
    names = PICKER_NAMES if names is None else names
    titles = {1: "Solid ink", 2: "Two inks", 3: "Three inks"}
    groups = {}
    for n in names:
        groups.setdefault(len(set(_inks(n))), []).append(n)
    return [{"title": titles[k], "names": sorted(groups[k], key=_hue_order)} for k in sorted(groups)]


def swatches() -> dict[str, dict[str, str]]:
    """For the web UI: each picker color's Google color, its look on the panel and its recipe."""
    out = {}
    for name in PICKER_NAMES:
        panel = "#%02x%02x%02x" % _mean(_inks(name))
        out[name] = {"google": GOOGLE[name][0] if name in GOOGLE else panel, "panel": panel, "recipe": recipe(name)}
    return out


@lru_cache(maxsize=32)
def _pattern(inks: tuple[str, ...], size: tuple[int, int]) -> Image.Image:
    """The 2x2 ink pattern tiled over an image of the given size."""
    img = Image.new("RGB", (2, 2))
    img.putdata([PURE[i] for i in inks])
    # Double the tile across, then down, instead of pasting it pixel by pixel.
    while img.width < size[0]:
        wide = Image.new("RGB", (img.width * 2, img.height))
        wide.paste(img, (0, 0))
        wide.paste(img, (img.width, 0))
        img = wide
    while img.height < size[1]:
        tall = Image.new("RGB", (img.width, img.height * 2))
        tall.paste(img, (0, 0))
        tall.paste(img, (0, img.height))
        img = tall
    return img.crop((0, 0, *size))


def _where(img: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    """Mask of the pixels exactly equal to rgb."""
    bands = [band.point(lambda v, want=want: 255 * (v == want)) for band, want in zip(img.split(), rgb)]
    return ImageChops.multiply(ImageChops.multiply(bands[0], bands[1]), bands[2])


def apply_mixes(img: Image.Image) -> None:
    """Replace every mix placeholder in img with its ink pattern."""
    present = img.getcolors(1024)
    used = {rgb for _, rgb in present} if present else None
    for inks, _, placeholder in MIXES.values():
        if used is not None and placeholder not in used:
            continue
        img.paste(_pattern(inks, img.size), (0, 0), _where(img, placeholder))


def _palette_image(colors: dict[str, tuple[int, int, int]]) -> Image.Image:
    pal = Image.new("P", (1, 1))
    pal.putpalette([v for name in COLOR_NAMES for v in colors[name]])
    return pal


def to_panel(img: Image.Image) -> Image.Image:
    """Snap an RGB image to the 6 pure colors (no dithering) as a 'P' image."""
    return img.convert("RGB").quantize(palette=_palette_image(PURE), dither=Image.Dither.NONE)


def simulate(img: Image.Image) -> Image.Image:
    """Re-color a panel image with approximate real ink colors for previews."""
    p = to_panel(img)
    p.putpalette([v for name in COLOR_NAMES for v in SIMULATED[name]])
    return p.convert("RGB")
