"""Generate and execute the Stage 2.1 candidate verification notebook."""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient


OUTPUT = Path("output/stage_02_1_hamiltonian_rebaseline")
NOTEBOOK = OUTPUT / "verification.ipynb"


def main() -> None:
    notebook = nbf.v4.new_notebook(
        metadata={"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}}
    )
    notebook.cells = [
        nbf.v4.new_markdown_cell(
            "# Stage 2.1 Hamiltonian rebaseline candidate\n\n"
            "This notebook verifies the candidate hash chain and numerical report. "
            "It does not approve the rebaseline."
        ),
        nbf.v4.new_code_cell(
            "from pathlib import Path\n"
            "import hashlib\n"
            "import json\n\n"
            "PREVIOUS_PATH = Path('previous_hamiltonian_artifacts.json')\n"
            "ANCHOR_PATH = Path('legacy_baseline_anchor.json')\n"
            "CANDIDATE_PATH = Path('../stage_02_hamiltonian/hamiltonian_artifacts.json')\n"
            "MANIFEST_PATH = Path('rebaseline_manifest.json')\n\n"
            "def load(path):\n"
            "    return json.loads(path.read_text(encoding='utf-8'))\n\n"
            "def sha256(path):\n"
            "    return hashlib.sha256(path.read_bytes()).hexdigest().upper()\n\n"
            "previous = load(PREVIOUS_PATH)\n"
            "anchor = load(ANCHOR_PATH)\n"
            "candidate = load(CANDIDATE_PATH)\n"
            "manifest = load(MANIFEST_PATH)\n"
            "{'inputs_loaded': [str(PREVIOUS_PATH), str(ANCHOR_PATH), str(CANDIDATE_PATH), str(MANIFEST_PATH)]}"
        ),
        nbf.v4.new_markdown_cell("## Hash chain"),
        nbf.v4.new_code_cell(
            "hash_chain = {\n"
            "    'previous_stage2_artifact': sha256(PREVIOUS_PATH),\n"
            "    'legacy_baseline_anchor': sha256(ANCHOR_PATH),\n"
            "    'candidate_stage2_artifact': sha256(CANDIDATE_PATH),\n"
            "    'rebaseline_manifest': sha256(MANIFEST_PATH),\n"
            "}\n"
            "assert hash_chain['previous_stage2_artifact'] == anchor['previous_stage2_artifacts_sha256']\n"
            "assert hash_chain['previous_stage2_artifact'] == anchor['expected_sha256']\n"
            "assert hash_chain['previous_stage2_artifact'] == manifest['previous_stage2_artifacts_sha256']\n"
            "assert hash_chain['legacy_baseline_anchor'] == manifest['legacy_baseline_anchor_sha256']\n"
            "assert hash_chain['candidate_stage2_artifact'] == manifest['sha256']['stage2_artifacts_sha256']\n"
            "{'hash_chain_ok': True, 'hashes': hash_chain, 'manifest_sha256_map': manifest['sha256']}"
        ),
        nbf.v4.new_markdown_cell("## Test, verify, and determinism summaries"),
        nbf.v4.new_code_cell(
            "{\n"
            "    'tests': manifest['test_summary'],\n"
            "    'verify_device': manifest['verify_device_summary'],\n"
            "    'verify_hamiltonian': manifest['verify_hamiltonian_summary'],\n"
            "    'determinism': manifest['determinism_checks'],\n"
            "}"
        ),
        nbf.v4.new_markdown_cell("## Old-to-new model and spectrum deltas"),
        nbf.v4.new_code_cell(
            "deltas = manifest['old_to_new_numeric_deltas']\n"
            "{\n"
            "    'cutoffs': deltas['cutoffs'],\n"
            "    'hilbert_dimension': deltas['hilbert_dimension'],\n"
            "    'runtime_seconds': deltas['runtime_seconds'],\n"
            "    'max_abs_mode_capacitance_delta_fF': deltas['max_abs_mode_capacitance_delta_fF'],\n"
            "    'max_abs_ec_delta_GHz': deltas['max_abs_ec_delta_GHz'],\n"
            "    'max_abs_ej_effective_delta_GHz': deltas['max_abs_ej_effective_delta_GHz'],\n"
            "    'lowest_12_gaps': deltas['lowest_12_gaps'],\n"
            "    'single_transmon_analytic_check': deltas['single_transmon_analytic_check'],\n"
            "}"
        ),
        nbf.v4.new_markdown_cell("## N=7 to N=9 convergence gate"),
        nbf.v4.new_code_cell(
            "convergence = deltas['n7_to_n9_convergence']\n"
            "assert deltas['n7_to_n9_gate_ok']\n"
            "assert all(row['within_0_50_MHz'] for row in convergence)\n"
            "{'budget_MHz': deltas['n7_to_n9_frequency_budget_MHz'], 'rows': convergence, 'gate_ok': True}"
        ),
        nbf.v4.new_markdown_cell("## Candidate checks and warnings"),
        nbf.v4.new_code_cell(
            "assert candidate['schema_version'] == '0.2'\n"
            "assert candidate['artifact_version'] == '0.2'\n"
            "assert all(row['passed'] or row['severity'] == 'warning' for row in candidate['checks'])\n"
            "{'checks': deltas['checks'], 'warnings': deltas['warnings']}"
        ),
        nbf.v4.new_markdown_cell(
            "## Candidate status\n\n"
            "All candidate checks displayed above passed. Independent approval is intentionally absent and remains required."
        ),
    ]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    client = NotebookClient(
        notebook,
        timeout=600,
        kernel_name="python3",
        resources={"metadata": {"path": str(OUTPUT.resolve())}},
    )
    client.execute()
    nbf.write(notebook, NOTEBOOK)


if __name__ == "__main__":
    main()
