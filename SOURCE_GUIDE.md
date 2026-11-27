# Source guide

The BPS game patch is independent of the optional Workbench. See PATCHING_GUIDE.md.

- `tools/build_bps_patch.py`: standard BPS1 creation and replay verification.
- `tools/translation_editor.py` / `.html`: Workbench interface.
- `tools/setup_public_workbench.py`: reconstruct local baselines/catalogue from a user's clean disc.
- `tools/translation_source.py`: original source identification and conservative comment parsing.
- `tools/translation_build.py`, `script_allocator.py`, `patch_bin.py`: build planning and disc changes.
- `tools/wrap.py`, `choice_layout.py`: measured dialogue and choice layouts.
- `tools/clean_disc_patch.py`: clean-disc deltas used by optional Workbench setup.

The optional Workbench ZIP includes its runtime, source modules, English baseline,
measurements, file metadata and setup deltas. It creates its local source database
from the user's own disc. No private source database or extracted game assets are shipped.
The repository tools are a development reference; disc images remain local.
