#!/usr/bin/env bash
# cachy-bench.sh — repeatable ~10-minute baseline for CachyOS
# Usage: ~/bench/cachy-bench.sh [--tag NAME] [--smoke] [--baseline] [--only LIST] [--update-row TS] [--cooldown]
#   --tag NAME     label this run (stored in CSV)
#   --smoke        short dry run (tiny workloads) for script validation
#   --baseline     mark this run as the comparison baseline
#   --only LIST    comma-separated subset: 7z,sysbench,memory,zstd,zstd2,x264,x264v2,fio,vkmark,glmark2,cyclictest
#   --update-row TS  patch metrics into an existing CSV row (for backfill); appends notes to that run's log
#   --cooldown      if CPU/GPU start >50C, wait (up to 5 min) for cool-down before workloads
#
# 7-Zip score: last number on the Tot: line = overall Rating in MIPS
#   (average of compress+decompress Ratings). 7-Zip 26.x Tot: is
#   "Tot: <Usage%> <R/U MIPS> <Rating MIPS>" — we keep Rating MIPS.
set -uo pipefail

# Data lives outside any AppImage/install tree. Override with CACHY_BENCH_DATA.
# CACHY_BENCH_APP (optional) points at the share dir that holds this script + app/.
BENCH_ROOT="${CACHY_BENCH_DATA:-${HOME}/bench}"
RUNS_DIR="${BENCH_ROOT}/runs"
TMP_DIR="${BENCH_ROOT}/tmp"
CSV="${BENCH_ROOT}/results.csv"
BASELINE_FILE="${BENCH_ROOT}/baseline.tag"
APP_ROOT="${CACHY_BENCH_APP:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

mkdir -p "${RUNS_DIR}" "${TMP_DIR}"

TAG=""
SMOKE=0
MARK_BASELINE=0
ONLY=""          # empty = all sections
UPDATE_ROW=""   # if set, patch this timestamp's CSV row instead of appending
COOLDOWN=0
prev=""
for arg in "$@"; do
  case "$arg" in
    --smoke) SMOKE=1 ;;
    --baseline) MARK_BASELINE=1 ;;
    --cooldown) COOLDOWN=1 ;;
    --tag) ;;
    --tag=*) TAG="${arg#--tag=}" ;;
    --only) ;;
    --only=*) ONLY="${arg#--only=}" ;;
    --update-row) ;;
    --update-row=*) UPDATE_ROW="${arg#--update-row=}" ;;
    *)
      if [[ "${prev:-}" == "--tag" ]]; then TAG="$arg"
      elif [[ "${prev:-}" == "--only" ]]; then ONLY="$arg"
      elif [[ "${prev:-}" == "--update-row" ]]; then UPDATE_ROW="$arg"
      fi
      ;;
  esac
  prev="$arg"
done

# Normalize ONLY list to lowercase comma-separated tokens
ONLY_NORM="$(printf '%s' "$ONLY" | tr '[:upper:]' '[:lower:]' | tr -d ' ')"
want() {
  # want SECTION — true if --only empty or SECTION is listed
  local s="$1"
  [[ -z "$ONLY_NORM" ]] && return 0
  [[ ",${ONLY_NORM}," == *",${s},"* ]] && return 0
  return 1
}

if [[ -n "$UPDATE_ROW" ]]; then
  TS="$UPDATE_ROW"
  # Prefer the log path recorded in CSV; fall back to runs/${TS}.log
  LOG="$(python3 - "$CSV" "$TS" <<'PY'
import csv, sys
csv_path, ts = sys.argv[1], sys.argv[2]
try:
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("timestamp") == ts:
                print(r.get("log_file") or "")
                break
except FileNotFoundError:
    pass
PY
)"
  if [[ -z "$LOG" || ! -f "$LOG" ]]; then
    LOG="${RUNS_DIR}/${TS}.log"
  fi
  if [[ ! -f "$LOG" ]]; then
    echo "ERROR: --update-row ${TS}: log not found at $LOG" >&2
    exit 1
  fi
else
  TS="$(date +%Y%m%d-%H%M%S)"
  LOG="${RUNS_DIR}/${TS}.log"
  [[ -n "$TAG" ]] && LOG="${RUNS_DIR}/${TS}-${TAG}.log"
fi

# ---- helpers ----
have() { command -v "$1" >/dev/null 2>&1; }

log() {
  # tee to log and stdout
  printf '%s\n' "$*" | tee -a "$LOG"
}

section() {
  log ""
  log "======== $* ========"
}

csv_escape() {
  local s="${1:-}"
  s="${s//\"/\"\"}"
  printf '"%s"' "$s"
}

# Sample sensors / nvidia into globals (best-effort)
CPU_TEMP_MAX=""
GPU_TEMP_MAX=""
GPU_CLOCK_MAX=""

