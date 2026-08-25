#!/usr/bin/env python3
"""Generate card images that identify good types by shape and letter, not color alone.

Race for the Galaxy encodes the four good types purely as color, and two of
those colors collapse onto each other for anyone with a red-green deficiency:
Rare and Genes sit at dE 64 apart in normal vision but only 23 apart under
simulated deuteranopia. This script stamps a shape-plus-letter marker next to
every good reference on a card so the information survives without color.

Output goes to image/, which gui.c already searches before falling back to
images.data (see load_one_image), so nothing else in the tree has to change.
Only cards that need a marker are written; the rest keep loading from the
bundle and are never re-encoded. Delete image/ to revert.

Requires Pillow. Run from the project root:

    python3 tools/colorblind_cards.py
"""
import argparse
import io
import os
import re
import sys

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
except ImportError:
    sys.exit("This script needs Pillow: python3 -m pip install Pillow")

# good type -> (letter, silhouette, fill). Fills match the original card art;
# the shape and letter are what carry the meaning when color does not.
GOODS = {
    "NOVELTY": ("N", "circle",   (0x80, 0xD2, 0xF0)),
    "RARE":    ("R", "square",   (0xDA, 0xA5, 0x63)),
    "GENE":    ("G", "triangle", (0x80, 0xCB, 0x2B)),
    "ALIEN":   ("A", "diamond",  (0xFB, 0xF0, 0x10)),
}
ORDER = ["NOVELTY", "RARE", "GENE", "ALIEN"]

CARD_W, CARD_H = 372, 520

# Vertical extent of each phase tab down the left edge. Measured off the art and
# identical on all 236 cards.
BANDS = {"I": (122, 169), "II": (184, 231), "III": (246, 293),
         "$": (308, 355), "IV": (370, 417), "V": (432, 479)}

# Both marker areas are plain dark border on every card. The guards below hold
# that claim to account at runtime: the brightest region any marker covers
# measures 36, and none contains saturated pixels. The chip stops short of x=23
# because expansion cards carry a small colored square at (24,496)-(28,501).
GUTTER = (2, 24)
WORLD_CHIP = (2, 482, 22, 514)
DARK_LIMIT = 60
CONTENT_MIN_VALUE = 110      # a pixel this bright and this saturated is content,
CONTENT_MIN_CHROMA = 45      # not border, however few of them there are
CONTENT_MAX_PIXELS = 8       # slack for JPEG ringing; real elements exceed 25

ROW_MARKER_MAX_H = 28
OUTLINE = (255, 255, 255)
INK = (10, 10, 10)

FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_fonts = {}


def font(size):
    if size not in _fonts:
        for path in FONT_PATHS:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def parse_cards(path):
    """Card records in the order gui.c indexes them (design index == position)."""
    cards, cur = [], None
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("N:"):
                cur = {"name": line[2:], "good": None, "powers": []}
                cards.append(cur)
            elif cur is None:
                continue
            elif line.startswith("G:"):
                cur["good"] = line[2:]
            elif line.startswith("P:"):
                fields = line[2:].split(":")
                cur["powers"].append((fields[0], fields[1].strip()))
    return cards


def read_bundle(path):
    """Card index -> encoded image bytes, from the images.data container."""
    data = open(path, "rb").read()
    if data[:4] != b"RFTG":
        sys.exit("%s: missing RFTG header" % path)
    images = {}
    pos = 4
    while pos < len(data):
        kind = data[pos]
        pos += 1
        if kind == 0:
            break
        if kind == 1:
            index = int(data[pos:pos + 4].split(b"\0")[0])
            pos += 4
        elif kind == 2:
            index = None
        elif kind in (3, 4, 5):
            index = None
            pos += 3
        else:
            sys.exit("%s: bad image type %d at offset %d" % (path, kind, pos - 1))
        size = int(data[pos:pos + 8].split(b"\0")[0])
        pos += 8
        if kind == 1:
            images[index] = data[pos:pos + size]
        pos += size
    return images


def goods_in(code):
    return [g for g in ORDER
            if re.search(r"(?:^|[ |_])" + g + r"(?:[ |]|$)", code)]


def band_for(phase, code):
    if phase == "4":
        return "$" if "TRADE" in code else "IV"
    return {"1": "I", "2": "II", "3": "III", "5": "V"}.get(phase)


def row_markers(card):
    """Phase band -> good types that band's powers refer to, in palette order."""
    rows = {}
    for phase, code in card["powers"]:
        band = band_for(phase, code)
        if band is None:
            continue
        hits = goods_in(code)
        # PRODUCE and WINDFALL without a named good act on the world's own good.
        if not hits and card["good"] in GOODS and \
                re.search(r"(?:^|[ |])(PRODUCE|WINDFALL)(?:[ |]|$)", code):
            hits = [card["good"]]
        for good in hits:
            rows.setdefault(band, [])
            if good not in rows[band]:
                rows[band].append(good)
    return rows


