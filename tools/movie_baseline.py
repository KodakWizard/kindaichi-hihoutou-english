"""Carry release movie sectors into Workbench builds without touching other bytes."""
import hashlib,json
from pathlib import Path

def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def preserve_release_movies(candidate,config_path=Path('setup/movie-baseline.json')):
    if not config_path.exists():return
    config=json.loads(config_path.read_text(encoding='utf-8'))
    source=Path(config['source'])
    if source.resolve()==candidate.resolve():raise ValueError('Movie baseline and new build must be separate')
    if digest(source)!=config['source_sha256']:raise ValueError('Release movie baseline checksum mismatch')
    size=candidate.stat().st_size
    spans=sorted(config['movies'],key=lambda m:m['offset'])
    end=0
    for m in spans:
        if not end<=m['offset']<m['offset']+m['bytes']<=size:raise ValueError('Invalid movie extent')
        end=m['offset']+m['bytes']
    def outside_hash():
        h=hashlib.sha256();pos=0
        with candidate.open('rb') as f:
            for m in spans:
                remaining=m['offset']-pos
                while remaining:
                    b=f.read(min(1048576,remaining));assert b;h.update(b);remaining-=len(b)
                pos=m['offset']+m['bytes'];f.seek(pos)
            while True:
                b=f.read(1048576)
                if not b:break
                h.update(b)
        return h.hexdigest()
    before=outside_hash()
    with source.open('rb') as baseline,candidate.open('r+b') as output:
        for m in spans:
            baseline.seek(m['offset']);data=baseline.read(m['bytes'])
            if hashlib.sha256(data).hexdigest()!=m['sha256']:raise ValueError('Movie source mismatch: '+m['path'])
            output.seek(m['offset']);output.write(data)
    assert candidate.stat().st_size==size and outside_hash()==before
    with candidate.open('rb') as f:
        for m in spans:
            f.seek(m['offset']);assert hashlib.sha256(f.read(m['bytes'])).hexdigest()==m['sha256']
    print(f"Preserved {len(spans)} release movies ({config['subtitle_cues']} subtitle cues).",flush=True)