sample_temps() {
  local ct gt gc
  ct=""
  if have sensors; then
    # Prefer Package id 0, fall back to Tctl / highest
    ct="$(sensors 2>/dev/null | awk '
      /Package id 0:/ {gsub(/\+|°C/,"",$4); print $4; exit}
      /Tctl:/ {gsub(/\+|°C/,"",$2); print $2; exit}
    ')"
  fi
  gt=""; gc=""
  if have nvidia-smi; then
    IFS=',' read -r gt gc < <(nvidia-smi --query-gpu=temperature.gpu,clocks.current.graphics --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  fi
  # update maxima (numeric compare)
  if [[ -n "$ct" ]]; then
    if [[ -z "$CPU_TEMP_MAX" ]] || awk -v a="$ct" -v b="$CPU_TEMP_MAX" 'BEGIN{exit !(a+0>b+0)}'; then
      CPU_TEMP_MAX="$ct"
    fi
  fi
  if [[ -n "$gt" ]]; then
    if [[ -z "$GPU_TEMP_MAX" ]] || awk -v a="$gt" -v b="$GPU_TEMP_MAX" 'BEGIN{exit !(a+0>b+0)}'; then
      GPU_TEMP_MAX="$gt"
    fi
  fi
  if [[ -n "$gc" ]]; then
    if [[ -z "$GPU_CLOCK_MAX" ]] || awk -v a="$gc" -v b="$GPU_CLOCK_MAX" 'BEGIN{exit !(a+0>b+0)}'; then
      GPU_CLOCK_MAX="$gc"
    fi
  fi
}

# Background sampler while a long test runs
SAMPLER_PID=""
start_sampler() {
  sample_temps
  (
    while true; do
      sleep 2
      # write samples into a side file so parent can merge maxima later
      ct=""; gt=""; gc=""
      if command -v sensors >/dev/null 2>&1; then
        ct="$(sensors 2>/dev/null | awk '/Package id 0:/{gsub(/\+|°C/,"",$4);print $4;exit} /Tctl:/{gsub(/\+|°C/,"",$2);print $2;exit}')"
      fi
      if command -v nvidia-smi >/dev/null 2>&1; then
        IFS=',' read -r gt gc < <(nvidia-smi --query-gpu=temperature.gpu,clocks.current.graphics --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
      fi
      printf '%s %s %s\n' "${ct:-}" "${gt:-}" "${gc:-}" >> "${TMP_DIR}/.samples.$$"
    done
  ) &
  SAMPLER_PID=$!
}

stop_sampler() {
  if [[ -n "${SAMPLER_PID:-}" ]]; then
    kill "$SAMPLER_PID" 2>/dev/null || true
    wait "$SAMPLER_PID" 2>/dev/null || true
    SAMPLER_PID=""
  fi
  if [[ -f "${TMP_DIR}/.samples.$$" ]]; then
    while read -r ct gt gc; do
      if [[ -n "$ct" ]]; then
        if [[ -z "$CPU_TEMP_MAX" ]] || awk -v a="$ct" -v b="$CPU_TEMP_MAX" 'BEGIN{exit !(a+0>b+0)}'; then CPU_TEMP_MAX="$ct"; fi
      fi
      if [[ -n "$gt" ]]; then
        if [[ -z "$GPU_TEMP_MAX" ]] || awk -v a="$gt" -v b="$GPU_TEMP_MAX" 'BEGIN{exit !(a+0>b+0)}'; then GPU_TEMP_MAX="$gt"; fi
      fi
      if [[ -n "$gc" ]]; then
        if [[ -z "$GPU_CLOCK_MAX" ]] || awk -v a="$gc" -v b="$GPU_CLOCK_MAX" 'BEGIN{exit !(a+0>b+0)}'; then GPU_CLOCK_MAX="$gc"; fi
      fi
    done < "${TMP_DIR}/.samples.$$"
    rm -f "${TMP_DIR}/.samples.$$"
  fi
  sample_temps
}

run_timed() {
  # run_timed VARNAME command...
  local _var="$1"; shift
  local _start _end _elapsed
  _start="$(date +%s.%N)"
  "$@"
  local _rc=$?
  _end="$(date +%s.%N)"
  _elapsed="$(awk -v s="$_start" -v e="$_end" 'BEGIN{printf "%.3f", e-s}')"
  printf -v "$_var" '%s' "$_elapsed"
  return $_rc
}

SKIPPED=()
skip() {
  SKIPPED+=("$1")
  log "SKIP: $1 ($2)"
}

# Result holders (empty = skipped/N/A)
R_7Z_ST=""          # 7z single-thread total rating
R_7Z_MT=""          # 7z multi-thread total rating
R_SB_CPU_ST=""      # sysbench cpu single events/s
R_SB_CPU_MT=""      # sysbench cpu multi events/s
R_SB_MEM=""         # sysbench memory MiB/s
R_ZSTD_MBPS=""      # zstd v1 compress MB/s (urandom)
R_ZSTD2_COMP=""     # zstd v2 -b3 -i10 compress MB/s
R_ZSTD2_DECOMP=""   # zstd v2 -b3 -i10 decompress MB/s
R_ZSTD_VER=""       # zstd --version string
R_X264_FPS=""       # x264 v1 encode fps (ultrafast)
R_X264V2_FPS=""     # x264 v2 encode fps (medium, 3000 frames)
ST_PINNED="none"    # e.g. pcores:0-15 for 7z ST pin; none if unpinned/skipped
OS_NAME="cachyos"
R_FIO_SEQ_R=""      # fio seq read MiB/s
R_FIO_SEQ_W=""      # fio seq write MiB/s
R_FIO_RAND_R=""     # fio 4k rand read IOPS
R_FIO_RAND_W=""     # fio 4k rand write IOPS
R_VKMARK=""         # vkmark score
R_GLMARK=""         # glmark2 score
R_LATENCY_MAX=""    # cyclictest max latency us (lower better)
R_WALL=""           # total wall seconds

# ---- metadata ----
if [[ -n "$UPDATE_ROW" ]]; then
  {
    echo ""
    echo "======== BACKFILL $(date '+%Y-%m-%d %H:%M:%S %Z') ========"
    echo "Re-running subset only=${ONLY_NORM:-all} to fill parse_failed metrics"
    echo "Original run timestamp: $TS"
  } >> "$LOG"
else
  : > "$LOG"
fi
section "CachyOS bench ${TS}"
log "tag: ${TAG:-none}"
log "smoke: ${SMOKE}"
log "only: ${ONLY_NORM:-all}"
log "update_row: ${UPDATE_ROW:-no}"
log "host: $(hostname)"
log "date_local: $(date '+%Y-%m-%d %H:%M:%S %Z')"
log "kernel: $(uname -r)"
log "cpu: $(lscpu 2>/dev/null | awk -F: '/Model name/{gsub(/^[ \t]+/,"",$2); print $2; exit}')"
log "nproc: $(nproc)"

# Detect P-core CPU list (higher MAXMHZ than E-cores on hybrid Intel).
# Override with CACHY_BENCH_PCORES (taskset -c list), e.g. export CACHY_BENCH_PCORES=0-15
# Fallback when detection fails: 0-15 (document / change via env on non-hybrid CPUs).
if [[ -n "${CACHY_BENCH_PCORES:-}" ]]; then
  PCORE_LIST="$CACHY_BENCH_PCORES"
else
PCORE_LIST="$(python3 - <<'PCORE'
import subprocess
out = subprocess.check_output(["lscpu", "-e"], text=True).strip().splitlines()
rows = []
for line in out[1:]:
    parts = line.split()
    if len(parts) < 7:
        continue
    try:
        cpu = int(parts[0]); maxmhz = float(parts[6])
    except ValueError:
        continue
    rows.append((cpu, maxmhz))
if not rows:
    print("")
    raise SystemExit
p = sorted(c for c, m in rows if m >= 4500.0)
if not p:
    rows_sorted = sorted(rows, key=lambda t: (-t[1], t[0]))
    cutoff = rows_sorted[0][1] - 500
    p = sorted(c for c, m in rows if m >= cutoff)
def compact(ids):
    if not ids:
        return ""
    ranges = []
    s = e = ids[0]
    for x in ids[1:]:
        if x == e + 1:
            e = x
        else:
            ranges.append(f"{s}-{e}" if s != e else str(s))
            s = e = x
    ranges.append(f"{s}-{e}" if s != e else str(s))
    return ",".join(ranges)
print(compact(p))
PCORE
)"
if [[ -z "$PCORE_LIST" ]]; then
  PCORE_LIST="0-15"
  log "WARNING: P-core detection failed; defaulting taskset to 0-15 (set CACHY_BENCH_PCORES to override)"
fi
fi
log "pcores: ${PCORE_LIST}  # used to pin 7z ST only"
log "os: ${OS_NAME}"

NVIDIA_DRV=""
GPU_NAME=""
if have nvidia-smi; then
  NVIDIA_DRV="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')"
  GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
fi
log "gpu: ${GPU_NAME:-unknown}"
log "nvidia_driver: ${NVIDIA_DRV:-unknown}"

PLASMA_VER="$(pacman -Q plasma-desktop 2>/dev/null | awk '{print $2}')"
MESA_VER="$(pacman -Q mesa 2>/dev/null | awk '{print $2}')"
log "plasma: ${PLASMA_VER:-unknown}"
log "mesa: ${MESA_VER:-unknown}"
log "session: ${XDG_SESSION_TYPE:-unknown}"

GOV="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo unknown)"
EPP="$(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null || echo n/a)"
POWER_PROFILE="n/a"
have powerprofilesctl && POWER_PROFILE="$(powerprofilesctl get 2>/dev/null || echo n/a)"
log "cpu_governor: $GOV"
log "cpu_epp: $EPP"
log "power_profile: $POWER_PROFILE"

SCX_STATUS="disabled"
if [[ -r /sys/kernel/sched_ext/state ]]; then
  SCX_STATUS="$(tr -d '\n' </sys/kernel/sched_ext/state)"
fi
if have scxctl; then
  SCX_GET="$(scxctl get 2>/dev/null | tr '\n' ' ' | sed 's/  */ /g;s/^[[:space:]]*//;s/[[:space:]]*$//')"
  [[ -n "$SCX_GET" ]] && SCX_STATUS="${SCX_STATUS}; ${SCX_GET}"
fi
log "scx: $SCX_STATUS"

LOADAVG="$(cut -d' ' -f1-3 /proc/loadavg)"
log "loadavg: $LOADAVG"
LOAD1="$(cut -d' ' -f1 /proc/loadavg)"
NCPU="$(nproc)"
# warn if load > nproc/2
if awk -v l="$LOAD1" -v n="$NCPU" 'BEGIN{exit !(l > n/2)}'; then
  log "WARNING: load average ${LOAD1} is high for ${NCPU} CPUs — close games/browsers/BOINC for a clean baseline"
fi

# BOINC / heavy process hint
HEAVY=""
if pgrep -x boinc >/dev/null 2>&1 || pgrep -x boincmgr >/dev/null 2>&1; then
  HEAVY="boinc"
  log "WARNING: BOINC appears to be running — pause it for cleaner scores"
fi
if pgrep -a steam 2>/dev/null | grep -qiE 'steam|GameOverlay' ; then
  HEAVY="${HEAVY:+$HEAVY,}steam"
  log "NOTE: Steam-related processes detected"
fi

sample_temps
START_CPU_TEMP="${CPU_TEMP_MAX:-}"
START_GPU_TEMP="${GPU_TEMP_MAX:-}"
log "cpu_temp_start_C: ${START_CPU_TEMP:-n/a}"
log "gpu_temp_start_C: ${START_GPU_TEMP:-n/a}"

HOT_START_THRESHOLD=50
COOLDOWN_WAITED_S=""
HOT_WARN=0
if [[ -n "$START_CPU_TEMP" ]] && awk -v t="$START_CPU_TEMP" -v lim="$HOT_START_THRESHOLD" 'BEGIN{exit !(t+0 > lim+0)}'; then
  HOT_WARN=1
fi
if [[ -n "$START_GPU_TEMP" ]] && awk -v t="$START_GPU_TEMP" -v lim="$HOT_START_THRESHOLD" 'BEGIN{exit !(t+0 > lim+0)}'; then
  HOT_WARN=1
fi
if [[ "$HOT_WARN" -eq 1 ]]; then
  log "WARNING: hot-start temps above ${HOT_START_THRESHOLD}C (CPU=${START_CPU_TEMP:-n/a} GPU=${START_GPU_TEMP:-n/a}) — scores may be noisy; cool down or pass --cooldown"
fi
if [[ "$COOLDOWN" -eq 1 && "$HOT_WARN" -eq 1 ]]; then
  log "cooldown: waiting until CPU and GPU <= ${HOT_START_THRESHOLD}C (max 300s)…"
  COOL_T0="$(date +%s)"
  while true; do
    sample_temps
    START_CPU_TEMP="${CPU_TEMP_MAX:-}"
    START_GPU_TEMP="${GPU_TEMP_MAX:-}"
    STILL_HOT=0
    if [[ -n "$START_CPU_TEMP" ]] && awk -v t="$START_CPU_TEMP" -v lim="$HOT_START_THRESHOLD" 'BEGIN{exit !(t+0 > lim+0)}'; then STILL_HOT=1; fi
    if [[ -n "$START_GPU_TEMP" ]] && awk -v t="$START_GPU_TEMP" -v lim="$HOT_START_THRESHOLD" 'BEGIN{exit !(t+0 > lim+0)}'; then STILL_HOT=1; fi
    ELAPSED=$(( $(date +%s) - COOL_T0 ))
    if [[ "$STILL_HOT" -eq 0 ]]; then
      COOLDOWN_WAITED_S="$ELAPSED"
      log "cooldown: reached threshold after ${ELAPSED}s (CPU=${START_CPU_TEMP:-n/a} GPU=${START_GPU_TEMP:-n/a})"
      break
    fi
    if [[ "$ELAPSED" -ge 300 ]]; then
      COOLDOWN_WAITED_S="$ELAPSED"
      log "WARNING: cooldown timed out after ${ELAPSED}s still hot (CPU=${START_CPU_TEMP:-n/a} GPU=${START_GPU_TEMP:-n/a})"
      break
    fi
    sleep 5
  done
  # Reset maxima so peak tracking starts from post-cooldown baseline
  CPU_TEMP_MAX="$START_CPU_TEMP"
  GPU_TEMP_MAX="$START_GPU_TEMP"
fi

# Sidecar: versions + pacman delta since previous run + hot-start (Python helper)
CHANGES_JSON="${RUNS_DIR}/${TS}.changes.json"
if [[ -f "${APP_ROOT}/app/changes.py" ]]; then
  # Resolve app version from bundled version.py (no CSV column)
  APP_VERSION="$(python3 -c "import sys; sys.path.insert(0, '${APP_ROOT}/app'); from version import __version__; print(__version__)" 2>/dev/null || true)"
  CHANGES_ARGS=(
    "${APP_ROOT}/app/changes.py" write-sidecar
    --csv "$CSV"
    --runs-dir "$RUNS_DIR"
    --ts "$TS"
    --kernel "$(uname -r)"
    --nvidia "${NVIDIA_DRV:-}"
    --plasma "${PLASMA_VER:-}"
    --mesa "${MESA_VER:-}"
    --cpu-temp "${START_CPU_TEMP:-}"
    --gpu-temp "${START_GPU_TEMP:-}"
    --app-version "${APP_VERSION:-}"
    --out "$CHANGES_JSON"
  )
  if [[ -n "${COOLDOWN_WAITED_S}" ]]; then
    CHANGES_ARGS+=(--cooldown-waited "$COOLDOWN_WAITED_S")
  fi
  if python3 "${CHANGES_ARGS[@]}" >>"$LOG" 2>&1; then
    log "changes_sidecar: $CHANGES_JSON"
  else
    log "WARNING: failed to write changes sidecar (non-fatal)"
  fi
else
  log "WARNING: changes.py missing under ${APP_ROOT}/app — skipping sidecar"
fi

WALL_START="$(date +%s)"

# Workload sizes
if [[ "$SMOKE" -eq 1 ]]; then
  FIO_SIZE="64M"
  FIO_RUNTIME=5
  ZSTD_MB=64
  X264_FRAMES=60
  SB_TIME=5
  VK_DURATION=1.0   # seconds per vkmark default-suite scene
  CYCLIC_DURATION=5
  DO_7Z=0
  log "SMOKE MODE: shortened workloads; 7z skipped (long)"
else
  FIO_SIZE="2G"
  FIO_RUNTIME=20
  ZSTD_MB=512
  X264_FRAMES=300
  SB_TIME=20
  VK_DURATION=2.0   # ~13 scenes × 2s ≈ 26s total
  CYCLIC_DURATION=20
  DO_7Z=1
fi

# ============================================================
# CPU: 7z benchmark
# Score = last number on Tot: line = overall Rating (MIPS),
# average of compress+decompress Ratings. 7-Zip 26.x Tot: format:
#   Tot:  <Usage%>  <R/U MIPS>  <Rating MIPS>
# Older 7z printed 8 numbers around a |; last number is still Rating.
# ============================================================
section "CPU — 7-Zip compress/decompress rating"
if want 7z && [[ "$DO_7Z" -eq 1 ]] && have 7z; then
  start_sampler
  ST_PINNED="pcores:${PCORE_LIST}"
  log "7z single-thread (-mmt=1) pinned to P-cores via taskset -c ${PCORE_LIST}..."
  OUT_ST="$(taskset -c "$PCORE_LIST" 7z b -mmt=1 2>&1 | tee -a "$LOG")"
  R_7Z_ST="$(printf '%s\n' "$OUT_ST" | awk '
    /^Tot:/ {
      last=""
      for (i=2; i<=NF; i++) if ($i ~ /^[0-9.]+$/) last=$i
      if (last != "") printf "%.0f", last+0
    }')"
  log "7z_st_score: ${R_7Z_ST:-parse_failed}  # Tot: overall Rating MIPS; st_pinned=${ST_PINNED}"

  log "7z multi-thread (all cores, unpinned)..."
  OUT_MT="$(7z b 2>&1 | tee -a "$LOG")"
  R_7Z_MT="$(printf '%s\n' "$OUT_MT" | awk '
    /^Tot:/ {
      last=""
      for (i=2; i<=NF; i++) if ($i ~ /^[0-9.]+$/) last=$i
      if (last != "") printf "%.0f", last+0
    }')"
  log "7z_mt_score: ${R_7Z_MT:-parse_failed}  # Tot: overall Rating MIPS"
  stop_sampler
elif ! want 7z; then
  skip "7z" "--only excluded"
elif [[ "$DO_7Z" -eq 0 ]]; then
  skip "7z" "smoke mode"
else
  skip "7z" "not installed"
fi

# ============================================================
# CPU: sysbench
# ============================================================
section "CPU — sysbench"
if want sysbench && have sysbench; then
  start_sampler
  log "sysbench cpu single-thread (${SB_TIME}s)..."
  OUT="$(sysbench cpu --cpu-max-prime=20000 --threads=1 --time="$SB_TIME" run 2>&1 | tee -a "$LOG")"
  R_SB_CPU_ST="$(printf '%s\n' "$OUT" | awk '/events per second:/{print $4; exit}')"
  log "sysbench_cpu_st_eps: ${R_SB_CPU_ST:-parse_failed}"

  log "sysbench cpu multi-thread (${NCPU} threads, ${SB_TIME}s)..."
  OUT="$(sysbench cpu --cpu-max-prime=20000 --threads="$NCPU" --time="$SB_TIME" run 2>&1 | tee -a "$LOG")"
  R_SB_CPU_MT="$(printf '%s\n' "$OUT" | awk '/events per second:/{print $4; exit}')"
  log "sysbench_cpu_mt_eps: ${R_SB_CPU_MT:-parse_failed}"
  stop_sampler
elif ! want sysbench; then
  skip "sysbench-cpu" "--only excluded"
else
  skip "sysbench-cpu" "not installed"
fi

# ============================================================
# Memory: sysbench
# ============================================================
section "Memory — sysbench"
if want memory && have sysbench; then
  start_sampler
  log "sysbench memory (${SB_TIME}s)..."
  OUT="$(sysbench memory --memory-block-size=1M --memory-total-size=100G --threads="$NCPU" --time="$SB_TIME" run 2>&1 | tee -a "$LOG")"
  # "transferred (XXX.XX MiB/sec)"
  R_SB_MEM="$(printf '%s\n' "$OUT" | awk '/transferred \(/ {
    if (match($0, /\(([0-9.]+) MiB\/sec\)/, a)) print a[1];
    else if (match($0, /\(([0-9.]+) GiB\/sec\)/, a)) printf "%.2f", a[1]*1024;
  }')"
  # fallback for mawk without 3-arg match
  if [[ -z "$R_SB_MEM" ]]; then
    R_SB_MEM="$(printf '%s\n' "$OUT" | sed -n 's/.*(\([0-9.]*\) MiB\/sec).*/\1/p' | head -1)"
  fi
  if [[ -z "$R_SB_MEM" ]]; then
    R_SB_MEM="$(printf '%s\n' "$OUT" | sed -n 's/.*(\([0-9.]*\) GiB\/sec).*/\1/p' | head -1)"
    if [[ -n "$R_SB_MEM" ]]; then
      R_SB_MEM="$(awk -v g="$R_SB_MEM" 'BEGIN{printf "%.2f", g*1024}')"
    fi
  fi
  log "sysbench_mem_MiBps: ${R_SB_MEM:-parse_failed}"
  stop_sampler
