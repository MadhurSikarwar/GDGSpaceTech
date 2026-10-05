"""End-to-end test cases for the OrbitWatch website (API + database), by role.

Runs in-process against the configured database (skipped when none is reachable), e.g.
    set MYSQL_PORT=3307 && pytest tests/test_website_e2e.py -v
It creates throwaway users and data and removes them afterwards. Case ids (TC-xx) match the
test report.
"""
import csv
import math
import secrets
import time

import pytest

H = {"X-Requested-With": "OrbitWatch"}


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def site():
    from orbitwatch import auth, config, db
    try:
        db.query("viewer", "SELECT 1 AS x")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no OrbitWatch database reachable: {exc}")
    from orbitwatch.web import create_app
    app = create_app(https=False)
    app.testing = True
    pw = secrets.token_urlsafe(12)
    tag = secrets.token_hex(4)
    emails = {r: f"e2e-{r}-{tag}@example.test" for r in ("viewer", "analyst", "admin")}
    for role, email in emails.items():
        auth.create_user(f"E2E {role}", email, pw, role)
    created = {"events": [], "emails": list(emails.values())}
    yield {"app": app, "pw": pw, "emails": emails, "db": db, "config": config, "created": created}
    for ev in created["events"]:
        db.execute("admin", "DELETE FROM conjunction_event WHERE event_id = %s", (ev,))
    for email in created["emails"]:
        db.execute("admin", "DELETE FROM app_user WHERE email = %s", (email,))


def client(site, role=None):
    c = site["app"].test_client()
    if role:
        r = c.post("/api/auth/login", json={"email": site["emails"][role], "password": site["pw"]}, headers=H)
        assert r.status_code == 200, r.json
    return c


@pytest.fixture(scope="module")
def anon(site):
    return client(site)


@pytest.fixture(scope="module")
def viewer(site):
    return client(site, "viewer")


@pytest.fixture(scope="module")
def analyst(site):
    return client(site, "analyst")


@pytest.fixture(scope="module")
def admin(site):
    return client(site, "admin")


def one(site, sql, params=()):
    return site["db"].query_one("admin", sql, params)


# --------------------------------------------------------------------------- TC-01..06 data integrity
def test_tc01_counts_match_the_source_catalogue(site):
    path = site["config"].CACHE_DIR / "satcat.csv"
    if not path.exists():
        pytest.skip("no cached SATCAT to compare with")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    earth = sum(1 for r in rows if not r["DECAY_DATE"] and r["ORBIT_CENTER"] == "EA" and r["ORBIT_TYPE"] == "ORB")
    db = one(site, "SELECT COUNT(*) AS total, SUM(in_earth_orbit) AS earth, SUM(decay_date IS NOT NULL) AS decayed "
                   "FROM space_object WHERE norad_id IN (SELECT norad_id FROM space_object)")
    satcat_ids = {int(r["NORAD_CAT_ID"]) for r in rows}
    extra = one(site, f"SELECT COUNT(*) AS n FROM space_object WHERE norad_id NOT IN ({','.join(map(str, satcat_ids))})")["n"]
    assert db["total"] - extra == len(rows)                     # every SATCAT object is in the catalogue
    assert int(db["earth"]) - extra == earth                    # 'in Earth orbit' = SATCAT centre EA, type ORB
    assert int(db["decayed"]) == sum(1 for r in rows if r["DECAY_DATE"])


def test_tc02_current_orbits_are_consistent(site):
    from orbitwatch import orbital
    bad = one(site, """SELECT COUNT(*) AS n FROM current_orbit co JOIN space_object so USING (norad_id)
                       WHERE so.decay_date IS NOT NULL OR co.perigee_km > co.apogee_km + 1e-9""")["n"]
    assert bad == 0
    for r in site["db"].query("admin", "SELECT mean_motion, eccentricity, perigee_km, apogee_km, period_min FROM current_orbit "
                                       "ORDER BY RAND() LIMIT 200"):
        d = orbital.derived_altitudes(r["mean_motion"], r["eccentricity"])
        assert math.isclose(d["perigee_km"], r["perigee_km"], abs_tol=1e-6)
        assert math.isclose(d["apogee_km"], r["apogee_km"], abs_tol=1e-6)
        assert math.isclose(d["period_min"], r["period_min"], rel_tol=1e-12)


