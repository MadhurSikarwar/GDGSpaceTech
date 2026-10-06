"""Tests of the operations layer: synthetic demo, manoeuvre approval, password change/reset, rate limits,
e-mail delivery (local SMTP sink), event log visibility, live stream, provenance, re-entry gate, Space-Track
when not configured, reconciliation idempotence.

Runs against the configured database (skipped when none is reachable):
    set MYSQL_PORT=3307 && pytest tests/test_operations.py -v
Everything it creates is removed afterwards.
"""
import re
import secrets
import socket
import time
from datetime import datetime

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
    tag = secrets.token_hex(3)
    emails = {r: f"ops-{r}-{tag}@example.test" for r in ("viewer", "analyst")}
    ids = {r: auth.create_user(f"Ops {r}", e, pw, r) for r, e in emails.items()}
    target = db.query_one("viewer", """SELECT co.norad_id FROM current_orbit co JOIN space_object so USING (norad_id)
                                        WHERE so.in_earth_orbit AND co.mean_altitude_km BETWEEN 300 AND 1200
                                          AND co.epoch > UTC_TIMESTAMP() - INTERVAL 10 DAY
                                        ORDER BY co.norad_id = 25544 DESC, co.norad_id LIMIT 1""")
    if target is None:
        pytest.skip("no LEO object with a recent orbit to build a synthetic encounter against")
    state = {"app": app, "pw": pw, "emails": emails, "ids": ids, "db": db, "target": target["norad_id"], "scenarios": []}
    yield state
    from orbitwatch import demo
    for sid in state["scenarios"]:
        db.execute("admin", "DELETE FROM demo_scenario WHERE scenario_id = %s", (sid,))
    for e in emails.values():
        db.execute("admin", "DELETE FROM email_outbox WHERE to_address = %s", (e,))
        db.execute("admin", "DELETE FROM app_user WHERE email = %s", (e,))
    del demo


def login(env, role, pw=None):
    c = env["app"].test_client()
    r = c.post("/api/auth/login", json={"email": env["emails"][role], "password": pw or env["pw"]}, headers=H)
    assert r.status_code == 200, r.json
    return c


# ----------------------------------------------------------------------------- synthetic demo
def test_ops01_synthetic_encounter_is_exact_and_isolated(env):
    from orbitwatch import demo, orbital
    from orbitwatch.jobs import screening
    db = env["db"]
    before = db.query_one("admin", "SELECT (SELECT COUNT(*) FROM space_object) so, (SELECT COUNT(*) FROM conjunction_event) ce")
    sc = demo.create(env["target"], env["ids"]["analyst"], "analyst", lead_min=360, miss_km=0.2)
    env["scenarios"].append(sc["scenario_id"])
    ev = sc["events"][0]
    assert abs(float(ev["miss_distance_km"]) - 0.2) < 0.05 and ev["designation"].startswith("SYN-")
    # the synthetic object is a genuine SGP4 element set: SGP4 reproduces the encounter independently
    obj = db.query_one("viewer", f"SELECT epoch, {', '.join(orbital.ELEMENT_FIELDS)} FROM demo_object "
                                 "WHERE scenario_id = %s", (sc["scenario_id"],))
    tgt = db.query_one("viewer", f"SELECT norad_id, epoch, {', '.join(orbital.ELEMENT_FIELDS)} FROM current_orbit "
                                 "WHERE norad_id = %s", (env["target"],))
    ref = screening._refine(orbital.satrec_from_elements(tgt), orbital.satrec_from_elements({**obj, "norad_id": 0}),
                            ev["time_of_closest_approach"], 120.0)
    assert ref and abs(ref["miss_km"] - float(ev["miss_distance_km"])) < 0.002
    after = db.query_one("admin", "SELECT (SELECT COUNT(*) FROM space_object) so, (SELECT COUNT(*) FROM conjunction_event) ce")
    assert after == before                                     # nothing entered the catalogue or the real events
    # (real satellites such as SYNCOM also start with "SYN": look for the synthetic naming and designations)
    assert db.query_one("viewer", "SELECT COUNT(*) AS n FROM space_object WHERE name LIKE 'SYNTHETIC%%' "
                                  "OR intl_designator LIKE 'SYN-%%'")["n"] == 0


