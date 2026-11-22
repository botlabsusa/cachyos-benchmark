import json
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest

# Add app/ to sys.path and import changes
_APP = Path(__file__).resolve().parent
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import changes


def test_resolve_baseline_ts_env():
    with patch.dict(os.environ, {"CACHY_BENCH_BASELINE_TS": "20230101-120000"}):
        assert changes.resolve_baseline_ts() == "20230101-120000"


def test_resolve_baseline_ts_file(tmp_path):
    data_dir = tmp_path / "bench"
    data_dir.mkdir()
    tag_file = data_dir / "baseline.tag"
    tag_file.write_text("20230101-110000")

    assert changes.resolve_baseline_ts(data_dir) == "20230101-110000"


def test_resolve_baseline_ts_empty():
    with patch.dict(os.environ, {}, clear=True):
        assert changes.resolve_baseline_ts() == ""


def test_tag_excluded():
    assert changes.tag_excluded("appimage-test-smoke") is True
    assert changes.tag_excluded("test-smoke-run") is False  # only appimage-test-smoke is excluded
    assert changes.tag_excluded("regular-run") is False
    assert changes.tag_excluded(None) is False


def test_is_comparable_row():
    row = {"tag": "appimage-test-smoke"}
    assert changes.is_comparable_row(row) is False

    row = {"tag": "smoke-test"}
    assert changes.is_comparable_row(row) is False

    row = {"tag": "regular-run"}
    assert changes.is_comparable_row(row) is True

    row = {"tag": None}
    assert changes.is_comparable_row(row) is True


def test_parse_run_ts():
    assert changes.parse_run_ts("20230101-120000") == datetime(2023, 1, 1, 12, 0, 0)
    assert changes.parse_run_ts("") is None
    assert changes.parse_run_ts("invalid") is None


def test_parse_pacman_ts():
    assert changes.parse_pacman_ts("2026-10-31T21:25:08-0500") == datetime(2026, 10, 31, 21, 25, 8)
    assert changes.parse_pacman_ts("2026-10-31T21:25:08+0500") == datetime(2026, 10, 31, 21, 25, 8)
    assert changes.parse_pacman_ts("") is None
    assert changes.parse_pacman_ts("invalid") is None


