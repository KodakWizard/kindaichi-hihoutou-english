"""Publish a version-matched Workbench with the exact release movie baseline."""
import argparse,ast,hashlib,json,re,shutil,zipfile
from pathlib import Path
from clean_disc_patch import create_patch

def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--version',required=True)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--subtitles',type=Path,required=True)
    parser.add_argument('--output-parent',type=Path,required=True)
    args=parser.parse_args()
    assert re.fullmatch(r'[A-Za-z0-9_.-]+',args.version)
    assert digest(args.image)==args.expected_sha256
    out=args.output_parent/f'Kindaichi-English-Workbench-v{args.version}'
    assert not out.exists() and not Path(str(out)+'.zip').exists()
    project=out/'Project';runtime=out/'Runtime';(project/'tools').mkdir(parents=True)
    shell=Path('build/Kindaichi-English-Workbench-v0.1.0-alpha-file-picker')
    shutil.copytree(shell/'Runtime',runtime)
    shutil.copyfile(shell/'KindaichiEditor.exe',out/'KindaichiEditor.exe')
    todo=['translation_editor','setup_public_workbench'];included=set()
    while todo:
        name=todo.pop()
        if name in included:continue
        source=Path('tools')/(name+'.py')
        if not source.is_file():continue
        included.add(name);shutil.copyfile(source,project/'tools'/source.name)
        for node in ast.walk(ast.parse(source.read_text(encoding='utf-8-sig'))):
            imports=[x.name.split('.')[0] for x in node.names] if isinstance(node,ast.Import) else ([node.module.split('.')[0]] if isinstance(node,ast.ImportFrom) and node.module else [])
            todo.extend(n for n in imports if n not in included and (Path('tools')/(n+'.py')).is_file())
    html=Path('tools/translation_editor.html').read_text(encoding='utf-8')
    html=re.sub(r'<title>.*?</title>',f'<title>Kindaichi Workbench {args.version}</title>',html)
    html=re.sub(r'<h1>.*?</h1>',f'<h1>Kindaichi Workbench {args.version}</h1>',html,count=1)
    (project/'tools/translation_editor.html').write_text(html,encoding='utf-8')
    for name in ['setup','build','extract']:(project/name).mkdir(exist_ok=True)
    archive=json.loads(args.archive.read_text(encoding='utf-8'))
    index={e['id']:e['en'] for e in archive['entries'] if e.get('kind') in ('dialogue','choice') and e.get('insertable')}
    assert len(index)==7648
    (project/'build/editor_english.json').write_text(json.dumps(index,ensure_ascii=False),encoding='utf-8')
    for name in ['w_metrics_g4.json','w_metrics_release1.json','w_metrics_release2.json']:shutil.copyfile(Path('build')/name,project/'build'/name)
    shutil.copyfile('extract/filelist.json',project/'extract/filelist.json')
    for name in ['speakers.json','combined1.pyz']:shutil.copyfile(shell/'Project/setup'/name,project/'setup'/name)
    clean=Path('Kindaichi Shounen no Jikenbo - Hihou-tou Arata naru Sangeki (Japan).cue')
    print('Preparing release-matched clean-disc setup delta...',flush=True)
    assert create_patch(clean,args.image,project/'setup/alpha.pyz')==args.expected_sha256
    subs=out/'Subtitles';shutil.copytree(args.subtitles,subs)
    srtfiles=sorted(subs.glob('*.srt'));cues=sum(len(re.findall(r' --> ',p.read_text(encoding='utf-8-sig'))) for p in srtfiles)
    inventory={r['path']:r for r in json.loads(Path('extract/filelist.json').read_text())}
    movies=[]
    with args.image.open('rb') as image:
        for srt in srtfiles:
            path='/MOV/'+srt.stem+'.STR';record=inventory[path]
            offset=record['lba']*2352;size=((record['size']+2047)//2048)*2352
            image.seek(offset);data=image.read(size);assert len(data)==size
            movies.append(dict(path=path,offset=offset,bytes=size,sha256=hashlib.sha256(data).hexdigest()))
    (project/'setup/movie-baseline.json').write_text(json.dumps(dict(version=args.version,
        source='build/patched_gameplayGraphics2.bin',source_sha256=args.expected_sha256,
        subtitle_cues=cues,movies=movies),indent=2)+'\n',encoding='utf-8')
    (out/'START-HERE.txt').write_text(f'''KINDAICHI WORKBENCH {args.version}

Extract the whole folder, then open KindaichiEditor.exe.
On first launch, browse to your original Japanese SLPS-00601 BIN or CUE.
Your original stays untouched. Setup prepares the matching {args.version} baseline.
No separate Python installation is needed. Allow at least 4 GB free space.

This version includes the release's 6,858 dialogue passages, 790 choices,
approved graphics and {cues} reviewed movie subtitle cues in {len(movies)} movies.
Story and graphics changes made in the Workbench retain the release movies.
Save/approve your edits, then choose Build & patch and use a fresh build name.

Subtitles/ contains the reviewed English SRTs for this release. They are reference
files: editing an SRT alone does not re-encode captions into the game movies.
The movie baseline is preserved when you build story or graphics edits.

Back up Project/extract/translation before upgrading. Extract each new Workbench
version into a separate folder, then export/import your translation archive.
Keep the generated BIN and matching CUE together.

If you just want to play, use the separate standard BPS patch download.
This archive contains no original game image, movie assets, emulator or BIOS.
''',encoding='utf-8')
    (out/'Licences').mkdir();shutil.copyfile('build/beta-publish-2026-11-27/LICENSE',out/'Licences/Project-MIT.txt')
    info=dict(version=args.version,target_sha256=args.expected_sha256,english_entries=len(index),
        dialogue=6858,choices=790,subtitle_cues=cues,movies=len(movies),modules=sorted(included),
        movies_preserved_on_rebuild=True,raw_disc_or_extracted_assets_included=False)
    (out/'package-info.json').write_text(json.dumps(info,indent=2)+'\n',encoding='utf-8')
    for file in out.rglob('*'):
        if file.is_file():
            assert file.suffix.lower() not in {'.bin','.cue','.str','.txp','.tex','.pal','.jan','.sqlite','.bps'},file
            assert file.suffix.lower()!='.png' or file.is_relative_to(runtime),file
            assert 'extract/iso' not in file.as_posix(),file
    target=Path(shutil.make_archive(str(out),'zip',out.parent,out.name))
    with zipfile.ZipFile(target) as z:assert z.testzip() is None
    manifest=dict(name=target.name,path=str(target),size=target.stat().st_size,sha256=digest(target),**info)
    (args.output_parent/'workbench-publication.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,indent=2),flush=True)

if __name__=='__main__':main()
