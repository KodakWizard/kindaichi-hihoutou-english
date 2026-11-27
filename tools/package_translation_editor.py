"""Package the Windows x64 editor with a private Python runtime and original demo text."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import translation_db as store

NAME = "KindaichiTranslationEditor-Portable"
ROOT = Path("build") / NAME
PYTHON = Path(sys.base_prefix)
STDLIB = ("collections", "concurrent", "email", "encodings", "html", "http", "importlib", "json",
          "logging", "re", "sqlite3", "urllib")
DLLS = ("_bz2.pyd", "_hashlib.pyd", "_lzma.pyd", "_socket.pyd", "_sqlite3.pyd",
        "_ssl.pyd", "_uuid.pyd", "libcrypto-3.dll", "libssl-3.dll", "select.pyd",
        "sqlite3.dll", "unicodedata.pyd")


def main():
    if sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32:
        raise SystemExit("Build using Windows x64 Python 3.11")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, help="English-only alpha catalogue")
    opts = parser.parse_args()
    root = ROOT if not opts.catalogue else Path("build/Kindaichi-English-Editor-v0.1.0-alpha")
    root.mkdir()
    runtime = root / "runtime"
    runtime.mkdir()
    for name in ("python.exe", "python311.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        shutil.copy2(PYTHON / name, runtime / name)
    for name in DLLS:
        shutil.copy2(PYTHON / "DLLs" / name, runtime / name)
    lib = runtime / "Lib"
    lib.mkdir()
    for path in (PYTHON / "Lib").glob("*.py"):
        shutil.copy2(path, lib)
    for name in STDLIB:
        shutil.copytree(PYTHON / "Lib" / name, lib / name,
                        ignore=shutil.ignore_patterns("__pycache__", "test", "tests"))
    (runtime / "python311._pth").write_text(".\nLib\n../app\n", encoding="ascii")
    app = root / "app"
    app.mkdir()
    for name in ("translation_editor.py", "translation_editor.html", "translation_db.py"):
        shutil.copy2(Path("tools") / name, app)
    demo = root / ("master.sqlite" if opts.catalogue else "demo.sqlite")
    if opts.catalogue:
        rows = json.loads(opts.catalogue.read_text(encoding="utf-8"))
        baseline = {}
        with store.open_db(demo) as db:
            for row in rows:
                text = row.pop("english", None)
                row["jp"] = "Japanese source available in the development project."
                if text:
                    baseline[row["id"]] = text
                db.execute("INSERT INTO source VALUES (?,?,?,?,?,?)",
                           (row["id"], row["path"], row["offset"], row["kind"],
                            row["source_sha256"], store.dump(row)))
        db.close()
        (root / "english.json").write_text(store.dump(baseline), encoding="utf-8")
        (root / "README.txt").write_text(
            "Extract the whole ZIP and open KindaichiTranslationEditor.exe.\n"
            "The editor opens in your browser. Keep the launcher window open.\n"
            "Search, browse scenes, save drafts and export drafts.\n"
            "The public editor contains English dialogue and choices.\n"
            "Use the separate BPS patch to create a playable disc.\n",
            encoding="utf-8")
    else:
        db = store.open_db(demo)
        with db:
            for i, jp in enumerate(("今日は晴れです。", "散歩に行きましょう。", "公園で会いましょう。"), 1):
                raw = jp.encode("utf-8")
                sha = hashlib.sha256(raw).hexdigest()
                key = f"/DEMO.TXT@{i:08x}:dialogue"
                row = dict(id=key, path="/DEMO.TXT", offset=i, size=len(raw), jp=jp,
                           kind="dialogue", scene="Demo", source_sha256=sha)
                db.execute("INSERT INTO source VALUES (?,?,?,?,?,?)",
                           (key, row["path"], i, "dialogue", sha, store.dump(row)))
        db.close()
        shutil.copy2("tools/translation_portable_readme.txt", root / "README.txt")
    compiler = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    exe = root / "KindaichiTranslationEditor.exe"
    subprocess.run([str(compiler), "/nologo", "/target:exe", "/optimize+", "/out:" + str(exe),
                    r"tools\TranslationEditorLauncher.cs"], check=True)
    subprocess.run([str(exe.resolve()), "--check"], check=True)
    shutil.make_archive(str(root), "zip", root.parent, root.name)


if __name__ == "__main__":
    main()
