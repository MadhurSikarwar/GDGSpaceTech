from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from services.propagation.app.synthetic.generator import generate_verified_synthetic_debris
from services.propagation.app.screening.fine_filter import FineFilter
from services.maneuver.app.delta_v_optimizer import (
    build_optimizer_inputs,
    predicted_pc_for_delta_v,
    solve_minimum_delta_v,
    classify_constraint_status,
    burn_direction_label,
)

ISS_LINE1 = "1 25544U 98067A   26255.34409722  .00016717  00000+0  30154-3 0  9993"
ISS_LINE2 = "2 25544  51.6416 230.1254 0006241 120.4512 245.6721 15.49812345421508"

# Fixed reference instant shortly after the hardcoded TLE's own epoch
# (2026-255 = 2026-09-12, ~08:15 UTC) -- NOT datetime.now(). A test that
# instead measured "now" relative to a fixed historical TLE would get a
# steadily staler epoch (and, with it, a silently drifting realized
# encounter geometry) every day further from when the test was written;
# observed in practice as an intermittent failure when the suite ran on a
# day the drift happened to tip a boundary-hugging Pc constraint the wrong
# way. Fixing the reference makes the whole scenario fully reproducible.
REFERENCE_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)


def _build_case(profile_index=0):
    # generate_verified_synthetic_debris tunes orbital elements (RAAN/MA
    # shift) for a crossing geometry, but the *exact* TCA it actually
    # produces is a first-order approximation, not guaranteed to land
    # precisely at the requested tca_offset_minutes. Finding the true TCA is
    # exactly FineFilter's job (the same coarse+refined search the real
    # screening pipeline runs) -- reusing it here, rather than assuming a
    # requested offset is the real one, is both more correct and more
    # representative of how main.py will actually call this optimizer (via
    # a resolved ConjunctionCandidate with a genuine, already-refined `tca`).
    now = REFERENCE_NOW
    # 18h is realistic operational TLE staleness (matches what was observed
    # against the live catalog during development: a several-hour-old TLE is
    # typical, not a worst case) -- and, at this fixed reference/profile
    # combination's ~3.6 km realized miss, is old enough for the resulting
    # covariance to actually put baseline Pc above the 1e-4 critical
    # threshold. A much fresher epoch (tried during development: 2h) still
    # produces the same ~3.6 km miss but a *nine-orders-of-magnitude*
    # smaller Pc (~1e-12, not 1e-4) -- correctly reflecting that a well-
    # determined orbit's few-km "close approach" can be genuinely safe, not
    # a test bug. Real production covariance ages both objects from their
    # own actual DB epochs, not a hardcoded constant like this test uses.
    primary_epoch = now - timedelta(hours=18)
    target = {"catalog_id": "25544", "name": "ISS", "tle_line_1": ISS_LINE1, "tle_line_2": ISS_LINE2}
    synth = generate_verified_synthetic_debris(target, profile_index=profile_index, reference_time=now)

    primary = {"catalog_id": "25544", "name": "ISS", "object_type": "SATELLITE",
               "tle_line_1": ISS_LINE1, "tle_line_2": ISS_LINE2, "source": "test", "epoch": primary_epoch}
    secondary = {"catalog_id": synth["catalog_id"], "name": synth["name"], "object_type": "SYNTHETIC_DEBRIS",
                 "tle_line_1": synth["tle_line_1"], "tle_line_2": synth["tle_line_2"], "source": "test",
                 "epoch": synth["epoch"]}

    candidate = FineFilter(threshold_km=2000.0).compute_conjunction_candidate(
        primary, secondary, start_dt=now, horizon_minutes=90
    )
    assert candidate is not None, "synthetic debris profile did not produce a close approach within 2000 km"

    inputs = build_optimizer_inputs(
        primary_tle1=ISS_LINE1, primary_tle2=ISS_LINE2, primary_name="ISS",
        primary_epoch=primary_epoch, primary_type="SATELLITE",
        secondary_tle1=synth["tle_line_1"], secondary_tle2=synth["tle_line_2"], secondary_name=synth["name"],
        secondary_epoch=synth["epoch"], secondary_type="SYNTHETIC_DEBRIS",
        tca_dt=candidate.tca, combined_hbr_km=0.02, drag_activity_scalar=1.0,
    )
    time_to_tca_s = (candidate.tca - now).total_seconds()
    return inputs, time_to_tca_s, candidate


def test_baseline_geometry_is_a_genuine_close_approach():
    inputs, t, candidate = _build_case()
    baseline_pc = predicted_pc_for_delta_v(np.zeros(3), inputs, t)
    miss_km = np.linalg.norm(inputs["rel_pos_teme"])
    assert miss_km < 15.0  # generate_verified_synthetic_debris engineers a close pass
    assert baseline_pc > 1e-6  # a close pass should register as more than negligible


def test_optimizer_satisfies_pc_constraint_at_convergence():
    inputs, t, candidate = _build_case()
    result = solve_minimum_delta_v(inputs, t)
    assert result.success
    final_pc = predicted_pc_for_delta_v(result.x, inputs, t)
    assert final_pc <= 1.0e-4 * 1.02  # allow tiny numerical slack at the boundary


def test_optimizer_respects_slot_retention_constraint():
    inputs, t, candidate = _build_case()
    tight_sma = 0.05  # km -- deliberately tight to force the slot constraint to matter
    result = solve_minimum_delta_v(inputs, t, max_sma_drift_km=tight_sma)
    from services.maneuver.app.cw_transition import semi_major_axis_drift_km
    drift = abs(semi_major_axis_drift_km(result.x[1], inputs["n"]))
    assert drift <= tight_sma * 1.05


def test_delta_v_magnitude_grows_as_pc_threshold_tightens():
    inputs, t, candidate = _build_case()
    dv_loose = solve_minimum_delta_v(inputs, t, pc_threshold=1.0e-3)
    dv_tight = solve_minimum_delta_v(inputs, t, pc_threshold=1.0e-6)
    assert np.linalg.norm(dv_tight.x) >= np.linalg.norm(dv_loose.x) - 1e-9


def test_zero_delta_v_baseline_status_reports_active_constraint_when_unsafe():
    inputs, t, candidate = _build_case()
    # A trivial "do-nothing" result should not report SATISFIED, since the
    # whole scenario was engineered to be an unsafe close approach.
    class _Zero:
        success = True
        x = np.zeros(3)
    status = classify_constraint_status(_Zero(), inputs, t, pc_threshold=1.0e-4, max_sma_drift_km=5.0)
    assert status in ("PC_CONSTRAINT_ACTIVE", "SLOT_CONSTRAINT_ACTIVE")


@pytest.mark.parametrize("dv,expected", [
    (np.array([0.0, 0.01, 0.0]), "POSIGRADE"),
    (np.array([0.0, -0.01, 0.0]), "RETROGRADE"),
    (np.array([0.0, 0.0, 0.01]), "NORMAL"),
    (np.array([0.0, 0.0, -0.01]), "ANTINORMAL"),
    (np.array([0.01, 0.0, 0.0]), "RADIAL"),
    (np.array([-0.01, 0.0, 0.0]), "ANTIRADIAL"),
])
def test_burn_direction_label_matches_dominant_axis(dv, expected):
    assert burn_direction_label(dv) == expected
