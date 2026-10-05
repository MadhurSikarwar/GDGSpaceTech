"""Long-term analysis over the MongoDB history; summaries are stored in MySQL (SRS 3.6).

* Monthly average altitude per object — MongoDB MapReduce (map / reduce /
  finalize in JavaScript, run across both shards, output to the
  mr_monthly_altitude collection) -> MySQL summary_monthly_altitude.
  MapReduce is deprecated since MongoDB 5.0 in favour of the aggregation
  framework but still supported; it is used here to demonstrate the model.
* Object counts per orbital region per year — aggregation pipeline. The
  region boundaries come from MySQL Orbit_Region and are turned into a
  $switch, so both databases agree on what each region is.
  -> MySQL summary_region_year.
"""
import logging
from datetime import date

from bson.code import Code

from orbitwatch import db

log = logging.getLogger(__name__)

MAP = Code("""
function () {
    var d = this.epoch;
    emit({ n: this.norad_id, y: d.getUTCFullYear(), m: d.getUTCMonth() + 1 },
         { sum: this.mean_altitude_km, count: 1, min: this.mean_altitude_km, max: this.mean_altitude_km });
}""")

# Must return the same shape it receives: MongoDB may re-reduce partial results.
REDUCE = Code("""
function (key, values) {
    var r = { sum: 0, count: 0, min: Infinity, max: -Infinity };
    values.forEach(function (v) {
        r.sum += v.sum; r.count += v.count;
        if (v.min < r.min) r.min = v.min;
        if (v.max > r.max) r.max = v.max;
    });
    return r;
}""")

FINALIZE = Code("""
function (key, r) { r.avg = r.sum / r.count; return r; }""")


def monthly_altitude_mapreduce(mongo):
    result = mongo.command("mapReduce", "orbit_history", map=MAP, reduce=REDUCE, finalize=FINALIZE,
                           out={"replace": "mr_monthly_altitude"})
    rows = []
    for doc in mongo.mr_monthly_altitude.find():
        k, v = doc["_id"], doc["value"]
        rows.append((int(k["n"]), date(int(k["y"]), int(k["m"]), 1), float(v["avg"]), float(v["min"]),
                     float(v["max"]), int(v["count"])))
    return rows, result


def region_year_pipeline(regions):
    branches = [{"case": {"$and": [{"$gte": ["$alt", float(r["min_altitude_km"])]},
                                   {"$lt": ["$alt", float(r["max_altitude_km"])]}]},
                 "then": int(r["region_id"])} for r in regions]
    return [
        # one row per object and year: its average altitude that year
        {"$group": {"_id": {"n": "$norad_id", "y": {"$year": "$epoch"}}, "alt": {"$avg": "$mean_altitude_km"}}},
        {"$project": {"_id": 0, "y": "$_id.y", "region": {"$switch": {"branches": branches, "default": None}}}},
        {"$match": {"region": {"$ne": None}}},
        {"$group": {"_id": {"y": "$y", "r": "$region"}, "count": {"$sum": 1}}},
    ]


def run(ctx):
    mongo = db.mongo_db("jobs")
    monthly, mr = monthly_altitude_mapreduce(mongo)
    regions = db.query("jobs", "SELECT region_id, min_altitude_km, max_altitude_km FROM orbit_region")
    per_year = [(d["_id"]["r"], d["_id"]["y"], d["count"])
                for d in mongo.orbit_history.aggregate(region_year_pipeline(regions), allowDiskUse=True)]

    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        cur.execute("SELECT norad_id FROM space_object")
        known = {r[0] for r in cur.fetchall()}
        monthly = [r for r in monthly if r[0] in known]
        db.bulk_upsert(cur, "INSERT INTO summary_monthly_altitude (norad_id, month, avg_altitude_km, "
                            "min_altitude_km, max_altitude_km, samples) VALUES", monthly,
                       "AS n ON DUPLICATE KEY UPDATE avg_altitude_km = n.avg_altitude_km, "
                       "min_altitude_km = n.min_altitude_km, max_altitude_km = n.max_altitude_km, "
                       "samples = n.samples, computed_at = CURRENT_TIMESTAMP")
        cur.execute("DELETE FROM summary_region_year")
        db.bulk_upsert(cur, "INSERT INTO summary_region_year (region_id, year, object_count) VALUES", per_year)
        conn.commit()
        cur.close()

    msg = (f"MapReduce over {mongo.orbit_history.estimated_document_count()} element sets: {len(monthly)} "
           f"object-months; "
           f"aggregation: {len(per_year)} region-year counts")
    return len(monthly) + len(per_year), msg
