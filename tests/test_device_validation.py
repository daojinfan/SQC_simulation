import pytest as _pytest

pytestmark = _pytest.mark.contract

from dataclasses import replace
from pathlib import Path

from sqvm.device import load_device, validate_device
from sqvm.device.spec import CapacitorSpec


CONFIG = Path("configs/devices/2q1c2r.yaml")


def test_valid_device_has_no_errors():
    report = validate_device(load_device(CONFIG))
    assert report.ok
    assert report.errors == ()


def test_reject_negative_capacitance():
    device = load_device(CONFIG)
    bad = replace(device, capacitors=(CapacitorSpec("bad", "q1_p", "c", -1.0),))
    report = validate_device(bad)
    assert not report.ok
    assert any("capacitance_fF" in issue.path for issue in report.errors)


def test_reject_unknown_capacitor_node():
    device = load_device(CONFIG)
    bad = replace(device, capacitors=(CapacitorSpec("bad", "q1_p", "missing", 1.0),))
    report = validate_device(bad)
    assert not report.ok
    assert any("unknown node" in issue.message for issue in report.errors)


def test_reject_unknown_prior_field():
    device = load_device(CONFIG)
    report = validate_device(replace(device, priors={"q1": {"bad_field": 1.0}}))
    assert not report.ok
    assert any("priors" in issue.path for issue in report.errors)
