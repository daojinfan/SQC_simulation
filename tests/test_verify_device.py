import pytest as _pytest

pytestmark = _pytest.mark.contract

import json
import subprocess
import sys
from pathlib import Path

import nbformat

from sqvm.device import verify_device


CONFIG = Path("configs/devices/2q1c2r.yaml")


def test_verify_device_writes_all_artifacts(tmp_path):
    report = verify_device(CONFIG, tmp_path)
    assert report.ok
    assert (tmp_path / "device_artifacts.json").exists()
    assert (tmp_path / "verification.ipynb").exists()


def test_verify_device_artifacts_contains_pass_fail(tmp_path):
    verify_device(CONFIG, tmp_path)
    data = json.loads((tmp_path / "device_artifacts.json").read_text(encoding="utf-8"))
    assert data["artifact_type"] == "stage_01_device_model"
    assert data["artifact_version"] == "0.1"
    assert data["checks"]
    assert all("passed" in check for check in data["checks"])


def test_verification_notebook_reads_device_artifacts(tmp_path):
    verify_device(CONFIG, tmp_path)
    notebook = nbformat.read(tmp_path / "verification.ipynb", as_version=4)
    sources = "\n".join(cell.source for cell in notebook.cells)
    assert "device_artifacts.json" in sources
    assert any(cell.outputs for cell in notebook.cells if cell.cell_type == "code")


def test_verification_notebook_embeds_capacitance_heatmap(tmp_path):
    verify_device(CONFIG, tmp_path)
    notebook = nbformat.read(tmp_path / "verification.ipynb", as_version=4)
    markdown = "\n".join(cell.source for cell in notebook.cells if cell.cell_type == "markdown")
    assert "阶段 1 器件验证" in markdown
    assert any(
        "image/png" in output.data
        for cell in notebook.cells
        for output in getattr(cell, "outputs", [])
        if hasattr(output, "data")
    )


def test_cli_verify_device_success(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sqvm",
            "verify-device",
            str(CONFIG),
            "--output",
            str(tmp_path),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert (tmp_path / "device_artifacts.json").exists()
    assert (tmp_path / "verification.ipynb").exists()


def test_vscode_stage1_runner_smoke():
    result = subprocess.run(
        [sys.executable, "scripts/run_stage_01_device.py"],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
