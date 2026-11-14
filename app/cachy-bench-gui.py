#!/usr/bin/env python3
"""CachyOS Benchmark — simple desktop GUI for run / log / compare.

Usage:
  cachy-bench-gui.py                 # launch GUI
  cachy-bench-gui.py --version       # print version and exit
  cachy-bench-gui.py --selftest      # parse CSV / metrics / changes, exit 0
  cachy-bench-gui.py --check-deps    # list missing tools + pacman line
  cachy-bench-gui.py --what-changed  # headless 'What changed' report
  cachy-bench-gui.py --delete-runs TS[,TS...] [--yes] [--allow-baseline]
                                     # remove runs (backs up results.csv, moves logs to runs/trash/)
  cachy-bench-gui.py --undo-delete   # undo the last delete
  cachy-bench-gui.py --temps-once    # print one CPU/GPU temperature reading

Env:
  CACHY_BENCH_DATA  data dir (results.csv, runs/, tmp/); default ~/bench
  CACHY_BENCH_APP   share dir with cachy-bench.sh + app/; default parent of this file
"""
from __future__ import annotations

import csv
import os
import shutil
import signal
import sys
import time
from pathlib import Path

# Ensure app dir is on path for metrics import
APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))


def resolve_layout() -> tuple[Path, Path, Path]:
    """Return (data_root, app_share, bench_script).

    Data always defaults to ~/bench so an AppImage never writes into itself.
    """
    if os.environ.get("CACHY_BENCH_APP"):
        app_share = Path(os.environ["CACHY_BENCH_APP"]).expanduser().resolve()
    else:
        app_share = APP_DIR.parent
    if os.environ.get("CACHY_BENCH_DATA"):
        data_root = Path(os.environ["CACHY_BENCH_DATA"]).expanduser().resolve()
    else:
        data_root = (Path.home() / "bench").resolve()
    return data_root, app_share, app_share / "cachy-bench.sh"


BENCH_ROOT, APP_SHARE, BENCH_SCRIPT = resolve_layout()

from version import APP_NAME, __version__  # noqa: E402

from metrics import (  # noqa: E402
    HISTORY_SCORE_COLS,
    LIB,
    METRICS,
    compare_note,
    higher_is_better,
    is_improved,
    parse_num,
    pct_change,
)
from changes import (  # noqa: E402
    FIXED_BASELINE_TS,
    HOT_START_THRESHOLD_C,
    build_what_changed,
    collect_metric_history,
    format_what_changed_text,
    is_comparable_row,
    metric_delta,
    read_start_temps,
    resolve_baseline_ts,
    tag_excluded,
)
from history_ops import delete_runs, last_delete, plan_delete, undo_last_delete  # noqa: E402
from temps import (  # noqa: E402
    TEMPS_CSV_HEADER,
    format_sample,
    parse_nvidia_smi,
    parse_sensors_json,
    read_temps,
    temp_level,
    temps_csv_line,
)

CSV_PATH = BENCH_ROOT / "results.csv"
BASELINE_FILE = BENCH_ROOT / "baseline.tag"
RUNS_DIR = BENCH_ROOT / "runs"
TMP_DIR = BENCH_ROOT / "tmp"


def active_baseline_ts() -> str:
    """Env override, else <data dir>/baseline.tag (generic; survives AppImage updates)."""
    return resolve_baseline_ts(BENCH_ROOT)

# Host tools (not bundled). pacman package names for CachyOS/Arch.
# Prefer 7zip (provides /usr/bin/7z on modern Arch); p7zip is the older name.
DEP_TOOLS: list[tuple[str, str]] = [
    ("sysbench", "sysbench"),
    ("7z", "7zip"),
    ("zstd", "zstd"),
    ("ffmpeg", "ffmpeg"),
    ("fio", "fio"),
    ("glmark2", "glmark2"),
    ("vkmark", "vkmark"),
    ("cyclictest", "rt-tests"),
    ("sensors", "lm_sensors"),
]

# HiDPI-friendly defaults for 4K @ 2.5 scale (Plasma already scales; keep fonts readable)
DEFAULT_WIDTH = 980
DEFAULT_HEIGHT = 720


def load_rows() -> list[dict]:
    if not CSV_PATH.is_file():
        return []
    with CSV_PATH.open(newline="") as f:
        return list(csv.DictReader(f))


def baseline_timestamp(rows: list[dict]) -> str | None:
    if BASELINE_FILE.is_file():
        ts = BASELINE_FILE.read_text().strip()
        if ts and any(r.get("timestamp") == ts for r in rows):
            return ts
    for r in rows:
        tag = r.get("tag") or ""
        if "baseline" in tag and "smoke" not in tag:
            return r.get("timestamp")
    non_smoke = [r for r in rows if "smoke" not in (r.get("tag") or "")]
    if non_smoke:
        return non_smoke[0].get("timestamp")
    if rows:
        return rows[0].get("timestamp")
    return None


def has_baseline() -> bool:
    rows = load_rows()
    if not rows:
        return False
    if BASELINE_FILE.is_file() and BASELINE_FILE.read_text().strip():
        return True
    return any("baseline" in (r.get("tag") or "") for r in rows)


def row_label(r: dict) -> str:
    ts = r.get("timestamp") or "?"
    tag = (r.get("tag") or "").strip() or "-"
    kern = (r.get("kernel") or "").strip()
    short = kern.split("-")[0] if kern else ""
    return f"{ts}  [{tag}]  {short}".strip()


def detect_busy() -> list[str]:
    """Return human-readable warnings if system looks busy for benchmarking."""
    warnings: list[str] = []
    try:
        load1 = float(Path("/proc/loadavg").read_text().split()[0])
        ncpu = os.cpu_count() or 1
        if load1 > ncpu / 2:
            warnings.append(
                f"Load average is high ({load1:.2f} on {ncpu} CPUs). "
                "Close other heavy apps for a cleaner run."
            )
    except Exception:
        pass

    # Scan process names for games / wine / proton / steam game overlays
    game_hits: set[str] = set()
    try:
        import subprocess

        out = subprocess.check_output(
            ["ps", "-eo", "comm=,args="], text=True, errors="ignore"
        )
        for line in out.splitlines():
            low = line.lower()
            if "proton" in low or "wine" in low or "wineserver" in low:
                game_hits.add("Wine/Proton")
            if "steamapps" in low or "gameoverlay" in low:
                game_hits.add("Steam game")
            # Common game launchers / prefixes
            if "reaper" in low and "steam" in low:
                game_hits.add("Steam game")
            if "boinc" in low.split()[0:1] or "/boinc" in low:
                game_hits.add("BOINC")
    except Exception:
        pass
    if game_hits:
        warnings.append(
            "Detected running: "
            + ", ".join(sorted(game_hits))
            + ". Pause them for more repeatable scores."
        )
    return warnings


