import math
from datetime import datetime, timezone
import pytest
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine, sanity_check_iss_propagation
from services.propagation.app.propagation.trajectory import generate_trajectory

ISS_LINE1 = "1 25544U 98067A   26255.34409722  .00016717  00000+0  30154-3 0  9993"
ISS_LINE2 = "2 25544  51.6416 230.1254 0006241 120.4512 245.6721 15.49812345421508"


def test_iss_sgp4_propagation():
    engine = SGP4PropagationEngine(ISS_LINE1, ISS_LINE2, "ISS")
    now_dt = datetime.now(timezone.utc)
    state = engine.propagate_state(now_dt)

    # Position components in km
    assert isinstance(state.position_km.x, float)
    assert isinstance(state.position_km.y, float)
    assert isinstance(state.position_km.z, float)

    # Verify magnitude of position vector (LEO orbit ~6600-6900 km radius)
    r_mag = math.sqrt(state.position_km.x**2 + state.position_km.y**2 + state.position_km.z**2)
    assert 6500.0 <= r_mag <= 7200.0

    # Velocity components in km/s (LEO speed ~7.5-7.8 km/s)
    v_mag = math.sqrt(state.velocity_km_s.x**2 + state.velocity_km_s.y**2 + state.velocity_km_s.z**2)
    assert 7.0 <= v_mag <= 8.5

    # Geodetic altitude in km (~380-450 km)
    assert 300.0 <= state.altitude_km <= 500.0


def test_iss_sanity_check_validation():
    assert sanity_check_iss_propagation(ISS_LINE1, ISS_LINE2) is True


def test_trajectory_generation():
    now_dt = datetime.now(timezone.utc)
    traj = generate_trajectory("obj-1", "25544", "ISS", ISS_LINE1, ISS_LINE2, start_dt=now_dt, horizon_minutes=90, step_minutes=1.0)

    assert traj.catalog_id == "25544"
    assert len(traj.trajectory) == 91  # 0 to 90 minutes inclusive = 91 points
    assert traj.trajectory[0].x == traj.trajectory[0].x
    assert traj.reference_frame == "TEME"
