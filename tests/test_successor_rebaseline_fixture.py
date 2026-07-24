from __future__ import annotations

import json
import os
from pathlib import Path

import pytest as _pytest

pytestmark = _pytest.mark.contract

import pytest

from sqvm.control.upstream import FROZEN_RECEIPT
from tests.support.fixture_loader import copy_fixture, fixture_path
from tests.tools.generate_successor_rebaseline_fixture import (
    _authority_chain,
    _canonical_bytes,
    refresh_manifest,
    validate_successor_fixture,
)


def test_successor_fixture_marks_old_authorities_unavailable_and_preserves_frozen_expectations():
    root = fixture_path("successor_rebaseline_authority_v1")
    chain = validate_successor_fixture(root)
    disposition = json.loads((root / "historical_authority_disposition.json").read_bytes())

    assert chain["status"] == "pending_production_selector"
    assert chain["historical_replay_claimed"] is False
    assert chain["physical_execution_claimed"] is False
    assert all(row["successor_evidence_status"] == "not_generated" for row in chain["stages"])
    assert all(row["old_authority_status"] == "unavailable" for row in disposition["stages"])
    assert disposition["frozen_expectation_ledger"] == [
        {"key": key, "path": path, "expected_raw_sha256": digest}
        for key, path, digest in FROZEN_RECEIPT
    ]


def test_successor_fixture_tamper_fails_closed(tmp_path: Path):
    target = copy_fixture("successor_rebaseline_authority_v1", tmp_path / "fixture")
    chain = target / "authority_chain.json"
    chain.write_bytes(chain.read_bytes() + b" ")

    with pytest.raises(ValueError, match="bytes do not match"):
        validate_successor_fixture(target)


def test_successor_fixture_rejects_rehashed_path_misdirection(tmp_path: Path):
    target = copy_fixture("successor_rebaseline_authority_v1", tmp_path / "fixture")
    provenance = target / "provisional_source_provenance.json"
    payload = json.loads(provenance.read_bytes())
    payload["source_files"][0]["path"] = "output/stage_03_solver_validation/eigsh_validation.json"
    provenance.write_bytes(_canonical_bytes(payload))
    disposition = target / "historical_authority_disposition.json"
    (target / "authority_chain.json").write_bytes(
        _canonical_bytes(_authority_chain(disposition.read_bytes(), provenance.read_bytes()))
    )
    refresh_manifest(target)

    with pytest.raises(ValueError, match="paths are invalid"):
        validate_successor_fixture(target)


def test_successor_fixture_rejects_hardlinked_authority_chain(tmp_path: Path):
    target = copy_fixture("successor_rebaseline_authority_v1", tmp_path / "fixture")
    authority_chain = target / "authority_chain.json"
    external = tmp_path / "external_authority_chain.json"
    external.write_bytes(authority_chain.read_bytes())
    authority_chain.unlink()
    os.link(external, authority_chain)

    with pytest.raises(ValueError, match="is linked"):
        validate_successor_fixture(target)
