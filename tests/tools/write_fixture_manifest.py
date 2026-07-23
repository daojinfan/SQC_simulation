"""Write a provenance manifest for a pre-existing fixture closure."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support.fixture_loader import _aggregate, verify_fixture_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--source-authority", required=True)
    parser.add_argument("--fixed-clock-utc", default="2026-07-16T00:00:00.000000Z")
    args = parser.parse_args()
    root = args.fixture
    if not root.is_dir() or (root / "provenance.json").exists():
        raise ValueError("fixture must exist and not already have a manifest")
    files = []
    for path in sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: path.relative_to(root).as_posix().encode("utf-8")):
        raw = path.read_bytes()
        files.append({"path": path.relative_to(root).as_posix(), "byte_length": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest().upper()})
    rows = [(row["path"], row["byte_length"], row["raw_sha256"]) for row in files]
    generator = Path(__file__)
    payload = {
        "schema_version": "0.1", "fixture_id": root.name, "generator_version": "0.1",
        "generator_raw_sha256": hashlib.sha256(generator.read_bytes()).hexdigest().upper(),
        "source_authority": args.source_authority, "fixed_clock_utc": args.fixed_clock_utc,
        "files": files, "aggregate_sha256": _aggregate(rows),
    }
    (root / "provenance.json").write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    verify_fixture_manifest(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
