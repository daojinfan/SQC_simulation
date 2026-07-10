"""Command line interface for sqvm."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from sqvm.device import verify_device
from sqvm.hamiltonian import verify_hamiltonian


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sqvm")
    subparsers = parser.add_subparsers(dest="command")

    verify = subparsers.add_parser("verify-device", help="verify a 2q1c2r device config")
    verify.add_argument("config", type=Path)
    verify.add_argument("--output", type=Path, default=Path("output/stage_01_device_model"))

    verify_h = subparsers.add_parser("verify-hamiltonian", help="verify a 2q1c Hamiltonian config")
    verify_h.add_argument("config", type=Path)
    verify_h.add_argument("--output", type=Path, default=Path("output/stage_02_hamiltonian"))

    args = parser.parse_args(argv)
    if args.command == "verify-device":
        try:
            report = verify_device(args.config, args.output)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    if args.command == "verify-hamiltonian":
        try:
            report = verify_hamiltonian(args.config, args.output)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
