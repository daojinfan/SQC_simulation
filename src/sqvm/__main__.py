"""Command line interface for sqvm."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import yaml

from sqvm.device import verify_device
from sqvm.hamiltonian import verify_hamiltonian
from sqvm.control import verify_control_signal
from sqvm.evolution import run_stage5_evolution
from sqvm.spectrum import verify_q1_q2_coupling, verify_static_spectrum


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sqvm")
    subparsers = parser.add_subparsers(dest="command")

    verify = subparsers.add_parser("verify-device", help="verify a 2q1c2r device config")
    verify.add_argument("config", type=Path)
    verify.add_argument("--output", type=Path, default=Path("output/stage_01_device_model"))

    verify_h = subparsers.add_parser("verify-hamiltonian", help="verify a 2q1c Hamiltonian config")
    verify_h.add_argument("config", type=Path)
    verify_h.add_argument("--output", type=Path, default=Path("output/stage_02_hamiltonian"))

    verify_s = subparsers.add_parser("verify-spectrum", help="verify a 2q1c static spectrum config")
    verify_s.add_argument("config", type=Path)
    verify_s.add_argument("--output", type=Path, default=Path("output/stage_03_static_spectrum"))

    verify_c = subparsers.add_parser("verify-control", help="compile and verify a Stage 4 control schedule")
    verify_c.add_argument("config", type=Path)
    verify_c.add_argument("schedule", type=Path)
    verify_c.add_argument("--output", type=Path, default=Path("output/stage_04_control_signal"))

    verify_e = subparsers.add_parser("verify-evolution", help="run a Stage 5 QuTiP evolution profile")
    verify_e.add_argument("config", type=Path)
    verify_e.add_argument("--output", type=Path, default=Path("output/stage_05_qutip_evolution"))

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

    if args.command == "verify-spectrum":
        try:
            raw = yaml.safe_load(args.config.read_text(encoding="utf-8"))
            spectrum = raw.get("spectrum", {}) if isinstance(raw, dict) else {}
            if raw.get("schema_version") == "0.2" and spectrum.get("experiment_type") == "q1_q2_coupling_vs_coupler":
                report = verify_q1_q2_coupling(args.config, args.output)
            else:
                report = verify_static_spectrum(args.config, args.output)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    if args.command == "verify-control":
        try:
            receipt = verify_control_signal(args.config, args.schedule, args.output)
        except (ValueError, FileExistsError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        payload = receipt.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if payload["profile"] == "smoke":
            return 0 if payload["execution_succeeded"] and not payload["blocking_reasons"] else 1
        return 0 if payload["acceptance_candidate_ready"] else 1

    if args.command == "verify-evolution":
        try:
            result = run_stage5_evolution(args.config, args.output)
        except (ValueError, FileExistsError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        payload = result.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] == "smoke_complete" else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