def notify(title: str, body: str) -> None:
    try:
        import subprocess

        subprocess.Popen(
            ["notify-send", "--app-name=CachyOS Benchmark", "-i", "utilities-system-monitor", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def check_deps(verbose: bool = True) -> list[tuple[str, str]]:
    """Return list of (binary, pacman_pkg) that are missing from PATH."""
    missing: list[tuple[str, str]] = []
    for binary, pkg in DEP_TOOLS:
        if shutil.which(binary) is None:
            missing.append((binary, pkg))
        elif verbose:
            print(f"OK  {binary:12s} ({pkg}) -> {shutil.which(binary)}")
    if verbose:
        if missing:
            pkgs = " ".join(dict.fromkeys(pkg for _, pkg in missing))
            print("MISSING:")
            for binary, pkg in missing:
                print(f"  - {binary}  (package: {pkg})")
            print(f"Install with:\n  sudo pacman -S {pkgs}")
        else:
            print("All benchmark tools present.")
            print("(optional) taskset from util-linux; nvidia-smi from nvidia-utils")
    return missing


def selftest() -> int:
    """Parse metrics + CSV + noise/changes helpers. No GUI."""
    assert METRICS, "METRICS empty"
    assert LIB, "LIB empty"
    assert higher_is_better("vkmark") is True
    assert higher_is_better("wall_seconds") is False
    ch, note = compare_note("vkmark", 100.0, 110.0)
    assert "better" in note and ch.startswith("+"), (ch, note)
    ch, note = compare_note("wall_seconds", 100.0, 90.0)
    assert "better" in note, (ch, note)
    # Noise-aware: tiny move should be within_noise with default floor
    d = metric_delta("vkmark", "vkmark", 1000.0, 1010.0, [])  # +1% < default 3%
    assert d.verdict == "within_noise", d
    d = metric_delta("vkmark", "vkmark", 1000.0, 1100.0, [])  # +10% > 3%
    assert d.verdict == "better", d
    assert tag_excluded("appimage-test-smoke")
    assert not tag_excluded("baseline")
    assert is_comparable_row({"tag": "baseline"})
    assert not is_comparable_row({"tag": "appimage-test-smoke"})
    rows = load_rows()
    base_ts = FIXED_BASELINE_TS or "selftest-baseline"
    sample = rows or [
        {
            "timestamp": base_ts,
            "tag": "baseline",
            "kernel": "test",
            "vkmark": "1000",
            "wall_seconds": "100",
        },
        {
            "timestamp": "20261101-000000",
            "tag": "after",
            "kernel": "test",
            "vkmark": "1100",
            "wall_seconds": "90",
        },
    ]
    report = build_what_changed(sample, baseline_ts=base_ts, runs_dir=RUNS_DIR)
    assert "vs_previous" in report and "vs_baseline" in report, report.keys()
    _selftest_temps()
    hist_note = _selftest_history()
    print(f"selftest OK — metrics={len(METRICS)} rows={len(rows)} bench_root={BENCH_ROOT}")
    print(f"temps parse OK; history delete/undo OK ({hist_note})")
    print(f"app_share={APP_SHARE}")
    print(f"bench_script_exists={BENCH_SCRIPT.is_file()} csv_exists={CSV_PATH.is_file()}")
    print(f"what_changed_latest={((report.get('latest') or {}).get('timestamp'))}")
    return 0


def _selftest_temps() -> None:
    sj = (
        '{"coretemp-isa-0000": {"Adapter": "ISA adapter",'
        ' "Package id 0": {"temp1_input": 47.0, "temp1_max": 80.0},'
        ' "Core 0": {"temp2_input": 44.0}, "Core 4": {"temp6_input": 52.0}},'
        ' "nvme-pci-0100": {"Composite": {"temp1_input": 39.9}}}'
    )
    assert parse_sensors_json(sj) == 47.0
    no_pkg = '{"coretemp-isa-0000": {"Core 0": {"temp2_input": 44.0}, "Core 1": {"temp3_input": 58.0}}}'
    assert parse_sensors_json(no_pkg) == 58.0
    assert parse_sensors_json('{"k10temp-pci-00c3": {"Tctl": {"temp1_input": 61.5}}}') == 61.5
    assert parse_sensors_json("not json") is None
    assert parse_sensors_json("{}") is None
    assert parse_nvidia_smi("45, 3, 25.10\n") == (45.0, 3.0, 25.1)
    assert parse_nvidia_smi("51, 0, [N/A]") == (51.0, 0.0, None)
    assert parse_nvidia_smi("") == (None, None, None)
    assert temp_level(None) == "none" and temp_level(69.9) == "green"
    assert temp_level(70) == "amber" and temp_level(85) == "amber" and temp_level(85.1) == "red"


def _selftest_history() -> str:
    """Delete/undo round trip in a throwaway dir; never touches the real data dir."""
    import hashlib
    import tempfile

    def sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    notes = []
    with tempfile.TemporaryDirectory(prefix="cachybench-selftest-") as td:
        d = Path(td) / "data"
        (d / "runs").mkdir(parents=True)
        hdr = '"timestamp","tag","vkmark","log_file"\n'
        lines = [hdr]
        for i, ts in enumerate(["20200101-000001", "20200101-000002", "20200101-000003"]):
            lines.append(f'"{ts}","t{i}","{1000 + i}","{d}/runs/{ts}-t{i}.log"\n')
            (d / "runs" / f"{ts}-t{i}.log").write_text("log\n")
            (d / "runs" / f"{ts}.changes.json").write_text("{}\n")
        (d / "runs" / "20200101-000002.temps.csv").write_text(TEMPS_CSV_HEADER)
        csvp = d / "results.csv"
        csvp.write_text("".join(lines))
        orig = sha(csvp)
        res = delete_runs(d, ["20200101-000002", "20200101-000003", "nope"])
        assert res["removed"] == ["20200101-000002", "20200101-000003"], res
        assert res["missing"] == ["nope"], res
        assert Path(res["backup"]).is_file() and sha(Path(res["backup"])) == orig
        assert Path(res["backup"]).name.startswith("results.csv.bak-")
        with csvp.open(newline="") as f:
            left = [r["timestamp"] for r in csv.DictReader(f)]
        assert left == ["20200101-000001"], left
        assert len(res["moved"]) == 5, res["moved"]
        assert all(Path(dst).parent == d / "runs" / "trash" for _, dst in res["moved"])
        assert not (d / "runs" / "20200101-000002.temps.csv").exists()
        assert (d / "runs" / "20200101-000001.changes.json").is_file()
        assert last_delete(d) is not None
        u = undo_last_delete(d)
        assert u and len(u["restored_files"]) == 5, u
        assert sha(csvp) == orig, "undo did not restore results.csv byte-for-byte"
        assert (d / "runs" / "20200101-000003-t2.log").is_file()
        assert last_delete(d) is None
        # Undo with a run added after the delete keeps the new run
        delete_runs(d, ["20200101-000001"])
        with csvp.open("a") as f:
            f.write(f'"20200101-000009","new","1","{d}/runs/x.log"\n')
        u = undo_last_delete(d)
        assert u["kept_new_rows"] == 1, u
        with csvp.open(newline="") as f:
            got = [r["timestamp"] for r in csv.DictReader(f)]
        assert got[:3] == ["20200101-000001", "20200101-000002", "20200101-000003"], got
        assert got[-1] == "20200101-000009", got
        notes.append("fixture")
        # Round trip on a COPY of the real data (read-only for the real dir)
        if CSV_PATH.is_file():
            c = Path(td) / "copy"
            (c / "runs").mkdir(parents=True)
            shutil.copy2(CSV_PATH, c / "results.csv")
            with (c / "results.csv").open(newline="") as f:
                real_rows = list(csv.DictReader(f))
            if real_rows:
                before = sha(c / "results.csv")
                r = delete_runs(c, [real_rows[-1]["timestamp"]])
                assert r["removed"] == [real_rows[-1]["timestamp"]]
                undo_last_delete(c)
                assert sha(c / "results.csv") == before
                notes.append(f"copy of {len(real_rows)} real rows")
    return ", ".join(notes)


def cli_delete_runs(arg: str, assume_yes: bool, allow_baseline: bool) -> int:
    ts_list = [t.strip() for t in arg.split(",") if t.strip()]
    if not ts_list:
        print("--delete-runs needs TS[,TS...]", file=sys.stderr)
        return 2
    plan = plan_delete(BENCH_ROOT, ts_list)
    for t in plan["missing"]:
        print(f"not found: {t}")
    if not plan["found"]:
        print("Nothing to delete.")
        return 1
    bl = active_baseline_ts() or baseline_timestamp(load_rows())
    if bl and bl in plan["found"] and not allow_baseline:
        print(f"Refusing: {bl} is the baseline run. Add --allow-baseline to delete it anyway.")
        return 3
    print(f"Will remove {len(plan['found'])} run(s) from {CSV_PATH}: {', '.join(plan['found'])}")
    print(f"Moves {len(plan['files'])} run file(s) to {RUNS_DIR / 'trash'}; results.csv is backed up first.")
    if not assume_yes:
        if not sys.stdin.isatty():
            print("Not a terminal; re-run with --yes to confirm.")
            return 4
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1
    res = delete_runs(BENCH_ROOT, plan["found"])
    print(f"Removed {len(res['removed'])} run(s). Backup: {res['backup']}")
    print(f"Moved {len(res['moved'])} file(s) to trash. Undo with --undo-delete")
    return 0


def cli_undo_delete() -> int:
    res = undo_last_delete(BENCH_ROOT)
    if not res:
        print("Nothing to undo.")
        return 1
    print(f"Restored {len(res['restored_rows'])} run(s) from {res['from_backup']}")
    print(f"Moved back {len(res['restored_files'])} file(s); skipped {len(res['skipped_files'])}")
    if res["kept_new_rows"]:
        print(f"Kept {res['kept_new_rows']} run(s) added after the delete")
    return 0


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def _short_path(p: Path) -> str:
    """Show the data dir as ~/... in the UI."""
    try:
        return "~/" + str(Path(p).relative_to(Path.home()))
    except ValueError:
        return str(p)


def run_gui(on_ready=None) -> int:
    """Launch the GUI. on_ready(app, win) is a dev hook (screenshots); it disables the offscreen auto-quit."""
    import theme
    from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer, QSize, QThread, Signal
    from PySide6.QtGui import QColor, QFont, QAction, QDesktopServices, QKeySequence, QShortcut
    from PySide6.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QTabWidget,
        QPushButton,
        QCheckBox,
        QLineEdit,
        QLabel,
        QPlainTextEdit,
        QTableWidget,
        QTableWidgetItem,
        QHeaderView,
        QComboBox,
        QMessageBox,
        QAbstractItemView,
        QGroupBox,
        QSizePolicy,
        QScrollArea,
        QFrame,
        QSplitter,
        QMenu,
    )
    from PySide6.QtCore import QUrl
    import collections
    import threading

    # Optional matplotlib
    HAS_MPL = False
    try:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure

        HAS_MPL = True
        theme.apply_mpl_theme()
    except Exception:
        HAS_MPL = False

    TW = theme.widgets()
    Plate, HeaderBar, GaugeDial = TW["Plate"], TW["HeaderBar"], TW["GaugeDial"]
    COL_GOOD = QColor(theme.C["good"])
    COL_BAD = QColor(theme.C["bad"])
    COL_NOISE = QColor(theme.C["noise"])

    class TempPoller(QThread):
        """Reads temps one at a time in a background thread (no pileups).

        Idle while paused; the GUI thread only receives finished samples.
        """

        sample = Signal(object)

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._stop = threading.Event()
            self._wake = threading.Event()
            self._active = False
            self.interval_s = 1.5

        def set_active(self, on: bool) -> None:
            self._active = bool(on)
            if on:
                self._wake.set()

        def stop(self) -> None:
            self._stop.set()
            self._wake.set()

        def run(self) -> None:  # noqa: D401
            while not self._stop.is_set():
                if not self._active:
                    self._wake.wait(1.0)
                    self._wake.clear()
                    continue
                t0 = time.monotonic()
                try:
                    s = read_temps(timeout=1.5)
                except Exception:
                    s = None
                if s is not None and not self._stop.is_set():
                    self.sample.emit(s)
                left = self.interval_s - (time.monotonic() - t0)
                if left > 0:
                    self._wake.wait(left)
                    self._wake.clear()

    class MainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"{APP_NAME} {__version__}")
            self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
            self._proc: QProcess | None = None
            self._run_started: float | None = None
            self._elapsed_timer = QTimer(self)
            self._elapsed_timer.setInterval(500)
            self._elapsed_timer.timeout.connect(self._tick_elapsed)
            self._last_finished_ts: str | None = None

            root = QWidget()
            root.setObjectName("centralRoot")
            root_l = QVBoxLayout(root)
            root_l.setContentsMargins(0, 0, 0, 0)
            root_l.setSpacing(0)
            self.header = HeaderBar(
                APP_NAME,
                "Performance ledger for rolling-release CachyOS",
                f"v{__version__}  ·  BOTLABS USA",
            )
            root_l.addWidget(self.header)
            tabs = QTabWidget()
            tabs.setDocumentMode(True)
            body = QWidget()
            body_l = QVBoxLayout(body)
            body_l.setContentsMargins(14, 10, 14, 12)
            body_l.addWidget(tabs)
            root_l.addWidget(body, stretch=1)
            self.setCentralWidget(root)

            self.run_tab = QWidget()
            self.history_tab = QWidget()
            self.compare_tab = QWidget()
            self.changed_tab = QWidget()
            tabs.addTab(self.run_tab, "Run")
            tabs.addTab(self.history_tab, "History")
            tabs.addTab(self.compare_tab, "Compare")
            tabs.addTab(self.changed_tab, "What changed")
            self._tabs = tabs

            self._build_run_tab()
            self._build_history_tab()
            self._build_compare_tab()
            self._build_changed_tab()

            # Live temperatures (Run tab)
            self._temp_hist: collections.deque = collections.deque(maxlen=400)
            self._peak_cpu: float | None = None
            self._peak_gpu: float | None = None
            self._temps_log_path: Path | None = None
            self._temps_log_start = 0.0
            self._pre_run_ts: set = set()
            self._chart_dirty_at = 0.0
            self._temp_poller = TempPoller(self)
            self._temp_poller.sample.connect(self._on_temp_sample)
            self._temp_poller.start()
            tabs.currentChanged.connect(lambda *_: self._update_temp_polling())
            self._update_temp_polling()

            help_menu = self.menuBar().addMenu("&Help")
            about_act = QAction("About", self)
            about_act.triggered.connect(self._show_about)
            help_menu.addAction(about_act)

            self._refresh_all()
            # Default baseline checkbox if none yet
            if not has_baseline():
                self.baseline_cb.setChecked(True)
                self.status_line.setText("No baseline yet — first run will be marked as baseline.")
            missing = check_deps(verbose=False)
            if missing:
                pkgs = " ".join(dict.fromkeys(pkg for _, pkg in missing))
                self.status_line.setText(
                    f"Missing tools: {', '.join(b for b, _ in missing)}. "
                    f"Install: sudo pacman -S {pkgs}"
                )

        # ---- Run tab ----
        def _build_run_tab(self) -> None:
            layout = QVBoxLayout(self.run_tab)
            layout.setContentsMargins(4, 12, 4, 4)
            layout.setSpacing(12)

            top = QHBoxLayout()
            top.setSpacing(12)
            layout.addLayout(top)

            # Left: controls plate
            ctl = Plate("Run a benchmark")
            ctl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            hint = QLabel(
                f"Runs {BENCH_SCRIPT.name} (about 10 min).  Data \u2192 {_short_path(BENCH_ROOT)}"
            )
            hint.setWordWrap(True)
            hint.setProperty("role", "hint")
            ctl.body.addWidget(hint)

            opts = QHBoxLayout()
            opts.setSpacing(10)
            self.baseline_cb = QCheckBox("Mark as baseline")
            self.tag_edit = QLineEdit()
            self.tag_edit.setPlaceholderText("Optional tag (e.g. after-nvidia-update)")
            self.tag_edit.setClearButtonEnabled(True)
            tag_lab = QLabel("Tag")
            tag_lab.setProperty("role", "caption")
            opts.addWidget(tag_lab)
            opts.addWidget(self.tag_edit, stretch=1)
            opts.addSpacing(8)
            opts.addWidget(self.baseline_cb)
            ctl.body.addLayout(opts)
            ctl.body.addSpacing(4)

            self.run_btn = QPushButton("RUN BENCHMARK")
            self.run_btn.setObjectName("runButton")
            self.run_btn.setMinimumHeight(64)
            self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.run_btn.setFont(theme.display_font(15, 700))
            self.run_btn.clicked.connect(self._on_run)
            ctl.body.addWidget(self.run_btn)

            btns = QHBoxLayout()
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setProperty("kind", "danger")
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.clicked.connect(self._on_cancel)
            self.open_logs_btn = QPushButton("Open logs folder")
            self.open_logs_btn.clicked.connect(self._open_logs)
            btns.addWidget(self.cancel_btn, stretch=1)
            btns.addWidget(self.open_logs_btn, stretch=1)
            ctl.body.addLayout(btns)

            # Logbook summary (read-only; refreshed with History)
            ctl.body.addSpacing(10)
            stats = QHBoxLayout()
            stats.setSpacing(12)
            self._stat_vals = {}
            for key, cap in (("runs", "Runs logged"), ("baseline", "Baseline"), ("latest", "Latest run")):
                cell = QFrame()
                cell.setStyleSheet(
                    f"QFrame {{ background: {theme.C['bg0']}; border: 1px solid #3a2f22; border-radius: 6px; }}"
                    "QLabel { border: none; }"
                )
                cl = QVBoxLayout(cell)
                cl.setContentsMargins(12, 8, 12, 8)
                cl.setSpacing(2)
                c_lab = QLabel(cap.upper())
                c_lab.setProperty("role", "caption")
                v_lab = QLabel("\u2014")
                v_lab.setFont(theme.mono_font(11))
                cl.addWidget(c_lab)
                cl.addWidget(v_lab)
                self._stat_vals[key] = v_lab
                stats.addWidget(cell, stretch=1)
            ctl.body.addLayout(stats)

            ctl.body.addStretch(1)
            self.status_line = QLabel("Ready.")
            self.status_line.setWordWrap(True)
            self.elapsed_label = QLabel("")
            self.elapsed_label.setFont(theme.mono_font(12))
            self.elapsed_label.setStyleSheet(f"color: {theme.C['brass_hi']};")
            status_row = QHBoxLayout()
            st_cap = QLabel("Status")
            st_cap.setProperty("role", "caption")
            status_row.addWidget(st_cap)
            status_row.addWidget(self.status_line, stretch=1)
            status_row.addWidget(self.elapsed_label)
            ctl.body.addLayout(status_row)
            top.addWidget(ctl, stretch=5)

            # Right: gauges plate
            temps_box = Plate("Live temperatures")
            temps_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            grow = QHBoxLayout()
            grow.setSpacing(18)
            self.cpu_gauge = GaugeDial("CPU")
            self.gpu_gauge = GaugeDial("GPU")
            grow.addWidget(self.cpu_gauge)
            grow.addWidget(self.gpu_gauge)
            temps_box.body.addLayout(grow, stretch=1)
            trow = QHBoxLayout()
            # Text readouts kept for accessibility / tooltips; gauges are the main display
            self.cpu_temp_label = QLabel("CPU \u2014")
            self.gpu_temp_label = QLabel("GPU \u2014")
            self.gpu_extra_label = QLabel("")
            for lab in (self.cpu_temp_label, self.gpu_temp_label, self.gpu_extra_label):
                lab.setProperty("role", "hint")
            reset_peak = QPushButton("Reset peak")
            reset_peak.clicked.connect(self._reset_temp_peaks)
            trow.addWidget(self.cpu_temp_label)
            trow.addSpacing(18)
            trow.addWidget(self.gpu_temp_label)
            trow.addSpacing(12)
            trow.addWidget(self.gpu_extra_label)
            trow.addStretch(1)
            trow.addWidget(reset_peak)
            temps_box.body.addLayout(trow)
            top.addWidget(temps_box, stretch=6)

            bottom = QSplitter(Qt.Orientation.Horizontal)
            bottom.setChildrenCollapsible(False)
            layout.addWidget(bottom, stretch=1)

            chart_plate = Plate("Temperature trace \u2014 last 5 minutes")
            if HAS_MPL:
                self.temp_fig = Figure(figsize=(8, 2.2), dpi=100)
                self.temp_canvas = FigureCanvas(self.temp_fig)
                self.temp_canvas.setMinimumHeight(160)
                self.temp_ax = self.temp_fig.add_subplot(111)
                theme.style_axes(self.temp_ax)
                (self.cpu_line,) = self.temp_ax.plot([], [], color=theme.C["copper_hi"], linewidth=1.8, label="CPU")
                (self.gpu_line,) = self.temp_ax.plot([], [], color=theme.C["teal_hi"], linewidth=1.8, label="GPU")
                self.temp_ax.axhspan(70, 85, color=theme.C["amber"], alpha=0.08, linewidth=0)
                self.temp_ax.axhspan(85, 140, color=theme.C["red"], alpha=0.10, linewidth=0)
                self.temp_ax.axhline(70, color=theme.C["amber"], alpha=0.45, linewidth=0.8, linestyle=(0, (4, 3)))
                self.temp_ax.axhline(85, color=theme.C["red"], alpha=0.55, linewidth=0.8, linestyle=(0, (4, 3)))
                self.temp_ax.set_xlim(-300, 0)
                self.temp_ax.set_ylim(20, 100)
                self.temp_ax.set_xlabel("seconds", fontsize=8)
                self.temp_ax.set_ylabel("\u00b0C", fontsize=8)
                self.temp_ax.tick_params(labelsize=8)
                self.temp_ax.legend(loc="upper left", fontsize=8, ncol=2)
                self.temp_fig.tight_layout(pad=0.6)
                chart_plate.body.addWidget(self.temp_canvas)
            else:
                self.temp_fig = None
                self.temp_canvas = None
                lab = QLabel("Chart skipped (matplotlib unavailable).")
                lab.setProperty("role", "hint")
                chart_plate.body.addWidget(lab)
            bottom.addWidget(chart_plate)

            log_plate = Plate("Console")
            self.log_pane = QPlainTextEdit()
            self.log_pane.setObjectName("console")
            self.log_pane.setReadOnly(True)
            self.log_pane.setFont(theme.mono_font())
            self.log_pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.log_pane.setPlaceholderText("Benchmark output appears here.")
            log_plate.body.addWidget(self.log_pane)
            bottom.addWidget(log_plate)
            bottom.setSizes([3000, 2000])

        # ---- History tab ----
        def _build_history_tab(self) -> None:
            outer = QVBoxLayout(self.history_tab)
            outer.setContentsMargins(4, 12, 4, 4)
            plate = Plate("Run ledger \u2014 results.csv")
            outer.addWidget(plate)
            layout = plate.body
            top = QHBoxLayout()
            hl = QLabel("All runs, newest first. Tick runs to delete, or pick one as the baseline.")
            hl.setProperty("role", "hint")
            top.addWidget(hl)
            top.addStretch(1)
            refresh = QPushButton("Refresh")
            refresh.clicked.connect(self._refresh_history)
            top.addWidget(refresh)
            set_bl = QPushButton("Set selected as baseline")
            set_bl.setProperty("kind", "brass")
            set_bl.clicked.connect(self._set_selected_baseline)
            top.addWidget(set_bl)
            layout.addLayout(top)

            sel_row = QHBoxLayout()
            self.sel_all_btn = QPushButton("Select all / none")
            self.sel_all_btn.clicked.connect(self._toggle_select_all)
            self.del_btn = QPushButton("Delete selected")
            self.del_btn.setProperty("kind", "danger")
            self.del_btn.clicked.connect(self._delete_selected)
            self.undo_btn = QPushButton("Undo last delete")
            self.undo_btn.clicked.connect(self._undo_delete)
            self.sel_count = QLabel("")
            self.sel_count.setStyleSheet("color: palette(mid);")
            sel_row.addWidget(self.sel_all_btn)
            sel_row.addWidget(self.del_btn)
            sel_row.addWidget(self.undo_btn)
            sel_row.addWidget(self.sel_count, stretch=1)
            layout.addLayout(sel_row)

            cols = ["", "Date", "Tag", "Baseline", "Kernel", "NVIDIA", "Plasma"] + [
                c[1] for c in HISTORY_SCORE_COLS
            ]
            self.hist_table = QTableWidget(0, len(cols))
            self.hist_table.setHorizontalHeaderLabels(cols)
            self.hist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.hist_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            self.hist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.hist_table.setSortingEnabled(True)
            self.hist_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.hist_table.horizontalHeader().setStretchLastSection(True)
            self.hist_table.verticalHeader().setVisible(False)
            self.hist_table.setAlternatingRowColors(True)
            self.hist_table.setShowGrid(False)
            self.hist_table.verticalHeader().setDefaultSectionSize(34)
            self.hist_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.hist_table.customContextMenuRequested.connect(self._history_menu)
            self.hist_table.itemChanged.connect(lambda *_: self._update_sel_count())
            del_sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.hist_table)
            del_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            del_sc.activated.connect(self._delete_selected)
            layout.addWidget(self.hist_table, stretch=1)
            hint = QLabel(
                "Tick runs (or select rows) and press Delete. results.csv is backed up first "
                "and logs move to runs/trash/, so Undo can put them back."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(mid);")
            layout.addWidget(hint)

        # ---- Compare tab ----
        def _build_compare_tab(self) -> None:
            outer = QVBoxLayout(self.compare_tab)
            outer.setContentsMargins(4, 12, 4, 4)
            outer.setSpacing(12)
            sel_plate = Plate("Select two runs")
            outer.addWidget(sel_plate)
            layout = sel_plate.body
            pick = QHBoxLayout()
            pick.setSpacing(10)
            cap_a = QLabel("A  baseline")
            cap_a.setProperty("role", "caption")
            pick.addWidget(cap_a)
            self.combo_a = QComboBox()
            self.combo_a.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            pick.addWidget(self.combo_a, stretch=1)
            pick.addSpacing(8)
            cap_b = QLabel("B  compare")
            cap_b.setProperty("role", "caption")
            pick.addWidget(cap_b)
            self.combo_b = QComboBox()
            self.combo_b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            pick.addWidget(self.combo_b, stretch=1)
            go = QPushButton("Compare")
            go.setProperty("kind", "brass")
            go.setMinimumWidth(130)
            go.clicked.connect(self._do_compare)
            pick.addWidget(go)
            layout.addLayout(pick)

            self.compare_meta = QLabel("")
            self.compare_meta.setWordWrap(True)
            self.compare_meta.setProperty("role", "meta")
            layout.addWidget(self.compare_meta)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.setChildrenCollapsible(False)
            outer.addWidget(splitter, stretch=1)

            self.compare_table = QTableWidget(0, 5)
            self.compare_table.setHorizontalHeaderLabels(
                ["Metric", "A", "B", "Change", "Note"]
            )
            self.compare_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.compare_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Stretch
            )
            for i in range(1, 5):
                self.compare_table.horizontalHeader().setSectionResizeMode(
                    i, QHeaderView.ResizeMode.ResizeToContents
                )
            self.compare_table.verticalHeader().setVisible(False)
            self.compare_table.setAlternatingRowColors(True)
            self.compare_table.setShowGrid(False)
            self.compare_table.verticalHeader().setDefaultSectionSize(32)
            tplate = Plate("All metrics, noise-aware")
            tplate.body.addWidget(self.compare_table)
            splitter.addWidget(tplate)

            self.chart_container = Plate("Change vs A  (better \u2192 right)")
            self.chart_layout = self.chart_container.body
            if HAS_MPL:
                self.figure = Figure(figsize=(6, 6), dpi=100)
                self.canvas = FigureCanvas(self.figure)
                self.chart_layout.addWidget(self.canvas)
            else:
                self.figure = None
                self.canvas = None
                lab = QLabel("Bar chart skipped (matplotlib unavailable).")
                lab.setStyleSheet("color: palette(mid);")
                self.chart_layout.addWidget(lab)
            splitter.addWidget(self.chart_container)
            splitter.setSizes([5000, 4000])

            self.combo_a.currentIndexChanged.connect(lambda *_: None)
            # Auto-compare when both populated via refresh


        # ---- What changed tab ----
        def _build_changed_tab(self) -> None:
            outer = QVBoxLayout(self.changed_tab)
            outer.setContentsMargins(4, 12, 4, 4)
            outer.setSpacing(12)
            sum_plate = Plate("Latest run")
            outer.addWidget(sum_plate)
            layout = sum_plate.body
            top = QHBoxLayout()
            intro = QLabel(
                f"Latest vs previous comparable run, and vs baseline {active_baseline_ts() or '(mark a run with --baseline)'}. "
                "Red/green only when change exceeds estimated noise."
            )
            intro.setProperty("role", "hint")
            intro.setWordWrap(True)
            top.addWidget(intro, stretch=1)
            top.addStretch(1)
            refresh = QPushButton("Refresh")
            refresh.clicked.connect(self._refresh_what_changed)
            top.addWidget(refresh)
            layout.addLayout(top)

            self.changed_meta = QLabel("")
            self.changed_meta.setWordWrap(True)
            self.changed_meta.setProperty("role", "meta")
            layout.addWidget(self.changed_meta)

            self.changed_hot = QLabel("")
            self.changed_hot.setWordWrap(True)
            layout.addWidget(self.changed_hot)

            self.changed_pkgs = QPlainTextEdit()
            self.changed_pkgs.setReadOnly(True)
            self.changed_pkgs.setMinimumHeight(110)
            self.changed_pkgs.setMaximumHeight(150)
            self.changed_pkgs.setObjectName("console")
            self.changed_pkgs.setFont(theme.mono_font())
            pkg_plate = Plate("Package changes \u2014 pacman.log since previous run")
            pkg_plate.body.addWidget(self.changed_pkgs)
            outer.addWidget(pkg_plate)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.setChildrenCollapsible(False)
            outer.addWidget(splitter, stretch=1)

            left = Plate("Vs previous run")
            left_l = left.body
            self.changed_prev_table = QTableWidget(0, 5)
            self.changed_prev_table.setHorizontalHeaderLabels(
                ["Metric", "Prev", "Latest", "Change", "Note"]
            )
            self.changed_prev_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.changed_prev_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Stretch
            )
            for i in range(1, 5):
                self.changed_prev_table.horizontalHeader().setSectionResizeMode(
                    i, QHeaderView.ResizeMode.ResizeToContents
                )
            self.changed_prev_table.verticalHeader().setVisible(False)
            self.changed_prev_table.setAlternatingRowColors(True)
            self.changed_prev_table.setShowGrid(False)
            self.changed_prev_table.verticalHeader().setDefaultSectionSize(30)
            left_l.addWidget(self.changed_prev_table)
            splitter.addWidget(left)

            right = Plate(f"Vs baseline {active_baseline_ts() or '(none set)'}")
            right_l = right.body
            self.changed_base_table = QTableWidget(0, 5)
            self.changed_base_table.setHorizontalHeaderLabels(
                ["Metric", "Base", "Latest", "Change", "Note"]
            )
            self.changed_base_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.changed_base_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Stretch
            )
            for i in range(1, 5):
                self.changed_base_table.horizontalHeader().setSectionResizeMode(
                    i, QHeaderView.ResizeMode.ResizeToContents
                )
            self.changed_base_table.verticalHeader().setVisible(False)
            self.changed_base_table.setAlternatingRowColors(True)
            self.changed_base_table.setShowGrid(False)
            self.changed_base_table.verticalHeader().setDefaultSectionSize(30)
            right_l.addWidget(self.changed_base_table)
            splitter.addWidget(right)

            self.changed_notes = QLabel("")
            self.changed_notes.setWordWrap(True)
            self.changed_notes.setStyleSheet("color: palette(mid);")
            outer.addWidget(self.changed_notes)

        def _fill_moved_table(self, table: QTableWidget, block: dict) -> None:
            green, red, grey = COL_GOOD, COL_BAD, COL_NOISE
            table.setRowCount(0)
            metrics = block.get("metrics") or []
            order = {"better": 0, "worse": 1, "within_noise": 2, "new": 3, "n/a": 4}
            metrics = sorted(metrics, key=lambda m: order.get(m.get("verdict"), 9))
            for m in metrics:
                row_i = table.rowCount()
                table.insertRow(row_i)
                pct = m.get("pct")
                ch = "" if pct is None else f"{pct:+.1f}%"
                noise = m.get("noise_pct")
                note = m.get("note") or m.get("verdict") or ""
                if noise is not None and m.get("verdict") == "within_noise":
                    note = f"within noise (±{noise:.1f}%)"
                elif noise is not None and m.get("verdict") in ("better", "worse"):
                    note = f"{note} (noise ±{noise:.1f}%)"
                cells = [
                    m.get("label") or m.get("key") or "",
                    f"{m['baseline']:.2f}" if m.get("baseline") is not None else "—",
                    f"{m['latest']:.2f}" if m.get("latest") is not None else "—",
                    ch,
                    note,
                ]
                verdict = m.get("verdict")
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if col in (3, 4):
                        if verdict == "better":
                            item.setForeground(green)
                        elif verdict == "worse":
                            item.setForeground(red)
                        else:
                            item.setForeground(grey)
                    table.setItem(row_i, col, item)

        def _refresh_what_changed(self) -> None:
            rows = load_rows()
            report = build_what_changed(
                rows, baseline_ts=active_baseline_ts(), runs_dir=RUNS_DIR, data_dir=BENCH_ROOT
            )
            latest = report.get("latest") or {}
            vers = latest.get("versions") or {}
            self.changed_meta.setText(
                f"Latest: {latest.get('timestamp') or 'n/a'}  tag={latest.get('tag') or '-'}  "
                f"kernel={vers.get('kernel')}  nvidia={vers.get('nvidia_driver')}  "
                f"plasma={vers.get('plasma')}  mesa={vers.get('mesa')}"
            )
            hot = report.get("hot_start") or {}
            if hot:
                flag = "HOT START" if hot.get("warned") else "ok"
                extra = ""
                if hot.get("cooldown_waited_s") is not None:
                    extra = f"  cooldown waited {hot.get('cooldown_waited_s')}s"
                self.changed_hot.setText(
                    f"Hot-start: {flag} — CPU={hot.get('cpu_temp_C')}°C  "
                    f"GPU={hot.get('gpu_temp_C')}°C  (threshold {hot.get('threshold_C')}°C)"
                    + extra
                )
                if hot.get("warned"):
                    self.changed_hot.setStyleSheet(f"color: {theme.C['bad']}; font-weight: bold;")
                else:
                    self.changed_hot.setStyleSheet("color: palette(mid);")
            else:
                self.changed_hot.setText(
                    "Hot-start: (no sidecar yet for latest run — recorded on next bench start)"
                )
                self.changed_hot.setStyleSheet("color: palette(mid);")

            pac = report.get("pacman") or {}
            pkg_lines: list[str] = []
            if pac.get("error"):
                pkg_lines.append(f"(pacman.log: {pac['error']})")
            elif not pac:
                pkg_lines.append("(no package delta available)")
            else:
                pkg_lines.append(
                    f"Since {pac.get('since_timestamp') or '?'}: "
                    f"installed={len(pac.get('installed') or [])}  "
                    f"upgraded={len(pac.get('upgraded') or [])}  "
                    f"removed={len(pac.get('removed') or [])}"
                )
                for e in pac.get("upgraded") or []:
                    pkg_lines.append(
                        f"  upgraded  {e.get('name')}  ({e.get('from')} -> {e.get('to')})"
                    )
                for e in pac.get("installed") or []:
                    pkg_lines.append(f"  installed {e.get('name')}  {e.get('version')}")
                for e in pac.get("removed") or []:
                    pkg_lines.append(f"  removed   {e.get('name')}  {e.get('version')}")
            self.changed_pkgs.setPlainText("\n".join(pkg_lines) if pkg_lines else "(none)")

            self._fill_moved_table(self.changed_prev_table, report.get("vs_previous") or {})
            self._fill_moved_table(self.changed_base_table, report.get("vs_baseline") or {})
            notes = report.get("notes") or []
            self.changed_notes.setText("  ·  ".join(notes))

        # ---- helpers ----
        def _append_log(self, text: str) -> None:
            self.log_pane.moveCursor(self.log_pane.textCursor().MoveOperation.End)
            self.log_pane.insertPlainText(text)
            self.log_pane.moveCursor(self.log_pane.textCursor().MoveOperation.End)

        def _open_logs(self) -> None:
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(RUNS_DIR)))

        def _tick_elapsed(self) -> None:
            if self._run_started is None:
                return
            secs = int(time.time() - self._run_started)
            m, s = divmod(secs, 60)
            self.elapsed_label.setText(f"Elapsed {m:02d}:{s:02d}")

        def _on_run(self) -> None:
            if self._proc is not None:
                return
            if not BENCH_SCRIPT.is_file():
                QMessageBox.critical(self, "Missing script", f"Not found:\n{BENCH_SCRIPT}")
                return

            warns = detect_busy()
            if warns:
                msg = "\n\n".join(warns) + "\n\nContinue anyway?"
                reply = QMessageBox.warning(
                    self,
                    "System looks busy",
                    msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            use_cooldown = False
            cpu_t, gpu_t = read_start_temps()
            hot_bits = []
            if cpu_t is not None and cpu_t > HOT_START_THRESHOLD_C:
                hot_bits.append(f"CPU {cpu_t:.0f}°C")
            if gpu_t is not None and gpu_t > HOT_START_THRESHOLD_C:
                hot_bits.append(f"GPU {gpu_t:.0f}°C")
            if hot_bits:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setWindowTitle("Hot start")
                box.setText(
                    f"Temps above ~{HOT_START_THRESHOLD_C:.0f}°C at start: "
                    + ", ".join(hot_bits)
                    + ".\nScores may be noisy until the machine cools."
                )
                box.setInformativeText(
                    "Wait for cooldown (up to 5 minutes), continue immediately, or cancel?"
                )
                wait_btn = box.addButton("Wait for cooldown", QMessageBox.ButtonRole.AcceptRole)
                cont_btn = box.addButton("Continue", QMessageBox.ButtonRole.DestructiveRole)
                cancel_btn = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(wait_btn)
                box.exec()
                clicked = box.clickedButton()
                if clicked == cancel_btn:
                    return
                if clicked == wait_btn:
                    use_cooldown = True
                    self._append_log(
                        f"Hot-start warning: {', '.join(hot_bits)} — requesting --cooldown\n"
                    )

            args: list[str] = []
            if self.baseline_cb.isChecked():
                args.append("--baseline")
            tag = self.tag_edit.text().strip()
            if tag:
                args.extend(["--tag", tag])
            if use_cooldown:
                args.append("--cooldown")

            self.log_pane.clear()
            self._append_log(f"$ {BENCH_SCRIPT} {' '.join(args)}\n\n")
            self.status_line.setText("Running…")
            self.run_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)
            self._run_started = time.time()
            self.elapsed_label.setText("Elapsed 00:00")
            self._elapsed_timer.start()

            TMP_DIR.mkdir(parents=True, exist_ok=True)
            RUNS_DIR.mkdir(parents=True, exist_ok=True)

            proc = QProcess(self)
            # New process group so Cancel can kill children (fio, stress-ng, etc.)
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            env = QProcessEnvironment.systemEnvironment()
            env.insert("CACHY_BENCH_DATA", str(BENCH_ROOT))
            env.insert("CACHY_BENCH_APP", str(APP_SHARE))
            proc.setProcessEnvironment(env)
            proc.setWorkingDirectory(str(BENCH_ROOT))
            proc.readyReadStandardOutput.connect(self._on_proc_output)
            proc.finished.connect(self._on_proc_finished)
            # startDetached would break streaming; use start with setsid via bash wrapper
            # Wrap in bash -c with setsid so we own the process group
            cmd = ["setsid", str(BENCH_SCRIPT), *args]
            proc.start(cmd[0], cmd[1:])
            if not proc.waitForStarted(5000):
                self._elapsed_timer.stop()
                self.run_btn.setEnabled(True)
                self.cancel_btn.setEnabled(False)
                self.status_line.setText("Failed to start.")
                QMessageBox.critical(
                    self,
                    "Start failed",
                    proc.errorString() or "Could not start benchmark script.",
                )
                self._proc = None
                return
            self._proc = proc
            self._start_temps_log()
            self._update_temp_polling()

        def _on_proc_output(self) -> None:
            if self._proc is None:
                return
            data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
            if data:
                self._append_log(data)

        def _on_cancel(self) -> None:
            if self._proc is None:
                return
            self.status_line.setText("Cancelling…")
            pid = int(self._proc.processId())
            try:
                # Kill the whole process group started by setsid
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception:
                self._proc.terminate()
            # Give it a moment, then SIGKILL the group
            QTimer.singleShot(2000, lambda: self._force_kill(pid))

        def _force_kill(self, pid: int) -> None:
            if self._proc is None:
                return
            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._cleanup_tmp()

        def _cleanup_tmp(self) -> None:
            try:
                import shutil

                if TMP_DIR.is_dir():
                    for p in TMP_DIR.iterdir():
                        try:
                            if p.is_file() or p.is_symlink():
                                p.unlink()
                            elif p.is_dir():
                                shutil.rmtree(p, ignore_errors=True)
                        except Exception:
                            pass
            except Exception:
                pass

        def _on_proc_finished(self, exit_code: int, _status) -> None:
            self._elapsed_timer.stop()
            self.cancel_btn.setEnabled(False)
            self.run_btn.setEnabled(True)
            self._proc = None
            self._finish_temps_log()
            self._update_temp_polling()
            cancelled = exit_code in (-15, -9) or exit_code == 143
            # Also treat SIGTERM from killpg
            if cancelled or (self.status_line.text().startswith("Cancelling")):
                self._cleanup_tmp()
                self.status_line.setText("Cancelled.")
                self._append_log("\n--- cancelled ---\n")
                notify("CachyOS Benchmark", "Run cancelled.")
                self._refresh_all()
                return

            if exit_code == 0:
                self.status_line.setText("Finished successfully.")
                self._append_log("\n--- done ---\n")
                notify("CachyOS Benchmark", "Benchmark finished. Opening Compare.")
            else:
                self.status_line.setText(f"Finished with exit code {exit_code}.")
                self._append_log(f"\n--- exit {exit_code} ---\n")
                notify("CachyOS Benchmark", f"Benchmark exited with code {exit_code}.")

            self._refresh_all()
            # Auto-switch to Compare: baseline vs latest
            rows = load_rows()
            if rows:
                latest_ts = rows[-1].get("timestamp")
                self._last_finished_ts = latest_ts
                self._select_compare_pair(
                    baseline_timestamp(rows), latest_ts
                )
                self._do_compare()
                self._tabs.setCurrentWidget(self.compare_tab)

        def _refresh_all(self) -> None:
            self._refresh_history()
            self._refresh_compare_combos()
            self._refresh_what_changed()

        def _refresh_history(self) -> None:
            rows = load_rows()
            bl = baseline_timestamp(rows)
            self.hist_table.blockSignals(True)
            self.hist_table.setSortingEnabled(False)
            self.hist_table.setRowCount(0)
            # Newest first for display
            for r in reversed(rows):
                row_i = self.hist_table.rowCount()
                self.hist_table.insertRow(row_i)
                chk = QTableWidgetItem("")
                chk.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                chk.setCheckState(Qt.CheckState.Unchecked)
                chk.setData(Qt.ItemDataRole.UserRole, r.get("timestamp"))
                self.hist_table.setItem(row_i, 0, chk)
                vals = [
                    r.get("timestamp") or "",
                    r.get("tag") or "",
                    "✓" if r.get("timestamp") == bl else "",
                    r.get("kernel") or "",
                    r.get("nvidia_driver") or "",
                    r.get("plasma") or "",
                ]
                for key, _lab in HISTORY_SCORE_COLS:
                    v = parse_num(r.get(key))
                    vals.append(f"{v:.1f}" if v is not None else "—")
                for col, val in enumerate(vals, start=1):
                    item = QTableWidgetItem(val)
                    if col == 1:
                        item.setData(Qt.ItemDataRole.UserRole, r.get("timestamp"))
                    # Numeric sort for score cols
                    if col >= 7:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                        num = parse_num(val if val != "—" else None)
                        if num is not None:
                            item.setData(Qt.ItemDataRole.UserRole, num)
                    self.hist_table.setItem(row_i, col, item)
            self.hist_table.setSortingEnabled(True)
            self.hist_table.blockSignals(False)
            self._update_sel_count()
            if hasattr(self, "_stat_vals"):
                self._stat_vals["runs"].setText(str(len(rows)))
                self._stat_vals["baseline"].setText(bl or "\u2014")
                self._stat_vals["latest"].setText((rows[-1].get("timestamp") or "\u2014") if rows else "\u2014")

        def _set_selected_baseline(self) -> None:
            items = self.hist_table.selectedItems()
            if not items:
                QMessageBox.information(self, "Select a run", "Select a row in History first.")
                return
            row = items[0].row()
            ts_item = self.hist_table.item(row, 1)
            if not ts_item:
                return
            ts = ts_item.text().strip()
            if not ts:
                return
            BASELINE_FILE.write_text(ts + "\n")
            self._refresh_all()
            self.status_line.setText(f"Baseline set to {ts}")

        # ---- History selection / delete ----
        def _checked_ts(self) -> list[str]:
            out = []
            for i in range(self.hist_table.rowCount()):
                it = self.hist_table.item(i, 0)
                if it is not None and it.checkState() == Qt.CheckState.Checked:
                    ts = it.data(Qt.ItemDataRole.UserRole)
                    if ts:
                        out.append(ts)
            return out

        def _targets(self) -> list[str]:
            """Checked runs; if none are ticked, the highlighted rows."""
            ticked = self._checked_ts()
            if ticked:
                return ticked
            rows = sorted({ix.row() for ix in self.hist_table.selectionModel().selectedRows()})
            out = []
            for r in rows:
                it = self.hist_table.item(r, 1)
                if it is not None and it.text().strip():
                    out.append(it.text().strip())
            return out

        def _update_sel_count(self) -> None:
            n = len(self._checked_ts())
            self.sel_count.setText(f"{n} ticked" if n else "")
            self.undo_btn.setEnabled(last_delete(BENCH_ROOT) is not None)

        def _toggle_select_all(self) -> None:
            n = self.hist_table.rowCount()
            if not n:
                return
            all_on = len(self._checked_ts()) == n
            state = Qt.CheckState.Unchecked if all_on else Qt.CheckState.Checked
            self.hist_table.blockSignals(True)
            for i in range(n):
                it = self.hist_table.item(i, 0)
                if it is not None:
                    it.setCheckState(state)
            self.hist_table.blockSignals(False)
            self._update_sel_count()

        def _history_menu(self, pos) -> None:
            menu = QMenu(self)
            a_del = menu.addAction("Delete selected…")
            a_bl = menu.addAction("Set as baseline")
            menu.addSeparator()
            a_all = menu.addAction("Select all / none")
            a_undo = menu.addAction("Undo last delete")
            a_undo.setEnabled(last_delete(BENCH_ROOT) is not None)
            chosen = menu.exec(self.hist_table.viewport().mapToGlobal(pos))
            if chosen == a_del:
                self._delete_selected()
            elif chosen == a_bl:
                self._set_selected_baseline()
            elif chosen == a_all:
                self._toggle_select_all()
            elif chosen == a_undo:
                self._undo_delete()

        def _delete_selected(self) -> None:
            ts_list = self._targets()
            if not ts_list:
                QMessageBox.information(self, "Nothing selected", "Tick or select runs to delete first.")
                return
            if self._proc is not None:
                QMessageBox.information(self, "Benchmark running", "Wait for the running benchmark to finish first.")
                return
            plan = plan_delete(BENCH_ROOT, ts_list)
            found = plan["found"]
            if not found:
                return
            preview = "\n".join(found[:12]) + (f"\n… and {len(found) - 12} more" if len(found) > 12 else "")
            reply = QMessageBox.question(
                self,
                "Delete runs",
                f"Remove {len(found)} run(s) from History?\n\n{preview}\n\n"
                f"results.csv is backed up first and {len(plan['files'])} log/sidecar file(s) "
                "move to runs/trash/. You can undo this.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            bl = active_baseline_ts() or baseline_timestamp(load_rows())
            if bl and bl in found:
                reply = QMessageBox.warning(
                    self,
                    "Baseline selected",
                    f"{bl} is your baseline run. Comparisons against the baseline will be empty "
                    "until you set a new one or undo.\n\nDelete the baseline too?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            try:
                res = delete_runs(BENCH_ROOT, found)
            except Exception as e:  # nothing removed if backup failed
                QMessageBox.critical(self, "Delete failed", str(e))
                return
            self._refresh_all()
            self.status_line.setText(
                f"Deleted {len(res['removed'])} run(s); backup {Path(res['backup']).name}"
            )
            QMessageBox.information(
                self, "Deleted",
                f"Removed {len(res['removed'])} run(s).\nBackup: {res['backup']}\n"
                f"Moved {len(res['moved'])} file(s) to runs/trash/.",
            )

        def _undo_delete(self) -> None:
            m = last_delete(BENCH_ROOT)
            if not m:
                QMessageBox.information(self, "Undo", "Nothing to undo.")
                return
            n = len(m.get("removed") or [])
            reply = QMessageBox.question(
                self, "Undo last delete",
                f"Restore {n} run(s) from {Path(m['backup']).name} and move their files back?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                res = undo_last_delete(BENCH_ROOT)
            except Exception as e:
                QMessageBox.critical(self, "Undo failed", str(e))
                return
            self._refresh_all()
            if res:
                self.status_line.setText(f"Restored {len(res['restored_rows'])} run(s).")

        # ---- Live temperatures ----
        def _update_temp_polling(self) -> None:
            on_run_tab = self._tabs.currentWidget() is self.run_tab
            active = on_run_tab or self._proc is not None
            # Slower during a benchmark to keep overhead negligible
            self._temp_poller.interval_s = 2.0 if self._proc is not None else 1.5
            self._temp_poller.set_active(active and self.isVisible() or (self._proc is not None))

        def showEvent(self, event) -> None:  # noqa: N802
            super().showEvent(event)
            if hasattr(self, "_temp_poller"):
                self._update_temp_polling()

        def _reset_temp_peaks(self) -> None:
            self._peak_cpu = None
            self._peak_gpu = None
            if hasattr(self, "cpu_gauge"):
                self.cpu_gauge.set_reading(self.cpu_gauge.value(), None)
                self.gpu_gauge.set_reading(self.gpu_gauge.value(), None)

        def _style_for(self, t: float | None) -> str:
            return {
                "green": f"color: {theme.C['good']};",
                "amber": f"color: {theme.C['amber']};",
                "red": f"color: {theme.C['red']};",
            }.get(temp_level(t), "color: palette(mid);")

        def _on_temp_sample(self, s) -> None:
            if s.cpu_c is not None:
                self._peak_cpu = s.cpu_c if self._peak_cpu is None else max(self._peak_cpu, s.cpu_c)
            if s.gpu_c is not None:
                self._peak_gpu = s.gpu_c if self._peak_gpu is None else max(self._peak_gpu, s.gpu_c)
            self._temp_hist.append((s.t, s.cpu_c, s.gpu_c))
            # Log during a run (separate file; results.csv untouched)
            if self._temps_log_path is not None:
                try:
                    with self._temps_log_path.open("a") as f:
                        f.write(temps_csv_line(s, self._temps_log_start))
                except OSError:
                    self._temps_log_path = None
            def fmt(v):
                return "—" if v is None else f"{v:.0f}°C"
            if s.cpu_c is None and "missing" in (s.cpu_source or ""):
                self.cpu_temp_label.setText("CPU — (install lm_sensors)")
                self.cpu_gauge.set_reading(None, self._peak_cpu, note="INSTALL LM_SENSORS")
            else:
                self.cpu_temp_label.setText(f"CPU {fmt(s.cpu_c)}  (peak {fmt(self._peak_cpu)})")
                self.cpu_gauge.set_reading(s.cpu_c, self._peak_cpu)
            self.cpu_temp_label.setStyleSheet(self._style_for(s.cpu_c))
            if s.gpu_source == "no nvidia-smi":
                self.gpu_temp_label.setText("GPU — (no nvidia-smi)")
                self.gpu_extra_label.setText("")
                self.gpu_gauge.set_reading(None, self._peak_gpu, note="NO NVIDIA-SMI")
            else:
                self.gpu_temp_label.setText(f"GPU {fmt(s.gpu_c)}  (peak {fmt(self._peak_gpu)})")
                bits = []
                if s.gpu_util is not None:
                    bits.append(f"{s.gpu_util:.0f}% util")
                if s.gpu_power_w is not None:
                    bits.append(f"{s.gpu_power_w:.0f} W")
                self.gpu_extra_label.setText("  ".join(bits))
                self.gpu_gauge.set_reading(s.gpu_c, self._peak_gpu, extra="   ".join(bits).upper())
            self.gpu_temp_label.setStyleSheet(self._style_for(s.gpu_c))
            if self.temp_canvas is not None and self._tabs.currentWidget() is self.run_tab:
                self._redraw_temp_chart(s.t)

        def _redraw_temp_chart(self, now: float) -> None:
            pts = [p for p in self._temp_hist if now - p[0] <= 300]
            xs_c = [p[0] - now for p in pts if p[1] is not None]
            ys_c = [p[1] for p in pts if p[1] is not None]
            xs_g = [p[0] - now for p in pts if p[2] is not None]
            ys_g = [p[2] for p in pts if p[2] is not None]
            self.cpu_line.set_data(xs_c, ys_c)
            self.gpu_line.set_data(xs_g, ys_g)
            hi = max(ys_c + ys_g, default=0)
            self.temp_ax.set_ylim(20, max(100, hi + 5))
            self.temp_canvas.draw_idle()

        def _start_temps_log(self) -> None:
            self._pre_run_ts = {r.get("timestamp") for r in load_rows()}
            self._temps_log_start = time.time()
            path = RUNS_DIR / f"live-{time.strftime('%Y%m%d-%H%M%S')}.temps.csv"
            try:
                path.write_text(TEMPS_CSV_HEADER)
                self._temps_log_path = path
            except OSError:
                self._temps_log_path = None
            self._reset_temp_peaks()

        def _finish_temps_log(self) -> None:
            path, self._temps_log_path = self._temps_log_path, None
            if path is None or not path.is_file():
                return
            new = [r.get("timestamp") for r in load_rows() if r.get("timestamp") not in self._pre_run_ts]
            if new and new[-1]:
                dst = RUNS_DIR / f"{new[-1]}.temps.csv"
                if not dst.exists():
                    try:
                        path.rename(dst)
                        self._append_log(f"Temperature log: {dst}\n")
                    except OSError:
                        pass

        def _refresh_compare_combos(self) -> None:
            rows = load_rows()
            # Preserve selection if possible
            a_ts = self.combo_a.currentData()
            b_ts = self.combo_b.currentData()
            self.combo_a.blockSignals(True)
            self.combo_b.blockSignals(True)
            self.combo_a.clear()
            self.combo_b.clear()
            for r in rows:
                lab = row_label(r)
                ts = r.get("timestamp")
                self.combo_a.addItem(lab, ts)
                self.combo_b.addItem(lab, ts)
            # Defaults: baseline vs latest
            bl = baseline_timestamp(rows)
            latest = rows[-1].get("timestamp") if rows else None
            prefer_a = a_ts or bl
            prefer_b = b_ts or self._last_finished_ts or latest
            self._select_combo(self.combo_a, prefer_a)
            self._select_combo(self.combo_b, prefer_b)
            self.combo_a.blockSignals(False)
            self.combo_b.blockSignals(False)
            if rows and self.combo_a.count() and self.combo_b.count():
                # Don't auto-fire on every refresh unless first load with data
                pass

        def _select_combo(self, combo: QComboBox, ts: str | None) -> None:
            if not ts:
                return
            for i in range(combo.count()):
                if combo.itemData(i) == ts:
                    combo.setCurrentIndex(i)
                    return

        def _select_compare_pair(self, a_ts: str | None, b_ts: str | None) -> None:
            self._select_combo(self.combo_a, a_ts)
            self._select_combo(self.combo_b, b_ts)

        def _do_compare(self) -> None:
            rows = load_rows()
            by_ts = {r.get("timestamp"): r for r in rows}
            a_ts = self.combo_a.currentData()
            b_ts = self.combo_b.currentData()
            a = by_ts.get(a_ts)
            b = by_ts.get(b_ts)
            if not a or not b:
                self.compare_meta.setText("Need two runs to compare. Run a benchmark first.")
                self.compare_table.setRowCount(0)
                if self.figure is not None:
                    self.figure.clear()
                    self.canvas.draw_idle()
                return

            self.compare_meta.setText(
                f"A: {a.get('timestamp')}  tag={a.get('tag') or '-'}  "
                f"kernel={a.get('kernel')}  nvidia={a.get('nvidia_driver')}  plasma={a.get('plasma')}\n"
                f"B: {b.get('timestamp')}  tag={b.get('tag') or '-'}  "
                f"kernel={b.get('kernel')}  nvidia={b.get('nvidia_driver')}  plasma={b.get('plasma')}"
            )

            green, red, muted = COL_GOOD, COL_BAD, COL_NOISE

            self.compare_table.setRowCount(0)
            chart_labels: list[str] = []
            chart_vals: list[float] = []
            chart_colors: list[str] = []

            hist_rows = [r for r in rows if is_comparable_row(r)]
            for key, label in METRICS:
                av = parse_num(a.get(key))
                bv = parse_num(b.get(key))
                hist = collect_metric_history(hist_rows, key, exclude_ts=b_ts)
                d = metric_delta(key, label, av, bv, hist)
                if d.verdict == "within_noise" and d.pct is not None:
                    ch = f"{d.pct:+.1f}%"
                    note = f"within noise (±{d.noise_pct:.1f}%)"
                elif d.verdict in ("better", "worse") and d.pct is not None:
                    ch = f"{d.pct:+.1f}%"
                    note = f"{d.note} (noise ±{d.noise_pct:.1f}%)"
                else:
                    ch, note = compare_note(key, av, bv)
                row_i = self.compare_table.rowCount()
                self.compare_table.insertRow(row_i)
                cells = [
                    label,
                    f"{av:.2f}" if av is not None else "—",
                    f"{bv:.2f}" if bv is not None else "—",
                    ch,
                    note,
                ]
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if col in (3, 4):
                        if d.verdict == "better":
                            item.setForeground(green)
                        elif d.verdict == "worse":
                            item.setForeground(red)
                        else:
                            item.setForeground(muted)
                    self.compare_table.setItem(row_i, col, item)

                # Chart: key performance metrics only (skip temps / wall for clarity)
                if key in (
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
                    "cyclictest_max_us",
                ):
                    c = pct_change(av, bv)
                    if c is not None:
                        # Short label
                        short = {
                            "7z_st": "7z ST",
                            "7z_mt": "7z MT",
                            "sysbench_cpu_st_eps": "CPU ST",
                            "sysbench_cpu_mt_eps": "CPU MT",
                            "sysbench_mem_MiBps": "Mem",
                            "zstd_MBps": "zstd1",
                            "zstd2_comp_MBps": "zstd2C",
                            "zstd2_decomp_MBps": "zstd2D",
                            "x264_fps": "x264v1",
                            "x264v2_fps": "x264v2",
                            "fio_seq_read_MiBps": "Disk R",
                            "fio_seq_write_MiBps": "Disk W",
                            "fio_rand_read_IOPS": "4k R",
                            "fio_rand_write_IOPS": "4k W",
                            "vkmark": "vk",
                            "glmark2": "gl",
                            "cyclictest_max_us": "lat",
                        }.get(key, key)
                        # For LIB metrics, flip sign so "better" is positive bar
                        display = -c if key in LIB else c
                        chart_labels.append(short)
                        chart_vals.append(display)
                        good, bad = theme.C["good"], theme.C["bad"]
                        if d.verdict == "within_noise":
                            chart_colors.append(theme.C["noise"])
                        elif d.verdict == "better":
                            chart_colors.append(good)
                        elif d.verdict == "worse":
                            chart_colors.append(bad)
                        else:
                            improved = bool(av is not None and bv is not None and is_improved(key, av, bv))
                            chart_colors.append(good if improved else bad)

            if self.figure is not None:
                self.figure.clear()
                if chart_labels:
                    ax = self.figure.add_subplot(111)
                    theme.style_axes(ax)
                    ys = list(range(len(chart_labels)))[::-1]
                    ax.barh(ys, chart_vals, color=chart_colors, height=0.66,
                            edgecolor=theme.C["bg0"], linewidth=0.6)
                    ax.set_yticks(ys, chart_labels)
                    ax.axvline(0, color=theme.C["brass"], linewidth=1.0)
                    ax.grid(axis="y", visible=False)
                    ax.set_xlabel("% vs A   (better \u2192 +)", fontsize=9)
                    ax.tick_params(labelsize=9)
                    lim = max([abs(v) for v in chart_vals] + [1.0]) * 1.28
                    ax.set_xlim(-lim, lim)
                    for y, v in zip(ys, chart_vals):
                        ax.text(v + (lim * 0.02 if v >= 0 else -lim * 0.02), y, f"{v:+.1f}%",
                                va="center", ha="left" if v >= 0 else "right",
                                fontsize=8, color=theme.C["text"])
                    self.figure.tight_layout(pad=0.8)
                self.canvas.draw_idle()

        def _show_about(self) -> None:
            license_paths = [
                APP_SHARE / "LICENSE",
                APP_DIR.parent / "LICENSE",
                APP_DIR / "LICENSE",
            ]
            license_note = next((str(p) for p in license_paths if p.is_file()), "MIT License")
            QMessageBox.about(
                self,
                f"About {APP_NAME}",
                f"<b>{APP_NAME}</b> {__version__}<br>"
                f"Copyright (c) 2026 Botlabs USA<br>"
                f"License: MIT<br>"
                f"Support: support@botlabsusa.com<br>"
                f"<small>{license_note}</small><br><br>"
                "Run, log, and compare CachyOS performance baselines.",
            )

        def closeEvent(self, event) -> None:  # noqa: N802
            if self._proc is not None:
                reply = QMessageBox.question(
                    self,
                    "Benchmark running",
                    "A benchmark is still running. Cancel it and quit?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self._on_cancel()
                # Brief wait
                if self._proc is not None:
                    self._proc.waitForFinished(3000)
            self._temp_poller.stop()
            self._temp_poller.wait(3000)
            event.accept()

    # HiDPI: respect Plasma scaling; also set AA
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("cachy-bench")
    theme.apply_app_theme(app)
    win = MainWindow()
    win.showMaximized()
    app.aboutToQuit.connect(lambda: (win._temp_poller.stop(), win._temp_poller.wait(3000)))
    if on_ready is not None:
        QTimer.singleShot(0, lambda: on_ready(app, win))
    elif os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # If offscreen, auto-quit after a moment for CI-style smoke
        QTimer.singleShot(800, app.quit)
    return app.exec()


def main() -> int:
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"{APP_NAME} {__version__}\nSupport: support@botlabsusa.com")
        return 0
    if "--selftest" in sys.argv:
        return selftest()
    if "--check-deps" in sys.argv:
        missing = check_deps(verbose=True)
        return 1 if missing else 0
    if "--temps-once" in sys.argv:
        print(format_sample(read_temps(timeout=3.0)))
        return 0
    if "--delete-runs" in sys.argv:
        i = sys.argv.index("--delete-runs")
        arg = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        return cli_delete_runs(arg, "--yes" in sys.argv, "--allow-baseline" in sys.argv)
    if "--undo-delete" in sys.argv:
        return cli_undo_delete()
    if "--what-changed" in sys.argv:
        rows = load_rows()
        report = build_what_changed(rows, baseline_ts=active_baseline_ts(), runs_dir=RUNS_DIR, data_dir=BENCH_ROOT)
        print(format_what_changed_text(report))
        return 0
    # Surface missing deps in GUI startup via env for status line consumers
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
