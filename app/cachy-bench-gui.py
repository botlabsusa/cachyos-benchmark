#!/usr/bin/env python3
"""CachyOS Benchmark — simple desktop GUI for run / log / compare.

Usage:
  cachy-bench-gui.py                 # launch GUI
  cachy-bench-gui.py --version       # print version and exit
  cachy-bench-gui.py --selftest      # parse CSV / metrics / changes, exit 0
  cachy-bench-gui.py --check-deps    # list missing tools + pacman line
  cachy-bench-gui.py --what-changed  # headless 'What changed' report

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
    print(f"selftest OK — metrics={len(METRICS)} rows={len(rows)} bench_root={BENCH_ROOT}")
    print(f"app_share={APP_SHARE}")
    print(f"bench_script_exists={BENCH_SCRIPT.is_file()} csv_exists={CSV_PATH.is_file()}")
    print(f"what_changed_latest={((report.get('latest') or {}).get('timestamp'))}")
    return 0


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def run_gui() -> int:
    from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer, QSize
    from PySide6.QtGui import QColor, QFont, QAction, QDesktopServices
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
    )
    from PySide6.QtCore import QUrl

    # Optional matplotlib
    HAS_MPL = False
    try:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure

        HAS_MPL = True
    except Exception:
        HAS_MPL = False

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

            tabs = QTabWidget()
            self.setCentralWidget(tabs)

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

            title = QLabel("CachyOS Benchmark")
            f = title.font()
            f.setPointSize(f.pointSize() + 4)
            f.setBold(True)
            title.setFont(f)
            layout.addWidget(title)

            hint = QLabel(
                f"Runs {BENCH_SCRIPT.name} (~10 min). Data → {BENCH_ROOT}"
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(mid);")
            layout.addWidget(hint)

            opts = QHBoxLayout()
            self.baseline_cb = QCheckBox("Mark as baseline")
            self.tag_edit = QLineEdit()
            self.tag_edit.setPlaceholderText("Optional tag (e.g. after-nvidia-update)")
            self.tag_edit.setClearButtonEnabled(True)
            opts.addWidget(self.baseline_cb)
            opts.addWidget(QLabel("Tag:"))
            opts.addWidget(self.tag_edit, stretch=1)
            layout.addLayout(opts)

            btns = QHBoxLayout()
            self.run_btn = QPushButton("Run Benchmark")
            self.run_btn.setMinimumHeight(48)
            bf = self.run_btn.font()
            bf.setPointSize(bf.pointSize() + 2)
            bf.setBold(True)
            self.run_btn.setFont(bf)
            self.run_btn.clicked.connect(self._on_run)
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.clicked.connect(self._on_cancel)
            self.open_logs_btn = QPushButton("Open logs folder")
            self.open_logs_btn.clicked.connect(self._open_logs)
            btns.addWidget(self.run_btn, stretch=2)
            btns.addWidget(self.cancel_btn)
            btns.addWidget(self.open_logs_btn)
            layout.addLayout(btns)

            self.status_line = QLabel("Ready.")
            self.elapsed_label = QLabel("")
            status_row = QHBoxLayout()
            status_row.addWidget(self.status_line, stretch=1)
            status_row.addWidget(self.elapsed_label)
            layout.addLayout(status_row)

            self.log_pane = QPlainTextEdit()
            self.log_pane.setReadOnly(True)
            mono = QFont("monospace")
            mono.setStyleHint(QFont.StyleHint.Monospace)
            mono.setPointSize(mono.pointSize())
            self.log_pane.setFont(mono)
            self.log_pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            layout.addWidget(self.log_pane, stretch=1)

        # ---- History tab ----
        def _build_history_tab(self) -> None:
            layout = QVBoxLayout(self.history_tab)
            top = QHBoxLayout()
            top.addWidget(QLabel("All runs from results.csv"))
            top.addStretch(1)
            refresh = QPushButton("Refresh")
            refresh.clicked.connect(self._refresh_history)
            top.addWidget(refresh)
            set_bl = QPushButton("Set selected as baseline")
            set_bl.clicked.connect(self._set_selected_baseline)
            top.addWidget(set_bl)
            layout.addLayout(top)

            cols = ["Date", "Tag", "Baseline", "Kernel", "NVIDIA", "Plasma"] + [
                c[1] for c in HISTORY_SCORE_COLS
            ]
            self.hist_table = QTableWidget(0, len(cols))
            self.hist_table.setHorizontalHeaderLabels(cols)
            self.hist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.hist_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.hist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.hist_table.setSortingEnabled(True)
            self.hist_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.hist_table.horizontalHeader().setStretchLastSection(True)
            self.hist_table.verticalHeader().setVisible(False)
            layout.addWidget(self.hist_table)

        # ---- Compare tab ----
        def _build_compare_tab(self) -> None:
            layout = QVBoxLayout(self.compare_tab)
            pick = QHBoxLayout()
            pick.addWidget(QLabel("A (baseline):"))
            self.combo_a = QComboBox()
            self.combo_a.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            pick.addWidget(self.combo_a, stretch=1)
            pick.addWidget(QLabel("B (compare):"))
            self.combo_b = QComboBox()
            self.combo_b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            pick.addWidget(self.combo_b, stretch=1)
            go = QPushButton("Compare")
            go.clicked.connect(self._do_compare)
            pick.addWidget(go)
            layout.addLayout(pick)

            self.compare_meta = QLabel("")
            self.compare_meta.setWordWrap(True)
            layout.addWidget(self.compare_meta)

            splitter = QSplitter(Qt.Orientation.Vertical)
            layout.addWidget(splitter, stretch=1)

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
            splitter.addWidget(self.compare_table)

            self.chart_container = QWidget()
            self.chart_layout = QVBoxLayout(self.chart_container)
            self.chart_layout.setContentsMargins(0, 0, 0, 0)
            if HAS_MPL:
                self.figure = Figure(figsize=(8, 2.8), dpi=100)
                self.canvas = FigureCanvas(self.figure)
                self.chart_layout.addWidget(self.canvas)
            else:
                self.figure = None
                self.canvas = None
                lab = QLabel("Bar chart skipped (matplotlib unavailable).")
                lab.setStyleSheet("color: palette(mid);")
                self.chart_layout.addWidget(lab)
            splitter.addWidget(self.chart_container)
            splitter.setSizes([400, 220])

            self.combo_a.currentIndexChanged.connect(lambda *_: None)
            # Auto-compare when both populated via refresh


        # ---- What changed tab ----
        def _build_changed_tab(self) -> None:
            layout = QVBoxLayout(self.changed_tab)
            top = QHBoxLayout()
            top.addWidget(
                QLabel(
                    f"Latest vs previous comparable run, and vs baseline {active_baseline_ts() or '(mark a run with --baseline)'}. "
                    "Red/green only when change exceeds estimated noise."
                )
            )
            top.addStretch(1)
            refresh = QPushButton("Refresh")
            refresh.clicked.connect(self._refresh_what_changed)
            top.addWidget(refresh)
            layout.addLayout(top)

            self.changed_meta = QLabel("")
            self.changed_meta.setWordWrap(True)
            layout.addWidget(self.changed_meta)

            self.changed_hot = QLabel("")
            self.changed_hot.setWordWrap(True)
            layout.addWidget(self.changed_hot)

            self.changed_pkgs = QPlainTextEdit()
            self.changed_pkgs.setReadOnly(True)
            self.changed_pkgs.setMaximumHeight(160)
            mono = QFont("monospace")
            mono.setStyleHint(QFont.StyleHint.Monospace)
            self.changed_pkgs.setFont(mono)
            layout.addWidget(QLabel("Package changes (pacman.log since previous run)"))
            layout.addWidget(self.changed_pkgs)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            layout.addWidget(splitter, stretch=1)

            left = QWidget()
            left_l = QVBoxLayout(left)
            left_l.setContentsMargins(0, 0, 0, 0)
            left_l.addWidget(QLabel("All metrics vs previous (noise-aware)"))
            self.changed_prev_table = QTableWidget(0, 5)
            self.changed_prev_table.setHorizontalHeaderLabels(
                ["Metric", "Prev", "Latest", "Change", "Note"]
            )
            self.changed_prev_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.changed_prev_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Stretch
            )
            self.changed_prev_table.verticalHeader().setVisible(False)
            left_l.addWidget(self.changed_prev_table)
            splitter.addWidget(left)

            right = QWidget()
            right_l = QVBoxLayout(right)
            right_l.setContentsMargins(0, 0, 0, 0)
            right_l.addWidget(QLabel(f"All metrics vs baseline {active_baseline_ts() or '(none set)'}"))
            self.changed_base_table = QTableWidget(0, 5)
            self.changed_base_table.setHorizontalHeaderLabels(
                ["Metric", "Base", "Latest", "Change", "Note"]
            )
            self.changed_base_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.changed_base_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Stretch
            )
            self.changed_base_table.verticalHeader().setVisible(False)
            right_l.addWidget(self.changed_base_table)
            splitter.addWidget(right)

            self.changed_notes = QLabel("")
            self.changed_notes.setWordWrap(True)
            self.changed_notes.setStyleSheet("color: palette(mid);")
            layout.addWidget(self.changed_notes)

        def _fill_moved_table(self, table: QTableWidget, block: dict) -> None:
            green = QColor(30, 140, 70)
            red = QColor(190, 50, 50)
            grey = QColor(120, 120, 120)
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
                    self.changed_hot.setStyleSheet("color: #be3232; font-weight: bold;")
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
            self.hist_table.setSortingEnabled(False)
            self.hist_table.setRowCount(0)
            # Newest first for display
            for r in reversed(rows):
                row_i = self.hist_table.rowCount()
                self.hist_table.insertRow(row_i)
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
                for col, val in enumerate(vals):
                    item = QTableWidgetItem(val)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, r.get("timestamp"))
                    # Numeric sort for score cols
                    if col >= 6:
                        num = parse_num(val if val != "—" else None)
                        if num is not None:
                            item.setData(Qt.ItemDataRole.UserRole, num)
                    self.hist_table.setItem(row_i, col, item)
            self.hist_table.setSortingEnabled(True)

        def _set_selected_baseline(self) -> None:
            items = self.hist_table.selectedItems()
            if not items:
                QMessageBox.information(self, "Select a run", "Select a row in History first.")
                return
            row = items[0].row()
            ts_item = self.hist_table.item(row, 0)
            if not ts_item:
                return
            ts = ts_item.text().strip()
            if not ts:
                return
            BASELINE_FILE.write_text(ts + "\n")
            self._refresh_all()
            self.status_line.setText(f"Baseline set to {ts}")

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

            green = QColor(30, 140, 70)
            red = QColor(190, 50, 50)
            muted = QColor(120, 120, 120)

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
                        if d.verdict == "within_noise":
                            chart_colors.append("#888888")
                        elif d.verdict == "better":
                            chart_colors.append("#1e8c46")
                        elif d.verdict == "worse":
                            chart_colors.append("#be3232")
                        else:
                            improved = bool(av is not None and bv is not None and is_improved(key, av, bv))
                            chart_colors.append("#1e8c46" if improved else "#be3232")

            if self.figure is not None:
                self.figure.clear()
                if chart_labels:
                    ax = self.figure.add_subplot(111)
                    ax.bar(chart_labels, chart_vals, color=chart_colors)
                    ax.axhline(0, color="#888", linewidth=0.8)
                    ax.set_ylabel("% vs A (better → +)")
                    ax.tick_params(axis="x", labelrotation=45)
                    self.figure.tight_layout()
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
            event.accept()

    # HiDPI: respect Plasma scaling; also set AA
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("cachy-bench")
    win = MainWindow()
    win.show()
    # If offscreen, auto-quit after a moment for CI-style smoke
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
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
    if "--what-changed" in sys.argv:
        rows = load_rows()
        report = build_what_changed(rows, baseline_ts=active_baseline_ts(), runs_dir=RUNS_DIR, data_dir=BENCH_ROOT)
        print(format_what_changed_text(report))
        return 0
    # Surface missing deps in GUI startup via env for status line consumers
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
