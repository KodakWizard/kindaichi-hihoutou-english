"""Validate and insert approved dialogue using the frozen Combined1 disc."""
import hashlib
import os
import re
import shutil
from pathlib import Path

import wrap
from movie_baseline import preserve_release_movies
from patch_bin import edc_compute, patch
from translation_source import SECTOR, extent, inventory
from script_allocator import MAX_SECTORS, allocate, verify_extents

BASE = Path("build/patched_dialogueCombined1.bin")
BASE_SHA256 = "e24e6c6d95fbcbb36ef00a8909a902930a795ac452ec0acb236e9a2e696b544d"
METRICS = next((path for path in (Path("build/w_metrics_release2.json"),
                                Path("build/w_metrics_release1.json")) if path.exists()),
               Path("build/w_metrics_g4.json"))
CLUE_LITERALS = {
    "/IN/OUBEYA.TXT@0000947a:dialogue": ("二七",),
    "/IN/OUBEYA.TXT@00009bd4:dialogue": ("二七",),
}
CLUE_CODES = {int.from_bytes(char.encode("cp932"), "big")
              for literals in CLUE_LITERALS.values() for literal in literals for char in literal}


def validate_english(text, *, native_literals=()):
    if "\u300c" in text or "\u300d" in text:
        raise ValueError("Remove Japanese speech delimiters from English")
    for literal in native_literals:
        text = text.replace(literal, "")
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text):
        raise ValueError("Japanese glyphs in an approved English page")


def render(text, metrics, *, native_literals=()):
    validate_english(text, native_literals=native_literals)
    lines, _ = wrap.pack(text.strip(), metrics, english_layout=True)
    if not lines:
        raise ValueError("Empty rendered page")
    return render_lines(lines, metrics)


def render_lines(lines, metrics):
    body = wrap.emit_page(lines)
    states = [wrap.simulate(body, metrics, prevh=p << 4) for p in range(16)]
    if any(s["errors"] or s["lines"] > 3 or s["records"] > wrap.MAX_RECORDS
           or s["glyphs"] != states[0]["glyphs"] for s in states):
        raise ValueError("Page fails renderer simulation")
    ink = [glyph for glyph in states[0]["glyphs"] if glyph[5] is not None]
    for previous, following in zip(ink, ink[1:]):
        if (previous[0] == following[0] and previous[3] == "pair"
                and previous[4] in CLUE_CODES
                and following[2] + following[5] <= previous[2] + previous[6]):
            raise ValueError("A following glyph overlaps the protected native clue")
    return body


def page_words(lines):
    return sum(len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*",
                              "".join(token["src"] for token in line)))
               for line in lines)


def dialogue_body(text, metrics, script, row, *, allow_pagination=True, fill_lines=False,
                  min_extra_words=0, extended_pagination=False, native_literals=()):
    """Insert intermediate pages while preserving the original following commands."""
    if not allow_pagination:
        try:
            return render(text, metrics, native_literals=native_literals)
        except ValueError as error:
            raise ValueError(f"Pagination disabled; single-page validation failed: {error}") from error
    offset = row["offset"]
    end = offset + row["size"]
    validate_english(text, native_literals=native_literals)
    layout = [lines for lines, _ in wrap.paginate(text.strip(), metrics,
              english_layout=True, fill_lines=fill_lines)]
    for index in range(1, len(layout)):
        previous, current = layout[index - 1:index + 1]
        if (min_extra_words and page_words(current) < min_extra_words
                and len(previous) > 1 and len(current) < 3
                and page_words(previous[:-1]) >= min_extra_words):
            current.insert(0, previous.pop())
        if page_words(current) < min_extra_words:
            raise ValueError(f"Extra dialogue page has {page_words(current)} words; "
                             f"minimum is {min_extra_words}")
    pages = [render_lines(lines, metrics) for lines in layout]
    if not pages:
        raise ValueError("Empty rendered page")
    if len(pages) == 1:
        return pages[0]

    header = re.search(rb"(?:^|\r\n)([ \t]+)#name[ \t]+([0-9]+)[ \t]*\r\n\1#msg[ \t]*\r\n\Z",
                       script[max(0, offset - 128):offset])
    if header is None or int(header[2]) != row.get("speaker"):
        raise ValueError("Pagination requires a matching native #name/#msg header")
    indent = header[1]
    key = indent + b"#key\r\n"
    if not extended_pagination and not script[end:].startswith(key):
        raise ValueError("Pagination requires a native #key immediately after the source page")
    if extended_pagination and not script[end:].lstrip(b" \t\r\n").startswith((b"#", b"$", b"\\")):
        raise ValueError("Extended pagination requires a verified following command boundary")
    transition = key + indent + b"#name " + header[2] + b"\r\n" + indent + b"#msg\r\n"
    return transition.join(pages)


