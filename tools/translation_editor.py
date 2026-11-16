"""Serve the translation editor on loopback. Run from the project root."""
import argparse
import base64
import hashlib
import re
import sys
from functools import lru_cache
import subprocess
from threading import Lock, Thread
from datetime import datetime
from uuid import uuid4

from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import webbrowser

import translation_db as store

PAGE = Path(__file__).with_suffix(".html")
EDITABLE = "kind IN ('dialogue','choice','speaker','system') AND json_extract(data,'$.jp') <> ''"


def detail(db, key, baseline=None):
    record = db.execute("SELECT data FROM source WHERE id=? AND " + EDITABLE, (key,)).fetchone()
    if record is None:
        raise ValueError("Unknown entry")
    row = json.loads(record["data"])
    history = [dict(r) for r in db.execute(
        "SELECT number,status,model,text,note FROM revision WHERE id=? ORDER BY number DESC", (key,))]
    legacy = [json.loads(r[0]) for r in db.execute(
        "SELECT data FROM legacy WHERE source_id=? ORDER BY id", (key,))]
    context = {}
    if row["kind"] in ("dialogue", "choice"):
        for field, comparison, order in (("previous", "<", "DESC"), ("next", ">", "ASC")):
            neighbor = db.execute(f"""SELECT id FROM source WHERE path=? AND kind='dialogue'
                                    AND offset {comparison} ? AND json_extract(data,'$.jp') <> ''
                                    ORDER BY offset {order}, id LIMIT 1""",
                                  (row["path"], row["offset"])).fetchone()
            if neighbor:
                context[field] = neighbor["id"]
    speaker = db.execute("""SELECT data FROM source WHERE kind='speaker'
                            AND json_extract(data,'$.speaker')=?""", (row.get("speaker"),)).fetchone()
    if speaker:
        context["speaker"] = json.loads(speaker[0])["baseline_en"]
    return dict(source=row, history=history, legacy=legacy, context=context, baseline=(baseline or {}).get(key))


def scene_transcript(db, key, baseline):
    anchor = db.execute("""SELECT path,json_extract(data,'$.scene_id') AS scene_id,
                            json_extract(data,'$.scene') AS scene FROM source
                            WHERE id=? AND kind IN ('dialogue','choice')""", (key,)).fetchone()
    if anchor is None:
        raise ValueError("Select dialogue or a choice to open its scene")
    speakers = {}
    for record in db.execute("SELECT data FROM source WHERE kind='speaker'"):
        speaker = json.loads(record["data"])
        speakers[speaker["speaker"]] = speaker.get("baseline_en")
    entries = []
    for record in db.execute("""SELECT data,
            (SELECT text FROM revision WHERE id=source.id ORDER BY number DESC LIMIT 1) AS english
            FROM source WHERE path=? AND kind IN ('dialogue','choice')
            AND json_extract(data,'$.scene_id') IS ? AND json_extract(data,'$.jp') <> ''
            ORDER BY offset,id""", (anchor["path"], anchor["scene_id"])):
        row = json.loads(record["data"])
        number = row.get("speaker")
        entries.append(dict(id=row["id"], kind=row["kind"], speaker=speakers.get(number),
                            speaker_number=number, branch=row.get("branch") or [],
                            choice_branch=row.get("choice_branch"),
                            text=record["english"] or baseline.get(row["id"]) or row["jp"]))
    return dict(path=anchor["path"], scene=anchor["scene"], entries=entries)


CURRENT_TEXT = """COALESCE((SELECT text FROM revision WHERE id=source.id ORDER BY number DESC LIMIT 1),
                          (SELECT text FROM baseline_text WHERE id=source.id))"""
SCRIPT_ORDER = "CASE WHEN path='/OUT/SANBA.TXT' THEN 0 ELSE 1 END,path,offset,kind"


def load_baseline(db, baseline):
    db.execute("CREATE TEMP TABLE baseline_text (id TEXT PRIMARY KEY, text TEXT)")
    db.executemany("INSERT INTO baseline_text VALUES (?,?)", baseline.items())


@lru_cache(maxsize=32768)
def short_continuation(text):
    if not text:
        return 0
    import wrap
    from translation_build import page_words
    try:
        pages = list(wrap.paginate(text.strip(), wrap_metrics(), english_layout=True, fill_lines=True))
        return min((page_words(lines) for lines, _ in pages[1:]), default=0)
    except ValueError:
        return 0


@lru_cache(maxsize=1)
def wrap_metrics():
    import wrap
    return wrap.Metrics("build/w_metrics_g4.json")