def test_tc03_history_holds_every_current_element_set(site):
    from orbitwatch import db
    sample = site["db"].query("admin", "SELECT norad_id, epoch FROM current_orbit ORDER BY RAND() LIMIT 150")
    coll = db.mongo_db("analyst").orbit_history
    missing = [s for s in sample if coll.count_documents({"norad_id": s["norad_id"], "epoch": s["epoch"]}) == 0]
    assert not missing, missing[:5]


def test_tc04_ownership_periods_never_overlap(site):
    assert one(site, """SELECT COUNT(*) AS n FROM object_ownership a JOIN object_ownership b
                         ON a.norad_id = b.norad_id AND a.from_date < b.from_date
                        AND b.from_date < COALESCE(a.to_date, '9999-12-31')""")["n"] == 0
    assert one(site, "SELECT COUNT(*) AS n FROM (SELECT norad_id FROM object_ownership WHERE to_date IS NULL "
                     "GROUP BY norad_id HAVING COUNT(*) > 1) x")["n"] == 0


def test_tc05_conjunction_events_are_sane(site):
    assert one(site, """SELECT COUNT(*) AS n FROM conjunction_event
                        WHERE primary_norad = secondary_norad OR miss_distance_km < 0
                           OR probability_of_collision < 0 OR probability_of_collision > 1
                           OR risk_level <> fn_risk_level(miss_distance_km, relative_velocity)""")["n"] == 0


def test_tc06_region_report_adds_up(site):
    total = one(site, "SELECT SUM(total_objects) AS n FROM v_report_region_counts")["n"]
    assert int(total) == one(site, "SELECT COUNT(*) AS n FROM current_orbit WHERE mean_altitude_km >= 0 "
                                   "AND mean_altitude_km < 500000")["n"]


# --------------------------------------------------------------------------- TC-10..19 public pages
def test_tc10_landing_and_dashboard_agree(anon):
    land = anon.get("/api/landing").json
    stats = anon.get("/api/stats").json
    assert land["counts"]["on_orbit"] == stats["counts"]["on_orbit"]
    assert land["counts"]["on_orbit"] + land["counts"]["beyond_earth_orbit"] + land["counts"]["decayed"] == land["counts"]["total"]
    assert land["histogram"]["rows"] and land["regions"] and land["reentries"] and land["fragmentations"]
    assert all(e["time_of_closest_approach"] >= land["generated_at"][:19] for e in land["upcoming"])


def test_tc11_catalogue_search_by_name_norad_and_cospar(anon):
    by_name = anon.get("/api/objects?q=CARTOSAT&status=all&page_size=100").json
    assert by_name["total"] > 5 and all("CARTOSAT" in i["name"] for i in by_name["items"])
    by_id = anon.get("/api/objects?q=25544").json
    assert by_id["items"][0]["norad_id"] == 25544
    by_cospar = anon.get("/api/objects?q=1998-067&status=all").json
    assert all(i["intl_designator"].startswith("1998-067") for i in by_cospar["items"])


def test_tc12_catalogue_filters_only_return_matching_rows(anon):
    lk = anon.get("/api/lookups").json
    region = lk["regions"][1]["region_id"]
    for q, check in (("type=Debris", lambda i: i["object_type"] == "Debris"),
                     ("country=IN", lambda i: i["country_code"] == "IN"),
                     ("org=ISRO", lambda i: i["org_id"] == "ISRO"),
                     (f"region={region}", lambda i: i["region_id"] == region),
                     ("status=decayed", lambda i: i["decay_date"] is not None),
                     ("status=beyond", lambda i: not i["in_earth_orbit"] and i["decay_date"] is None),
                     ("has_orbit=1", lambda i: i["epoch"] is not None)):
        r = anon.get(f"/api/objects?{q}&page_size=200").json
        assert r["total"] > 0 and all(check(i) for i in r["items"]), q
    beyond = anon.get("/api/objects?status=beyond").json["total"]
    from orbitwatch import db
    assert beyond == db.query_one("viewer", "SELECT COUNT(*) AS n FROM space_object WHERE decay_date IS NULL "
                                            "AND NOT in_earth_orbit")["n"] > 0


def test_tc13_pagination_and_sorting(anon):
    p1 = anon.get("/api/objects?sort=name&page_size=25").json
    p2 = anon.get("/api/objects?sort=name&page=2&page_size=25").json
    assert p1["total"] == p2["total"] and not {i["norad_id"] for i in p1["items"]} & {i["norad_id"] for i in p2["items"]}
    names = [i["name"] for i in p1["items"]]
    assert names == sorted(names, key=str.lower) or names == sorted(names)


