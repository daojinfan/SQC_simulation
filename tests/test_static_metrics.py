from dataclasses import replace

import numpy as np

from sqvm.spectrum import compute_static_metrics


def test_transition_frequency_formula(spectrum_session):
    metrics = spectrum_session["baseline"].metrics
    assigned = spectrum_session["baseline"].dressed_states.by_label()
    assert metrics.transition_frequencies_GHz["q1_01"] == assigned["100"].energy_GHz - assigned["000"].energy_GHz


def test_anharmonicity_formula(spectrum_session):
    metrics = spectrum_session["baseline"].metrics
    assigned = spectrum_session["baseline"].dressed_states.by_label()
    expected = assigned["200"].energy_GHz - 2 * assigned["100"].energy_GHz + assigned["000"].energy_GHz
    assert metrics.anharmonicities_GHz["q1_alpha"] == expected


def test_zz_formula(spectrum_session):
    metrics = spectrum_session["baseline"].metrics
    assigned = spectrum_session["baseline"].dressed_states.by_label()
    expected = assigned["101"].energy_GHz - assigned["100"].energy_GHz - assigned["001"].energy_GHz + assigned["000"].energy_GHz
    assert metrics.zz_metrics_GHz["zz_q1_q2"] == expected


def test_missing_state_metric_warning(spectrum_session):
    table = spectrum_session["baseline"].dressed_states
    rows = tuple(replace(row, status="low_overlap") if row.label.key == "200" else row for row in table.assignments)
    metrics = compute_static_metrics(replace(table, assignments=rows), spectrum_session["baseline"].eigenstates, spectrum_session["baseline"].participation)
    assert "200 has low overlap" in metrics.warnings