elif ! want memory; then
  skip "sysbench-memory" "--only excluded"
else
  skip "sysbench-memory" "not installed"
fi

# ============================================================
# CPU: zstd compress of fixed random file
# ============================================================
section "CPU — zstd compress"
if want zstd && have zstd && have dd; then
  ZFILE="${TMP_DIR}/zstd-input.bin"
  ZOUT="${TMP_DIR}/zstd-output.zst"
  log "Generating ${ZSTD_MB} MiB random input (urandom — incompressible)..."
  dd if=/dev/urandom of="$ZFILE" bs=1M count="$ZSTD_MB" status=none 2>>"$LOG"
  start_sampler
  log "zstd -T0 -3 compress..."
  # zstd prints: /*stdin*\            : XX.XX%   (insize => outsize, XX.XX MB/s)
  OUT="$(zstd -T0 -3 -f -o "$ZOUT" "$ZFILE" -v 2>&1 | tee -a "$LOG")"
  R_ZSTD_MBPS="$(printf '%s\n' "$OUT" | sed -n 's/.* \([0-9.]*\) MB\/s.*/\1/p' | tail -1)"
  # Also try from -b style; zstd -v on file may say differently
  if [[ -z "$R_ZSTD_MBPS" ]]; then
    # time-based fallback
    SZ=$((ZSTD_MB * 1024 * 1024))
    ELAPSED="$(awk -v s="$(stat -c%s "$ZFILE" 2>/dev/null || echo 0)" 'BEGIN{}')"
    # re-run with time
    rm -f "$ZOUT"
    T0="$(date +%s.%N)"
    zstd -T0 -3 -f -o "$ZOUT" "$ZFILE" >/dev/null 2>&1
    T1="$(date +%s.%N)"
    R_ZSTD_MBPS="$(awk -v mb="$ZSTD_MB" -v s="$T0" -v e="$T1" 'BEGIN{d=e-s; if(d<=0)d=0.001; printf "%.2f", mb/d}')"
  fi
  log "zstd_MBps: ${R_ZSTD_MBPS:-parse_failed}"
  stop_sampler
  rm -f "$ZFILE" "$ZOUT"
