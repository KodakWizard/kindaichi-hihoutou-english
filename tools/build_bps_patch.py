"""Build BPS1 from a raw PlayStation disc and a translated image."""
import argparse
import hashlib
import json
import mmap
from pathlib import Path
import struct
from zlib import crc32

from clean_disc_patch import check_stock, stock_path

BLOCK = 65536
ANCHOR = 12


def number(value):
    result = bytearray()
    while value >= 128:
        result.append(value & 127)
        value = (value >> 7) - 1
    result.append(value | 128)
    return result


def source_index(source, inventory):
    """Index raw script/EXE payloads so relocated original bytes can be copied."""
    index = {}
    for record in inventory:
        path = record["path"]
        if not (path.endswith(".TXT") or path == "/SLPS_006.01"):
            continue
        for offset in range(0, record["size"], 2048):
            start = (record["lba"] + offset // 2048) * 2352 + 24
            data = source[start:start + min(2048, record["size"] - offset)]
            for i in range(len(data) - ANCHOR + 1):
                index.setdefault(data[i:i + ANCHOR], start + i)
    return index


def build(source, target, inventory):
    index = source_index(source, inventory)
    patch = bytearray(b"BPS1") + number(len(source)) + number(len(target)) + number(0)
    relative = 0
    pos = 0
    literals = bytearray()

    def flush():
        if literals:
            patch.extend(number(((len(literals) - 1) << 2) | 1))
            patch.extend(literals)
            literals.clear()

    while pos < len(target):
        if pos < len(source) and source[pos] == target[pos]:
            flush()
            begin = pos
            limit = min(len(source), len(target))
            while pos + BLOCK <= limit and source[pos:pos+BLOCK] == target[pos:pos+BLOCK]:
                pos += BLOCK
            while pos < limit and source[pos] == target[pos]:
                pos += 1
            patch.extend(number((pos - begin - 1) << 2))
            continue
        match = index.get(target[pos:pos + ANCHOR])
        if match is not None:
            flush()
            size = ANCHOR
            while match + size < len(source) and pos + size < len(target) and source[match + size] == target[pos + size]:
                size += 1
            delta = match - relative
            patch.extend(number(((size - 1) << 2) | 2))
            patch.extend(number((abs(delta) << 1) | (delta < 0)))
            relative = match + size
            pos += size
            continue
        literals.append(target[pos])
        pos += 1
    flush()
    patch.extend(struct.pack("<II", crc32(source), crc32(target)))
    patch.extend(struct.pack("<I", crc32(patch)))
    return patch


def verify(patch, source, target):
    """Replay BPS commands and compare the resulting SHA-256 with the target."""
    assert patch[:4] == b"BPS1"
    source_crc, target_crc, patch_crc = struct.unpack("<III", patch[-12:])
    assert (source_crc, target_crc, patch_crc) == (crc32(source), crc32(target), crc32(patch[:-4]))
    cursor, pos, relative = 4, 0, 0
    digest = hashlib.sha256()

    def read():
        nonlocal cursor
        value, shift = 0, 1
        while True:
            byte = patch[cursor]
            cursor += 1
            value += (byte & 127) * shift
            if byte & 128:
                return value
            shift <<= 7
            value += shift

    assert read() == len(source) and read() == len(target)
    metadata = read()
    cursor += metadata
    while cursor < len(patch) - 12:
        command = read()
        size = (command >> 2) + 1
        action = command & 3
        if action == 0:
            data = source[pos:pos+size]
        elif action == 1:
            data = patch[cursor:cursor+size]
            cursor += size
        elif action == 2:
            offset = read()
            relative += -(offset >> 1) if offset & 1 else offset >> 1
            assert relative >= 0
            data = source[relative:relative+size]
            relative += size
        else:
            raise ValueError("Unexpected BPS action")
        assert len(data) == size
        pos += size
        digest.update(data)
    assert cursor == len(patch)-12 and pos == len(target)
    assert digest.digest() == hashlib.sha256(target).digest()
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clean")
    parser.add_argument("target", type=Path)
    parser.add_argument("output", type=Path)
    opts = parser.parse_args()
    inventory = json.loads(Path("extract/filelist.json").read_text(encoding="utf-8"))
    with stock_path(opts.clean).open("rb") as clean, opts.target.open("rb") as translated:
        check_stock(clean)
        with mmap.mmap(clean.fileno(), 0, access=mmap.ACCESS_READ) as source, mmap.mmap(
                translated.fileno(), 0, access=mmap.ACCESS_READ) as target:
            patch = build(source, target, inventory)
            sha = verify(patch, source, target)
    with opts.output.open("xb") as file:
        file.write(patch)
    print(f"{opts.output}: {len(patch)} bytes, output SHA-256 {sha}")


if __name__ == "__main__":
    main()
