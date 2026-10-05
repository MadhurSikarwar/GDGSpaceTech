"""Screening on constructed geometries with a known answer.

Two circular orbits of the same altitude and RAAN but different inclinations
intersect at their ascending node. With both objects at the node at the same
instant the pair collides there; shifting one object's mean anomaly by a
small angle turns that into a near miss of a predictable size.
"""
import math
from datetime import datetime, timedelta

import pytest

from orbitwatch import orbital
from orbitwatch.jobs.screening import screen

EPOCH = datetime(2026, 10, 5, 12, 0, 0)
MEAN_MOTION = 15.2  # rev/day, ~480 km


def circular(norad, inclination, mean_anomaly=0.0, raan=40.0, mean_motion=MEAN_MOTION, epoch=EPOCH):
    el = {"norad_id": norad, "epoch": epoch, "mean_motion": mean_motion, "eccentricity": 0.0001,
          "inclination": inclination, "raan": raan, "arg_perigee": 0.0, "mean_anomaly": mean_anomaly,
          "bstar": 0.0, "mean_motion_dot": 0.0, "mean_motion_ddot": 0.0}
    el.update(orbital.derived_altitudes(mean_motion, 0.0001))
    return el


def test_crossing_orbits_produce_one_close_approach_at_the_node():
    a = circular(1, 51.6)
    b = circular(2, 97.5, mean_anomaly=0.02)  # ~2.4 km along-track offset at 6,860 km radius
    far = circular(3, 51.6, mean_anomaly=180.0)  # same plane, half an orbit away: never close
    start = EPOCH - timedelta(minutes=10)
    events, stats = screen([a, b, far], [1], start, horizon_s=1800, step_s=60, threshold_km=10)
    assert len(events) == 1
    ev = events[0]
    assert {ev["primary"], ev["secondary"]} == {1, 2}
    # Both start at the ascending node at EPOCH; the TCA must be within a few seconds of it.
    assert abs((ev["tca"] - EPOCH).total_seconds()) < 5
    assert 0.5 < ev["miss_km"] < 4.0
    # Relative velocity of two circular orbits crossing at 45.9 deg: 2 v sin(di/2).
    v = 2 * math.pi * (orbital.derived_altitudes(MEAN_MOTION, 0)["semi_major_axis_km"]) / (86400 / MEAN_MOTION)
    expected = 2 * v * math.sin(math.radians((97.5 - 51.6) / 2))
    assert ev["rel_vel_kms"] == pytest.approx(expected, rel=0.03)
    assert stats["primaries"] == 1


def test_threshold_excludes_wider_misses():
    a = circular(1, 51.6)
    b = circular(2, 97.5, mean_anomaly=0.2)  # ~24 km offset
    events, _ = screen([a, b], [1], EPOCH - timedelta(minutes=10), 1800, 60, threshold_km=10)
    assert events == []
    events, _ = screen([a, b], [1], EPOCH - timedelta(minutes=10), 1800, 60, threshold_km=50)
    assert len(events) == 1 and 15 < events[0]["miss_km"] < 35


def test_attached_objects_are_not_reported():
    """Docked modules share one element set (e.g. ISS ZARYA and NAUKA): not an approach."""
    a = circular(1, 51.6)
    b = circular(2, 51.6)
    events, _ = screen([a, b], [1, 2], EPOCH, 3600, 60, threshold_km=10)
    assert events == []


def test_altitude_filter_skips_distant_shells():
    leo = circular(1, 51.6)
    geo = circular(2, 0.1, mean_motion=1.0027)
    events, stats = screen([leo, geo], [1], EPOCH, 3600, 60, threshold_km=10)
    assert events == [] and stats["pairs"] == 0
