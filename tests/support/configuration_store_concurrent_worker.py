from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from sqvm.web.configuration import (
    ConfigurationManagementError,
    PlatformConfigurationStore,
)


def main() -> int:
    repository_root = Path(sys.argv[1]).resolve()
    configuration_root = Path(sys.argv[2]).resolve()
    ready = Path(sys.argv[3]).resolve()
    go = Path(sys.argv[4]).resolve()
    result_path = Path(sys.argv[5]).resolve()
    operation_id = sys.argv[6]
    worker_name = sys.argv[7]
    store = PlatformConfigurationStore(repository_root, configuration_root)
    current = store.current_configuration("demo_2q1c2r")
    ready.write_text("ready", encoding="ascii")
    deadline = time.monotonic() + 15
    while not go.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("concurrency release file was not created")
        time.sleep(0.01)
    editable = current["editable"]
    editable["control_values"]["idle_flux_phi0"]["q1"] = (
        float(editable["control_values"]["idle_flux_phi0"]["q1"])
        + (0.001 if worker_name == "worker-0" else 0.002)
    )
    try:
        updated = store.update_current_configuration(
            "demo_2q1c2r",
            actor_id="concurrency.worker",
            expected_content_sha256=current["content_sha256"],
            name=current["name"],
            note=worker_name,
            editable=editable,
            operation_id=operation_id,
        )
        result = {
            "status": 200,
            "revision": updated["revision"],
            "transaction_id": updated["transaction"]["transaction_id"],
        }
    except ConfigurationManagementError as exc:
        result = {
            "status": exc.status,
            "code": exc.code,
            "message": str(exc),
        }
    result_path.write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
