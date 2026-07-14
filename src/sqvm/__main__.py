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
from sqvm.runtime import (
    expand_scan,
    load_experiment_request,
    load_experiment_run,
    point_table_payload,
    rebuild_run_catalog,
    recover_interrupted_run,
    recover_terminal_resource_lock,
    request_run_cancellation,
    run_experiment,
    verify_experiment_run,
)


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

    preview_x = subparsers.add_parser("preview-experiment", help="admit and expand a Stage 6 experiment request")
    preview_x.add_argument("config", type=Path)

    run_x = subparsers.add_parser("run-experiment", help="run a Stage 6 experiment request")
    run_x.add_argument("config", type=Path)
    run_x.add_argument("--output", type=Path, default=Path("output/stage_06_experiment_runtime_smoke"))

    inspect_x = subparsers.add_parser("inspect-run", help="inspect an immutable Stage 6 run")
    inspect_x.add_argument("run_dir", type=Path)

    verify_x = subparsers.add_parser("verify-run", help="independently verify an immutable Stage 6 run")
    verify_x.add_argument("run_dir", type=Path)

    cancel_x = subparsers.add_parser("cancel-run", help="request cooperative cancellation of a Stage 6 run")
    cancel_x.add_argument("output_root", type=Path)
    cancel_x.add_argument("run_id")

    recover_x = subparsers.add_parser("recover-run", help="quarantine and record an interrupted Stage 6 run")
    recover_x.add_argument("output_root", type=Path)
    recover_x.add_argument("run_id")

    recover_lock_x = subparsers.add_parser("recover-resource-lock", help="release a verified terminal run resource lock")
    recover_lock_x.add_argument("output_root", type=Path)
    recover_lock_x.add_argument("run_id")

    rebuild_x = subparsers.add_parser("rebuild-run-catalog", help="rebuild the Stage 6 SQLite catalog")
    rebuild_x.add_argument("output_root", type=Path)

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

    if args.command == "preview-experiment":
        try:
            request = load_experiment_request(args.config)
            points = expand_scan(request)
        except (ValueError, OSError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, "point_count": len(points), "point_table": point_table_payload(request)}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "run-experiment":
        try:
            result = run_experiment(args.config, args.output)
        except (ValueError, OSError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.status == "completed" else 1

    if args.command == "inspect-run":
        try:
            run = load_experiment_run(args.run_dir)
        except (ValueError, OSError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"manifest": dict(run.manifest), "report": dict(run.report), "receipt": dict(run.receipt)}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "verify-run":
        report = verify_experiment_run(args.run_dir)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    if args.command == "cancel-run":
        try:
            receipt = request_run_cancellation(args.output_root, args.run_id)
        except (ValueError, OSError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "recover-run":
        try:
            result = recover_interrupted_run(args.output_root, args.run_id)
        except (ValueError, OSError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"recovery_id": result.recovery_id, "original_run_id": result.original_run_id, "run_dir": result.run_dir.as_posix(), "status": result.status}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "recover-resource-lock":
        try:
            result = recover_terminal_resource_lock(args.output_root, args.run_id)
        except (ValueError, OSError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"run_id": result.run_id, "authorization_path": result.authorization_path.as_posix(), "resource_lock_sha256": result.resource_lock_sha256}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "rebuild-run-catalog":
        result = rebuild_run_catalog(args.output_root)
        print(json.dumps({"ok": result.ok, "indexed_runs": result.indexed_runs, "catalog_path": result.catalog_path.as_posix(), "blocking_reasons": list(result.blocking_reasons)}, ensure_ascii=False, indent=2))
        return 0 if result.ok else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
