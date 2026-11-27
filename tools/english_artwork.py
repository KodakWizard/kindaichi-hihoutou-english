"""Replace reviewed text regions while preserving texture layout and CLUTs."""
from PIL import Image, ImageDraw, ImageFont

import graphics
from translation_source import extent

MEMO = "/ETC/MEMO1.TXP"
EXE = "/SLPS_006.01"
CARD_MESSAGES = {
    0x6AB78: "No memory card",
    0x6ABA0: "No save",
    0x6ABB4: "Card damaged",
    0x6ABD4: "Unformatted",
    0x6ABF0: "Format?",
    0x6AC08: "Not enough blocks",
    0x6AC30: "Use another card",
    0x6AC58: "or delete old data",
    0x6AC80: "in Manager",
    0x6AC98: "OK  No",
    0x6ACA8: "Save failed." + "Load failed." + "No load  ",
}
VERSES = (
    ("I. White Torii", "When the star\nFills the heavens,\nA raging god\nDraws dark to night.\nA hot pillar rises,\nAnd on the earth\nA star appears."),
    ("II. Green Torii", "Many fierce\nBeast children\nGuard the star.\nThey never meet.\nBind them with thread\nAnd they will lead\nYou to the star."),
    ("III. Black Torii", "The children\nSay nothing.\nYet when they gather\nAnd play together,\nThe silent children\nShow where\nThe star has gone."),
)


