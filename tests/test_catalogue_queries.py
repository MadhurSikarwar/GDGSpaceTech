"""The catalogue search and the memoised dashboard aggregates.

The search used to read the whole five-join v_object_catalog view for every request (0.6 s a page). It now finds the
page's ids on space_object and reads the view for just those ids, so these tests hold it to exactly what plain SQL over
the view returns. The memo keeps the heavy aggregates for a few seconds and must never outlive a write.
"""
import secrets
import threading
import time

import pytest

H = {"X-Requested-With": "OrbitWatch"}

# sort key -> the same ORDER BY written against v_object_catalog (ties end in the primary key, as in the endpoint)
VIEW_ORDER = {"norad": "norad_id", "name": "name, norad_id", "launch": "launch_date DESC, norad_id",
              "perigee": "perigee_km, norad_id", "type": "object_type, name, norad_id"}

# (query string, the same filters as plain conditions on the view's columns, their parameters, sort key)
CASES = [
    ("", ["in_earth_orbit"], [], "norad"),
    ("q=ISS", ["name LIKE %s", "in_earth_orbit"], ["%ISS%"], "norad"),
    ("q=25544", ["(norad_id = %s OR name LIKE %s)", "in_earth_orbit"], [25544, "%25544%"], "norad"),
    ("q=1998-067&status=all", ["intl_designator LIKE %s"], ["1998-067%"], "norad"),
    ("type=Debris&sort=name", ["object_type = %s", "in_earth_orbit"], ["Debris"], "name"),
    ("country=IN&status=all&sort=launch", ["country_code = %s"], ["IN"], "launch"),
    ("org=ISRO&status=all", ["org_id = %s"], ["ISRO"], "norad"),
    ("status=decayed&sort=type", ["decay_date IS NOT NULL"], [], "type"),
    ("status=beyond", ["decay_date IS NULL AND NOT in_earth_orbit"], [], "norad"),
    ("has_orbit=1&sort=perigee", ["in_earth_orbit", "epoch IS NOT NULL"], [], "perigee"),
    ("country=IN&type=Payload&has_orbit=1&sort=name", ["country_code = %s", "object_type = %s", "in_earth_orbit",
                                                       "epoch IS NOT NULL"], ["IN", "Payload"], "name"),
    ("q=zzzzqqqq", ["name LIKE %s", "in_earth_orbit"], ["%zzzzqqqq%"], "norad"),
]


@pytest.fixture(scope="module")
def site():
    from orbitwatch import db
    try:
        db.query("viewer", "SELECT 1 AS x")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no OrbitWatch database reachable: {exc}")
    from orbitwatch.web import create_app
    app = create_app(https=False)
    app.testing = True
    return {"app": app, "db": db}


def oracle(db, where, params, sort, size, page):
    w = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.query_one("viewer", f"SELECT COUNT(*) AS n FROM v_object_catalog{w}", params)["n"]
    ids = [r["norad_id"] for r in db.query(
        "viewer", f"SELECT norad_id FROM v_object_catalog{w} ORDER BY {VIEW_ORDER[sort]} LIMIT %s OFFSET %s",
        params + [size, (page - 1) * size])]
    return total, ids


@pytest.mark.parametrize("query,where,params,sort", CASES, ids=[c[0] or "default" for c in CASES])
def test_cq01_search_returns_what_the_view_returns(site, query, where, params, sort):
    c = site["app"].test_client()
    for page in (1, 2):
        got = c.get(f"/api/objects?{query}&page={page}&page_size=20").json
        total, ids = oracle(site["db"], where, params, sort, 20, page)
        assert got["total"] == total, (query, page)
        assert [i["norad_id"] for i in got["items"]] == ids, (query, page)


def test_cq02_region_filter_matches_the_view(site):
    c = site["app"].test_client()
    for region in c.get("/api/lookups").json["regions"][:3]:
        rid = region["region_id"]
        got = c.get(f"/api/objects?region={rid}&sort=perigee&page_size=20").json
        total, ids = oracle(site["db"], ["region_id = %s", "in_earth_orbit"], [rid], "perigee", 20, 1)
        assert got["total"] == total and [i["norad_id"] for i in got["items"]] == ids, rid
        assert all(i["region_id"] == rid for i in got["items"])


