"""Shared metric definitions for CachyOS Benchmark (GUI + compare.sh).

One source of truth for labels and higher/lower-is-better direction.
v1 zstd/x264 and v2 zstd/x264 are never compared against each other —
blank values show as n/a, not errors.
"""
from __future__ import annotations

# Ordered list of (csv_column, display_label)
METRICS: list[tuple[str, str]] = [
    ("7z_st", "7-Zip single-thread rating (P-core pinned when st_pinned set)"),
    ("7z_mt", "7-Zip multi-thread rating"),
    ("sysbench_cpu_st_eps", "sysbench CPU 1-thread (events/s)"),
    ("sysbench_cpu_mt_eps", "sysbench CPU multi (events/s)"),
    ("sysbench_mem_MiBps", "Memory bandwidth (MiB/s)"),
    ("zstd_MBps", "zstd v1 compress urandom (MB/s)"),
    ("zstd2_comp_MBps", "zstd v2 compress -b3 -i10 (MB/s)"),
    ("zstd2_decomp_MBps", "zstd v2 decompress -b3 -i10 (MB/s)"),
    ("x264_fps", "x264 v1 encode ultrafast (fps)"),
    ("x264v2_fps", "x264 v2 encode medium 3000f (fps)"),
    ("fio_seq_read_MiBps", "Disk seq read (MiB/s)"),
    ("fio_seq_write_MiBps", "Disk seq write (MiB/s)"),
    ("fio_rand_read_IOPS", "Disk 4k rand read (IOPS)"),
    ("fio_rand_write_IOPS", "Disk 4k rand write (IOPS)"),
    ("vkmark", "vkmark Vulkan score"),
    ("glmark2", "glmark2 OpenGL score"),
    ("cyclictest_max_us", "Latency max µs (non-RT, indicative only; lower better)"),
    ("cpu_temp_max_C", "CPU temp max °C (lower better)"),
    ("gpu_temp_max_C", "GPU temp max °C (lower better)"),
    ("wall_seconds", "Total run time (s)"),
]

HIB: set[str] = {
    "7z_st",
    "7z_mt",
    "sysbench_cpu_st_eps",
    "sysbench_cpu_mt_eps",
    "sysbench_mem_MiBps",
    "zstd_MBps",
    "zstd2_comp_MBps",
    "zstd2_decomp_MBps",
    "x264_fps",
    "x264v2_fps",
    "fio_seq_read_MiBps",
    "fio_seq_write_MiBps",
    "fio_rand_read_IOPS",
    "fio_rand_write_IOPS",
    "vkmark",
    "glmark2",
}

LIB: set[str] = {
    "cyclictest_max_us",
    "cpu_temp_max_C",
    "gpu_temp_max_C",
    "wall_seconds",
}

# Short labels for history table columns
HISTORY_SCORE_COLS: list[tuple[str, str]] = [
    ("7z_mt", "7z MT"),
    ("7z_st", "7z ST"),
    ("sysbench_cpu_mt_eps", "CPU MT"),
    ("sysbench_mem_MiBps", "Mem"),
    ("zstd2_comp_MBps", "zstd2 C"),
    ("x264v2_fps", "x264v2"),
    ("fio_seq_read_MiBps", "Disk R"),
    ("vkmark", "vkmark"),
    ("glmark2", "glmark2"),
    ("wall_seconds", "Wall s"),
]


def higher_is_better(key: str) -> bool:
    return key not in LIB


def parse_num(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().strip('"')
    if s == "" or s.lower() in ("n/a", "none", "parse_failed", "—", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pct_change(baseline: float | None, latest: float | None) -> float | None:
    if baseline is None or latest is None or baseline == 0:
        return None
    return (latest - baseline) / abs(baseline) * 100.0


def is_improved(key: str, baseline: float, latest: float) -> bool:
    if key in LIB:
        return latest < baseline
    return latest > baseline


def compare_note(key: str, baseline: float | None, latest: float | None) -> tuple[str, str]:
    """Return (change_str, note) e.g. ('+2.1%', 'better'). Blank → n/a, not error."""
    if baseline is None and latest is None:
        return ("", "n/a")
    if baseline is None:
        return ("", "new")
    if latest is None:
        return ("", "n/a")
    c = pct_change(baseline, latest)
    if c is None:
        return ("", "n/a")
    ch = f"{c:+.1f}%"
    if abs(c) < 0.05:
        return (ch, "same")
    if is_improved(key, baseline, latest):
        arrow = "↓" if key in LIB else "↑"
        return (ch, f"better {arrow}")
    arrow = "↑" if key in LIB else "↓"
    return (ch, f"worse {arrow}")
