"""Re-entry prediction (innovative experiment, SRS 3.8).

A regression model is trained on objects that have ALREADY re-entered: for
each element set in the last year before its decay date, the features
describe the orbit and how fast it was decaying at that moment, and the
target is how many days were left. scikit-learn gradient-boosted trees with
quantile loss give a median estimate plus a 10-90 % interval.

Training data needs the altitude history of re-entered objects. It
accumulates on its own as watched objects decay, and can be bootstrapped
from Space-Track (`ow spacetrack-import --mode decayed`).
"""
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import joblib
import numpy as np

from orbitwatch import config, db, orbital
from orbitwatch.jobs.runner import SkipJob

log = logging.getLogger(__name__)

MODEL_PATH = config.MODEL_DIR / "reentry_model.joblib"
FEATURES = ["mean_altitude_km", "perigee_km", "apogee_km", "eccentricity", "inclination", "log_bstar",
            "mean_motion_dot", "loss_rate_14d"]
QUANTILES = (0.1, 0.5, 0.9)
MIN_OBJECTS = 20
MAX_SAMPLES_PER_OBJECT = 40


def _features(docs):
    """Feature rows for a time-sorted list of history documents of one object."""
    t = np.array([d["epoch"].timestamp() / 86400.0 for d in docs])
    alt = np.array([d["mean_altitude_km"] for d in docs])
    rows = []
    for i, d in enumerate(docs):
        window = (t >= t[i] - 14) & (t <= t[i])
        rate = np.nan
        if window.sum() >= 2 and t[i] - t[window][0] >= 1:
            rate = -float(np.polyfit(t[window], alt[window], 1)[0])  # km/day lost (positive = decaying)
        bstar = d.get("bstar") or 0.0
        rows.append([d["mean_altitude_km"], d["perigee_km"], d["apogee_km"], d["eccentricity"], d["inclination"],
                     np.sign(bstar) * np.log10(abs(bstar) + 1e-9) if bstar else -9.0,
                     d.get("mean_motion_dot") or 0.0, rate])
    return np.array(rows, dtype=float)


def _histories(norad_ids, since=None, until_by_id=None):
    coll = db.mongo_db("jobs").orbit_history
    out = defaultdict(list)
    ids = list(norad_ids)
    for i in range(0, len(ids), 500):
        query = {"norad_id": {"$in": ids[i:i + 500]}}
        if since is not None:
            query["epoch"] = {"$gte": since}
        for d in coll.find(query, {"_id": 0, "raw": 0}).sort([("norad_id", 1), ("epoch", 1)]):
            if until_by_id is None or d["epoch"].date() <= until_by_id[d["norad_id"]]:
                out[d["norad_id"]].append(d)
    return out


def build_training_set():
    decayed = {r["norad_id"]: r["decay_date"] for r in db.query(
        "jobs", "SELECT norad_id, decay_date FROM space_object WHERE decay_date IS NOT NULL")}
    with_history = set(db.mongo_db("jobs").orbit_history.distinct("norad_id", {"norad_id": {"$in": list(decayed)}})) \
        if decayed else set()
    hist = _histories(with_history, until_by_id=decayed)
    X, y, groups = [], [], []
    for n, docs in hist.items():
        decay = datetime.combine(decayed[n], datetime.min.time(), tzinfo=timezone.utc)
        docs = [d for d in docs if timedelta(0) < decay - d["epoch"] <= timedelta(days=365)]
        if len(docs) < 3:
            continue
        feats = _features(docs)
        keep = np.unique(np.linspace(0, len(docs) - 1, min(len(docs), MAX_SAMPLES_PER_OBJECT)).astype(int))
        for i in keep:
            X.append(feats[i])
            y.append((decay - docs[i]["epoch"]).total_seconds() / 86400.0)
            groups.append(n)
    return np.array(X), np.array(y), np.array(groups)