def listing(db, query):
    clauses, values = [EDITABLE], []
    for field in ("path", "kind"):
        if query.get(field):
            clauses.append(field + "=?")
            values.append(query[field])
    if query.get("scene"):
        clauses.append("json_extract(data,'$.scene_id')=?")
        values.append(query["scene"])
    if query.get("short"):
        db.create_function("short_page", 1, short_continuation)
        clauses.append(f"kind='dialogue' AND short_page({CURRENT_TEXT}) BETWEEN 1 AND ?")
        values.append(int(query["short"]))
    if query.get("q"):
        clauses.append("""(instr(lower(json_extract(data,'$.jp')),lower(?)) OR instr(lower(id),lower(?))
            OR instr(lower(coalesce((SELECT text FROM baseline_text WHERE id=source.id),'')),lower(?))
            OR EXISTS (SELECT 1 FROM revision r WHERE r.id=source.id AND instr(lower(r.text),lower(?)))
            OR EXISTS (SELECT 1 FROM legacy l WHERE l.source_id=source.id
                       AND instr(lower(json_extract(l.data,'$.en_text')),lower(?))))""")
        values.extend([query["q"].strip()] * 5)
    approved = "EXISTS (SELECT 1 FROM revision WHERE id=source.id AND status='approved')"
    draft = "EXISTS (SELECT 1 FROM revision WHERE id=source.id AND status='draft')"
    status = f"CASE WHEN {approved} THEN 'Approved' WHEN {draft} THEN 'Draft' WHEN EXISTS (SELECT 1 FROM baseline_text WHERE id=source.id) THEN 'In build' ELSE 'Untranslated' END"
    if query.get("status"):
        clauses.append(f"({status})=?")
        values.append(query["status"])
    where = " AND ".join(clauses)
    page = max(0, min(100000, int(query.get("page", "0"))))
    total = db.execute(f"SELECT count(*) FROM source WHERE {where}", values).fetchone()[0]
    order = "path,offset,kind" if query.get("order") == "file" else SCRIPT_ORDER
    rows = db.execute(f"""SELECT id,path,kind,json_extract(data,'$.jp') AS jp,
                           json_extract(data,'$.scene') AS scene,
                           json_extract(data,'$.speaker') AS speaker,
                           {CURRENT_TEXT} AS english, {status} AS status
                           FROM source WHERE {where} ORDER BY {order} LIMIT 40 OFFSET ?""",
                      [*values, page * 40])
    return dict(total=total, rows=list(map(dict, rows)))


def replace_text(db, baseline, data):
    import translation_build as build
    from repair_plamo_dialogue import BASELINE_WINDOWS
    find, replace = data["find"], data["replace"]
    if not isinstance(find, str) or not find or not isinstance(replace, str):
        raise ValueError("Enter the text to find")
    expression = re.escape(find)
    if data.get("whole"):
        expression = r"(?<!\w)" + expression + r"(?!\w)"
    pattern = re.compile(expression, 0 if data.get("case") else re.I)
    with db:
        if data.get("token"):
            db.execute("BEGIN IMMEDIATE")
        rows = store.source_rows(db)
        approved = store.revisions(db, "approved")
        changes = {}
        for key, row in rows.items():
            if row["kind"] not in ("dialogue", "choice"):
                continue
            if any(data.get(field) and data[field] != row.get(actual)
                   for field, actual in (("path", "path"), ("scene", "scene_id"), ("kind", "kind"))):
                continue
            before = approved.get(key, {}).get("text", baseline.get(key, ""))
            after, count = pattern.subn(lambda _: replace, before)
            if count and after != before:
                changes[key] = dict(text=after.strip(), before=before, count=count)
        token = hashlib.sha256(store.dump(changes).encode("utf-8")).hexdigest()
        if data.get("token") and data["token"] != token:
            raise ValueError("Matching entries changed. Preview again before applying.")
        _, _, errors = build.plan(rows, changes, fill_lines=True,
                                   baseline_windows=BASELINE_WINDOWS, expanded_choices=True)
        for key, item in changes.items():
            if not item["text"]:
                errors[key] = "Translation cannot be blank"
        if data.get("token"):
            if errors:
                raise ValueError("Fix the invalid replacements before applying.")
            db.executemany("""INSERT INTO revision(id,status,model,text,note,origin)
                              VALUES (?,'approved','manual',?,?,?)""",
                           ((key, item["text"], f"Search and replace: {find}", "editor:" + uuid4().hex)
                            for key, item in changes.items()))
        return {"entries": len(changes), "matches": sum(item["count"] for item in changes.values()),
                "token": token, "errors": list(errors.items())[:30], "invalid": len(errors),
                "sample": list(changes.items())[:20]}



