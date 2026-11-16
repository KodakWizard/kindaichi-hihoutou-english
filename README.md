<p align="center"><strong>金田一少年の事件簿</strong><br>悲報島 新たなる惨劇</p>

<h1 align="center">Kindaichi: Hihou-tou - English Fan Translation</h1>

<p align="center">Bringing the Japan-only PlayStation mystery into English.</p>

<p align="center"><strong>v0.1.0-alpha</strong> · Sony PlayStation · Japanese release (SLPS-00601)</p>

<p align="center">
  <a href="patches/Kindaichi_Hihoutou_English_v0.1.0-alpha.pyz?raw=true"><strong>Download the English Patch</strong></a>
  &nbsp; · &nbsp;
  <a href="PATCHING_GUIDE.md">Installation Guide</a>
  &nbsp; · &nbsp;
  <a href="https://github.com/KodakWizard/kindaichi-hihoutou-english/issues">Report a Problem</a>
</p>

---

I've been working on an English patch for **Kindaichi Shounen no Jikenbo - Hihou-tou Arata naru Sangeki**, the Japan-only PlayStation mystery.

The aim is to make the story and investigation playable in English while keeping the feel of the original. I've spent a lot of time getting the dialogue to fit, translating character captions and case notes, and cleaning up details like the save and load screens.

**This is the first public alpha.** The identified story dialogue and choices have been translated. Some interface text and artwork still need work, and the game needs more testing across different routes.

## Getting started

You'll need your own clean Japanese copy of **SLPS-00601** in BIN/CUE format and **Python 3.11 or later**.

1. Download the [English alpha patch](patches/Kindaichi_Hihoutou_English_v0.1.0-alpha.pyz?raw=true).
2. Follow the [installation guide](PATCHING_GUIDE.md) to create a new English BIN/CUE.
3. Open the new CUE in your PlayStation emulator.

## Translation progress

| Area | Status |
| --- | --- |
| Story dialogue | 6,852 identified entries in English |
| Choices | 778 identified entries in English |
| Character captions and case notes | Selected artwork translated |
| Save and load screens | English labels and revised typography |
| Playthroughs and original hardware | Further testing needed |

## Found something that needs fixing?

If you spot some strange wording, text that gets cut off, or a graphic that looks wrong, [open an issue](https://github.com/KodakWizard/kindaichi-hihoutou-english/issues). A screenshot and a quick note about where it happened would be really helpful. Keep disc images and save files private.

## Project files and technical details

<details>
<summary><strong>Source files and translation tools</strong></summary>

The [source guide](SOURCE_GUIDE.md) explains the patcher and the development tools included here.

</details>

## A note on AI

I used PLaMo and ChatGPT for translation drafts, coding and troubleshooting. Getting the text to fit and the graphics to look right took a lot of back and forth, with manual checks and playable test builds along the way.

## Credits and licence

This is an unofficial fan translation. The original game, characters, artwork and music belong to their respective owners. The project tools are available under the [MIT licence](tools/LICENSE). The translation is distributed as an alpha patch and requires a matching original disc.