def label(image, box, text, *, ink=2, background=0, size=10):
    x, y, right, bottom = box
    mask = Image.new("1", (right - x, bottom - y))
    draw = ImageDraw.Draw(mask)
    font = ImageFont.truetype("C:/Windows/Fonts/tahomabd.ttf", size)
    bounds = draw.textbbox((0, 0), text, font=font)
    if bounds[2] > mask.width or bounds[3] - bounds[1] > mask.height:
        raise ValueError(f"Artwork label exceeds {box}: {text}")
    draw.text(((mask.width - bounds[2]) // 2,
               (mask.height - bounds[3] - bounds[1]) // 2),
              text, font=font, fill=1)
    image.paste(background, box)
    image.paste(ink, box, mask)


PORTRAIT_SHEETS = {
    "/ATREN1/2_B_7_2.TEX": (41, 64, "Hazuki Mayura Yone Manami Mimura", "Kogure Endo Tomyo Sayuri"),
    "/ATREN2/2_9_6_1.TEX": (29, 53, "Iwata Mayura Masaki Yone Manami", "Mimura Kogure Endo Tomyo Sayuri"),
    "/ATREN3/2_7_6_3.TEX": (17, 41, "Iwata Mayura Masaki Yone Manami Mimura", "Kogure Endo Tomyo Sayuri Sato"),
    "/ATXP2_1/2_A_1.TXP": (29, 53, "Iwata Mayura Masaki Yone Manami", "Mimura Kogure Endo Tomyo Sayuri"),
}


def island_label_edits(base, records):
    # Mixed-format atlas: scenery is 8bpp, but destination strips are 4bpp.
    path = "/ETC/SIMA.TXP"
    data, pages, palettes = graphics.load(path, records)
    if extent(base, records[path]) != data or palettes[1][3] != 16:
        raise ValueError("Island map source or label palette differs")
    offset = pages[2][0]
    image = graphics.decode(data[offset:offset + 32768], 4)
    for row, name in enumerate(("Manor", "Shed", "Boathouse", "Tomyo Hut", "Cove",
                               "Shrine", "Cliff", "White Torii", "Archives", "Pier",
                               "Green Torii", "Cave", "Demon Mouth")):
        y = 33 + row * 16
        label(image, (134, y, 204, y + 14), name, ink=8, background=2, size=9)
    return path, data, [(offset, offset + 32768, graphics.encode(image, 4))]


def final_portrait_edits(base, records):
    # This sheet crosses a file boundary: page 3 of _2 precedes pages 0–1 of _1.
    first = "/ATREN4/2_5_8_2.TEX"
    second = "/ATREN4/2_5_8_1.TEX"
    sheet = Image.new("P", (384, 256))
    chunks = []
    for slot, (path, page) in enumerate(((first, 3), (second, 0), (second, 1))):
        data, pages, _ = graphics.load(path, records)
        if extent(base, records[path]) != data:
            raise ValueError(f"Portrait texture differs: {path}")
        offset = pages[page][0]
        chunks.append((path, data, offset))
        sheet.paste(graphics.decode(data[offset:offset + 32768], 8), (128 * slot, 0))
    for y, start, names in (
        (105, 17, "Iwata Mayura Masaki Yone Manami Mimura"),
        (185, 41, "Kogure Endo Tomyo Sayuri Sato"),
    ):
        for i, name in enumerate(names.split()):
            x = start + i * 48
            label(sheet, (x + 2, y, x + 46, y + 16), name, ink=0, background=1, size=9)
    results = {}
    for slot, (path, data, offset) in enumerate(chunks):
        replacement = graphics.encode(sheet.crop((128 * slot, 0, 128 * (slot + 1), 256)), 8)
        if replacement != data[offset:offset + 32768]:
            results.setdefault(path, (data, []))[1].append((offset, offset + 32768, replacement))
    return results


def portrait_name_edits(base, records):
    from struct import unpack_from

    for path, (top, bottom, upper, lower) in PORTRAIT_SHEETS.items():
        data, pages, palettes = graphics.load(path, records)
        if extent(base, records[path]) != data:
            raise ValueError(f"Portrait texture differs from Combined1: {path}")
        palette_data = data
        if not palettes:
            palette_path = path.rsplit("_", 1)[0] + ".PAL"
            palette_data, _, palettes = graphics.load(palette_path, records)
        colors = unpack_from("<256H", palette_data, palettes[-1][0])
        ink = colors.index(0x7fff)
        background = colors.index(0x8000)
        sheet = Image.new("P", (384, 256))
        for page, (offset, _, _) in enumerate(pages[:3]):
            sheet.paste(graphics.decode(data[offset:offset + 32768], 8), (128 * page, 0))
        for y, x, names in ((105, top, upper), (185, bottom, lower)):
            for index, name in enumerate(names.split()):
                left = x + 48 * index
                label(sheet, (left + 2, y, left + 46, y + 16), name,
                      ink=ink, background=background, size=9)
        spans = []
        for page, (offset, _, _) in enumerate(pages[:3]):
            replacement = graphics.encode(sheet.crop((128 * page, 0, 128 * (page + 1), 256)), 8)
            if replacement != data[offset:offset + 32768]:
                spans.append((offset, offset + 32768, replacement))
        yield path, data, spans


def card(image):
    for y, cells in {
        16: ((0, 128, "Loaded."), (128, 256, "Saved.")),
        32: ((0, 128, "Save game?"), (128, 176, "Save"), (176, 224, "Load")),
        48: ((0, 32, "Yes"), (48, 96, "No"), (176, 192, "M"), (192, 208, "D"), (224, 240, "m")),
        64: ((0, 48, "Card"), (64, 112, "Card"), (176, 192, "M"),
             (208, 224, "D"), (224, 256, "Time")),
        80: ((144, 176, "Count"),),
        96: ((0, 64, "Loading"),),
        112: ((0, 64, "Saving"),),
    }.items():
        for x, right, text in cells:
            # The card screen uses the palette where index 1 is transparent.
            label(image, (x, y, right, y + 16), text, background=1, ink=14)
    image.paste(1, (0, 128, 192, 144))
    label(image, (112, 128, 192, 144), "Checking card", background=1, ink=14)


def floor_labels(image, page, variant):
    if page == 0:
        boxes = [
            ((0, 42, 61, 56), "Kogure"), ((0, 58, 61, 73), "Endo"),
            ((0, 80, 64, 96), "Mayura"), ((0, 140, 53, 155), "Yone"),
            ((0, 159, 49, 176), "Itsuki"), ((0, 180, 48, 197), "Kenmochi"),
            ((122, 190, 208, 207), "Hazuki Manor 1F"),
        ]
        # Right-side labels extend onto page 1.
        for box in ((250, 42, 256, 55), (250, 60, 256, 74)):
            image.paste(15, box)
    else:
        boxes = [
            ((0, 39, 39, 55), "Sayuri"), ((0, 58, 39, 74), "Chris"),
            ((94, 51, 186, 70), "3F Clock Tower"),
            ((0, 139, 42, 155), "Manami"), ((7, 159, 45, 176), "Miyuki"),
            ((8, 180, 56, 197), "Hajime"), ((77, 131, 115, 147), "Hazuki"),
        ]
        if variant == 2:
            boxes.append(((0, 76, 42, 92), "Mayuki"))
    for box, name in boxes:
        label(image, box, name, background=15, ink=0, size=9)


def english_artwork(base, records):
    data, pages, _ = graphics.load(MEMO, records)
    if extent(base, records[MEMO]) != data:
        raise ValueError("Memo artwork differs from Combined1")
    edits = []
    for index in (6, 7, 8, 10):
        offset = pages[index][0]
        raw = data[offset:offset + 32768]
        image = graphics.decode(raw, 4)
        if index == 10:
            card(image)
        else:
            title, verse = VERSES[index - 6]
            label(image, (0, 0, 120, 32), title, size=11)
            for line, text in enumerate(verse.splitlines()):
                y = 32 + 16 * line
                label(image, (0, y, 120, y + 16), text, size=10)
            # Original cipher pixels on the right remain byte-identical.
            if image.crop((120, 0, 256, 256)).tobytes() != graphics.decode(
                    raw, 4).crop((120, 0, 256, 256)).tobytes():
                raise ValueError("Verse cipher pixels changed")
        edits.append((offset, offset + 32768, graphics.encode(image, 4)))
    return edits


def floor_plan_edits(base, records, variant):
    path = f"/ETC/YASIKI{variant}.TXP"
    data, pages, _ = graphics.load(path, records)
    if extent(base, records[path]) != data:
        raise ValueError(f"Floor plan differs from Combined1: {path}")
    edits = []
    for index in (0, 1):
        offset = pages[index][0]
        image = graphics.decode(data[offset:offset + 32768], 4)
        floor_labels(image, index, variant)
        edits.append((offset, offset + 32768, graphics.encode(image, 4)))
    return path, data, edits


def card_message_edits(base, records, rows):
    data = extent(base, records[EXE])
    edits = []
    for offset, text in CARD_MESSAGES.items():
        key = f"{EXE}@{offset:08x}:system"
        original = bytes.fromhex(rows[key]["raw_hex"])
        end = offset + len(original)
        if data[offset:end + 1] != original + b"\0":
            raise ValueError(f"Memory card string differs at {offset:x}")
        width = len(original) // 2
        if len(text) > width:
            raise ValueError(f"Memory card string exceeds its slot at {offset:x}")
        encoded = "".join("\u3000" if c == " " else chr(ord(c) + 0xFEE0)
                          for c in text.ljust(width)).encode("cp932")
        if len(encoded) != len(original):
            raise ValueError("Memory card string changed size")
        edits.append((offset, end, encoded))
    return data, edits
