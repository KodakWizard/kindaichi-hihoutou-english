# Kindaichi English v0.2.0-beta.1

One combined English beta for the Japanese PlayStation release SLPS-00601.

## Play the game

Download **Kindaichi_Hihoutou_English_v0.2.0-beta.1-Patch.zip**. It contains the standard BPS patch, matching CUE,
checksums and instructions. Apply the BPS to your original Japanese BIN with
[Floating IPS](https://github.com/Sir-Walrus/Flips/releases). No Workbench or Python is required.
The raw BPS and CUE are also attached separately.

## Optional translation Workbench

**Kindaichi-English-Workbench-v0.2.0-beta.1.zip** is a separate Windows x64 download for contributors.
Extract it to a short folder, open KindaichiEditor.exe and browse to your own
original BIN/CUE. It includes the current English text and graphics and can build edits.
It is not required to apply the game patch.

## Included in this beta

- 6,858 identified dialogue passages and 790 choices, including 18 recovered playable records.
- Existing proportional dialogue/choice and punctuation fixes; confirmed renderer retained.
- Current map and character-caption work.
- Approved redesigned licence, marriage forms, Written Pledge and investigation timetable,
  including duplicate copies on the disc.
- All six corrected Day 1–3 notebook pages in the approved uniform style.
- Bilingual torii verses with original Japanese/cipher graphics preserved and quoted wording aligned.
- Uniform 10px navigation/save/load lettering, including digits and date symbols.

## Validation and open testing

Static verification passed for all 39 story scripts and 63 intended payload files.
All 17 approved artwork instances match the final disc; source IDs, branches,
protected 二七 clues, original title/credits and movies are preserved.
Floating IPS v198 independently reproduced the exact beta BIN hash from the BPS.
Optional Workbench setup reconstructs that same beta from the original disc.

This is a prerelease: full route/ending and original-hardware tests remain open.
111 long choice labels need visual wrap checks; one SYOKU_3 menu totals 205 glyph
records against an inferred 192-record budget and needs gameplay review.
New notebook/torii panel visibility and save/load alignment also need manual testing.
Opening/title art and credits intentionally remain original. Unchanged movies include
Japanese text inserts that are outside this completed graphics batch.

Supported original BIN SHA-1: `65f582ffe78b5fa55e45d0c163ba2e67a5b15e15` (682,458,672 bytes).
Patched BIN SHA-256: `3f99713f312490f831329eb8e2a2f48b3a29942c869157e84f59a6ebe5454e13` (683,994,528 bytes).
No game images, extracted original assets, emulator or BIOS are distributed.
