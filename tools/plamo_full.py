"""Export and translate the complete Japanese catalogue with one command."""
import argparse
import sys
from contextlib import closing
from pathlib import Path

import plamo_footage
import translation_db as store
import translation_model as model

DIRECTORY = store.OUTPUT / "PLaMo3-Full"
INPUT = DIRECTORY / "all_japanese.jsonl"


def show_progress(index, total, row, text, cached):
    status = "cached" if cached else "translated"
    print(f"[{index:,}/{total:,}] {status} | {row['id']}\n"
          f"  JP {row['jp']}\n  EN {text}", flush=True)


def catalogue(db):
    rows = store.source_rows(db)
    contextual = {row["id"]: row for row in model.batch(db, rows, None, include_existing=True)}
    sources = {key: contextual.get(key, {field: row[field] for field in
                                         ("id", "path", "kind", "source_sha256", "jp")})
               for key, row in rows.items() if row["jp"].strip()}
    for row in store.interchange(plamo_footage.REVIEW):
        if row.get("jp", "").strip():
            source = {field: row[field] for field in ("id", "path", "kind", "source_sha256", "jp")}
            source.update(review_status=row["status"], note=row.get("note", ""))
            if row["id"] in sources and sources[row["id"]]["jp"] != row["jp"]:
                source["catalogued_jp"] = sources[row["id"]]["jp"]
            sources[row["id"]] = source
    for clip in plamo_footage.sources():
        parent = sources.get(clip["id"].split(":title-", 1)[0])
        if parent and clip.get("timestamp") and clip["jp"] == parent["jp"]:
            parent["timestamp"] = clip["timestamp"]
    for source in sources.values():
        source["path"] = source["id"].split("@", 1)[0]
    return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "run"))
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--model", default="plamo-en:latest")
    parser.add_argument("--context-limit", type=int, default=4096)
    parser.add_argument("--quiet", action="store_true", help="Hide per-entry translation output")
    args = parser.parse_args()
    with closing(store.open_db()) as db:
        sources = catalogue(db)
        if args.action == "export":
            DIRECTORY.mkdir(parents=True, exist_ok=True)
            store.jsonl(INPUT, sources.values())
            store.jsonl(DIRECTORY / "source_review_queue.jsonl",
                        (row for row in store.interchange(plamo_footage.REVIEW)
                         if row["status"] != "no_text"))
            print(f"Exported {len(sources)} Japanese entries to {INPUT}")
            return
        material = list(store.interchange(INPUT))
        if material != list(sources.values()):
            raise ValueError("Catalogue changed. Refresh it with export before running.")
        sys.stdout.reconfigure(encoding="utf-8")
        print(f"Processing {len(material):,} entries with {args.model} at {args.endpoint}",
              flush=True)
        count = model.run(db, material, DIRECTORY / "all_english_drafts.jsonl",
                          args.endpoint, args.model, context_limit=args.context_limit,
                          media_sources=sources, progress=None if args.quiet else show_progress)
        print(f"Completed {count:,} entries.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
