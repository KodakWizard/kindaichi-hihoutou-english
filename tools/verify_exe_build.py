"""Extra verification layer for EXE (executable) patches, on top of
tools/verify_build.py (which does the sector diff / EDC / reconstruction).

Usage: python tools/verify_exe_build.py hookA1

Reads build/<name>_patchinfo.json written by the patch builder and audits the
FINISHED BIN on disc, so what is checked is what the emulator will read:

  A. original disc untouched (size + SHA1); built disc same size
  B. CUE sheet sanity (names the built bin, MODE2/2352, INDEX 01 00:00:00)
  C. every differing byte of the built BIN lies inside the ISO extent of the
     EXE (=> no script/text file was touched by this build)
  D. the EXE recovered from the built BIN is byte-identical to the patched EXE
     file AND differs from the original EXE in exactly the claimed words
  E. reference scan re-run on the recovered EXE: the hooked word keeps its
     incoming branch/jump refs, and the only reference into the code cave is
     the jump we added (dead-code assumption re-verified post-patch)
  F. delay-slot / encoding audit of every jump the patch introduces
  G. if build/<name>_armips (or SLPS_006.01_armips) exists, it must equal the
     Python-built EXE (two independent assemblers agree)
  H. residual-risk scan: no u32 anywhere on the disc (in the extracted file
     set) points into the cave range, and no pointer in the EXE's data
     regions points into the hooked words
"""
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xref_scan as X

BIN_ORIG = 'Kindaichi Shounen no Jikenbo - Hihou-tou Arata naru Sangeki (Japan).bin'
BIN_SHA1 = '65F582FFE78B5FA55E45D0C163BA2E67A5B15E15'
S, D = 2352, 24

_log = []


def say(s=''):
    print(s)
    _log.append(s)


