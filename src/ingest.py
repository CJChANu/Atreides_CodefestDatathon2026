"""Stream each monthly CSV out of the raw zip into DuckDB (no full extraction -> low disk use).

Per month we write:
  data/quality/<m>.parquet   - anomaly counts over ALL rows (full-population data-quality audit)
  data/agg/hourly_<m>.parquet - genuine pickups per zone per hour (demand forecasting input)
  data/agg/od_<m>.parquet     - genuine-pickup origin-destination counts per time-of-day band (flow clustering)
  data/agg/daily_<m>.parquet  - daily business KPIs (dashboard input)
  data/sample/<m>.parquet     - reproducible 5% random sample of raw rows (modelling input)

Usage: python src/ingest.py            (all months)
"""
import subprocess, sys, zipfile
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW_ZIP = ROOT.parent / "Datathon 2026 - Round 1 - Materials" / "Urban_Flow_Analytics_Dataset_csv.zip"
OUT = ROOT / "data"
SAMPLE_PCT = 5

COLS = {
    "provider_code": "INTEGER", "pickup_timestamp": "TIMESTAMP", "dropoff_timestamp": "TIMESTAMP",
    "rider_count": "DOUBLE", "distance_miles": "DOUBLE", "rate_class_id": "DOUBLE",
    "offline_record_flag": "VARCHAR", "origin_loc_id": "INTEGER", "dest_loc_id": "INTEGER",
    "fare_settlement_method": "INTEGER", "base_fare": "DOUBLE", "surcharge_misc": "DOUBLE",
    "transit_tax": "DOUBLE", "driver_tip_payment": "DOUBLE", "toll_total": "DOUBLE",
    "service_improvement_fee": "DOUBLE", "charge_total": "DOUBLE", "zone_congestion_fee": "DOUBLE",
    "Airport_fee": "DOUBLE", "congestion_relief_fee": "DOUBLE",
}

# Single source of truth for the anomaly rules (also imported by the notebook).
DUR_MIN = "(epoch(dropoff_timestamp) - epoch(pickup_timestamp)) / 60.0"
# Same thresholds as features.flag_anomalies (the row-level version used for modelling).
FLAGS = {
    "neg_fare":            "base_fare < 0 OR charge_total < 0",
    "zero_fare":           "base_fare = 0",
    "zero_dist_fare":      "distance_miles = 0 AND base_fare > 0",
    "zero_riders":         "rider_count = 0",
    "null_block":          "rider_count IS NULL",
    "dropoff_before_pu":   "dropoff_timestamp < pickup_timestamp",
    "sub_minute":          f"{DUR_MIN} >= 0 AND {DUR_MIN} < 1",
    "helix_zero_duration": "provider_code = 7 AND dropoff_timestamp = pickup_timestamp",
    "over_3h":             f"{DUR_MIN} > 180",
    "unrealistic_speed":   f"{DUR_MIN} > 0 AND distance_miles / ({DUR_MIN}/60) > 80",
    "extreme_distance":    "distance_miles > 100",
    "extreme_fare":        "base_fare > 500",
    "out_of_period":       "pickup_timestamp < TIMESTAMP '{start}' OR pickup_timestamp >= TIMESTAMP '{end}'",
    "unknown_zone":        "origin_loc_id IN (264, 265) OR dest_loc_id IN (264, 265)",
    "rate_99":             "rate_class_id = 99",
    "offline_Y":           "offline_record_flag = 'Y'",
}
# Rows used for modelling (fare & duration targets): valid trips only (rules justified in the notebook).
CLEAN = """base_fare > 0 AND charge_total > 0 AND distance_miles > 0 AND distance_miles <= 100
  AND dropoff_timestamp > pickup_timestamp AND {dur} BETWEEN 1 AND 180
  AND distance_miles / ({dur}/60) <= 80
  AND pickup_timestamp >= TIMESTAMP '{start}' AND pickup_timestamp < TIMESTAMP '{end}'""".replace("{dur}", DUR_MIN)
# Demand counts every genuine pickup: meter-distance faults still mean a passenger was served,
# but refunds/reversals (fare <= 0), instant voids and out-of-period stamps are not pickups.
DEMAND = """base_fare > 0 AND dropoff_timestamp >= pickup_timestamp
  AND NOT (distance_miles = 0 AND {dur} < 1)
  AND pickup_timestamp >= TIMESTAMP '{start}' AND pickup_timestamp < TIMESTAMP '{end}'""".replace("{dur}", DUR_MIN)


