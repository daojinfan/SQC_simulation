from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.web.configuration_transactions import ConfigurationTransactionManager


DEVICE = "demo_2q1c2r"


def _increment(workspace: Path) -> dict:
    path = workspace / "current" / f"{DEVICE}.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["revision"] += 1
    payload["content_sha256"] = "B" * 64
    path.write_bytes(canonical_json_bytes(payload))
    return payload


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    operation_id = sys.argv[2]
    cut = sys.argv[3]
    manager = ConfigurationTransactionManager(root)

    if cut == "before_head":
        manager._switch_head = lambda *_args, **_kwargs: os._exit(73)
    elif cut == "after_head":
        manager.materialize = lambda *_args, **_kwargs: os._exit(74)
    else:
        raise ValueError("unknown crash cut")

    manager.execute(
        device_id=DEVICE,
        operation_type="crash_test_update",
        operation_id=operation_id,
        request={"expected_revision": 1},
        legacy_root=root,
        transform=_increment,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
