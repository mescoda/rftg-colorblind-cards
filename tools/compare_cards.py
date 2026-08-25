#!/usr/bin/env python3
"""Build a single-file HTML review page for the patched card art.

Puts the bundled original next to the image/ override for every card the
marker pass touched, and lets the reviewer flip between them and apply a
color vision deficiency simulation without leaving the page. The simulation
uses the same Vienot projections as the analysis that motivated the markers,
baked into SVG color matrices so the browser does the work.

Requires Pillow. Run from the project root, after colorblind_cards.py:

    python3 tools/compare_cards.py
"""
import argparse
import base64
import io
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from PIL import Image, ImageChops, ImageDraw
except ImportError:
    sys.exit("This script needs Pillow: python3 -m pip install Pillow")

import colorblind_cards as cb

RGB2LMS = ((17.8824, 43.5161, 4.11935),
           (3.45565, 27.1554, 3.86714),
           (0.0299566, 0.184309, 1.46709))
LMS2RGB = ((0.080944, -0.130504, 0.116721),
           (-0.010248, 0.054019, -0.113614),
           (-0.000365, -0.004125, 0.693513))
PROJECTIONS = {
    "protan": ((0, 2.02344, -2.52581), (0, 1, 0), (0, 0, 1)),
    "deutan": ((1, 0, 0), (0.494207, 0, 1.24827), (0, 0, 1)),
    "tritan": ((1, 0, 0), (0, 1, 0), (-0.395913, 0.801109, 0)),
}
VISION_LABELS = [("none", "Normal"), ("deutan", "Deuteranopia"),
                 ("protan", "Protanopia"), ("tritan", "Tritanopia")]


def matmul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3))
                       for j in range(3)) for i in range(3))


def color_matrix(kind):
    """Row-major 4x5 feColorMatrix for one dichromacy simulation."""
    m = matmul(LMS2RGB, matmul(PROJECTIONS[kind], RGB2LMS))
    rows = ["%.6f %.6f %.6f 0 0" % m[i] for i in range(3)]
    rows.append("0 0 0 1 0")
    return " ".join(rows)


def psnr_outside_markers(original, patched, boxes):
    """Re-encode error on the pixels the pass was not supposed to touch."""
    keep = Image.new("L", original.size, 255)
    draw = ImageDraw.Draw(keep)
    for _good, (x0, y0, x1, y1), _w in boxes:
        draw.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], fill=0)
    diff = ImageChops.multiply(
        ImageChops.difference(original, patched).convert("L"), keep)
    hist = diff.histogram()
    pixels = float(original.size[0] * original.size[1])
    mse = sum(count * value ** 2 for value, count in enumerate(hist)) / pixels
    if mse == 0:
        return 99.0, 0
    peak = max(value for value, count in enumerate(hist) if count)
    return 10 * math.log10(255.0 ** 2 / mse), peak


def data_uri(blob):
    return "data:image/jpeg;base64," + base64.b64encode(blob).decode("ascii")


def marker_summary(card):
    rows = cb.row_markers(card)
    parts = ["%s:%s" % (band, "".join(cb.GOODS[g][0] for g in goods))
             for band, goods in sorted(rows.items(),
                                      key=lambda kv: cb.BANDS[kv[0]][0])]
    if card["good"] in cb.GOODS:
        parts.append("world:" + cb.GOODS[card["good"]][0])
    return " ".join(parts)


def escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


