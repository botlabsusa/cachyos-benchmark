"""Reversible removal of runs from results.csv (History delete / undo).

Nothing is ever rm'd:
  * results.csv is copied to results.csv.bak-<YYYYmmdd-HHMMSS> first
  * each run's runs/<ts>.log / <ts>-<tag>.log / <ts>.changes.json /
    <ts>.temps.csv is moved into runs/trash/
  * a manifest runs/trash/delete-<stamp>.json records what happened, so
    undo_last_delete() can put everything back (newest delete first).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import time
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    if not path.is_file():
        return [], []
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _write_csv_atomic(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buf = io.StringIO()
    # Same style as cachy-bench.sh: every field quoted, \n line endings.
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    tmp = path.with_name(path.name + ".tmp-write")
    tmp.write_text(buf.getvalue())
    os.replace(tmp, path)


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 1
    while True:
        cand = path.with_name(f"{path.name}.{i}")
        if not cand.exists():
            return cand
        i += 1


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def trash_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "runs" / "trash"


def run_files(data_dir: Path, ts: str, log_file: str = "") -> list[Path]:
    """Files in runs/ that belong to run ts (never anything outside runs/)."""
    runs = Path(data_dir) / "runs"
    out: list[Path] = []
    if runs.is_dir():
        for p in sorted(runs.iterdir()):
            if p.is_file() and (p.name.startswith(ts + ".") or p.name.startswith(ts + "-")):
                out.append(p)
    if log_file:
        lp = Path(log_file)
        try:
            if lp.is_file() and lp.resolve().parent == runs.resolve() and lp not in out:
                out.append(lp)
        except OSError:
            pass
    return out


def plan_delete(data_dir: Path, ts_list: list[str]) -> dict:
    """What a delete would do, without touching anything."""
    data_dir = Path(data_dir)
    header, rows = _read_csv(data_dir / "results.csv")
    if not header:
        return {"found": [], "missing": list(ts_list), "files": []}
    ti = header.index("timestamp") if "timestamp" in header else 0
    li = header.index("log_file") if "log_file" in header else None
    want = [t.strip() for t in ts_list if t and t.strip()]
    present = {r[ti]: r for r in rows if len(r) > ti}
    found = [t for t in dict.fromkeys(want) if t in present]
    missing = [t for t in dict.fromkeys(want) if t not in present]
    files: list[str] = []
    for t in found:
        lf = present[t][li] if li is not None and len(present[t]) > li else ""
        files += [str(p) for p in run_files(data_dir, t, lf)]
    return {"found": found, "missing": missing, "files": files}


def delete_runs(data_dir: Path, ts_list: list[str]) -> dict:
    """Back up results.csv, drop the rows, move run files into runs/trash/."""
    data_dir = Path(data_dir)
    csv_path = data_dir / "results.csv"
    header, rows = _read_csv(csv_path)
    if not header:
        raise FileNotFoundError(f"no results.csv in {data_dir}")
    plan = plan_delete(data_dir, ts_list)
    if not plan["found"]:
        return {"removed": [], "missing": plan["missing"], "backup": None, "moved": []}
    ti = header.index("timestamp") if "timestamp" in header else 0
    stamp = _stamp()
    backup = _unique(data_dir / f"results.csv.bak-{stamp}")
    shutil.copy2(csv_path, backup)
    if _sha(backup) != _sha(csv_path):
        raise OSError("backup verification failed; nothing deleted")
    drop = set(plan["found"])
    keep = [r for r in rows if not (len(r) > ti and r[ti] in drop)]
    _write_csv_atomic(csv_path, header, keep)
    tdir = trash_dir(data_dir)
    tdir.mkdir(parents=True, exist_ok=True)
    moved: list[list[str]] = []
    for src in plan["files"]:
        sp = Path(src)
        if not sp.is_file():
            continue
        dst = _unique(tdir / sp.name)
        shutil.move(str(sp), str(dst))
        moved.append([str(sp), str(dst)])
    manifest = {
        "stamp": stamp,
        "created": time.time(),
        "backup": str(backup),
        "removed": plan["found"],
        "moved": moved,
        "csv_sha_after": _sha(csv_path),
    }
    mpath = tdir / f"delete-{stamp}.json"
    n = 1
    while mpath.exists() or mpath.with_name(mpath.stem + ".undone.json").exists():
        mpath = tdir / f"delete-{stamp}-{n}.json"
        n += 1
    mpath.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest["manifest"] = str(mpath)
    manifest["missing"] = plan["missing"]
    return manifest


def last_delete(data_dir: Path) -> dict | None:
    """Newest delete manifest that has not been undone."""
    tdir = trash_dir(data_dir)
    if not tdir.is_dir():
        return None
    cands = sorted(
        (p for p in tdir.glob("delete-*.json") if not p.name.endswith(".undone.json")),
        key=lambda p: (p.stat().st_mtime, p.name),
    )
    for p in reversed(cands):
        try:
            m = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        m["manifest"] = str(p)
        return m
    return None


def undo_last_delete(data_dir: Path) -> dict | None:
    """Restore results.csv from the last backup and move run files back.

    If new runs were added after the delete, they are kept (appended after the
    restored rows). The pre-undo CSV is itself backed up first.
    """
    data_dir = Path(data_dir)
    m = last_delete(data_dir)
    if not m:
        return None
    csv_path = data_dir / "results.csv"
    backup = Path(m["backup"])
    if not backup.is_file():
        raise FileNotFoundError(f"backup missing: {backup}")
    pre = None
    if csv_path.is_file():
        pre = _unique(data_dir / f"results.csv.bak-{_stamp()}-preundo")
        shutil.copy2(csv_path, pre)
    if _sha(csv_path) == m.get("csv_sha_after"):
        shutil.copy2(backup, csv_path)  # exact byte restore
        merged_extra = 0
    else:
        bh, brows = _read_csv(backup)
        ch, crows = _read_csv(csv_path)
        ti = bh.index("timestamp") if "timestamp" in bh else 0
        have = {r[ti] for r in brows if len(r) > ti}
        extra = []
        if ch:
            cti = ch.index("timestamp") if "timestamp" in ch else 0
            for r in crows:
                if len(r) > cti and r[cti] not in have:
                    d = dict(zip(ch, r))
                    extra.append([d.get(k, "") for k in bh])
        _write_csv_atomic(csv_path, bh, brows + extra)
        merged_extra = len(extra)
    restored, skipped = [], []
    for orig, dst in m.get("moved") or []:
        op, dp = Path(orig), Path(dst)
        if dp.is_file() and not op.exists():
            op.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dp), str(op))
            restored.append(orig)
        else:
            skipped.append(orig)
    mp = Path(m["manifest"])
    done = _unique(mp.with_name(mp.stem + ".undone.json"))
    os.replace(mp, done)
    return {
        "restored_rows": m.get("removed") or [],
        "restored_files": restored,
        "skipped_files": skipped,
        "kept_new_rows": merged_extra,
        "pre_undo_backup": str(pre) if pre else None,
        "from_backup": str(backup),
    }
