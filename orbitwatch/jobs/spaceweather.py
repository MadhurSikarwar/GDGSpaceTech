"""Space-weather job: one NOAA SWPC reading into MySQL space_weather (feeds Pc and the dashboard)."""
from orbitwatch import db
from orbitwatch.physics import spaceweather


def latest(account="viewer", max_age_hours=12):
    """Most recent stored reading, or None if there is none recent enough."""
    return db.query_one(account, """
        SELECT observed_at, kp, ap, f107, activity, drag_scalar, source, live, fetched_at
          FROM space_weather WHERE fetched_at >= UTC_TIMESTAMP() - INTERVAL %s HOUR
         ORDER BY observed_at DESC LIMIT 1""", (max_age_hours,))


def drag_scalar(account="jobs"):
    row = latest(account)
    return float(row["drag_scalar"]) if row else 1.0


def run(ctx):
    sw = spaceweather.fetch()
    db.execute("jobs", """
        INSERT INTO space_weather (observed_at, kp, ap, f107, activity, drag_scalar, source, live)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) AS n
        ON DUPLICATE KEY UPDATE kp = n.kp, ap = n.ap, f107 = n.f107, activity = n.activity,
                                drag_scalar = n.drag_scalar, source = n.source, live = n.live,
                                fetched_at = CURRENT_TIMESTAMP""",
               (sw["observed_at"], sw["kp"], sw["ap"], sw["f107"], sw["activity"], sw["drag_scalar"],
                sw["source"], sw["live"]))
    from orbitwatch.jobs import sources
    sources.record_source("noaa_swpc", "ok" if sw["live"] else "error", 1,
                          f"Kp {sw['kp']:.2f}, F10.7 {sw['f107']:.0f} sfu" + ("" if sw["live"] else " (fallback values)"))
    return 1, (f"Kp {sw['kp']:.2f} ({sw['activity'].lower()}), Ap {sw['ap']:.0f}, F10.7 {sw['f107']:.0f} sfu, "
               f"drag scalar {sw['drag_scalar']:.2f} — {sw['source']}")
