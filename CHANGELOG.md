# Changelog

## 1.0.4 — 2026-11-13

- New app icon: the brass and copper cogs from the header bar, on a dark plate with a thin brass rim. Used for the window, the AppImage, the menu/desktop launcher and the project page (SVG plus PNGs from 16 to 512 px, with simplified cogs at 16–32 px so they stay readable)
- Icon sources and generator live in `assets/icon/` (favicon, apple-touch icon and a GitHub social preview too)
- Release files: the gauges close-up screenshot no longer has a thin black bar on its right edge, and `SHA256SUMS` lists only the version being released

## 1.0.3 — 2026-11-13

- New dark steampunk theme: charcoal and gunmetal panels with riveted brass frames, brass tabs and buttons, a copper Run button, a header bar with cogs drawn in code, and themed tables, checkboxes, scrollbars, menus and dialogs
- Live temperatures shown as analog CPU/GPU gauges: brass bezel, green/amber/red bands at 70/85 °C, needle, peak marker and a digital readout; the 5-minute trace chart is themed to match
- Compare chart is now a horizontal bar chart with % labels; all charts use a dark theme with desaturated green (faster) and red (slower)
- Run tab shows a small logbook summary (runs logged, baseline, latest run)
- The window opens maximized
- No new files or fonts are bundled (serif titles use installed fonts with fallbacks). Features and the `results.csv` columns are unchanged

## 1.0.2 — 2026-11-13

- History: checkbox column, Select all / none, Delete selected (button, right-click menu or Delete key) with a confirm dialog and an extra warning for the baseline run
- Deletes are reversible: `results.csv` is backed up to `results.csv.bak-<timestamp>`, run logs/sidecars move to `runs/trash/`, and **Undo last delete** puts them back
- Live CPU (lm_sensors) and GPU (nvidia-smi) temperatures on the Run tab: current value, peak, green/amber/red, 5-minute chart; saved to `runs/<timestamp>.temps.csv` during runs
- New CLI: `--delete-runs TS[,TS...]`, `--undo-delete`, `--temps-once`; `--selftest` covers delete/undo and temperature parsing

## 1.0.1 — 2026-11-01

- Added support contact (`support@botlabsusa.com`) in README and About

## 1.0.0 — 2026-11-01

First AppImage release of CachyOS Benchmark.

- Bundled AppImage (Python + PySide6 + matplotlib) with dependency check
- **What changed** view / CLI with pacman-log package change tracking
- Noise-aware regression flags (better / worse / within noise)
- Hot-start CPU/GPU temperature warning (optional cooldown)
- Run sidecars (`runs/<ts>.changes.json`) without changing the CSV schema
- MIT license
