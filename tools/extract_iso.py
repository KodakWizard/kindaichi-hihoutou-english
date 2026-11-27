"""ISO9660 (MODE2/2352) filesystem reader for PS1 discs. Extracts all files."""
import struct, os, sys, json
sys.setrecursionlimit(100000)

BIN = "Kindaichi Shounen no Jikenbo - Hihou-tou Arata naru Sangeki (Japan).bin"
OUT = "extract/iso"

SECTOR = 2352
DATA_OFF = 24
NSECT = 290161

class Mode2Image:
    def __init__(self, path):
        self.f = open(path, 'rb')
    def sector(self, lba, count=1):
        self.f.seek(lba*SECTOR + DATA_OFF)
        return self.f.read(2048*count)
    def extent(self, lba, size):
        out = bytearray()
        remaining = size
        s = lba
        while remaining > 0:
            n = min(remaining, 2048)
            out += self.sector(s)[:n]
            remaining -= 2048
            s += 1
        return bytes(out)

def le32(b): return struct.unpack('<I', b)[0]

def parse_dir_records(data):
    """Parse ISO dir records from raw extent data. Returns list of dicts."""
    recs = []
    i = 0
    while i < len(data):
        ln = data[i]
        if ln == 0:
            # records are 2048-aligned; skip to next 2048 boundary
            i = (i // 2048 + 1) * 2048
            continue
        if ln < 34: break
        rec = data[i:i+ln]
        lba = le32(rec[2:6])
        size = le32(rec[10:14])
        flags = rec[25]
        fid = rec[33:33+rec[32]]
        if fid in (b'\x00', b'\x01'):
            i += ln
            continue
        # strip version ;1
        name = fid.decode('latin1')
        if ';' in name: name = name.split(';')[0]
        recs.append({'name': name, 'lba': lba, 'size': size, 'dir': bool(flags & 2), 'flags': flags, 'rec_off': i, 'fid': fid.decode('latin1')})
        i += ln
    return recs

def main():
    img = Mode2Image(BIN)
    # sanity: root dir record from path table LBA 18: first record LBA of root
    pt = img.sector(18)
    # first record: len=1, ea=0, lba=le32(pt[2:6]), parent=1
    root_lba = le32(pt[2:6])
    print("path table L-type at LBA18, root dir LBA =", root_lba)

    tree = {}
    entries = []  # flat list for JSON db

    def walk(lba, path):
        # determine dir size from its own '.' record
        head = img.extent(lba, 2048)
        dsize = le32(head[10:14])
        data = img.extent(lba, dsize)
        recs = parse_dir_records(data)
        for r in recs:
            if r['name'] in ('.', '..'):
                continue
            rp = path + '/' + r['name']
            entries.append(r | {'path': rp})
            if r['dir']:
                walk(r['lba'], rp)

    walk(root_lba, '')
    dirs = [e for e in entries if e['dir']]
    files = [e for e in entries if not e['dir']]
    print(f"dirs={len(dirs)} files={len(files)}")
    for e in sorted(dirs, key=lambda x: x['path']):
        print(f"D  LBA {e['lba']:7d} {e['path']}")
    for e in sorted(files, key=lambda x: x['lba']):
        print(f"F  LBA {e['lba']:7d} len {e['size']:9d} {e['path']}")

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, '..', 'filelist.json'), 'w') as f:
        json.dump(entries, f, indent=1)
    for e in files:
        safe = e['path'].strip('/').replace('/', os.sep)
        outp = os.path.join(OUT, safe)
        os.makedirs(os.path.dirname(outp), exist_ok=True)
        with open(outp, 'wb') as f:
            f.write(img.extent(e['lba'], e['size']))
    print("extraction done ->", OUT)

if __name__ == '__main__':
    main()
