# Kindaichi English v0.2.0-beta.1 — game patch

You only need this download, your original game and a BPS patcher.
The optional Workbench is a separate download for translation editing.

## Apply the patch

1. Extract this ZIP.
2. Open Floating IPS: https://github.com/Sir-Walrus/Flips/releases
3. Choose Apply Patch and select `Kindaichi_Hihoutou_English_v0.2.0-beta.1.bps`.
4. Select your clean Japanese **BIN**, not its CUE.
5. Save the result as `Kindaichi_Hihoutou_English_v0.2.0-beta.1.bin`.
6. Keep that BIN beside the included `Kindaichi_Hihoutou_English_v0.2.0-beta.1.cue` and load the CUE to play.

If you choose another output name, change the FILE line inside the CUE to match.
Keep your original BIN/CUE unchanged. Apply this beta to the original disc,
not to an earlier translation patch. No Python or Workbench is needed to play.

## Supported original

- Japanese PlayStation release SLPS-00601, single-track MODE2/2352 BIN/CUE.
- Original BIN size: 682,458,672 bytes.
- Original BIN SHA-1: `65f582ffe78b5fa55e45d0c163ba2e67a5b15e15`.
- Patched BIN size: 683,994,528 bytes.
- Patched BIN SHA-256: `3f99713f312490f831329eb8e2a2f48b3a29942c869157e84f59a6ebe5454e13`.

Floating IPS v198 independently applied this BPS and reproduced that output hash.

## Beta status

Static checks cover 6,858 dialogue passages, 790 choices and 39 story scripts.
The beta combines the current renderer fixes, recovered playable text, maps,
captions, evidence graphics, corrected notebook notes, torii verses and save/load labels.
All 17 new approved artwork instances match their previews in disc read-back.

Full start-to-finish route/ending and original-hardware testing are not complete.
Long choice menus, new notebook/torii panel crops and save/load alignment need gameplay checks.
Opening/title artwork and credits intentionally remain original. Movies remain unchanged;
some Japanese inserts in them are not translated. Original puzzle symbols are preserved.

Report problems with a screenshot, scene, selected choice and beta version:
https://github.com/KodakWizard/kindaichi-hihoutou-english/issues
No game image, BIOS or emulator is included.
