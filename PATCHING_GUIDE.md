# Installation guide

Use an unmodified Japanese BIN/CUE of **SLPS-00601**. Keep the original BIN and its CUE together. The supported clean BIN is 682,458,672 bytes and has SHA-1 `65f582ffe78b5fa55e45d0c163ba2e67a5b15e15`.

## Windows

Install Python 3.11 or later. In PowerShell, open the folder containing the patch and run this command, replacing the original CUE path with yours.

```powershell
py -3 ".\Kindaichi_Hihoutou_English_v0.1.0-alpha.pyz" "D:\Games\Original.cue" ".\Kindaichi-English.bin"
```

On macOS or Linux, use `python3` with Python 3.11 or later and your own paths. The destination folder must already exist. The patcher checks both the input and the finished image. It creates a fresh BIN and CUE alongside one another and refuses existing output filenames.

Start the game by opening the English CUE. Use a fresh boot for testing. The earlier renderer and artwork checks were done piecemeal. Full route coverage and original hardware testing are still pending.
