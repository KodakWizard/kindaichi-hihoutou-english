"""Replace Memo1 Day 1 text in a separate Combined1 test build."""
import argparse
from pathlib import Path
from struct import unpack_from

from PIL import Image, ImageDraw, ImageFont

import graphics
import translation_build as disc
from translation_source import extent

PATH = "/ETC/MEMO1.TXP"
COLUMNS = (
    """Day 1
October 14

Victim: Joichiro Kogure

Staging: Scarlet Ball

Method: Unknown
After the murder, the killer put Kogure's neck out the tower window. The clock hand cut it off.

Time of crime:
After dinner, before discovery.""",
    """Circumstances

Manami switches on the clock every three days.
The controls are outside the front entrance.

The bell is usually off, but it rang that night.

The tower was locked.
The only key was in the victim's pocket.
A locked room?""",
)


def save_comparison(original, replacement, output):
    """Use the same index-grayscale view as the original memo comparison."""
    comparison = Image.new("RGB", (520, 256), "#303030")
    for x, packed in ((0, original), (264, replacement)):
        comparison.paste(graphics.decode(packed, 4).convert("RGB"), (x, 0))
    comparison.resize((1040, 512), Image.Resampling.NEAREST).save(output)


def save_palette_comparison(original, replacement, colors, output):
    comparison = Image.new("RGBA", (520, 256), "#101010")
    for x, packed in ((0, original), (264, replacement)):
        page = graphics.decode(packed, 4, colors)
        comparison.alpha_composite(page.convert("RGBA"), (x, 0))
    comparison.resize((1040, 512), Image.Resampling.NEAREST).save(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/ARIALNB.TTF"))
    args = parser.parse_args()
    records = graphics.inventory()
    data, pages, palettes = graphics.load(PATH, records)
    offset, _, _ = pages[0]
    end = offset + 32768
    with disc.BASE.open("rb") as base:
        if extent(base, records[PATH]) != data:
            raise ValueError("MEMO1 differs from Combined1")
    font = ImageFont.truetype(str(args.font), 12)
    mask = Image.new("1", (256, 256))
    draw = ImageDraw.Draw(mask)
    for x, limit, text in zip((0, 120), (120, 236), COLUMNS):
        lines = []
        for source_line in text.splitlines():
            line = ""
            for word in source_line.split():
                candidate = f"{line} {word}" if line else word
                if line and draw.textbbox((x, 0), candidate, font=font)[2] > limit:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            lines.append(line)
        for index, line in enumerate(lines):
            y = index * 12
            bounds = draw.textbbox((x, y), line, font=font)
            if bounds[2] > limit or bounds[3] > 256:
                raise ValueError(f"Text exceeds its column: {line}")
            draw.text((x, y), line, fill=1, font=font)
    # A solid stroke uses the same light ink index found in the source page.
    original = data[offset:end]
    image = graphics.decode(original, 4)
    image.frombytes(mask.convert("L").tobytes().translate(bytes([0] * 255 + [2])))
    replacement = graphics.encode(image, 4)
    cue = disc.build_image({PATH: data}, {PATH: [(offset, end, replacement)]}, args.name)
    colors = unpack_from("<16H", data, palettes[0][0])
    save_palette_comparison(original, replacement, colors,
                            cue.parent / f"{args.name}_comparison.png")
    save_comparison(original, replacement, cue.parent / f"{args.name}_indices.png")
    print(cue)


if __name__ == "__main__":
    main()
