# Changelog

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