def test_tc14_object_pages(anon, site):
    iss = anon.get("/api/objects/25544").json
    assert iss["launch"]["vehicle_name"] and iss["ownership"] and iss["current_orbit"]["norad_id"] == 25544
    deb = one(site, "SELECT norad_id FROM space_object WHERE object_type = 'Debris' AND parent_norad_id IS NOT NULL LIMIT 1")
    assert anon.get(f"/api/objects/{deb['norad_id']}").json["parent"] is not None
    gone = one(site, "SELECT norad_id FROM space_object WHERE decay_date IS NOT NULL LIMIT 1")
    d = anon.get(f"/api/objects/{gone['norad_id']}").json
    assert d["object"]["status"] == "Decayed" and d["current_orbit"] is None
    mom = anon.get("/api/objects/39370").json["object"]
    assert mom["orbit_center"] == "MA" and not mom["in_earth_orbit"]
    assert anon.get("/api/objects/999999999").status_code == 404


def test_tc15_close_approach_filters_detail_and_replay_track(anon):
    crit = anon.get("/api/conjunctions?risk=CRITICAL,HIGH&when=all&page_size=200").json
    assert crit["total"] > 0 and all(i["risk_level"] in ("CRITICAL", "HIGH") for i in crit["items"])
    by_miss = anon.get("/api/conjunctions?sort=miss&when=all&page_size=50").json["items"]
    assert [i["miss_distance_km"] for i in by_miss] == sorted(i["miss_distance_km"] for i in by_miss)
    ev = crit["items"][0]
    obj = anon.get(f"/api/conjunctions?norad={ev['primary_norad']}&when=all").json["items"]
    assert all(ev["primary_norad"] in (i["primary_norad"], i["secondary_norad"]) for i in obj)
    ranged = anon.get("/api/conjunctions?from=2000-01-01&to=2000-01-02").json
    assert ranged["total"] == 0
    detail = anon.get(f"/api/conjunctions/{ev['event_id']}").json
    assert detail["primary"]["norad_id"] == ev["primary_norad"]
    track = anon.get(f"/api/conjunctions/{ev['event_id']}/track").json
    assert len(track["objects"]) == 2 and len(track["objects"][0]["ecef_km"]) == 361
    assert anon.get("/api/conjunctions/987654321").status_code == 404


def test_tc16_globe_data(anon):
    p = anon.get("/api/visual/positions?span=120").json
    assert len(p["ids"]) > 1000 and len(p["pos"]) == len(p["pos2"]) == 3 * len(p["ids"])
    r = [math.dist((0, 0, 0), p["pos"][i:i + 3]) for i in range(0, 300, 3)]
    assert all(6378 < x < 500000 for x in r)                        # every point is above the Earth's surface
    o = anon.get("/api/visual/orbit/25544?periods=2").json
    assert len(o["ecef_km"]) == 481 and abs(o["now"]["lat"]) <= 52.0 and 380 < o["now"]["alt_km"] < 460
    assert len(anon.get("/api/visual/names").json["ids"]) >= len(p["ids"])


def test_tc17_space_weather_ground_stations_and_passes(anon):
    sw = anon.get("/api/space-weather").json
    assert 0 <= sw["kp"] <= 9 and sw["f107"] > 50
    st = anon.get("/api/ground-stations").json["items"]
    assert {"SVALBARD", "ISTRAC-BLR"} <= {s["station_id"] for s in st}
    p = anon.get("/api/objects/25544/passes?hours=24").json["items"]
    assert p and all(x["los"] >= x["aos"] for x in p) and not {x["station_id"] for x in p} & {"SVALBARD", "MCMURDO"}


def test_tc18_bad_parameters_are_rejected_cleanly(anon):
    assert anon.get("/api/objects?norad=abc").status_code == 400
    assert anon.get("/api/conjunctions?from=yesterday").status_code == 400
    assert anon.get("/api/objects?page=-5&page_size=99999").status_code == 200  # clamped, not an error
    assert anon.get("/api/does-not-exist").status_code == 404


