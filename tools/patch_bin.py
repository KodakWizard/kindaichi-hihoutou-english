"""Patch a copy of the disc image: replace file payloads at fixed LBAs with
equal-size replacements. EDC/ECC fields are left byte-identical to the source.

Verified discovery (tools/edc_stats.py + tools/brute_edc.py + tools/sector_analysis.py
+ tools/iso_truth2.py, 2026-11-08): in this rip the EDC field (raw offset 2072)
matches NO plausible CRC variant (ranges 16/24..2071, init 0/FF, xor/no-xor,
LE+BE) on ANY of 1,985 sampled sectors (valid_any = 0/1985). Sector header
timecodes are ALSO garbage (e.g. LBA 20055 carries 04:29:30 = LBA 16170).
The source image ships with fully invalid error-correction/addressing data,
including in sectors whose text renders correctly in DuckStation (e.g. the
original LBA 20055 SANBA.TXT dialogue displays fine) -> these fields are
demonstrably not validated by emulator or game, so byte-identical fields are
not a regression. Optional: "recompute_edc": true in the manifest regenerates
a standards-correct EDC (MODE2/2352: CRC over raw bytes 16..2071 inclusive,
table poly 0xEDB88320 reflected, init 0, stored LE at 2072) but ONLY for
sectors whose payload bytes actually changed; sectors with unchanged payload
are never written at all. ECC left untouched (no recoverable valid form exists).

Usage: python tools/patch_bin.py [manifest.json]  # [{iso_path, local_file}]
"""
import json, os, sys, shutil

BIN_ORIG = 'Kindaichi Shounen no Jikenbo - Hihou-tou Arata naru Sangeki (Japan).bin'
BIN_OUT  = 'build/patched.bin'
SECTOR   = 2352
DATA_OFF = 24

def _edc_table():
    tab = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 if c & 1 else 0)
        tab.append(c)
    return tab
_EDC_TAB = _edc_table()

def edc_compute(data, init=0):
    e = init
    for b in data:
        e = _EDC_TAB[(e ^ b) & 0xFF] ^ (e >> 8)
    return e

def patch(bin_out: str, manifest: list, filelist_path: str = 'extract/filelist.json',
          recompute_edc: bool = False):
    fl = {e['path']: e for e in json.load(open(filelist_path))}
    with open(bin_out, 'r+b') as f:
        for m in manifest:
            e = fl[m['iso_path']]
            data = open(m['local_file'], 'rb').read()
            assert len(data) == e['size'], f"{m['iso_path']}: size {len(data)} != {e['size']}"
            start, nsec = e['lba'], (e['size'] + 2047) // 2048
            f.seek(start * SECTOR); before = f.read(nsec * SECTOR)
            for i in range(nsec):
                chunk = data[i*2048:(i+1)*2048]
                new_payload = chunk + b'\x00' * (2048 - len(chunk))
                a = before[i*SECTOR:(i+1)*SECTOR]
                if a[DATA_OFF:DATA_OFF+2048] == new_payload:
                    continue  # payload identical: leave sector 100% untouched
                f.seek((start+i)*SECTOR + DATA_OFF)
                f.write(new_payload)
                if recompute_edc:
                    # standards-correct MODE2/2352 EDC: CRC over raw 16..2071
                    # (header tail unchanged, so compute from `a` + new payload)
                    edc = edc_compute(a[16:DATA_OFF] + new_payload)
                    f.seek((start+i)*SECTOR + 2072)
                    f.write(edc.to_bytes(4, 'little'))
            # verify: payload matches; untouched sectors byte-identical;
            # changed sectors: header + ECC identical, EDC valid (if recomputed)
            f.seek(start * SECTOR); after = f.read(nsec * SECTOR)
            got = b''.join(after[i*SECTOR+DATA_OFF:i*SECTOR+DATA_OFF+2048] for i in range(nsec))[:len(data)]
            assert got == data, f'{m["iso_path"]}: payload verify failed'
            for i in range(nsec):
                a, b = before[i*SECTOR:(i+1)*SECTOR], after[i*SECTOR:(i+1)*SECTOR]
                assert a[:DATA_OFF] == b[:DATA_OFF], f'sector {start+i}: header changed!'
                if a[DATA_OFF:DATA_OFF+2048] != b[DATA_OFF:DATA_OFF+2048]:
                    if recompute_edc:
                        assert a[2076:] == b[2076:], f'sector {start+i}: ECC changed!'
                        chk = edc_compute(b[16:2072])
                        assert int.from_bytes(b[2072:2076], 'little') == chk, f'sector {start+i}: EDC verify failed!'
                    else:
                        assert a[DATA_OFF+2048:] == b[DATA_OFF+2048:], f'sector {start+i}: EDC/ECC changed!'
                else:
                    assert a == b, f'sector {start+i}: payload unchanged but sector differs!'
                assert b[:7] == b'\x00\xff\xff\xff\xff\xff\xff', f'sector {start+i}: bad sync'
            changed = sum(1 for i in range(nsec)
                          if before[i*SECTOR+DATA_OFF:i*SECTOR+DATA_OFF+2048]
                          != after[i*SECTOR+DATA_OFF:i*SECTOR+DATA_OFF+2048])
            print(f'patched+verified {m["iso_path"]} LBA {start} x{nsec} sectors ({changed} payload-changed)')

def main():
    bin_out = BIN_OUT
    redo_edc = False
    if len(sys.argv) > 1:
        man = json.load(open(sys.argv[1]))
        if isinstance(man, dict):  # {"bin_out": "...", "entries": [...], "recompute_edc": bool}
            bin_out = man['bin_out']; redo_edc = bool(man.get('recompute_edc', False)); man = man['entries']
    else:
        man = [{'iso_path': '/IN/KOGURE.TXT', 'local_file': 'build/IN/KOGURE.TXT'}]
    if not os.path.exists('build'):
        os.makedirs('build')
    if not os.path.exists(bin_out):
        print('copying BIN (682 MB)...')
        shutil.copyfile(BIN_ORIG, bin_out)
    patch(bin_out, man, recompute_edc=redo_edc)
    print('done ->', bin_out)

if __name__ == '__main__':
    main()
