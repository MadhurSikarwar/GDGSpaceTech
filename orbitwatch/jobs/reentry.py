"""Re-entry prediction (SRS 3.8): remaining orbital lifetime of decaying objects.

Pipeline, all from real data:

1. Ground truth: objects that have ALREADY re-entered. The re-entry time is the
   Space-Track decay message when available (reentry_observation), otherwise
   the SATCAT decay date.
2. Training set: for every element set in the year before re-entry (MongoDB
   history: CelesTrak, Space-Track gp_history, the OrbitalGuard archive), the
   orbit and how fast it was decaying at that moment; the target is the days
   that were left. Stored with the model (dataset.npz) and fingerprinted.
3. Training and evaluation: gradient-boosted trees with quantile loss give a
   median and a 10-90 % interval. 20 % of the re-entered OBJECTS are held out
   (never seen in training) for the test score, plus grouped cross-validation.
4. Registry (MySQL ml_model): every model with its data window, sizes, test
   metrics, features, dataset hash and artifact path. A model is only
   activated if the data are sufficient and the held-out test passes the gate;
   otherwise it is registered as 'rejected' and predictions stay unavailable.
5. Predictions: only from the active model, only for objects measurably
   decaying, with the interval and the element set they were made from.
"""
import hashlib
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import joblib
import numpy as np

from orbitwatch import config, db
from orbitwatch.jobs.runner import SkipJob

log = logging.getLogger(__name__)

FEATURES = ["mean_altitude_km", "perigee_km", "apogee_km", "eccentricity", "inclination", "log_bstar",
            "mean_motion_dot", "loss_rate_14d"]
QUANTILES = (0.1, 0.5, 0.9)
MIN_OBJECTS = 40                 # re-entered objects with usable history needed to train at all
MIN_TEST_OBJECTS = 8
GATE_MAX_MEDIAN_REL_ERROR = 0.6  # held-out median relative error must be below this
GATE_MIN_COVERAGE = 0.5          # ... and the 10-90 % interval must contain at least half the test truths
MAX_SAMPLES_PER_OBJECT = 40
ALGORITHM = "HistGradientBoostingRegressor (quantile loss 0.1/0.5/0.9) on log1p(days remaining)"


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
            if until_by_id is None or d["epoch"] <= until_by_id[d["norad_id"]]:
                out[d["norad_id"]].append(d)
    return out


def ground_truth():
    """{norad_id: re-entry datetime (UTC, aware)} with the most precise source available."""
    truth = {}
    for r in db.query("jobs", "SELECT norad_id, decay_date FROM space_object WHERE decay_date IS NOT NULL"):
        truth[r["norad_id"]] = (datetime.combine(r["decay_date"], datetime.min.time()) + timedelta(hours=12),
                                "SATCAT decay date")
    for r in db.query("jobs", "SELECT norad_id, decay_epoch FROM reentry_observation WHERE source = 'Space-Track decay'"):
        truth[r["norad_id"]] = (r["decay_epoch"], "Space-Track decay message")
    return {n: (dt.replace(tzinfo=timezone.utc), src) for n, (dt, src) in truth.items()}


def build_training_set():
    truth = ground_truth()
    hist_coll = db.mongo_db("jobs").orbit_history
    with_history = set(hist_coll.distinct("norad_id", {"norad_id": {"$in": list(truth)}})) if truth else set()
    hist = _histories(with_history, until_by_id={n: truth[n][0] for n in with_history})
    X, y, groups, epochs = [], [], [], []
    for n, docs in hist.items():
        decay = truth[n][0]
        docs = [d for d in docs if timedelta(0) < decay - d["epoch"] <= timedelta(days=365)]
        if len(docs) < 3:
            continue
        feats = _features(docs)
        keep = np.unique(np.linspace(0, len(docs) - 1, min(len(docs), MAX_SAMPLES_PER_OBJECT)).astype(int))
        for i in keep:
            X.append(feats[i])
            y.append((decay - docs[i]["epoch"]).total_seconds() / 86400.0)
            groups.append(n)
            epochs.append(docs[i]["epoch"].date())
    return np.array(X), np.array(y), np.array(groups), epochs, len(truth), len(with_history)


def _fit(q, X, y):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=300, learning_rate=0.05,
                                         max_leaf_nodes=31, min_samples_leaf=10, random_state=42).fit(X, y)


