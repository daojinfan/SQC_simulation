"""Josephson junction parameter resolution."""

from __future__ import annotations

from dataclasses import dataclass
from math import pi

from sqvm.device.spec import DeviceSpec


PHI0_WB = 2.067833848e-15
E_CHARGE_C = 1.602176634e-19
PLANCK_J_S = 6.62607015e-34
DELTA_AL_EV = 180e-6


@dataclass(frozen=True, slots=True)
class JunctionParameterRow:
    component: str
    junction: str
    rn_ohm: float
    ej_GHz: float
    source: str = "rn"


@dataclass(frozen=True, slots=True)
class JunctionParameterTable:
    rows: tuple[JunctionParameterRow, ...]

    def ej_sum_by_component(self) -> dict[str, float]:
        sums: dict[str, float] = {}
        for row in self.rows:
            sums[row.component] = sums.get(row.component, 0.0) + row.ej_GHz
        return sums


def rn_to_ic_ampere(rn_ohm: float, *, gap_ev: float = DELTA_AL_EV) -> float:
    if rn_ohm <= 0:
        raise ValueError("rn_ohm must be positive")
    delta_joule = gap_ev * E_CHARGE_C
    return pi * delta_joule / (2.0 * E_CHARGE_C * rn_ohm)


def ic_to_ej_GHz(ic_ampere: float) -> float:
    if ic_ampere <= 0:
        raise ValueError("ic_ampere must be positive")
    ej_joule = PHI0_WB * ic_ampere / (2.0 * pi)
    return ej_joule / PLANCK_J_S / 1e9


def rn_to_ej_GHz(rn_ohm: float, *, gap_ev: float = DELTA_AL_EV) -> float:
    return ic_to_ej_GHz(rn_to_ic_ampere(rn_ohm, gap_ev=gap_ev))


def resolve_junction_parameters(device: DeviceSpec) -> JunctionParameterTable:
    rows: list[JunctionParameterRow] = []
    for name in ("q1", "q2", "c"):
        component = device.components.get(name)
        if component is None or component.squid is None:
            continue
        rows.append(
            JunctionParameterRow(
                component=name,
                junction="j1",
                rn_ohm=component.squid.rn1_ohm,
                ej_GHz=rn_to_ej_GHz(component.squid.rn1_ohm),
            )
        )
        rows.append(
            JunctionParameterRow(
                component=name,
                junction="j2",
                rn_ohm=component.squid.rn2_ohm,
                ej_GHz=rn_to_ej_GHz(component.squid.rn2_ohm),
            )
        )
    return JunctionParameterTable(rows=tuple(rows))
