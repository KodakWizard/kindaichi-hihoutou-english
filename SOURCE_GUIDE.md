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

## Matching Workbench releases

Workbench 1.0 reconstructs the exact game release from the user's original disc. Its packaged runtime can rebuild the release unchanged, or build story/graphics edits while retaining all 14 subtitled movies.

- `tools/movie_baseline.py` validates and preserves the release's raw movie extents during a build. Other image bytes must remain unchanged by this step.
- `tools/package_release_workbench.py` packages a version-matched baseline, tools, runtime and reviewed English SRTs, without original game assets.
- `tools/verify_release_workbench.py` tests a fresh setup, exact release rebuild and edited-dialogue rebuild with movie preservation.
- `subtitles/` contains the 14 reviewed English SRTs and native timing-origin notes. Editing these reference files alone does not burn new captions into the movies.

Every game release must have a freshly built Workbench with the same version and matching release image. Keep the standard BPS game patch and optional Workbench as separate downloads. Verify both before publishing.