STYLE = """
:root { --cardw: 240px; }
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.5 -apple-system, "Helvetica Neue", Arial, sans-serif;
       background: #14161c; color: #dfe3ea; }
svg.filters { position: absolute; width: 0; height: 0; }
header { position: sticky; top: 0; z-index: 5; padding: 14px 20px 12px;
         background: #1b1e26; border-bottom: 1px solid #2c313d; }
h1 { margin: 0 0 4px; font-size: 17px; }
.sub { color: #8d97a8; font-size: 12px; margin-bottom: 12px; }
.controls { display: flex; flex-wrap: wrap; gap: 18px; align-items: center; }
.controls label { font-size: 12px; color: #a9b3c4; }
select, input[type=search] { background: #262b36; color: #e8ecf3;
    border: 1px solid #39404f; border-radius: 5px; padding: 5px 8px; font-size: 13px; }
input[type=range] { vertical-align: middle; }
.legend { display: flex; gap: 14px; align-items: center; font-size: 12px; color: #a9b3c4; }
.chip { display: inline-flex; align-items: center; gap: 5px; }
.chip i { width: 14px; height: 14px; display: inline-block; }
.chip i.circle { border-radius: 50%; }
.chip i.triangle { clip-path: polygon(50% 0, 100% 100%, 0 100%); }
.chip i.diamond { clip-path: polygon(50% 0, 100% 50%, 50% 100%, 0 50%); }
main { padding: 18px 20px 60px; display: grid; gap: 20px;
       grid-template-columns: repeat(auto-fill, minmax(calc(var(--cardw) * 2 + 14px), 1fr)); }
main.swap { grid-template-columns: repeat(auto-fill, minmax(var(--cardw), 1fr)); }
figure { margin: 0; }
figcaption { font-size: 12px; color: #aab4c5; margin-bottom: 6px;
             display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; }
figcaption b { color: #f0f3f8; font-weight: 600; }
figcaption .mk { color: #7fcd8f; font-family: ui-monospace, Menlo, monospace; }
figcaption .q { color: #75808f; font-family: ui-monospace, Menlo, monospace; }
.pair { display: flex; gap: 14px; }
main.swap .pair { position: relative; display: block; }
.shot { position: relative; flex: 1 1 0; min-width: 0; }
main.swap .shot { position: absolute; inset: 0; }
main.swap .shot.before { opacity: 0; }
main.swap figure:hover .shot.before { opacity: 1; }
main.swap figure:hover .shot.after { opacity: 0; }
main.swap .pair { height: calc(var(--cardw) * 520 / 372); }
.shot img { display: block; width: 100%; height: auto; border-radius: 4px;
            background: #0d1018; }
main.swap .shot img { width: var(--cardw); }
/* Top right is the only corner clear of the phase gutter, the cost badge,
   the world chip and the VP badge, so the label cannot hide a marker. */
.shot span { position: absolute; right: 5px; top: 5px; font-size: 9px;
    letter-spacing: .05em; text-transform: uppercase; padding: 1px 5px;
    border-radius: 3px; background: rgba(10,12,18,.8); color: #c3ccda; }
main.swap .shot span { background: rgba(10,12,18,.92); color: #e8ecf3; }
main.vision-deutan .shot img { filter: url(#f-deutan); }
main.vision-protan .shot img { filter: url(#f-protan); }
main.vision-tritan .shot img { filter: url(#f-tritan); }
.empty { color: #8d97a8; padding: 30px 0; }
"""

SCRIPT = """
const grid = document.getElementById('grid');
// Absent when the page is built without the vision control; the simulation
// filters and the #deutan style hashes keep working either way.
const vision = document.getElementById('vision');
const view = document.getElementById('view');
const width = document.getElementById('width');
const good = document.getElementById('good');
const q = document.getElementById('q');
const count = document.getElementById('count');
const VISION_MODES = ['none', 'deutan', 'protan', 'tritan'];
let visionMode = 'none';

function applyVision() {
  if (vision) visionMode = vision.value;
  grid.classList.remove('vision-deutan', 'vision-protan', 'vision-tritan');
  if (visionMode !== 'none') grid.classList.add('vision-' + visionMode);
}
function applyView() { grid.classList.toggle('swap', view.value === 'swap'); }
function applyWidth() {
  document.documentElement.style.setProperty('--cardw', width.value + 'px');
}
function applyFilter() {
  const needle = q.value.trim().toLowerCase();
  const want = good.value;
  let shown = 0;
  for (const fig of grid.children) {
    if (!fig.dataset.name) continue;
    const okGood = want === 'all' || fig.dataset.good === want;
    const okText = !needle || fig.dataset.name.toLowerCase().includes(needle)
                   || fig.dataset.index === needle;
    const show = okGood && okText;
    fig.hidden = !show;
    if (show) shown++;
  }
  count.textContent = shown;
}
// #deutan, #swap etc. make a particular view linkable.
function readHash() {
  for (const token of location.hash.replace('#', '').split(',')) {
    if (VISION_MODES.includes(token)) {
      visionMode = token;
      if (vision) vision.value = token;
    }
    if ([...view.options].some(o => o.value === token)) view.value = token;
  }
}
if (vision) vision.addEventListener('change', applyVision);
view.addEventListener('change', applyView);
width.addEventListener('input', applyWidth);
good.addEventListener('change', applyFilter);
q.addEventListener('input', applyFilter);
window.addEventListener('hashchange', () => { readHash(); applyVision(); applyView(); });
readHash();
applyVision(); applyView(); applyWidth(); applyFilter();
"""


