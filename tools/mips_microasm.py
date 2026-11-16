"""Minimal MIPS R3000A (little-endian) micro-assembler for the hand-written
patch cave, plus a self-test that validates every encoder against real words
read out of extract/iso/SLPS_006.01.

Why this exists: hello1 (build/patched_hello1.bin) carried a hand-rolled
encoder whose sra() emitted funct 0x07 (SRAV, variable shift with $zero as the
shift amount) instead of 0x03 (SRA), so its "sra $a1,0x10" / "sra $a2,0x10"
silently became no-ops. self_test() below is exactly that class of check: each
encoder re-encodes an instruction that capstone decoded from the real game
EXE and must reproduce the original 4 bytes exactly.

Usage: import and call the enc_* helpers; run the file to execute self_test().
"""
import struct

REG = {
    'zero': 0, 'at': 1, 'v0': 2, 'v1': 3,
    'a0': 4, 'a1': 5, 'a2': 6, 'a3': 7,
    't0': 8, 't1': 9, 't2': 10, 't3': 11, 't4': 12, 't5': 13, 't6': 14, 't7': 15,
    's0': 16, 's1': 17, 's2': 18, 's3': 19, 's4': 20, 's5': 21, 's6': 22, 's7': 23,
    't8': 24, 't9': 25, 'k0': 26, 'k1': 27, 'gp': 28, 'sp': 29, 'fp': 30, 's8': 30,
    'ra': 31,
}


def reg(name):
    if isinstance(name, int):
        return name
    n = str(name).strip().lower().lstrip('$')
    if n not in REG:
        raise KeyError(f'unknown register {name!r}')
    return REG[n]


def _s16(v):
    return v & 0xFFFF


NOP = 0x00000000

# ---- jump class (26-bit absolute target, upper 4 bits come from PC[31:28]) --
def enc_j(target):
    """j <target>: opcode 0x02 (NOT 0x08 -- 0x08 is addi; the self_test below
    caught that mistake the first time it was made here)."""
    _check_word(target)
    return (0x02 << 26) | ((target >> 2) & 0x03FFFFFF)


def enc_jal(target):
    _check_word(target)
    return (0x03 << 26) | ((target >> 2) & 0x03FFFFFF)


def _branch_fields(pc, target):
    _check_word(pc)
    _check_word(target)
    d = (target - (pc + 4)) // 4
    if not (-0x8000 <= d < 0x8000):
        raise ValueError(f'branch offset {d} out of range (pc {pc:08X} -> {target:08X})')
    return d & 0xFFFF


def enc_beq(rs, rt, pc, target):
    return (0x04 << 26) | (reg(rs) << 21) | (reg(rt) << 16) | _branch_fields(pc, target)


def enc_bne(rs, rt, pc, target):
    return (0x05 << 26) | (reg(rs) << 21) | (reg(rt) << 16) | _branch_fields(pc, target)


def enc_bgtz(rs, pc, target):
    return (0x07 << 26) | (reg(rs) << 21) | _branch_fields(pc, target)


def enc_blez(rs, pc, target):
    return (0x06 << 26) | (reg(rs) << 21) | _branch_fields(pc, target)


def enc_beqz(rs, pc, target):
    return enc_beq(rs, 'zero', pc, target)


def enc_bnez(rs, pc, target):
    return enc_bne(rs, 'zero', pc, target)


def enc_b(target, pc=None):
    # pseudo "b target": beq $zero,$zero -- only valid when pc is known
    if pc is None:
        raise ValueError('enc_b needs pc')
    return enc_beq('zero', 'zero', pc, target)


# ---- upper immediate -------------------------------------------------------
def enc_lui(rt, imm):
    return (0x0F << 26) | (reg(rt) << 16) | (imm & 0xFFFF)


# ---- special (R-type) ------------------------------------------------------
def _special(rs, rt, rd, sa, funct):
    return (rs << 21) | (rt << 16) | (rd << 11) | (sa << 6) | funct


# NOTE: for the shifts the *source* lives in the rt field and rs must be 0
# (sll rd, rt, sa). Getting this backwards encodes a silent no-op.
def enc_sll(src, rd, sa):     # written in assembly as: sll rd, src, sa
    return _special(0, reg(src), reg(rd), sa, 0x00)


def enc_srl(src, rd, sa):     # written in assembly as: srl rd, src, sa
    return _special(0, reg(src), reg(rd), sa, 0x02)


def enc_sra(src, rd, sa):     # written in assembly as: sra rd, src, sa (funct 0x03, NOT 0x07)
    return _special(0, reg(src), reg(rd), sa, 0x03)


def enc_addu(rd, rs, rt):
    return _special(reg(rs), reg(rt), reg(rd), 0, 0x21)


def enc_subu(rd, rs, rt):
    return _special(reg(rs), reg(rt), reg(rd), 0, 0x23)


def enc_move(rd, rs):
    return enc_addu(rd, rs, 'zero')


