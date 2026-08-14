"""`make-samples` — synthetic invoice scans to try the pipeline on.

Four of them are clean enough for a vision model. The fifth is a bad scan:
blurred, noisy and skewed, the way a fax of a photocopy looks. No model reads
it, which is exactly the case the human expert is there for.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1000, 1300

INVOICES = [
    (
        "north-harbor-logistics",
        "Northharbor Logistics BV",
        "INV-2026-0841",
        "2026-07-03",
        "4820.00",
        "EUR",
        "Freight forwarding, Rotterdam–Gdansk",
    ),
    (
        "bluepeak-analytics",
        "BluePeak Analytics Ltd",
        "BP-11627",
        "2026-07-11",
        "1295.50",
        "GBP",
        "Data quality audit, June retainer",
    ),
    (
        "cedarworks-supply",
        "Cedarworks Supply Co.",
        "2026-CW-3390",
        "2026-07-18",
        "738.25",
        "USD",
        "Office furniture, 6 units",
    ),
    (
        "meridian-labs",
        "Meridian Labs GmbH",
        "ML-2026-00214",
        "2026-07-22",
        "12400.00",
        "EUR",
        "Instrument calibration, Q3",
    ),
    (
        "velocity-print",
        "Velocity Print & Mail",
        "VP-58812",
        "2026-07-29",
        "2140.75",
        "USD",
        "Statement print run, 18k envelopes",
    ),
]

#: The last one comes out of the scanner unreadable.
DEGRADED = "velocity-print"


def _font(size: int, *, bold: bool = False):
    candidates = [
        f"/System/Library/Fonts/Supplemental/{'Arial Bold' if bold else 'Arial'}.ttf",
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render(vendor, number, date, total, currency, description) -> Image.Image:
    """One plain, believable invoice."""
    image = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(image)
    head, label, body, big = (
        _font(44, bold=True),
        _font(22),
        _font(26),
        _font(38, bold=True),
    )

    draw.text((70, 70), vendor, font=head, fill="black")
    draw.text((70, 130), "INVOICE", font=body, fill="#444444")
    draw.line((70, 180, W - 70, 180), fill="#999999", width=2)

    for row, (name, value) in enumerate(
        [("Invoice number", number), ("Issue date", date), ("Payment terms", "Net 30")]
    ):
        y = 220 + row * 70
        draw.text((70, y), name.upper(), font=label, fill="#777777")
        draw.text((70, y + 28), value, font=body, fill="black")

    draw.text((70, 470), "DESCRIPTION", font=label, fill="#777777")
    draw.text((70, 505), description, font=body, fill="black")

    draw.line((70, 940, W - 70, 940), fill="#999999", width=2)
    draw.text((70, 970), "TOTAL DUE", font=label, fill="#777777")
    draw.text((70, 1005), f"{total} {currency}", font=big, fill="black")
    draw.text(
        (70, H - 110),
        "Payable by bank transfer. Quote the invoice number as reference.",
        font=label,
        fill="#777777",
    )
    return image


def degrade(image: Image.Image, seed: int = 7) -> Image.Image:
    """Make it look like a fax of a photocopy, stamped over the invoice number.

    Deliberately calibrated: a careful human at full zoom still gets every
    field out of this, an OCR model does not — which is the whole point of
    the escalation path.
    """
    rng = random.Random(seed)
    draw = ImageDraw.Draw(image)
    stamp = Image.new("RGBA", (520, 190))
    ImageDraw.Draw(stamp).text(
        (10, 10), "PAID", font=_font(150, bold=True), fill=(150, 40, 40, 130)
    )
    ImageDraw.Draw(stamp).rectangle(
        (0, 0, 460, 175), outline=(150, 40, 40, 130), width=8
    )
    image.paste(
        stamp.rotate(-14, expand=True), (150, 190), stamp.rotate(-14, expand=True)
    )
    draw.line((60, 300, 700, 250), fill="#bbbbbb", width=3)  # fold crease

    small = image.resize((W // 2, H // 2), Image.BILINEAR)
    small = small.rotate(2.4, resample=Image.BILINEAR, fillcolor="white")
    small = small.filter(ImageFilter.GaussianBlur(0.9))
    pixels = small.load()
    for x in range(small.width):
        for y in range(small.height):
            noise = rng.randint(-38, 38)
            pixels[x, y] = tuple(max(0, min(255, c + noise)) for c in pixels[x, y])
    return small.resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(0.6))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="make-samples", description="Write sample invoice scans."
    )
    parser.add_argument("out", type=Path, nargs="?", default=Path("documents/inbox"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    for slug, vendor, number, date, total, currency, description in INVOICES:
        image = render(vendor, number, date, total, currency, description)
        if slug == DEGRADED:
            image = degrade(image)
        path = args.out / f"{slug}.png"
        image.save(path)
        print(f"{path}{'  (bad scan)' if slug == DEGRADED else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
