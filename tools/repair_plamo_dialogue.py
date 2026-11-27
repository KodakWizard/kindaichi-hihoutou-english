"""Retranslate opening and rejected dialogue with local PLaMo, then check coverage."""
import argparse
import re
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import build_plamo_preview as preview
import plamo_full
import translation_build as disc
import translation_db as store
import translation_model as model


OPENING = "/OUT/SANBA.TXT"
BASELINE_WINDOWS = {f"{OPENING}@{offset:08x}:dialogue": sha for offset, sha in {
    0x14a0: "bc4f975ce191ed16028acb7ab50b61d963afa4d95148f0b676e4529015924f0a",
    0x14f8: "61dca0987f1bc210cf5145aab9a2d1036a25f314bd8ed075fae3d847e40eebee",
    0x1532: "29840c6fc34a7bc22060c50f673ef9e255819bed63fc3a3ac3c66572698545a8",
    0x1599: "62cf2444b18227d31e0f3c3712203ec108a63061016dd172871197aa9b3b88e0",
    0x15ef: "12ee6a7b98e3c015b0f7fae37f213bcc8e521646d28a1c5bcd218d25f4472991",
    0x1673: "a504e093b88185a2215d295cf3069df88239f11716affa34f6d1daa56be65312",
    0x16ff: "fa08d1a13e09d6f3285402bfb738881ddf995c43c92aef8aef1f0442896548f9",
    0x176a: "62a005fb0040c6bb441192bec86cd0a720e052ed5a9c29fe6bdcf0c49320ddf2",
}.items()}
PRONOUNS = re.compile(r"\b(?:he|him|his|himself|she|her|hers|herself)\b", re.I)
WORK = plamo_full.DIRECTORY / "dialogue_repair"


