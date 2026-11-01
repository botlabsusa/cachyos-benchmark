"""Package/version deltas, hot-start temps, and noise-aware 'what changed' reports.

Sidecar: runs/<ts>.changes.json (keeps results.csv schema stable).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Local import when run as script or alongside GUI
_APP = Path(__file__).resolve().parent
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from version import __version__  # noqa: E402

from metrics import (  # noqa: E402
    LIB,
    METRICS,
    compare_note,
    higher_is_better,
    is_improved,
    parse_num,
    pct_change,
)

HOT_START_THRESHOLD_C = 50.0
def resolve_baseline_ts(data_dir: Path | str | None = None) -> str:
    """Baseline timestamp for what-changed.

    Order: CACHY_BENCH_BASELINE_TS env, then <data_dir>/baseline.tag
    (data_dir defaults to $CACHY_BENCH_DATA or ~/bench). Empty if unset.
    """
    env = os.environ.get("CACHY_BENCH_BASELINE_TS", "").strip()
    if env:
        return env
    root = Path(data_dir) if data_dir else Path(
        os.environ.get("CACHY_BENCH_DATA") or Path.home() / "bench"
    )
    tag = root / "baseline.tag"
    if tag.is_file():
        return tag.read_text().strip()
    return ""


# Prefer env / baseline.tag at import time when present; else empty (resolved again at use).
FIXED_BASELINE_TS = resolve_baseline_ts()
EXCLUDE_TAG_SUBSTR = ("appimage-test-smoke",)

# Default run-to-run noise (%) when fewer than 3 comparable samples exist.
DEFAULT_NOISE_PCT: dict[str, float] = {
    "7z_st": 2.0,
    "7z_mt": 2.0,
    "sysbench_cpu_st_eps": 2.0,
    "sysbench_cpu_mt_eps": 2.0,
    "sysbench_mem_MiBps": 3.0,
    "zstd_MBps": 2.5,
    "zstd2_comp_MBps": 2.5,
    "zstd2_decomp_MBps": 2.5,
    "x264_fps": 2.5,
    "x264v2_fps": 2.5,
    "fio_seq_read_MiBps": 5.0,
    "fio_seq_write_MiBps": 5.0,
    "fio_rand_read_IOPS": 5.0,
    "fio_rand_write_IOPS": 5.0,
    "vkmark": 3.0,
    "glmark2": 3.0,
    "cyclictest_max_us": 15.0,
    "cpu_temp_max_C": 5.0,
    "gpu_temp_max_C": 5.0,
    "wall_seconds": 5.0,
}

def _pacman_log() -> Path:
    return Path(os.environ.get("CACHY_BENCH_PACMAN_LOG", "/var/log/pacman.log"))

PACMAN_LOG = Path("/var/log/pacman.log")
_PACMAN_RE = re.compile(
    r"^\[([^\]]+)\]\s+\[ALPM\]\s+(upgraded|installed|removed)\s+(\S+)(?:\s+\((.+)\))?"
)


def tag_excluded(tag: str | None) -> bool:
    t = (tag or "").lower()
    return any(s in t for s in EXCLUDE_TAG_SUBSTR)


def is_comparable_row(row: dict) -> bool:
    """Rows usable for noise / previous-run metric compares."""
    if tag_excluded(row.get("tag")):
        return False
    # Smoke workloads are not comparable to full runs for noise.
    if "smoke" in (row.get("tag") or "").lower():
        return False
    return True


def parse_run_ts(ts: str) -> datetime | None:
    """Parse CSV timestamp YYYYMMDD-HHMMSS as naive local time."""
    ts = (ts or "").strip()
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def parse_pacman_ts(s: str) -> datetime | None:
    """Parse pacman.log stamp like 2026-10-31T21:25:08-0500.

    Keep the logged wall-clock time (drop tz without converting) so it lines
    up with CSV timestamps that are naive local wall times. Converting via
    astimezone() skews across DST offset changes.
    """
    s = (s or "").strip()
    if not s:
        return None
    # Normalize +0500 / -0500 to +05:00 for fromisoformat
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{2})(\d{2})$", s)
    if m:
        s = f"{m.group(1)}{m.group(2)}:{m.group(3)}"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def load_csv_rows(csv_path: Path) -> list[dict]:
    if not csv_path.is_file():
        return []
    with csv_path.open(newline="") as f:
        return list(csv.DictReader(f))


def _rows_before(rows: list[dict], current_ts: str | None) -> list[dict]:
    """Rows strictly before current_ts chronologically; if ts unknown, all except exact ts."""
    out = [r for r in rows if r.get("timestamp") and r.get("timestamp") != current_ts]
    if not current_ts:
        return out
    cur = parse_run_ts(current_ts)
    if cur is None:
        return out
    before: list[dict] = []
    for r in out:
        dt = parse_run_ts(r.get("timestamp") or "")
        if dt is not None and dt < cur:
            before.append(r)
    return before


def previous_run_row(rows: list[dict], current_ts: str | None = None) -> dict | None:
    """Most recent row before current_ts (or last row if current not in CSV yet)."""
    prior = _rows_before(rows, current_ts)
    return prior[-1] if prior else None


def previous_comparable_row(rows: list[dict], current_ts: str | None = None) -> dict | None:
    prior = [r for r in _rows_before(rows, current_ts) if is_comparable_row(r)]
    return prior[-1] if prior else None


def parse_pacman_since(since_ts: str | None, log_path: Path | None = None) -> dict[str, Any]:
    """Return upgraded/installed/removed lists since CSV timestamp (exclusive)."""
    out: dict[str, Any] = {
        "since_timestamp": since_ts,
        "log_readable": False,
        "upgraded": [],
        "installed": [],
        "removed": [],
        "error": None,
    }
    if log_path is None:
        log_path = _pacman_log()
    since_dt = parse_run_ts(since_ts) if since_ts else None
    if not log_path.is_file():
        out["error"] = f"missing {log_path}"
        return out
    if not os.access(log_path, os.R_OK):
        out["error"] = f"unreadable {log_path}"
        return out
    out["log_readable"] = True
    try:
        # Read last ~2MB to keep it snappy on large logs
        data = log_path.read_bytes()
        if len(data) > 2_000_000:
            data = data[-2_000_000:]
        text = data.decode("utf-8", errors="replace")
    except OSError as e:
        out["error"] = str(e)
        return out

    for line in text.splitlines():
        m = _PACMAN_RE.match(line)
        if not m:
            continue
        ts_s, action, name, detail = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        dt = parse_pacman_ts(ts_s)
        if since_dt is not None and dt is not None and dt <= since_dt:
            continue
        if since_dt is not None and dt is None:
            continue
        entry: dict[str, str] = {"name": name, "when": ts_s}
        if action == "upgraded":
            # detail like "1.0-1 -> 1.0-2"
            if "->" in detail:
                left, right = detail.split("->", 1)
                entry["from"] = left.strip()
                entry["to"] = right.strip()
            else:
                entry["detail"] = detail
            out["upgraded"].append(entry)
        elif action == "installed":
            entry["version"] = detail.strip()
            out["installed"].append(entry)
        elif action == "removed":
            entry["version"] = detail.strip()
            out["removed"].append(entry)
    return out


def read_start_temps() -> tuple[float | None, float | None]:
    """Best-effort CPU/GPU temps via sensors / nvidia-smi."""
    cpu = None
    gpu = None
    try:
        out = subprocess.check_output(["sensors"], text=True, errors="ignore", timeout=5)
        for line in out.splitlines():
            if "Package id 0:" in line or line.strip().startswith("Tctl:"):
                # +91.0°C
                m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*°?C", line)
                if m:
                    cpu = float(m.group(1))
                    break
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            errors="ignore",
            timeout=5,
        )
        gpu = float(out.strip().splitlines()[0].strip())
    except Exception:
        pass
    return cpu, gpu


def hot_start_info(
    cpu: float | None,
    gpu: float | None,
    threshold: float = HOT_START_THRESHOLD_C,
    cooldown_waited_s: float | None = None,
) -> dict[str, Any]:
    warned = (cpu is not None and cpu > threshold) or (gpu is not None and gpu > threshold)
    return {
        "cpu_temp_C": cpu,
        "gpu_temp_C": gpu,
        "threshold_C": threshold,
        "warned": bool(warned),
        "cooldown_waited_s": cooldown_waited_s,
    }


def _stdev(vals: list[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    mean = sum(vals) / n
    var = sum((x - mean) ** 2 for x in vals) / (n - 1)
    return math.sqrt(var)


def noise_threshold_pct(key: str, history_values: list[float]) -> tuple[float, str]:
    """Return (threshold_pct, source) for a metric."""
    default = DEFAULT_NOISE_PCT.get(key, 3.0)
    usable = [v for v in history_values if v is not None]
    if len(usable) < 3:
        return default, f"default_n={len(usable)}"
    mean = sum(usable) / len(usable)
    if mean == 0:
        return default, f"default_zero_mean_n={len(usable)}"
    cv_pct = _stdev(usable) / abs(mean) * 100.0
    # Use at least the default floor so tiny stdev doesn't flag everything.
    thr = max(default, cv_pct)
    return thr, f"stdev_n={len(usable)}"


def collect_metric_history(rows: list[dict], key: str, exclude_ts: str | None = None) -> list[float]:
    vals: list[float] = []
    for r in rows:
        if exclude_ts and r.get("timestamp") == exclude_ts:
            continue
        if not is_comparable_row(r):
            continue
        v = parse_num(r.get(key))
        if v is not None:
            vals.append(v)
    return vals


@dataclass
class MetricDelta:
    key: str
    label: str
    baseline: float | None
    latest: float | None
    pct: float | None
    noise_pct: float
    noise_source: str
    verdict: str  # better | worse | within_noise | n/a | new
    note: str


def metric_delta(
    key: str,
    label: str,
    baseline: float | None,
    latest: float | None,
    history: list[float],
) -> MetricDelta:
    thr, src = noise_threshold_pct(key, history)
    if baseline is None and latest is None:
        return MetricDelta(key, label, None, None, None, thr, src, "n/a", "n/a")
    if baseline is None:
        return MetricDelta(key, label, None, latest, None, thr, src, "new", "new")
    if latest is None:
        return MetricDelta(key, label, baseline, None, None, thr, src, "n/a", "n/a")
    pct = pct_change(baseline, latest)
    if pct is None:
        return MetricDelta(key, label, baseline, latest, None, thr, src, "n/a", "n/a")
    if abs(pct) <= thr:
        return MetricDelta(
            key, label, baseline, latest, pct, thr, src, "within_noise", "within noise"
        )
    if is_improved(key, baseline, latest):
        arrow = "↓" if key in LIB else "↑"
        return MetricDelta(
            key, label, baseline, latest, pct, thr, src, "better", f"better {arrow}"
        )
    arrow = "↑" if key in LIB else "↓"
    return MetricDelta(
        key, label, baseline, latest, pct, thr, src, "worse", f"worse {arrow}"
    )


def build_what_changed(
    rows: list[dict],
    latest_ts: str | None = None,
    baseline_ts: str | None = None,
    runs_dir: Path | None = None,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Build report: latest vs previous comparable, and vs fixed baseline."""
    if baseline_ts is None:
        baseline_ts = resolve_baseline_ts(data_dir)
    if not rows:
        return {"error": "no rows", "latest": None}

    def _ts_key(r: dict) -> tuple:
        dt = parse_run_ts(r.get("timestamp") or "")
        return (dt or __import__("datetime").datetime.min, r.get("timestamp") or "")

    # Prefer chronologically latest comparable row; else latest any row
    comparable = sorted([r for r in rows if is_comparable_row(r)], key=_ts_key)
    ordered = sorted(rows, key=_ts_key)
    if latest_ts:
        latest = next((r for r in rows if r.get("timestamp") == latest_ts), None)
    else:
        latest = comparable[-1] if comparable else (ordered[-1] if ordered else None)
    if not latest:
        return {"error": "latest not found", "latest": None}
    latest_ts = latest.get("timestamp")
    prev = previous_comparable_row(rows, latest_ts)
    by_ts = {r.get("timestamp"): r for r in rows}
    baseline = by_ts.get(baseline_ts)

    sidecar = None
    if runs_dir and latest_ts:
        p = runs_dir / f"{latest_ts}.changes.json"
        if p.is_file():
            try:
                sidecar = json.loads(p.read_text())
            except Exception:
                sidecar = {"error": f"failed to parse {p}"}

    def side_for(ref: dict | None, label: str) -> dict[str, Any]:
        if not ref:
            return {"label": label, "ref_timestamp": None, "metrics": [], "moved": []}
        deltas: list[MetricDelta] = []
        for key, lab in METRICS:
            hist = collect_metric_history(rows, key, exclude_ts=latest_ts)
            d = metric_delta(
                key,
                lab,
                parse_num(ref.get(key)),
                parse_num(latest.get(key)),
                hist,
            )
            deltas.append(d)
        moved = [d for d in deltas if d.verdict in ("better", "worse")]
        return {
            "label": label,
            "ref_timestamp": ref.get("timestamp"),
            "ref_tag": ref.get("tag"),
            "metrics": [
                {
                    "key": d.key,
                    "label": d.label,
                    "baseline": d.baseline,
                    "latest": d.latest,
                    "pct": None if d.pct is None else round(d.pct, 2),
                    "noise_pct": round(d.noise_pct, 2),
                    "noise_source": d.noise_source,
                    "verdict": d.verdict,
                    "note": d.note,
                }
                for d in deltas
            ],
            "moved": [
                {
                    "key": d.key,
                    "label": d.label,
                    "pct": round(d.pct or 0.0, 2),
                    "noise_pct": round(d.noise_pct, 2),
                    "verdict": d.verdict,
                    "note": d.note,
                }
                for d in moved
            ],
        }

    versions = {
        "kernel": latest.get("kernel"),
        "nvidia_driver": latest.get("nvidia_driver"),
        "plasma": latest.get("plasma"),
        "mesa": latest.get("mesa"),
    }
    versions_prev = None
    if prev:
        versions_prev = {
            "kernel": prev.get("kernel"),
            "nvidia_driver": prev.get("nvidia_driver"),
            "plasma": prev.get("plasma"),
            "mesa": prev.get("mesa"),
        }

    pacman = (sidecar or {}).get("pacman") if sidecar else None
    if pacman is None and prev:
        pacman = parse_pacman_since(prev.get("timestamp"))

    hot = (sidecar or {}).get("hot_start") if sidecar else None

    app_ver = (sidecar or {}).get("app_version") if sidecar else None
    return {
        "latest": {
            "timestamp": latest_ts,
            "tag": latest.get("tag"),
            "versions": versions,
            "app_version": app_ver,
        },
        "vs_previous": side_for(prev, "previous comparable run"),
        "vs_baseline": side_for(baseline, f"baseline {baseline_ts}"),
        "versions_previous": versions_prev,
        "pacman": pacman,
        "hot_start": hot,
        "sidecar_path": str(runs_dir / f"{latest_ts}.changes.json") if runs_dir and latest_ts else None,
        "notes": [
            "appimage-test-smoke and other smoke-tagged rows excluded from comparisons/noise.",
            "v1/v2 metrics are separate columns and are never cross-compared.",
            "Noise uses sample stdev when ≥3 comparable prior values; else per-metric defaults.",
        ],
    }


