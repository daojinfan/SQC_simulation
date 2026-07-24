from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.contract

import subprocess
import sys
from pathlib import Path

import pytest

from tools.generate_test_locks import verify_runtime_dependency_input


def test_test_lock_metadata_and_hashes_verify() -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "tools" / "generate_test_locks.py"), "--verify"], check=True)


def test_test_lock_rejects_runtime_dependency_drift(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    input_path = tmp_path / "requirements-test-py312.in"
    input_path.write_text(
        (root / "requirements-test-py312.in").read_text(encoding="utf-8").replace(
            "numpy>=1.25", "numpy>=9.99", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="runtime dependencies drift"):
        verify_runtime_dependency_input(input_path)