# --------------------------------------------------------------------------- TC-20..27 authentication
def test_tc20_registration_rules(site, anon):
    c = client(site)
    email = f"e2e-reg-{secrets.token_hex(4)}@example.test"
    site["created"]["emails"].append(email)
    r = c.post("/api/auth/register", json={"name": "Reg", "email": email.upper(), "password": site["pw"], "role": "admin"}, headers=H)
    assert r.status_code == 201 and r.json["user"]["role"] == "viewer" and r.json["user"]["email"] == email
    for body, why in (({"name": "Dup", "email": email, "password": site["pw"]}, "duplicate"),
                      ({"name": "Bad", "email": "not-an-email", "password": site["pw"]}, "email"),
                      ({"name": "Short", "email": "short@example.test", "password": "abc"}, "short"),
                      ({"name": "Long", "email": "long@example.test", "password": "é" * 40}, "72 bytes"),
                      ({"name": "", "email": "noname@example.test", "password": site["pw"]}, "name")):
        assert anon.post("/api/auth/register", json=body, headers=H).status_code == 400, why


def test_tc21_login_logout_and_wrong_password(site):
    c = client(site)
    assert c.post("/api/auth/login", json={"email": site["emails"]["viewer"], "password": "wrong-password"}, headers=H).status_code == 401
    assert c.post("/api/auth/login", json={"email": "nobody@example.test", "password": "whatever1"}, headers=H).status_code == 401
    assert c.post("/api/auth/login", json={"email": site["emails"]["viewer"], "password": site["pw"]}, headers=H).status_code == 200
    assert c.get("/api/auth/me").json["user"]["role"] == "viewer"
    c.post("/api/auth/logout", headers=H)
    assert c.get("/api/auth/me").json["user"] is None


def test_tc22_brute_force_throttle(site):
    c = client(site)
    email = f"throttle-{secrets.token_hex(3)}@example.test"
    codes = [c.post("/api/auth/login", json={"email": email, "password": "nope-nope"}, headers=H).status_code for _ in range(9)]
    assert codes[:8] == [401] * 8 and codes[8] == 429


def test_tc23_csrf_header_is_required(viewer):
    assert viewer.post("/api/me/subscriptions", json={"norad_id": 25544}).status_code == 400


def test_tc24_session_cookie_flags(site):
    c = site["app"].test_client()
    r = c.post("/api/auth/login", json={"email": site["emails"]["viewer"], "password": site["pw"]}, headers=H)
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_tc26_only_login_and_logout_write_the_session_cookie(site):
    # A slow request still in flight at logout must not answer with the old cookie and log the user back in.
    c = client(site, "viewer")
    for path in ("/api/auth/me", "/api/landing", "/api/me/subscriptions", "/api/objects?page_size=5"):
        r = c.get(path)
        assert r.status_code == 200 and "Set-Cookie" not in r.headers, path
    r = c.post("/api/auth/logout", headers=H)
    assert "Set-Cookie" in r.headers                            # logout clears the cookie
    assert c.get("/api/auth/me").json["user"] is None


def test_tc25_deactivated_user_cannot_log_in(site, admin):
    uid = one(site, "SELECT user_id FROM app_user WHERE email = %s", (site["emails"]["viewer"],))["user_id"]
    assert admin.patch(f"/api/admin/users/{uid}", json={"is_active": False}, headers=H).status_code == 200
    c = site["app"].test_client()
    assert c.post("/api/auth/login", json={"email": site["emails"]["viewer"], "password": site["pw"]}, headers=H).status_code == 401
    admin.patch(f"/api/admin/users/{uid}", json={"is_active": True}, headers=H)
    assert c.post("/api/auth/login", json={"email": site["emails"]["viewer"], "password": site["pw"]}, headers=H).status_code == 200


# --------------------------------------------------------------------------- TC-30 role matrix
MATRIX = [  # (method, path, anon, viewer, analyst, admin)
    ("GET", "/api/landing", 200, 200, 200, 200),
    ("GET", "/api/me/alerts", 401, 200, 200, 200),
    ("GET", "/api/me/subscriptions", 401, 200, 200, 200),
    ("GET", "/api/objects?format=csv", 401, 403, 200, 200),
    ("GET", "/api/conjunctions?format=csv", 401, 403, 200, 200),
    ("GET", "/api/reports/regions", 401, 403, 200, 200),
    ("GET", "/api/reports/history?norad=25544", 401, 403, 200, 200),
    ("GET", "/api/reports/fastest-decaying", 401, 403, 200, 200),
    ("GET", "/api/admin/users", 401, 403, 403, 200),
    ("GET", "/api/admin/config", 401, 403, 403, 200),
    ("GET", "/api/admin/jobs", 401, 403, 403, 200),
    ("GET", "/api/admin/logs?source=download", 401, 403, 403, 200),
    ("GET", "/api/admin/ref/country", 401, 403, 403, 200),
    ("GET", "/api/admin/mongo-status", 401, 403, 403, 200),
    ("POST", "/api/conjunctions/0/assess", 401, 403, 404, 404),
]