def preview(row, text):
    import translation_build as build
    import wrap
    from repair_plamo_dialogue import BASELINE_WINDOWS
    key = row["id"]
    _, _, errors = build.plan({key: row}, {key: {"text": text}},
                            fill_lines=True, baseline_windows=BASELINE_WINDOWS, expanded_choices=True)
    result = {"error": errors.get(key), "pages": []}
    if row["kind"] == "dialogue" and text.strip():
        try:
            metrics = wrap_metrics()
            for lines, _ in wrap.paginate(text.strip(), metrics, english_layout=True, fill_lines=True):
                result["pages"].append({
                    "lines": ["".join(token["src"] for token in line) for line in lines],
                    "words": build.page_words(lines),
                })
        except ValueError:
            pass
    return result


def save(db, data, *, portable=False, baseline=None):
    key = data["id"]
    text, note, status = data["text"], data["note"], data["status"]
    if (not isinstance(text, str) or not text.strip() or not isinstance(note, str)
            or status not in ("draft", "approved")):
        raise ValueError("Enter English text and choose a valid status")
    db.execute("BEGIN IMMEDIATE")
    with db:
        current = detail(db, key)
        latest = current["history"][0]["number"] if current["history"] else None
        if latest != data["revision"]:
            raise ValueError("This entry changed since you opened it. Copy your edit before reopening.")
        row = current["source"]
        if portable:
            if status == "approved":
                raise ValueError("Approve for build in the project editor")
            errors = {}
        else:
            issue = preview(row, text)["error"]
            errors = {key: issue} if issue else {}
        if status == "approved" and errors:
            raise ValueError(errors[key] + ". Save as draft to keep this edit.")
        db.execute("""INSERT INTO revision(id,status,model,text,note,origin) VALUES (?,?,?,?,?,?)""",
                   (key, status, "manual", text.strip(), note, "editor:" + uuid4().hex))
    return dict(warning=errors.get(key), **detail(db, key, baseline))


