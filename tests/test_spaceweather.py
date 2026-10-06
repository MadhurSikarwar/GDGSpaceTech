"""NOAA SWPC space-weather parsing: the newest record wins whatever order the feed is published in.

Regression: the F10.7 feed became newest-first, and taking the last row returned a reading from weeks ago.
"""
from orbitwatch.physics import spaceweather

KP_OLDEST_FIRST = [
    {"time_tag": "2026-10-06T03:00:00", "Kp": 1.33, "a_running": 5, "station_count": 8},
    {"time_tag": "2026-10-06T06:00:00", "Kp": 2.0, "a_running": 7, "station_count": 8},
    {"time_tag": "2026-10-06T09:00:00", "Kp": 2.67, "a_running": 12, "station_count": 8},
]
KP_HEADER_ROW = [["time_tag", "Kp", "a_running", "station_count"],
                 ["2026-10-06T09:00:00", "4.33", "27", "8"], ["2026-10-06T06:00:00", "3.0", "15", "8"]]
F107_NEWEST_FIRST = [
    {"time_tag": "2026-10-05T22:00:00", "frequency": 2800, "flux": 103.0},
    {"time_tag": "2026-10-05T20:00:00", "frequency": 2695, "flux": 999.0},   # not the 2800 MHz index
    {"time_tag": "2026-10-04T22:00:00", "frequency": 2800, "flux": 110.0},
    {"time_tag": "2026-08-26T17:00:00", "frequency": 2800, "flux": 131.0},
]


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _serve(monkeypatch, kp, f107):
    def fake_get(url, **_):
        return _Resp(kp if url == spaceweather.KP_URL else f107)
    monkeypatch.setattr(spaceweather.requests, "get", fake_get)


def test_sw01_newest_f107_wins_in_a_newest_first_feed(monkeypatch):
    _serve(monkeypatch, KP_OLDEST_FIRST, F107_NEWEST_FIRST)
    r = spaceweather.fetch()
    assert r["live"] is True
    assert r["f107"] == 103.0           # not the last row (131, from August) and not the 2695 MHz row
    assert r["kp"] == 2.67 and r["ap"] == 12.0
    assert r["observed_at"].isoformat() == "2026-10-06T09:00:00"
    assert r["activity"] == "QUIET"
    assert abs(r["drag_scalar"] - 1.1) < 1e-9


def test_sw02_newest_kp_wins_in_a_header_row_feed(monkeypatch):
    _serve(monkeypatch, KP_HEADER_ROW, list(reversed(F107_NEWEST_FIRST)))
    r = spaceweather.fetch()
    assert r["kp"] == 4.33 and r["ap"] == 27.0
    assert r["activity"] == "MODERATE"
    assert r["f107"] == 103.0           # the same answer for an oldest-first F10.7 feed
    assert abs(r["drag_scalar"] - (1 + 20 / 50)) < 1e-9


def test_sw03_unreachable_feed_is_reported_as_fallback_not_as_data(monkeypatch):
    def boom(*_, **__):
        raise ConnectionError("NOAA unreachable")
    monkeypatch.setattr(spaceweather.requests, "get", boom)
    r = spaceweather.fetch()
    assert r["live"] is False
    assert r["source"] == "quiet-sun fallback"
    assert r["drag_scalar"] == 1.0


def test_sw04_activity_scale_and_drag_scalar():
    assert [spaceweather.activity_level(k) for k in (0, 3.9, 4, 5, 7, 9)] == \
        ["QUIET", "QUIET", "MODERATE", "ELEVATED", "STORM", "STORM"]
    assert spaceweather.drag_scalar(7) == 1.0 and spaceweather.drag_scalar(0) == 1.0
    assert abs(spaceweather.drag_scalar(57) - 2.0) < 1e-9
