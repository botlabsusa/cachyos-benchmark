import pytest
import temps
from pathlib import Path

def test_temp_level():
    assert temps.temp_level(None) == "none"
    assert temps.temp_level(60.0) == "green"
    assert temps.temp_level(70.0) == "amber"
    assert temps.temp_level(75.0) == "amber"
    assert temps.temp_level(85.0) == "amber"  # red is > 85 C
    assert temps.temp_level(85.1) == "red"
    assert temps.temp_level(90.0) == "red"

def test__num():
    assert temps._num("42.5") == 42.5
    assert temps._num("  42.5  ") == 42.5
    assert temps._num("invalid") is None
    assert temps._num(None) is None
    assert temps._num(float("nan")) is None

def test__input_of():
    assert temps._input_of({"temp1_input": "42.5"}) == 42.5
    assert temps._input_of({"temp1_input": "invalid"}) is None
    assert temps._input_of({"temp1_input": None}) is None
    assert temps._input_of({}) is None
    assert temps._input_of(None) is None

def test_parse_sensors_json():
    # Test valid JSON with coretemp
    json_data = '''
    {
        "coretemp": {
            "Package id 0": {"temp1_input": "42.5"},
            "Core 0": {"temp1_input": "40.0"},
            "Core 1": {"temp1_input": "45.0"}
        }
    }
    '''
    assert temps.parse_sensors_json(json_data) == 42.5

    # Test valid JSON with AMD
    json_data = '''
    {
        "k10temp": {
            "Tctl": {"temp1_input": "50.0"},
            "Tdie": {"temp1_input": "45.0"}
        }
    }
    '''
    assert temps.parse_sensors_json(json_data) == 50.0

    # Test invalid JSON
    assert temps.parse_sensors_json("invalid json") is None

    # Test empty JSON
    assert temps.parse_sensors_json("") is None

def test_parse_nvidia_smi():
    # Test valid CSV line
    csv_line = "65, 30, 120"
    assert temps.parse_nvidia_smi(csv_line) == (65.0, 30.0, 120.0)

    # Test with N/A values
    csv_line = "N/A, N/A, N/A"
    assert temps.parse_nvidia_smi(csv_line) == (None, None, None)

    # Test empty input
    assert temps.parse_nvidia_smi("") == (None, None, None)

def test_format_sample():
    sample = temps.TempSample(
        cpu_c=65.0,
        gpu_c=75.0,
        gpu_util=30.0,
        gpu_power_w=120.0,
        cpu_source="sensors",
        gpu_source="nvidia-smi"
    )
    result = temps.format_sample(sample)
    assert "CPU 65°C" in result
    assert "GPU 75°C" in result
    assert "util 30%" in result
    assert "power 120 W" in result

def test_temps_csv_line(tmp_path):
    sample = temps.TempSample(
        cpu_c=65.0,
        gpu_c=75.0,
        gpu_util=30.0,
        gpu_power_w=120.0
    )
    start = 1000.0
    result = temps.temps_csv_line(sample, start)
    assert "65.0" in result
    assert "75.0" in result
    assert "30.0" in result
    assert "120.0" in result

def test_parse_sensors_json_no_cores():
    # Test case where no coretemp or AMD sensors are found
    json_data = '''
    {
        "other_chip": {
            "temp1_input": "42.5"
        }
    }
    '''
    assert temps.parse_sensors_json(json_data) is None

def test_parse_nvidia_smi_partial():
    # Test with fewer than 3 values
    csv_line = "65, 30"
    assert temps.parse_nvidia_smi(csv_line) == (65.0, 30.0, None)
