"""Read and apply editor changes against the GameplayGraphics2 project baseline."""
import argparse
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from struct import unpack_from

from clean_disc_patch import check_stock, stock_path
import translation_build as disc
import translation_db as store

LATEST = Path("build/patched_gameplayGraphics2.bin")
QUEUE = Path("extract/translation/editor_graphics.json")
INDEX = Path("build/editor_english.json")
FORMAT = "Kindaichi-editor-1"
OUTPUT = Path(os.environ.get("KINDAICHI_EDITOR_OUTPUT", "build"))
ARCHIVE = "Kindaichi-translation-1"


def insertable(row):
    return row["kind"] in ("dialogue", "choice") and bool(row["jp"].strip())


def translation_archive(db, baseline, *, blank=False):
    approved = store.revisions(db, "approved")
    rows = store.source_rows(db)
    fields = ("id", "source_sha256", "path", "offset", "scene", "scene_id",
              "kind", "speaker", "jp", "branch", "choice_block", "choice_branch")
    entries = []
    for row in sorted(rows.values(), key=lambda r: (r["path"] != "/OUT/SANBA.TXT", r["path"], r["offset"])):
        key = row["id"]
        english = approved.get(key, {}).get("text", baseline.get(key, row.get("baseline_en")))
        entries.append({**{field: row.get(field) for field in fields},
                        "insertable": insertable(row), "en": None if blank else english})
    return {"format": ARCHIVE, "disc": "SLPS-00601", "entries": entries}


def archive_edits(archive, rows):
    if archive["format"] != ARCHIVE or archive["disc"] != "SLPS-00601":
        raise ValueError("Unsupported translation archive")
    required = {key for key, row in rows.items() if insertable(row)}
    seen, selected = set(), {}
    for item in archive["entries"]:
        key = item["id"]
        if key in seen or key not in rows or item["source_sha256"] != rows[key]["source_sha256"]:
            raise ValueError("Duplicate, unknown or stale archive entry: " + key)
        seen.add(key)
        text = item["en"]
        if key in required and isinstance(text, str) and text.strip():
            selected[key] = {"text": text.strip()}
    missing = sorted(required - selected.keys())
    if missing:
        raise ValueError(f"{len(missing)} dialogue or choice entries are blank or missing. First: {missing[0]}")
    return selected


def build_archive(patch, name):
    from contextlib import closing
    from repair_plamo_dialogue import BASELINE_WINDOWS
    with closing(store.open_db()) as db:
        rows = store.source_rows(db)
    selected, _ = read_patch(patch, rows)
    scripts, edits, errors = disc.plan(rows, selected, fill_lines=True,
                                       baseline_windows=BASELINE_WINDOWS, expanded_choices=True)
    if errors:
        raise ValueError("\n".join(f"{key}: {error}" for key, error in sorted(errors.items())))
    prepare_graphics(scripts, edits, patch, rows)
    print(disc.build_image(scripts, edits, name), flush=True)


