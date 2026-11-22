import csv
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import pytest

# Add app/ to sys.path and import the module
import sys
sys.path.insert(0, 'app')
import history_ops


def test_sha(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")
    sha1 = history_ops._sha(test_file)
    assert isinstance(sha1, str)
    assert len(sha1) == 64  # SHA-256 hex digest length

    # Test with non-existent file
    non_existent = tmp_path / "nonexistent.txt"
    sha2 = history_ops._sha(non_existent)
    assert sha2 == ""


def test_read_csv(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text('a,b,c\n1,2,3\n4,5,6\n')
    
    header, rows = history_ops._read_csv(csv_file)
    assert header == ['a', 'b', 'c']
    assert rows == [['1', '2', '3'], ['4', '5', '6']]

    # Test with non-existent file
    non_existent = tmp_path / "nonexistent.csv"
    header, rows = history_ops._read_csv(non_existent)
    assert header == []
    assert rows == []


def test_write_csv_atomic(tmp_path):
    csv_file = tmp_path / "test.csv"
    header = ['a', 'b', 'c']
    rows = [['1', '2', '3'], ['4', '5', '6']]
    
    history_ops._write_csv_atomic(csv_file, header, rows)
    
    # Verify content
    with csv_file.open(newline='') as f:
        reader = csv.reader(f)
        read_header = next(reader)
        read_rows = list(reader)
    
    assert read_header == header
    assert read_rows == rows


def test_unique(tmp_path):
    test_file = tmp_path / "test.txt"
    # Should return same path if not exists
    result = history_ops._unique(test_file)
    assert result == test_file
    test_file.write_text("content")
    
    # Should return new path with suffix if exists
    result2 = history_ops._unique(tmp_path / "test.txt")
    assert result2.name == "test.txt.1"
    assert result2.parent == tmp_path


def test_stamp():
    stamp = history_ops._stamp()
    assert isinstance(stamp, str)
    assert len(stamp) == 15  # YYYYmmdd-HHMMSS format
    assert stamp[8] == '-'  # Separator between date and time


def test_trash_dir(tmp_path):
    data_dir = tmp_path / "data"
    trash = history_ops.trash_dir(data_dir)
    expected = data_dir / "runs" / "trash"
    assert trash == expected


def test_run_files(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    
    # Create some files
    (runs_dir / "123456789.log").write_text("log content")
    (runs_dir / "123456789-tag.log").write_text("tag log content")
    (runs_dir / "123456789.changes.json").write_text("{}")
    (runs_dir / "other.txt").write_text("other")
    
    files = history_ops.run_files(tmp_path, "123456789")
    assert len(files) == 3
    file_names = [f.name for f in files]
    assert "123456789.log" in file_names
    assert "123456789-tag.log" in file_names
    assert "123456789.changes.json" in file_names


def test_plan_delete(tmp_path):
    # Setup
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    results_file = data_dir / "results.csv"
    results_file.write_text('timestamp,log_file\n123456789,/path/to/log\n')
    
    # Test with existing timestamp
    plan = history_ops.plan_delete(data_dir, ["123456789"])
    assert "found" in plan
    assert "missing" in plan
    assert "files" in plan
    
    # Test with non-existing timestamp
    plan = history_ops.plan_delete(data_dir, ["987654321"])
    assert plan["missing"] == ["987654321"]
    assert plan["found"] == []


def test_last_delete(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # No trash directory
    result = history_ops.last_delete(data_dir)
    assert result is None
    
    # Create trash dir and manifest
    trash_dir = history_ops.trash_dir(data_dir)
    trash_dir.mkdir(parents=True, exist_ok=True)
    
    manifest_file = trash_dir / "delete-20230101-120000.json"
    manifest_data = {
        "stamp": "20230101-120000",
        "backup": "/some/path/results.csv.bak-20230101-120000",
        "removed": ["123456789"],
        "moved": [],
        "csv_sha_after": ""
    }
    manifest_file.write_text(json.dumps(manifest_data))
    
    result = history_ops.last_delete(data_dir)
    assert result is not None
    assert result["stamp"] == "20230101-120000"


def test_undo_last_delete(tmp_path):
    # Setup
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    results_file = data_dir / "results.csv"
    results_file.write_text('timestamp,log_file\n123456789,/path/to/log\n')
    
    # Create trash directory and manifest
    trash_dir = history_ops.trash_dir(data_dir)
    trash_dir.mkdir(parents=True, exist_ok=True)
    
    backup_file = data_dir / "results.csv.bak-20230101-120000"
    backup_file.write_text('timestamp,log_file\n123456789,/path/to/log\n')
    
    manifest_file = trash_dir / "delete-20230101-120000.json"
    manifest_data = {
        "stamp": "20230101-120000",
        "backup": str(backup_file),
        "removed": ["123456789"],
        "moved": [],
        "csv_sha_after": ""
    }
    manifest_file.write_text(json.dumps(manifest_data))
    
    # Run undo
    result = history_ops.undo_last_delete(data_dir)
    assert result is not None
    assert "restored_rows" in result
    assert "from_backup" in result