@pytest.mark.parametrize("method,path,expected", [(m[0], m[1], m[2:]) for m in MATRIX], ids=[m[1] for m in MATRIX])
def test_tc30_role_matrix(site, anon, viewer, analyst, admin, method, path, expected):
    got = [(c.get(path) if method == "GET" else c.post(path, headers=H)).status_code for c in (anon, viewer, analyst, admin)]
    assert got == list(expected), path


# --------------------------------------------------------------------------- TC-40..42 viewer flows
def test_tc40_subscribe_alert_acknowledge_unsubscribe(site, viewer):
    norad = 25544
    assert viewer.post("/api/me/subscriptions", json={"norad_id": norad}, headers=H).status_code == 201
    assert viewer.post("/api/me/subscriptions", json={"norad_id": 999999999}, headers=H).status_code == 404
    before = viewer.get("/api/me/alerts/count").json["unacknowledged"]
    with site["db"].mysql_conn("jobs") as conn:
        cur = conn.cursor()
        res = cur.callproc("sp_record_conjunction", (norad, 49044, "2032-02-02 02:02:02.000", 3.2, 9.1, None, 0, 0))
        conn.commit()
    site["created"]["events"].append(res[6])
    assert viewer.get("/api/me/alerts/count").json["unacknowledged"] == before + 1
    alert = next(a for a in viewer.get("/api/me/alerts").json["items"] if a["event_id"] == res[6])
    assert alert["risk_level"] == "HIGH"
    assert viewer.post(f"/api/me/alerts/{alert['alert_id']}/ack", headers=H).json["acknowledged"] == 1
    assert viewer.post(f"/api/me/alerts/{alert['alert_id']}/ack", headers=H).json["acknowledged"] == 0  # already done
    assert any(a["alert_id"] == alert["alert_id"] for a in viewer.get("/api/me/alerts?status=all").json["items"])
    viewer.post("/api/me/alerts/ack-all", headers=H)
    assert viewer.get("/api/me/alerts/count").json["unacknowledged"] == 0
    assert viewer.delete(f"/api/me/subscriptions/{norad}", headers=H).status_code == 200
    assert all(s["norad_id"] != norad for s in viewer.get("/api/me/subscriptions").json["items"])


def test_tc41_alerts_are_private(site, analyst):
    # an analyst sees only their own alerts, never the viewer's
    items = analyst.get("/api/me/alerts?status=all").json["items"]
    uid = one(site, "SELECT user_id FROM app_user WHERE email = %s", (site["emails"]["analyst"],))["user_id"]
    owners = {one(site, "SELECT user_id FROM alert WHERE alert_id = %s", (a["alert_id"],))["user_id"] for a in items}
    assert owners <= {uid}


# --------------------------------------------------------------------------- TC-50..52 analyst
def test_tc50_reports_and_exports(analyst):
    for path in ("/api/reports/regions", "/api/reports/debris-by-country", "/api/reports/debris-by-country?sort=leo",
                 "/api/reports/reentries-per-year", "/api/reports/region-year", "/api/reports/monthly-altitude?norad=25544",
                 "/api/reports/altitude-loss?norad=25544", "/api/reports/fastest-decaying?days=30"):
        r = analyst.get(path)
        assert r.status_code == 200 and ("items" in r.json or "series" in r.json), path
        csvr = analyst.get(path + ("&" if "?" in path else "?") + "format=csv")
        assert csvr.status_code == 200 and csvr.mimetype == "text/csv" and csvr.data.count(b"\n") >= 1, path
    leo = analyst.get("/api/reports/debris-by-country?sort=leo").json["items"]
    assert [r["debris_in_leo"] for r in leo] == sorted((r["debris_in_leo"] for r in leo), reverse=True)


def test_tc51_history_query(analyst):
    h = analyst.get("/api/reports/history?norad=25544").json
    assert h["count"] >= 1 and all(i["norad_id"] == 25544 for i in h["items"])
    assert analyst.get("/api/reports/history").status_code == 400
    assert analyst.get("/api/reports/history?norad=25544&from=2030-01-01&to=2029-01-01").status_code == 400