def test_ops02_demo_api_roles_and_reset(env):
    anon = env["app"].test_client()
    viewer = login(env, "viewer")
    analyst = login(env, "analyst")
    assert anon.get("/api/demo").status_code == 200                         # anyone can see (labelled) demos
    assert anon.post("/api/demo", json={"target_norad": env["target"]}, headers=H).status_code == 401
    assert viewer.post("/api/demo", json={"target_norad": env["target"]}, headers=H).status_code == 403
    assert analyst.post("/api/demo", json={"target_norad": 1}, headers=H).status_code == 400   # not in orbit
    r = analyst.post("/api/demo", json={"target_norad": env["target"], "miss_km": 0.3}, headers=H)
    assert r.status_code == 201
    sid = r.json["scenario_id"]
    env["scenarios"].append(sid)
    assert analyst.get(f"/api/demo/events/{r.json['events'][0]['demo_event_id']}/track").json["synthetic"] is True
    pos = anon.get("/api/demo/positions?span=60").json["items"]
    assert any(p["synthetic"] and p["designation"].startswith("SYN-") for p in pos)
    assert analyst.delete(f"/api/demo/{sid}", headers=H).json["cleared"] == 1
    assert env["db"].query_one("viewer", "SELECT COUNT(*) AS n FROM demo_object WHERE scenario_id = %s", (sid,))["n"] == 0


# ----------------------------------------------------------------------------- agent guardrail + approval
def test_ops03_no_feasible_is_only_accepted_after_every_candidate_is_checked(env):
    from orbitwatch.agent.orchestrator import DecisionAgent
    db = env["db"]
    de = db.query_one("viewer", "SELECT demo_event_id FROM demo_event WHERE scenario_id = %s", (env["scenarios"][0],))
    _, aid = db.execute("analyst", "INSERT INTO agent_assessment (demo_event_id, engine) VALUES (%s, 'test')",
                        (de["demo_event_id"],))
    agent = DecisionAgent(aid, de["demo_event_id"], "analyst", synthetic=True)
    for name in ("get_conjunction", "get_space_weather", "compute_collision_probability", "assess_risk"):
        agent.call_tool(name, {})
    gen = agent.call_tool("generate_maneuver_candidates", {"count": 3})
    first = gen["candidates"][0]["candidate_id"]
    agent.call_tool("evaluate_maneuver_constraints", {"candidate_id": first})       # the LLM stops after one
    out = agent.guard({"decision": "NO_FEASIBLE_MANEUVER", "selected_candidate_id": None, "explanation": "x"})
    assert set(agent.tools.evaluations) == set(agent.tools.candidates)                # all were checked
    feasible = [c for c, e in agent.tools.evaluations.items() if e["feasible"]]
    if feasible:
        assert out["decision"] == "MANEUVER_RECOMMENDED" and out["selected_candidate_id"] in feasible
    else:
        assert out["decision"] == "NO_FEASIBLE_MANEUVER"


def _recommended(env, de_id, **kw):
    from orbitwatch.agent import orchestrator
    from orbitwatch import decisions
    aid = orchestrator.start(None, env["ids"]["analyst"], "analyst", background=False, demo_event_id=de_id, **kw)
    return decisions.load("analyst", aid)


