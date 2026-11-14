#!/usr/bin/env python3
"""Generate CachyOS Benchmark icons from the header cogs drawn in app/theme.py.

Same gear geometry as HeaderBar (big brass cog 12 teeth, small copper cog 9 teeth),
written as SVG, then rasterised with QtSvg. Run:  QT_QPA_PLATFORM=offscreen python make_icons.py [outdir]
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent)

# Header geometry (theme.py HeaderBar, cy = 0)
BIG = dict(cx=46.0, cy=0.0, r_out=27.0, r_in=21.5, teeth=12, hole=7.5, ring=14.0, phase=0.0)
SMALL = dict(cx=80.0, cy=15.0, r_out=15.0, r_in=11.5, teeth=9, hole=4.5, ring=0.0, phase=0.2)
# Simplified cogs for 16-32 px: fewer, deeper teeth, bigger hubs, no inner ring
BIG_S = dict(cx=46.0, cy=0.0, r_out=27.5, r_in=20.0, teeth=8, hole=8.0, ring=0.0, phase=0.0)
SMALL_S = dict(cx=80.5, cy=15.5, r_out=16.0, r_in=11.0, teeth=6, hole=4.5, ring=0.0, phase=0.25)


def gear_d(cx, cy, r_out, r_in, teeth, hole, phase=0.0, **_):
    step = 2 * math.pi / teeth
    pts = []
    for i in range(teeth):
        a0 = phase + i * step
        for frac, r in ((0.0, r_in), (0.18, r_out), (0.50, r_out), (0.68, r_in)):
            a = a0 + frac * step
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d = "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts) + " Z"
    # hub hole as a second subpath (evenodd)
    d += (f" M{cx + hole:.3f},{cy:.3f} A{hole},{hole} 0 1,0 {cx - hole:.3f},{cy:.3f}"
          f" A{hole},{hole} 0 1,0 {cx + hole:.3f},{cy:.3f} Z")
    return d


def bbox(g1, g2):
    x0 = min(g1["cx"] - g1["r_out"], g2["cx"] - g2["r_out"])
    x1 = max(g1["cx"] + g1["r_out"], g2["cx"] + g2["r_out"])
    y0 = min(g1["cy"] - g1["r_out"], g2["cy"] - g2["r_out"])
    y1 = max(g1["cy"] + g1["r_out"], g2["cy"] + g2["r_out"])
    return x0, y0, x1, y1


def cogs_svg(big, small, fit, center, stroke=1.0, ring=True, shadow=False, ids="a"):
    """Return (<defs>, <g>) placing both cogs to fit `fit` px centred on `center`."""
    x0, y0, x1, y1 = bbox(big, small)
    s = fit / max(x1 - x0, y1 - y0)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    tx, ty = center[0] - mx * s, center[1] - my * s
    b, m = big, small
    defs = f"""
    <radialGradient id="brass{ids}" gradientUnits="userSpaceOnUse" cx="{b['cx']-6}" cy="{b['cy']-8}" r="34" fx="{b['cx']-6}" fy="{b['cy']-8}">
      <stop offset="0" stop-color="#f0cf86"/><stop offset="0.55" stop-color="#b5893b"/><stop offset="1" stop-color="#6a4c1c"/>
    </radialGradient>
    <radialGradient id="copper{ids}" gradientUnits="userSpaceOnUse" cx="{m['cx']-4}" cy="{m['cy']-5}" r="20" fx="{m['cx']-4}" fy="{m['cy']-5}">
      <stop offset="0" stop-color="#eaa478"/><stop offset="0.6" stop-color="#b8693d"/><stop offset="1" stop-color="#5e2f18"/>
    </radialGradient>"""
    body = [f'<g transform="translate({tx:.3f},{ty:.3f}) scale({s:.5f})">']
    if shadow:
        body.append(f'<g transform="translate(1.2,1.8)" fill="#000" fill-opacity="0.45" fill-rule="evenodd">'
                    f'<path d="{gear_d(**b)}"/><path d="{gear_d(**m)}"/></g>')
    body.append(f'<path d="{gear_d(**b)}" fill="url(#brass{ids})" fill-rule="evenodd" '
                f'stroke="#000" stroke-opacity="0.67" stroke-width="{stroke}" stroke-linejoin="round"/>')
    if ring and b.get("ring"):
        body.append(f'<circle cx="{b["cx"]}" cy="{b["cy"]}" r="{b["ring"]}" fill="none" '
                    f'stroke="#3c280c" stroke-opacity="0.78" stroke-width="1.2"/>')
    body.append(f'<path d="{gear_d(**m)}" fill="url(#copper{ids})" fill-rule="evenodd" '
                f'stroke="#000" stroke-opacity="0.67" stroke-width="{stroke}" stroke-linejoin="round"/>')
    body.append("</g>")
    return defs, "\n  ".join(body)


def plate_svg(size=512, small=False, full_bleed=False):
    big, sm = (BIG_S, SMALL_S) if small else (BIG, SMALL)
    pad = 0 if full_bleed else size * 0.04
    w = size - 2 * pad
    rx = 0 if full_bleed else w * 0.2
    rim = max(size * 0.012, 1.0) if not small else 1.0 * size / 32
    fit = w * (0.90 if small else 0.80)
    defs, g = cogs_svg(big, sm, fit, (size / 2, size / 2 + (0 if small else size * 0.01)),
                       stroke=1.4 if small else 0.8, ring=not small, shadow=not small, ids="p")
    rings = ""
    if not small:
        rings = "".join(
            f'<circle cx="{size*0.5}" cy="{size*0.5}" r="{size*(0.12+i*0.035):.2f}" fill="none" '
            f'stroke="#d4a94f" stroke-opacity="0.05" stroke-width="{size*0.003:.2f}"/>' for i in range(11))
    bevel = ""
    if not small and not full_bleed:
        i = size * 0.025
        bevel = (f'<rect x="{pad+i}" y="{pad+i}" width="{w-2*i}" height="{w-2*i}" rx="{rx-i}" fill="none" '
                 f'stroke="#d4a94f" stroke-opacity="0.18" stroke-width="{size*0.004:.2f}"/>')
    rim_el = "" if full_bleed else (
        f'<rect x="{pad+rim/2}" y="{pad+rim/2}" width="{w-rim}" height="{w-rim}" rx="{rx-rim/2}" '
        f'fill="none" stroke="url(#rim)" stroke-width="{rim:.2f}"/>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <title>CachyOS Benchmark</title>
  <defs>{defs}
    <linearGradient id="plate" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2c241b"/><stop offset="0.6" stop-color="#1d1813"/><stop offset="1" stop-color="#120e0b"/>
    </linearGradient>
    <linearGradient id="rim" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#e8c77e"/><stop offset="0.5" stop-color="#b5893b"/><stop offset="1" stop-color="#6e5428"/>
    </linearGradient>
  </defs>
  <rect x="{pad}" y="{pad}" width="{w}" height="{w}" rx="{rx}" fill="url(#plate)"/>
  {rings}{bevel}{rim_el}
  {g}
</svg>
"""


