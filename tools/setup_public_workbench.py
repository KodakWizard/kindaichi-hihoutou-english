"""Prepare a full translation workbench from the user's clean disc."""
from contextlib import closing
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from clean_disc_patch import apply_patch, check_stock, stock_path
import editor_project as project
from translation_source import extent, inventory, scripts
import translation_db as database


def setup(selection):
    if database.DATABASE.exists():
        raise ValueError("Workbench is already configured.")
    stock = stock_path(selection)
    if stock in {project.disc.BASE.resolve(), project.LATEST.resolve()}:
        raise ValueError("Keep your original disc outside the generated build folder.")
    with stock.open("rb") as source:
        check_stock(source)
        records = inventory()
        expected = json.loads(project.INDEX.read_text(encoding="utf-8"))
        rows = []
        for record in records.values():
            if Path(record["path"]).suffix != ".TXT" or not record["path"].startswith(("/IN/", "/OUT/")):
                continue
            found, _ = scripts(record, extent(source, record))
            rows.extend(row for row in found if row["id"] in expected)
        if {row["id"] for row in rows} != set(expected):
            raise ValueError("The original script catalogue does not match this alpha.")
        rows.extend(json.loads(Path("setup/speakers.json").read_text(encoding="utf-8")))
    with TemporaryDirectory(dir=".") as folder:
        temporary = Path(folder)
        for patch, destination in (
            ("combined1.pyz", project.disc.BASE),
            ("alpha.pyz", project.LATEST),
        ):
            print(f"Preparing {destination.stem}...", flush=True)
            staged = temporary / destination.name
            apply_patch(Path("setup") / patch, stock, staged)
            staged.replace(destination)
            staged.with_suffix(".cue").replace(destination.with_suffix(".cue"))
        staged_db = temporary / "master.sqlite"
        with closing(database.open_db(staged_db)) as db, db:
            db.executemany("INSERT INTO source VALUES (?,?,?,?,?,?)",
                           ((r["id"], r["path"], r["offset"], r["kind"],
                             r["source_sha256"], database.dump(r)) for r in rows))
        database.DATABASE.parent.mkdir(parents=True, exist_ok=True)
        staged_db.rename(database.DATABASE)
    print("Workbench ready.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disc", help="Path to the clean Japanese SLPS-00601 BIN or CUE")
    args = parser.parse_args()
    try:
        setup(args.disc)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