elif ! want zstd; then
  skip "zstd" "--only excluded"
else
  skip "zstd" "zstd or dd missing"
fi

# ============================================================
# CPU: zstd v2 built-in benchmark (-b3 -i10 -T0)
# Matches common Windows bench fio summary: parse LAST line with two MB/s
# (compress, then decompress). Sample label may be
# "Synthetic 50%" or "Lorem ipsum".
# ============================================================
section "CPU — zstd v2 (-b3 -i10 -T0)"
_run_zstd2=0
if want zstd2; then _run_zstd2=1; fi
if [[ -z "$ONLY_NORM" ]]; then _run_zstd2=1; fi
if [[ ",${ONLY_NORM}," == *",zstd,"* ]]; then _run_zstd2=1; fi
if [[ "$_run_zstd2" -eq 1 ]] && have zstd; then
  R_ZSTD_VER="$(zstd --version 2>&1 | head -1 | tr -d '\r')"
  R_ZSTD_VER="$(printf '%s\n' "$R_ZSTD_VER" | sed -n 's/.*v\([0-9][0-9.]*\).*/\1/p' | head -1)"
  [[ -z "$R_ZSTD_VER" ]] && R_ZSTD_VER="$(zstd --version 2>&1 | head -1)"
  log "zstd_version: ${R_ZSTD_VER}"
  start_sampler
  log "zstd -b3 -i10 -T0 ..."
  OUT="$(zstd -b3 -i10 -T0 2>&1 | tee -a "$LOG")"
  PARSE="$(printf '%s\n' "$OUT" | tr '\r' '\n' | python3 -c '
