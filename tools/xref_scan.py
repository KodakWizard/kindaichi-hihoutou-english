"""Shared reference scanner for the SLPS_006.01 PS-X EXE image.

find_refs(data, ranges) -> list of (ram, kind, detail) for every way the given
RAM ranges could be referenced from inside the EXE. Deliberately over-eager:
false positives (data decoded as instructions) are acceptable, missed
references are not. Kinds:

  ptr        raw little-endian u32 word whose value lies in the range
             (function-pointer tables, .data constants, script blocks)
  j/jal      absolute jump/call (opcode 0x08 / 0x03, 26-bit target)
  branch     PC-relative branch (beq/bne/blez/bgtz/REGIMM)
  lui+imm    lui + ori/addiu pair materialising an address in the range
  lui+ld/st  lui + load/store pair addressing the range
  gp+ld/st   $gp-relative (gp = 0x80093210) load/store addressing the range
  delay      the word right after a branch/jump, i.e. the branch-delay slot
             (a patched word there would be executed as the delay instruction)

This is the same detection logic as tools/verify_cave.py, factored into a
reusable function so patch builders can assert deadness before and after
patching.
"""
import struct
import capstone

EXE_FILE = 'extract/iso/SLPS_006.01'
HEADERSIZE = 0x8000F800
RAM_OF = lambda off: off + HEADERSIZE
FILE_OF = lambda ram: ram - HEADERSIZE
GP = 0x80093210

_LOADSTORE = (0x20, 0x21, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2B, 0x2C, 0x28 + 0x20, 0xA0, 0xA1)
_IMM_ARITH = (0x0C, 0x0D, 0x09, 0x0B)


def _md():
    md = capstone.Cs(capstone.CS_ARCH_MIPS,
                     capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    return md


def find_refs(data, ranges, base_file=0x800, scan_pointers=True):
    """data = full EXE image bytes. ranges = [(lo, hi_exclusive), ...] RAM."""
    hits = []

    def inr(v):
        for lo, hi in ranges:
            if lo <= v < hi:
                return True
        return False

    if scan_pointers:
        for o in range(0, len(data) - 3, 4):
            w = struct.unpack_from('<I', data, o)[0]
            if inr(w):
                hits.append((RAM_OF(o), 'ptr', f'u32={w:08X}'))

    md = _md()
    prev_lui = None
    for o in range(base_file, len(data) - 3, 4):
        ram = RAM_OF(o)
        w = struct.unpack_from('<I', data, o)[0]
        op = w >> 26
        raw = data[o:o + 4]
        dec = list(md.disasm(raw, ram, count=1))
        nm = dec[0].mnemonic if dec else f'op{op:02X}'
        imm16 = w & 0xFFFF
        offs16 = imm16 - 0x10000 if imm16 & 0x8000 else imm16
        if op in (0x02, 0x03):
            tgt = (ram & 0xF0000000) | ((w & 0x03FFFFFF) << 2)
            if inr(tgt):
                hits.append((ram, 'j/jal', f'{nm} -> {tgt:08X}'))
            nxt = struct.unpack_from('<I', data, o + 4)[0] if o + 7 < len(data) else None
            if nxt is not None and (op == 0x02 or op == 0x03) and inr(ram + 4):
                hits.append((ram, 'delay', f'{nm} delay slot at {ram + 4:08X}'))
        elif op in (0x01, 0x04, 0x05, 0x06, 0x07):
            imm = w & 0xFFFF
            if imm & 0x8000:
                imm -= 0x10000
            tgt = ram + 4 + imm * 4
            if inr(tgt):
                hits.append((ram, 'branch', f'{nm} -> {tgt:08X}'))
            if inr(ram + 4):
                hits.append((ram, 'delay', f'{nm} delay slot at {ram + 4:08X}'))
        if op == 0x0F:
            prev_lui = (ram, (w >> 16) & 0x1F, (w & 0xFFFF) << 16)
            continue
        if prev_lui and prev_lui[0] == ram - 4 and prev_lui[1] == ((w >> 21) & 0x1F):
            base = prev_lui[2]
            imm = w & 0xFFFF
            offs = imm - 0x10000 if imm & 0x8000 else imm
            if op in _IMM_ARITH:
                addr = (base | imm) if op == 0x0C else base + offs
                if inr(addr):
                    hits.append((prev_lui[0], 'lui+imm', f'{nm} {addr:08X}'))
            elif op in _LOADSTORE:
                addr = base + offs
                if inr(addr):
                    hits.append((prev_lui[0], 'lui+ld/st', f'{nm} {addr:08X}'))
        prev_lui = None
        if op in _LOADSTORE and ((w >> 21) & 0x1F) == 28:
            addr = GP + offs16
            if inr(addr):
                hits.append((ram, 'gp+ld/st', f'{nm} {addr:08X}'))

    seen, uniq = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    return uniq


def load_exe(path=EXE_FILE):
    return open(path, 'rb').read()


if __name__ == '__main__':
    import sys
    data = load_exe()
    rs = []
    for a in sys.argv[1:] or ['8004DCD0..8004DFAC']:
        lo, hi = a.split('..')
        rs.append((int(lo, 16), int(hi, 16)))
    for h in find_refs(data, rs):
        print('%08X %-10s %s' % h)
    print('total', len(find_refs(data, rs)))