def enc_or(rd, rs, rt):
    # OR rd,rs,rt = rs | rt (funct 0x25, not 0x21 which is ADDU).  Anchored on
    # the engine's own join word 0x8003A1DC = or $a0,$v1,$a0 (SELFTEST).
    return _special(reg(rs), reg(rt), reg(rd), 0, 0x25)


def enc_jr(rs):
    return _special(reg(rs), 0, 0, 0, 0x08)


# ---- branch / load-store / arithmetic immediates ---------------------------
def enc_addiu(rt, rs, imm):
    return (0x09 << 26) | (reg(rs) << 21) | (reg(rt) << 16) | _s16(imm)


def enc_sltiu(rt, rs, imm):
    return (0x0B << 26) | (reg(rs) << 21) | (reg(rt) << 16) | _s16(imm)


def enc_ori(rt, rs, imm):
    return (0x0D << 26) | (reg(rs) << 21) | (reg(rt) << 16) | _s16(imm)


def enc_andi(rt, rs, imm):
    return (0x0C << 26) | (reg(rs) << 21) | (reg(rt) << 16) | _s16(imm)


_LOAD = {'lb': 0x20, 'lbu': 0x24, 'lh': 0x21, 'lhu': 0x25, 'lw': 0x23}
# 6-bit opcode FIELD values (verified against real EXE words, see SELFTEST):
#   sb 0xA020342C @0x80011A90 -> field 0b101000 = 0x28
#   sh 0xA420341C @0x8003A16C -> field 0b101001 = 0x29
#   sw 0xAC223488 @0x8003A218 -> field 0b101011 = 0x2B
# The 0xA0/0xA1 byte-encoded forms (opcode field already shifted left by 2) are
# NOT valid here: they overflow the 6-bit field and produced 33-bit words.
_STORE = {'sb': 0x28, 'sh': 0x29, 'sw': 0x2B}


def _u32(w):
    if not 0 <= w <= 0xFFFFFFFF:
        raise ValueError(f'encoded word {w:09X} does not fit in 32 bits')
    return w


def enc_load(mn, rt, base, off):
    if mn not in _LOAD:
        raise KeyError(mn)
    return _u32((_LOAD[mn] << 26) | (reg(base) << 21) | (reg(rt) << 16) | _s16(off))


def enc_store(mn, rt, base, off):
    if mn not in _STORE:
        raise KeyError(mn)
    return _u32((_STORE[mn] << 26) | (reg(base) << 21) | (reg(rt) << 16) | _s16(off))


def enc_lw(rt, base, off):
    return enc_load('lw', rt, base, off)


def enc_lbu(rt, base, off):
    return enc_load('lbu', rt, base, off)


def enc_lh(rt, base, off):
    return enc_load('lh', rt, base, off)


def enc_lhu(rt, base, off):
    return enc_load('lhu', rt, base, off)


def enc_sh(rt, base, off):
    return enc_store('sh', rt, base, off)


def enc_sw(rt, base, off):
    return enc_store('sw', rt, base, off)


def enc_sb(rt, base, off):
    return enc_store('sb', rt, base, off)


# ---- address-map helpers (verified against the PS-X EXE header) -------------
EXE_FILE = 'extract/iso/SLPS_006.01'
EXE_SIZE = 540672
HEADERSIZE = 0x8000F800          # RAM 0x80010000 <-> file 0x800 (armips convention)
RAM_OF = lambda off: off + HEADERSIZE
FILE_OF = lambda ram: ram - HEADERSIZE


def _check_word(a):
    if a % 4:
        raise ValueError(f'address {a:08X} is not word aligned')