import sys,re
comp=decomp=None
for line in sys.stdin:
    nums=re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*MB/s", line)
    if len(nums)>=2:
        comp,decomp=nums[0],nums[1]
if comp is not None:
    print(comp, decomp)
')"
  R_ZSTD2_COMP="$(printf '%s\n' "$PARSE" | awk '{print $1}')"
  R_ZSTD2_DECOMP="$(printf '%s\n' "$PARSE" | awk '{print $2}')"
  log "zstd2_comp_MBps: ${R_ZSTD2_COMP:-parse_failed}"
  log "zstd2_decomp_MBps: ${R_ZSTD2_DECOMP:-parse_failed}"
  stop_sampler
elif [[ "$_run_zstd2" -eq 0 ]]; then
  skip "zstd2" "--only excluded"
else
  skip "zstd2" "zstd missing"
fi

# ============================================================
# CPU: x264 encode of generated pattern (ffmpeg lavfi)
# ============================================================
section "CPU — x264 encode"
if want x264 && have ffmpeg && have x264; then
  start_sampler
  # Generate and encode via lavfi — deterministic frames
  # Use ultrafast for speed; fixed frames; yuv420p
  log "ffmpeg x264 encode ${X264_FRAMES} frames @ 1920x1080..."
  OUT="$(ffmpeg -y -f lavfi -i "testsrc2=size=1920x1080:rate=60" -frames:v "$X264_FRAMES" \
    -c:v libx264 -preset ultrafast -crf 23 -pix_fmt yuv420p \
    -f null - 2>&1 | tee -a "$LOG")"
  # parse "frame=  300 fps=XXX"
  # Prefer last non-zero fps=; if encode finishes instantly ffmpeg prints fps=0.0 — use speed*60 or frames/elapsed
  R_X264_FPS="$(printf '%s\n' "$OUT" | tr '\r' '\n' | awk '
    /fps=/ {
      fps=""; speed=""; elapsed=""; frames=""
      for(i=1;i<=NF;i++){
        if($i ~ /^frame=/){ split($i,a,"="); frames=a[2] }
        if($i ~ /^fps=/){ split($i,a,"="); fps=a[2] }
        if($i ~ /^speed=/){ split($i,a,"="); speed=a[2]; sub(/x$/,"",speed) }
        if($i ~ /^elapsed=/){ split($i,a,"="); elapsed=a[2] }
      }
      if(fps+0 > 0){ best=fps }
      else if(speed+0 > 0){ best=sprintf("%.1f", speed*60) }
      last_frames=frames; last_elapsed=elapsed; last_speed=speed
    }
    END {
      if(best!="") print best
      else if(last_speed+0 > 0) printf "%.1f\n", last_speed*60
      else print ""
    }')"
  log "x264_fps: ${R_X264_FPS:-parse_failed}"
  stop_sampler
elif ! want x264; then
  skip "x264" "--only excluded"
else
  skip "x264" "ffmpeg/x264 missing"
fi

# ============================================================
# CPU: x264 v2 — medium preset, 3000 frames (matches Windows)
# Score = 3000 / elapsed seconds from ffmpeg elapsed=; wall fallback
# ============================================================
section "CPU — x264 v2 (medium, 3000 frames)"
_run_x264v2=0
if want x264v2; then _run_x264v2=1; fi
if [[ -z "$ONLY_NORM" ]]; then _run_x264v2=1; fi
if [[ ",${ONLY_NORM}," == *",x264,"* ]]; then _run_x264v2=1; fi
if [[ "$_run_x264v2" -eq 1 ]] && have ffmpeg; then
  start_sampler
  X264V2_FRAMES=3000
  [[ "$SMOKE" -eq 1 ]] && X264V2_FRAMES=120
  log "ffmpeg x264v2: testsrc2 1920x1080@60, ${X264V2_FRAMES} frames, libx264 -preset medium..."
  T0="$(date +%s.%N)"
  OUT="$(ffmpeg -hide_banner -y -f lavfi -i "testsrc2=size=1920x1080:rate=60" -frames:v "$X264V2_FRAMES" \
    -c:v libx264 -preset medium -f null - 2>&1 | tee -a "$LOG")" || true
  T1="$(date +%s.%N)"
  WALL_ELAPSED="$(awk -v s="$T0" -v e="$T1" 'BEGIN{printf "%.6f", e-s}')"
  FF_ELAPSED="$(printf '%s\n' "$OUT" | tr '\r' '\n' | awk '
    {
      for (i=1;i<=NF;i++) if ($i ~ /^elapsed=/) {
        split($i,a,"="); val=a[2]
      }
    }
    END { print val }
  ')"
  USED="wall"
  ELAPSED_SEC=""
  if [[ -n "$FF_ELAPSED" ]]; then
    ELAPSED_SEC="$(FF_ELAPSED="$FF_ELAPSED" python3 -c "
import os
s=os.environ['FF_ELAPSED'].strip()
parts=s.split(':')
try:
  parts=[float(p) for p in parts]
except Exception:
  print('')
  raise SystemExit
if len(parts)==3:
  print(parts[0]*3600+parts[1]*60+parts[2])
elif len(parts)==2:
  print(parts[0]*60+parts[1])
elif len(parts)==1:
  print(parts[0])
")"
    if [[ -n "$ELAPSED_SEC" ]]; then USED="ffmpeg_elapsed"; fi
  fi
  if [[ -z "$ELAPSED_SEC" ]] || ! awk -v e="$ELAPSED_SEC" 'BEGIN{exit !(e>0)}'; then
    ELAPSED_SEC="$WALL_ELAPSED"
    USED="wall"
  fi
  R_X264V2_FPS="$(awk -v f="$X264V2_FRAMES" -v e="$ELAPSED_SEC" 'BEGIN{if(e<=0)e=0.001; printf "%.1f", f/e}')"
  log "x264v2_fps: ${R_X264V2_FPS:-parse_failed}  # frames=${X264V2_FRAMES} elapsed_sec=${ELAPSED_SEC} source=${USED}"
  stop_sampler
elif [[ "$_run_x264v2" -eq 0 ]]; then
  skip "x264v2" "--only excluded"
