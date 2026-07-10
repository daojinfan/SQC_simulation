"""Effective SQUID Josephson energies for Hamiltonian construction."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, sqrt
from typing import Any

from sqvm.hamiltonian.artifacts import DeviceArtifacts


@dataclass(frozen=True, slots=True)
class EffectiveJunction:
    mode: str
    component: str
    ej1_GHz: float
    ej2_GHz: float
    flux_bias_phi0: float
    ej_effective_GHz: float
    formula: str = "sqrt((EJ1+EJ2)^2*cos(pi*phi)^2 + (EJ1-EJ2)^2*sin(pi*phi)^2)"


def resolve_effective_junctions(device_artifacts: DeviceArtifacts) -> tuple[EffectiveJunction, ...]:
    rows_by_component: dict[str, list[dict[str, Any]]] = {"q1": [], "q2": [], "c": []}
    for row in device_artifacts.payload["junction_parameters"]:
        component = row.get("component")
        if component in rows_by_component:
            rows_by_component[component].append(row)

    components = device_artifacts.payload["components"]
    resolved: list[EffectiveJunction] = []
    for mode in ("q1", "c", "q2"):
        rows = sorted(rows_by_component[mode], key=lambda item: str(item.get("junction", "")))
        if len(rows) != 2:
            raise ValueError(f"junction_parameters for {mode} must contain exactly two rows")
        squid = components.get(mode, {}).get("squid")
        if not isinstance(squid, dict) or "flux_bias_phi0" not in squid:
            raise ValueError(f"components.{mode}.squid.flux_bias_phi0 is required")
        ej1 = float(rows[0]["ej_GHz"])
        ej2 = float(rows[1]["ej_GHz"])
        flux = float(squid["flux_bias_phi0"])
        resolved.append(
            EffectiveJunction(
                mode=mode,
                component=mode,
                ej1_GHz=ej1,
                ej2_GHz=ej2,
                flux_bias_phi0=flux,
                ej_effective_GHz=effective_ej_GHz(ej1, ej2, flux),
            )
        )
    return tuple(resolved)


def effective_ej_GHz(ej1_GHz: float, ej2_GHz: float, flux_bias_phi0: float) -> float:
    ej_sum = ej1_GHz + ej2_GHz
    ej_delta = ej1_GHz - ej2_GHz
    phase = pi * flux_bias_phi0
    return sqrt((ej_sum * cos(phase)) ** 2 + (ej_delta * np_sin(phase)) ** 2)


def np_sin(value: float) -> float:
    from math import sin

    return sin(value)