def write_sidecar(
    *,
    csv_path: Path,
    runs_dir: Path,
    ts: str,
    kernel: str,
    nvidia: str,
    plasma: str,
    mesa: str,
    cpu_temp: float | None,
    gpu_temp: float | None,
    cooldown_waited_s: float | None = None,
    app_version: str | None = None,
    out_path: Path | None = None,
) -> Path:
    rows = load_csv_rows(csv_path)
    prev = previous_run_row(rows, current_ts=ts)
    prev_ts = prev.get("timestamp") if prev else None
    versions_prev = None
    if prev:
        versions_prev = {
            "kernel": prev.get("kernel"),
            "nvidia_driver": prev.get("nvidia_driver"),
            "plasma": prev.get("plasma"),
            "mesa": prev.get("mesa"),
        }
    payload = {
        "timestamp": ts,
        "previous_timestamp": prev_ts,
        "app_version": app_version or __version__,
        "versions": {
            "kernel": kernel or None,
            "nvidia_driver": nvidia or None,
            "plasma": plasma or None,
            "mesa": mesa or None,
        },
        "versions_previous": versions_prev,
        "pacman": parse_pacman_since(prev_ts),
        "hot_start": hot_start_info(cpu_temp, gpu_temp, cooldown_waited_s=cooldown_waited_s),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    out = out_path or (runs_dir / f"{ts}.changes.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    return out


def format_what_changed_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    latest = report.get("latest") or {}
    lines.append(f"Latest: {latest.get('timestamp')}  tag={latest.get('tag') or '-'}")
    if latest.get("app_version"):
        lines.append(f"  app_version={latest.get('app_version')}")
    vers = latest.get("versions") or {}
    lines.append(
        f"  kernel={vers.get('kernel')}  nvidia={vers.get('nvidia_driver')}  "
        f"plasma={vers.get('plasma')}  mesa={vers.get('mesa')}"
    )
    hot = report.get("hot_start") or {}
    if hot:
        flag = "HOT" if hot.get("warned") else "ok"
        lines.append(
            f"Hot-start: {flag}  CPU={hot.get('cpu_temp_C')}C  GPU={hot.get('gpu_temp_C')}C  "
            f"(threshold {hot.get('threshold_C')}C)"
        )
    pac = report.get("pacman") or {}
    if pac:
        lines.append(
            f"Packages since {pac.get('since_timestamp') or 'beginning'}: "
            f"+{len(pac.get('installed') or [])} "
            f"↑{len(pac.get('upgraded') or [])} "
            f"-{len(pac.get('removed') or [])}"
        )
        for e in (pac.get("upgraded") or [])[:25]:
            lines.append(f"  upgraded {e.get('name')} ({e.get('from')} -> {e.get('to')})")
        if len(pac.get("upgraded") or []) > 25:
            lines.append(f"  ... {len(pac['upgraded']) - 25} more upgrades")
        for e in (pac.get("installed") or [])[:10]:
            lines.append(f"  installed {e.get('name')} {e.get('version')}")
        for e in (pac.get("removed") or [])[:10]:
            lines.append(f"  removed {e.get('name')} {e.get('version')}")
    for key in ("vs_previous", "vs_baseline"):
        block = report.get(key) or {}
        lines.append("")
        lines.append(
            f"== {block.get('label')} ({block.get('ref_timestamp') or 'n/a'}) =="
        )
        moved = block.get("moved") or []
        if not moved:
            lines.append("  (no metrics beyond noise)")
        for m in moved:
            lines.append(
                f"  {m['verdict']:13s} {m['pct']:+6.1f}%  "
                f"(noise±{m['noise_pct']:.1f}%)  {m['label']}"
            )
    return "\n".join(lines) + "\n"


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="changes.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write-sidecar", help="Write runs/<ts>.changes.json at run start")
    w.add_argument("--csv", type=Path, required=True)
    w.add_argument("--runs-dir", type=Path, required=True)
    w.add_argument("--ts", required=True)
    w.add_argument("--kernel", default="")
    w.add_argument("--nvidia", default="")
    w.add_argument("--plasma", default="")
    w.add_argument("--mesa", default="")
    w.add_argument("--cpu-temp", default="")
    w.add_argument("--gpu-temp", default="")
    w.add_argument("--cooldown-waited", default="")
    w.add_argument("--app-version", default="")
    w.add_argument("--out", type=Path, default=None)

    r = sub.add_parser("what-changed", help="Print what-changed report")
    r.add_argument("--csv", type=Path, required=True)
    r.add_argument("--runs-dir", type=Path, required=True)
    r.add_argument("--latest", default=None)
    r.add_argument("--baseline", default=None, help="baseline timestamp (default: env or baseline.tag)")
    r.add_argument("--json", action="store_true")

    t = sub.add_parser("read-temps", help="Print CPU,GPU temps")
    args = p.parse_args(argv)

    if args.cmd == "write-sidecar":
        def fnum(s: str) -> float | None:
            s = (s or "").strip()
            if not s:
                return None
            try:
                return float(s)
            except ValueError:
                return None

        path = write_sidecar(
            csv_path=args.csv,
            runs_dir=args.runs_dir,
            ts=args.ts,
            kernel=args.kernel,
            nvidia=args.nvidia,
            plasma=args.plasma,
            mesa=args.mesa,
            cpu_temp=fnum(args.cpu_temp),
            gpu_temp=fnum(args.gpu_temp),
            cooldown_waited_s=fnum(args.cooldown_waited),
            app_version=(args.app_version or None),
            out_path=args.out,
        )
        print(path)
        return 0

    if args.cmd == "what-changed":
        report = build_what_changed(
            load_csv_rows(args.csv),
            latest_ts=args.latest,
            baseline_ts=args.baseline,
            runs_dir=args.runs_dir,
            data_dir=args.csv.parent,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(format_what_changed_text(report))
        return 0

    if args.cmd == "read-temps":
        cpu, gpu = read_start_temps()
        print(f"{'' if cpu is None else cpu},{'' if gpu is None else gpu}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
