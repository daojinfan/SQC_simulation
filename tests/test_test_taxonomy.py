from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.contract

from pathlib import Path
import json

from tests.conftest import ITEM_PRIMARY_MARKERS, MODULE_PRIMARY_MARKERS, PRIMARY_MARKERS


MIXED_MODULE_PRIMARY_MARKERS = {
    "tests/test_run_circuits.py": {
        "integration": 10,
        "physics_slow": 1,
    },
    "tests/test_stage51_verified_control_admission.py": {
        "integration": 7,
        "physics_slow": 1,
    },
}


def test_every_test_module_has_an_explicit_primary_owner() -> None:
    root = Path(__file__).parent
    modules = {path.name for path in root.glob("test_*.py")}
    assert modules == set(MODULE_PRIMARY_MARKERS)
    assert set(MODULE_PRIMARY_MARKERS.values()) <= PRIMARY_MARKERS


def test_mixed_modules_declare_complete_explicit_primary_markers() -> None:
    root = Path(__file__).parent
    expected_overrides = {
        "tests/test_run_circuits.py::test_run_circuits_returns_final_q1_q2_probabilities_from_verified_evolution": "physics_slow",
        "tests/test_stage51_verified_control_admission.py::test_real_stage41_handle_runs_through_production_qutip_worker_and_replay": "physics_slow",
    }

    assert ITEM_PRIMARY_MARKERS == expected_overrides
    for module, expected_counts in MIXED_MODULE_PRIMARY_MARKERS.items():
        source = (root / Path(module).name).read_text(encoding="utf-8")
        assert "pytestmark =" not in source
        for marker, expected_count in expected_counts.items():
            assert source.count(f"@pytest.mark.{marker}") == expected_count


def test_physics_suite_count_lock_matches_current_baseline() -> None:
    lock = json.loads((Path(__file__).parent / "physics_suite_lock.json").read_text(encoding="utf-8"))
    assert lock == {
        "schema_version": "0.1",
        "primary_marker": "physics_slow",
        "expected_testcases": 193,
    }
