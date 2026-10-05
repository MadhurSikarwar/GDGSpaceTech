"""Re-entry model features and training on a physically shaped synthetic population."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from orbitwatch.jobs import reentry


def decaying_history(start_alt, days, loss0, bstar, n=40):
    """Element sets of an object whose altitude loss accelerates as it sinks."""
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    docs = []
    for i in range(n):
        d = days * i / n
        frac = d / days
        alt = start_alt - (start_alt - 150) * (1 - (1 - frac) ** 0.35)
        docs.append({"epoch": t0 + timedelta(days=d), "mean_altitude_km": alt, "perigee_km": alt - 3,
                     "apogee_km": alt + 3, "eccentricity": 0.0005, "inclination": 53.0, "bstar": bstar,
                     "mean_motion_dot": loss0 * (1 + 5 * frac)})
    return docs, t0 + timedelta(days=days)


def test_features_measure_altitude_loss():
    docs, _ = decaying_history(400, 200, 1e-4, 3e-4)
    feats = reentry._features(docs)
    assert feats.shape == (40, len(reentry.FEATURES))
    rate = feats[:, reentry.FEATURES.index("loss_rate_14d")]
    assert np.isnan(rate[0])                       # no window yet for the first element set
    assert np.all(rate[3:] > 0)                    # losing altitude
    assert rate[-1] > rate[5]                      # and faster near the end


def test_quantile_models_learn_lifetime():
    sklearn = pytest.importorskip("sklearn.ensemble")
    rng = np.random.default_rng(0)
    X, y = [], []
    for _ in range(60):
        start = rng.uniform(300, 480)
        days = (start - 150) * rng.uniform(0.6, 1.4)
        docs, decay = decaying_history(start, days, 1e-4, rng.uniform(1e-4, 8e-4))
        feats = reentry._features(docs)
        for i in range(0, len(docs), 4):
            X.append(feats[i])
            y.append((decay - docs[i]["epoch"]).total_seconds() / 86400)
    X, y = np.array(X), np.array(y)
    model = sklearn.HistGradientBoostingRegressor(loss="quantile", quantile=0.5, max_iter=200, random_state=0)
    model.fit(X[:400], np.log1p(y[:400]))
    pred = np.expm1(model.predict(X[400:]))
    rel = np.median(np.abs(pred - y[400:]) / np.maximum(y[400:], 1))
    assert rel < 0.35
