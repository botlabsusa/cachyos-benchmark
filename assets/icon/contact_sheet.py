import sys
from pathlib import Path
from PIL import Image, ImageDraw
d = Path(sys.argv[1]); out = Path(sys.argv[2])
sizes = [16, 24, 32, 48, 64, 128, 256]
W, H = 1500, 1100
sheet = Image.new("RGBA", (W, H), (40, 40, 44, 255))
dr = ImageDraw.Draw(sheet)
dr.rectangle((0, H // 2, W, H), fill=(236, 236, 232, 255))
def row(prefix, y0):
    x = 20
    for s in sizes:
        im = Image.open(d / "png" / f"{prefix}-{s}.png")
        sheet.alpha_composite(im, (x, y0))
        if s <= 48:
            z = im.resize((s * 4, s * 4), Image.NEAREST)
            sheet.alpha_composite(z, (x, y0 + s + 10))
            x += max(s, s * 4) + 20
        else:
            x += s + 20
for i, prefix in enumerate(("cachy-bench", "cachy-bench-plate")):
    row(prefix, 15 + i * 275)
    row(prefix, H // 2 + 15 + i * 275)
sheet.convert("RGB").save(out)
