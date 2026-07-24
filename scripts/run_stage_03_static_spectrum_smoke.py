"""Run the non-acceptance Stage 3 smoke profile from VSCode."""

from __future__ import annotations

import argparse
import json

from sqvm.spectrum import verify_static_spectrum


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/spectra/2q1c_static_smoke.yaml")
    parser.add_argument("--output", default="output/stage_03_static_spectrum_smoke")
    args = parser.parse_args(argv)
    report = verify_static_spectrum(
        config_path=args.config,
        output_dir=args.output,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.execution_succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