def test_ops04_approve_reject_replan_and_simulation(env, monkeypatch):
    from orbitwatch import decisions
    from orbitwatch import demo
    monkeypatch.setenv("GROQ_API_KEY", "")                                          # deterministic workflow
    # A burn is feasible only if a ground-station pass allows an uplink before it: try a few encounter times.
    a = de = None
    for lead_h in (6, 9, 12, 16, 20):
        sc = demo.create(env["target"], env["ids"]["analyst"], "analyst", lead_min=lead_h * 60, miss_km=0.2)
        env["scenarios"].append(sc["scenario_id"])
        de = sc["events"][0]["demo_event_id"]
        a = _recommended(env, de)
        if a["decision"] == "MANEUVER_RECOMMENDED":
            break
    assert a["decision"] == "MANEUVER_RECOMMENDED", f"no feasible burn at any lead time ({a['decision']})"
    analyst = login(env, "analyst")
    viewer = login(env, "viewer")
    aid = a["assessment_id"]
    sim = analyst.get(f"/api/assessments/{aid}/simulation").json
    assert sim["simulated"] is True and abs(sim["miss_after_km"] - a["maneuver"]["miss_after_km"]) < 1e-3
    assert sim["miss_after_km"] > sim["miss_before_km"]
    assert len(sim["primary_before_ecef_km"]) == len(sim["primary_after_ecef_km"]) == len(sim["offsets_s"])
    url = f"/api/assessments/{aid}/decision"
    assert viewer.post(url, json={"action": "approve", "confirm": True}, headers=H).status_code == 403
    assert analyst.post(url, json={"action": "approve"}, headers=H).status_code == 400          # no confirmation
    r = analyst.post(url, json={"action": "approve", "confirm": True, "reason": "ops test"}, headers=H)
    assert r.status_code == 201 and r.json["status"] == "APPROVED"
    assert analyst.post(url, json={"action": "approve", "confirm": True}, headers=H).status_code == 409
    rec = env["db"].query_one("analyst", "SELECT status, execution, decided_by_name FROM v_maneuver_decision "
                                         "WHERE assessment_id = %s", (aid,))
    assert rec["status"] == "APPROVED" and rec["execution"] == "SIMULATED" and rec["decided_by_name"] == "Ops analyst"
    # a reviewer's name is not public
    assert "decided_by_name" not in (viewer.get(f"/api/assessments/{aid}").json["decision_record"] or {})

    b = _recommended(env, de)
    url = f"/api/assessments/{b['assessment_id']}/decision"
    assert analyst.post(url, json={"action": "reject", "reason": ""}, headers=H).status_code == 400
    want = round(max(5.0, (b["maneuver"] or {}).get("miss_after_km", 1) * 1.5), 1)
    r = analyst.post(url, json={"action": "reject", "reason": "need more margin", "replan": True, "min_miss_km": want},
                     headers=H)
    assert r.status_code == 201 and r.json["replan_assessment_id"]
    child = r.json["replan_assessment_id"]
    for _ in range(240):
        c = decisions.load("analyst", child)
        if c["status"] != "running":
            break
        time.sleep(0.5)
    assert c["parent_assessment_id"] == b["assessment_id"] and c["min_miss_km"] == want and c["feedback"] == "need more margin"
    if c["decision"] == "MANEUVER_RECOMMENDED":
        assert c["maneuver"]["miss_after_km"] >= want * 0.999                        # the reviewer's constraint holds


# ----------------------------------------------------------------------------- accounts
def _outbox_token(env, email):
    row = env["db"].query_one("admin", "SELECT body_text FROM email_outbox WHERE to_address = %s AND kind = 'password_reset' "
                                       "ORDER BY email_id DESC LIMIT 1", (email,))
    m = re.search(r"#/reset\?token=([A-Za-z0-9_\-]+)", row["body_text"] if row else "")
    return m.group(1) if m else None


def test_ops05_change_password_signs_out_other_sessions(env):
    a = login(env, "viewer")
    b = login(env, "viewer")
    assert a.post("/api/auth/password", json={"current_password": "wrong-one", "new_password": "x" * 10}, headers=H).status_code == 400
    new = secrets.token_urlsafe(12)
    r = a.post("/api/auth/password", json={"current_password": env["pw"], "new_password": new}, headers=H)
    assert r.status_code == 200
    assert a.get("/api/auth/me").json["user"]["email"] == env["emails"]["viewer"]    # this session stays
    assert b.get("/api/auth/me").json["user"] is None                                 # the other one ended
    assert env["db"].query_one("admin", "SELECT COUNT(*) AS n FROM email_outbox WHERE to_address = %s AND kind = "
                                        "'password_changed'", (env["emails"]["viewer"],))["n"] >= 1
    a.post("/api/auth/password", json={"current_password": new, "new_password": env["pw"]}, headers=H)


