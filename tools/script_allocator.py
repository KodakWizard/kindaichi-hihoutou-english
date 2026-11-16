"""Allocate expanded story scripts at the end of the Combined1 ISO volume."""
import json
from pathlib import Path

from extract_iso import parse_dir_records
from patch_bin import edc_compute
from translation_source import SECTOR, extent

SYNC = b"\0" + b"\xff" * 10 + b"\0"


def dual32(value):
    return value.to_bytes(4, "little") + value.to_bytes(4, "big")


def sector(image, lba):
    image.seek(lba * SECTOR)
    raw = image.read(SECTOR)
    if len(raw) != SECTOR:
        raise ValueError(f"Missing sector {lba}")
    return raw


# CD-ROM Reed-Solomon parity lookup tables, GF(256) polynomial 0x11D.
FORWARD = tuple((value << 1) ^ (0x11D if value & 0x80 else 0) for value in range(256))
BACKWARD = {value ^ product: value for value, product in enumerate(FORWARD)}
MAX_SECTORS = 80 * 4500 - 150


def parity(data, major_count, minor_count, major_step, minor_step):
    first, second = bytearray(), bytearray()
    span = major_count * minor_count
    for major in range(major_count):
        index = (major >> 1) * major_step + (major & 1)
        a = b = 0
        for _ in range(minor_count):
            value = data[index]
            index = (index + minor_step) % span
            a = FORWARD[a ^ value]
            b ^= value
        a = BACKWARD[FORWARD[a] ^ b]
        first.append(a)
        second.append(a ^ b)
    return bytes(first + second)


def form1(lba, payload, final):
    minute, rest = divmod(lba + 150, 4500)
    second, frame = divmod(rest, 75)
    header = bytes((int(f"{value:02d}", 16) for value in (minute, second, frame, 2)))
    body = (b"\0\0" + (b"\x89" if final else b"\x08") + b"\0") * 2 + payload
    body += edc_compute(body).to_bytes(4, "little")
    protected = bytes(4) + body
    p = parity(protected, 86, 24, 2, 86)
    q = parity(protected + p, 52, 43, 86, 88)
    return SYNC + header + body + p + q


def allocate(image, expanded):
    """Return changed metadata payloads, appended raw sectors and new extents."""
    records = {r["path"]: r for r in json.loads(Path("extract/filelist.json").read_text())}
    volume, tail = divmod(image.seek(0, 2), SECTOR)
    descriptor = bytearray(sector(image, 16)[24:2072])
    if tail or descriptor[:7] != b"\x01CD001\x01" or descriptor[80:88] != dual32(volume):
        raise ValueError("Unexpected ISO volume layout")
    if sector(image, 17)[24:31] != b"\xffCD001\x01":
        raise ValueError("Unexpected secondary ISO volume descriptor")
    if any(r["lba"] + (r["size"] + 2047) // 2048 > volume for r in records.values()):
        raise ValueError("ISO extent exceeds the baseline volume")
    metadata, appended, relocated = {}, [], {}
    cursor = volume
    for path, script in sorted(expanded.items()):
        parent = str(Path(path).parent).replace("\\", "/")
        source, directory = records[path], records[parent]
        if parent not in ("/IN", "/OUT") or not path.endswith(".TXT") or source["flags"]:
            raise ValueError(f"Unsupported story script extent: {path}")
        count = (len(script) + 2047) // 2048
        if cursor + count > MAX_SECTORS:
            raise ValueError("Expanded scripts exceed the 80-minute CD capacity")
        lba, offset = divmod(source["rec_off"], 2048)
        lba += directory["lba"]
        if lba not in metadata:
            metadata[lba] = bytearray(sector(image, lba)[24:2072])
        data = metadata[lba]
        fid = source["fid"].encode("ascii")
        if (data[offset + 2:offset + 10] != dual32(source["lba"])
                or data[offset + 10:offset + 18] != dual32(source["size"])
                or data[offset + 32] != len(fid)
                or data[offset + 33:offset + 33 + len(fid)] != fid
                or any(data[offset + index] for index in (1, 25, 26, 27))):
            raise ValueError(f"Unexpected ISO directory record: {path}")
        data[offset + 2:offset + 10] = dual32(cursor)
        data[offset + 10:offset + 18] = dual32(len(script))
        relocated[path] = dict(source, lba=cursor, size=len(script))
        for index in range(count):
            payload = script[index * 2048:(index + 1) * 2048].ljust(2048, b"\0")
            appended.append(form1(cursor + index, payload, index == count - 1))
        cursor += count
    descriptor[80:88] = dual32(cursor)
    metadata[16] = descriptor
    return {lba: bytes(data) for lba, data in metadata.items()}, appended, relocated


def verify_extents(image, relocated, expanded, resources):
    for path, record in relocated.items():
        directory = resources[path.rsplit("/", 1)[0]]
        matches = [row for row in parse_dir_records(extent(image, directory))
                   if row["fid"] == record["fid"]]
        if (len(matches) != 1 or matches[0]["lba"] != record["lba"]
                or matches[0]["size"] != record["size"]
                or extent(image, matches[0]) != expanded[path]):
            raise ValueError(f"Relocated script verification failed: {path}")