else
  skip "x264v2" "ffmpeg missing"
fi

# ============================================================
# Disk: fio
# ============================================================
section "Disk — fio (NVMe under ~/bench/tmp)"
if want fio && have fio; then
  FIO_FILE="${TMP_DIR}/fio-target.bin"
  start_sampler
  log "fio sequential read (${FIO_SIZE}, direct=1)..."
  OUT="$(fio --name=seqread --filename="$FIO_FILE" --size="$FIO_SIZE" --rw=read \
    --bs=1M --direct=1 --ioengine=libaio --iodepth=32 --numjobs=1 \
    --runtime="$FIO_RUNTIME" --time_based=0 --group_reporting \
    --output-format=json 2>/dev/null)"
  RC=$?
  if [[ $RC -ne 0 || -z "$OUT" ]]; then
    # fallback text mode
    OUT_TXT="$(fio --name=seqread --filename="$FIO_FILE" --size="$FIO_SIZE" --rw=read \
      --bs=1M --direct=1 --ioengine=libaio --iodepth=32 --numjobs=1 \
      --group_reporting 2>&1 | tee -a "$LOG")"
    R_FIO_SEQ_R="$(printf '%s\n' "$OUT_TXT" | awk '/READ:/{ if(match($0,/BW=([0-9.]+)MiB\/s/,a)) print a[1]; else if(match($0,/BW=([0-9.]+)GiB\/s/,a)) printf "%.1f", a[1]*1024 }')"
    if [[ -z "$R_FIO_SEQ_R" ]]; then
      R_FIO_SEQ_R="$(printf '%s\n' "$OUT_TXT" | sed -n 's/.*BW=\([0-9.]*\)MiB\/s.*/\1/p' | head -1)"
    fi
  else
    printf '%s\n' "$OUT" >> "$LOG"
    R_FIO_SEQ_R="$(printf '%s\n' "$OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(round(d["jobs"][0]["read"]["bw_bytes"]/1024/1024,2))' 2>/dev/null || true)"
  fi
  log "fio_seq_read_MiBps: ${R_FIO_SEQ_R:-parse_failed}"

  log "fio sequential write (${FIO_SIZE}, direct=1)..."
  OUT="$(fio --name=seqwrite --filename="$FIO_FILE" --size="$FIO_SIZE" --rw=write \
    --bs=1M --direct=1 --ioengine=libaio --iodepth=32 --numjobs=1 \
    --group_reporting --output-format=json 2>/dev/null)"
  if [[ -z "$OUT" ]]; then
    OUT_TXT="$(fio --name=seqwrite --filename="$FIO_FILE" --size="$FIO_SIZE" --rw=write \
      --bs=1M --direct=1 --ioengine=libaio --iodepth=32 --numjobs=1 \
      --group_reporting 2>&1 | tee -a "$LOG")"
    R_FIO_SEQ_W="$(printf '%s\n' "$OUT_TXT" | sed -n 's/.*BW=\([0-9.]*\)MiB\/s.*/\1/p' | head -1)"
  else
    printf '%s\n' "$OUT" >> "$LOG"
    R_FIO_SEQ_W="$(printf '%s\n' "$OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(round(d["jobs"][0]["write"]["bw_bytes"]/1024/1024,2))' 2>/dev/null || true)"
  fi
  log "fio_seq_write_MiBps: ${R_FIO_SEQ_W:-parse_failed}"

  log "fio 4k random read (direct=1, ${FIO_RUNTIME}s)..."
  OUT="$(fio --name=randread --filename="$FIO_FILE" --size="$FIO_SIZE" --rw=randread \
    --bs=4k --direct=1 --ioengine=libaio --iodepth=32 --numjobs=4 \
    --runtime="$FIO_RUNTIME" --time_based=1 --group_reporting --output-format=json 2>/dev/null)"
  if [[ -z "$OUT" ]]; then
    OUT_TXT="$(fio --name=randread --filename="$FIO_FILE" --size="$FIO_SIZE" --rw=randread \
      --bs=4k --direct=1 --ioengine=libaio --iodepth=32 --numjobs=4 \
      --runtime="$FIO_RUNTIME" --time_based=1 --group_reporting 2>&1 | tee -a "$LOG")"
    R_FIO_RAND_R="$(printf '%s\n' "$OUT_TXT" | sed -n 's/.*IOPS=\([0-9.]*\)k.*/\1/p' | head -1)"
    if [[ -n "$R_FIO_RAND_R" ]]; then
      R_FIO_RAND_R="$(awk -v k="$R_FIO_RAND_R" 'BEGIN{printf "%.0f", k*1000}')"
    else
      R_FIO_RAND_R="$(printf '%s\n' "$OUT_TXT" | sed -n 's/.*IOPS=\([0-9.]*\).*/\1/p' | head -1)"
    fi
  else
    printf '%s\n' "$OUT" >> "$LOG"
    R_FIO_RAND_R="$(printf '%s\n' "$OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(round(d["jobs"][0]["read"]["iops"]))' 2>/dev/null || true)"
  fi
  log "fio_rand_read_IOPS: ${R_FIO_RAND_R:-parse_failed}"

  log "fio 4k random write (direct=1, ${FIO_RUNTIME}s)..."
  OUT="$(fio --name=randwrite --filename="$FIO_FILE" --size="$FIO_SIZE" --rw=randwrite \
    --bs=4k --direct=1 --ioengine=libaio --iodepth=32 --numjobs=4 \
    --runtime="$FIO_RUNTIME" --time_based=1 --group_reporting --output-format=json 2>/dev/null)"
  if [[ -z "$OUT" ]]; then
    OUT_TXT="$(fio --name=randwrite --filename="$FIO_FILE" --size="$FIO_SIZE" --rw=randwrite \
      --bs=4k --direct=1 --ioengine=libaio --iodepth=32 --numjobs=4 \
      --runtime="$FIO_RUNTIME" --time_based=1 --group_reporting 2>&1 | tee -a "$LOG")"
    R_FIO_RAND_W="$(printf '%s\n' "$OUT_TXT" | sed -n 's/.*IOPS=\([0-9.]*\)k.*/\1/p' | head -1)"
    if [[ -n "$R_FIO_RAND_W" ]]; then
      R_FIO_RAND_W="$(awk -v k="$R_FIO_RAND_W" 'BEGIN{printf "%.0f", k*1000}')"
    else
      R_FIO_RAND_W="$(printf '%s\n' "$OUT_TXT" | sed -n 's/.*IOPS=\([0-9.]*\).*/\1/p' | head -1)"
    fi
  else
    printf '%s\n' "$OUT" >> "$LOG"
    R_FIO_RAND_W="$(printf '%s\n' "$OUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(round(d["jobs"][0]["write"]["iops"]))' 2>/dev/null || true)"
  fi
  log "fio_rand_write_IOPS: ${R_FIO_RAND_W:-parse_failed}"
  stop_sampler
  rm -f "$FIO_FILE"
elif ! want fio; then
  skip "fio" "--only excluded"
else
  skip "fio" "not installed"
fi