def sha1_file(p, chunk=1 << 20):
    h = hashlib.sha1()
    with open(p, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest().upper()


def diff_ranges(pa, pb, chunk=1 << 20):
    n, start = os.path.getsize(pa), None
    out = []
    with open(pa, 'rb') as fa, open(pb, 'rb') as fb:
        off = 0
        while off < n:
            a, b = fa.read(chunk), fb.read(chunk)
            if a != b:
                for i in range(len(a)):
                    if a[i] != b[i]:
                        if start is None:
                            start = off + i
                    elif start is not None:
                        out.append((start, off + i))
                        start = None
            off += len(a)
    if start is not None:
        out.append((start, n))
    return out


def main(name):
    info = json.load(open(f'build/{name}_patchinfo.json'))
    bin_out, cue_out = info['bin_out'], info['cue_out']
    fails = []

    def ck(cond, msg):
        say(('  OK   ' if cond else '  FAIL ') + msg)
        if not cond:
            fails.append(msg)

    say(f'=== EXE-patch disc audit: {name} ===')

    # ---- A ----
    say('-- images --')
    ck(os.path.getsize(BIN_ORIG) == 682458672, 'original BIN size 682458672')
    ck(sha1_file(BIN_ORIG) == BIN_SHA1, f'original BIN SHA1 still {BIN_SHA1}')
    ck(os.path.getsize(bin_out) == os.path.getsize(BIN_ORIG),
        f'{bin_out} same size ({os.path.getsize(bin_out)}) = '
        f'{os.path.getsize(bin_out) // S} sectors')

    # ---- B ----
    say('-- cue --')
    cue = [l.strip() for l in open(cue_out).read().splitlines()]
    say('  ' + ' | '.join(cue))
    ck(len(cue) == 3 and cue[0].endswith('BINARY') and 'MODE2/2352' in cue[1]
       and cue[2] == 'INDEX 01 00:00:00', 'cue: single MODE2/2352 track, INDEX 01 at 00:00:00')
    named = cue[0].split('"')[1]
    ck(os.path.basename(bin_out) == named, f'cue FILE "{named}" == {os.path.basename(bin_out)}')
    ck(os.path.exists(os.path.join(os.path.dirname(cue_out), named)), 'cue target exists')

    # ---- C ----
    files = {e['path']: e for e in json.load(open('extract/filelist.json'))}
    e = files[info.get('iso_path', '/SLPS_006.01')]
    lo, hi = e['lba'] * S, (e['lba'] + (e['size'] + 2047) // 2048) * S

    def extent(p):
        g = files[p]
        return g['lba'] * S, (g['lba'] + (g['size'] + 2047) // 2048) * S

    declared = info.get('changed_extents') or [info.get('iso_path', '/SLPS_006.01')]
    exts = []
    for p in declared:
        a, b = extent(p)
        exts.append((p, a, b))
        say(f'-- declared changed extent {p} LBA {files[p]["lba"]} '
            f'bytes {a}..{b} ({files[p]["size"]} bytes)')
    say(f'-- byte diff (whole BIN) -- extent {e["path"]} LBA {e["lba"]} '
        f'bytes {lo}..{hi}')
    rs = diff_ranges(BIN_ORIG, bin_out)
    for a, b in rs:
        say(f'   {a}..{b} ({b - a} bytes) sectors {a // S}..{(b - 1) // S}')
    ck(rs and all(any(pa <= a and b <= pb for _, pa, pb in exts) for a, b in rs),
       f'all differing bytes lie inside the declared changed extents '
       f'({", ".join(declared)}) — nothing else on the disc moved')
    others = [p for p in ('/OUT/SANBA.TXT', '/IN/SANBA.TXT', '/OUT/OPEN.TXT')
              if p in files and p not in declared]
    for p in others:
        o_lo, o_hi = extent(p)
        ck(not any(o_lo <= a and b <= o_hi for a, b in rs),
           f'{p} byte-identical to original disc')

    # ---- C2: audit every declared non-EXE data change against its own file --
    for dc in info.get('data_changes', []):
        p, loc = dc['iso_path'], dc['local_file']
        src = 'extract/iso' + p.replace('/', os.sep)
        ck(p in files, f'{p} exists in the ISO directory (extract/filelist.json)')
        o = open(src, 'rb').read()
        n = open(loc, 'rb').read()
        dcg = files[p]
        dsz = dcg['size']
        ck(dsz == len(o), f'{p} extracted size {dsz} matches the disc payload')
        ck(len(o) == len(n), f'{loc} same byte size as {src} ({len(o)} bytes) '
                             '=> file record / directory / LBA layout untouched')
        if 'sha1_orig' in dc:
            ck(hashlib.sha1(o).hexdigest().upper() == dc['sha1_orig'],
               f'{src} still sha1 {dc["sha1_orig"]}')
        od = [i for i in range(min(len(o), len(n))) if o[i] != n[i]]
        want = [c['off'] for c in dc['changed_bytes']]
        ck(od == want, f'{p}: exactly {len(want)} byte(s) differ at '
                       f'{[hex(x) for x in want]}')
        for c in dc['changed_bytes']:
            ck(o[c['off']] == c['old'] and n[c['off']] == c['new'],
               f'{p} @{c["off"]}: original 0x{c["old"]:02X} -> new 0x{c["new"]:02X}')
        # and those bytes really are what the finished disc now holds
        a0, b0 = extent(p)
        with open(bin_out, 'rb') as f:
            f.seek(a0)
            raw = f.read(b0 - a0)
        pay = b''.join(raw[i * S + D:i * S + D + 2048]
                       for i in range((b0 - a0) // S))[:dcg['size']]
        ck(pay == n, f'{p} recovered from {bin_out} == {loc} (sha1 '
                     f'{hashlib.sha1(pay).hexdigest().upper()})')

    # ---- D ----
    say('-- recovered EXE --')
    nsec = (e['size'] + 2047) // 2048
    with open(bin_out, 'rb') as f:
        f.seek(e['lba'] * S)
        raw = f.read(S * nsec)
    pay = b''.join(raw[i * S + D:i * S + D + 2048] for i in range(nsec))[:e['size']]
    built = open(info['exe_out'], 'rb').read()
    orig = open(info['exe_orig'], 'rb').read()
    ck(pay == built, f'recovered payload == {info["exe_out"]} (sha1 '
       f'{hashlib.sha1(pay).hexdigest().upper()})')
    ck(hashlib.sha1(orig).hexdigest().upper() == info['exe_sha1_orig'],
       f'{info["exe_orig"]} still sha1 {info["exe_sha1_orig"]}')
    cw = {w['file']: w for w in info['changed_words']}
    got = [o for o in range(0, len(pay) - 3, 4) if pay[o:o + 4] != orig[o:o + 4]]
    ck(sorted(got) == sorted(cw), f'exactly {len(cw)} words differ in the EXE: '
       + ', '.join(f'{X.RAM_OF(o):08X}' for o in sorted(got)))
    for o in sorted(cw):
        w = cw[o]
        gv = struct.unpack_from('<I', pay, o)[0]
        ov = struct.unpack_from('<I', orig, o)[0]
        say(f'   RAM {X.RAM_OF(o):08X} file {o:06X}: {ov:08X} -> {gv:08X}')
        ck(gv == w['new'] and ov == w['old'] and X.RAM_OF(o) == w['ram'],
           f'   matches declared word at RAM {w["ram"]:08X}')

    # ---- E ----
    say('-- reference scan on recovered (PATCHED) EXE --')
    hook, cave = info['hook'], info['cave']
    # A build may hook a SECOND engine site (asciiC1 hooks the x computation at
    # 0x8003A1B0 as well as the loop head).  Every hook declared in the
    # patchinfo is audited; nothing is assumed about how many there are.
    hooks = [info['hook']] + [info[k] for k in ('hook2', 'hook3', 'hook4')
                              if k in info]
    changed_files = {w['file'] for w in info['changed_words']}

    def untouchable(ram):
        """a word the patch must leave byte-identical (fallthrough / resume)"""
        return X.FILE_OF(ram) not in changed_files

    for hk in hooks:
        ra = X.find_refs(pay, [(hk['ram_a'], hk['ram_a'] + 4)])
        say('   hooked word %08X (resume %08X):' % (hk['ram_a'], hk['resume']))
        for h in ra:
            say('   %08X %-9s %s' % h)
        ck(len(ra) == hk.get('expect_refs', 4),
           'hooked word %08X keeps the %d incoming branch/jump refs the patchinfo '
           'declares (got %d)' % (hk['ram_a'], hk.get('expect_refs', 4), len(ra)))
        ck([h for h in ra if h[1] == 'delay'] == [],
           'no delay slot lands on the hooked word %08X' % hk['ram_a'])
        ck(untouchable(hk['resume']),
           'the resume word %08X of hook %08X is NOT changed by this build, so the '
           'cave hands control back to untouched engine code'
           % (hk['resume'], hk['ram_a']))
        if 'fallthrough_from' in hk:
            ff = hk['fallthrough_from']
            w = struct.unpack_from('<I', pay, X.FILE_OF(ff))[0]
            ck(untouchable(ff),
               'the word before the hooked word (%08X) is untouched by this build'
               % ff)
            ck(w >> 26 not in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07),
               'the word %08X before the hooked word is word %08X = a plain '
               'non-branch/non-jump instruction, so control falls through into the '
               'hook exactly as it used to fall through into the original '
               'instruction (that is why 0 incoming refs is the expected count '
               'here)' % (ff, w))
    rc = X.find_refs(pay, [(cave['ram'], cave['ram'] + cave['size'])])
    for h in rc:
        say('   cave-ref %08X %-9s %s' % h)
    # EXTENSION (asciiF5, additive): the F5 dispatcher uses internal `j`
    # instructions whose targets are its own labels INSIDE the cave (L_JOIN),
    # where asciiD1/D2/F3/F4 used only branches.  A j whose PC is itself inside
    # the cave is a self-reference of the patch's own code, exactly like the
    # branch self-refs accepted by the check below; it reroutes nothing that
    # existed before.  Only jumps from OUTSIDE the cave must be the hooks.
    cave_to = cave['ram'] + cave['size']
    into = [h for h in rc if h[1] == 'j/jal'
            and not (cave['ram'] <= h[0] < cave_to)]
    want = sorted(hk['ram_a'] for hk in hooks)
    ck(sorted(set(h[0] for h in into)) == want and len(into) == len(want),
       'the only jumps into the cave come from the %d hooked word(s) this build '
       'declares: %s' % (len(want), ', '.join('%08X' % a for a in want)))
    brs = [h for h in rc if h[1] == 'branch']
    for h in brs:
        say(f'   branch into cave from {h[0]:08X} {h[1]} {h[2]}')
    ck(all(cave['ram'] <= h[0] < cave_to for h in brs),
       'every branch landing in the cave originates inside the cave (the patch\'s '
       'own dispatcher), so no existing code was rerouted into it')
    ck(not [h for h in rc if h[1] not in ('j/jal', 'branch', 'delay')
            and not (cave['ram'] <= h[0] < cave_to)],
       'no pointer / lui / gp-relative reference into the cave from outside it')

    # ---- F ----
    say('-- introduced jumps/branches: encoding + delay slots --')
    jr = info.get('jumps') or [('hook -> cave', hook['ram_a'], cave['ram']),
                               ('cave -> resume', cave['ram'] + 8, hook['resume'])]

    # A declared, deliberate non-nop delay slot is only acceptable if it cannot
    # have a side effect on the engine: no store, no control transfer, and it
    # must write one of the patch's own scratch registers.  (asciiC1 puts
    # `andi $t2,$t2,0xFF` in the delay slot of a two-path branch: it is the
    # needed truncation on the taken-fallthrough path and dead but harmless on
    # the branch path, and the builder's audit proves both paths still hand the
    # engine the right x.)
    #
    # EXTENSION (asciiF5, declared and additive): the accepted classes are
    # widened from I-type ALU only to (a) pure R-type addu (shift field 0,
    # funct 0x21) and (b) loads, in both cases with dest inside the declared
    # scratch set AND a mandatory `delay_note` carrying the builder's
    # def-use/load-delay proof for that exact word (the builder audit, not
    # this verifier, is what proves no path reads the loaded/derived value one
    # instruction too early; the verifier still pins the declared bits and the
    # dest register).  Stores and jumps/branches as delay slots stay refused.
    ALU_I = {0x09: 'addiu', 0x0B: 'sltiu', 0x0C: 'andi', 0x0D: 'ori', 0x0F: 'lui'}
    R_ALU = {0x21: 'addu'}
    LOADS = {0x20: 'lb', 0x21: 'lh', 0x22: 'lwl', 0x23: 'lw',
             0x24: 'lbu', 0x25: 'lhu'}
    scratch = set(info.get('scratch_regs', [1, 8, 9, 10, 11, 12, 13]))

    def benign_nonop(word):
        op = word >> 26
        if word == 0:
            return None
        if op in ALU_I:
            rt = (word >> 16) & 0x1F
            if rt not in scratch:
                return None
            return '%s, dest $%d' % (ALU_I[op], rt)
        if (op == 0x00 and (word & 0x3F) in R_ALU
                and ((word >> 6) & 0x1F) == 0):
            # (word >> 21) == 0 was an over-strict mis-parse of "shift field
            # zero": bits 21-25 are rs, not shamt.  shamt is bits 6-10; the
            # F5 folded-anchor word addu $a0,$t2,$at = 0x01412021 has rs=10
            # and shamt=0, and funct 0x21 (addu) has no shift semantics at
            # all.  rd (bits 11-15) is the only written register and must be
            # scratch -- checked below.
            rd = (word >> 11) & 0x1F
            if rd not in scratch:
                return None
            return '%s, dest $%d (R-type)' % (R_ALU[word & 0x3F], rd)
        if op in LOADS:
            rt = (word >> 16) & 0x1F
            if rt not in scratch:
                return None
            return '%s, dest $%d (load)' % (LOADS[op], rt)
        return None

    def jent(ent):
        """accept both {'tag','pc','target'} and a plain (tag, pc, target) triple"""
        if isinstance(ent, dict):
            return ent['tag'], ent['pc'], ent['target']
        tag, pc, tgt = ent
        return tag, pc, tgt

    for ent in jr:
        tag, pc, tgt = jent(ent)
        w = struct.unpack_from('<I', pay, X.FILE_OF(pc))[0]
        nxt = struct.unpack_from('<I', pay, X.FILE_OF(pc + 4))[0]
        dec = (pc & 0xF0000000) | ((w & 0x03FFFFFF) << 2)
        ck(w >> 26 == 0x02, f'{tag} j at {pc:08X} word {w:08X} has jump opcode 0x02')
        ck(dec == tgt, f'{tag} j at {pc:08X} decodes to {dec:08X} == {tgt:08X}')
        ck((tgt & 0xF0000000) == (pc & 0xF0000000),
           f'{tag} target {tgt:08X} shares PC[31:28] with {pc:08X}')
        if nxt == 0:
            ck(True, f'{tag} delay slot at {pc + 4:08X} is nop')
        elif isinstance(ent, dict) and 'delay_word' in ent:
            ow = struct.unpack_from('<I', orig, X.FILE_OF(pc + 4))[0]
            hk = next((h for h in hooks if h['ram_a'] == pc), None)
            ck(nxt == ent['delay_word'],
               f'{tag} delay slot at {pc + 4:08X} = {nxt:08X} equals the value '
               f'the patchinfo declares ({ent["delay_word"]:08X})')
            if nxt == ow:
                # The untouched-engine form (asciiD1): the delay slot is the
                # pristine engine word that the patch deliberately leaves in
                # place, and it is exactly this hook's declared resume address,
                # so the cave hands control back to the word it just ran as a
                # delay slot and the engine keeps its own load-delay pairing.
                ck(hk is not None and hk.get('resume') == pc + 4,
                   f'{tag} non-nop delay slot at {pc + 4:08X} is '
                   'byte-identical to the PRISTINE disc word there, and it must '
                   f'be exactly the declared resume address of the hook at '
                   f'{pc:08X} ({hk.get("resume") if hk else None:08X}), so the '
                   'cave hands control back to the same word it just ran as a '
                   'delay slot and the engine keeps its own load-delay pairing')
            else:
                # The authored form (asciiF5): the patch wrote this delay slot
                # itself (a cave-internal jump).  Accept only a benign op
                # writing a declared scratch register, and only with the
                # builder's def-use note; stores/branches/jumps as delay slots
                # never pass benign_nonop.
                ben = benign_nonop(nxt)
                ck(ben is not None,
                   f'{tag} declared non-nop delay slot at {pc + 4:08X} '
                   f'({nxt:08X}) is not the pristine word there, so it must be '
                   "a side-effect-free ALU/load op writing one of the patch's "
                   f'own scratch registers: {ben}')
                ck(bool(ent.get('delay_note')),
                   f'{tag} authored delay slot at {pc + 4:08X} requires a '
                   'delay_note carrying the builder def-use/liveness proof')
        else:
            ck(False, f'{tag} delay slot at {pc + 4:08X} = {nxt:08X} is neither '
                      'nop nor a declared, audited non-nop instruction')
    for ent in info.get('branches', []):
        tag, pc, tgt = jent(ent)
        extra = ent if isinstance(ent, dict) else {}
        w = struct.unpack_from('<I', pay, X.FILE_OF(pc))[0]
        off = (w & 0xFFFF) - (0x10000 if (w & 0x8000) else 0)
        ck(w >> 26 in (0x04, 0x05), f'{tag} branch at {pc:08X} word {w:08X} '
                                    'is beq/bne (opcode 4/5)')
        ck(pc + 4 + off * 4 == tgt, f'{tag} branch at {pc:08X} decodes to '
                                    f'{pc + 4 + off * 4:08X} == {tgt:08X}')
        ck(abs(off) <= 32767, f'{tag} branch displacement {off:+d} words '
                              f'({off * 4:+d} bytes) inside the 16-bit signed '
                              'branch field (-32768..+32767 words)')
        dlw = struct.unpack_from('<I', pay, X.FILE_OF(pc + 4))[0]
        if dlw == 0:
            ck(True, f'{tag} delay slot at {pc + 4:08X} is nop')
        elif 'delay_word' in extra:
            ben = benign_nonop(dlw)
            ck(dlw == extra['delay_word'],
               f'{tag} delay slot at {pc + 4:08X} = {dlw:08X} equals the value the '
               f'patchinfo declares ({extra["delay_word"]:08X})')
            ck(ben is not None,
               f'{tag} non-nop delay slot at {pc + 4:08X} ({dlw:08X}) is a '
               'side-effect-free ALU immediate op writing one of the patch\'s own '
               f'scratch registers: {ben}; it runs on BOTH branch paths by design '
               'and cannot disturb engine state')
            if dlw >> 26 not in ALU_I:
                ck(bool(extra.get('delay_note')),
                   f'{tag} delay slot at {pc + 4:08X} is an R-type/load op (the '
                   'extended class), so the patchinfo MUST carry a delay_note '
                   "with the builder's def-use/load-delay proof for it")
            if extra.get('delay_note'):
                say(f'   note: {tag} delay slot: {extra["delay_note"]}')
        else:
            ck(False, f'{tag} delay slot at {pc + 4:08X} = {dlw:08X} is neither nop '
                      'nor a declared, audited non-nop instruction')
    # every displaced word of every hook must be replayed verbatim in the cave
    for hk in hooks:
        where = hk.get('displace_at') or [cave['ram'] + 4 * i for i in (0, 1)]
        for i, ram in enumerate((hk['ram_a'], hk['ram_b'])):
            src = struct.unpack_from('<I', orig, X.FILE_OF(ram))[0]
            dst = struct.unpack_from('<I', pay, X.FILE_OF(where[i]))[0]
            ck(src == dst, 'cave word at %08X = %08X == original displaced word '
                           '%08X of hook %08X' % (where[i], dst, ram, hk['ram_a']))

    # ---- G ----
    arm = info.get('armips_image')
    if arm:
        # the build declared the exact image it cross-assembled: it must exist
        # and match, no fallback to another build's image
        ck(os.path.exists(arm), f'declared armips image {arm} exists')
        if os.path.exists(arm):
            say(f'-- armips cross-assembly ({arm}, asm {info.get("armips_asm")}) --')
            ck(open(arm, 'rb').read() == built,
               'armips-assembled EXE byte-identical to Python-built EXE')
    else:
        arm = f'build/{name}_armips'
        if not os.path.exists(arm):
            arm = 'build/SLPS_006.01_armips'
        if os.path.exists(arm):
            say(f'-- armips cross-assembly ({arm}) --')
            ck(open(arm, 'rb').read() == built,
               'armips-assembled EXE byte-identical to Python-built EXE')
        else:
            say('-- armips cross-assembly: no armips image found --')

    # ---- H ----
    say('-- residual risk: any byte-aligned u32 on the disc pointing into the '
        'cave / hooked words --')
    cave_lo, cave_hi = cave['ram'], cave['ram'] + cave['size']
    spans = [(cave_lo, cave_hi, 'cave')] + [(hk['ram_a'], hk['ram_b'] + 4, 'hook')
                                            for hk in hooks]
    pats = {}
    for lo, hi, what in spans:
        for v in range(lo, hi):
            pats[struct.pack('<I', v)] = (what, v)
    hits, checked = 0, 0
    by_file, aligned_hits = {}, []
    for ent in json.load(open('extract/filelist.json')):
        p = os.path.join('extract/iso', ent['path'].lstrip('/').replace('/', os.sep))
        if not os.path.isfile(p):
            continue
        data = open(p, 'rb').read()
        checked += 1
        for pat, (what, val) in pats.items():
            i = data.find(pat)
            while i != -1:
                hits += 1
                key = (ent['path'], what, 'aligned' if i % 4 == 0 else 'unaligned')
                by_file[key] = by_file.get(key, 0) + 1
                if what in ('cave', 'hook') and i % 4 == 0:
                    aligned_hits.append((ent['path'], i, what, val))
                i = data.find(pat, i + 1)
    for (path, what, al), n in sorted(by_file.items()):
        say(f'   {path:24s} {what}-range, {al}: {n}')
    say(f'   scanned {checked} extracted disc files for {len(pats)} candidate '
        f'addresses, {hits} byte-aligned u32 pattern hits total')

    # An aligned 32-bit value that happens to equal an address in the cave is
    # NOT automatically a pointer. Random data produces such coincidences at
    # exactly this rate: (total disc bytes / 4) * (cave bytes / 2**32).
    total = sum(os.path.getsize(os.path.join('extract/iso',
               e['path'].lstrip('/').replace('/', os.sep)))
               for e in json.load(open('extract/filelist.json'))
               if os.path.isfile(os.path.join('extract/iso',
                  e['path'].lstrip('/').replace('/', os.sep))))
    span = sum(hi - lo for lo, hi, _ in spans)
    exp = (total / 4) * (span / 2 ** 32)
    say(f'   {total} extracted bytes => expected random aligned-u32 coincidences '
        f'in these {span} addresses (cave + {len(hooks)} hooked word pair(s)): '
        f'{exp:.1f}; observed: {len(aligned_hits)}')

    def ram_ptrs_near(data, i, span=32):
        """u32s in the PS1 RAM range within +-span words of word i//4. A real
        pointer table has several; an isolated coincidence has ~0.25 by chance."""
        w, n = i // 4, 0
        for k in range(max(0, w - span), min(len(data) // 4, w + span + 1)):
            if k == w:
                continue
            v = int.from_bytes(data[4 * k:4 * k + 4], 'little')
            if 0x8000_0000 <= v <= 0x80FF_FFFF:
                n += 1
        return n

    suspicious = []
    for path, i, what, val in aligned_hits:
        src = os.path.join('extract/iso', path.lstrip('/').replace('/', os.sep))
        nb = ram_ptrs_near(open(src, 'rb').read(), i)
        mov = path.startswith('/MOV/') and path.endswith('.STR')
        legal_word_addr = (val % 4 == 0)
        say(f'   aligned hit: {path} +{i:08X} ({what}) u32 {val:08X} -> '
            f'legal word-aligned address: {legal_word_addr}; in a compressed '
            f'/MOV/*.STR stream: {mov}; RAM-range u32s within +-32 words: {nb}')
        if not (mov and (not legal_word_addr or nb < 4)):
            suspicious.append((path, i, val))
    ck(not aligned_hits or not suspicious,
       'every aligned u32 landing in the cave/hook range is an isolated '
       'coincidence inside a compressed /MOV/*.STR movie stream: either its '
        'value is not even a legal word-aligned address, or it has no '
        'pointer-table neighbours (>=4 RAM u32s within +-32 words)')
    for path, i, val in suspicious:
        say(f'   !! unexplained aligned pointer candidate {path} +{i:08X} = {val:08X}')
    # the EXE itself must contain zero aligned u32 pointing into the cave
    exe_hits = [h for h in aligned_hits if h[0] == info.get('iso_path', '/SLPS_006.01')]
    ck(not exe_hits, 'no aligned u32 anywhere inside the EXE points into the '
                     'cave or the hooked words (the image that is actually loaded)')
    say('\n=== RESULT: %s (%d failures) ===' % ('PASS' if not fails else 'FAIL', len(fails)))
    for f in fails:
        say('  !! ' + f)
    open(f'build/{name}_exeverify.txt', 'w', encoding='utf-8').write('\n'.join(_log) + '\n')
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'hookA1'))