def _score(models, X, y):
    pred = {q: np.expm1(models[q].predict(X)) for q in QUANTILES}
    lo, hi = np.minimum(pred[0.1], pred[0.9]), np.maximum(pred[0.1], pred[0.9])
    err = np.abs(pred[0.5] - y)
    return {"mae_days": float(np.mean(err)), "median_ae_days": float(np.median(err)),
            "median_relative_error": float(np.median(err / np.maximum(y, 1.0))),
            "interval_coverage": float(np.mean((y >= lo) & (y <= hi)))}


def train(ctx):
    from sklearn.model_selection import GroupKFold, GroupShuffleSplit

    X, y, groups, epochs, n_truth, n_with_history = build_training_set()
    n_objects = len(set(groups.tolist()))
    version = datetime.now(timezone.utc).strftime("reentry-%Y%m%d-%H%M%S")
    if n_objects < MIN_OBJECTS:
        note = (f"insufficient training data: {n_objects} re-entered objects with at least 3 element sets in their last "
                f"year (need {MIN_OBJECTS}); {n_truth} known re-entries, {n_with_history} with any history. "
                "Import history with `ow spacetrack-import --mode decayed` (needs Space-Track credentials).")
        _register(version, "rejected", n_objects, len(y), 0, 0, epochs, {}, None, None, note)
        raise SkipJob(note)

    target = np.log1p(y)
    # Held-out test: whole objects, never seen in training.
    split = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
    tr, te = next(split.split(X, target, groups))
    test_objects = len(set(groups[te].tolist()))
    models_tr = {q: _fit(q, X[tr], target[tr]) for q in QUANTILES}
    test = _score(models_tr, X[te], y[te])
    # Grouped cross-validation on the training part, for stability of the estimate.
    folds = min(5, len(set(groups[tr].tolist())))
    cv = [_score({q: _fit(q, X[tr][a], target[tr][a]) for q in QUANTILES}, X[tr][b], y[tr][b])
          for a, b in GroupKFold(n_splits=folds).split(X[tr], target[tr], groups[tr])]
    metrics = {"test": test, "cv_mean": {k: float(np.mean([c[k] for c in cv])) for k in cv[0]}, "cv_folds": folds,
               "objects": n_objects, "samples": int(len(y))}
    passed = (test_objects >= MIN_TEST_OBJECTS and test["median_relative_error"] < GATE_MAX_MEDIAN_REL_ERROR
              and test["interval_coverage"] >= GATE_MIN_COVERAGE)

    # The deployed model is refit on all objects; the registry keeps the held-out score of the same recipe.
    final = {q: _fit(q, X, target) for q in QUANTILES}
    out_dir = config.MODEL_DIR / version
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_dir / "dataset.npz", X=X, y=y, groups=groups, features=np.array(FEATURES))
    digest = hashlib.sha256((out_dir / "dataset.npz").read_bytes()).hexdigest()
    joblib.dump({"models": final, "features": FEATURES, "version": version, "metrics": metrics}, out_dir / "model.joblib")
    (out_dir / "metrics.json").write_text(json.dumps({"version": version, **metrics}, indent=2), encoding="utf-8")
    note = ("accepted" if passed else
            f"rejected by the gate (test objects {test_objects} >= {MIN_TEST_OBJECTS}, median relative error "
            f"{test['median_relative_error']:.2f} < {GATE_MAX_MEDIAN_REL_ERROR}, coverage "
            f"{test['interval_coverage']:.2f} >= {GATE_MIN_COVERAGE} required)")
    _register(version, "active" if passed else "rejected", n_objects - test_objects, len(tr), test_objects, len(te),
              epochs, metrics, digest, str(out_dir / "model.joblib"), note, test)
    msg = (f"model {version} {'ACTIVE' if passed else 'REJECTED'}: {n_objects} re-entered objects, {len(y)} samples; "
           f"held-out ({test_objects} objects) median error {test['median_ae_days']:.1f} days "
           f"({test['median_relative_error'] * 100:.0f} %), 10-90 % interval covers {test['interval_coverage'] * 100:.0f} %")
    return len(y), msg


