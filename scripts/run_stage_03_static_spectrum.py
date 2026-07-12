"""Run the Stage 3 static-spectrum acceptance profile from VSCode."""

from __future__ import annotations

import json

from sqvm.spectrum import verify_static_spectrum


if __name__ == "__main__":
    report = verify_static_spectrum(
        config_path="configs/spectra/2q1c_static.yaml",
        output_dir="output/stage_03_static_spectrum",
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