def test_load_csv_rows(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("a,b,c\n1,2,3\n4,5,6\n")

    rows = changes.load_csv_rows(csv_file)
    assert len(rows) == 2
    assert rows[0]["a"] == "1"
    assert rows[1]["c"] == "6"

    assert changes.load_csv_rows(tmp_path / "nonexistent.csv") == []


def test_previous_run_row():
    rows = [
        {"timestamp": "20230101-100000", "tag": "run1"},
        {"timestamp": "20230101-110000", "tag": "run2"},
        {"timestamp": "20230101-120000", "tag": "run3"}
    ]
    assert changes.previous_run_row(rows, "20230101-120000") == {"timestamp": "20230101-110000", "tag": "run2"}
    assert changes.previous_run_row(rows) == {"timestamp": "20230101-120000", "tag": "run3"}


def test_previous_comparable_row():
    rows = [
        {"timestamp": "20230101-100000", "tag": "appimage-test-smoke"},
        {"timestamp": "20230101-110000", "tag": "run2"},
        {"timestamp": "20230101-120000", "tag": "run3"}
    ]
    assert changes.previous_comparable_row(rows, "20230101-120000") == {"timestamp": "20230101-110000", "tag": "run2"}


def test_parse_pacman_since(tmp_path):
    log_file = tmp_path / "pacman.log"
    log_content = """[2023-01-01T10:00:00+0000] [ALPM] installed package1 1.0-1
[2023-01-01T11:00:00+0000] [ALPM] upgraded package2 1.0-1 -> 1.0-2
[2023-01-01T12:00:00+0000] [ALPM] removed package3 1.0-1"""
    log_file.write_text(log_content)

    result = changes.parse_pacman_since("20230101-090000", log_file)
    assert result["log_readable"] is True
    assert len(result["installed"]) == 1
    assert len(result["upgraded"]) == 1
    assert len(result["removed"]) == 1


def test_hot_start_info():
    info = changes.hot_start_info(60.0, 40.0)
    assert info["warned"] is True

    info = changes.hot_start_info(40.0, 40.0)
    assert info["warned"] is False


def test_noise_threshold_pct():
    # Test with < 3 values
    threshold, source = changes.noise_threshold_pct("7z_st", [1.0, 2.0])
    assert threshold == 2.0  # default from DEFAULT_NOISE_PCT
    assert "default_n=2" in source

    # Test with 3+ values
    threshold, source = changes.noise_threshold_pct("7z_st", [1.0, 2.0, 3.0])
    assert threshold >= 2.0  # should be at least default
    assert "stdev_n=3" in source


def test_collect_metric_history():
    rows = [
        {"timestamp": "20230101-100000", "tag": "run1", "metric1": "1.0"},
        {"timestamp": "20230101-110000", "tag": "smoke-test", "metric1": "2.0"},
        {"timestamp": "20230101-120000", "tag": "run2", "metric1": "3.0"}
    ]
    result = changes.collect_metric_history(rows, "metric1")
    assert len(result) == 2  # smoke test excluded
    assert result[0] == 1.0
    assert result[1] == 3.0


def test_metric_delta():
    # Test with no baseline or latest
    delta = changes.metric_delta("7z_st", "7-Zip ST", None, None, [])
    assert delta.verdict == "n/a"

    # Test with only baseline
    delta = changes.metric_delta("7z_st", "7-Zip ST", 1.0, None, [])
    assert delta.verdict == "n/a"

    # Test with only latest
    delta = changes.metric_delta("7z_st", "7-Zip ST", None, 2.0, [])
    assert delta.verdict == "new"

    # Test within noise
    delta = changes.metric_delta("7z_st", "7-Zip ST", 1.0, 1.01, [1.0, 1.0, 1.0])  # 1% < 2% noise floor
    assert delta.verdict == "within_noise"

    # Test better
    delta = changes.metric_delta("7z_st", "7-Zip ST", 1.0, 1.2, [1.0, 1.0, 1.0])
    assert delta.verdict == "better"

    # Test worse
    delta = changes.metric_delta("7z_st", "7-Zip ST", 1.0, 0.8, [1.0, 1.0, 1.0])
    assert delta.verdict == "worse"


def test_build_what_changed(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("timestamp,tag,7z_st\n20230101-100000,run1,1.0\n20230101-110000,run2,2.0\n")

    rows = changes.load_csv_rows(csv_file)
    report = changes.build_what_changed(rows, latest_ts="20230101-110000")
    assert "latest" in report
    assert "vs_previous" in report


def test_write_sidecar(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("timestamp,tag,7z_st\n20230101-100000,run1,1.0\n")

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    path = changes.write_sidecar(
        csv_path=csv_file,
        runs_dir=runs_dir,
        ts="20230101-110000",
        kernel="5.10",
        nvidia="470",
        plasma="5.20",
        mesa="21.0",
        cpu_temp=40.0,
        gpu_temp=50.0
    )

    assert path.exists()
    content = json.loads(path.read_text())
    assert content["timestamp"] == "20230101-110000"
    assert content["hot_start"]["cpu_temp_C"] == 40.0


def test_format_what_changed_text():
    report = {
        "latest": {
            "timestamp": "20230101-110000",
            "tag": "test-run",
            "versions": {"kernel": "5.10", "nvidia_driver": "470"}
        },
        "vs_previous": {
            "label": "previous comparable run",
            "ref_timestamp": "20230101-100000",
            "moved": [
                {
                    "verdict": "better",
                    "pct": 10.0,
                    "noise_pct": 2.0,
                    "label": "7-Zip ST"
                }
            ]
        },
        "vs_baseline": {
            "label": "baseline 20230101-090000",
            "ref_timestamp": "20230101-090000",
            "moved": []
        }
    }

    text = changes.format_what_changed_text(report)
    assert "Latest: 20230101-110000" in text
    assert "7-Zip ST" in text
    assert "better" in text