def extract_stock(selection, job):
    try:
        source = stock_path(selection)
        job["log"] = "Checking the original disc checksum."
        with source.open("rb") as image:
            check_stock(image)
            destination = OUTPUT / ("Original assets " + job["key"][:6])
            destination.mkdir()
            records = disc.inventory()
            selected = [(path, record) for path, record in records.items()
                        if Path(path).suffix in {".TXT", ".TXP", ".TEX", ".PAL", ".JAN", ".TMD"}
                        or path in {"/SLPS_006.01", "/SYSTEM.CNF"}]
            for i, (path, record) in enumerate(selected, 1):
                target = destination / path.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(disc.extent(image, record))
                job["log"] = f"Extracting {i}/{len(selected)} {path}"
        from contextlib import closing
        with closing(store.open_db()) as db:
            archive = translation_archive(db, {}, blank=True)
        (destination / "translation-template.json").write_text(
            json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
        job.update(status="complete", location=str(destination.resolve()),
                   log=f"Extracted {len(selected)} original scripts, graphics, models and system files.")
    except (ValueError, OSError) as error:
        job.update(status="failed", log=str(error))



def recipe_command(name):
    return [sys.executable, "-B", "-u", "tools/repair_plamo_dialogue.py", name,
            "--build-saved", "--choices-and-memo", "--fixes",
            "extract/translation/PLaMo3-Full/dialogue_repair/manual_fixes.jsonl"]


def index():
    if os.environ.get("KINDAICHI_EDITOR_FROZEN_INDEX"):
        return json.loads(INDEX.read_text(encoding="utf-8"))
    subprocess.run(recipe_command("editorIndex") + ["--editor-index", str(INDEX)], check=True)
    return json.loads(INDEX.read_text(encoding="utf-8"))


def queued():
    return json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else {}


def archive_patch(archive, rows):
    selected = archive_edits(archive, rows)
    return dict(format=FORMAT, graphics=queued(),
                archive=dict(format=ARCHIVE, disc="SLPS-00601",
                    entries=[dict(id=key, en=item["text"], source_sha256=rows[key]["source_sha256"])
                             for key, item in selected.items()]))


def export_patch(db, path, baseline):
    archive = translation_archive(db, baseline)
    data = archive_patch(archive, store.source_rows(db))
    with path.open("x", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False)


def read_patch(path, rows):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["format"] != FORMAT:
        raise ValueError("Unknown editor patch format")
    for key, value in data.get("text", {}).items():
        if key not in rows or value["source_sha256"] != rows[key]["source_sha256"]:
            raise ValueError("Source changed for " + key)
    text = archive_edits(data["archive"], rows) if "archive" in data else data["text"]
    return text, data["graphics"]


def texture(path, records, *, original=False):
    import graphics
    if path not in records or Path(path).suffix not in graphics.FORMATS:
        raise ValueError("Unknown texture or palette")
    with (disc.BASE if original else LATEST).open("rb") as image:
        data = disc.extent(image, records[path])
    return data, *graphics.parse(data, Path(path).suffix)


def page_changes(source, edited):
    return [[i, value] for i, value in enumerate(edited) if value != source[i]]


def changed_page(source, changes):
    packed = bytearray(source)
    previous = -1
    for offset, value in changes:
        if not previous < offset < len(source) or not 0 <= value <= 255:
            raise ValueError("Invalid indexed graphic edit")
        packed[offset] = value
        previous = offset
    return bytes(packed)


def sheets():
    from english_artwork import PORTRAIT_SHEETS
    groups = {}
    for index, path in enumerate(PORTRAIT_SHEETS, 1):
        palette = path if path.endswith(".TXP") else path.rsplit("_", 1)[0] + ".PAL"
        groups[f"sheet:Suspect portraits {index}"] = (
            [(path, page) for page in range(3)], 8, palette, -1)
    groups["sheet:Final suspect portraits"] = (
        [("/ATREN4/2_5_8_2.TEX", 3), ("/ATREN4/2_5_8_1.TEX", 0), ("/ATREN4/2_5_8_1.TEX", 1)],
        8, "/ATREN4/2_5_8.PAL", 0)
    for variant in (1, 2):
        path = f"/ETC/YASIKI{variant}.TXP"
        groups[f"sheet:Manor floor plan {variant}"] = ([(path, 0), (path, 1)], 4, path, 0)
    return groups


# Caption parts follow the JAN screen positions, including Mayura's 8 px vertical offset.
CAPTIONS = {
    "sheet:Kogure introduction": ("/ATXP1_2/31_2_1.TXP", 5, 18, 0, 128, 176, 80, 80, 0),
    "sheet:Endo introduction": ("/ATXP1_2/31_2_1.TXP", 5, 17, 0, 0, 48, 80, 80, 0),
    "sheet:Manami introduction": ("/ATXP1_2/23_2_1.TXP", 10, 15, 0, 0, 48, 80, 80, 0),
    "sheet:Yone introduction": ("/ATXP1_2/23_2_1.TXP", 9, 16, 0, 160, 208, 80, 80, 0),
    "sheet:Shoko introduction": ("/ATXP1_2/10_4_1.TXP", 0, 2, 0, 128, 176, 80, 80, 0),
    "sheet:Shoko at dinner": ("/ATREN1/2_5_2_2.TEX", 0, 10, 0, 0, 48, 80, 80, 0),
    "sheet:Mitsusada at dinner": ("/ATREN1/2_5_2_2.TEX", 0, 11, 0, 128, 176, 80, 80, 0),
    "sheet:Sayuri at dinner": ("/ATXP1_1/2_5_1.TXP", 3, 10, 0, 0, 48, 80, 80, 0),
    "sheet:Sayuri introduction": ("/ATXP1_1/1_4_1.TXP", 2, 4, 0, 0, 48, 80, 80, 0),
    "sheet:Tomyo introduction": ("/ATXP1_2/34_2_1.TXP", 9, 12, 8, 128, 176, 72, 48, 0),
    "sheet:Mayura introduction": ("/ATXP1_2/34_2_1.TXP", 10, 11, 8, 0, 56, 72, 72, 8),
}


def caption_parts(path):
    asset, page, palette, x, y1, y2, w1, w2, dy = CAPTIONS[path]
    palette_path = asset if asset.endswith(".TXP") else asset.rsplit("_", 1)[0] + ".PAL"
    return [(asset, page, (x, y1, x + w1, y1 + 48), 0),
            (asset, page, (0, y2, w2, y2 + 48 - dy), dy)], 8, palette_path, palette


def texture_parts(path, index, depth):
    if path in CAPTIONS:
        if index != 0 or depth != 8:
            raise ValueError("Use the assembled caption's original dimensions and bit depth")
        return caption_parts(path)[0]
    if path in sheets():
        parts, expected, _, _ = sheets()[path]
        if index != 0 or depth != expected:
            raise ValueError("Use the assembled sheet's original dimensions and bit depth")
        return [(asset, page, (0, 0, 1024 // depth, 256), 0) for asset, page in parts]
    if depth not in (4, 8):
        raise ValueError("Choose 4 or 8 bits per pixel")
    return [(path, index, (0, 0, 1024 // depth, 256), 0)]


def png(path, index, depth, palette_path="", palette=0, *, original=False):
    import graphics
    parts = texture_parts(path, index, depth)
    records = disc.inventory()
    queue = {} if original else queued()
    colors = None
    if palette_path:
        raw, _, entries = texture(palette_path, records, original=original)
        start, _, _, width = graphics.select(entries, palette)
        if width != 1 << depth:
            raise ValueError("Palette size does not match this bit depth")
        colors = unpack_from(f"<{width}H", raw, start)
    width = sum(box[2] - box[0] for _, _, box, _ in parts)
    height = parts[0][2][3] - parts[0][2][1]
    image = graphics.Image.new("P", (width, height))
    x = 0
    for asset, page, box, y in parts:
        data, pages, _ = texture(asset, records, original=original)
        offset, _, _ = graphics.select(pages, page)
        changes = queue.get(asset, {}).get(str(page), [])
        tile = graphics.decode(changed_page(data[offset:offset + 32768], changes), depth, colors)
        if x == 0:
            image.putpalette(tile.getpalette())
            image.info.update(tile.info)
        if x == 0 and path in CAPTIONS:
            image.paste(4, (0, 0, width, height))
        image.paste(tile.crop(box), (x, y))
        x += box[2] - box[0]
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def queue_png(path, index, depth, contents):
    import graphics
    parts = texture_parts(path, index, depth)
    records = disc.inventory()
    queue = queued()
    width = sum(box[2] - box[0] for _, _, box, _ in parts)
    height = parts[0][2][3] - parts[0][2][1]
    x = 0
    with graphics.Image.open(BytesIO(contents), formats=["PNG"]) as image:
        if image.mode != "P" or image.size != (width, height):
            raise ValueError("Use the exported indexed PNG at its original dimensions")
        for asset, page, box, y in parts:
            data, pages, _ = texture(asset, records)
            offset, _, _ = graphics.select(pages, page)
            source = data[offset:offset+32768]
            group = queue.setdefault(asset, {})
            tile = graphics.decode(changed_page(source, group.get(str(page), [])), depth)
            right = x + box[2] - box[0]
            tile.paste(image.crop((x, y, right, y + box[3] - box[1])), box)
            group[str(page)] = page_changes(source, graphics.encode(tile, depth))
            x = right
    temporary = QUEUE.with_suffix(".tmp")
    temporary.write_text(json.dumps(queue, separators=(",", ":")), encoding="utf-8")
    temporary.replace(QUEUE)


def catalogue():
    records = disc.inventory()
    return {"sheets": [*CAPTIONS, *sheets()],
            "textures": sorted(p for p in records if Path(p).suffix in (".TXP", ".TEX")),
            "palettes": sorted(p for p in records if Path(p).suffix in (".TXP", ".PAL"))}


def graphics_info(path):
    records = disc.inventory()
    if path in CAPTIONS or path in sheets():
        _, depth, palette_path, index = caption_parts(path) if path in CAPTIONS else sheets()[path]
        count = len(texture(palette_path, records)[2])
        return dict(pages=1, palettes=[], depth=depth, palette_path=palette_path,
                    palette=str(index % count), assembled=True)
    _, pages, palettes = texture(path, records)
    source = Path(path)
    palette_path = path if source.suffix == ".TXP" else ""
    if source.suffix == ".TEX":
        matches = [candidate for candidate in records
                   if Path(candidate).parent == source.parent
                   and Path(candidate).suffix == ".PAL"
                   and (source.stem == Path(candidate).stem
                        or source.stem.startswith(Path(candidate).stem + "_"))]
        if len(matches) == 1:
            palette_path = matches[0]
    widths = {entry[3] for entry in texture(palette_path, records)[2]} if palette_path else set()
    depth = 4 if widths == {16} else 8
    first = "0" if len(widths) == 1 else ""
    return dict(pages=len(pages), palettes=[entry[3] for entry in palettes],
                depth=int(depth), palette_path=palette_path, palette=first)


def prepare_graphics(scripts, edits, patch_path, rows):
    import graphics
    _, replacements = read_patch(patch_path, rows)
    records = disc.inventory()
    for path in replacements:
        if path not in catalogue()["textures"]:
            raise ValueError("Unknown editable texture " + path)
    with LATEST.open("rb") as latest, disc.BASE.open("rb") as baseline:
        for path, record in records.items():
            if Path(path).suffix not in graphics.FORMATS and path != "/SLPS_006.01":
                continue
            before = disc.extent(baseline, record)
            after = disc.extent(latest, record)
            if path in replacements:
                pages, _ = graphics.parse(after, Path(path).suffix)
                after = bytearray(after)
                for key, changes in replacements[path].items():
                    offset, _, _ = graphics.select(pages, int(key))
                    after[offset:offset+32768] = changed_page(after[offset:offset+32768], changes)
            if before != after:
                scripts[path] = before
                edits[path] = [(0, len(before), bytes(after))]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", type=Path)
    parser.add_argument("name")
    args = parser.parse_args()
    if "archive" in json.loads(args.patch.read_text(encoding="utf-8")):
        try:
            build_archive(args.patch, args.name)
        except (OSError, ValueError) as error:
            raise SystemExit(str(error)) from None
        raise SystemExit(0)
    raise SystemExit(subprocess.call(recipe_command(args.name) + ["--editor-patch", str(args.patch)]))
