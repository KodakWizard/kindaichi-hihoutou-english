"""Build or apply a standalone SLPS-00601 clean-disc patch using stock-relative XOR."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

STOCK_SHA1 = "65f582ffe78b5fa55e45d0c163ba2e67a5b15e15"
STOCK_SIZE = 682458672
BLOCK = 65536
RECORD = struct.Struct("<I")
FORMAT = "SLPS-00601-xor-v1"


def stock_path(selection):
    path = Path(str(selection).strip().strip('"')).resolve(strict=True)
    if path.suffix.lower() == ".cue":
        cue = path.read_text(encoding="utf-8-sig", errors="replace")
        files = re.findall(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))\s+BINARY\s*$', cue, re.I | re.M)
        tracks = re.findall(r'^\s*TRACK\s+\d+\s+(\S+)', cue, re.I | re.M)
        if len(files) != 1 or [track.upper() for track in tracks] != ["MODE2/2352"]:
            raise ValueError("Select the original single-track MODE2/2352 CUE or BIN.")
        path = (path.parent / (files[0][0] or files[0][1])).resolve(strict=True)
    return path


def check_stock(file):
    if (os.fstat(file.fileno()).st_size != STOCK_SIZE
            or hashlib.file_digest(file, "sha1").hexdigest() != STOCK_SHA1):
        raise ValueError("The input does not match the supported clean SLPS-00601 disc.")
    file.seek(0)


def read_mask(stock, length):
    data = stock.read(length)
    if len(data) < length:
        stock.seek(0)
        data += stock.read(length - len(data))
    return data


def xor(left, right):
    return (int.from_bytes(left, "little") ^ int.from_bytes(right, "little")).to_bytes(len(left), "little")


def publish(candidate, output):
    if os.name == "nt":
        candidate.rename(output)
    else:
        os.link(candidate, output)


def create_patch(clean, target, output):
    """Publish after independently applying the encoded delta and verifying the target hash."""
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        candidate = Path(folder) / "patch.pyz"
        with stock_path(clean).open("rb") as source, target.open("rb") as translated:
            check_stock(source)
            size = os.fstat(translated.fileno()).st_size
            if size < STOCK_SIZE or size % 2352:
                raise ValueError("The translated BIN has an invalid raw disc size.")
            digest = hashlib.sha256()
            with ZipFile(candidate, "w", ZIP_DEFLATED) as archive:
                archive.write(__file__, "__main__.py")
                with archive.open("delta.bin", "w") as delta:
                    for index in range((size + BLOCK - 1) // BLOCK):
                        new = translated.read(BLOCK)
                        old = read_mask(source, len(new))
                        digest.update(new)
                        if old != new:
                            delta.write(RECORD.pack(index))
                            delta.write(xor(old, new))
                archive.writestr("manifest.json", json.dumps(
                    dict(format=FORMAT, size=size, sha256=digest.hexdigest())))
            source.seek(0)
            apply_delta(candidate, source, None)
        publish(candidate, output)
    return digest.hexdigest()


def apply_delta(package, source, destination):
    with ZipFile(package) as archive:
        info = json.loads(archive.read("manifest.json"))
        size = info["size"]
        if info["format"] != FORMAT or size < STOCK_SIZE or size % 2352:
            raise ValueError("Invalid clean-disc patch manifest.")
        digest = hashlib.sha256()
        with archive.open("delta.bin") as delta:
            header = delta.read(RECORD.size)
            for index, offset in enumerate(range(0, size, BLOCK)):
                length = min(BLOCK, size - offset)
                data = read_mask(source, length)
                if header:
                    changed, = RECORD.unpack(header)
                    if changed < index:
                        raise ValueError("Patch blocks are out of order.")
                    if changed == index:
                        change = delta.read(length)
                        if len(change) != length:
                            raise ValueError("Truncated patch block.")
                        data = xor(data, change)
                        header = delta.read(RECORD.size)
                digest.update(data)
                if destination is not None:
                    destination.write(data)
            if header or digest.hexdigest() != info["sha256"]:
                raise ValueError("Patched disc checksum mismatch.")


def apply_patch(package, selection, output):
    output = output.resolve()
    if output.suffix.lower() != ".bin" or any(c in output.name for c in '"\r\n'):
        raise ValueError("Choose a valid .bin output filename.")
    cue = output.with_suffix(".cue")
    if output.exists() or cue.exists():
        raise FileExistsError("Choose a fresh BIN/CUE output name.")
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        temp = Path(folder) / "candidate.bin"
        temp_cue = temp.with_suffix(".cue")
        with stock_path(selection).open("rb") as clean, temp.open("wb") as target:
            check_stock(clean)
            apply_delta(package, clean, target)
        temp_cue.write_text(f'FILE "{output.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
                            encoding="utf-8")
        publish(temp, output)
        try:
            publish(temp_cue, cue)
        except OSError:
            output.unlink()
            raise
    return cue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clean", help="Path to the original SLPS-00601 CUE or BIN")
    parser.add_argument("output", type=Path, help="Path to a new translated .bin")
    args = parser.parse_args()
    try:
        print(apply_patch(Path(sys.argv[0]), args.clean, args.output))
    except (OSError, ValueError, KeyError, struct.error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