def test_cq03_totals_are_right_on_a_short_last_page_and_past_the_end(site):
    c = site["app"].test_client()
    total, _ = oracle(site["db"], ["country_code = %s"], ["IN"], "norad", 1, 1)
    assert total > 500                                          # so that page 2 of 500 is a short page
    page1 = c.get("/api/objects?country=IN&status=all&page_size=500").json
    page2 = c.get("/api/objects?country=IN&status=all&page_size=500&page=2").json
    page9 = c.get("/api/objects?country=IN&status=all&page_size=500&page=9").json
    assert page1["total"] == page2["total"] == page9["total"] == total
    assert len(page1["items"]) == 500 and len(page2["items"]) == total - 500 and page9["items"] == []
    assert not {i["norad_id"] for i in page1["items"]} & {i["norad_id"] for i in page2["items"]}


def test_cq04_rows_keep_the_columns_of_the_view(site):
    c = site["app"].test_client()
    item = c.get("/api/objects?q=25544").json["items"][0]
    cols = [r["Field"] for r in site["db"].query("admin", "SHOW COLUMNS FROM v_object_catalog")]
    assert set(item) == set(cols)


@pytest.fixture(scope="module")
def analyst(site):
    from orbitwatch import auth
    email, pw = f"cq-{secrets.token_hex(4)}@example.test", secrets.token_urlsafe(12)
    auth.create_user("CQ analyst", email, pw, "analyst")
    c = site["app"].test_client()
    assert c.post("/api/auth/login", json={"email": email, "password": pw}, headers=H).status_code == 200
    yield c
    site["db"].execute("admin", "DELETE FROM app_user WHERE email = %s", (email,))


def test_cq05_csv_export_lists_the_same_objects_in_the_same_order(site, analyst):
    import csv
    import io
    r = analyst.get("/api/objects?format=csv&country=IN&type=Payload&status=all&sort=launch")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    rows = list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
    total, ids = oracle(site["db"], ["country_code = %s", "object_type = %s"], ["IN", "Payload"], "launch", 50000, 1)
    assert total > 50 and [int(x["norad_id"]) for x in rows] == ids


# ----------------------------------------------------------------------------- the memo
@pytest.fixture
def memo(monkeypatch):
    from orbitwatch import config
    from orbitwatch.web import common
    monkeypatch.setattr(config, "READ_CACHE_S", 30.0)
    common.memo_clear()
    yield common
    common.memo_clear()


def test_cq10_a_result_is_reused_until_it_expires(memo, monkeypatch):
    from orbitwatch import config
    monkeypatch.setattr(config, "READ_CACHE_S", 0.2)
    calls = []

    def build():
        calls.append(1)
        return len(calls)

    assert memo.memo("k", build) == 1 and memo.memo("k", build) == 1 and len(calls) == 1
    assert memo.memo("other", build) == 2                       # one entry per key
    time.sleep(0.3)
    assert memo.memo("k", build) == 3                           # expired
    assert memo.memo("k", build, factor=100) == 3               # a longer lifetime for rarely changing data


def test_cq11_zero_turns_the_memo_off(memo, monkeypatch):
    from orbitwatch import config
    monkeypatch.setattr(config, "READ_CACHE_S", 0)
    calls = []
    for _ in range(3):
        memo.memo("k", lambda: calls.append(1))
    assert len(calls) == 3


