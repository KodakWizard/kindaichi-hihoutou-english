"""Address-materialisation data-flow tracker for MIPS cave code (shared gate).

Why this exists: the 2026-11-08 asciiB2 build addressed its map table with
    lui $t2,0x8004 ; addiu $t2,$t2,-0x229c
and the builder's own comment asserted that produced 0x8004DD64. It does not:
`addiu` SIGN-EXTENDS, so 0x80040000 + 0xFFFFDD64 = 0x8003DD64 — one PAGE lower,
which is live .text. The map lookup therefore read machine code, the build
"worked" statically, and in-game the line died at the first ASCII byte and the
game was left with bogus glyph records (lingering/overlapping graphics).

Rule this encodes: to materialise a 32-bit address whose low half is >= 0x8000,
use `lui` + `ori` (zero-extends). `lui` + `addiu` is only correct when the low
half is < 0x8000.

`track(words)` walks a list of (pc, word) and returns the addresses the code
ACTUALLY computes, as (pc, kind, value) tuples:
  'page'                  a `lui` page base
  'lui+ori (zero-ext)'    the value formed by `lui` then `ori rt,$zero,imm`/`ori rt,rt,imm`
  'lui+addiu (sign-ext)'  the value formed by `lui` then `addiu`
  'base+index'            an indexed base register used with addu
  'load/store disp'       base register + signed displacement of a load/store
"""
import struct

REG = (['$zero', '$at', '$v0', '$v1', '$a0', '$a1', '$a2', '$a3']
       + ['$t%d' % i for i in range(10)]
       + ['$s%d' % i for i in range(8)]
       + ['$k0', '$k1', '$gp', '$sp', '$fp', '$ra'])

# Primary opcode fields, re-derived from the words they came from (the earlier
# guess of 0x27 for addiu is WRONG — 0x27 is lwc1's slot inside the load/store
# range; addiu is 0x09, confirmed from 0x254ADD64 = `addiu $t2,$t2,-0x229c`).
OP_LUI, OP_ORI, OP_ADDIU = 0x0F, 0x0D, 0x09
LOAD_OPS = (0x20, 0x21, 0x22, 0x23, 0x24, 0x25)          # lb lh lwl lw lbu lhu
LOAD_STORE_OPS = LOAD_OPS + (0x28, 0x29, 0x2A, 0x2B)     # + sb sh sw slc?/lwc1 slot
FUNCT_ADDU = 0x21


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


def words_from_file(buf, lo_ram, hi_ram, base=0x80010000, hdr=0x800):
    """Read the word run [lo_ram, hi_ram) out of a PS-X EXE image buffer."""
    out = []
    for pc in range(lo_ram, hi_ram, 4):
        out.append((pc, struct.unpack_from('<I', buf, pc - base + hdr)[0]))
    return out


def track(words):
    page, out = {}, []
    for pc, w in words:
        op, rs, rt, im = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        rd, funct = (w >> 11) & 31, w & 0x3F
        if op == OP_LUI:
            page[REG[rt]] = im << 16
            out.append((pc, 'page', im << 16))
        elif op == OP_ORI:
            if REG[rs] in page and rs == rt:            # the lui+ori address idiom
                val = (page[REG[rs]] | im) & 0xFFFFFFFF
                page[REG[rt]] = val
                out.append((pc, 'lui+ori (zero-ext)', val))
            else:
                page.pop(REG[rt], None)                 # plain constant/OR, no page
        elif op == OP_ADDIU:
            base = page.get(REG[rs])
            page.pop(REG[rt], None)
            if base is not None:
                val = (base + s16(im)) & 0xFFFFFFFF
                page[REG[rt]] = val
                out.append((pc, 'lui+addiu (sign-ext)', val))
        elif op == 0x00 and funct == FUNCT_ADDU:
            if REG[rs] in page and rs != rt:
                out.append((pc, 'base+index', page[REG[rs]]))
            page.pop(REG[rd], None)
        elif op in LOAD_STORE_OPS:
            base = page.get(REG[rs])
            if base is not None:
                out.append((pc, 'load/store disp', (base + s16(im)) & 0xFFFFFFFF))
            if op in LOAD_OPS:
                page.pop(REG[rt], None)
        elif op not in (0x00, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C):
            page.pop(REG[rt], None)
        elif op == 0x00 and funct != 0:
            page.pop(REG[rd], None)
    return out


def assert_addresses(words, expect, label='cave'):
    """Check every address the cave code actually materialises.

    `expect` is the collection of addresses this build is allowed to touch for
    its data (the map table, the engine's cursor / glyph-index / line-counter
    variables...). Returns (track_list, problems) where each problem is
    (pc, kind, value, message). Two classes are reported:
      * an effective address (load/store/indexed) outside `expect`;
      * the sign-extend page trap: a `lui`+`addiu` whose result is exactly one
        page away from an intended address — the classic way to mis-address a
        cave table whose low half is >= 0x8000.
    """
    tr = track(words)
    bad = []
    for pc, kind, val in tr:
        if kind in ('load/store disp', 'base+index', 'lui+ori (zero-ext)') and val not in expect:
            bad.append((pc, kind, val, 'effective address %08X is not one of the build\'s data addresses' % val))
        elif kind == 'lui+addiu (sign-ext)':
            for e in expect:
                if abs(val - e) == 0x10000:
                    bad.append((pc, 'sign-extend page trap', val,
                                'lui+addiu yields %08X but %08X was intended (low half '
                                '>= 0x8000): use lui + ori (zero-extends) instead' % (val, e)))
    return tr, bad
