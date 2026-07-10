"""Run stage 2 Hamiltonian verification from VSCode."""

from __future__ import annotations

import json

from sqvm.hamiltonian import verify_hamiltonian


if __name__ == "__main__":
    report = verify_hamiltonian(
        config_path="configs/hamiltonians/2q1c_charge_basis.yaml",
        output_dir="output/stage_02_hamiltonian",
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
