# CachyOS Benchmark

[![Support on Patreon](https://img.shields.io/badge/Patreon-Support%20Botlabs-F96854?logo=patreon&logoColor=white)](https://www.patreon.com/botlabs)

Repeatable ~10-minute CPU, memory, storage, and graphics baseline for CachyOS. Bash workloads write `results.csv` and per-run logs; a PySide6 GUI compares runs, charts scores, and shows a noise-aware “what changed” report (including pacman.log package deltas).

## Features

- Shell suite: 7-Zip, sysbench (CPU/memory), zstd, x264 via ffmpeg, fio, vkmark, glmark2, cyclictest
- GUI: Run / History / Compare / What changed; live log; cancel
- Sidecar `runs/<timestamp>.changes.json` (CSV schema stays stable)
- Noise-aware better / worse / within-noise flags
- Hot-start CPU/GPU temperature warning and optional `--cooldown`
- AppImage packaging (bundled Python + PySide6 + matplotlib)

## Requirements

- Linux x86_64
- Tested on CachyOS with KDE Plasma 6 (Wayland)

### Test-tool dependencies

Host tools are not bundled in the AppImage. On CachyOS/Arch:

```
sudo pacman -S --needed sysbench 7zip zstd ffmpeg fio glmark2 vkmark rt-tests lm_sensors
```

Optional extras used by some workflows: `stress-ng`, `hyperfine`.

## Install / run

```
chmod +x cachy-bench.sh compare.sh app/cachy-bench-gui.py
./cachy-bench.sh --baseline          # first clean run
./cachy-bench.sh --tag after-update  # later runs
./compare.sh                         # baseline vs latest
./app/cachy-bench-gui.py             # GUI
```

Useful flags and modes:

| Flag / mode | Purpose |
|---|---|
| `--check-deps` | GUI/CLI: list missing host tools + pacman install line |
| `--selftest` | GUI: quick metrics / what-changed sanity check |
| `--what-changed` | GUI/CLI: print what-changed report |
| `--version` | Print app version |
| `--run` | AppImage: skip GUI and run `cachy-bench.sh` |
| `--only LIST` | Comma-separated subset of workloads |
| `--tag NAME` | Label stored in CSV |
| `--baseline` | Mark this run as the comparison baseline |
| `--smoke` | Short dry run for script validation |
| `--cooldown` | Wait (up to 5 min) if start temps are hot |

Data directory (results, runs, baseline tag):

- Default: `~/bench` (`$HOME/bench`)
- Override: `CACHY_BENCH_DATA=/path/to/data`

Optional share/app override: `CACHY_BENCH_APP` (directory that holds `cachy-bench.sh` and `app/`).

P-core pinning for single-thread 7-Zip uses `lscpu` auto-detect. Override with `CACHY_BENCH_PCORES` (a `taskset -c` CPU list). If detection fails, the default is `0-15`.

What-changed baseline: reads `$CACHY_BENCH_DATA/baseline.tag` (written by `--baseline`), or override with `CACHY_BENCH_BASELINE_TS`.

## Build AppImage

From the repository root, with a Python 3.13 venv containing PySide6 and matplotlib at `build/venv` (or point `CACHY_BENCH_VENV` at an existing venv):

```
./build_appimage.sh
```

Outputs under `dist/`:

- `CachyBench-<version>-x86_64.AppImage`
- `CachyBench-x86_64.AppImage` (same file)

`slim_appdir.py` drops unused Qt Quick/QML bits before packing.

## License

MIT License. Copyright (c) 2026 Botlabs USA. See [LICENSE](LICENSE).

## Contact

Support: [support@botlabsusa.com](mailto:support@botlabsusa.com)

## Support Botlabs

Botlabs apps are free. If they help you, consider supporting development on [Patreon](https://www.patreon.com/botlabs) for early builds, feature votes and your name in the app credits.
