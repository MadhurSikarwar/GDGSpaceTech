from datetime import datetime, timezone
import pytest

from services.propagation.app.physics.ground_stations import (
    GROUND_STATIONS,
    compute_passes_for_object,
)

# Real TLE snapshots (2026-09-12) -- same convention as ISS_LINE1/2 in
# test_propagation.py. TERRA/AQUA are real NASA sun-synchronous polar
# satellites (~98 deg inclination) already present in the seeded catalog.
TERRA_LINE1 = "1 25994U 99068A   26255.25868730  .00000231  00000+0  55821-4 0  9990"
TERRA_LINE2 = "2 25994  97.9374 301.3181 0001553 353.3189  48.7649 14.61158690422325"

AQUA_LINE1 = "1 27424U 02022A   26255.23567314  .00000499  00000+0  10895-3 0  9993"
AQUA_LINE2 = "2 27424  98.4385 226.3026 0001637  70.0506 346.6067 14.62198310295995"

ISS_LINE1 = "1 25544U 98067A   26255.20788499  .00004954  00000+0  97729-4 0  9996"
ISS_LINE2 = "2 25544  51.6305 229.4056 0004952 131.3152 228.8264 15.49086570585247"

START = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)


def test_three_real_polar_stations_configured():
    ids = {s.station_id for s in GROUND_STATIONS}
    assert ids == {"SVALBARD", "FAIRBANKS", "MCMURDO"}
    # Svalbard/Fairbanks near-arctic, McMurdo near-antarctic.
    by_id = {s.station_id: s for s in GROUND_STATIONS}
    assert by_id["SVALBARD"].lat_deg > 70
    assert by_id["FAIRBANKS"].lat_deg > 60
    assert by_id["MCMURDO"].lat_deg < -70


@pytest.mark.parametrize("line1,line2,name", [
    (TERRA_LINE1, TERRA_LINE2, "TERRA"),
    (AQUA_LINE1, AQUA_LINE2, "AQUA"),
])
def test_polar_satellite_gets_real_passes_at_every_station_over_a_day(line1, line2, name):
    # A ~98 deg sun-synchronous orbit crosses near-polar latitudes on every
    # orbit (~99 min period); over 24h it must pass within a 5 deg mask of
    # each polar station at least once. Using a full day (rather than the
    # app's default 90-min horizon) makes this assertion robust to whatever
    # the current orbital phasing happens to be, rather than depending on a
    # single lucky/unlucky 90-minute window.
    windows = compute_passes_for_object(line1, line2, name, START, horizon_minutes=1440)
    stations_seen = {w.station_id for w in windows}
    assert stations_seen == {"SVALBARD", "FAIRBANKS", "MCMURDO"}, (
        f"{name} should be visible from every polar station across a full day, got: {stations_seen}"
    )
    for w in windows:
        assert w.los > w.aos
        assert w.max_elevation_deg >= 5.0


def test_polar_satellite_reaches_near_overhead_elevation_somewhere_over_a_day():
    # Not a precise overhead geometry (hard to construct exactly for a real
    # TLE), but a meaningful proxy: across many passes in a day, a
    # near-polar orbit should come close to directly overhead a near-polar
    # station at least once.
    windows = compute_passes_for_object(TERRA_LINE1, TERRA_LINE2, "TERRA", START, horizon_minutes=1440)
    best = max(w.max_elevation_deg for w in windows)
    assert best >= 80.0


def test_iss_never_visible_from_any_polar_station():
    # 51.6 deg inclination -- ground track never reaches these stations'
    # ~65-78 deg latitudes, and at the 10-degree mask configured for all
    # three stations, LEO horizon geometry doesn't graze far enough past the
    # ground track to compensate (it can at a looser ~5-degree mask, which is
    # exactly why 10 was chosen -- see the module docstring). This documents
    # a real physical limitation rather than leaving it as a silent surprise:
    # it is not a bug that the demo should route around the ISS for this
    # specific feature.
    windows = compute_passes_for_object(ISS_LINE1, ISS_LINE2, "ISS", START, horizon_minutes=1440)
    assert windows == []
