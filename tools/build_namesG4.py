"""Build and verify the G4 speaker-name test disc from the project root."""
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import addr_flow
import mips_microasm as mips
from patch_bin import BIN_ORIG, edc_compute, patch
from verify_exe_build import diff_ranges, sha1_file, BIN_SHA1
from xref_scan import FILE_OF, find_refs

BASE = Path("build/SLPS_006.01_asciiG4")
BASE_SHA = "4B2EAE489CF43529711ED1D59EE95F2B9F4FA006"
EXE = Path("build/SLPS_006.01_namesG4b")
BIN = Path("build/patched_namesG4b.bin")
CUE = BIN.with_suffix(".cue")
MIRROR = Path("build/SLPS_006.01_namesG4b_armips")
TABLE = Path("build/namesG4b_table.bin")
ASM = Path("tools/namesG4.asm")
CAVE = 0x8005F8C8
NAME = 0x8002BC30

# Original tags at EXE 0x6A620, indexed by the script's #name ID.
# Kana spellings follow the source. ID 13 is Hazuki Mitsusada.
NAMES = (
    ("", ""), ("美雪", "Miyuki"), ("剣持", "Kenmochi"), ("いつき", "Itsuki"),
    ("クリス", "Chris"), ("マユラ", "Mayura"), ("三村", "Mimura"),
    ("真奈美", "Manami"), ("ヨネ", "Yone"), ("遠藤", "Endo"), ("灯妙", "Tomyo"),
    ("金田一", "Hajime"), ("木暮", "Kogure"), ("光定", "Hazuki"),
    ("マサキ", "Masaki"), ("マユキ", "Mayuki"), ("岩田", "Iwata"),
    ("茅", "Kaya"), ("小百合", "Sayuri"),
)