def test_tc52_analyst_runs_the_ai_agent(site, analyst, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")   # deterministic mode in tests (no network, no quota)
    ev = one(site, "SELECT event_id FROM conjunction_event WHERE time_of_closest_approach > UTC_TIMESTAMP() + "
                   "INTERVAL 2 HOUR ORDER BY probability_of_collision DESC LIMIT 1")
    if not ev:
        pytest.skip("no upcoming event")
    r = analyst.post(f"/api/conjunctions/{ev['event_id']}/assess", headers=H)
    assert r.status_code == 202
    aid = r.json["assessment_id"]
    for _ in range(120):
        a = analyst.get(f"/api/assessments/{aid}").json
        if a["status"] != "running":
            break
        time.sleep(0.5)
    try:
        assert a["status"] == "complete" and a["decision"] and a["steps"]
        assert any(s["tool_name"] == "compute_collision_probability" for s in a["steps"])
        if a["decision"] == "MANEUVER_RECOMMENDED":
            assert a["human_approval_required"] and a["maneuver"]["dv_mps"] > 0
        listed = analyst.get(f"/api/conjunctions/{ev['event_id']}/assessments").json["items"]
        assert listed[0]["assessment_id"] == aid
    finally:
        site["db"].execute("admin", "DELETE FROM agent_assessment WHERE assessment_id = %s", (aid,))


# --------------------------------------------------------------------------- TC-60..66 administrator
def test_tc60_user_management(site, admin):
    email = f"e2e-made-{secrets.token_hex(3)}@example.test"
    site["created"]["emails"].append(email)
    r = admin.post("/api/admin/users", json={"name": "Made", "email": email, "password": site["pw"], "role": "analyst"}, headers=H)
    assert r.status_code == 201
    uid = r.json["user_id"]
    assert admin.patch(f"/api/admin/users/{uid}", json={"role": "viewer"}, headers=H).status_code == 200
    assert one(site, "SELECT role FROM app_user WHERE user_id = %s", (uid,))["role"] == "viewer"
    assert admin.patch(f"/api/admin/users/{uid}", json={"role": "superuser"}, headers=H).status_code == 400
    me = one(site, "SELECT user_id FROM app_user WHERE email = %s", (site["emails"]["admin"],))["user_id"]
    assert admin.patch(f"/api/admin/users/{me}", json={"role": "viewer"}, headers=H).status_code == 400
    assert admin.delete(f"/api/admin/users/{me}", headers=H).status_code == 400
    assert admin.delete(f"/api/admin/users/{uid}", headers=H).status_code == 200


def test_tc61_reference_data_crud_and_integrity(site, admin):
    code = "ZZ" + secrets.token_hex(1).upper()
    assert admin.post("/api/admin/ref/country", json={"country_code": code, "name": "Testland"}, headers=H).status_code == 201
    assert admin.put(f"/api/admin/ref/country/{code}", json={"name": "Testland Republic"}, headers=H).json["updated"] == 1
    assert admin.post("/api/admin/ref/organisation", json={"org_id": code, "name": "Test Org", "org_type": "Academic",
                                                           "country_code": code}, headers=H).status_code == 201
    assert admin.post("/api/admin/ref/organisation", json={"org_id": code + "X", "name": "Bad", "org_type": "Alien",
                                                           "country_code": code}, headers=H).status_code == 400
    assert admin.delete(f"/api/admin/ref/country/{code}", headers=H).status_code == 200   # FK: org's country set NULL
    assert one(site, "SELECT country_code FROM organisation WHERE org_id = %s", (code,))["country_code"] is None
    assert admin.delete(f"/api/admin/ref/organisation/{code}", headers=H).status_code == 200
    # an organisation that still owns objects is protected by the foreign key
    assert admin.delete("/api/admin/ref/organisation/ISRO", headers=H).status_code == 409
    r = admin.post("/api/admin/ref/orbit_region", json={"name": "Overlap", "min_altitude_km": 100, "max_altitude_km": 900}, headers=H)
    assert r.status_code == 409 and "overlap" in r.json["error"].lower()
    assert admin.get("/api/admin/ref/launch_vehicle?q=PSLV").json["total"] >= 1
    assert admin.get("/api/admin/ref/nope").status_code == 404


def test_tc62_watchlist_rules(site, admin):
    added = admin.post("/api/admin/watchlist", json={"norad_id": 44804, "reason": "e2e"}, headers=H)
    assert added.status_code == 201
    decayed = one(site, "SELECT norad_id FROM space_object WHERE decay_date IS NOT NULL LIMIT 1")["norad_id"]
    assert admin.post("/api/admin/watchlist", json={"norad_id": decayed}, headers=H).status_code == 400
    assert admin.post("/api/admin/watchlist", json={"norad_id": 39370}, headers=H).status_code == 400   # Mars orbiter
    assert admin.post("/api/admin/watchlist", json={"norad_id": "abc"}, headers=H).status_code == 400
    assert any(w["norad_id"] == 44804 for w in admin.get("/api/admin/watchlist").json["items"])


def test_tc63_configuration_validation(admin):
    cfg = {c["config_key"]: c["config_value"] for c in admin.get("/api/admin/config").json["items"]}
    assert admin.put("/api/admin/config", json={"screening_threshold_km": 0}, headers=H).status_code == 400
    assert admin.put("/api/admin/config", json={"backup_cron": "every day"}, headers=H).status_code == 400
    assert admin.put("/api/admin/config", json={"celestrak_groups": "active; DROP TABLE x"}, headers=H).status_code == 400
    assert admin.put("/api/admin/config", json={"nonsense": 1}, headers=H).status_code == 400
    assert admin.put("/api/admin/config", json={"screening_threshold_km": cfg["screening_threshold_km"]}, headers=H).status_code == 200


def test_tc64_jobs_logs_and_cluster(site, admin):
    before = one(site, "SELECT COALESCE(MAX(run_id), 0) AS n FROM job_run WHERE job_name = 'space_weather'")["n"]
    r = admin.post("/api/admin/jobs/space_weather/run", headers=H)
    assert r.status_code == 202
    # The job starts on a background thread: wait for *this* run to appear and finish.
    run = None
    for _ in range(120):
        run = one(site, "SELECT run_id, status FROM job_run WHERE job_name = 'space_weather' AND run_id > %s "
                        "ORDER BY run_id DESC LIMIT 1", (before,))
        if run and run["status"] != "running":
            break
        time.sleep(0.5)
    assert run and run["status"] == "success"
    latest = {j["job_name"]: j for j in admin.get("/api/admin/jobs").json["latest"]}
    assert latest["space_weather"]["run_id"] == run["run_id"]
    assert admin.post("/api/admin/jobs/unknown/run", headers=H).status_code == 404
    assert admin.get("/api/admin/logs?source=download&limit=5").json["items"]
    assert admin.get("/api/admin/logs?source=evil").status_code == 400
    st = admin.get("/api/admin/mongo-status").json
    assert all(st["processes"].values()) and st["sharding"]["shard_key"] == {"norad_id": 1}


# --------------------------------------------------------------------------- TC-70..72 security
INJECTIONS = ["' OR 1=1 --", "\" OR \"\"=\"", "%' UNION SELECT password_hash FROM app_user -- ", "1; DROP TABLE alert"]


@pytest.mark.parametrize("payload", INJECTIONS)
def test_tc70_sql_injection_is_inert(anon, payload, site):
    r = anon.get("/api/objects", query_string={"q": payload, "status": "all"})
    assert r.status_code == 200 and r.json["total"] == 0
    r = anon.get("/api/conjunctions", query_string={"q": payload, "when": "all"})
    assert r.status_code == 200 and r.json["total"] == 0
    assert one(site, "SELECT COUNT(*) AS n FROM alert")["n"] >= 0          # table still exists


def test_tc71_sort_and_identifier_parameters_are_whitelisted(anon):
    r = anon.get("/api/objects", query_string={"sort": "name; DROP TABLE space_object"})
    assert r.status_code == 200 and r.json["total"] > 0


def test_tc72_markup_in_user_data_is_not_executed(site, admin):
    # stored as text; every page renders it through esc() (checked in the browser test)
    email = f"e2e-xss-{secrets.token_hex(3)}@example.test"
    site["created"]["emails"].append(email)
    r = admin.post("/api/admin/users", json={"name": "<img src=x onerror=alert(1)>", "email": email, "password": site["pw"],
                                             "role": "viewer"}, headers=H)
    assert r.status_code == 201
    assert one(site, "SELECT name FROM app_user WHERE email = %s", (email,))["name"].startswith("<img")
