"""Decode texture pages and inject edited indexed PNGs into Combined1."""
import argparse
from collections import Counter
from pathlib import Path
from struct import unpack_from

from PIL import Image

from translation_source import ISO, extent, inventory

FORMATS = {".TXP", ".TEX", ".PAL"}


def parse(data, suffix):
    offset = 0

    def read(fmt, size):
        nonlocal offset
        values = unpack_from(fmt, data, offset)
        offset += size
        return values

    pages, palettes = [], []
    if suffix in (".TXP", ".TEX"):
        for _ in range(read("<I", 4)[0]):
            x, y, w, h = read("<4H", 8)
            if (w, h) != (64, 256) or x + w > 1024 or y + h > 512:
                raise ValueError("Invalid texture geometry")
            pages.append((offset, x, y))
            offset += 32768
    if suffix in (".TXP", ".PAL"):
        for _ in range(read("<I", 4)[0]):
            size, x, y, w, h = read("<I4H", 12)
            if h != 1 or w not in (16, 256) or size != 12 + 2*w or x+w > 1024 or y >= 512:
                raise ValueError("Invalid palette geometry")
            palettes.append((offset, x, y, w))
            offset += 2*w
    if offset != len(data) or not (pages or palettes):
        raise ValueError("Unparsed container bytes")
    return pages, palettes


def decode(packed, depth, colors=None):
    indices = packed if depth == 8 else bytes(n for b in packed for n in (b & 15, b >> 4))
    image = Image.frombytes("P", (1024 // depth, 256), indices)
    if colors is None:
        rgb = [i * 255 // ((1 << depth)-1) for i in range(1 << depth) for _ in range(3)]
    else:
        rgb = [((c >> shift) & 31) * 255 // 31 for c in colors for shift in (0, 5, 10)]
        image.info["transparency"] = bytes(0 if c == 0 else 255 for c in colors)
    image.putpalette(rgb + [0] * (768 - len(rgb)))
    return image


def encode(image, depth):
    if image.mode != "P" or image.size != (1024 // depth, 256):
        raise ValueError("Use an indexed PNG with the exported dimensions")
    pixels = image.tobytes()
    if max(pixels) >= 1 << depth:
        raise ValueError("Palette index exceeds selected depth")
    return pixels if depth == 8 else bytes(a | b << 4 for a, b in zip(pixels[::2], pixels[1::2]))


def load(path, records):
    if path not in records or Path(path).suffix not in FORMATS:
        raise ValueError("Use a TXP, TEX or PAL disc path")
    data = (ISO / path[1:]).read_bytes()
    return data, *parse(data, Path(path).suffix)


def select(blocks, index):
    if not 0 <= index < len(blocks):
        raise ValueError("Block index is out of range")
    return blocks[index]


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("audit")
    for name in ("inspect", "extract", "inject"):
        cmd = commands.add_parser(name)
        cmd.add_argument("path")
        if name != "inspect":
            cmd.add_argument("--page", type=int, required=True)
            cmd.add_argument("--depth", type=int, choices=(4, 8), required=True)
            cmd.add_argument("png", type=Path)
        if name == "extract":
            cmd.add_argument("--palette-path")
            cmd.add_argument("--palette", type=int)
        if name == "inject":
            cmd.add_argument("name")
    args = cli.parse_args()
    records = inventory()
    if args.command == "audit":
        counts = Counter()
        for path in records:
            suffix = Path(path).suffix
            if suffix in FORMATS:
                data, pages, palettes = load(path, records)
                counts[suffix] += 1
                counts["pages"] += len(pages)
                counts["palettes"] += len(palettes)
                for offset, _, _ in pages:
                    packed = data[offset:offset+32768]
                    for depth in (4, 8):
                        if encode(decode(packed, depth), depth) != packed:
                            raise ValueError(f"Round trip failed: {path}")
        print("[STATIC]", dict(counts))
        return
    data, pages, palettes = load(args.path, records)
    if args.command == "inspect":
        for label, blocks in (("page (offset,x,y)", pages), ("palette (offset,x,y,colors)", palettes)):
            for i, block in enumerate(blocks):
                print(i, label, block)
        return
    offset, _, _ = select(pages, args.page)
    end = offset + 32768
    if args.command == "extract":
        colors = None
        if args.palette_path and args.palette is None:
            cli.error("--palette-path requires --palette")
        if args.palette is not None:
            palette_data = data
            if args.palette_path:
                palette_data, _, palettes = load(args.palette_path, records)
            start, _, _, width = select(palettes, args.palette)
            if width != 1 << args.depth:
                cli.error("Palette size does not match selected depth")
            colors = unpack_from(f"<{width}H", palette_data, start)
        with args.png.open("xb") as output:
            decode(data[offset:end], args.depth, colors).save(output, format="PNG")
        return
    import translation_build as disc

    with disc.BASE.open("rb") as image:
        if data != extent(image, records[args.path]):
            cli.error("Extracted container differs from Combined1")
    with Image.open(args.png) as image:
        if image.format != "PNG":
            cli.error("Use an indexed PNG")
        replacement = encode(image, args.depth)
    print(disc.build_image({args.path: data},
                           {args.path: [(offset, end, replacement)]}, args.name))


if __name__ == "__main__":
    main()
