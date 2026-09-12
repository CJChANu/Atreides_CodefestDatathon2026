"""Cleaning + pre-trip feature engineering shared by the notebook, training and inference."""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ZONES = ROOT.parent / "Datathon 2026 - Round 1 - Materials" / "Urban_Flow_Analytics_Zone_Dataset.csv"

AIRPORTS = {1, 132, 138}          # Newark, JFK, LaGuardia
UNKNOWN_ZONES = {264, 265}        # "N/A" / "Outside of NYC" in the zone file
US_HOLIDAYS = pd.to_datetime([
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-10-13", "2025-11-11",
    "2025-11-27", "2025-11-28", "2025-12-24", "2025-12-25", "2025-12-31", "2026-01-01",
    "2026-01-19", "2026-02-16",
])


def load_zones():
    z = pd.read_csv(ZONES)
    return z.set_index("loc_id")


def flag_anomalies(df):
    """Boolean columns, one per anomaly type (a row can carry several)."""
    dur = (df.dropoff_timestamp - df.pickup_timestamp).dt.total_seconds() / 60
    speed = df.distance_miles / (dur / 60)
    m = df.pickup_timestamp.dt.to_period("M").astype(str)
    return pd.DataFrame({
        "neg_fare": (df.base_fare < 0) | (df.charge_total < 0),
        "zero_fare": (df.base_fare == 0),
        "zero_dist_fare": (df.distance_miles == 0) & (df.base_fare > 0),
        "zero_riders": df.rider_count == 0,
        "null_block": df.rider_count.isna(),
        "dropoff_before_pu": dur < 0,
        "sub_minute": (dur >= 0) & (dur < 1),
        "over_3h": dur > 180,
        "unrealistic_speed": (dur > 0) & (speed > 80),
        "extreme_distance": df.distance_miles > 100,
        "out_of_period": m != df["src_month"],
        "extreme_fare": df.base_fare > 500,
    }, index=df.index)


def clean(df, drop_rows=True):
    """Apply the drop / impute decisions justified in the notebook. Returns cleaned copy.

    drop_rows=False applies only the imputations (used for the "no cleaning" ablation).
    """
    f = flag_anomalies(df)
    # Provider 7 (Helix) never records a real drop-off time (dropoff == pickup on 100% of its rows),
    # but its fares & distances are valid: keep those rows, only blank the duration target.
    helix = df.provider_code == 7
    drop = (f.neg_fare | f.zero_fare | f.zero_dist_fare | f.dropoff_before_pu | (f.sub_minute & ~helix)
            | f.over_3h | f.unrealistic_speed | f.extreme_distance | f.out_of_period | f.extreme_fare)
    out = df.loc[~drop].copy() if drop_rows else df.copy()
    out["is_flex"] = out.rider_count.isna().astype("int8")        # null block == Flex Fare trips
    out["rider_imputed"] = (out.rider_count.isna() | (out.rider_count == 0)).astype("int8")
    out["rider_count"] = out.rider_count.where(out.rider_count > 0, 1).clip(upper=6)   # mode = 1
    out["rate_class_id"] = out.rate_class_id.fillna(99).astype(int)
    out["duration_min"] = (out.dropoff_timestamp - out.pickup_timestamp).dt.total_seconds() / 60
    out.loc[out.provider_code == 7, "duration_min"] = np.nan
    return out


T0 = pd.Timestamp("2025-04-01")


def time_features(ts):
    # t_index lets trees learn step changes in pricing policy (e.g. the Dec-2025 Flex Fare repricing);
    # calendar month is deliberately excluded - test months are never seen in training.
    return pd.DataFrame({
        "hour": ts.dt.hour, "dow": ts.dt.dayofweek, "t_index": (ts - T0).dt.days,
        "minute_of_day": ts.dt.hour * 60 + ts.dt.minute,
        "is_weekend": (ts.dt.dayofweek >= 5).astype("int8"),
        "is_holiday": ts.dt.normalize().isin(US_HOLIDAYS).astype("int8"),
        "hour_sin": np.sin(2 * np.pi * ts.dt.hour / 24), "hour_cos": np.cos(2 * np.pi * ts.dt.hour / 24),
    }, index=ts.index)


