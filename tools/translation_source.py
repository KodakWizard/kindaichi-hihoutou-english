"""Catalogue source bytes and conservative script context from the original disc."""
import hashlib
import json
import re
from pathlib import Path

from extract_iso import BIN

ISO = Path("extract/iso")
ENC = "shift_jis_2004"
PAYLOAD = 2048
SECTOR = 2352


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory():
    return {row["path"]: row for row in json.loads(Path("extract/filelist.json").read_text())
            if not row["dir"]}


def extent(image, resource):
    count = (resource["size"] + PAYLOAD - 1) // PAYLOAD
    image.seek(resource["lba"] * SECTOR)
    raw = image.read(count * SECTOR)
    if len(raw) != count * SECTOR:
        raise ValueError("Truncated disc extent: " + resource["path"])
    return b"".join(raw[i + 24:i + 2072] for i in range(0, len(raw), SECTOR))[:resource["size"]]


def entry(resource, offset, raw, text, kind, **context):
    path = resource["path"]
    return dict(id=f"{path}@{offset:08x}:{kind}", path=path, offset=offset,
                lba=resource["lba"] + offset // PAYLOAD, sector_offset=24 + offset % PAYLOAD,
                size=len(raw), raw_hex=raw.hex(), source_sha256=digest(raw),
                encoding=context.pop("encoding", ENC), jp=text, kind=kind, **context)


def scripts(resource, data):
    """Keep all structural lines in order, including malformed or unresolved flow."""
    decoded = data.decode(ENC)
    if decoded.encode(ENC) != data:
        raise ValueError("Non-reversible script: " + resource["path"])
    lines = []
    offset = 0
    for line in data.splitlines(keepends=True):
        lines.append((offset, line, line.strip(b" \t\r\n")))
        offset += len(line)
    result, commands = [], []
    scene = scene_id = speaker = selection = option = None
    branch = []
    choice_indent = option_indent = None
    commented = False
    conversation = None
    previous = None
    order = 0
    cursor = 0
    while cursor < len(lines):
        offset, raw, body = lines[cursor]
        cursor += 1
        if not body:
            continue
        token = body.split()[0]
        indent = len(raw) - len(raw.lstrip(b" \t"))
        # Inline terminators end comments even after annotation text.
        if commented and b"#cmtend" in body:
            commands.append(dict(offset=offset, text=body.decode(ENC), raw_hex=raw.hex(),
                                 scene_id=scene_id))
            commented = False
            continue
        if not commented:
            if selection is not None and indent <= choice_indent:
                selection = option = None
            elif option is not None and indent <= option_indent:
                option = None
        if body.startswith((b"#", b"$", b"\\")) and (not commented or token == b"#cmtend"):
            commands.append(dict(offset=offset, text=body.decode(ENC), raw_hex=raw.hex(),
                                 scene_id=scene_id))
            if token == b"#cmt":
                commented = b"#cmtend" not in body
            elif token == b"#cmtend":
                commented = False
            elif token == b"#scene":
                scene = body[len(token):].strip().decode(ENC)
                scene_id = f"{resource['path']}@{offset:08x}"
                branch = []
                selection = option = speaker = None
            elif token == b"#name":
                match = re.fullmatch(rb"#name[ \t]+(\d+)", body)
                speaker = int(match[1]) if match else None
            elif token == b"#if":
                branch.append(body.decode(ENC))
            elif token == b"#else" and branch:
                branch[-1] = "else of " + branch[-1]
            elif token == b"#endif" and branch:
                branch.pop()
            elif token == b"#sentaku":
                selection, option = offset, None
                choice_indent = indent
            elif selection is not None and body.startswith(b"\\"):
                option = body.decode(ENC)
                option_indent = indent
            elif token == b"#msg":
                start = cursor
                while cursor < len(lines):
                    content = lines[cursor][2]
                    if not content or content.startswith((b"#", b"$", b"%", b"\\")):
                        break
                    cursor += 1
                if cursor == start:
                    continue
                block = lines[start:cursor]
                text = "\n".join(line[2].decode(ENC) for line in block)
                if conversation is None:
                    conversation = f"{resource['path']}@{offset:08x}"
                    order = 0
                order += 1
                page = entry(resource, block[0][0], b"".join(line[1] for line in block),
                             text, "dialogue", speaker=speaker, command_offset=offset,
                             command=body.decode(ENC), scene=scene, scene_id=scene_id,
                             branch=list(branch), choice_block=selection, choice_branch=option,
                             conversation=conversation, order=order,
                             previous=previous["id"] if previous else None, next=None)
                if previous:
                    previous["next"] = page["id"]
                previous = page
                result.append(page)
                speaker = None
                continue
            # Only directly adjacent speaker/page turns establish conversation order.
            if token != b"#name":
                conversation = previous = None
            continue
        choice_body = re.sub(rb"^(?:\x81\x40[ \t]*)+", b"", body)
        kind = "comment" if commented else "choice" if choice_body.startswith(b"%") else "unclassified"
        if kind == "unclassified" and body.isascii():
            continue
        text = (choice_body[1:] if kind == "choice" else body).decode(ENC)
        result.append(entry(resource, offset, raw, text, kind, scene=scene, scene_id=scene_id,
                            branch=list(branch), choice_block=selection, choice_branch=option,
                            parser_status=("full-width indentation unverified"
                                           if kind == "choice" and choice_body != body else None),
                            command="%" if kind == "choice" else None))
        conversation = previous = None
    return result, commands


