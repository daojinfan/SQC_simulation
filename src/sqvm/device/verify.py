"""Stage 1 device verification orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqvm.device.artifacts import write_device_artifacts
from sqvm.device.notebook import write_verification_notebook
from sqvm.device.spec import load_device


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class VerificationReport:
    ok: bool
    device_name: str
    checks: tuple[VerificationCheck, ...]
    artifacts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "device_name": self.device_name,
            "checks": [
                {"name": check.name, "passed": check.passed, "message": check.message}
                for check in self.checks
            ],
            "artifacts": dict(self.artifacts),
        }


def verify_device(path: str | Path, output_dir: str | Path) -> VerificationReport:
    device = load_device(path)
    artifact_set = write_device_artifacts(device, output_dir)
    notebook_path = Path(output_dir) / "verification.ipynb"
    write_verification_notebook(artifact_set.device_artifacts, notebook_path)
    payload = json.loads(artifact_set.device_artifacts.read_text(encoding="utf-8"))
    checks = tuple(
        VerificationCheck(name=item["name"], passed=bool(item["passed"]), message=item["message"])
        for item in payload["checks"]
    )
    return VerificationReport(
        ok=bool(payload["validation"]["ok"]) and all(check.passed for check in checks),
        device_name=device.name,
        checks=checks,
        artifacts={
            "device_artifacts": str(artifact_set.device_artifacts),
            "verification_notebook": str(notebook_path),
        },
    )
