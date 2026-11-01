#!/usr/bin/env bash
# compare.sh — print baseline vs latest run with % change
# Usage: ~/bench/compare.sh [baseline_timestamp]
set -uo pipefail

BENCH_ROOT="${CACHY_BENCH_DATA:-${HOME}/bench}"
CSV="${BENCH_ROOT}/results.csv"
BASELINE_FILE="${BENCH_ROOT}/baseline.tag"
APP_ROOT="${CACHY_BENCH_APP:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

if [[ ! -f "$CSV" ]]; then
  echo "No results yet. Run cachy-bench.sh first."
  exit 1
fi

# Higher-is-better metrics (comma-separated column names matching CSV header)
HIB="7z_st 7z_mt sysbench_cpu_st_eps sysbench_cpu_mt_eps sysbench_mem_MiBps zstd_MBps zstd2_comp_MBps zstd2_decomp_MBps x264_fps x264v2_fps fio_seq_read_MiBps fio_seq_write_MiBps fio_rand_read_IOPS fio_rand_write_IOPS vkmark glmark2"
# Lower-is-better
LIB="cyclictest_max_us cpu_temp_max_C gpu_temp_max_C wall_seconds"

# Parse CSV with python for robustness (quoted fields)
python3 - "$CSV" "${1:-}" "$BASELINE_FILE" << 'PY'
import csv, sys, os
app_root = os.environ.get("CACHY_BENCH_APP") or os.environ.get("CACHY_BENCH_DATA") or os.path.expanduser("~/bench")
sys.path.insert(0, os.path.join(app_root, "app"))
from metrics import METRICS, HIB, LIB, parse_num as num, pct_change as pct, compare_note

csv_path = sys.argv[1]
forced_baseline = sys.argv[2] if len(sys.argv) > 2 else ""
baseline_file = sys.argv[3] if len(sys.argv) > 3 else ""

with open(csv_path, newline="") as f:
    rows = list(csv.DictReader(f))

if not rows:
    print("CSV is empty.")
    sys.exit(1)

# Filter out pure smoke runs for "latest" unless that's all we have
non_smoke = [r for r in rows if "smoke" not in (r.get("tag") or "")]
latest_pool = non_smoke if non_smoke else rows
latest = latest_pool[-1]

# Baseline selection
baseline = None
if forced_baseline:
    for r in rows:
        if r.get("timestamp") == forced_baseline:
            baseline = r
            break
    if baseline is None:
        print(f"No run with timestamp {forced_baseline}")
        sys.exit(1)
else:
    # Prefer explicit baseline.tag
    ts = ""
    if os.path.isfile(baseline_file):
        ts = open(baseline_file).read().strip()
    if ts:
        for r in rows:
            if r.get("timestamp") == ts:
                baseline = r
                break
    if baseline is None:
        # Prefer a row tagged baseline
        for r in rows:
            if "baseline" in (r.get("tag") or "") and "smoke" not in (r.get("tag") or ""):
                baseline = r
                break
    if baseline is None:
        # First non-smoke, else first
        baseline = (non_smoke[0] if non_smoke else rows[0])


print("=" * 78)
print("CachyOS bench compare")
print("=" * 78)
print(f"Baseline : {baseline.get('timestamp')}  tag={baseline.get('tag') or '-'}  kernel={baseline.get('kernel')}")
print(f"           nvidia={baseline.get('nvidia_driver')}  plasma={baseline.get('plasma')}")
print(f"Latest   : {latest.get('timestamp')}  tag={latest.get('tag') or '-'}  kernel={latest.get('kernel')}")
print(f"           nvidia={latest.get('nvidia_driver')}  plasma={latest.get('plasma')}")
if baseline.get("timestamp") == latest.get("timestamp"):
    print("\n(Only one comparable run — deltas will be 0. Run again after an update.)")
print()
print(f"{'Metric':<40} {'Baseline':>12} {'Latest':>12} {'Change':>10}  Note")
print("-" * 78)

for key, label in METRICS:
    b = num(baseline.get(key))
    l = num(latest.get(key))
    bs = f"{b:.2f}" if b is not None else "—"
    ls = f"{l:.2f}" if l is not None else "—"
    ch, note = compare_note(key, b, l)
    print(f"{label:<40} {bs:>12} {ls:>12} {ch:>10}  {note}")

print("-" * 78)
print(f"Logs: {baseline.get('log_file')}")
print(f"      {latest.get('log_file')}")
print(f"CSV : {csv_path}")
print()
print("Tip: mark a clean run as baseline with:  cachy-bench.sh --baseline")
print("     or:  echo TIMESTAMP > $CACHY_BENCH_DATA/baseline.tag  (default ~/bench)")
PY
