from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml


pytestmark = pytest.mark.contract


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict:
    value = yaml.load((WORKFLOWS / name).read_text("utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return value


def test_qualification_runs_once_per_promotion_path() -> None:
    qualification = _workflow("ci-pr.yml")
    release = _workflow("ci-release.yml")

    assert set(qualification["on"]) == {"pull_request", "workflow_call"}
    assert qualification["on"]["pull_request"]["branches"] == ["dev"]
    assert "push" not in qualification["on"]
    assert release["on"]["pull_request"]["branches"] == ["main"]
    assert release["jobs"]["pr-qualification"]["uses"] == "./.github/workflows/ci-pr.yml"
    assert release["jobs"]["release-gate-main"]["needs"] == [
        "candidate",
        "pr-qualification",
        "physics",
        "hosted-evidence",
    ]


def test_physics_platforms_are_isolated_and_run_in_parallel() -> None:
    physics = _workflow("ci-nightly-physics.yml")
    job = physics["jobs"]["physics"]
    matrix = job["strategy"]["matrix"]["include"]

    assert job["strategy"]["max-parallel"] == "2"
    assert {row["platform"] for row in matrix} == {"windows", "linux"}
    assert len({row["runner"] for row in matrix}) == 2
    source = (WORKFLOWS / "ci-nightly-physics.yml").read_text("utf-8")
    assert "physics-${{ matrix.platform }}-${{ github.run_id }}" in source
    assert "pip-${{ runner.os }}-" in source


def test_javascript_actions_use_current_node24_or_newer_majors() -> None:
    expected = {
        "checkout": 7,
        "setup-python": 7,
        "setup-node": 7,
        "cache": 6,
        "upload-artifact": 7,
    }
    found: set[str] = set()
    pattern = re.compile(r"actions/([a-z-]+)@v(\d+)")
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for action, raw_major in pattern.findall(path.read_text("utf-8")):
            if action not in expected:
                continue
            found.add(action)
            assert int(raw_major) >= expected[action], f"outdated action in {path.name}"
    assert found == set(expected)

    qualification = (WORKFLOWS / "ci-pr.yml").read_text("utf-8")
    assert "package-manager-cache: false" in qualification
