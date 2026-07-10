"""Hamiltonian artifact I/O."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DeviceArtifacts:
    path: Path
    payload: dict[str, Any]

    @property
    def node_order(self) -> tuple[str, ...]:
        return tuple(self.payload["capacitance_matrix"]["nodes"])

    @property
    def node_capacitance_matrix_fF(self) -> tuple[tuple[float, ...], ...]:
        return tuple(tuple(float(value) for value in row) for row in self.payload["capacitance_matrix"]["matrix_fF"])


@dataclass(frozen=True, slots=True)
class HamiltonianArtifactSet:
    root: Path
    hamiltonian_artifacts: Path


def load_device_artifacts(path: str | Path) -> DeviceArtifacts:
    artifact_path = Path(path)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{artifact_path}: cannot read device artifacts: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{artifact_path}: invalid JSON: {exc}") from exc
    if payload.get("artifact_type") != "stage_01_device_model":
        raise ValueError("device artifact_type must be stage_01_device_model")
    for key in ("capacitance_matrix", "junction_parameters", "components"):
        if key not in payload:
            raise ValueError(f"device artifacts missing {key}")
    return DeviceArtifacts(path=artifact_path, payload=payload)


def write_hamiltonian_artifacts(payload: dict[str, Any], output_dir: str | Path) -> HamiltonianArtifactSet:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "hamiltonian_artifacts.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return HamiltonianArtifactSet(root=root, hamiltonian_artifacts=path)