def choice_line(text, original, row, *, expanded=False, proportional=False, metrics=None):
    """Replace the label within the proven fixed-width menu format."""
    if expanded:
        match = re.fullmatch(rb"((?:[ \t]|\x81@)+%)([^\r\n]+)\r\n", original)
        if match is None:
            raise ValueError("Unrecognised choice record")
        prefix, source = match.groups()
        label = text.strip()
        if not re.fullmatch(r"[A-Za-z0-9 .,!?():';%/+&-]+", label):
            raise ValueError("Unsupported English choice glyph")
        quoted = source.startswith(b"\x81w") and source.endswith(b"\x81x")
        if proportional:
            from choice_layout import encode
            if metrics is None:
                metrics = wrap.Metrics(METRICS)
            encoded = encode(label, metrics)
        else:
            encoded = "".join("\u3000" if c == " " else chr(ord(c) + 0xFEE0)
                              for c in label).encode("cp932")
            if quoted:
                encoded = b"\x81w" + encoded + b"\x81x"
        return prefix + encoded + b"\r\n"
    match = re.fullmatch(rb"([ \t]+%\x81w)(.+)(\x81x\r\n)", original)
    if match is None or row.get("choice_block") is None:
        raise ValueError("Choice layout or menu association is unverified")
    label = text.strip()
    if not re.fullmatch(r"[A-Za-z0-9 ]+", label):
        raise ValueError("Choice labels support English letters, digits and spaces")
    width = len(match[2].decode("shift_jis_2004"))
    if len(label) > width:
        raise ValueError(f"Choice needs {len(label)} glyph cells; original label has {width}")
    full_width = "".join("\u3000" if char == " " else chr(ord(char) + 0xFEE0)
                         for char in label.ljust(width))
    encoded = full_width.encode("cp932")
    if len(encoded) != len(match[2]):
        raise ValueError("Choice source does not have a fixed-width glyph span")
    return match[1] + encoded + match[3]


def plan(rows, approved, *, allow_expansion=True, allow_pagination=True, fill_lines=False,
         min_extra_words=0, baseline_windows=None, expanded_choices=False,
         extended_pagination=False, preserve_clue_literals=False, proportional_choices=None):
    metrics = wrap.Metrics(METRICS)
    if proportional_choices is None:
        proportional_choices = metrics.meta.get("proportional_choices", False)
    resources = inventory()
    scripts, edits, errors = {}, {}, {}
    growth = {}
    with BASE.open("rb") as image:
        for key, revision in approved.items():
            row = rows[key]
            path = row["path"]
            if row["kind"] not in ("dialogue", "choice"):
                errors[key] = "No approved insertion path for " + row["kind"]
                continue
            try:
                if path not in scripts:
                    scripts[path] = extent(image, resources[path])
                offset = row["offset"]
                end = offset + row["size"]
                before = scripts[path][offset:end]
                if (before != bytes.fromhex(row["raw_hex"]) and
                        hashlib.sha256(before).hexdigest() != (baseline_windows or {}).get(key)):
                    raise ValueError("Page already differs in Combined1; baseline overlay requires separate review")
                if not before.endswith(b"\r\n"):
                    raise ValueError("Page has no proven CRLF boundary")
                if row["kind"] == "choice":
                    body = choice_line(revision["text"], before, row, expanded=expanded_choices,
                                       proportional=proportional_choices, metrics=metrics)
                    extra = max(0, len(body) - row["size"])
                    if extra and not allow_expansion:
                        raise ValueError("Choice expansion disabled")
                    if extra:
                        growth[path] = growth.get(path, 0) + extra
                    edits.setdefault(path, []).append((offset, end, body))
                    continue
                literals = CLUE_LITERALS.get(key, ()) if preserve_clue_literals else ()
                if any(literal not in row["jp"] for literal in literals):
                    raise ValueError("The protected clue literal does not match its Japanese source")
                if any(literal not in revision["text"] for literal in literals):
                    raise ValueError("Keep the source clue characters unchanged: " + ", ".join(literals))
                body = dialogue_body(revision["text"], metrics, scripts[path], row,
                                     allow_pagination=allow_pagination, fill_lines=fill_lines,
                                     min_extra_words=min_extra_words,
                                     extended_pagination=extended_pagination, native_literals=literals)
                extra = max(0, len(body) - row["size"])
                if extra and not allow_expansion:
                    raise ValueError(
                        f"Script expansion disabled: needs {len(body)} bytes including page controls; "
                        f"original window has {row['size']}"
                    )
                if extra:
                    growth[path] = growth.get(path, 0) + extra
                edits.setdefault(path, []).append((offset, end, b"\t" * (row["size"] - len(body)) + body))
            except ValueError as error:
                errors[key] = str(error)
        sectors = sum((len(scripts[path]) + extra + 2047) // 2048
                      for path, extra in growth.items())
        if BASE.stat().st_size // SECTOR + sectors > MAX_SECTORS:
            for key in approved:
                if rows[key]["path"] in growth:
                    errors[key] = "Combined expansion exceeds the 80-minute CD capacity"
    return scripts, edits, errors


