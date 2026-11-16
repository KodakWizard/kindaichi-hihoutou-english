"""Canonical SQLite catalogue and lossless translation interchange."""
import csv
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path


OUTPUT = Path("extract/translation")
DATABASE = OUTPUT / "master.sqlite"
KINDS = ("dialogue", "choice", "speaker", "system", "caption")
SCHEMA = """
CREATE TABLE IF NOT EXISTS source (
    id TEXT PRIMARY KEY, path TEXT NOT NULL, offset INTEGER NOT NULL,
    kind TEXT NOT NULL, sha TEXT NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS source_path_kind_offset ON source(path, kind, offset);
CREATE TABLE IF NOT EXISTS resource (path TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS control (
    path TEXT NOT NULL, offset INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(path, offset)
);
CREATE TABLE IF NOT EXISTS revision (
    number INTEGER PRIMARY KEY, id TEXT NOT NULL REFERENCES source(id),
    status TEXT NOT NULL, model TEXT NOT NULL, text TEXT NOT NULL, note TEXT NOT NULL,
    origin TEXT NOT NULL, UNIQUE(id,status,model,text,note,origin)
);
CREATE TABLE IF NOT EXISTS legacy (
    id TEXT PRIMARY KEY, source_id TEXT REFERENCES source(id), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS glossary (
    id TEXT PRIMARY KEY, jp TEXT NOT NULL, en TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS annotation (
    scope TEXT NOT NULL, id TEXT NOT NULL, note TEXT NOT NULL, provenance TEXT NOT NULL,
    PRIMARY KEY(scope,id)
);
CREATE TABLE IF NOT EXISTS insertion (
    build TEXT NOT NULL, id TEXT NOT NULL REFERENCES source(id), revision INTEGER NOT NULL REFERENCES revision(number),
    PRIMARY KEY(build,id)
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY, text TEXT NOT NULL
);
"""


