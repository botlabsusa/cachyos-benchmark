import sys
from pathlib import Path

# Add app/ to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import metrics


def test_higher_is_better():
    # Test HIB (higher is better) keys
    for key in metrics.HIB:
        assert metrics.higher_is_better(key) is True

    # Test LIB (lower is better) keys
    for key in metrics.LIB:
        assert metrics.higher_is_better(key) is False


def test_parse_num():
    # Test valid numbers
    assert metrics.parse_num("123.45") == 123.45
    assert metrics.parse_num("  456.78  ") == 456.78
    assert metrics.parse_num('"789.01"') == 789.01

    # Test None input
    assert metrics.parse_num(None) is None

    # Test invalid strings
    assert metrics.parse_num("") is None
    assert metrics.parse_num("n/a") is None
    assert metrics.parse_num("none") is None
    assert metrics.parse_num("parse_failed") is None
    assert metrics.parse_num("—") is None
    assert metrics.parse_num("-") is None
    assert metrics.parse_num("not_a_number") is None


def test_pct_change():
    # Test normal case
    assert metrics.pct_change(100.0, 105.0) == 5.0
    assert metrics.pct_change(100.0, 95.0) == -5.0

    # Test with zero baseline
    assert metrics.pct_change(0.0, 10.0) is None

    # Test with None values
    assert metrics.pct_change(None, 10.0) is None
    assert metrics.pct_change(10.0, None) is None
    assert metrics.pct_change(None, None) is None


def test_is_improved():
    # Test HIB (higher is better)
    assert metrics.is_improved("7z_st", 100.0, 105.0) is True
    assert metrics.is_improved("7z_st", 100.0, 95.0) is False

    # Test LIB (lower is better)
    assert metrics.is_improved("cyclictest_max_us", 100.0, 95.0) is True
    assert metrics.is_improved("cyclictest_max_us", 100.0, 105.0) is False


def test_compare_note():
    # Test both None
    assert metrics.compare_note("7z_st", None, None) == ("", "n/a")

    # Test baseline None
    assert metrics.compare_note("7z_st", None, 100.0) == ("", "new")

    # Test latest None
    assert metrics.compare_note("7z_st", 100.0, None) == ("", "n/a")

    # Test normal case with improvement
    assert metrics.compare_note("7z_st", 100.0, 105.0) == ("+5.0%", "better ↑")
    assert metrics.compare_note("cyclictest_max_us", 100.0, 95.0) == ("-5.0%", "better ↓")

    # Test normal case with regression
    assert metrics.compare_note("7z_st", 100.0, 95.0) == ("-5.0%", "worse ↓")
    assert metrics.compare_note("cyclictest_max_us", 100.0, 105.0) == ("+5.0%", "worse ↑")

    # Test small change (should be "same")
    assert metrics.compare_note("7z_st", 100.0, 100.004) == ("+0.0%", "same")
