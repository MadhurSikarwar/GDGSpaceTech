"""The replay window of /api/conjunctions/<id>/track: unchanged by default, widenable by the globe, always capped.

The globe asks for a full orbit of lead-in (?before=5400&after=900&step=15) so the replay can show the object
moving along its orbit before the encounter; a request can never ask for an unbounded amount of work.
"""
import pytest


@pytest.fixture(scope="module")
def env():
    from orbitwatch import db
    try:
        ev = db.query_one("viewer", """SELECT event_id FROM conjunction_event c
                                        WHERE origin = 'orbitwatch'
                                          AND EXISTS (SELECT 1 FROM current_orbit WHERE norad_id = c.primary_norad)
                                          AND EXISTS (SELECT 1 FROM current_orbit WHERE norad_id = c.secondary_norad)
                                        ORDER BY time_of_closest_approach DESC LIMIT 1""")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no OrbitWatch database reachable: {exc}")
    if ev is None:
        pytest.skip("no close approach with both orbits to replay")
    from orbitwatch.web import create_app
    app = create_app(https=False)
    app.testing = True
    return {"client": app.test_client(), "id": ev["event_id"]}


def _track(env, query=""):
    r = env["client"].get(f"/api/conjunctions/{env['id']}/track{query}")
    assert r.status_code == 200, r.json
    return r.json


def test_rp01_default_window_is_thirty_minutes_either_side_every_ten_seconds(env):
    t = _track(env)
    assert t["offsets_s"][0] == -1800 and t["offsets_s"][-1] == 1800 and len(t["offsets_s"]) == 361
    assert len(t["objects"]) == 2 and all(len(o["ecef_km"]) == len(o["offsets_s"]) for o in t["objects"])


def test_rp02_the_globe_can_ask_for_a_longer_lead_in(env):
    t = _track(env, "?before=5400&after=900&step=15")
    offs = t["offsets_s"]
    assert offs[0] == -5400 and offs[-1] == 900
    assert {round(b - a, 6) for a, b in zip(offs, offs[1:])} == {15.0}
    # still one position per sample for each object (SGP4 may drop a few when it cannot propagate)
    assert all(len(o["ecef_km"]) == len(o["offsets_s"]) <= len(offs) for o in t["objects"])


def test_rp03_requests_are_capped(env):
    t = _track(env, "?before=9999999&after=9999999&step=0.001")
    offs = t["offsets_s"]
    assert offs[0] == -6 * 3600 and offs[-1] >= 3600 - 60              # at most six hours before, one hour after
    assert len(offs) <= 2600                                           # the step widens until the request is small
    assert offs[1] - offs[0] >= 5                                      # and never below five seconds
    short = _track(env, "?before=1&after=1")
    assert short["offsets_s"][0] == -60                                # and never absurdly short
