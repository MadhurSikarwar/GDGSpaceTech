"""The watchlist, assessment comparison, error details, the space-weather cache and push notifications.

Runs against the configured database (skipped when none is reachable). Outbound requests (push services, NOAA) are
replaced by fakes: nothing leaves this machine. Users and events created here are removed afterwards.
"""
import json
import secrets

import pytest

H = {"X-Requested-With": "OrbitWatch"}


# --------------------------------------------------------------------------- with the database
@pytest.fixture(scope="module")
def site():
    from orbitwatch import auth, db
    try:
        db.query("viewer", "SELECT 1 AS x")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no OrbitWatch database reachable: {exc}")
    from orbitwatch.web import create_app
    app = create_app(https=False)
    app.testing = True
    pw, tag = secrets.token_urlsafe(12), secrets.token_hex(4)
    emails = {r: f"feat-{r}-{tag}@example.test" for r in ("viewer", "analyst", "admin")}
    for role, email in emails.items():
        auth.create_user(f"Feature {role}", email, pw, role)
    yield {"app": app, "pw": pw, "emails": emails, "db": db}
    for email in emails.values():
        db.execute("admin", "DELETE FROM email_outbox WHERE to_address = %s", (email,))   # e.g. failed-login notices
        db.execute("admin", "DELETE FROM app_user WHERE email = %s", (email,))


def login(site, role):
    c = site["app"].test_client()
    assert c.post("/api/auth/login", json={"email": site["emails"][role], "password": site["pw"]}, headers=H).status_code == 200
    return c


def uid(site, role):
    return site["db"].query_one("admin", "SELECT user_id FROM app_user WHERE email = %s", (site["emails"][role],))["user_id"]


class FakePost:
    def __init__(self, status=204):
        self.calls, self.status = [], status

    def __call__(self, url, json=None, **kw):
        assert kw.get("allow_redirects") is False                 # a redirect could lead anywhere
        self.calls.append((url, json))
        return type("R", (), {"status_code": self.status})()


def test_watchlist_lists_followed_objects_with_their_events_in_the_window(site):
    db, viewer = site["db"], uid(site, "viewer")
    c = login(site, "viewer")
    assert site["app"].test_client().get("/api/me/watchlist").status_code == 401
    assert c.get("/api/me/watchlist").json["items"] == []
    other = db.query_one("admin", "SELECT norad_id FROM space_object WHERE norad_id <> 25544 AND in_earth_orbit LIMIT 1")
    db.execute("admin", "INSERT IGNORE INTO subscription (user_id, norad_id) VALUES (%s, 25544)", (viewer,))
    from datetime import datetime, timedelta, timezone
    in_two_days = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S.000")
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        soon = cur.callproc("sp_record_conjunction", (other["norad_id"], 25544, in_two_days, 0.8, 9.1, None, 0, 0))
        conn.commit()
        far = cur.callproc("sp_record_conjunction", (25544, other["norad_id"], "2032-05-05 05:05:05.000", 3.2, 9.1,
                                                     None, 0, 0))
        conn.commit()
    try:
        r = c.get("/api/me/watchlist?days=7").json
        iss = next(o for o in r["items"] if o["norad_id"] == 25544)
        ids = [e["event_id"] for e in iss["events"]]
        assert soon[6] in ids and far[6] not in ids                     # inside the window only
        ev = next(e for e in iss["events"] if e["event_id"] == soon[6])
        assert ev["other_norad"] == other["norad_id"]                   # the followed object was the secondary
        assert c.get("/api/me/watchlist?days=x").status_code == 400
    finally:
        db.execute("admin", "DELETE FROM conjunction_event WHERE event_id IN (%s, %s)", (soon[6], far[6]))
        db.execute("admin", "DELETE FROM subscription WHERE user_id = %s", (viewer,))