def train(ctx):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import GroupKFold

    X, y, groups = build_training_set()
    n_objects = len(set(groups.tolist()))
    if n_objects < MIN_OBJECTS:
        raise SkipJob(f"insufficient training data: {n_objects} re-entered objects with history "
                      f"(need {MIN_OBJECTS}); import some with `ow spacetrack-import --mode decayed`")
    target = np.log1p(y)

    def fit(q, Xs, ys):
        return HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=300, learning_rate=0.05,
                                             max_leaf_nodes=31, min_samples_leaf=10, random_state=42).fit(Xs, ys)

    # Cross-validation grouped by object, so no object is in both train and test.
    folds = min(5, n_objects)
    errs, rel, covered = [], [], []
    for tr, te in GroupKFold(n_splits=folds).split(X, target, groups):
        models = {q: fit(q, X[tr], target[tr]) for q in QUANTILES}
        pred = {q: np.expm1(models[q].predict(X[te])) for q in QUANTILES}
        errs.extend(np.abs(pred[0.5] - y[te]))
        rel.extend(np.abs(pred[0.5] - y[te]) / np.maximum(y[te], 1))
        covered.extend((y[te] >= np.minimum(pred[0.1], pred[0.9])) & (y[te] <= np.maximum(pred[0.1], pred[0.9])))
    metrics = {"objects": n_objects, "samples": int(len(y)), "cv_folds": folds,
               "median_abs_error_days": float(np.median(errs)), "mean_abs_error_days": float(np.mean(errs)),
               "median_relative_error": float(np.median(rel)), "interval_coverage_10_90": float(np.mean(covered))}
    models = {q: fit(q, X, target) for q in QUANTILES}
    version = datetime.now(timezone.utc).strftime("hgb-%Y%m%d-%H%M")
    joblib.dump({"models": models, "features": FEATURES, "version": version, "metrics": metrics}, MODEL_PATH)
    (config.MODEL_DIR / "reentry_metrics.json").write_text(json.dumps({"version": version, **metrics}, indent=2))
    return len(y), (f"model {version}: {n_objects} re-entered objects, {len(y)} samples; cross-validated median "
                    f"error {metrics['median_abs_error_days']:.1f} days "
                    f"({metrics['median_relative_error'] * 100:.0f} %), 10-90 % interval covers "
                    f"{metrics['interval_coverage_10_90'] * 100:.0f} %")


def predict(ctx):
    if not MODEL_PATH.exists():
        raise SkipJob("no trained model yet (run reentry-train)")
    bundle = joblib.load(MODEL_PATH)
    candidates = db.query("jobs", """
        SELECT co.norad_id, co.epoch, co.mean_altitude_km, co.perigee_km, co.apogee_km, co.eccentricity,
               co.inclination, co.bstar, co.mean_motion_dot
          FROM current_orbit co JOIN space_object so ON so.norad_id = co.norad_id
         WHERE so.decay_date IS NULL AND co.mean_altitude_km < 600 AND co.mean_motion_dot > 0""")
    if not candidates:
        raise SkipJob("no low, decaying objects with a current orbit")
    since = datetime.now(timezone.utc) - timedelta(days=21)
    hist = _histories([c["norad_id"] for c in candidates], since=since)
    rows, today = [], date.today()
    for c in candidates:
        docs = hist.get(c["norad_id"], [])
        epoch = c["epoch"].replace(tzinfo=timezone.utc)
        current = {**c, "epoch": epoch}
        series = [d for d in docs if d["epoch"] < epoch] + [current]
        feats = _features(series)[-1]
        rate = feats[-1]
        # "Currently decaying": measurably losing altitude, or already very low.
        if not ((not np.isnan(rate) and rate > 0.02) or (np.isnan(rate) and c["mean_motion_dot"] > 5e-5)
                or c["mean_altitude_km"] < 300):
            continue
        pred = {q: float(np.expm1(bundle["models"][q].predict(feats.reshape(1, -1))[0])) for q in QUANTILES}
        lo, mid, hi = sorted(max(0.0, pred[q]) for q in QUANTILES)
        elapsed = (datetime.now(timezone.utc) - epoch).total_seconds() / 86400.0
        mid, lo, hi = max(mid - elapsed, 0.0), max(lo - elapsed, 0.0), max(hi - elapsed, 0.0)
        rows.append((c["norad_id"], datetime.now(timezone.utc).replace(tzinfo=None),
                     today + timedelta(days=round(mid)), mid, min(lo, mid), max(hi, mid), bundle["version"]))
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM reentry_prediction")
        db.bulk_upsert(cur, "INSERT INTO reentry_prediction (norad_id, predicted_on, predicted_decay_date, "
                            "days_remaining, lower_days, upper_days, model_version) VALUES", rows)
        conn.commit()
    return len(rows), f"{len(rows)} decaying objects predicted with model {bundle['version']}"
