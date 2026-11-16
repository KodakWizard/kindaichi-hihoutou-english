# Source guide

- `tools/clean_disc_patch.py` contains the standalone delta builder and applicator. Each distributed PYZ contains its own applicator and patch data.
- `tools/translation_editor.py` and `tools/translation_editor.html` are the workbench interface. The supporting modules cover the database, build planner, layout and graphics.
- `tools/patch_bin.py` and `tools/script_allocator.py` handle sector edits and script placement.

The tools are published under the MIT licence. The workbench source is a development reference and expects additional local project modules, data, disc baselines and measurements. The alpha patch can be applied independently using the installation guide. Private workbench archives, extracted scripts, disc images, emulator binaries and BIOS files are kept in the development workspace.