def test_two_assessments_compare_side_by_side_and_export(site):
    db = site["db"]
    ev = db.query_one("admin", "SELECT event_id FROM conjunction_event ORDER BY event_id DESC LIMIT 1")
    if not ev:
        pytest.skip("no close approach to attach assessments to")
    _, a = db.execute("admin", "INSERT INTO agent_assessment (event_id, engine, status, risk_tier, decision, pc_before, "
                               "pc_after, delta_v_mps) VALUES (%s, 'test', 'complete', 'HIGH', 'MANEUVER_RECOMMENDED', "
                               "1e-3, 1e-5, 0.20)", (ev["event_id"],))
    _, b = db.execute("admin", "INSERT INTO agent_assessment (event_id, engine, status, risk_tier, decision, pc_before, "
                               "pc_after, delta_v_mps, parent_assessment_id, feedback) VALUES (%s, 'test', 'complete', "
                               "'HIGH', 'MANEUVER_RECOMMENDED', 1e-3, 1e-6, 0.10, %s, 'too much fuel')",
                      (ev["event_id"], a))
    try:
        r = site["app"].test_client().get(f"/api/assessments/compare?a={a}&b={b}").json
        rows = {x["field"]: x for x in r["rows"]}
        assert r["same_event"] and rows["parent_assessment_id"]["b"] == a and rows["feedback"]["b"] == "too much fuel"
        assert rows["delta_v_mps"]["change"] == "-50% (better)" and rows["risk_tier"]["change"] == ""
        assert site["app"].test_client().get(f"/api/assessments/compare?a={a}").status_code == 400
        assert login(site, "viewer").get(f"/api/assessments/compare?a={a}&b={b}&format=csv").status_code == 403
        csv = login(site, "analyst").get(f"/api/assessments/compare?a={a}&b={b}&format=csv")
        assert csv.status_code == 200 and csv.mimetype == "text/csv"
        assert f"assessment_{a},assessment_{b}" in csv.get_data(as_text=True).splitlines()[0]
        assert r["options"] == {"a": [], "b": []} and r["trace"] == {"a": [], "b": []}      # rows inserted by hand
        assert site["app"].test_client().get(f"/api/assessments/compare?a={a}&b={a}").status_code == 400
        assert login(site, "viewer").get(f"/api/assessments/compare?a={a}&b={b}&format=pdf").status_code == 403
        doc = login(site, "analyst").get(f"/api/assessments/compare?a={a}&b={b}&format=pdf")
        assert doc.status_code == 200 and doc.mimetype == "application/pdf"
        text = pdf_text(doc.get_data())
        assert f"assessment #{a} vs #{b}" in text and "too much fuel" in text and "-50% (better)" in text
    finally:
        db.execute("admin", "DELETE FROM agent_assessment WHERE assessment_id IN (%s, %s)", (b, a))


def test_database_error_details_are_for_administrators_only(site):
    """A refused value answers with the plain sentence; the raw MySQL text only reaches an administrator."""
    import mysql.connector
    from orbitwatch.web import create_app
    app = create_app(https=False)

    @app.get("/api/_boom")
    def _boom():
        raise mysql.connector.errors.DataError(msg="Data too long for column 'name' at row 1", errno=1406)
    for role, sees in (("viewer", False), ("admin", True)):
        c = app.test_client()
        c.post("/api/auth/login", json={"email": site["emails"][role], "password": site["pw"]}, headers=H)
        r = c.get("/api/_boom")
        assert r.status_code == 400 and r.json["error"] == "That value is not valid here."
        assert ("detail" in r.json) is sees
    assert "detail" not in app.test_client().get("/api/_boom").json                       # anonymous visitor


def test_live_space_weather_is_fetched_once_for_many_requests(site, monkeypatch, tmp_path):
    from orbitwatch.jobs import spaceweather as sw_job
    from orbitwatch.physics import spaceweather
    from datetime import datetime
    calls = []

    def fake_fetch():
        calls.append(1)
        return {"observed_at": datetime(2026, 1, 1), "kp": 2.0, "ap": 7.0, "f107": 100.0, "activity": "QUIET",
                "drag_scalar": 1.0, "source": "NOAA SWPC", "live": True}
    monkeypatch.setattr(sw_job, "latest", lambda *a, **k: None)             # as if no stored reading were recent
    monkeypatch.setattr(spaceweather, "fetch", fake_fetch)
    monkeypatch.setattr(spaceweather, "CACHE_FILE", tmp_path / "sw.json")
    c = site["app"].test_client()
    answers = [c.get("/api/space-weather").json for _ in range(5)]
    assert len(calls) == 1 and all(a["kp"] == 2.0 and a["stored"] is False for a in answers)