# ---- self test: re-encode real instructions taken from the game EXE --------
# (ram, lambda that returns the word, expected capstone mnemonic)
SELFTEST = [
    (0x8003A0FC, lambda: enc_lui('a1', 0x8009),                       'lui'),
    (0x8003A100, lambda: enc_lw('a1', 'a1', 0x3488),                  'lw'),
    (0x8003A108, lambda: enc_lbu('v0', 'a1', 0),                      'lbu'),
    (0x8003A110, lambda: enc_sltiu('v0', 'v0', 0x80),                 'sltiu'),
    (0x8003A114, lambda: enc_beqz('v0', 0x8003A114, 0x8003A154),      'beqz'),
    (0x8003A11C, lambda: enc_lui('a0', 0x8009),                       'lui'),
    (0x8003A120, lambda: enc_addiu('a0', 'a0', 0x323C),               'addiu'),
    (0x8003A124, lambda: enc_jal(0x80044AD8),                         'jal'),
    (0x80049FF8, lambda: enc_addiu('sp', 'sp', -0x18),                'addiu'),
    (0x80049FFC, lambda: enc_sw('ra', 'sp', 0x10),                    'sw'),
    (0x8004A018, lambda: enc_j(0x8004A0C4),                           'j'),
    (0x8004A0AC, lambda: enc_lui('a0', 0x8001),                       'lui'),
    (0x8004A0B0, lambda: enc_addiu('a0', 'a0', 0xD80),                'addiu'),
    (0x8004A0B4, lambda: enc_jal(0x800452E0),                         'jal'),
    (0x8004A0D4, lambda: enc_subu('v0', 'v0', 'v1'),                  'subu'),
    (0x8004A0D8, lambda: enc_sll('v0', 'v0', 1),                      'sll'),
    (0x8004A0E4, lambda: enc_lhu('v0', 'at', -0x337C),                'lhu'),
    (0x8004A000, lambda: enc_move('v1', 'zero'),                      'move'),
    (0x8004A01C, lambda: enc_ori('v1', 'zero', 1),                    'ori'),
    # store instructions: added 2026-11-08 after fixing the sb/sh opcode fields
    (0x80011A90, lambda: enc_sb('zero', 'at', 0x342C),                'sb'),
    (0x8003A16C, lambda: enc_sh('zero', 'at', 0x341C),                'sh'),
    (0x8003A184, lambda: enc_sh('v0', 'at', 0x3420),                  'sh'),
    (0x8003A200, lambda: enc_sh('v0', 'at', 0x341C),                  'sh'),
    (0x8003A0F0, lambda: enc_sw('v0', 'at', 0x3488),                  'sw'),
    (0x8003A218, lambda: enc_sw('v0', 'at', 0x3488),                  'sw'),
    # ---- forms the asciiC1 "pen" cave emits, each anchored on a real word
    # (found with tools/find_real_ops.py, operands confirmed with capstone)
    (0x8003A1DC, lambda: enc_or('a0', 'v1', 'a0'),                     'or'),
    (0x80044188, lambda: enc_srl('v1', 's3', 8),                       'srl'),
    (0x80011D80, lambda: enc_andi('v0', 'v0', 0x820),                  'andi'),
    (0x80011C20, lambda: enc_addu('at', 'at', 'v1'),                   'addu'),
    (0x8005CEE4, lambda: enc_move('t0', 'a1'),                         'move'),
    (0x80012DF4, lambda: enc_lui('t5', 0x800C),                        'lui'),
    (0x80012E00, lambda: enc_ori('t4', 't4', 0x14),                    'ori'),
    # capstone prints rt=$zero branches as the beqz alias; the word is identical
    (0x80011DB8, lambda: enc_beq('v0', 'zero', 0x80011DB8, 0x80011DC4), 'beqz'),
    (0x80011A50, lambda: enc_bne('v0', 'v1', 0x80011A50, 0x80011A84),  'bne'),
    (0x80011E08, lambda: enc_sll('a0', 'v1', 8),                       'sll'),
]


def self_test(exe_path=EXE_FILE, verbose=True):
    """Re-encode real EXE instructions and require byte-exact agreement."""
    import capstone
    data = open(exe_path, 'rb').read()
    assert len(data) == EXE_SIZE, f'{exe_path}: unexpected size {len(data)}'
    md = capstone.Cs(capstone.CS_ARCH_MIPS,
                     capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN)
    ok = 0
    problems = []
    for ram, fn, want_mn in SELFTEST:
        if fn is None:
            continue
        off = FILE_OF(ram)
        orig = struct.unpack_from('<I', data, off)[0]
        mine = fn() & 0xFFFFFFFF
        dec = list(md.disasm(struct.pack('<I', mine), ram, count=1))
        mn = dec[0].mnemonic if dec else '<none>'
        line = f'{ram:08X} orig {orig:08X} mine {mine:08X} -> {mn} {dec[0].op_str if dec else ""}'.rstrip()
        bad = None
        if mine != orig:
            bad = 'MISMATCH '
        elif want_mn and mn != want_mn:
            bad = f'MNEMONIC expected {want_mn}: '
        if bad:
            problems.append(bad + line)
            if verbose:
                print('  BAD ' + line)
        else:
            ok += 1
            if verbose:
                print('  ok  ' + line)
    # the sra bug that killed hello1: prove our sra is SRA (funct 3), not SRAV
    sra = enc_sra('a2', 'a2', 0x10)
    d = list(md.disasm(struct.pack('<I', sra), 0x8004DCD0, count=1))
    if d[0].mnemonic != 'sra' or d[0].op_str.replace(' ', '') != '$a2,$a2,0x10':
        problems.append(f'SRA encoding wrong: {d[0].mnemonic} {d[0].op_str}')
    else:
        ok += 1
        if verbose:
            print(f'  ok  sra check {sra:08X} -> {d[0].mnemonic} {d[0].op_str}')
    for p in problems:
        print('  !! ' + p)
    print(f'self_test: {ok} encodings byte-exact vs real EXE words, {len(problems)} problems')
    return len(problems) == 0


if __name__ == '__main__':
    import sys
    sys.exit(0 if self_test() else 1)
