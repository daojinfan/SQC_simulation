"""Run the formal Stage 3.1 q1-q2 coupling profile from VSCode."""

from __future__ import annotations

import json

from sqvm.spectrum import verify_q1_q2_coupling


if __name__ == "__main__":
    report = verify_q1_q2_coupling(
        "configs/spectra/2q1c_q1q2_coupling.yaml",
        "output/stage_03_1_q1_q2_coupling",
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