# --------------------------------------------------------------------------- push notifications
def _browser_keys():
    """A browser's side of a push subscription: its P-256 key pair and auth secret."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from orbitwatch.push import b64u
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, b64u(pub), b64u(secrets.token_bytes(16))


def _decrypt(body, ua_key, ua_pub_b64, auth_b64):
    """RFC 8291 from the receiving browser's side, written independently of orbitwatch.push.encrypt."""
    import struct
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from orbitwatch.push import unb64u
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_pub = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    assert rs == 4096 and idlen == 65
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))

    def hk(s, k, info, n):
        return HKDF(algorithm=hashes.SHA256(), length=n, salt=s, info=info).derive(k)
    ikm = hk(unb64u(auth_b64), shared, b"WebPush: info\x00" + unb64u(ua_pub_b64) + as_pub, 32)
    plain = AESGCM(hk(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(
        hk(salt, ikm, b"Content-Encoding: nonce\x00", 12), ciphertext, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_push_message_decrypts_in_the_browser_and_is_signed():
    import json as _json
    import time
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from orbitwatch import push
    key, pub, auth = _browser_keys()
    msg = _json.dumps({"title": "HIGH close approach", "body": "ISS vs debris"}).encode()
    body = push.encrypt(msg, pub, auth)
    assert _decrypt(body, key, pub, auth) == msg
    assert push.encrypt(msg, pub, auth) != body                     # a fresh salt and key every time
    header = push.vapid_authorization("https://fcm.googleapis.com/fcm/send/abc")
    token, k = header[len("vapid t="):].split(", k=")
    h, claims, sig = token.split(".")
    c = _json.loads(push.unb64u(claims))
    assert c["aud"] == "https://fcm.googleapis.com" and c["exp"] > time.time()
    assert c["sub"].startswith(("mailto:", "https://"))
    vk = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64u(k))
    raw = push.unb64u(sig)
    vk.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
              f"{h}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))       # raises if the signature is wrong
    assert k == push.public_key()


@pytest.mark.parametrize("endpoint,expected", [
    ("https://fcm.googleapis.com/fcm/send/abc:def", True), ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://web.push.apple.com/QGx", True), ("https://wns2-bl2p.notify.windows.com/w/?token=x", True),
    ("http://fcm.googleapis.com/fcm/send/x", False), ("https://fcm.googleapis.com.evil.example/x", False),
    ("https://127.0.0.1/x", False), ("https://169.254.169.254/latest/meta-data", False),
    ("https://user:pw@fcm.googleapis.com/x", False), ("https://fcm.googleapis.com:8443/x", False),
    ("https://notify.windows.com.evil.example/x", False),
])
def test_only_real_push_services_are_accepted(endpoint, expected):
    from orbitwatch import push
    assert push.endpoint_allowed(endpoint) is expected


class FakePushService:
    def __init__(self, status=201):
        self.status, self.calls = status, []

    def __call__(self, url, data=None, headers=None, **kw):
        assert kw.get("allow_redirects") is False and headers["Content-Encoding"] == "aes128gcm"
        assert headers["Authorization"].startswith("vapid t=")
        self.calls.append((url, data, headers))
        return type("R", (), {"status_code": self.status})()


def _subscribe(c, endpoint):
    key, pub, auth = _browser_keys()
    r = c.post("/api/me/push/subscriptions", json={"endpoint": endpoint, "keys": {"p256dh": pub, "auth": auth}}, headers=H)
    return r, (key, pub, auth)


def _record_event(db, primary, secondary, miss_km, days=1):
    from datetime import datetime, timedelta, timezone
    tca = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.000")
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        res = cur.callproc("sp_record_conjunction", (primary, secondary, tca, miss_km, 9.1, None, 0, 0))
        conn.commit()
    return res[6]


def test_push_devices_are_managed_per_user_and_never_reveal_the_endpoint(site):
    viewer, analyst = login(site, "viewer"), login(site, "analyst")
    ep = f"https://fcm.googleapis.com/fcm/send/test-{secrets.token_hex(8)}"
    assert viewer.post("/api/me/push/subscriptions", json={"endpoint": "https://example.com/x", "keys": {}},
                       headers=H).status_code == 400
    r, _ = _subscribe(viewer, ep)
    assert r.status_code == 201
    got = viewer.get("/api/me/push").json
    assert got["public_key"] and len(got["devices"]) == 1 and ep not in str(got)
    assert got["devices"][0]["endpoint_hash"] == r.json["endpoint_hash"]
    assert analyst.delete(f"/api/me/push/subscriptions/{r.json['subscription_id']}", headers=H).json["removed"] == 0
    assert viewer.put("/api/me/push", json={"min_risk_level": "CRITICAL"}, headers=H).status_code == 200
    assert viewer.get("/api/me/push").json["min_risk_level"] == "CRITICAL"
    assert viewer.delete(f"/api/me/push/subscriptions/hash/{r.json['endpoint_hash']}", headers=H).json["removed"] == 1
    assert viewer.get("/api/me/push").json["devices"] == []
    assert site["app"].test_client().get("/api/me/push").status_code == 401


def test_new_alerts_are_pushed_once_per_device_retried_and_gone_devices_removed(site, monkeypatch):
    import json as _json
    from orbitwatch import push
    db, viewer_id = site["db"], uid(site, "viewer")
    c = login(site, "viewer")
    c.put("/api/me/push", json={"min_risk_level": "HIGH"}, headers=H)
    _, k1 = _subscribe(c, f"https://fcm.googleapis.com/fcm/send/a-{secrets.token_hex(6)}")
    _subscribe(c, f"https://updates.push.services.mozilla.com/wpush/v2/b-{secrets.token_hex(6)}")
    other = db.query_one("admin", "SELECT norad_id FROM space_object WHERE norad_id <> 25544 AND in_earth_orbit LIMIT 1")
    db.execute("admin", "INSERT IGNORE INTO subscription (user_id, norad_id) VALUES (%s, 25544)", (viewer_id,))
    ev = _record_event(db, 25544, other["norad_id"], 3.2)                                     # HIGH
    ev2 = None
    try:
        alert = db.query_one("admin", "SELECT alert_id FROM alert WHERE event_id = %s AND user_id = %s", (ev, viewer_id))
        monkeypatch.setattr(push.requests, "post", FakePushService(status=500))
        push.send_alerts()
        assert db.query_one("admin", "SELECT COUNT(*) n FROM push_delivery WHERE alert_id = %s AND status = 'pending'",
                            (alert["alert_id"],))["n"] == 2                         # both devices will be retried
        ok_ = FakePushService(status=201)
        monkeypatch.setattr(push.requests, "post", ok_)
        push.send_alerts()
        push.send_alerts()                                                           # idempotent
        mine = [(u, d) for u, d, _ in ok_.calls if "/a-" in u or "/b-" in u]
        assert len(mine) == 2
        msg = _json.loads(_decrypt(next(d for u, d in mine if "fcm" in u), *k1))
        assert msg["title"].startswith("HIGH close approach") and msg["url"] == f"/#/conjunctions?event={ev}"
        assert all(d["last_success_at"] for d in c.get("/api/me/push").json["devices"])
        # A device the push service no longer knows is removed at its next delivery.
        ev2 = _record_event(db, other["norad_id"], 25544, 2.5, days=2)   # another day: not merged
        monkeypatch.setattr(push.requests, "post", FakePushService(status=410))
        push.send_alerts()
        assert c.get("/api/me/push").json["devices"] == []
    finally:
        db.execute("admin", "DELETE FROM conjunction_event WHERE event_id IN (%s, %s)", (ev, ev2 or ev))
        db.execute("admin", "DELETE FROM subscription WHERE user_id = %s", (viewer_id,))
        db.execute("admin", "DELETE FROM push_subscription WHERE user_id = %s", (viewer_id,))


def test_push_respects_the_chosen_risk_level_and_the_test_button(site, monkeypatch):
    from orbitwatch import push, ratelimit
    db, viewer_id = site["db"], uid(site, "viewer")
    c = login(site, "viewer")
    ratelimit.clear("push_test", viewer_id)
    assert c.post("/api/me/push/test", headers=H).status_code == 400              # no device yet
    c.put("/api/me/push", json={"min_risk_level": "CRITICAL"}, headers=H)
    _subscribe(c, f"https://fcm.googleapis.com/fcm/send/c-{secrets.token_hex(6)}")
    other = db.query_one("admin", "SELECT norad_id FROM space_object WHERE norad_id <> 25544 AND in_earth_orbit LIMIT 1")
    db.execute("admin", "INSERT IGNORE INTO subscription (user_id, norad_id) VALUES (%s, 25544)", (viewer_id,))
    ev = _record_event(db, 25544, other["norad_id"], 3.2)                                     # HIGH
    try:
        fake = FakePushService()
        monkeypatch.setattr(push.requests, "post", fake)
        push.send_alerts()
        assert not [u for u, *_ in fake.calls if "/c-" in u]                       # HIGH < CRITICAL: nothing sent
        r = c.post("/api/me/push/test", headers=H)
        assert r.status_code == 200 and r.json["message"] == "Sent to 1 of 1 device." and len(fake.calls) == 1
    finally:
        db.execute("admin", "DELETE FROM conjunction_event WHERE event_id = %s", (ev,))
        db.execute("admin", "DELETE FROM subscription WHERE user_id = %s", (viewer_id,))
        db.execute("admin", "DELETE FROM push_subscription WHERE user_id = %s", (viewer_id,))
        ratelimit.clear("push_test", viewer_id)


# --------------------------------------------------------------------------- login: challenge, trusted device, notice
def _attempt(c, email, password, ip, answer=None):
    body_ = {"email": email, "password": password, **({"challenge": answer} if answer else {})}
    return c.post("/api/auth/login", json=body_, headers=H, environ_base={"REMOTE_ADDR": ip})


def _clear_login(email, ip):
    from orbitwatch import ratelimit
    for name, key in (("login_ip", ip), ("login_email", email), ("login", f"{email}|{ip}"), ("login_notice", email)):
        ratelimit.clear(name, key)


def test_after_three_failures_a_solved_challenge_is_needed_and_works_once(site):
    from orbitwatch import challenge
    email, ip = site["emails"]["viewer"], f"192.0.2.{secrets.randbelow(250) + 1}"
    c = site["app"].test_client()
    try:
        assert [_attempt(c, email, "wrong-wrong", ip).status_code for _ in range(3)] == [401, 401, 401]
        r = _attempt(c, email, site["pw"], ip)                       # the right password, but no challenge solved
        assert r.status_code == 401 and r.json["challenge"]["bits"] == challenge.BITS
        assert _attempt(c, email, site["pw"], ip, {**r.json["challenge"], "nonce": 0}).json.get("challenge")  # wrong nonce
        forged = challenge.solve({**challenge.issue(), "bits": 1})                # cheaper, but the signature breaks
        assert _attempt(c, email, site["pw"], ip, forged).json.get("challenge")
        answer = challenge.solve(r.json["challenge"])
        assert _attempt(c, email, site["pw"], ip, answer).status_code == 200      # solved: the password is checked
        # c is now a trusted browser (it logged in); a new one, after new failures, must not reuse the answer.
        fresh = site["app"].test_client()
        for _ in range(3):
            _attempt(fresh, email, "wrong-wrong", ip)
        assert _attempt(fresh, email, site["pw"], ip, answer).json.get("challenge")  # not accepted twice
        assert _attempt(c, email, site["pw"], ip).status_code == 200              # the trusted browser is not asked
    finally:
        _clear_login(email, ip)


def test_browser_solver_answers_what_the_server_accepts():
    """frontend/js/logic.js solveChallenge (run in Node) against orbitwatch.challenge.verify."""
    import json as _json
    import shutil
    import subprocess
    from pathlib import Path
    from orbitwatch import challenge
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    url = (Path(__file__).resolve().parent.parent / "frontend" / "js" / "logic.js").as_uri()
    ch = challenge.issue()
    code = f"import {{ solveChallenge }} from '{url}'; console.log(JSON.stringify(await solveChallenge({_json.dumps(ch)})));"
    r = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    from orbitwatch import ratelimit
    answer = _json.loads(r.stdout)
    try:
        assert challenge.verify(answer) and not challenge.verify(answer)          # accepted once
    finally:
        ratelimit.clear("challenge_used", ch["salt"])


def test_a_trusted_browser_is_not_locked_out_by_other_peoples_failures(site):
    """20 failures from many addresses lock the account for new browsers; the owner's own browser still gets in."""
    email = site["emails"]["analyst"]
    owner = site["app"].test_client()
    home = f"192.0.2.{secrets.randbelow(250) + 1}"
    attackers = [f"203.0.113.{i + 1}" for i in range(20)]
    try:
        assert _attempt(owner, email, site["pw"], home).status_code == 200          # sets the trusted-device cookie
        from orbitwatch import challenge
        for ip in attackers:
            r = _attempt(site["app"].test_client(), email, "guess-guess", ip)
            if r.status_code == 401 and r.json.get("challenge"):
                _attempt(site["app"].test_client(), email, "guess-guess", ip, challenge.solve(r.json["challenge"]))
        stranger = _attempt(site["app"].test_client(), email, site["pw"], "198.51.100.77")
        assert stranger.status_code == 429                                          # a new browser is locked out
        assert _attempt(owner, email, site["pw"], home).status_code == 200          # the owner's browser is not
    finally:
        _clear_login(email, home)
        _clear_login(email, "198.51.100.77")
        for ip in attackers:
            _clear_login(email, ip)


def test_the_owner_is_emailed_once_about_repeated_failed_logins(site):
    from orbitwatch import challenge
    db, email = site["db"], site["emails"]["admin"]
    ips = [f"203.0.113.{i + 100}" for i in range(12)]
    try:
        for ip in ips:
            c = site["app"].test_client()
            r = _attempt(c, email, "guess-guess", ip)
            if r.status_code == 401 and r.json.get("challenge"):
                _attempt(c, email, "guess-guess", ip, challenge.solve(r.json["challenge"]))
        rows = db.query("admin", "SELECT kind, subject FROM email_outbox WHERE to_address = %s AND kind = 'security'", (email,))
        assert len(rows) == 1 and "failed sign-ins" in rows[0]["subject"]
    finally:
        db.execute("admin", "DELETE FROM email_outbox WHERE to_address = %s", (email,))
        for ip in ips:
            _clear_login(email, ip)


def test_the_space_weather_cache_survives_a_restart_and_serves_the_agent(monkeypatch, tmp_path):
    """A second process (or the same one after a restart) reads the file instead of asking NOAA again."""
    from datetime import datetime
    from orbitwatch.physics import spaceweather
    calls = []
    reading = {"observed_at": datetime(2026, 1, 1, 3), "kp": 4.3, "ap": 30.0, "f107": 140.0, "activity": "MODERATE",
               "drag_scalar": 1.46, "source": "NOAA SWPC", "live": True}
    monkeypatch.setattr(spaceweather, "CACHE_FILE", tmp_path / "sw.json")
    monkeypatch.setattr(spaceweather, "fetch", lambda: calls.append(1) or dict(reading))
    first = spaceweather.cached_fetch()
    import threading
    monkeypatch.setattr(spaceweather, "_cache_lock", threading.Lock())     # as in a fresh process
    again = spaceweather.cached_fetch()
    assert len(calls) == 1 and again == first and again["observed_at"] == datetime(2026, 1, 1, 3)
    # Older than its 10 minutes: the next caller asks NOAA again.
    cached = json.loads((tmp_path / "sw.json").read_text(encoding="utf-8"))
    (tmp_path / "sw.json").write_text(json.dumps({**cached, "_fetched_at": cached["_fetched_at"] - 601}), encoding="utf-8")
    spaceweather.cached_fetch()
    assert len(calls) == 2


def pdf_text(data):
    """Check a PDF's structure (every xref offset points at its object) and return the text of its pages."""
    import re
    import zlib
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    xref_at = int(re.search(rb"startxref\s+(\d+)", data).group(1))
    assert data[xref_at:xref_at + 4] == b"xref"
    entries = re.findall(rb"(\d{10}) 00000 n ", data[xref_at:])
    for i, off in enumerate(entries, start=1):
        assert data[int(off):].startswith(f"{i} 0 obj".encode()), f"object {i} is not where the xref says"
    pages = [zlib.decompress(m) for m in re.findall(rb"stream\n(.*?)\nendstream", data, re.S)]
    def unescape(t):                                  # PDF strings escape ( ) and the backslash itself
        return re.sub(r"\\(.)", r"\1", t.decode("cp1252"))
    strings = rb"\(((?:\\.|[^\\)])*)\) Tj"
    return " ".join(" ".join(unescape(t) for t in re.findall(strings, pg)) for pg in pages)


def test_long_tables_flow_onto_new_pages_and_wrap():
    from orbitwatch import pdf
    doc = pdf.Document("wrap test")
    doc.heading("A long table")
    doc.table(["#", "Text"], [[i, "word " * 60] for i in range(40)], [1, 9])
    data = doc.render()
    assert data.count(b"/Type /Page ") >= 3
    text = pdf_text(data)
    assert "page 1 of" in text and "word word" in text
    assert max(pdf.text_width(line, 8) for line in pdf.wrap("word " * 60, 300, 8)) <= 300
