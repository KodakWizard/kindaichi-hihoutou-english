# Installation guide

Use an unmodified Japanese BIN/CUE of **SLPS-00601**. Keep the original BIN and its CUE together. The supported clean BIN is 682,458,672 bytes and has SHA-1 `65f582ffe78b5fa55e45d0c163ba2e67a5b15e15`.

## Apply the BPS patch

1. Open a BPS patcher such as Floating IPS and choose **Apply Patch**.
2. Select `Kindaichi_Hihoutou_English_v0.1.0-alpha.bps` and then the original Japanese **BIN**.
3. Save the patched file as `Kindaichi-English.bin`.
4. Copy the original CUE to `Kindaichi-English.cue`. Open the copy in a text editor and change only the filename on its `FILE` line.

```cue
FILE "Kindaichi-English.bin" BINARY
  TRACK 01 MODE2/2352
    INDEX 01 00:00:00
```

Keep the patched BIN and its CUE in the same folder. Open the English CUE for a fresh boot.

The finished BIN SHA-256 is `162b6dd0a105ecfbe4d75f2f5c1da8bac1a0521a25291b35f915c4f8506be4b8`. Full route coverage and original hardware testing are still pending.
