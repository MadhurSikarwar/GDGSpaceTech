"""API and role enforcement against a live OrbitWatch database (skipped when none is reachable).

Run against a disposable database, e.g.  set MYSQL_PORT=3307 && pytest tests/test_api_roles.py
Creates three throwaway users and removes them (and any test event) afterwards.
"""
import secrets

import pytest

H = {"X-Requested-With": "OrbitWatch"}


@pytest.fixture(scope="module")
def env():
    from orbitwatch import auth, db
    try:
        db.query("viewer", "SELECT 1 AS x")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no OrbitWatch database reachable: {exc}")
    from orbitwatch.web import create_app
    app = create_app(https=False)
    app.testing = True
    pw = secrets.token_urlsafe(12)
    tag = secrets.token_hex(4)
    emails = {r: f"pytest-{r}-{tag}@example.test" for r in ("viewer", "analyst", "admin")}
    for role, email in emails.items():
        auth.create_user(f"Pytest {role}", email, pw, role)
    created_events = []
    yield {"app": app, "pw": pw, "emails": emails, "db": db, "events": created_events}
    for ev in created_events:
        db.execute("admin", "DELETE FROM conjunction_event WHERE event_id = %s", (ev,))
    for email in emails.values():
        db.execute("admin", "DELETE FROM app_user WHERE email = %s", (email,))


def login(env, role):
    c = env["app"].test_client()
    r = c.post("/api/auth/login", json={"email": env["emails"][role], "password": env["pw"]}, headers=H)
    assert r.status_code == 200, r.json
    return c


def test_public_endpoints(env):
    c = env["app"].test_client()
    assert c.get("/api/stats").status_code == 200
    assert c.get("/api/objects?q=ISS&page_size=5").status_code == 200
    assert c.get("/api/conjunctions?page_size=5").status_code == 200
    assert c.get("/api/reports/regions").status_code == 401
    assert c.get("/api/objects?format=csv").status_code == 401  # export is an analyst function


def test_csrf_header_required(env):
    c = env["app"].test_client()
    assert c.post("/api/auth/login", json={"email": "x@y.z", "password": "nope-nope"}).status_code == 400


def test_role_gates(env):
    viewer, analyst, admin = (login(env, r) for r in ("viewer", "analyst", "admin"))
    assert viewer.get("/api/reports/regions").status_code == 403
    assert analyst.get("/api/reports/regions").status_code == 200
    assert analyst.get("/api/reports/debris-by-country?format=csv").mimetype == "text/csv"
    assert analyst.get("/api/admin/users").status_code == 403
    assert admin.get("/api/admin/users").status_code == 200


def test_registration_always_creates_a_viewer(env):
    c = env["app"].test_client()
    email = f"pytest-reg-{secrets.token_hex(4)}@example.test"
    r = c.post("/api/auth/register", json={"name": "Reg", "email": email, "password": env["pw"], "role": "admin"},
               headers=H)
    try:
        assert r.status_code == 201 and r.json["user"]["role"] == "viewer"
    finally:
        env["db"].execute("admin", "DELETE FROM app_user WHERE email = %s", (email,))


def test_database_denies_what_the_role_lacks(env):
    """Privileges are enforced by MySQL itself, not only by the application."""
    import mysql.connector
    db = env["db"]
    with pytest.raises(mysql.connector.Error) as exc:
        db.query("viewer", "SELECT password_hash FROM app_user")
    assert exc.value.errno == 1142
    with pytest.raises(mysql.connector.Error) as exc:
        db.execute("analyst", "UPDATE system_config SET config_value = '1' WHERE config_key = 'backup_keep'")
    assert exc.value.errno == 1142
    with pytest.raises(mysql.connector.Error) as exc:
        db.execute("viewer", "UPDATE alert SET user_id = 1 WHERE alert_id = 0")   # only ack columns are updatable
    assert exc.value.errno in (1142, 1143)


def test_subscription_alert_and_acknowledge(env):
    viewer = login(env, "viewer")
    norad = env["db"].query_one("viewer", "SELECT norad_id FROM space_object WHERE decay_date IS NULL LIMIT 1")["norad_id"]
    other = env["db"].query_one("viewer", "SELECT norad_id FROM space_object WHERE decay_date IS NULL AND norad_id <> %s "
                                          "LIMIT 1", (norad,))["norad_id"]
    assert viewer.post("/api/me/subscriptions", json={"norad_id": norad}, headers=H).status_code == 201
    before = viewer.get("/api/me/alerts/count").json["unacknowledged"]
    with env["db"].mysql_conn("jobs") as conn:
        cur = conn.cursor()
        res = cur.callproc("sp_record_conjunction", (norad, other, "2031-01-01 00:00:00.000", 0.4, 11.0, None, 0, 0))
        conn.commit()
    env["events"].append(res[6])
    assert res[7]  # new event
    assert viewer.get("/api/me/alerts/count").json["unacknowledged"] == before + 1
    # Re-recording the same encounter refines it instead of alerting again.
    with env["db"].mysql_conn("jobs") as conn:
        cur = conn.cursor()
        res2 = cur.callproc("sp_record_conjunction", (other, norad, "2031-01-01 00:03:00.000", 0.3, 11.0, None, 0, 0))
        conn.commit()
    assert res2[6] == res[6] and not res2[7]
    assert viewer.get("/api/me/alerts/count").json["unacknowledged"] == before + 1
    alert = next(a for a in viewer.get("/api/me/alerts").json["items"] if a["event_id"] == res[6])
    assert alert["risk_level"] == "CRITICAL"
    assert viewer.post(f"/api/me/alerts/{alert['alert_id']}/ack", headers=H).json["acknowledged"] == 1
    assert viewer.delete(f"/api/me/subscriptions/{norad}", headers=H).status_code == 200


def test_overlapping_region_rejected_by_trigger(env):
    admin = login(env, "admin")
    r = admin.post("/api/admin/ref/orbit_region", json={"name": "Overlap", "min_altitude_km": 100,
                                                        "max_altitude_km": 900}, headers=H)
    assert r.status_code == 409 and "overlap" in r.json["error"].lower()