# ============================================================
# GPU: vkmark
# vkmark has no global --duration; set per-scene via -b :duration=N
# (applies to the default suite). Use Wayland + immediate present mode
# so scores are not vsync-capped at ~60. Fixed size 1920x1080.
# ============================================================
section "GPU — vkmark (Vulkan)"
if want vkmark && have vkmark; then
  start_sampler
  VK_WINSYS="wayland"
  if [[ "${XDG_SESSION_TYPE:-}" == "x11" ]]; then
    VK_WINSYS="xcb"
  fi
  # VK_DURATION is seconds per default-suite scene (smoke~1, full~2 → ~13–26s)
  VK_ARGS=(--size 1920x1080 --winsys "$VK_WINSYS" --present-mode immediate -b ":duration=${VK_DURATION}")
  log "vkmark ${VK_ARGS[*]}..."
  OUT="$(vkmark "${VK_ARGS[@]}" 2>&1 | tee -a "$LOG")" || true
  R_VKMARK="$(printf '%s\n' "$OUT" | awk '/vkmark Score:/{print $NF; exit}')"
  if [[ -z "$R_VKMARK" ]]; then
    R_VKMARK="$(printf '%s\n' "$OUT" | sed -n 's/.*vkmark Score:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | tail -1)"
  fi
  # Fallback: try auto winsys if wayland/xcb failed
  if [[ -z "$R_VKMARK" ]]; then
    log "vkmark primary winsys failed; retrying auto winsys..."
    OUT="$(vkmark --size 1920x1080 --present-mode immediate -b ":duration=${VK_DURATION}" 2>&1 | tee -a "$LOG")" || true
    R_VKMARK="$(printf '%s\n' "$OUT" | awk '/vkmark Score:/{print $NF; exit}')"
  fi
  log "vkmark_score: ${R_VKMARK:-parse_failed}"
  stop_sampler
elif ! want vkmark; then
  skip "vkmark" "--only excluded"
else
  skip "vkmark" "not installed"
fi

# ============================================================
# GPU: glmark2 (OpenGL fallback / secondary)
# ============================================================
section "GPU — glmark2 (OpenGL)"
if want glmark2 && have glmark2; then
  start_sampler
  # Offscreen is more repeatable on Wayland; fall back to on-screen
  log "glmark2 --off-screen..."
  OUT="$(glmark2 --off-screen 2>&1 | tee -a "$LOG")" || true
  R_GLMARK="$(printf '%s\n' "$OUT" | awk '/glmark2 Score:/{print $3; exit}')"
  if [[ -z "$R_GLMARK" ]]; then
    log "off-screen failed or unscored; trying default..."
    OUT="$(glmark2 2>&1 | tee -a "$LOG")" || true
    R_GLMARK="$(printf '%s\n' "$OUT" | awk '/glmark2 Score:/{print $3; exit}')"
  fi
  log "glmark2_score: ${R_GLMARK:-parse_failed}"
  stop_sampler
elif ! want glmark2; then
  skip "glmark2" "--only excluded"
else
  skip "glmark2" "not installed"
fi

# ============================================================
# Latency under light load (optional)
# Non-root: no -p (cannot set RT priority). Metric is indicative only.
# ============================================================
section "Latency — cyclictest (optional, non-RT)"
if want cyclictest && have cyclictest; then
  start_sampler
  # Non-root cyclictest works with reduced privileges; may warn
  STRESS_PID=""
  if have stress-ng; then
    stress-ng --cpu 2 --timeout "${CYCLIC_DURATION}s" >/dev/null 2>&1 &
    STRESS_PID=$!
  fi
  log "cyclictest -l $((CYCLIC_DURATION*1000)) -t 1 -m (approx ${CYCLIC_DURATION}s; non-RT, no -p)..."
  # -l loops; interval 1000us → ~1000 loops/sec. No -p: non-root cannot set RT priority.
  LOOPS=$((CYCLIC_DURATION * 1000))
  OUT="$(cyclictest -l "$LOOPS" -t 1 -m -q 2>&1 | tee -a "$LOG")" || true
  R_LATENCY_MAX="$(printf '%s\n' "$OUT" | awk '/Max:/{ for(i=1;i<=NF;i++) if($i=="Max:") {print $(i+1); exit} }')"
  if [[ -z "$R_LATENCY_MAX" ]]; then
    R_LATENCY_MAX="$(printf '%s\n' "$OUT" | sed -n 's/.*Max:[[:space:]]*\([0-9]*\).*/\1/p' | head -1)"
  fi
  log "cyclictest_max_us: ${R_LATENCY_MAX:-parse_failed}"
  if [[ -n "$STRESS_PID" ]]; then
    wait "$STRESS_PID" 2>/dev/null || true
  fi
  stop_sampler
elif ! want cyclictest; then
  skip "cyclictest" "--only excluded"
else
  skip "cyclictest" "not installed"
fi

# ============================================================
# Finish
# ============================================================
WALL_END="$(date +%s)"
R_WALL=$((WALL_END - WALL_START))
sample_temps