def collect():
    from build_namesG4 import NAMES
    from text_artwork import PAGES, artwork_entries

    artwork_paths = {f"/ETC/{name}.TXP" for name in PAGES}
    resources, entries, controls = [], [], []
    with open(BIN, "rb") as original:
        for path, resource in sorted(inventory().items()):
            suffix = Path(path).suffix
            status, reason = "unassessed", "Binary resource. Embedded text or artwork not ruled out."
            if suffix == ".TXT" or path == "/SLPS_006.01" or path in artwork_paths:
                data = (ISO / path[1:]).read_bytes()
                if data != extent(original, resource):
                    raise ValueError("Extract does not match original disc: " + path)
                if path in artwork_paths:
                    entries.extend(artwork_entries(resource, data, entry))
                    status, reason = "partial", "Text artwork identified; Japanese transcription and runtime mapping pending."
                elif path == "/SLPS_006.01":
                    offset = 0x10B0
                    title = data[offset:data.index(b"\0", offset)]
                    jp = title.decode("shift_jis")
                    if not jp.startswith("金田一少年の事件簿") or jp.encode("shift_jis") != title:
                        raise ValueError("Unexpected executable title")
                    entries.append(entry(resource, offset, title, jp, "system", encoding="shift_jis",
                                         context="Executable title; display use unverified"))
                    for number, (jp, en) in enumerate(NAMES):
                        offset = 0x6A620 + number * 8
                        raw = data[offset:offset + 8]
                        if raw != jp.ljust(3, "\u3000").encode("cp932") + bytes(2):
                            raise ValueError("Unexpected speaker record")
                        entries.append(entry(resource, offset, raw, jp, "speaker", encoding="cp932",
                                             speaker=number, baseline_en=en))
                    # Known memory-card strings from the existing exe_sjis.py scan.
                    for match in re.finditer(rb"[^\0]+", data[0x6AB78:0x6ACEC]):
                        raw = match[0]
                        text = raw.decode("cp932")
                        if text.encode("cp932") != raw:
                            raise ValueError("Memory-card text did not round-trip")
                        entries.append(entry(resource, 0x6AB78 + match.start(), raw, text,
                                             "system", encoding="cp932",
                                             context="Memory-card message or choice; display boundaries unverified"))
                    status, reason = "partial", "Speaker table and memory-card strings verified. Remaining EXE strings and UI renderer unresolved."
                elif path == "/COPY.TXT":
                    jp = data.decode(ENC)
                    if jp.encode(ENC) != data:
                        raise ValueError("COPY.TXT did not round-trip")
                    entries.append(entry(resource, 0, data, jp, "copyright"))
                    status, reason = "catalogued", "Copyright text. No reinsertion path."
                else:
                    rows, commands = scripts(resource, data)
                    entries.extend(rows)
                    controls.extend(dict(row, path=path) for row in commands)
                    if path == "/OUT/SANBA.TXT":
                        caption = "木暮開発社長  木暮条一郎"
                        raw = caption.encode(ENC)
                        marker = ("テロップ『" + caption + "』").encode(ENC)
                        start = data.index(marker)
                        offset = start + marker.index(raw)
                        command_offset = data.index(b"#dspsp 15 100 1 3", offset)
                        if command_offset - offset > 100:
                            raise ValueError("Kogure caption lost its adjacent sprite command")
                        entries.append(entry(resource, offset, raw, caption, "caption",
                                             command_offset=command_offset,
                                             command="#dspsp 15 100 1 3",
                                             provenance="Script caption comment; texture mapping unresolved"))
                    status, reason = "catalogued", "Whole-script round trip; see unclassified lines and control-flow blockers."
            resources.append(dict(resource, status=status, reason=reason))
    return resources, entries, controls
