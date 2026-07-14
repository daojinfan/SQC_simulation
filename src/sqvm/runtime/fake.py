"""The deterministic, platform-only Stage 6 fake experiment and backend."""

from __future__ import annotations

from sqvm.runtime.models import (
    BackendAdmission,
    BackendCapabilities,
    BackendCommand,
    ExperimentDefinition,
    ExperimentRequest,
    ParameterSpec,
    PointResult,
    PreparedExperiment,
    ScanPoint,
    frozen_mapping,
)


EXPERIMENT_ID = "platform_deterministic_smoke_v1"
BACKEND_ID = "deterministic_fake_v1"
CLAIM_ENVELOPE = frozen_mapping(
    {
        "evidence_class": "platform_test_fixture",
        "physics_claim": "none",
        "observation_model": "absent",
        "measurement_payload": None,
    }
)
RESPONSE_BYTES = bytes.fromhex(
    "00000000000000C000000000000000000000000000000040"
    "000000000000F8BF000000000000E03F0000000000000440"
)
RESPONSE_SHA256 = "01279CD8EFD786E0F4ED0EA714EE57AF7C82BE12D7259339A5998FF7C120E1A0"


def _prepare(request: ExperimentRequest, snapshots: dict[str, object]) -> PreparedExperiment:
    return PreparedExperiment(EXPERIMENT_ID, request, frozen_mapping(snapshots))


def _build_command(prepared: PreparedExperiment, point: ScanPoint) -> BackendCommand:
    parameters = dict(prepared.request.parameters)
    parameters.update(point.coordinate_map())
    return BackendCommand(EXPERIMENT_ID, point.point_id, frozen_mapping(parameters))


def deterministic_fake_definition() -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_id=EXPERIMENT_ID,
        parameter_specs=(
            ParameterSpec("x", "dimensionless", scannable=True, value_kind="binary64"),
            ParameterSpec("y", "dimensionless", scannable=True, value_kind="binary64"),
        ),
        program_schema="null_only_v1",
        required_backend_capabilities=frozenset({"cooperative_deadline_v1", "platform_test_fixture_v1"}),
        result_schema=frozen_mapping({"response": "binary64"}),
        dataset_schema=frozen_mapping(
            {
                "response": {
                    "dimensions": ("point",),
                    "dtype": "<f8",
                    "unit": "dimensionless",
                    "semantic_role": "platform_fixture_response",
                }
            }
        ),
        prepare=_prepare,
        build_command=_build_command,
    )


class DeterministicFakeBackend:
    backend_id = BACKEND_ID

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(frozenset({"cooperative_deadline_v1", "platform_test_fixture_v1"}))

    def admit(self, prepared_experiment: PreparedExperiment) -> BackendAdmission:
        if prepared_experiment.definition_id != EXPERIMENT_ID:
            raise ValueError("deterministic fake backend only admits its frozen experiment")
        return BackendAdmission(self.backend_id, self.capabilities())

    def execute_point(self, command: BackendCommand, context: object) -> PointResult:
        if command.experiment_id != EXPERIMENT_ID or set(command.parameters) != {"x", "y"}:
            raise ValueError("deterministic fake command is invalid")
        x = command.parameters["x"]
        y = command.parameters["y"]
        if not isinstance(x, float) or not isinstance(y, float):
            raise ValueError("deterministic fake command requires binary64 x and y")
        return PointResult(frozen_mapping({"response": x + (2.0 * y)}))