section "Summary"
log "wall_seconds: $R_WALL"
log "cpu_temp_max_C: ${CPU_TEMP_MAX:-n/a}"
log "gpu_temp_max_C: ${GPU_TEMP_MAX:-n/a}"
log "gpu_clock_max_MHz: ${GPU_CLOCK_MAX:-n/a}"
if ((${#SKIPPED[@]})); then
  log "skipped: ${SKIPPED[*]}"
fi

# CSV header (stable columns)
HEADER="timestamp,tag,os,hostname,kernel,nvidia_driver,plasma,mesa,session,governor,epp,power_profile,scx,loadavg,cpu_temp_max_C,gpu_temp_max_C,gpu_clock_max_MHz,7z_st,7z_mt,st_pinned,sysbench_cpu_st_eps,sysbench_cpu_mt_eps,sysbench_mem_MiBps,zstd_MBps,zstd2_comp_MBps,zstd2_decomp_MBps,zstd_version,x264_fps,x264v2_fps,fio_seq_read_MiBps,fio_seq_write_MiBps,fio_rand_read_IOPS,fio_rand_write_IOPS,vkmark,glmark2,cyclictest_max_us,wall_seconds,log_file"

if [[ ! -f "$CSV" ]]; then
  printf '%s\n' "$HEADER" > "$CSV"
fi

SCX_CSV="$(printf '%s' "$SCX_STATUS" | tr ',' ';' | tr '\n' ' ')"
ROW_TAG="${TAG}"
[[ "$MARK_BASELINE" -eq 1 ]] && ROW_TAG="${ROW_TAG:+$ROW_TAG-}baseline"
[[ "$SMOKE" -eq 1 ]] && ROW_TAG="${ROW_TAG:+$ROW_TAG-}smoke"

# Write or update CSV row
if [[ -n "$UPDATE_ROW" ]]; then
  # Patch only the metric columns that this run produced (non-empty R_*)
  python3 - "$CSV" "$TS" \
      "${R_7Z_ST}" "${R_7Z_MT}" "${ST_PINNED}" \
      "${R_SB_CPU_ST}" "${R_SB_CPU_MT}" "${R_SB_MEM}" \
      "${R_ZSTD_MBPS}" "${R_ZSTD2_COMP}" "${R_ZSTD2_DECOMP}" "${R_ZSTD_VER}" \
      "${R_X264_FPS}" "${R_X264V2_FPS}" \
      "${R_FIO_SEQ_R}" "${R_FIO_SEQ_W}" "${R_FIO_RAND_R}" "${R_FIO_RAND_W}" \
      "${R_VKMARK}" "${R_GLMARK}" "${R_LATENCY_MAX}" "${OS_NAME}" <<'PY'
import csv, sys, os, tempfile
path = sys.argv[1]
ts = sys.argv[2]
updates = {
    "7z_st": sys.argv[3],
    "7z_mt": sys.argv[4],
    "st_pinned": sys.argv[5],
    "sysbench_cpu_st_eps": sys.argv[6],
    "sysbench_cpu_mt_eps": sys.argv[7],
    "sysbench_mem_MiBps": sys.argv[8],
    "zstd_MBps": sys.argv[9],
    "zstd2_comp_MBps": sys.argv[10],
    "zstd2_decomp_MBps": sys.argv[11],
    "zstd_version": sys.argv[12],
    "x264_fps": sys.argv[13],
    "x264v2_fps": sys.argv[14],
    "fio_seq_read_MiBps": sys.argv[15],
    "fio_seq_write_MiBps": sys.argv[16],
    "fio_rand_read_IOPS": sys.argv[17],
    "fio_rand_write_IOPS": sys.argv[18],
    "vkmark": sys.argv[19],
    "glmark2": sys.argv[20],
    "cyclictest_max_us": sys.argv[21],
    "os": sys.argv[22],
}
wanted_extra = ["os","st_pinned","zstd2_comp_MBps","zstd2_decomp_MBps","zstd_version","x264v2_fps"]
with open(path, newline="") as f:
    reader = csv.DictReader(f)
    header = list(reader.fieldnames or [])
    rows = list(reader)
for col in wanted_extra:
    if col not in header:
        if col == "os" and "tag" in header:
            header.insert(header.index("tag")+1, col)
        elif col == "st_pinned" and "7z_mt" in header:
            header.insert(header.index("7z_mt")+1, col)
        elif col == "zstd2_comp_MBps" and "zstd_MBps" in header:
            header.insert(header.index("zstd_MBps")+1, col)
        elif col == "zstd2_decomp_MBps" and "zstd2_comp_MBps" in header:
            header.insert(header.index("zstd2_comp_MBps")+1, col)
        elif col == "zstd_version" and "zstd2_decomp_MBps" in header:
            header.insert(header.index("zstd2_decomp_MBps")+1, col)
        elif col == "x264v2_fps" and "x264_fps" in header:
            header.insert(header.index("x264_fps")+1, col)
        else:
            header.append(col)
found = False
for r in rows:
    if r.get("timestamp") == ts:
        for k, v in updates.items():
            if v != "":
                r[k] = v
        if not r.get("os"):
            r["os"] = updates.get("os") or "cachyos"
        found = True
        break
if not found:
    sys.stderr.write(f"ERROR: no CSV row with timestamp {ts}\n")
    sys.exit(1)
fd, tmp = tempfile.mkstemp(prefix="results.", suffix=".csv", dir=os.path.dirname(path) or ".")
os.close(fd)
with open(tmp, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=header, quoting=csv.QUOTE_ALL)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in header})
os.replace(tmp, path)
filled = [k for k, v in updates.items() if v != ""]
print("updated columns: " + ",".join(filled))
PY
  log "Updated CSV row ${TS} in $CSV (backfill)"
  {
    echo ""
    echo "BACKFILL complete $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "Filled: 7z_st=${R_7Z_ST:-} 7z_mt=${R_7Z_MT:-} vkmark=${R_VKMARK:-}"
  } >> "$LOG"
else
  # Ensure CSV header has all current columns (migrate older files)
  python3 - "$CSV" "$HEADER" <<'PY'
import csv, sys, os, tempfile
path, header_csv = sys.argv[1], sys.argv[2]
wanted = [c.strip() for c in header_csv.split(",")]
if not os.path.isfile(path):
    with open(path, "w", newline="") as f:
        csv.writer(f, quoting=csv.QUOTE_ALL).writerow(wanted)
    raise SystemExit
with open(path, newline="") as f:
    reader = csv.DictReader(f)
    old_fields = list(reader.fieldnames or [])
    rows = list(reader)
if old_fields == wanted:
    raise SystemExit
fd, tmp = tempfile.mkstemp(prefix="results.", suffix=".csv", dir=os.path.dirname(path) or ".")
os.close(fd)
with open(tmp, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=wanted, quoting=csv.QUOTE_ALL)
    w.writeheader()
    for r in rows:
        out = {k: r.get(k, "") for k in wanted}
        if not out.get("os"):
            out["os"] = "cachyos"
        if not out.get("st_pinned"):
            out["st_pinned"] = "none"
        w.writerow(out)
os.replace(tmp, path)
print("migrated CSV header to include new columns")
PY

  # Append new row
  {
    csv_escape "$TS"; printf ','
    csv_escape "$ROW_TAG"; printf ','
    csv_escape "$OS_NAME"; printf ','
    csv_escape "$(hostname)"; printf ','
    csv_escape "$(uname -r)"; printf ','
    csv_escape "${NVIDIA_DRV}"; printf ','
    csv_escape "${PLASMA_VER}"; printf ','
    csv_escape "${MESA_VER}"; printf ','
    csv_escape "${XDG_SESSION_TYPE:-}"; printf ','
    csv_escape "$GOV"; printf ','
    csv_escape "$EPP"; printf ','
    csv_escape "$POWER_PROFILE"; printf ','
    csv_escape "$SCX_CSV"; printf ','
    csv_escape "$LOADAVG"; printf ','
    csv_escape "${CPU_TEMP_MAX}"; printf ','
    csv_escape "${GPU_TEMP_MAX}"; printf ','
    csv_escape "${GPU_CLOCK_MAX}"; printf ','
    csv_escape "${R_7Z_ST}"; printf ','
    csv_escape "${R_7Z_MT}"; printf ','
    csv_escape "${ST_PINNED}"; printf ','
    csv_escape "${R_SB_CPU_ST}"; printf ','
    csv_escape "${R_SB_CPU_MT}"; printf ','
    csv_escape "${R_SB_MEM}"; printf ','
    csv_escape "${R_ZSTD_MBPS}"; printf ','
    csv_escape "${R_ZSTD2_COMP}"; printf ','
    csv_escape "${R_ZSTD2_DECOMP}"; printf ','
    csv_escape "${R_ZSTD_VER}"; printf ','
    csv_escape "${R_X264_FPS}"; printf ','
    csv_escape "${R_X264V2_FPS}"; printf ','
    csv_escape "${R_FIO_SEQ_R}"; printf ','
    csv_escape "${R_FIO_SEQ_W}"; printf ','
    csv_escape "${R_FIO_RAND_R}"; printf ','
    csv_escape "${R_FIO_RAND_W}"; printf ','
    csv_escape "${R_VKMARK}"; printf ','
    csv_escape "${R_GLMARK}"; printf ','
    csv_escape "${R_LATENCY_MAX}"; printf ','
    csv_escape "${R_WALL}"; printf ','
    csv_escape "$LOG"; printf '\n'
  } >> "$CSV"
  log "Results appended to $CSV"
fi

if [[ "$MARK_BASELINE" -eq 1 ]]; then
  printf '%s\n' "$TS" > "$BASELINE_FILE"
  log "Marked as baseline: $TS"
fi

log ""
log "Full log: $LOG"
log "Done in ${R_WALL}s. Run compare.sh (or the GUI Compare tab) to see deltas vs baseline."
