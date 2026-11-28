"""Fresh packaged-runtime setup and rebuild; release movies must survive edits."""
import argparse,json,os,subprocess,zipfile,uuid
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('package',type=Path);args=parser.parse_args()
    root=Path.cwd();test=root/'build'/('wb-test-'+uuid.uuid4().hex[:6]);test.mkdir(exist_ok=False)
    with zipfile.ZipFile(args.package) as z:
        assert z.testzip() is None
        for item in z.infolist():
            parts=Path(item.filename).parts[1:]
            if not parts:continue
            target=test.joinpath(*parts)
            assert target.resolve().is_relative_to(test.resolve())
            if item.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(item))
    project=test/'Project';python=test/'Runtime/python.exe'
    source=root/'Kindaichi Shounen no Jikenbo - Hihou-tou Arata naru Sangeki (Japan).cue'
    env=dict(os.environ,KINDAICHI_EDITOR_FROZEN_INDEX='1')
    def run(arguments,log):
        with (test/log).open('w',encoding='utf-8') as f:
            result=subprocess.run([str(python),'-B','-u',*arguments],cwd=project,env=env,stdout=f,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError((test/log).read_text(encoding='utf-8',errors='replace')[-4000:])
    print('Fresh setup using packaged Python and original disc...',flush=True)
    run(['tools/setup_public_workbench.py',str(source)],'setup.log')
    code='''import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,'tools')
import editor_project as p,translation_db as d
def sha(x):
 with Path(x).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
info=json.loads(Path('../package-info.json').read_text())
assert sha(p.LATEST)==info['target_sha256']
index=p.index();assert len(index)==info['english_entries']
with d.open_db() as db:
 rows=d.source_rows(db);archive=p.translation_archive(db,index)
patch=p.archive_patch(archive,rows)
path=Path('build/workbench10-check.kindaichi-patch');path.write_text(json.dumps(patch,ensure_ascii=False),encoding='utf-8')
assert len(p.catalogue()['textures'])==280
assert p.png('/ETC/MEMO1.TXP',10,4).startswith(bytes.fromhex('89504e47'))
p.build_archive(path,'Workbench10Roundtrip')
assert sha('build/patched_Workbench10Roundtrip.bin')==info['target_sha256']
movieconfig=json.loads(Path('setup/movie-baseline.json').read_text())
entry=next(e for e in patch['archive']['entries'] if e['id'].startswith('/OUT/SANBA.TXT') and e['id'].endswith(':dialogue'))
entry['en']='Workbench check.'
edited=Path('build/workbench10-edited.kindaichi-patch');edited.write_text(json.dumps(patch,ensure_ascii=False),encoding='utf-8')
p.build_archive(edited,'Workbench10Edited')
changed=Path('build/patched_Workbench10Edited.bin')
assert sha(changed)!=info['target_sha256']
with changed.open('rb') as f:
 for m in movieconfig['movies']:
  f.seek(m['offset']);assert hashlib.sha256(f.read(m['bytes'])).hexdigest()==m['sha256']
 assert b'Workbench' in changed.read_bytes()
result=dict(version=info['version'],fresh_setup_exact=True,unmodified_rebuild_sha256=info['target_sha256'],edited_dialogue_applied=True,all_release_movies_preserved_after_edit=True,subtitle_cues=info['subtitle_cues'],movies=info['movies'],english_entries=len(index),texture_catalogue=280,navigation_png=True,packaged_runtime=True)
Path('../verification.json').write_text(json.dumps(result,indent=2)+'\\n',encoding='utf-8')
print(json.dumps(result,indent=2))
'''
    print('Checking UI data, exact release rebuild and edited-dialogue movie preservation...',flush=True)
    run(['-c',code],'rebuild.log')
    result=json.loads((test/'verification.json').read_text())
    parent=args.package.parent
    (parent/'workbench-verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':main()
