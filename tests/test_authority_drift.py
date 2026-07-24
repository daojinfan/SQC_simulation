from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.evidence

from pathlib import Path

import pytest

from tools.verify_authority_drift import verify


@pytest.mark.release
def test_frozen_stage_authorities_match_the_wp2_baseline() -> None:
    root = Path(__file__).resolve().parents[1]
    assert verify(root, root / "tests" / "authority_baseline_v1.json") == []