def test_ops06_forgot_and_reset_password(env):
    anon = env["app"].test_client()
    email = env["emails"]["viewer"]
    same = anon.post("/api/auth/forgot", json={"email": "nobody-here@example.test"}, headers=H).json["message"]
    assert anon.post("/api/auth/forgot", json={"email": email}, headers=H).json["message"] == same   # no account oracle
    token = _outbox_token(env, email)
    assert token and len(token) >= 40
    stored = env["db"].query_one("admin", "SELECT token_hash FROM password_reset_token WHERE user_id = %s AND used_at IS NULL",
                                 (env["ids"]["viewer"],))
    assert stored and stored["token_hash"] != token and len(stored["token_hash"]) == 64          # only a hash is stored
    old_session = login(env, "viewer")
    new = secrets.token_urlsafe(12)
    assert anon.post("/api/auth/reset", json={"token": token, "new_password": "short"}, headers=H).status_code == 400
    assert anon.post("/api/auth/reset", json={"token": "x" * 43, "new_password": new}, headers=H).status_code == 400
    assert anon.post("/api/auth/reset", json={"token": token, "new_password": new}, headers=H).status_code == 200
    assert anon.post("/api/auth/reset", json={"token": token, "new_password": new}, headers=H).status_code == 400   # single use
    assert old_session.get("/api/auth/me").json["user"] is None
    login(env, "viewer", new)
    # put the original password back via another reset (expired tokens are rejected too)
    anon.post("/api/auth/forgot", json={"email": email}, headers=H)
    tok2 = _outbox_token(env, email)
    env["db"].execute("admin", "UPDATE password_reset_token SET created_at = NOW(3) - INTERVAL 1 HOUR, "
                               "expires_at = NOW(3) - INTERVAL 1 SECOND WHERE user_id = %s AND used_at IS NULL",
                      (env["ids"]["viewer"],))
    assert anon.post("/api/auth/reset", json={"token": tok2, "new_password": env["pw"]}, headers=H).status_code == 400
    env["db"].execute("admin", "UPDATE app_user SET password_hash = %s WHERE user_id = %s",
                      (__import__("orbitwatch.auth", fromlist=["x"]).hash_password(env["pw"]), env["ids"]["viewer"]))


def _clear_forgot_ip():
    from orbitwatch import ratelimit
    ratelimit.clear("forgot_ip", "127.0.0.1")