def dump(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def open_db(path=DATABASE):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    return db


def rebuild(db):
    from translation_source import collect

    resources, rows, controls = collect()
    with db:
        existing = {row["id"]: row["sha"] for row in db.execute("SELECT id,sha FROM source")}
        incoming = {row["id"]: row["source_sha256"] for row in rows}
        if len(incoming) != len(rows) or any(incoming.get(k) != v for k, v in existing.items()):
            raise ValueError("Source IDs disappeared or original bytes changed")
        db.executemany("""INSERT INTO source VALUES (?,?,?,?,?,?)
                          ON CONFLICT(id) DO UPDATE SET data=excluded.data""",
                       ((r["id"], r["path"], r["offset"], r["kind"], r["source_sha256"], dump(r))
                        for r in rows))
        db.execute("DELETE FROM control")
        db.execute("DELETE FROM resource")
        db.executemany("INSERT INTO resource VALUES (?,?)", ((r["path"], dump(r)) for r in resources))
        db.executemany("INSERT INTO control VALUES (?,?,?)",
                       ((r["path"], r["offset"], dump(r)) for r in controls))
        for row in rows:
            if row["kind"] == "speaker" and row["jp"]:
                db.execute("INSERT OR IGNORE INTO glossary VALUES (?,?,?,?)",
                           (row["id"], row["jp"], row["baseline_en"], "Original EXE and namesG4b"))
        slots = {(r["path"], r["offset"]): r for r in rows if r["kind"] == "dialogue"}
        with open("db/translation_db.csv", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                match = re.search(r"\bbyte(?:s)? (\d+)", row["identifier"])
                source = slots.get((row["iso_file"], int(match[1]))) if match else None
                # Exact text match guards the historical offset association.
                normalized = lambda text: " ".join(text.replace(" / ", " ").split())
                if source and normalized(source["jp"]) != normalized(row["jp_source"]):
                    source = None
                source_id = source["id"] if source else None
                db.execute("INSERT OR IGNORE INTO legacy VALUES (?,?,?)",
                           (row["id"], source_id, dump(row)))


def source_rows(db):
    return {row["id"]: json.loads(row["data"])
            for row in db.execute("SELECT data,id FROM source ORDER BY path,offset,kind")}


def revisions(db, status):
    return {row["id"]: dict(row) for row in db.execute(
        "SELECT * FROM revision WHERE status=? ORDER BY number", (status,))}


def interchange(path):
    with path.open(encoding="utf-8-sig", newline="") as file:
        if path.suffix.lower() == ".csv":
            yield from csv.DictReader(file)
        elif path.suffix.lower() == ".jsonl":
            yield from (json.loads(line) for line in file if line.strip())
        else:
            raise ValueError("Use .csv or .jsonl")


def import_results(db, path):
    rows = source_rows(db)
    seen = set()
    with db:
        for result in interchange(path):
            key = result["id"]
            row = rows.get(key)
            if (key in seen or row is None or row["kind"] not in KINDS
                    or result["source_sha256"] != row["source_sha256"]):
                raise ValueError(f"Duplicate, unknown, or stale result: {key}")
            seen.add(key)
            status, text = result["status"], result["text"]
            if status not in ("draft", "approved") or not isinstance(text, str) or not text.strip():
                raise ValueError(f"Invalid result: {key}")
            if not isinstance(result["model"], str) or not result["model"].strip():
                raise ValueError(f"Missing model/version: {key}")
            db.execute("""INSERT OR IGNORE INTO revision(id,status,model,text,note,origin)
                          VALUES (?,?,?,?,?,?)""",
                       (key, status, result["model"], text, result.get("note", ""), "interchange"))
    return len(seen)


def jsonl(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(dump(row) + "\n")


def export(db, directory):
    directory.mkdir(parents=True, exist_ok=True)
    rows = source_rows(db)
    for table, order in (("resource", "path"), ("control", "path,offset"), ("legacy", "id"),
                         ("glossary", "id"), ("annotation", "scope,id"), ("revision", "number"),
                         ("insertion", "build,id")):
        jsonl(directory / f"{table}.jsonl", (dict(row) for row in db.execute(
            f"SELECT * FROM {table} ORDER BY {order}")))
    jsonl(directory / "captions.jsonl", (row for row in rows.values() if row["kind"] == "caption"))
    jsonl(directory / "artwork.jsonl", (row for row in rows.values() if row["kind"] == "artwork"))
    controls = (json.loads(row[0]) for row in db.execute("SELECT data FROM control ORDER BY path,offset"))
    investigation_commands = {"#suiri", "#nextsuiri5", "#mbook", "#pannel", "$daisuiri",
                              "$nextsuiri1", "$nextsuiri2", "$nextsuiri3", "$nextsuiri4", "$nextsuiri5"}
    jsonl(directory / "investigation.jsonl", (
        row for row in controls if row["text"].split()[0] in investigation_commands))
    jsonl(directory / "source.jsonl", rows.values())
    jsonl(directory / "results.jsonl", (
        dict(id=row["id"], source_sha256=rows[row["id"]]["source_sha256"],
             status=row["status"], model=row["model"], text=row["text"], note=row["note"])
        for row in sorted((revisions(db, "draft") | revisions(db, "approved")).values(),
                          key=lambda r: r["id"])))
    with (directory / "source.csv").open("w", encoding="utf-8", newline="") as file:
        keys = sorted({key for row in rows.values() for key in row})
        writer = csv.DictWriter(file, keys)
        writer.writeheader()
        writer.writerows({k: dump(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}
                         for row in rows.values())
    speakers = {row["speaker"]: row for row in rows.values() if row["kind"] == "speaker"}
    scenes = {}
    for row in rows.values():
        if row.get("scene_id") is None:
            continue
        scene = scenes.setdefault(row["scene_id"], dict(
            id=row["scene_id"], label=row["scene"], path=row["path"],
            chapter=None, location=None, summary=None, prior_events=None,
            characters=set(), comments=[]))
        if row.get("speaker") not in (None, 0):
            scene["characters"].add(row["speaker"])
        if row["kind"] == "comment":
            scene["comments"].append(row["id"])
    jsonl(directory / "scenes.jsonl", (dict(scene, characters=sorted(scene["characters"]))
                                      for scene in scenes.values()))
    jsonl(directory / "context.jsonl", (
        dict(id=row["id"], scene=row.get("scene"), scene_id=row.get("scene_id"),
             conversation=row["conversation"], order=row["order"],
             previous=row["previous"], next=row["next"], branch=row["branch"],
             choice_block=row["choice_block"], choice_branch=row["choice_branch"],
             speaker_jp=speakers[row["speaker"]]["jp"] if row["speaker"] in speakers else None,
             speaker_en=speakers[row["speaker"]]["baseline_en"] if row["speaker"] in speakers else None,
             provenance="Adjacent script turns only; chapter order and branch reachability unresolved")
        for row in rows.values() if row["kind"] == "dialogue"))


def report(db):
    rows = source_rows(db)
    counts = Counter(row["kind"] for row in rows.values())
    resources = Counter(json.loads(row[0])["status"] for row in db.execute("SELECT data FROM resource"))
    scope = {key for key, row in rows.items() if row["kind"] in KINDS and row["jp"]}
    drafts = scope & revisions(db, "draft").keys()
    reviewed = scope & revisions(db, "approved").keys()
    inserted = scope & {row[0] for row in db.execute("SELECT DISTINCT id FROM insertion")}
    stages = {stage: {"count": count, "percent": round(100 * count / len(scope), 2) if scope else None}
              for stage, count in (("draft", len(drafts)), ("reviewed", len(reviewed)),
                                   ("reinserted", len(inserted)), ("in_game_db_verified", 0))}
    return dict(resources=dict(resources), story_scripts_catalogued=resources["catalogued"] - 1,
                whole_game_extraction_percent=None, entries=dict(counts), verified_offsets=len(rows),
                japanese_source_characters=sum(len(row["jp"]) for row in rows.values()),
                dialogue_with_speaker=sum(row.get("speaker") is not None for row in rows.values()
                                          if row["kind"] == "dialogue"),
                dialogue_with_scene=sum(row.get("scene_id") is not None for row in rows.values()
                                        if row["kind"] == "dialogue"),
                translatable_scope=len(scope), untranslated=len(scope - drafts - reviewed),
                legacy_records=db.execute("SELECT COUNT(*) FROM legacy").fetchone()[0],
                legacy_linked=db.execute("SELECT COUNT(source_id) FROM legacy").fetchone()[0],
                stages=stages)