def month_bounds(m):
    y, mo = map(int, m.split("-"))
    ny, nmo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return f"{y}-{mo:02d}-01", f"{ny}-{nmo:02d}-01"


def process(member):
    m = member.split("_")[-1].removesuffix(".csv")
    start, end = month_bounds(m)
    con = duckdb.connect()
    con.execute("SET memory_limit='8GB'; SET threads=8")
    proc = subprocess.Popen(["unzip", "-p", str(RAW_ZIP), member], stdout=subprocess.PIPE)
    types = ", ".join(f"'{k}': '{v}'" for k, v in COLS.items())
    fd = proc.stdout.fileno()  # read unzip's pipe directly
    con.execute(f"""CREATE TABLE t AS SELECT * FROM read_csv('/dev/fd/{fd}', header=true,
                    columns={{{types}}}, ignore_errors=true)""")
    proc.wait()
    clean = CLEAN.format(start=start, end=end)
    demand = DEMAND.format(start=start, end=end)

    flag_sql = ", ".join(f"sum(CASE WHEN {v.format(start=start, end=end)} THEN 1 ELSE 0 END) AS {k}" for k, v in FLAGS.items())
    null_sql = ", ".join(f"sum(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) AS null_{c}" for c in COLS)
    con.execute(f"""COPY (SELECT '{m}' AS month, count(*) AS total_rows,
                    sum(CASE WHEN {clean} THEN 1 ELSE 0 END) AS clean_rows,
                    sum(CASE WHEN {demand} THEN 1 ELSE 0 END) AS demand_rows, {flag_sql}, {null_sql} FROM t)
                    TO '{OUT}/quality/{m}.parquet'""")

    con.execute(f"""COPY (SELECT date_trunc('hour', pickup_timestamp) AS hour, origin_loc_id AS loc_id, count(*) AS pickups
                    FROM t WHERE {demand} GROUP BY ALL) TO '{OUT}/agg/hourly_{m}.parquet'""")

    con.execute(f"""COPY (SELECT origin_loc_id, dest_loc_id,
                    CASE WHEN hour(pickup_timestamp) BETWEEN 6 AND 9 THEN 'morning_peak'
                         WHEN hour(pickup_timestamp) BETWEEN 10 AND 15 THEN 'midday'
                         WHEN hour(pickup_timestamp) BETWEEN 16 AND 19 THEN 'evening_peak'
                         WHEN hour(pickup_timestamp) BETWEEN 20 AND 23 THEN 'night'
                         ELSE 'late_night' END AS band,
                    isodow(pickup_timestamp) >= 6 AS weekend,
                    count(*) AS trips, avg(distance_miles) AS avg_dist, avg(base_fare) AS avg_fare
                    FROM t WHERE {demand} GROUP BY ALL) TO '{OUT}/agg/od_{m}.parquet'""")

    con.execute(f"""COPY (SELECT CAST(pickup_timestamp AS DATE) AS day, hour(pickup_timestamp) AS hr,
                    origin_loc_id AS loc_id, fare_settlement_method AS pay,
                    count(*) AS trips, sum(charge_total) AS revenue, sum(base_fare) AS base_fare,
                    sum(driver_tip_payment) AS tips, sum(distance_miles) AS miles,
                    sum({DUR_MIN}) AS minutes, sum(CASE WHEN driver_tip_payment > 0 THEN 1 ELSE 0 END) AS tipped
                    FROM t WHERE {clean} GROUP BY ALL) TO '{OUT}/agg/daily_{m}.parquet'""")

    con.execute(f"""COPY (SELECT * FROM t USING SAMPLE {SAMPLE_PCT} PERCENT (bernoulli, 42))
                    TO '{OUT}/sample/{m}.parquet' (COMPRESSION zstd)""")
    n = con.execute("SELECT count(*) FROM t").fetchone()[0]
    print(m, f"{n:,} rows", flush=True)
    con.close()


if __name__ == "__main__":
    for d in ("quality", "agg", "sample"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    members = sorted(n for n in zipfile.ZipFile(RAW_ZIP).namelist()
                     if n.endswith(".csv") and not n.startswith("__MACOSX"))
    only = sys.argv[1:]
    for mem in members:
        if not only or any(o in mem for o in only):
            process(mem)
