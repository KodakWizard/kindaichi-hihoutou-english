"""PLaMo PFML3 and local OpenAI-compatible Completions transport."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from collections import Counter

from translation_db import dump, revisions
from translation_source import digest

TAG = "<|plamo:tag|>"
MSG = "<|plamo:msg|>"
OUTPUT = TAG + "output<|plamo:key|>lang<|plamo:val|>en" + MSG


def batch(db, rows, limit, prefix="", *, include_existing=False):
    done = revisions(db, "draft").keys() | revisions(db, "approved").keys()
    glossary = list(map(dict, db.execute("SELECT jp,en FROM glossary ORDER BY id")))
    speakers = {row["speaker"]: row for row in rows.values() if row["kind"] == "speaker"}
    count = 0
    for key, row in rows.items():
        if row["kind"] not in ("dialogue", "choice", "system", "caption") or (key in done and not include_existing) or not key.startswith(prefix):
            continue
        previous = rows.get(row.get("previous"))
        speaker = speakers.get(row.get("speaker"), {})
        yield dict(id=key, source_sha256=row["source_sha256"], jp=row["jp"],
                   kind=row["kind"],
                   layout=dict(max_lines_per_page=3, original_window_bytes=row["size"],
                               wrapping="automatic",
                               pagination="conditional on native key boundary and encoded byte budget")
                   if row["kind"] == "dialogue" else None,
                   conversation=row.get("conversation"), scene_id=row.get("scene_id"),
                   speaker=speaker.get("jp"),
                   previous_jp=previous["jp"] if previous else None,
                   glossary=[g for g in glossary if g["jp"] in row["jp"] or g["jp"] == speaker.get("jp")])
        count += 1
        if count == limit:
            break


def prompt(row):
    source = row["jp"]
    context = row.get("repair_context") or "\n".join(
        x for x in (row.get("previous_jp"), row.get("speaker")) if x)
    terms = row.get("glossary", [])
    fields = [source, context] + [g[k] for g in terms for k in ("jp", "en")]
    if any("<|plamo:" in field for field in fields):
        raise ValueError("Reserved PFML3 token in input")
    glossary = "".join("<|plamo:index|>" + g["jp"] + "<|plamo:item|>" + g["en"] for g in terms)
    return (TAG + "dataset" + MSG + "translation"
            + (TAG + "context" + MSG + context if context else "")
            + (TAG + "glossary" + glossary if glossary else "")
            + TAG + "input" + MSG + source + OUTPUT)


def run(db, batch_rows, output, endpoint, model, context_limit=4096, max_tokens=512, *,
        media_sources=None, progress=None):
    # The byte bound is conservative for byte-fallback tokenizers; allow BOS/EOS overhead.
    if not 0 < max_tokens < context_limit <= 32768:
        raise ValueError("Invalid context or output budget")
    material = list(batch_rows)
    keys = Counter(row["id"] for row in material)
    sources = {r["id"]: r for r in db.execute("SELECT id,sha,data FROM source")}
    for row in material:
        if media_sources is not None:
            match = media_sources.get(row["id"])
            if (keys[row["id"]] != 1 or match is None
                    or row["source_sha256"] != match["source_sha256"]
                    or row["jp"] != match["jp"]):
                raise ValueError("Invalid or stale media batch row: " + row["id"])
            continue
        match = sources.get(row["id"])
        if (keys[row["id"]] != 1 or match is None or row["source_sha256"] != match["sha"]
                or row["jp"] != json.loads(match["data"])["jp"]):
            raise ValueError("Invalid or stale batch row: " + row["id"])
    with output.open("w", encoding="utf-8", newline="\n") as file:
        for index, row in enumerate(material, 1):
            formatted = prompt(row)
            native = urlsplit(endpoint).path.rstrip("/") == "/api/generate"
            if native:
                request_body = dict(model=model, prompt=formatted, raw=True, stream=False,
                                    options=dict(temperature=0, num_predict=max_tokens,
                                                 num_ctx=context_limit, stop=[TAG]))
            else:
                request_body = dict(model=model, prompt=formatted, temperature=0,
                                    max_tokens=max_tokens, stop=[TAG])
            encoded = dump(request_body).encode("utf-8")
            if len(request_body["prompt"].encode()) + max_tokens + 8 > context_limit:
                raise ValueError("Prompt exceeds conservative budget: " + row["id"])
            key = digest(endpoint.encode() + b"\0" + encoded)
            cached = db.execute("SELECT text FROM cache WHERE key=?", (key,)).fetchone()
            if cached is None:
                request = urllib.request.Request(endpoint, data=encoded,
                                                 headers={"Content-Type": "application/json; charset=utf-8"})
                try:
                    with urllib.request.urlopen(request, timeout=180) as response:
                        result = json.load(response)
                except urllib.error.HTTPError as error:
                    detail = error.read().decode("utf-8", errors="replace")
                    raise ValueError(f"Server returned HTTP {error.code} for {model}: {detail}") from error
                except urllib.error.URLError as error:
                    raise ValueError(f"Cannot reach {endpoint}. Start the local model server "
                                     f"or check the endpoint. {error.reason}") from error
                if native:
                    text = result.get("response", "").strip()
                    finished = result.get("done") and result.get("done_reason") == "stop"
                else:
                    choice, = result["choices"]
                    text = choice["text"].strip()
                    finished = choice.get("finish_reason") == "stop"
                if not finished or not text or "<|plamo:" in text:
                    raise ValueError("Malformed or truncated completion: " + row["id"])
                with db:
                    db.execute("INSERT OR IGNORE INTO cache VALUES (?,?)", (key, text))
            else:
                text = cached["text"]
            draft = dict(id=row["id"], source_sha256=row["source_sha256"],
                         status="draft", model=model, text=text)
            if media_sources is not None:
                draft.update({field: row[field] for field in
                              ("path", "kind", "timestamp", "review_status", "note") if field in row})
            file.write(dump(draft) + "\n")
            file.flush()
            if progress is not None:
                progress(index, len(material), row, text, cached is not None)
    return len(material)
