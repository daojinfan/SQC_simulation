"""Deterministically rebind the tracked Stage 4.1 production authority chain."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
STAGE41 = ROOT / "configs" / "control" / "stage41"
SOURCE = STAGE41 / "source_snapshot_v1.json"
AUTHORITY = STAGE41 / "production_authority_v1.json"
APPROVAL = STAGE41 / "production_approval_v1.json"
ENVIRONMENT = STAGE41 / "environment_snapshot_v1.json"
PUBLICATION = STAGE41 / "publication_policy_v1.json"
STAGE71 = ROOT / "configs" / "runtime" / "stage71"
ENTRANCE_AUTHORITY = STAGE71 / "entrance_authority_v1.json"
ENTRANCE_APPROVAL = STAGE71 / "entrance_approval_v1.json"


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def build() -> dict[Path, bytes]:
    source = load(SOURCE)
    rows = source.get("sources")
    if not isinstance(rows, list):
        raise ValueError("source snapshot sources must be a list")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "raw_sha256"}:
            raise ValueError("source snapshot row is invalid")
        path = ROOT / str(row["path"])
        path.resolve().relative_to(ROOT)
        row["raw_sha256"] = raw_sha256(path)
    source_raw = canonical_bytes(source)
    source_sha = hashlib.sha256(source_raw).hexdigest().upper()

    authority = load(AUTHORITY)
    authority["source_snapshot_sha256"] = source_sha
    authority["environment_snapshot_sha256"] = raw_sha256(ENVIRONMENT)
    authority["publication_policy_sha256"] = raw_sha256(PUBLICATION)
    payload = dict(authority)
    payload.pop("authority_id", None)
    authority["authority_id"] = canonical_sha256(payload)
    authority_raw = canonical_bytes(authority)
    authority_sha = hashlib.sha256(authority_raw).hexdigest().upper()

    approval = load(APPROVAL)
    approval["authority_id"] = authority["authority_id"]
    approval["authority_raw_sha256"] = authority_sha
    approval["context_bindings"] = {
        "environment_snapshot": raw_sha256(ENVIRONMENT),
        "publication_policy": raw_sha256(PUBLICATION),
        "source_snapshot": source_sha,
    }
    approval_raw = canonical_bytes(approval)

    entrance_authority = load(ENTRANCE_AUTHORITY)
    entrance_authority["stage4_1_approval"]["raw_sha256"] = hashlib.sha256(
        approval_raw
    ).hexdigest().upper()
    entrance_payload = dict(entrance_authority)
    entrance_payload.pop("authority_id", None)
    entrance_authority["authority_id"] = canonical_sha256(entrance_payload)
    entrance_authority_raw = canonical_bytes(entrance_authority)

    entrance_approval = load(ENTRANCE_APPROVAL)
    entrance_approval["authority_id"] = entrance_authority["authority_id"]
    entrance_approval["authority_raw_sha256"] = hashlib.sha256(
        entrance_authority_raw
    ).hexdigest().upper()
    return {
        SOURCE: source_raw,
        AUTHORITY: authority_raw,
        APPROVAL: approval_raw,
        ENTRANCE_AUTHORITY: entrance_authority_raw,
        ENTRANCE_APPROVAL: canonical_bytes(entrance_approval),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the authority chain needs regeneration",
    )
    arguments = parser.parse_args()
    generated = build()
    changed = [path for path, raw in generated.items() if path.read_bytes() != raw]
    if arguments.check:
        if changed:
            print("Stage 4.1 authority drift: " + ", ".join(path.name for path in changed))
            return 1
        print("Stage 4.1 authority chain is current")
        return 0
    for path in changed:
        path.write_bytes(generated[path])
        print(path.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
