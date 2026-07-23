"""Create the one-generation configuration reference fixture from a supplied closure."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support.fixture_loader import _aggregate, verify_fixture_manifest


FIXTURE_ID = "platform_configuration_reference_v1"
FIXED_CLOCK = "2026-07-16T00:00:00.000000Z"


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_manifest(target: Path) -> None:
    files = []
    for path in sorted((path for path in target.rglob("*") if path.is_file() and path.name != "provenance.json"), key=lambda path: path.relative_to(target).as_posix().encode("utf-8")):
        raw = path.read_bytes()
        files.append({"path": path.relative_to(target).as_posix(), "byte_length": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest().upper()})
    rows = [(row["path"], row["byte_length"], row["raw_sha256"]) for row in files]
    payload = {
        "schema_version": "0.1", "fixture_id": FIXTURE_ID, "generator_version": "0.1",
        "generator_raw_sha256": _raw_sha256(Path(__file__)),
        "source_authority": "current_output_minimal_applied_closure",
        "fixed_clock_utc": FIXED_CLOCK, "files": files, "aggregate_sha256": _aggregate(rows),
    }
    target.joinpath("provenance.json").write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")


def generate(source_root: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"fixture target already exists: {target}")
    current_path = source_root / "current" / "demo_2q1c2r.json"
    active_path = source_root / "active" / "demo_2q1c2r.json"
    current = json.loads(current_path.read_bytes())
    active = json.loads(active_path.read_bytes())
    snapshot_id = active["snapshot_id"]
    snapshot_dir = source_root / "snapshots" / snapshot_id
    source_candidate = snapshot_dir / "source_candidate.json"
    if not source_candidate.is_file():
        raise ValueError("active snapshot has no source candidate sidecar")
    candidate = json.loads(source_candidate.read_bytes())
    if current.get("source_candidate") != candidate:
        raise ValueError("current and active snapshot candidate sources differ")
    audit = next(
        (path for path in (source_root / "audit").glob("*.json")
         if json.loads(path.read_bytes()).get("event") == "experiment_candidates_applied_to_current"
         and json.loads(path.read_bytes()).get("details", {}).get("experiment_run_id") == candidate.get("experiment_run_id")),
        None,
    )
    if audit is None:
        raise ValueError("applied-candidate audit is unavailable")
    for source, relative in (
        (current_path, "platform-configurations/current/demo_2q1c2r.json"),
        (active_path, "platform-configurations/active/demo_2q1c2r.json"),
        (snapshot_dir / "snapshot.json", f"platform-configurations/snapshots/{snapshot_id}/snapshot.json"),
        (source_candidate, f"platform-configurations/snapshots/{snapshot_id}/source_candidate.json"),
        (audit, f"platform-configurations/audit/{audit.name}"),
    ):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    _write_manifest(target)
    verify_fixture_manifest(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--verify-against", type=Path)
    args = parser.parse_args()
    generate(args.source_root, args.target)
    if args.verify_against is not None:
        verify_fixture_manifest(args.verify_against)
        left = {path.relative_to(args.target): path.read_bytes() for path in args.target.rglob("*") if path.is_file()}
        right = {path.relative_to(args.verify_against): path.read_bytes() for path in args.verify_against.rglob("*") if path.is_file()}
        if left != right:
            raise ValueError("generated fixture differs from --verify-against")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