def verify_disc(output, expected, *, subheaders=None, appended=(), base=BASE):
    subheaders = subheaders or {}
    remaining = set(expected)
    with base.open("rb") as original, output.open("rb") as patched:
        lba = 0
        while chunk := original.read(SECTOR * 512):
            after = patched.read(len(chunk))
            if len(after) != len(chunk):
                raise ValueError("Image truncated")
            if chunk == after:
                lba += len(chunk) // SECTOR
                continue
            for offset in range(0, len(chunk), SECTOR):
                a = chunk[offset:offset + SECTOR]
                b = after[offset:offset + SECTOR]
                if a != b:
                    payload = expected.get(lba)
                    if payload is None:
                        raise ValueError(f"Unexpected changed sector {lba}")
                    body = subheaders.get(lba, a[16:24]) + payload
                    edc = edc_compute(body).to_bytes(4, "little")
                    if b != a[:16] + body + edc + a[2076:]:
                        raise ValueError(f"Incorrect payload, EDC or ancillary bytes at {lba}")
                    remaining.remove(lba)
                lba += 1
        for index, raw in enumerate(appended):
            if patched.read(SECTOR) != raw:
                raise ValueError(f"Appended sector {index} differs")
        if remaining or patched.read(1):
            raise ValueError("Disc delta is incomplete or image length differs")


def build(db, rows, approved, name, *, allow_expansion=True, allow_pagination=True):
    if not approved:
        raise ValueError("No approved translations selected")
    scripts, edits, errors = plan(rows, approved, allow_expansion=allow_expansion,
                                  allow_pagination=allow_pagination)
    if errors:
        raise ValueError("\n".join(f"{key}: {error}" for key, error in errors.items()))
    cue = build_image(scripts, edits, name)
    with db:
        db.executemany("INSERT INTO insertion VALUES (?,?,?)",
                       ((name, key, row["number"]) for key, row in approved.items()))
    return cue


def build_image(scripts, edits, name):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Invalid build name")
    output = Path(os.environ.get("KINDAICHI_EDITOR_OUTPUT", "build")) / f"patched_{name}.bin"
    cue = output.with_suffix(".cue")
    staging = Path("build") / f"translation_{name}"
    if any(path.exists() for path in (output, cue, staging)):
        raise ValueError("Choose a new build name")
    with BASE.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != BASE_SHA256:
            raise ValueError("Frozen disc hash mismatch")
    cue_data = BASE.with_suffix(".cue").read_bytes()
    if cue_data.splitlines() != [f'FILE "{BASE.name}" BINARY'.encode(),
                                 b"  TRACK 01 MODE2/2352", b"    INDEX 01 00:00:00"]:
        raise ValueError("Frozen CUE shape mismatch")
    staging.mkdir()
    resources = inventory()
    manifest, expected, subheaders = [], {}, {}
    expanded = {}
    for index, (path, spans) in enumerate(sorted(edits.items())):
        source = scripts[path]
        result = bytearray()
        end = 0
        for start, stop, replacement in sorted(spans):
            if not end <= start < stop <= len(source):
                raise ValueError("Overlapping or out-of-range page")
            result.extend(source[end:start])
            result.extend(replacement)
            end = stop
        result.extend(source[end:])
        if len(result) != len(source):
            expanded[path] = bytes(result)
            continue
        local = staging / f"{index}.TXT"
        local.write_bytes(result)
        manifest.append(dict(iso_path=path, local_file=str(local)))
        for start in range(0, len(source), 2048):
            old = source[start:start + 2048]
            new = result[start:start + 2048]
            if old != new:
                expected[resources[path]["lba"] + start // 2048] = bytes(new).ljust(2048, b"\0")
    metadata, appended, relocated = {}, [], {}
    if expanded:
        with BASE.open("rb") as base:
            metadata, appended, relocated = allocate(base, expanded)
        if expected.keys() & metadata.keys():
            raise ValueError("Script data overlaps ISO metadata")
        expected.update(metadata)
    if not expected:
        raise ValueError("No payload changes")
    candidate = staging / "candidate.bin"
    with BASE.open("rb") as source, candidate.open("xb") as target:
        shutil.copyfileobj(source, target, 1 << 20)
    patch(str(candidate), manifest, recompute_edc=True)
    if relocated:
        import json
        from script_allocator import sector
        with BASE.open("rb") as base, candidate.open("r+b") as target:
            for lba, payload in metadata.items():
                body = sector(base, lba)[16:24] + payload
                target.seek(lba * SECTOR + 24)
                target.write(payload + edc_compute(body).to_bytes(4, "little"))
            target.seek(0, 2)
            target.writelines(appended)
        records = {r["path"]: r for r in json.loads(Path("extract/filelist.json").read_text())}
        with candidate.open("rb") as image:
            verify_extents(image, relocated, expanded, records)
    verify_disc(candidate, expected, appended=appended)
    preserve_release_movies(candidate)
    candidate.rename(output)
    with cue.open("xb") as file:
        file.write(cue_data.replace(BASE.name.encode(), output.name.encode()))
    return cue