def plain_svg(size=512, small=False):
    big, sm = (BIG_S, SMALL_S) if small else (BIG, SMALL)
    defs, g = cogs_svg(big, sm, size * (0.98 if small else 0.94), (size / 2, size / 2),
                       stroke=1.4 if small else 0.8, ring=not small, ids="t")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <title>CachyOS Benchmark</title>
  <defs>{defs}
  </defs>
  {g}
</svg>
"""


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
    from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QLinearGradient,
                               QPainter, QPen, QRadialGradient, QBrush)
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / "png"
    png.mkdir(exist_ok=True)

    svgs = {
        "icon.svg": plain_svg(512),
        "icon-small.svg": plain_svg(32, small=True),
        "icon-plate.svg": plate_svg(512),
        "icon-plate-small.svg": plate_svg(32, small=True),
    }
    for name, text in svgs.items():
        (OUT / name).write_text(text)

    def render(svg_text: str, px: int, dest: Path) -> QImage:
        r = QSvgRenderer(QByteArray(svg_text.encode()))
        img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r.render(p, QRectF(0, 0, px, px))
        p.end()
        img.save(str(dest))
        return img

    for px in (16, 24, 32, 48, 64, 128, 256, 512):
        small = px <= 32
        render(plain_svg(px if small else 512, small), px, png / f"cachy-bench-{px}.png")
        render(plate_svg(px if small else 512, small), px, png / f"cachy-bench-plate-{px}.png")

    # apple-touch-icon: opaque full-bleed plate (iOS rounds the corners itself)
    render(plate_svg(512, full_bleed=True), 180, OUT / "apple-touch-icon.png")
    # favicon.ico 16/32/48 from the plated variant
    from PIL import Image
    ims = [Image.open(png / f"cachy-bench-plate-{s}.png").convert("RGBA") for s in (16, 32, 48)]
    ims[2].save(OUT / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)],
                append_images=ims[:2])
    for s in (16, 32):
        Image.open(png / f"cachy-bench-plate-{s}.png").save(OUT / f"favicon-{s}.png")

    # GitHub social preview 1280x640
    W, H = 1280, 640
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    g = QLinearGradient(0, 0, 0, H)
    g.setColorAt(0.0, QColor("#2a221a")); g.setColorAt(0.6, QColor("#1d1813")); g.setColorAt(1.0, QColor("#120e0b"))
    p.fillRect(0, 0, W, H, g)
    vg = QRadialGradient(QPointF(W * 0.32, H * 0.5), W * 0.75)
    vg.setColorAt(0.0, QColor(0, 0, 0, 0)); vg.setColorAt(1.0, QColor(0, 0, 0, 150))
    p.fillRect(0, 0, W, H, vg)
    p.setPen(QPen(QColor(212, 169, 79, 16), 1.2))
    for i in range(46):
        rr = 60 + i * 13
        p.drawEllipse(QPointF(W - 170, H / 2), rr, rr)
    # frame
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#b5893b"), 3)); p.drawRoundedRect(QRectF(22, 22, W - 44, H - 44), 18, 18)
    p.setPen(QPen(QColor(212, 169, 79, 60), 1.2)); p.drawRoundedRect(QRectF(32, 32, W - 64, H - 64), 12, 12)
    for x, y in ((48, 48), (W - 48, 48), (48, H - 48), (W - 48, H - 48)):
        rg = QRadialGradient(QPointF(x - 2.5, y - 2.5), 10)
        rg.setColorAt(0.0, QColor("#f6dc9c")); rg.setColorAt(0.45, QColor("#b5893b")); rg.setColorAt(1.0, QColor("#4a3412"))
        p.setPen(QPen(QColor(0, 0, 0, 150), 1)); p.setBrush(QBrush(rg)); p.drawEllipse(QPointF(x, y), 7, 7)
    # cogs
    r = QSvgRenderer(QByteArray(plain_svg(512).encode()))
    r.render(p, QRectF(70, H / 2 - 210, 420, 420))
    # title
    fams = ["Noto Serif Display", "Noto Serif", "Cormorant Garamond", "DejaVu Serif", "serif"]
    tf = QFont(); tf.setFamilies(fams); tf.setPixelSize(86); tf.setWeight(QFont.Weight.Bold)
    tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
    fm = QFontMetricsF(tf)
    tx = 520
    lines = [("CachyOS", H / 2 - 40), ("Benchmark", H / 2 + 52)]
    p.setFont(tf)
    for text, y in lines:
        p.setPen(QColor(0, 0, 0, 190)); p.drawText(QPointF(tx + 3, y + 3), text)
        tg = QLinearGradient(0, y - fm.ascent(), 0, y)
        tg.setColorAt(0.0, QColor("#f6dc9c")); tg.setColorAt(1.0, QColor("#c99a46"))
        p.setPen(QPen(QBrush(tg), 1)); p.drawText(QPointF(tx, y), text)
    # rule + subtitle
    yy = H / 2 + 92
    lg = QLinearGradient(QPointF(tx, yy), QPointF(W - 90, yy))
    lg.setColorAt(0.0, QColor(212, 169, 79, 220)); lg.setColorAt(1.0, QColor(212, 169, 79, 0))
    p.setPen(QPen(QBrush(lg), 2)); p.drawLine(QPointF(tx, yy), QPointF(W - 90, yy))
    p.setPen(QPen(QBrush(lg), 1)); p.drawLine(QPointF(tx, yy + 6), QPointF(W - 90, yy + 6))
    sf = QFont(); sf.setFamilies(fams); sf.setPixelSize(30); sf.setItalic(True)
    p.setFont(sf); p.setPen(QColor("#e0c79a"))
    p.drawText(QPointF(tx, yy + 50), "Track performance across rolling updates")
    sf2 = QFont(); sf2.setFamilies(fams); sf2.setPixelSize(22); sf2.setWeight(QFont.Weight.DemiBold)
    sf2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
    p.setFont(sf2); p.setPen(QColor("#b5893b"))
    p.drawText(QPointF(tx, yy + 92), "BOTLABS USA  ·  FREE & OPEN SOURCE")
    p.end()
    img.save(str(OUT / "social-preview.png"))
    print("ok", OUT)


if __name__ == "__main__":
    main()
