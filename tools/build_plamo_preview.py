"""Compile a local preview disc from PLaMo drafts without approving them in SQLite."""
import argparse
import re
import unicodedata
from contextlib import closing
from pathlib import Path

import plamo_full
import translation_build as disc
import translation_db as store


# Source-linked preview edits for lines that need shorter copy.
PREVIEW_REVISIONS = {
    "/OUT/SANBA.TXT@00001404:dialogue": "Arrival at Hibo Island\nOct 14, 1:30 PM",
    "/OUT/SANBA.TXT@000018a3:dialogue": "But Mimasaka died in the last case Hajime solved, remember?",
    "/OUT/SANBA.TXT@000019b9:dialogue": "There was a string of murders here. Why on earth buy the island back?",
    "/OUT/YAMA.TXT@0000014b:dialogue": "Yes. This yamawaro isn't the only odd thing left on the island.",
    "/OUT/YAMA.TXT@0000106d:dialogue": "Barely any work. Three meals, a nap, and I laze around drinking all day.",
    "/OUT/GENKAN.TXT@00000504:dialogue": "Huh, they're gone. Weren't they going to show us around the mansion?",
    "/OUT/GENKAN.TXT@00002c83:dialogue": "The culprit wrote that letter? How cruel to make his own daughter watch.",
    "/OUT/SANBA.TXT@000029f2:dialogue": "Making a fool of me? If you think you know it all, you're dead wrong!",
    "/OUT/SANBA.TXT@00003595:dialogue": "I'm counting on you, Hajime. I won't get my hopes up, though.",
    "/IN/OUBEYA.TXT@00004ea7:dialogue": "This was once known as Secret Treasure Island.",
    "/IN/SALON.TXT@000023c1:dialogue": "Now you're an accomplice too, Hajime. Heh.",
    "/OUT/IRIE.TXT@00000826:dialogue": "If it's yours, I'll give it back. I'll go with Eboshi, nyao.",
}

STUTTER = re.compile(r"\b([a-z]{1,3})\s*[-\u2010-\u2014]+\s*(?=\1[a-z]*\b)", re.I)


def prepare(text, source):
    text = unicodedata.normalize("NFKC", text).strip()
    text = text.removeprefix('"').removesuffix('"')
    if source["kind"] == "dialogue":
        title = source["jp"].lstrip().startswith("\u300a")
        if title:
            text = text.replace("\u300a", "").replace("\u300b", "")
            return "\n".join(prepare(line, {"kind": "title"}) for line in text.splitlines()
                             if line.strip())
        text = text.replace("\u300c", "").replace("\u300d", "")
        if re.fullmatch(r"[-\u2010-\u2014]+", text.strip()):
            return "..."
        text = re.sub(r"(?i)(?<=[a-z])-(?:san|han|sama|chan|kun|sensei)\b", "", text)
        text = re.sub(r"\s*[-\u2013\u2014]+\s*(?=[?!.,]|$)", "", text)
        text = re.sub(r"(?:^|(?<=\n))\s*[-\u2013\u2014]+\s*", "", text)
        # Keep repeated syllables attached using the renderer's short hyphen.
        segments = []
        start = 0
        for match in STUTTER.finditer(text):
            segments.append(clean_dashes(text[start:match.start()]))
            segments.append(match[1] + "-")
            start = match.end()
        segments.append(clean_dashes(text[start:]))
        text = "".join(segments)
    text = text.translate(str.maketrans({
        '"': "'", "\u2018": "'", "\u2019": "'", "\u201c": "'", "\u201d": "'",
        "\u2013": "-", "\u2014": "-", "\u2025": "..", "\u014d": "o",
        "\u014c": "O", "\u016b": "u", "\u016a": "U", "\u00e9": "e",
    }))
    return " ".join(text.split())


def clean_dashes(text):
    text = re.sub(r"\s*[\u2013\u2014]+\s*|\s+-\s+", ", ", text)
    return re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", text)


def load_drafts(catalogue):
    drafts, omitted = {}, {}
    seen = set()
    for draft in store.interchange(plamo_full.DIRECTORY / "all_english_drafts.jsonl"):
        key = draft["id"]
        source = catalogue.get(key)
        if (key in seen or source is None or source["source_sha256"] != draft["source_sha256"]
                or source["kind"] != draft["kind"]):
            raise ValueError(f"Duplicate or stale draft: {key}")
        seen.add(key)
        if source["kind"] not in ("dialogue", "choice"):
            omitted[key] = "No preview insertion path for " + source["kind"]
            continue
        revised = PREVIEW_REVISIONS.get(key)
        drafts[key] = dict(draft, text=revised if revised is not None else
                           prepare(draft["text"], source))
    if seen != catalogue.keys():
        raise ValueError("Draft file does not cover the full catalogue")
    return drafts, omitted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    args = parser.parse_args()
    report = Path("build") / f"translation_{args.name}" / "draft_report.jsonl"
    with closing(store.open_db()) as db:
        catalogue = plamo_full.catalogue(db)
        rows = store.source_rows(db)
        drafts, omitted = load_drafts(catalogue)
        approved = store.revisions(db, "approved")
        selected = drafts | approved
        scripts, edits, errors = disc.plan(rows, selected, fill_lines=True)
        if errors.keys() & approved.keys():
            raise ValueError("Existing approved revisions failed preview validation")
        omitted.update(errors)
        included = selected.keys() - errors.keys()
        cue = disc.build_image(scripts, edits, args.name)
        store.jsonl(report, (
            dict(id=key, kind=catalogue[key]["kind"], reason=reason)
            for key, reason in sorted(omitted.items())
        ))
        for kind in ("dialogue", "choice"):
            print(f"{kind}: {sum(rows[key]['kind'] == kind for key in included)} inserted")
        print(f"{cue} | {len(omitted)} entries listed in {report}")


if __name__ == "__main__":
    main()