def test_cq12_simultaneous_requests_share_one_build(memo):
    calls, out = [], []

    def build():
        calls.append(1)
        time.sleep(0.2)
        return "value"

    threads = [threading.Thread(target=lambda: out.append(memo.memo("k", build))) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1 and out == ["value"] * 8


def test_cq13_a_result_built_before_a_write_is_not_kept(memo):
    started, release, out = threading.Event(), threading.Event(), []

    def slow():
        started.set()
        release.wait(5)
        return "before the write"

    t = threading.Thread(target=lambda: out.append(memo.memo("k", slow)))
    t.start()
    assert started.wait(5)
    memo.memo_clear()                                           # a write lands while the build is still running
    release.set()
    t.join()
    assert out == ["before the write"]                          # that request still gets its answer
    assert memo.memo("k", lambda: "after the write") == "after the write"       # but nobody else does


def test_cq14_only_a_successful_write_clears_the_memo(memo):
    from orbitwatch.web import create_app
    app = create_app(https=False)
    app.testing = True
    app.add_url_rule("/api/_probe_get", "probe_get", lambda: ("", 200), methods=["GET"])
    app.add_url_rule("/api/_probe_ok", "probe_ok", lambda: ("", 204), methods=["POST"])
    app.add_url_rule("/api/_probe_bad", "probe_bad", lambda: ("", 400), methods=["POST"])
    c = app.test_client()
    memo.memo("probe", lambda: 1)
    assert c.get("/api/_probe_get").status_code == 200 and "probe" in memo._memo
    assert c.post("/api/_probe_bad", headers=H).status_code == 400 and "probe" in memo._memo
    assert c.post("/api/_probe_ok", headers=H).status_code == 204 and "probe" not in memo._memo


def test_cq15_memoised_endpoints_answer_from_memory_with_the_same_content(site, memo, monkeypatch):
    from orbitwatch import config, db
    c = site["app"].test_client()
    monkeypatch.setattr(config, "READ_CACHE_S", 0)
    fresh = {p: c.get(p).json for p in ("/api/stats", "/api/lookups", "/api/provenance")}
    monkeypatch.setattr(config, "READ_CACHE_S", 30.0)
    first = {p: c.get(p).json for p in fresh}

    def no_database(*args, **kwargs):
        raise AssertionError("served from the database, not from the memo")

    real_query = db.query
    monkeypatch.setattr(db, "query", no_database)
    monkeypatch.setattr(db, "query_one", no_database)
    assert c.get("/api/stats").status_code == 200 and c.get("/api/lookups").status_code == 200
    monkeypatch.setattr(db, "query", real_query)                # the source list is a 3 ms query and is not memoised,
    assert c.get("/api/provenance").status_code == 200          # the coverage scan beside it is (it uses query_one)
    assert first["/api/stats"] == fresh["/api/stats"] and first["/api/lookups"] == fresh["/api/lookups"]
    assert first["/api/provenance"]["coverage"] == fresh["/api/provenance"]["coverage"]


def test_cq06_relevance_sort_puts_names_that_start_with_the_term_first(site):
    c = site["app"].test_client()
    got = c.get("/api/objects?q=ISS&status=all&sort=relevance&page_size=500").json
    starts = [i["name"].upper().startswith("ISS") for i in got["items"]]
    assert starts[0] and starts == sorted(starts, reverse=True)       # names that start with the term come before names that contain it
    in_orbit = [i["in_earth_orbit"] for i in got["items"] if i["name"].upper().startswith("ISS")]
    assert in_orbit == sorted(in_orbit, reverse=True)                  # and among those, objects still in orbit come first
    plain = c.get("/api/objects?q=ISS&status=all&page_size=500").json
    assert got["total"] == plain["total"] and {i["norad_id"] for i in got["items"]} == {i["norad_id"] for i in plain["items"]}


def test_cq07_upcoming_lists_every_event_of_the_window_in_one_request(site):
    c = site["app"].test_client()
    got = c.get("/api/conjunctions/upcoming?hours=72").json
    items = got["items"]
    direct = site["db"].query_one("viewer", """
        SELECT COUNT(*) AS n FROM v_conjunction_detail WHERE origin = 'orbitwatch' AND risk_level IN ('CRITICAL', 'HIGH', 'MEDIUM')
           AND time_of_closest_approach BETWEEN UTC_TIMESTAMP() AND UTC_TIMESTAMP() + INTERVAL 72 HOUR""")["n"]
    assert abs(len(items) - direct) <= 5                       # a few events may pass their TCA between the two queries
    assert [i["tca"] for i in items] == sorted(i["tca"] for i in items)
    assert all(i["risk"] in ("CRITICAL", "HIGH", "MEDIUM") and i["miss_km"] >= 0 for i in items)
    if items:
        first = items[0]
        assert set(first) == {"event_id", "tca", "risk", "miss_km", "pc", "primary", "secondary"}
        assert set(first["primary"]) == {"norad_id", "name"} and first["tca"].endswith("Z")
    high = c.get("/api/conjunctions/upcoming?hours=72&risk=CRITICAL").json["items"]
    assert all(i["risk"] == "CRITICAL" for i in high) and len(high) <= len(items)
    assert c.get("/api/conjunctions/upcoming?hours=abc").status_code == 400


def test_cq08_type_facets_follow_every_other_filter_but_not_the_type(site):
    c = site["app"].test_client()
    plain = c.get("/api/objects?country=IN&page_size=1").json
    assert "facets" not in plain                                   # only when asked for
    got = c.get("/api/objects?country=IN&type=Debris&facets=type&page_size=1").json
    facets = got["facets"]["type"]
    rows = site["db"].query("viewer", """
        SELECT object_type AS t, COUNT(*) AS n FROM v_object_catalog WHERE country_code = 'IN' AND in_earth_orbit GROUP BY object_type""")
    assert facets == {r["t"]: r["n"] for r in rows}                # the type filter itself does not narrow the counts
    assert sum(facets.values()) == plain["total"] and got["total"] == facets["Debris"]


def test_cq09_risk_facets_ignore_the_risk_filter_and_a_window_ahead_reads_forwards(site):
    from datetime import datetime, timedelta, timezone
    c = site["app"].test_client()
    both = c.get("/api/conjunctions?risk=HIGH,CRITICAL&facets=risk&page_size=1").json
    every = c.get("/api/conjunctions?page_size=1").json
    assert "facets" not in every                                         # only when asked for
    assert sum(both["facets"]["risk"].values()) == every["total"]        # counts for every risk level, whatever is chosen
    assert both["total"] == both["facets"]["risk"].get("HIGH", 0) + both["facets"]["risk"].get("CRITICAL", 0)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window = f"from={now.isoformat()}Z&to={(now + timedelta(hours=24)).isoformat()}Z&when=upcoming&page_size=200"
    items = c.get(f"/api/conjunctions?{window}").json["items"]
    times = [i["time_of_closest_approach"] for i in items]
    assert items and times == sorted(times)                              # soonest first


def test_cq16_the_facet_routes_give_the_counts_the_parameter_gives(site):
    c = site["app"].test_client()
    for query in ("country=IN", "q=STARLINK", "status=all", "has_orbit=1"):
        beside_rows = c.get(f"/api/objects?{query}&type=Debris&facets=type&page_size=1").json["facets"]
        assert c.get(f"/api/objects/facets?{query}&type=Debris").json == beside_rows        # the type filter does not narrow them
    for query in ("risk=HIGH", "when=all", "q=STARLINK"):
        beside_rows = c.get(f"/api/conjunctions?{query}&facets=risk&page_size=1").json["facets"]
        assert c.get(f"/api/conjunctions/facets?{query}").json == beside_rows
    assert c.get("/api/objects/facets?norad=abc").status_code == 400                        # a bad filter is refused as in the list


def test_cq17_the_facet_routes_are_kept_in_the_memo(site, memo, monkeypatch):
    from orbitwatch import db
    c = site["app"].test_client()
    objects = c.get("/api/objects/facets?country=IN").json
    events = c.get("/api/conjunctions/facets?when=all&from=2026-10-07T10:15:11.123Z").json

    def no_database(*args, **kwargs):
        raise AssertionError("served from the database, not from the memo")

    monkeypatch.setattr(db, "query", no_database)
    assert c.get("/api/objects/facets?country=IN").json == objects
    assert c.get("/api/conjunctions/facets?when=all&from=2026-10-07T10:15:59.999Z").json == events      # the same minute is the same key


def test_cq18_the_memo_has_a_ceiling_and_frees_what_has_expired(memo, monkeypatch):
    from orbitwatch import config
    monkeypatch.setattr(config, "READ_CACHE_S", 0.2)
    monkeypatch.setattr(memo, "_MEMO_MAX", 10)
    for i in range(60):
        memo.memo(("search", i), lambda: "result")             # keys that come from request arguments: a search term, a date
    assert len(memo._memo) == 10 and len(memo._memo_gates) <= 21        # the first ten stay; the rest were answered but not kept
    time.sleep(0.3)
    assert memo.memo("new", lambda: "kept") == "kept"
    assert list(memo._memo) == ["new"]                                  # what had expired made room
