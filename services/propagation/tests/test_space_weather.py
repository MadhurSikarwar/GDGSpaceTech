import httpx
import pytest

from services.propagation.app.spaceweather import noaa_client
from services.propagation.app.spaceweather.noaa_client import (
    _classify_activity,
    _drag_activity_scalar,
    _quiet_sun_fallback,
    get_space_weather,
)


def test_classify_activity_tier_boundaries():
    assert _classify_activity(0.0) == "QUIET"
    assert _classify_activity(3.99) == "QUIET"
    assert _classify_activity(4.0) == "MODERATE"
    assert _classify_activity(4.99) == "MODERATE"
    assert _classify_activity(5.0) == "ELEVATED"
    assert _classify_activity(6.99) == "ELEVATED"
    assert _classify_activity(7.0) == "STORM"
    assert _classify_activity(9.0) == "STORM"


def test_drag_activity_scalar_floors_at_one_and_is_monotonic():
    assert _drag_activity_scalar(0.0) == pytest.approx(1.0)
    assert _drag_activity_scalar(7.0) == pytest.approx(1.0)  # quiet-time reference
    quiet = _drag_activity_scalar(7.0)
    moderate = _drag_activity_scalar(30.0)
    storm = _drag_activity_scalar(200.0)
    assert quiet < moderate < storm


def test_quiet_sun_fallback_is_flagged_not_live():
    snap = _quiet_sun_fallback()
    assert snap.live is False
    assert snap.source == "QUIET_SUN_FALLBACK"
    assert snap.drag_activity_scalar == pytest.approx(1.0)


def test_get_space_weather_falls_back_deterministically_when_noaa_unreachable(monkeypatch):
    def _boom(*args, **kwargs):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr(noaa_client, "_fetch_latest_kp_ap", _boom)
    result = get_space_weather(force_refresh=True)
    assert result.live is False
    assert result.source == "QUIET_SUN_FALLBACK"


def test_get_space_weather_live_or_gracefully_degrades():
    # Exercises the real NOAA endpoints when reachable; must never raise, and
    # must always come back as a fully-populated, honestly-labeled snapshot
    # either way (live=True from NOAA, or live=False from the quiet-sun
    # fallback) -- never a partially-filled or silently-fabricated result.
    result = get_space_weather(force_refresh=True)
    assert isinstance(result.live, bool)
    assert result.kp_index >= 0.0
    assert result.ap_index >= 0.0
    assert result.f107_sfu > 0.0
    assert result.drag_activity_scalar >= 1.0
    assert result.activity_level in {"QUIET", "MODERATE", "ELEVATED", "STORM"}


def test_space_weather_cache_avoids_refetch_within_ttl(monkeypatch):
    calls = {"n": 0}

    def _fake_kp_ap(client):
        calls["n"] += 1
        return 2.0, 7.0

    monkeypatch.setattr(noaa_client, "_fetch_latest_kp_ap", _fake_kp_ap)
    monkeypatch.setattr(noaa_client, "_fetch_latest_f107", lambda client: 150.0)

    first = get_space_weather(force_refresh=True)
    second = get_space_weather(force_refresh=False)
    assert calls["n"] == 1
    assert first == second
