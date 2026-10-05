"""/api/visual: positions for the CesiumJS globe (SRS 3.8).

SGP4 records for every object with a current orbit are built once and
reused until Current_Orbit changes (checked at most once a minute). One
request propagates the whole catalogue in a single vectorised SGP4 call.
"""
import threading
import time

import numpy as np
from flask import Blueprint, abort, request

from orbitwatch import db, orbital
from orbitwatch.web.common import account, ok, parse_dt

bp = Blueprint("visual_api", __name__, url_prefix="/api/visual")

TYPE_CODES = {"Payload": 0, "Rocket Body": 1, "Debris": 2, "Unknown": 3}
_cache = {"key": None, "checked": 0.0, "ids": None, "types": None, "sats": None, "names": None}
_lock = threading.Lock()


def _catalog(acct):
    with _lock:
        if time.time() - _cache["checked"] > 60 or _cache["sats"] is None:
            key = tuple(db.query_one(acct, "SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM current_orbit").values())
            _cache["checked"] = time.time()
            if key != _cache["key"]:
                rows = db.query(acct, f"""
                    SELECT co.norad_id, co.epoch, {', '.join('co.' + f for f in orbital.ELEMENT_FIELDS)},
                           so.object_type, so.name
                      FROM current_orbit co JOIN space_object so ON so.norad_id = co.norad_id
                     ORDER BY co.norad_id""")
                _cache.update(key=key, ids=np.array([r["norad_id"] for r in rows]),
                              types=np.array([TYPE_CODES[r["object_type"]] for r in rows]),
                              names=[r["name"] for r in rows],
                              sats=[orbital.satrec_from_elements(r) for r in rows])
        return _cache["ids"], _cache["types"], _cache["sats"], _cache["names"]


@bp.get("/positions")
def positions():
    """Earth-fixed positions (km) of every object at time t (default: now).

    With span=S the positions at t + S seconds come back too (pos2), so the
    globe can interpolate smooth motion between the two instead of jumping.
    """
    t = parse_dt(request.args.get("t"), "t") or orbital.now_utc()
    span = request.args.get("span", type=float)
    span = min(max(span, 1.0), 600.0) if span else None
    ids, types, sats, names = _catalog(account())
    if not sats:
        return ok({"t": t.isoformat() + "Z", "ids": [], "types": [], "pos": []})
    jd, fr = orbital.time_grid(t, [0.0, span] if span else [0.0])
    err, r, _ = orbital.propagate(sats, jd, fr)
    ecef = orbital.teme_to_ecef(r[:, 0, :], jd[0], fr[0])
    good = (err[:, 0] == 0) & np.isfinite(ecef).all(axis=1)
    out = {"t": t.isoformat() + "Z", "ids": ids[good].tolist(), "types": types[good].tolist()}
    if span:
        ecef2 = orbital.teme_to_ecef(r[:, 1, :], jd[1], fr[1])
        good &= (err[:, 1] == 0) & np.isfinite(ecef2).all(axis=1)
        out.update(ids=ids[good].tolist(), types=types[good].tolist(), span=span,
                   pos2=np.round(ecef2[good], 1).ravel().tolist())
    out["pos"] = np.round(ecef[good], 1).ravel().tolist()
    return ok(out)


@bp.get("/names")
def names():
    ids, _, _, nm = _catalog(account())
    return ok({"ids": ids.tolist() if ids is not None else [], "names": nm or []})


@bp.get("/orbit/<int:norad_id>")
def orbit(norad_id):
    """Orbital periods from t (default one, up to three), 240 samples each: Earth-fixed path (km)."""
    acct = account()
    el = db.query_one(acct, f"SELECT norad_id, epoch, {', '.join(orbital.ELEMENT_FIELDS)}, period_min "
                            "FROM current_orbit WHERE norad_id = %s", (norad_id,))
    if el is None:
        abort(404, description="No current orbit for this object")
    t = parse_dt(request.args.get("t"), "t") or orbital.now_utc()
    periods = min(max(request.args.get("periods", 1, type=float), 0.1), 3.0)
    period_s = float(el["period_min"]) * 60.0
    offsets = np.linspace(0, period_s * periods, int(240 * periods) + 1)
    jd, fr = orbital.time_grid(t, offsets)
    err, r, _ = orbital.propagate([orbital.satrec_from_elements(el)], jd, fr)
    ecef = orbital.teme_to_ecef(r[0], jd, fr)
    good = err[0] == 0
    lat, lon, alt = orbital.ecef_to_geodetic(ecef[good])
    return ok({"norad_id": norad_id, "t": t.isoformat() + "Z", "period_min": float(el["period_min"]),
               "offsets_s": offsets[good].tolist(), "ecef_km": np.round(ecef[good], 2).tolist(),
               "now": {"lat": float(lat[0]), "lon": float(lon[0]), "alt_km": float(alt[0])} if len(lat) else None})