def build_table(base):
    metrics = json.loads(Path("build/w_metrics_g4.json").read_text(encoding="utf8"))
    assert metrics["renderer_sha1"] == BASE_SHA
    table = bytearray(len(NAMES) * 2)
    for ident, (jp, english) in enumerate(NAMES):
        source = base[0x6A620 + ident * 8:0x6A628 + ident * 8]
        assert source == jp.ljust(3, "　").encode("cp932") + bytes(2), ident
        struct.pack_into("<H", table, ident * 2, len(table))
        scale_offset = len(table)
        table.extend(bytes(2))
        bounds = []
        previous = None
        origin = 0
        for letter in english:
            glyph = metrics["claim"][str(ord(letter))]
            pq = base[FILE_OF(0x8004DD7E + ord(letter) - 0x21)]
            assert pq == glyph["P"] * 16 + glyph["Q"]
            assert glyph["code"] == 0x8200 + ord(letter) + 0x1F + letter.islower()
            if previous:
                origin += previous["P"] + glyph["Q"]
                assert origin + glyph["L"] > previous_right, english
            assert 0 <= origin < 96, english
            previous = glyph
            previous_right = origin + glyph["R"]
            bounds.append((origin + glyph["L"], previous_right))
            table.extend((glyph["code"] & 255, origin))
        table.extend(bytes(2))
        if previous:
            # Keep a two-pixel margin before the body starts at x=-96.
            max_scale = min(256, (46 * 256 - 1) // previous_right)
            scale = next(candidate for candidate in range(max_scale, 127, -1)
                         if all(right * candidate // 256 + 1 < left * candidate // 256
                                for (_, right), (left, _) in zip(bounds, bounds[1:])))
            assert previous_right * scale // 256 <= 45
            struct.pack_into("<H", table, scale_offset, scale)
            print(f"[STATIC] {ident:2} {english:9} ink ends at x={-144 + previous_right * scale // 256}")
    ids = set()
    for folder in ("IN", "OUT"):
        for script in (Path("extract/iso") / folder).glob("*.TXT"):
            ids.update(map(int, re.findall(rb"(?m)^[ \t]*#name[ \t]+(\d+)\b",
                                           script.read_bytes())))
    assert ids == set(range(len(NAMES))), ids
    return bytes(table)


def verify_texture_space():
    # Check the dedicated 48x16, 4-bpp strip against all texture uploads.
    for ext in ("TXP", "TEX", "PAL"):
        for path in Path("extract/iso").rglob(f"*.{ext}"):
            data = path.read_bytes()
            offset = 0
            for group in range(2 if ext == "TXP" else 1):
                count, = struct.unpack_from("<I", data, offset)
                offset += 4
                for _ in range(count):
                    if group or ext == "PAL":
                        offset += 4
                    x, y, width, height = struct.unpack_from("<4H", data, offset)
                    offset += 8 + 2 * width * height
                    assert not (x < 972 and x + width > 960 and
                                y < 512 and y + height > 496), path
            assert offset == len(data), path


def assemble(base, table):
    """Encode the limited instruction set used in the patch and compare armips."""
    code = []
    labels = {}
    pc = 0
    for line in ASM.read_text().splitlines():
        line = line.partition(";")[0].strip()
        if not line or line.startswith((".psx", ".open", ".close")):
            continue
        if line.startswith(".org"):
            pc = int(line.split()[1], 0)
        elif line.startswith(".incbin"):
            code.append((pc, table))
            pc += len(table)
        elif line.startswith(".align"):
            padding = bytes(-pc % int(line.split()[1]))
            code.append((pc, padding))
            pc += len(padding)
        elif line.endswith(":"):
            labels[line[:-1]] = pc
        else:
            code.append((pc, line))
            pc += 4
    result = bytearray(base)
    assert labels["renderer_end"] <= 0x8002BD6C
    assert labels["names_end"] <= 0x8005FB38
    for pc, entry in code:
        if isinstance(entry, bytes):
            result[FILE_OF(pc):FILE_OF(pc) + len(entry)] = entry
            continue
        op, _, operands = entry.partition(" ")
        args = operands.split(",") if operands else []
        if op in ("j", "jal", "beq", "bne"):
            dest = args.pop()
            target = labels[dest] if dest in labels else int(dest, 0)
            if op in ("j", "jal"):
                assert (pc + 4) >> 28 == target >> 28
                args = [target]
            else:
                args.extend((pc, target))
        elif op in ("lw", "lbu", "lhu", "sw", "sb", "sh"):
            disp, reg = args.pop().split("(")
            args.extend((reg[:-1], int(disp, 0)))
        elif op in ("sll", "srl"):
            args = [args[1], args[0], int(args[2], 0)]
        elif op == "multu":
            rs, rt = map(mips.reg, args)
            word = mips._special(rs, rt, 0, 0, 0x19)
        elif op == "mflo":
            word = mips._special(0, 0, mips.reg(args[0]), 0, 0x12)
        elif op == "sllv":
            rd, rt, rs = map(mips.reg, args)
            word = mips._special(rs, rt, rd, 0, 4)
        elif op in ("ori", "andi", "lui", "sltiu", "addiu"):
            args[-1] = int(args[-1], 0)
        if op not in ("sllv", "multu", "mflo"):
            word = 0 if op == "nop" else getattr(mips, f"enc_{op}")(*args)
        struct.pack_into("<I", result, FILE_OF(pc), word)
    return bytes(result), labels



def audit_code(base, image, labels):
    from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

    ranges = [(NAME, labels["renderer_end"]), (CAVE, labels["names_end"])]
    assert base[FILE_OF(NAME):FILE_OF(NAME) + 8] == bytes.fromhex("d0ffbd272c00bfaf")
    assert all(a == b or any(lo <= offset + 0x8000F800 < hi for lo, hi in ranges)
               for offset, (a, b) in enumerate(zip(base, image)))
    for current in (base, image):
        renderer_refs = find_refs(current, [(NAME, 0x8002BD6C)])
        assert [r for r in renderer_refs if not NAME <= r[0] < 0x8002BD6C] == [
            (0x8003C4D0, "j/jal", "jal -> 8002BC30")]
        cave_refs = find_refs(current, [(CAVE, 0x8005FB38)])
        external = [r for r in cave_refs if not CAVE <= r[0] < 0x8005FB38]
        assert len(external) == (0 if current is base else 2), external
        assert all(NAME <= pc < labels["renderer_end"] and kind == "j/jal"
                   for pc, kind, _ in external), external
        assert not any(kind == "ptr" for _, kind, _ in cave_refs)
    for path in Path("extract/iso").rglob("*"):
        if not path.is_file() or path.suffix == ".STR":
            continue
        data = path.read_bytes()
        assert not any(CAVE <= word < 0x8005FB38 for word, in
                       struct.iter_unpack("<I", data[:len(data) & ~3])), path

    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    for lo, hi in [(NAME, labels["renderer_end"]), (labels["paint"], labels["names_end"])]:
        words = addr_flow.words_from_file(image, lo, hi)
        allowed = {CAVE, 0x800BE408, 0x800BE410, 0x800BE412, 0x800BE414,
                   0x800BE44C, 0x800BE44E, 0x800BE450,
                   0x800C23E0, 0x800C23E2, 0x800C23E4,
                   0x800C241C, 0x800C241E, 0x800C2420, 0x8009344C}
        _, errors = addr_flow.assert_addresses(words, allowed)
        assert not errors, errors
        decoded = list(md.disasm(image[FILE_OF(lo):FILE_OF(hi)], lo))
        assert len(decoded) == len(words)
        for index, (pc, word) in enumerate(words):
            opcode, funct = word >> 26, word & 63
            if opcode in (2, 3, 4, 5) or (opcode == 0 and funct == 8):
                assert words[index + 1][1] == 0, hex(pc)
            if opcode in (4, 5):
                target = pc + 4 + addr_flow.s16(word & 65535) * 4
                assert lo <= target < hi
        for instruction in decoded:
            print(f"{instruction.address:08X} {instruction.mnemonic:7} {instruction.op_str}")


def verify_disc(image, original=Path("build/patched_asciiG4.bin")):
    assert sha1_file(BIN_ORIG) == BIN_SHA1
    assert original.stat().st_size == BIN.stat().st_size == 682458672
    filelist = json.loads(Path("extract/filelist.json").read_text())
    exe = next(entry for entry in filelist if entry["path"] == "/SLPS_006.01")
    start = exe["lba"]
    count = (len(image) + 2047) // 2048
    changed = {left // 2352 for left, _ in diff_ranges(original, BIN)}
    with original.open("rb") as old, BIN.open("rb") as new:
        old.seek(start * 2352)
        new.seek(start * 2352)
        expected = set()
        for i in range(count):
            before, after = old.read(2352), new.read(2352)
            payload = image[2048 * i:2048 * (i + 1)].ljust(2048, b"\0")
            assert after[24:2072] == payload
            if before[24:2072] != payload:
                expected.add(start + i)
                assert before[:24] == after[:24] and before[2076:] == after[2076:]
                assert struct.unpack_from("<I", after, 2072)[0] == edc_compute(after[16:2072])
            else:
                assert before == after
        assert changed == expected
    assert CUE.read_bytes() == original.with_suffix(".cue").read_bytes().replace(
        original.name.encode(), BIN.name.encode())
    print(f"[STATIC] PASS. {original.stem} disc diff confined to EXE sectors {sorted(changed)}.")


def main():
    for path in (EXE, BIN, CUE):
        if path.exists():
            raise FileExistsError(path)
    base = BASE.read_bytes()
    assert hashlib.sha1(base).hexdigest().upper() == BASE_SHA
    assert mips.self_test(verbose=False)
    verify_texture_space()
    table = build_table(base)
    image, labels = assemble(base, table)
    audit_code(base, image, labels)
    TABLE.write_bytes(table)
    assembly = subprocess.run(["tools/armips/armips.exe", str(ASM)],
                              capture_output=True, text=True, check=True)
    assert not assembly.stderr and "warning" not in assembly.stdout.lower(), assembly
    assert MIRROR.read_bytes() == image
    EXE.write_bytes(image)
    with Path("build/patched_asciiG4.bin").open("rb") as source, BIN.open("xb") as output:
        shutil.copyfileobj(source, output, length=1 << 20)
    patch(str(BIN), [{"iso_path": "/SLPS_006.01", "local_file": str(EXE)}],
          recompute_edc=True)
    subprocess.run([sys.executable, "tools/write_cue.py", BIN.stem], check=True)
    verify_disc(image)


if __name__ == "__main__":
    main()