def build(cards, images, args):
    entries = []
    for index, card in enumerate(cards):
        boxes = cb.marker_boxes(card)
        patched_path = os.path.join(args.images, "card%03d.jpg" % index)
        if not boxes or not os.path.exists(patched_path):
            continue
        if args.only and index not in args.only:
            continue
        original_blob = images[index]
        patched_blob = open(patched_path, "rb").read()
        with Image.open(io.BytesIO(original_blob)) as a, \
                Image.open(patched_path) as b:
            db, peak = psnr_outside_markers(a.convert("RGB"), b.convert("RGB"),
                                            boxes)
        entries.append({
            "index": index, "name": card["name"], "good": card["good"] or "-",
            "markers": marker_summary(card), "psnr": db, "peak": peak,
            "before": data_uri(original_blob), "after": data_uri(patched_blob),
        })

    filters = "\n".join(
        '  <filter id="f-%s" color-interpolation-filters="sRGB">'
        '<feColorMatrix type="matrix" values="%s"/></filter>'
        % (kind, color_matrix(kind)) for kind in ("deutan", "protan", "tritan"))

    # The simulation filters, styles and script stay in the page regardless; only
    # the picker is optional, so #deutan still reaches it when it is left out.
    if args.with_vision:
        vision_control = (
            '    <label>Vision\n      <select id="vision">\n%s\n'
            '      </select>\n    </label>\n'
            % "\n".join('        <option value="%s">%s</option>' % (value, label)
                        for value, label in VISION_LABELS))
        vision_note = ("Vision simulation applies a Vienot dichromacy "
                       "projection in the browser.")
    else:
        vision_control = ""
        vision_note = ""

    good_options = "\n".join(
        '        <option value="%s">%s</option>' % (g, g.title())
        for g in cb.ORDER)

    legend = "\n".join(
        '      <span class="chip"><i class="%s" style="background:%s"></i>%s %s</span>'
        % (cb.GOODS[g][1], "#%02x%02x%02x" % cb.GOODS[g][2], cb.GOODS[g][0],
           g.title())
        for g in cb.ORDER)

    figures = []
    for e in entries:
        figures.append(
            '  <figure data-index="%d" data-name="%s" data-good="%s">\n'
            '    <figcaption><b>%03d</b> %s <span class="mk">%s</span>'
            '<span class="q">%.1f dB / max %d</span></figcaption>\n'
            '    <div class="pair">\n'
            '      <div class="shot before"><img loading="lazy" src="%s" alt="original %03d"><span>before</span></div>\n'
            '      <div class="shot after"><img loading="lazy" src="%s" alt="patched %03d"><span>after</span></div>\n'
            '    </div>\n'
            '  </figure>'
            % (e["index"], escape(e["name"]), e["good"], e["index"],
               escape(e["name"]), escape(e["markers"]), e["psnr"], e["peak"],
               e["before"], e["index"], e["after"], e["index"]))

    worst = min((e["psnr"] for e in entries), default=0)
    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RFTG card marker review</title>
<style>%s</style>
</head>
<body>
<svg class="filters" aria-hidden="true"><defs>
%s
</defs></svg>

<header>
  <h1>RFTG card marker review &mdash; <span id="count">%d</span> / %d cards</h1>
  <div class="sub">
    before = original art from images.data, after = override in image/.
    Lowest re-encode accuracy outside the markers is %.1f dB. %s
  </div>
  <div class="controls">
%s    <label>Compare
      <select id="view">
        <option value="side">Side by side</option>
        <option value="swap">Hover swap</option>
      </select>
    </label>
    <label>Width <input id="width" type="range" min="150" max="372" step="6" value="240"></label>
    <label>Good
      <select id="good">
        <option value="all">All</option>
%s
      </select>
    </label>
    <label>Search <input id="q" type="search" placeholder="name or index"></label>
    <span class="legend">
%s
    </span>
  </div>
</header>

<main id="grid">
%s
</main>

<script>%s</script>
</body>
</html>
""" % (STYLE, filters, len(entries), len(entries), worst, vision_note,
       vision_control, good_options, legend, "\n".join(figures), SCRIPT)
    return html, entries


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cards", default="cards.txt")
    ap.add_argument("--bundle", default="images.data")
    ap.add_argument("--images", default="image")
    ap.add_argument("--out", default="card-comparison.html")
    ap.add_argument("--only", type=int, nargs="+", metavar="INDEX",
                    help="restrict the page to these card indices")
    ap.add_argument("--with-vision", action="store_true",
                    help="expose the color vision simulation picker")
    args = ap.parse_args()

    if not os.path.isdir(args.images):
        sys.exit("%s/ not found; run tools/colorblind_cards.py first" % args.images)

    cards = cb.parse_cards(args.cards)
    images = cb.read_bundle(args.bundle)
    html, entries = build(cards, images, args)
    if not entries:
        sys.exit("no patched cards found in %s/" % args.images)

    with open(args.out, "w") as fh:
        fh.write(html)
    print("wrote %s (%d card pair(s), %.1f MB)"
          % (args.out, len(entries), os.path.getsize(args.out) / 1048576.0))


if __name__ == "__main__":
    main()
