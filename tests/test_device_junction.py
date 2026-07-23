import pytest as _pytest

pytestmark = _pytest.mark.contract

from dataclasses import replace
from pathlib import Path

import pytest

from sqvm.device import load_device, resolve_junction_parameters
from sqvm.device.junction import rn_to_ej_GHz


CONFIG = Path("configs/devices/2q1c2r.yaml")


def test_rn_to_ej_positive():
    assert rn_to_ej_GHz(18038.0) > 0


def test_squid_ej_sum_from_two_rn():
    table = resolve_junction_parameters(load_device(CONFIG))
    sums = table.ej_sum_by_component()
    assert set(sums) == {"q1", "q2", "c"}
    assert sums["q1"] > 0
    q1_rows = [row for row in table.rows if row.component == "q1"]
    assert q1_rows[0].ej_GHz == pytest.approx(q1_rows[1].ej_GHz)


def test_reject_unresolvable_squid():
    device = load_device(CONFIG)
    q1 = replace(device.components["q1"], squid=None)
    components = dict(device.components)
    components["q1"] = q1
    table = resolve_junction_parameters(replace(device, components=components))
    assert {row.component for row in table.rows} == {"q2", "c"}