def test_ops07_forgot_password_is_rate_limited(env):
    _clear_forgot_ip()
    anon = env["app"].test_client()
    email = env["emails"]["analyst"]
    codes = [anon.post("/api/auth/forgot", json={"email": email}, headers=H).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    issued = env["db"].query_one("admin", "SELECT COUNT(*) AS n FROM password_reset_token WHERE user_id = %s "
                                          "AND created_at > NOW(3) - INTERVAL 1 HOUR", (env["ids"]["analyst"],))["n"]
    assert issued <= 3


def test_ops08_notification_preferences(env):
    v = login(env, "viewer")
    assert v.get("/api/me/notifications").json["prefs"] == {"email_alerts": True, "min_risk_level": "HIGH", "email_decisions": False}
    assert v.put("/api/me/notifications", json={"min_risk_level": "EXTREME"}, headers=H).status_code == 400
    assert v.put("/api/me/notifications", json={"email_alerts": "yes"}, headers=H).status_code == 400
    assert v.put("/api/me/notifications", json={"email_alerts": False, "min_risk_level": "CRITICAL"}, headers=H).status_code == 200
    assert v.get("/api/me/notifications").json["prefs"]["min_risk_level"] == "CRITICAL"


# ----------------------------------------------------------------------------- e-mail delivery
def test_ops09_email_is_delivered_with_retries_and_reset_bodies_scrubbed(env, monkeypatch):
    from aiosmtpd.controller import Controller
    from orbitwatch import config, notify

    class Sink:
        def __init__(self):
            self.messages = []

        async def handle_DATA(self, server, session, envelope):
            self.messages.append(envelope)
            return "250 OK"

    def free_port():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port_ = sock.getsockname()[1]
        sock.close()
        return port_

    db = env["db"]
    email, other = env["emails"]["analyst"], env["emails"]["viewer"]
    mine = [email, other]
    monkeypatch.setattr(notify, "dispatch_soon", lambda: None)      # deliver only when this test says so
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SMTP_SECURITY", "none")
    monkeypatch.setattr(config, "SMTP_USERNAME", "")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "")
    # 1. the mail server is down: the message is kept and retried later, not lost
    monkeypatch.setattr(config, "SMTP_PORT", free_port())                 # nothing listens there
    notify.enqueue("test", email, "ops test 1", "hello", user_id=env["ids"]["analyst"])
    with pytest.raises(RuntimeError):
        notify.dispatch(only_to=mine)
    row = db.query_one("admin", "SELECT status, attempts, last_error, next_attempt_at > NOW(3) AS later FROM email_outbox "
                                "WHERE to_address = %s ORDER BY email_id DESC LIMIT 1", (email,))
    assert row["status"] == "queued" and row["attempts"] == 1 and row["last_error"] and row["later"]
    # 2. the server is up: the retry and a password-reset message are delivered
    sink = Sink()
    port = free_port()
    ctl = Controller(sink, hostname="127.0.0.1", port=port, ready_timeout=30)    # the default 5 s fails on a busy machine
    ctl.start()
    monkeypatch.setattr(config, "SMTP_PORT", port)
    try:
        db.execute("admin", "UPDATE email_outbox SET next_attempt_at = NOW(3) WHERE to_address = %s AND status = 'queued'", (email,))
        from orbitwatch import ratelimit
        ratelimit.clear("forgot_ip", "127.0.0.1")
        ratelimit.clear("forgot_email", other)
        db.execute("admin", "DELETE FROM password_reset_token WHERE user_id = %s", (env["ids"]["viewer"],))
        assert env["app"].test_client().post("/api/auth/forgot", json={"email": other}, headers=H).status_code == 200
        sent, _ = notify.dispatch(only_to=mine)
        subjects = [re.search(rb"Subject: ([^\r\n]+)", m.content).group(1).decode() for m in sink.messages]
        assert sent >= 2 and "ops test 1" in subjects and any("Reset your password" in x for x in subjects), subjects
        test_row = db.query_one("admin", "SELECT status FROM email_outbox WHERE to_address = %s AND kind = 'test' "
                                       "ORDER BY email_id DESC LIMIT 1", (email,))
        reset = db.query_one("admin", "SELECT status, body_text FROM email_outbox WHERE to_address = %s "
                                    "AND kind = 'password_reset' ORDER BY email_id DESC LIMIT 1", (other,))
        assert test_row["status"] == "sent" and reset["status"] == "sent"
        assert "token=" not in reset["body_text"]                              # the link is not kept after sending
    finally:
        ctl.stop()


# ----------------------------------------------------------------------------- event log, live stream, status
def test_ops10_event_log_visibility_and_live_stream(env):
    from orbitwatch import events
    pub = events.record("system", "ops_public", f"ops public {secrets.token_hex(3)}", visibility="public")
    adm = events.record("system", "ops_admin", "ops admin-only", visibility="admin")
    anon = env["app"].test_client()
    analyst = login(env, "analyst")
    ids_anon = {r["log_id"] for r in anon.get(f"/api/events?after={pub - 1}&limit=50").json["items"]}
    ids_an = {r["log_id"] for r in analyst.get(f"/api/events?after={pub - 1}&limit=50").json["items"]}
    assert pub in ids_anon and adm not in ids_anon and adm not in ids_an
    assert all("actor_user_id" not in r and "detail" not in r for r in anon.get("/api/events?limit=5").json["items"])
    r = anon.get(f"/api/stream?after={pub - 1}", buffered=False)
    assert r.mimetype == "text/event-stream"
    chunks = ""
    for part in r.response:
        chunks += part.decode()
        if f"id: {pub}" in chunks:
            break
    r.close()
    assert "event: hello" in chunks and f"id: {pub}" in chunks and "ops_admin" not in chunks


