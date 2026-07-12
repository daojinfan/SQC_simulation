"""Run stage 1 device verification from VSCode."""

from __future__ import annotations

import json

from sqvm.device import verify_device


if __name__ == "__main__":
    report = verify_device(
        path="configs/devices/2q1c2r.yaml",
        output_dir="output/stage_01_device_model",
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
