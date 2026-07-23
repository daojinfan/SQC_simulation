"""Run stage 1 device verification from VSCode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqvm.device import verify_device


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run stage 1 device verification")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/stage_01_device_model"),
        help="directory for generated verification artifacts",
    )
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    report = verify_device(
        path="configs/devices/2q1c2r.yaml",
        output_dir=args.output,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
