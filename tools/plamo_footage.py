"""Export and run Japanese footage transcriptions through the project PLaMo adapter."""
import argparse
from contextlib import closing
from pathlib import Path

import translation_db as store
import translation_model as model

REVIEW = store.OUTPUT / "review" / "review_queue.jsonl"


def sources():
    for row in store.interchange(REVIEW):
        if row["kind"] != "video":
            continue
        if row["status"] == "transcribed" and row.get("jp", "").strip():
            yield {key: row[key] for key in ("id", "path", "source_sha256", "jp")}
        elif (row["path"] == "/MOV/OPD.STR" and row["status"] == "needs_review"
              and row["source_sha256"] == "5cfb91b4771cb3f2850f06ba0eb298e54cdb0412b1b7672a339b7c59645ce990"
              and row.get("note", "").startswith("Title verified at 00:04.")
              and len(row.get("jp", "").splitlines()) == 2):
            # Only the verified title at 00:04 is ready. The handwritten insert is still open.
            yield dict(id=row["id"] + ":title-00m04s", path=row["path"],
                       source_sha256=row["source_sha256"], jp=row["jp"], timestamp="00:04")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export")
    export.add_argument("directory", type=Path)
    run = sub.add_parser("run")
    run.add_argument("batch", type=Path)
    run.add_argument("results", type=Path)
    run.add_argument("--endpoint", default="http://127.0.0.1:8000/v1/completions")
    run.add_argument("--model", default="plamo-3-translate")
    args = parser.parse_args()
    ready = {row["id"]: row for row in sources()}
    if args.action == "export":
        args.directory.mkdir(parents=True, exist_ok=True)
        store.jsonl(args.directory / "footage_ready.jsonl", ready.values())
        if ready:
            sample = next(iter(ready.values()))
            (args.directory / "footage_sample.pfml3.txt").write_bytes(model.prompt(sample).encode("utf-8"))
        store.jsonl(args.directory / "footage_review.jsonl",
                    (row for row in store.interchange(REVIEW)
                     if row["kind"] == "video" and row["id"] not in ready))
        with closing(store.open_db()) as db:
            store.jsonl(args.directory / "story_sample.jsonl",
                        model.batch(db, store.source_rows(db), 5, "/OUT/SANBA.TXT@00001b"))
        print(f"{len(ready)} reviewed footage transcripts ready")
        return
    if args.batch.resolve() == args.results.resolve():
        parser.error("Batch and results must have distinct paths")
    batch = list(store.interchange(args.batch))
    if not batch:
        parser.error("No reviewed footage transcripts in this batch")
    with closing(store.open_db()) as db:
        print(model.run(db, batch, args.results, args.endpoint, args.model, media_sources=ready))


if __name__ == "__main__":
    main()