def marker_boxes(card):
    """Every marker to draw, as (good, box, outline_width)."""
    out = []
    gx0, gx1 = GUTTER
    for band, goods in row_markers(card).items():
        y0, y1 = BANDS[band]
        count = len(goods)
        height = min(ROW_MARKER_MAX_H, (y1 - y0) // count)
        stack = height * count + 2 * (count - 1)
        top = (y0 + y1) // 2 - stack // 2
        for i, good in enumerate(goods):
            y = top + i * (height + 2)
            out.append((good, (gx0, y, gx1, y + height), 2))
    if card["good"] in GOODS:
        out.append((card["good"], WORLD_CHIP, 3))
    return out


def content_pixels(im):
    """Count pixels bright and saturated enough to be a drawn element."""
    r, g, b = im.split()
    high = ImageChops.lighter(ImageChops.lighter(r, g), b)
    low = ImageChops.darker(ImageChops.darker(r, g), b)
    bright = high.point(lambda v: 255 if v > CONTENT_MIN_VALUE else 0)
    chroma = ImageChops.difference(high, low).point(
        lambda v: 255 if v > CONTENT_MIN_CHROMA else 0)
    return ImageChops.multiply(bright, chroma).histogram()[255]


def check_clear(im, box, card_index, name):
    """Refuse to draw over anything that is not plain dark border.

    Mean brightness alone misses small elements such as the expansion square,
    which is why saturated pixels are counted separately.
    """
    crop = im.crop((box[0], box[1], box[2] + 1, box[3] + 1))
    mean = ImageStat.Stat(crop.convert("L")).mean[0]
    if mean > DARK_LIMIT:
        sys.exit("card %03d (%s): marker box %s covers content "
                 "(brightness %.1f > %d), refusing to draw"
                 % (card_index, name, box, mean, DARK_LIMIT))
    found = content_pixels(crop)
    if found > CONTENT_MAX_PIXELS:
        sys.exit("card %03d (%s): marker box %s covers %d colored pixel(s), "
                 "refusing to draw" % (card_index, name, box, found))


def draw_marker(draw, box, good, width):
    letter, shape, fill = GOODS[good]
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    span = min(x1 - x0, y1 - y0)
    letter_y, scale = cy, 0.80

    if shape == "circle":
        draw.ellipse([x0, y0, x1, y1], fill=fill, outline=OUTLINE, width=width)
    elif shape == "square":
        draw.rounded_rectangle([x0, y0, x1, y1], radius=max(2, span // 6),
                               fill=fill, outline=OUTLINE, width=width)
        scale = 0.84
    elif shape == "triangle":
        draw.polygon([(cx, y0), (x1, y1), (x0, y1)], fill=fill,
                     outline=OUTLINE, width=width)
        letter_y = y0 + (y1 - y0) * 0.66      # a triangle's centroid sits low
        scale = 0.56
    else:
        draw.polygon([(cx, y0), (x1, cy), (cx, y1), (x0, cy)], fill=fill,
                     outline=OUTLINE, width=width)
        scale = 0.60

    draw.text((cx, letter_y + 1), letter, font=font(max(8, int(span * scale))),
              fill=INK, anchor="mm")


def annotate(im, card, index, boxes):
    for _good, box, _width in boxes:
        check_clear(im, box, index, card["name"])
    out = im.copy()
    draw = ImageDraw.Draw(out)
    for good, box, width in boxes:
        draw_marker(draw, box, good, width)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cards", default="cards.txt")
    ap.add_argument("--bundle", default="images.data")
    ap.add_argument("--out", default="image")
    ap.add_argument("--quality", type=int, default=95)
    ap.add_argument("--dry-run", action="store_true",
                    help="run the safety checks and report, write nothing")
    args = ap.parse_args()

    cards = parse_cards(args.cards)
    images = read_bundle(args.bundle)
    missing = [i for i in range(len(cards)) if i not in images]
    if missing:
        sys.exit("%s has no image for card index %s" % (args.bundle, missing[:5]))

    if not args.dry_run:
        os.makedirs(args.out, exist_ok=True)

    written = markers = 0
    for index, card in enumerate(cards):
        boxes = marker_boxes(card)
        if not boxes:
            continue
        with Image.open(io.BytesIO(images[index])) as src:
            im = src.convert("RGB")
        if im.size != (CARD_W, CARD_H):
            sys.exit("card %03d: unexpected size %s" % (index, im.size))
        patched = annotate(im, card, index, boxes)
        markers += len(boxes)
        if not args.dry_run:
            patched.save(os.path.join(args.out, "card%03d.jpg" % index),
                         quality=args.quality, subsampling=0)
        written += 1

    verb = "would write" if args.dry_run else "wrote"
    print("%s %d card(s) carrying %d marker(s) to %s/ "
          "(%d card(s) left to images.data)"
          % (verb, written, markers, args.out, len(cards) - written))


if __name__ == "__main__":
    main()