def _register(version, status, train_objects, train_rows, test_objects, test_rows, epochs, metrics, digest, path, note,
              test=None):
    test = test or {}
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        if status == "active":
            cur.execute("UPDATE ml_model SET status = 'retired' WHERE task = 'reentry' AND status = 'active'")
        cur.execute("""INSERT INTO ml_model (model_version, task, algorithm, status, training_objects, training_rows,
                           test_objects, test_rows, data_from, data_to, mae_days, median_ae_days, interval_coverage,
                           metrics, features, dataset_sha256, artifact_path, notes)
                       VALUES (%s, 'reentry', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (version, ALGORITHM, status, train_objects, train_rows, test_objects, test_rows,
                     min(epochs) if epochs else None, max(epochs) if epochs else None, test.get("mae_days"),
                     test.get("median_ae_days"), test.get("interval_coverage"), json.dumps(metrics),
                     json.dumps(FEATURES), digest, path, note[:500]))
        conn.commit()


def active_model():
    return db.query_one("jobs", "SELECT * FROM ml_model WHERE task = 'reentry' AND status = 'active' "
                                "ORDER BY trained_at DESC LIMIT 1")


def predict(ctx):
    model = active_model()
    if model is None:
        latest = db.query_one("jobs", "SELECT model_version, notes FROM ml_model WHERE task = 'reentry' "
                                      "ORDER BY trained_at DESC LIMIT 1")
        with db.mysql_conn("jobs") as conn:   # never leave predictions of a model that is no longer active
            cur = conn.cursor()
            cur.execute("DELETE FROM reentry_prediction")
            conn.commit()
        raise SkipJob("no accepted re-entry model: predictions unavailable"
                      + (f" (latest {latest['model_version']}: {latest['notes']})" if latest else ""))
    bundle = joblib.load(model["artifact_path"])
    candidates = db.query("jobs", """
        SELECT co.norad_id, co.epoch, co.mean_altitude_km, co.perigee_km, co.apogee_km, co.eccentricity,
               co.inclination, co.bstar, co.mean_motion_dot
          FROM current_orbit co JOIN space_object so ON so.norad_id = co.norad_id
         WHERE so.in_earth_orbit AND co.mean_altitude_km < 600 AND co.mean_motion_dot > 0
           AND co.epoch > UTC_TIMESTAMP() - INTERVAL 10 DAY""")
    if not candidates:
        raise SkipJob("no low, decaying objects with a recent element set")
    since = datetime.now(timezone.utc) - timedelta(days=21)
    hist = _histories([c["norad_id"] for c in candidates], since=since)
    rows, today, now = [], date.today(), datetime.now(timezone.utc)
    for c in candidates:
        docs = hist.get(c["norad_id"], [])
        epoch = c["epoch"].replace(tzinfo=timezone.utc)
        series = [d for d in docs if d["epoch"] < epoch] + [{**c, "epoch": epoch}]
        feats = _features(series)[-1]
        rate = feats[-1]
        # "Currently decaying": measurably losing altitude over the last two weeks, or already very low.
        if not ((not np.isnan(rate) and rate > 0.02) or c["mean_altitude_km"] < 300):
            continue
        pred = {q: float(np.expm1(bundle["models"][q].predict(feats.reshape(1, -1))[0])) for q in QUANTILES}
        lo, mid, hi = sorted(max(0.0, pred[q]) for q in QUANTILES)
        elapsed = (now - epoch).total_seconds() / 86400.0
        mid, lo, hi = max(mid - elapsed, 0.0), max(lo - elapsed, 0.0), max(hi - elapsed, 0.0)
        rows.append((c["norad_id"], now.replace(tzinfo=None), today + timedelta(days=round(mid)), mid, min(lo, mid),
                     max(hi, mid), model["model_version"], c["epoch"]))
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM reentry_prediction")
        db.bulk_upsert(cur, "INSERT INTO reentry_prediction (norad_id, predicted_on, predicted_decay_date, "
                            "days_remaining, lower_days, upper_days, model_version, input_epoch) VALUES", rows)
        conn.commit()
    return len(rows), f"{len(rows)} decaying objects predicted with model {model['model_version']}"


def status():
    """Model registry + prediction availability, for the reports page and dashboard."""
    models = db.query("viewer", "SELECT model_version, algorithm, status, trained_at, training_objects, training_rows, "
                                "test_objects, test_rows, data_from, data_to, mae_days, median_ae_days, interval_coverage, "
                                "notes FROM ml_model WHERE task = 'reentry' ORDER BY trained_at DESC LIMIT 10")
    active = next((m for m in models if m["status"] == "active"), None)
    n_pred = db.query_one("viewer", "SELECT COUNT(*) AS n FROM reentry_prediction")["n"]
    return {"available": bool(active and n_pred), "active_model": active, "models": models, "predictions": n_pred,
            "gate": {"min_objects": MIN_OBJECTS, "min_test_objects": MIN_TEST_OBJECTS,
                     "max_median_relative_error": GATE_MAX_MEDIAN_REL_ERROR, "min_interval_coverage": GATE_MIN_COVERAGE}}