def test_ops11_provenance_and_system_status(env):
    anon = env["app"].test_client()
    st = anon.get("/api/system/status").json
    cov = st["coverage"]
    db = env["db"]
    assert cov["objects_in_orbit"] == db.query_one("viewer", "SELECT COUNT(*) AS n FROM space_object WHERE in_earth_orbit")["n"]
    assert cov["with_orbit"] == db.query_one("viewer", "SELECT COUNT(*) AS n FROM current_orbit co JOIN space_object so "
                                                       "USING (norad_id) WHERE so.in_earth_orbit")["n"]
    assert {s["source_key"] for s in st["sources"]} >= {"celestrak_gp", "celestrak_satcat", "spacetrack_gp", "noaa_swpc"}
    assert "email_queue" not in st                                                     # admin-only detail
    hb = st["scheduler"]["heartbeat"]
    if hb:
        assert re.match(r"\d{4}-\d\d-\d\dT", hb["heartbeat_at"])                     # ISO 8601, also when nested


def test_ops12_reentry_gate_is_honest(env):
    from orbitwatch.jobs import reentry
    from orbitwatch.jobs.runner import SkipJob
    st = reentry.status()
    if st["active_model"] is None:
        with pytest.raises(SkipJob):
            reentry.predict(None)
        assert env["db"].query_one("viewer", "SELECT COUNT(*) AS n FROM reentry_prediction")["n"] == 0
    else:
        bad = env["db"].query_one("viewer", "SELECT COUNT(*) AS n FROM reentry_prediction p LEFT JOIN ml_model m "
                                            "USING (model_version) WHERE m.status IS NULL OR m.status <> 'active'")["n"]
        assert bad == 0


def test_ops13_spacetrack_without_credentials_is_skipped_not_faked(env, monkeypatch):
    from orbitwatch.jobs import spacetrack
    from orbitwatch.jobs.runner import SkipJob
    monkeypatch.delenv("SPACETRACK_USERNAME", raising=False)
    monkeypatch.delenv("SPACETRACK_USER", raising=False)
    monkeypatch.delenv("SPACETRACK_PASSWORD", raising=False)
    assert not spacetrack.configured()
    with pytest.raises(SkipJob):
        spacetrack.run(None, mode="watchlist")


def test_ops14_reconciliation_is_idempotent(env):
    from orbitwatch import reconcile
    rep, plan = reconcile.analyse(include_git=False)
    assert not plan["test"]["events"] and not plan["real_conj"] and not plan["stale_current"] and not plan["dup_events"]
    assert all(reconcile.is_test_email(e) for e in env["emails"].values())


def test_ops15_tle_parser_matches_sgp4():
    from sgp4.api import Satrec, jday
    from orbitwatch import orbital
    l1 = "1 25544U 98067A   26278.51782528  .00016717  00000-0  30238-3 0  9993"
    l2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50034532 30001"
    try:
        el = orbital.parse_tle(l1, l2, "ISS")
    except ValueError:
        # build a valid checksum for the synthetic test line pair
        fix = lambda s: s[:68] + str((sum(int(c) for c in s[:68] if c.isdigit()) + s[:68].count("-")) % 10)
        l1, l2 = fix(l1), fix(l2)
        el = orbital.parse_tle(l1, l2, "ISS")
    assert el["norad_id"] == 25544 and el["intl_designator"] == "1998-067A"
    ref = Satrec.twoline2rv(l1, l2)
    jd, fr = jday(2026, 10, 6, 0, 0, 0)
    _, r1, _ = ref.sgp4(jd, fr)
    _, r2, _ = orbital.satrec_from_elements(el).sgp4(jd, fr)
    assert max(abs(a - b) for a, b in zip(r1, r2)) < 1e-3
    assert el["epoch"] > datetime(2026, 10, 4)
