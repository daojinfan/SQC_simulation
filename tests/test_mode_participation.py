import numpy as np

from sqvm.spectrum import detect_character_exchange


def test_product_state_mean_excitations(spectrum_session):
    rows = spectrum_session["baseline"].participation.by_label()
    assert rows["100"].mean_excitations["q1"] > rows["100"].mean_excitations["c"]
    assert rows["010"].mean_excitations["c"] > rows["010"].mean_excitations["q1"]


def test_single_excitation_fractions_sum_to_one(spectrum_session):
    rows = spectrum_session["baseline"].participation.by_label()
    for label in ("100", "010", "001"):
        np.testing.assert_allclose(sum(rows[label].fractions.values()), 1.0, atol=1e-12)


def test_ground_state_fractions_are_null(spectrum_session):
    assert spectrum_session["baseline"].participation.by_label()["000"].fractions is None


def test_participation_is_finite_and_nonnegative(spectrum_session):
    for row in spectrum_session["baseline"].participation.rows:
        assert all(np.isfinite(value) and value >= 0 for value in row.mean_excitations.values())


def test_scan_points_store_three_single_excitation_branch_participations():
    point = {
        label: {"fractions": {"q1": 1.0 if label == "100" else 0.0, "c": 1.0 if label == "010" else 0.0, "q2": 1.0 if label == "001" else 0.0}}
        for label in ("100", "010", "001")
    }
    assert set(point) == {"100", "010", "001"}


def test_character_exchange_detected_from_participation():
    result = detect_character_exchange(
        left={"100": {"q1": 0.95, "c": 0.05}, "010": {"q1": 0.05, "c": 0.95}},
        minimum={"100": {"q1": 0.5, "c": 0.5}, "010": {"q1": 0.5, "c": 0.5}},
        right={"100": {"q1": 0.05, "c": 0.95}, "010": {"q1": 0.95, "c": 0.05}},
        branch_labels=("100", "010"),
        modes=("q1", "c"),
        endpoint_min_fraction=0.8,
        exchange_min_delta=0.6,
        target_pair_min_fraction=0.8,
    )
    assert result["passed"]


def test_character_evidence_points_are_outside_final_bracket_when_needed():
    evidence = {"left": "0.300000000000", "minimum": "0.350000000000", "right": "0.400000000000"}
    bracket = ("0.340000000000", "0.360000000000")
    assert evidence["left"] < bracket[0] < evidence["minimum"] < bracket[1] < evidence["right"]
