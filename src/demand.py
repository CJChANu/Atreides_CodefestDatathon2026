"""Zone-level hourly pickup forecasting, 1-72 h ahead (direct multi-horizon, one global model).

Every training row is (zone, forecast origin T, horizon h). Features only use information
available at T (pickups up to T), so the same model serves any horizon 1..72 without leakage.
"""
import numpy as np
import pandas as pd
from features import US_HOLIDAYS

H = 72


def hourly_matrix(hourly, zones):
    """Dense hour x zone matrix of pickups (missing hours -> 0)."""
    s = hourly[hourly.loc_id.isin(zones)].pivot_table(index="hour", columns="loc_id", values="pickups", aggfunc="sum")
    idx = pd.date_range(s.index.min(), s.index.max(), freq="h")
    return s.reindex(idx).fillna(0)[zones]


def make_rows(M, origins, horizons=range(1, H + 1)):
    """Build the (zone, origin, horizon) design matrix. origins: timestamps of the last observed hour."""
    vals = M.to_numpy()
    pos = {t: i for i, t in enumerate(M.index)}
    rows = []
    for T in origins:
        i = pos[T]
        if i < 24 * 28:
            continue
        past = vals[: i + 1]
        last24 = past[-24:].mean(0)
        last168 = past[-168:].mean(0)
        for h in horizons:
            j = i + h
            if j >= len(vals):
                break
            t = M.index[j]
            # same hour-of-week in the last 4 weeks (all strictly <= T because h <= 168)
            wk = [past[j - 168 * k] for k in range(1, 5) if j - 168 * k <= i]
            wk = np.array(wk)
            # same hour-of-day on the most recent observed day
            d_lag = j - 24 * int(np.ceil(h / 24))
            for z, zone in enumerate(M.columns):
                rows.append((zone, T, h, t, vals[j, z],
                             wk[0, z], wk[:, z].mean(), np.median(wk[:, z]), past[d_lag, z],
                             last24[z], last168[z], past[-1, z]))
    cols = ["loc_id", "origin", "h", "target_time", "y", "lag_1w", "how_mean_4w", "how_med_4w",
            "last_day_same_hour", "mean_last24", "mean_last168", "last_obs"]
    df = pd.DataFrame(rows, columns=cols)
    tt = df.target_time
    df["hour"] = tt.dt.hour
    df["dow"] = tt.dt.dayofweek
    df["is_weekend"] = (df.dow >= 5).astype("int8")
    df["is_holiday"] = tt.dt.normalize().isin(US_HOLIDAYS).astype("int8")
    df["level_ratio"] = df.mean_last168 / (df.how_mean_4w.groupby([df.loc_id, df.origin]).transform("mean") + 1e-6)
    df["loc_id"] = df.loc_id.astype("category")
    return df


FEATURES = ["loc_id", "h", "hour", "dow", "is_weekend", "is_holiday", "lag_1w", "how_mean_4w", "how_med_4w",
            "last_day_same_hour", "mean_last24", "mean_last168", "last_obs", "level_ratio"]


def metrics(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    return {"MAE": np.mean(np.abs(y - p)), "RMSE": np.sqrt(np.mean((y - p) ** 2)),
            "WAPE_%": 100 * np.abs(y - p).sum() / y.sum(),
            "R2": 1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum()}