def build_task(job, patch, name, clean=None):
    from editor_project import OUTPUT
    try:
        with subprocess.Popen([sys.executable, "-B", "-u", "tools/editor_project.py", str(patch), name],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding="utf-8", errors="replace") as process:
            for line in process.stdout:
                job["log"] = (job["log"] + line)[-16000:]
            code = process.wait()
        if code == 0:
            cue = (OUTPUT / f"patched_{name}.cue").resolve()
            job["cue"] = str(cue)
            if clean is not None:
                from clean_disc_patch import create_patch
                release = cue.with_suffix(".pyz")
                job["log"] += "\nExporting and verifying the clean-disc patch.\n"
                digest = create_patch(clean, cue.with_suffix(".bin"), release)
                job["release"] = str(release)
                job["log"] += "Verified rebuilt BIN SHA-256 " + digest + "\n"
            job["status"] = "complete"
        else:
            job["status"] = "failed"
    except (OSError, ValueError) as error:
        job.update(status="failed", log=job["log"] + "\n" + str(error))


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body, content_type="application/json"):
        if content_type == "application/json":
            body = store.dump(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlsplit(self.path)
        query = {key: value[0] for key, value in parse_qs(route.query).items()}
        try:
            if route.path == "/":
                self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            with closing(store.open_db(self.server.database)) as db:
                if route.path == "/entries":
                    load_baseline(db, self.server.baseline)
                    data = listing(db, query)
                elif route.path == "/scenes":
                    data = [dict(row) for row in db.execute("""SELECT DISTINCT path,
                        json_extract(data,'$.scene') AS scene, json_extract(data,'$.scene_id') AS id
                        FROM source WHERE kind IN ('dialogue','choice') AND json_extract(data,'$.scene_id') IS NOT NULL
                        ORDER BY CASE WHEN path='/OUT/SANBA.TXT' THEN 0 ELSE 1 END,path,id""") if row["id"]]
                elif not self.server.portable and route.path == "/translation.json":
                    from editor_project import translation_archive
                    archive = translation_archive(db, self.server.baseline, blank=query.get("blank") == "1")
                    self.reply(200, json.dumps(archive, ensure_ascii=False, indent=2).encode("utf-8"),
                               "application/json; charset=utf-8")
                    return
                elif route.path == "/entry":
                    data = detail(db, query["id"], self.server.baseline)
                elif route.path == "/transcript":
                    data = scene_transcript(db, query["id"], self.server.baseline)
                elif not self.server.portable and route.path in ("/graphics", "/graphic", "/palette", "/texture.png", "/job", "/patch", "/release"):
                    import editor_project as project
                    if route.path == "/graphics":
                        data = project.catalogue()
                    elif route.path == "/graphic":
                        data = project.graphics_info(query["path"])
                    elif route.path == "/palette":
                        data = project.graphics_info(query["path"])["palettes"]
                    elif route.path == "/texture.png":
                        image = project.png(query["path"], int(query["page"]), int(query["depth"]),
                                            query.get("palette_path", ""), int(query.get("palette", 0)),
                                            original=query.get("original") == "1")
                        self.reply(200, image, "image/png")
                        return
                    elif route.path == "/job":
                        data = self.server.jobs[query["id"]]
                    else:
                        job = self.server.jobs[query["id"]]
                        self.reply(200, Path(job["release" if route.path == "/release" else "patch"]).read_bytes(), "application/octet-stream")
                        return
                elif route.path == "/settings":
                    data = {"portable": self.server.portable}
                elif route.path == "/drafts.jsonl":
                    rows = db.execute("""SELECT s.id, s.sha AS source_sha256, r.status,
                                                r.model, r.text, r.note
                                         FROM revision r JOIN source s ON s.id=r.id
                                         WHERE r.number IN (
                                             SELECT max(number) FROM revision
                                             WHERE status='draft' AND origin LIKE 'editor:%'
                                             GROUP BY id
                                         ) ORDER BY s.id""")
                    body = "".join(store.dump(dict(row)) + "\n" for row in rows)
                    self.reply(200, body.encode("utf-8"), "application/x-ndjson; charset=utf-8")
                    return
                elif route.path == "/paths":
                    data = [r[0] for r in db.execute("SELECT DISTINCT path FROM source WHERE "
                                                     + EDITABLE + " ORDER BY path")]
                else:
                    self.reply(404, {"error": "Unknown route"})
                    return
            self.reply(200, data)
        except (ValueError, KeyError, OSError) as error:
            self.reply(400, {"error": str(error)})

    def do_POST(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if (self.headers.get("Origin") != origin
                or self.headers.get_content_type() != "application/json"):
            self.reply(403, {"error": "Open the editor at " + origin})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            limit = {"/graphic": 2_000_000, "/build": 128_000_000}.get(self.path, 65_536)
            if not 0 < size <= limit:
                raise ValueError("Invalid request size")
            with closing(store.open_db(self.server.database)) as db:
                payload = json.loads(self.rfile.read(size))
                if self.path == "/entry":
                    data = save(db, payload, portable=self.server.portable, baseline=self.server.baseline)
                elif self.server.portable:
                    raise ValueError("Open the full project editor for this action")
                elif self.path == "/replace":
                    data = replace_text(db, self.server.baseline, payload)
                elif self.path == "/preview":
                    data = preview(detail(db, payload["id"])["source"], payload["text"])
                elif self.path in ("/graphic", "/build", "/extract"):
                    import editor_project as project
                    with self.server.action_lock:
                        if self.path == "/graphic":
                            project.queue_png(payload["path"], int(payload["page"]), int(payload["depth"]),
                                              base64.b64decode(payload["png"], validate=True))
                            data = {}
                        elif self.path == "/extract":
                            if any(job["status"] == "running" for job in self.server.jobs.values()):
                                raise ValueError("Wait for the current task to finish")
                            key = uuid4().hex
                            job = dict(status="running", log="", key=key)
                            self.server.jobs[key] = job
                            Thread(target=project.extract_stock, args=(payload["path"], job), daemon=True).start()
                            data = {"id": key}
                        else:
                            if any(job["status"] == "running" for job in self.server.jobs.values()):
                                raise ValueError("A build is already running")
                            clean = payload.get("clean")
                            if clean is not None:
                                from clean_disc_patch import stock_path
                                clean = stock_path(clean)
                            key = uuid4().hex
                            name = "editor_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + key[:6]
                            patch = project.OUTPUT / (name + ".kindaichi-patch")
                            if payload.get("patch"):
                                imported = json.loads(base64.b64decode(payload["patch"], validate=True).decode("utf-8-sig"))
                                if imported.get("format") == project.ARCHIVE:
                                    imported = project.archive_patch(imported, store.source_rows(db))
                                patch.write_text(json.dumps(imported, ensure_ascii=False), encoding="utf-8")
                                try:
                                    project.read_patch(patch, store.source_rows(db))
                                except (ValueError, KeyError, TypeError):
                                    patch.unlink()
                                    raise
                            else:
                                project.export_patch(db, patch, self.server.baseline)
                            job = dict(status="running", log="", patch=patch.as_posix())
                            self.server.jobs[key] = job
                            Thread(target=build_task, args=(job, patch, name, clean), daemon=True).start()
                            data = {"id": key}
                else:
                    raise ValueError("Unknown route")
            self.reply(200, data)
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.reply(400, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=store.DATABASE)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--portable", action="store_true")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("Database missing. Run python tools/translate_game.py rebuild")
    baseline = {}
    if not args.portable:
        from editor_project import index
        baseline = index()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.database = args.db
        server.baseline = baseline
        server.jobs = {}
        server.action_lock = Lock()
        server.portable = args.portable
        url = f"http://127.0.0.1:{server.server_port}"
        print(url, flush=True)
        if args.open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
