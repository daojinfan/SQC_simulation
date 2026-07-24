"""Run stage 2 Hamiltonian verification from VSCode."""

from __future__ import annotations

import argparse
import json

from sqvm.hamiltonian import verify_hamiltonian


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/hamiltonians/2q1c_charge_basis.yaml")
    parser.add_argument("--output", default="output/stage_02_hamiltonian")
    args = parser.parse_args(argv)
    report = verify_hamiltonian(
        config_path=args.config,
        output_dir=args.output,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