def request_rows(queue, catalogue, rows, errors, attempt):
    following = defaultdict(list)
    for key, row in rows.items():
        if row["kind"] == "dialogue" and row.get("previous"):
            following[row["previous"]].append(key)
    for key in queue:
        source = catalogue[key]
        next_keys = following[key]
        next_line = catalogue.get(next_keys[0], {}) if len(next_keys) == 1 else {}
        context = [
            "Translate only the target Japanese into concise, natural spoken English. "
            "Fit one short dialogue page where possible. Preserve clues, names, numbers, "
            "negation and stutters. Use neighboring turns to identify referents, "
            "and use a name or neutral wording if gender is unclear. Do not infer the "
            "referent's gender from the speaker. Output only the English.",
            f"Speaker: {source.get('speaker') or 'unknown'}",
            f"Previous turn: {source.get('previous_jp') or ''}",
            f"Next turn ({next_line.get('speaker') or 'unknown'}): {next_line.get('jp', '')}",
        ]
        if attempt >= 2:
            context.append(f"The previous version failed layout validation ({errors.get(key, 'missing draft')}). "
                           "Use fewer, shorter words while keeping the meaning. "
                           "Fill the first page before continuing.")
        if attempt == 3:
            context.append("This entry needs a single page if the layout error mentions a "
                           "native boundary or header. Aim for 45 to 60 English characters. "
                           "Use direct speech and short words. Preserve every clue and who did what.")
        yield dict(source, repair_context="\n".join(context))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="A fresh build name")
    parser.add_argument("--review-pronouns", action="store_true",
                        help="Also recheck existing drafts containing gendered pronouns")
    parser.add_argument("--fixes", type=Path, help="JSONL with manually corrected dialogue")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--next-pass", action="store_true", help="Retry only unresolved saved repairs")
    mode.add_argument("--check-only", action="store_true", help="Revalidate saved repairs without Ollama")
    mode.add_argument("--build-saved", action="store_true", help="Build from saved repairs if all dialogue passes")
    parser.add_argument("--choices-and-memo", action="store_true",
                        help="Include all choice drafts and six English case-note textures")
    parser.add_argument("--more-artwork", action="store_true",
                        help="Include the deduction verses, card menu and floor plans")
    parser.add_argument("--portrait-names", action="store_true",
                        help="Include reviewed suspect portrait labels")
    parser.add_argument("--preview", action="store_true",
                        help="Build a test disc even when dialogue remains unresolved")
    parser.add_argument("--editor-index", type=Path)
    parser.add_argument("--editor-patch", type=Path)
    args = parser.parse_args()
    if args.portrait_names and not args.more_artwork:
        parser.error("--portrait-names requires --more-artwork")
    if args.more_artwork and not args.choices_and_memo:
        parser.error("--more-artwork requires --choices-and-memo")
    if args.choices_and_memo and not args.build_saved:
        parser.error("--choices-and-memo requires --build-saved")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("Build name must contain only letters, numbers, hyphens or underscores")
    sys.stdout.reconfigure(encoding="utf-8")
    WORK.mkdir(parents=True, exist_ok=True)
    with closing(store.open_db()) as db:
        rows = store.source_rows(db)
        catalogue = plamo_full.catalogue(db)
        drafts, _ = preview.load_drafts(catalogue)
        approved = store.revisions(db, "approved")
        opening = sorted((key for key, row in rows.items()
                          if row["path"] == OPENING and row["kind"] == "dialogue"
                          and row["speaker"] != 0), key=lambda key: rows[key]["offset"])[:20]
        required = {key for key, row in rows.items() if row["kind"] == "dialogue" and row["jp"].strip()}
        repairs = {}
        validation = dict(fill_lines=True, baseline_windows=BASELINE_WINDOWS)
        if args.choices_and_memo:
            validation["expanded_choices"] = True
        saved_mode = args.next_pass or args.check_only or args.build_saved
        if saved_mode:
            for repair in store.interchange(WORK / "repairs.jsonl"):
                key = repair["id"]
                if (key not in required or key in repairs
                        or repair["source_sha256"] != rows[key]["source_sha256"]):
                    raise ValueError(f"Duplicate or stale saved repair: {key}")
                repairs[key] = repair
            selected = drafts | repairs | approved
            targets = set()
            if args.next_pass and not args.check_only:
                print("Checking saved repairs and page breaks...", flush=True)
                _, _, errors = disc.plan(rows, selected, **validation)
                targets = ((errors.keys() & required) | (required - selected.keys())) - approved.keys()
        else:
            _, _, errors = disc.plan(rows, drafts | approved, **validation)
            targets = (set(opening) | (errors.keys() & required) | (required - drafts.keys())) - approved.keys()
        if args.review_pronouns and not saved_mode:
            targets.update(key for key in (required & drafts.keys()) - approved.keys()
                           if PRONOUNS.search(drafts[key]["text"]))
        fixes = {}
        if args.fixes:
            for fix in store.interchange(args.fixes):
                key = fix["id"]
                if (key not in required or (key in approved and not (args.editor_index or args.editor_patch)) or key in fixes
                        or fix["source_sha256"] != rows[key]["source_sha256"]):
                    raise ValueError(f"Invalid or stale manual correction: {key}")
                fixes[key] = dict(text=fix["draft"])
        targets -= fixes.keys()
        if args.editor_index or args.editor_patch:
            approved = {key: value for key, value in approved.items()
                        if not value.get("origin", "").startswith("editor:")}
            if args.editor_patch:
                from editor_project import read_patch
                editor_text, _ = read_patch(args.editor_patch, rows)
                approved.update(editor_text)
        sources = {key: catalogue[key] for key in targets & catalogue.keys()}
        queue = sorted(sources)
        attempts = (3,) if args.next_pass else () if saved_mode else (1, 2)
        for attempt in attempts:
            if not queue:
                break
            print(f"Repair pass {attempt}: {len(queue)} entries", flush=True)
            output = WORK / f"pass_{attempt}.jsonl"
            model.run(db, request_rows(queue, catalogue, rows, errors, attempt), output,
                      "http://127.0.0.1:11434/api/generate", "plamo-en:latest",
                      media_sources=sources, progress=plamo_full.show_progress)
            for draft in store.interchange(output):
                key = draft["id"]
                repairs[key] = dict(draft, text=preview.prepare(draft["text"], catalogue[key]))
            _, _, errors = disc.plan(rows, repairs, **validation)
            queue = sorted(errors)
        # A failed rewrite stays visible in the report, even if an old draft fits.
        selected = drafts | repairs | fixes | approved
        if args.choices_and_memo:
            punctuation = str.maketrans({"*": "", "@": " at ", "=": " ", "[": "(",
                                         "]": ")", ";": ",", '"': "'", "_": " "})
            for key, revision in drafts.items():
                if rows[key]["kind"] == "choice" and key not in approved:
                    text = revision["text"].translate(punctuation)
                    text = re.sub(r"^Going to (?:the )?", "", text, flags=re.I)
                    text = re.sub(r"\bTorii Gate\b", "Torii", text, flags=re.I)
                    text = re.sub(r"^Returning to the (.+)$", r"\1", text, flags=re.I)
                    text = re.sub(r"^Leaving the Room$", "Leave Room", text, flags=re.I)
                    text = re.sub(r"^Staying in the Room$", "Stay Here", text, flags=re.I)
                    text = re.sub(r"^Listening to the conversation between (\w+) and (\w+)$",
                                  r"\1 and \2", text, flags=re.I)
                    text = re.sub(r"^Inspecting (\w+)'s Luggage$", r"\1's Bags", text, flags=re.I)
                    text = re.sub(r"^Going to (?:the )?Open[ -]Air Bath$", "Outdoor Bath", text, flags=re.I)
                    text = re.sub(r"^Sending Miyuki Back to Her Room$", "Send Miyuki Back", text, flags=re.I)
                    text = re.sub(r"^Keeping an eye on Mayura$", "Watch Mayura", text, flags=re.I)
                    text = re.sub(r"^Inspect Hazuki's body$", "Check Hazuki", text, flags=re.I)
                    text = re.sub(r"^Open[ -]Air Bath$", "Outdoor Bath", text, flags=re.I)
                    selected[key] = dict(revision, text=" ".join(text.split()))
        punctuated = {}
        for key in selected.keys() - approved.keys():
            if key in required:
                revision = selected[key]
                text = preview.STUTTER.sub(r"\1... ", revision["text"])
                selected[key] = dict(revision, text=text)
                jp = rows[key]["jp"].rstrip("\u300d\u300f \n")
                if text and text[-1].isalnum() and not jp.startswith("\u300a"):
                    mark = {"\u3002": ".", "\uff01": "!", "\uff1f": "?"}.get(jp[-1:])
                    if jp.endswith(("\u2015", "\u2025", "\u2026")):
                        mark = "..."
                    if mark:
                        punctuated[key] = dict(revision, text=text + mark)
        if punctuated:
            _, _, punctuation_errors = disc.plan(rows, punctuated, **validation)
            selected.update({key: revised for key, revised in punctuated.items()
                             if key not in punctuation_errors})
            print(f"Restored {len(punctuated) - len(punctuation_errors)} sentence endings; "
                  f"{len(punctuation_errors)} need layout review", flush=True)
        print("Validating the full dialogue catalogue...", flush=True)
        scripts, edits, errors = disc.plan(rows, selected, **validation)
        unresolved = {key: errors.get(key, "No nonblank English source draft")
                      for key in required if key in errors or key not in selected}
        if args.editor_index or args.editor_patch:
            choices = {key for key, row in rows.items() if row["kind"] == "choice"}
            failures = unresolved | {key: errors.get(key, "Missing choice")
                                     for key in choices if key in errors or key not in selected}
            if failures or errors.keys() & approved.keys():
                for key, reason in sorted((errors | failures).items()):
                    print(f"{key}: {reason}", flush=True)
                return 1
            if args.editor_index:
                args.editor_index.write_text(store.dump({
                    key: selected[key]["text"] for key in required | choices
                }), encoding="utf-8")
            else:
                from editor_project import prepare_graphics
                prepare_graphics(scripts, edits, args.editor_patch, rows)
                print(disc.build_image(scripts, edits, args.name), flush=True)
            return 0
        report = WORK / "unresolved.jsonl"
        store.jsonl(report, (dict(id=key, jp=rows[key]["jp"], reason=reason,
                                  source_sha256=rows[key]["source_sha256"],
                                  draft=selected.get(key, {}).get("text"))
                             for key, reason in sorted(unresolved.items())))
        store.jsonl(WORK / "repairs.jsonl", repairs.values())
        print(f"{len(unresolved)} unresolved dialogue entries. Report: {report}", flush=True)
        if args.next_pass or args.check_only:
            return bool(unresolved)
        if unresolved and (args.build_saved or not args.preview):
            return 1
        if errors.keys() & approved.keys():
            raise ValueError("Approved translations failed insertion checks")
        if args.choices_and_memo:
            choices = {key for key, row in rows.items() if row["kind"] == "choice"}
            failures = {key: errors.get(key, "Missing choice draft") for key in choices
                        if key in errors or key not in selected}
            if failures:
                raise ValueError("\n".join(f"{key}: {error}" for key, error in sorted(failures.items())))
            store.jsonl(Path("build") / f"{args.name}_choice_width_review.jsonl", (
                dict(id=key, jp=rows[key]["jp"], english=selected[key]["text"],
                     cells=len(selected[key]["text"]) + 2)
                for key in sorted(choices) if len(selected[key]["text"]) > 14
            ))
            from memo_english_pages import PATH, memo_edits
            with disc.BASE.open("rb") as base:
                records = disc.inventory()
                scripts[PATH], edits[PATH] = memo_edits(base, records)
                if args.more_artwork:
                    from english_artwork import EXE, card_message_edits, english_artwork, floor_plan_edits
                    edits[PATH].extend(english_artwork(base, records))
                    for variant in (1, 2):
                        path, scripts_data, spans = floor_plan_edits(base, records, variant)
                        scripts[path], edits[path] = scripts_data, spans
                    scripts[EXE], edits[EXE] = card_message_edits(base, records, rows)
                    if args.portrait_names:
                        from english_artwork import final_portrait_edits, island_label_edits, portrait_name_edits
                        for path, data, spans in portrait_name_edits(base, records):
                            scripts[path], edits[path] = data, spans
                        for path, (data, spans) in final_portrait_edits(base, records).items():
                            scripts[path], edits[path] = data, spans
                        path, data, spans = island_label_edits(base, records)
                        scripts[path], edits[path] = data, spans
            print(f"{len(choices)} choices and {len(edits[PATH])} memo atlas pages ready", flush=True)
        print(disc.build_image(scripts, edits, args.name))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
