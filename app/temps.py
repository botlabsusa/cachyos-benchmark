"""Live CPU/GPU temperature reading (lm_sensors + nvidia-smi).

Pure parsing helpers are separate from the subprocess calls so they can be
tested without hardware. Every external call has a short timeout, and the
caller is expected to poll sequentially (one reading at a time), so readings
can never pile up.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field

# Color bands for the live readout (°C)
TEMP_AMBER_C = 70.0
TEMP_RED_C = 85.0

NVIDIA_QUERY = [
    "nvidia-smi",
    "--query-gpu=temperature.gpu,utilization.gpu,power.draw",
    "--format=csv,noheader,nounits",
]


def temp_level(t: float | None) -> str:
    """'green' < 70 °C, 'amber' 70–85 °C, 'red' > 85 °C, 'none' when unknown."""
    if t is None:
        return "none"
    if t > TEMP_RED_C:
        return "red"
    if t >= TEMP_AMBER_C:
        return "amber"
    return "green"


def _num(v) -> float | None:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _input_of(feature) -> float | None:
    """First *_input value in a sensors -j feature dict."""
    if not isinstance(feature, dict):
        return None
    for k, v in feature.items():
        if k.endswith("_input"):
            return _num(v)
    return None


def parse_sensors_json(text: str) -> float | None:
    """CPU package temp from `sensors -j` output.

    Order: coretemp 'Package id 0' -> max coretemp 'Core N' ->
    k10temp/zenpower Tctl/Tdie (AMD) -> None.
    """
    try:
        data = json.loads(text or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    core_max: float | None = None
    amd: float | None = None
    for chip, feats in data.items():
        if not isinstance(feats, dict):
            continue
        name = str(chip).lower()
        if name.startswith("coretemp"):
            pkg = _input_of(feats.get("Package id 0"))
            if pkg is not None:
                return pkg
            for fname, feat in feats.items():
                if str(fname).startswith("Core "):
                    v = _input_of(feat)
                    if v is not None and (core_max is None or v > core_max):
                        core_max = v
        elif name.startswith(("k10temp", "zenpower")):
            for key in ("Tctl", "Tdie"):
                v = _input_of(feats.get(key))
                if v is not None:
                    amd = v if amd is None else max(amd, v)
    return core_max if core_max is not None else amd


def parse_nvidia_smi(text: str) -> tuple[float | None, float | None, float | None]:
    """(temp_C, util_pct, power_W) from the first GPU line; '[N/A]' -> None."""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        parts += [""] * (3 - len(parts))
        return _num(parts[0]), _num(parts[1]), _num(parts[2])
    return None, None, None


@dataclass
class TempSample:
    t: float = field(default_factory=time.time)
    cpu_c: float | None = None
    gpu_c: float | None = None
    gpu_util: float | None = None
    gpu_power_w: float | None = None
    cpu_source: str = ""
    gpu_source: str = ""


def read_temps(timeout: float = 1.5) -> TempSample:
    """One blocking reading. Missing tools are skipped quietly."""
    s = TempSample()
    if shutil.which("sensors"):
        try:
            out = subprocess.run(
                ["sensors", "-j"], capture_output=True, text=True,
                errors="ignore", timeout=timeout,
            ).stdout
            s.cpu_c = parse_sensors_json(out)
            s.cpu_source = "sensors" if s.cpu_c is not None else "sensors (no CPU sensor)"
        except (subprocess.TimeoutExpired, OSError):
            s.cpu_source = "sensors (timeout)"
    else:
        s.cpu_source = "missing (install lm_sensors)"
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                NVIDIA_QUERY, capture_output=True, text=True,
                errors="ignore", timeout=timeout,
            ).stdout
            s.gpu_c, s.gpu_util, s.gpu_power_w = parse_nvidia_smi(out)
            s.gpu_source = "nvidia-smi"
        except (subprocess.TimeoutExpired, OSError):
            s.gpu_source = "nvidia-smi (timeout)"
    else:
        s.gpu_source = "no nvidia-smi"
    return s


def format_sample(s: TempSample) -> str:
    def f(v, unit):
        return "n/a" if v is None else f"{v:.0f}{unit}"
    gpu = f"GPU {f(s.gpu_c, '°C')}"
    if s.gpu_c is not None:
        gpu += f"  util {f(s.gpu_util, '%')}  power {f(s.gpu_power_w, ' W')}"
    return f"CPU {f(s.cpu_c, '°C')} [{s.cpu_source}]   {gpu} [{s.gpu_source}]"


TEMPS_CSV_HEADER = "epoch,elapsed_s,cpu_C,gpu_C,gpu_util_pct,gpu_power_W\n"


def temps_csv_line(s: TempSample, start: float) -> str:
    def f(v):
        return "" if v is None else f"{v:.1f}"
    return f"{s.t:.1f},{s.t - start:.1f},{f(s.cpu_c)},{f(s.gpu_c)},{f(s.gpu_util)},{f(s.gpu_power_w)}\n"