class OdStats:
    """Historical origin-destination statistics learned on the TRAIN split only (no leakage).

    At booking time the app knows the pickup & drop-off zones, so the typical distance/duration
    of that corridor (optionally at that hour band) is a legitimate pre-trip estimate of route length.
    """
    MIN_N = 5

    def fit(self, tr):
        tr = tr.assign(hband=tr.pickup_timestamp.dt.hour // 3)
        g = tr.groupby(["origin_loc_id", "dest_loc_id"])
        self.od = pd.DataFrame({"od_dist": g.distance_miles.median(), "od_dur": g.duration_min.median(),
                                "od_n": g.size()}).query("od_n >= @self.MIN_N")
        gh = tr.groupby(["origin_loc_id", "dest_loc_id", "hband"])
        self.odh = pd.DataFrame({"odh_dur": gh.duration_min.median(), "odh_n": gh.size()}).query("odh_n >= @self.MIN_N")
        self.o = tr.groupby("origin_loc_id").agg(o_dist=("distance_miles", "median"), o_dur=("duration_min", "median"))
        self.d = tr.groupby("dest_loc_id").agg(d_dist=("distance_miles", "median"), d_dur=("duration_min", "median"))
        self.glob = tr[["distance_miles", "duration_min"]].median()
        return self

    def transform(self, df):
        k = df[["origin_loc_id", "dest_loc_id"]].assign(hband=df.pickup_timestamp.dt.hour // 3)
        x = (k.join(self.od, on=["origin_loc_id", "dest_loc_id"])
              .join(self.odh, on=["origin_loc_id", "dest_loc_id", "hband"])
              .join(self.o, on="origin_loc_id").join(self.d, on="dest_loc_id"))
        # Back-off: unseen corridor -> mean of origin & destination zone medians -> global median
        x["od_dist"] = x.od_dist.fillna((x.o_dist + x.d_dist) / 2).fillna(self.glob.distance_miles)
        x["od_dur"] = x.od_dur.fillna((x.o_dur + x.d_dur) / 2).fillna(self.glob.duration_min)
        x["odh_dur"] = x.odh_dur.fillna(x.od_dur)
        x["od_n"] = x.od_n.fillna(0)
        return x[["od_dist", "od_dur", "odh_dur", "od_n"]]


CAT = ["origin_loc_id", "dest_loc_id", "o_borough", "d_borough", "provider_code", "rate_class_id"]


def build_features(df, od: OdStats, zones):
    b = zones.borough_name.astype("category").cat.codes
    X = pd.concat([time_features(df.pickup_timestamp), od.transform(df)], axis=1)
    X["origin_loc_id"] = df.origin_loc_id
    X["dest_loc_id"] = df.dest_loc_id
    X["o_borough"] = df.origin_loc_id.map(b).fillna(-1).astype(int)
    X["d_borough"] = df.dest_loc_id.map(b).fillna(-1).astype(int)
    X["o_airport"] = df.origin_loc_id.isin(AIRPORTS).astype("int8")
    X["d_airport"] = df.dest_loc_id.isin(AIRPORTS).astype("int8")
    X["same_zone"] = (df.origin_loc_id == df.dest_loc_id).astype("int8")
    X["unknown_zone"] = (df.origin_loc_id.isin(UNKNOWN_ZONES) | df.dest_loc_id.isin(UNKNOWN_ZONES)).astype("int8")
    X["provider_code"] = df.provider_code
    X["rate_class_id"] = df.rate_class_id
    X["rider_count"] = df.rider_count
    X["is_flex"] = df.is_flex
    for c in CAT:
        X[c] = X[c].astype("category")
    return X


class TripModel:
    """Deployable bundle: raw booking request -> prediction. This is the object saved as .pkl.

    Booking request columns: pickup_timestamp, origin_loc_id, dest_loc_id, rider_count,
    provider_code, rate_class_id, is_flex. Nothing measured after the meter starts is used.
    """

    def __init__(self, model, od_stats, zones, feature_names, target):
        self.model, self.od, self.zones = model, od_stats, zones
        self.feature_names, self.target = feature_names, target

    def prepare(self, req):
        req = req.copy()
        req["pickup_timestamp"] = pd.to_datetime(req.pickup_timestamp)
        req["rider_count"] = req.get("rider_count", 1)
        req["rider_count"] = pd.Series(req.rider_count, index=req.index).fillna(1).clip(1, 6)
        req["rate_class_id"] = pd.Series(req.get("rate_class_id", 99), index=req.index).fillna(99).astype(int)
        req["is_flex"] = pd.Series(req.get("is_flex", 0), index=req.index).fillna(0).astype("int8")
        X = build_features(req, self.od, self.zones)[self.feature_names]
        # align category levels with training so unseen zones map to NaN (handled by LightGBM)
        for c, levels in self.categories_.items():
            X[c] = pd.Categorical(X[c].astype(object), categories=levels)
        return X

    def fit_categories(self, X):
        self.categories_ = {c: X[c].cat.categories for c in X.columns if str(X[c].dtype) == "category"}
        return self

    def predict(self, req):
        return np.clip(self.model.predict(self.prepare(req)), 0, None)
