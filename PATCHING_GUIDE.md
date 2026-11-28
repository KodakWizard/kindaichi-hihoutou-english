# Installing Kindaichi 1.0

You need your original Japanese game and the patch ZIP. The Workbench is a separate download for editing the translation; it isn't needed to play.

## Before you start

Use the Japanese PlayStation release **SLPS-00601** in BIN/CUE format. Keep your original files safe and apply this patch to a clean BIN, rather than to an earlier English patch.

## Apply the patch

1. Extract **Kindaichi_Hihoutou_English_v1.0-Patch.zip**.
2. Open [Floating IPS](https://github.com/Sir-Walrus/Flips/releases) and choose **Apply Patch**.
3. Select **Kindaichi_Hihoutou_English_v1.0.bps**, then select your original Japanese **BIN** file.
4. Save the new file as **Kindaichi_Hihoutou_English_v1.0.bin**.
5. Put it beside **Kindaichi_Hihoutou_English_v1.0.cue**, which is included in the ZIP.
6. Open the CUE in your PlayStation emulator to play.

If you give the patched BIN a different name, edit the `FILE` line in the CUE to match it. Both files need to stay together.

## Something didn't work?

If the patcher says the original file doesn't match, check that you selected the clean Japanese BIN for SLPS-00601. The CUE is the small text file used to load the finished game; apply the patch to the BIN.

If you're still stuck, [open an issue](https://github.com/KodakWizard/kindaichi-hihoutou-english/issues) and include the message you saw. Please don't upload your game image.

<details>
<summary>File details and checksums</summary>

Original BIN: 682,458,672 bytes, SHA-1 `65f582ffe78b5fa55e45d0c163ba2e67a5b15e15`.

Patched BIN: 683,994,528 bytes, SHA-256 `0b763ca624413a43c5c399a2f06ab60015a5d6a9f05b13f4f5c263a763600fdf`.

The BPS has been applied and checked with Floating IPS v198.

</details>
