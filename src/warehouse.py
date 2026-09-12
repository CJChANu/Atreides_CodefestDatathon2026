"""Build data/warehouse.duckdb - the analytics store behind the dashboard (Track 6) and the assistant (Track 5).

Tables use plain-English names and are documented in SCHEMA_DOC (also given to the assistant).
Usage: python src/warehouse.py
"""
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "warehouse.duckdb"
ZONES_CSV = ROOT.parent / "Datathon 2026 - Round 1 - Materials" / "Urban_Flow_Analytics_Zone_Dataset.csv"

PAYMENT_TYPES = [(0, "Flex Fare"), (1, "Credit card"), (2, "Cash"), (3, "No charge"), (4, "Dispute"), (5, "Unknown"), (6, "Voided")]
PROVIDERS = [(1, "Creative Mobile Technologies"), (2, "Curb Mobility"), (6, "Myle Technologies"), (7, "Helix")]

SCHEMA_DOC = """
Data covers yellow-taxi trips from 2025-04-01 to 2026-03-31 (48.6 M raw records). Money is in US dollars.

zones(loc_id, borough, zone, service_zone, archetype)
    265 taxi zones. archetype = behavioural cluster (e.g. 'Nightlife districts'); NULL for low-activity zones.
payment_types(payment_code, payment_type)      0 Flex Fare, 1 Credit card, 2 Cash, 3 No charge, 4 Dispute, 5 Unknown, 6 Voided
providers(provider_code, provider)

trip_stats(day DATE, hour INT 0-23, pickup_loc_id, payment_code, trips, revenue, base_fare, tips, miles, meter_minutes, tipped_trips)
    ALL 44.1 M valid trips aggregated by pickup day, hour, pickup zone and payment type.
    revenue = total amount billed to riders (excludes cash tips). tips = card tips only.
    Use this table for trip counts, revenue, tips, averages per trip (SUM(revenue)/SUM(trips)), revenue per meter-hour
    (SUM(revenue)/SUM(meter_minutes)*60), average speed (SUM(miles)/SUM(meter_minutes)*60), tip rate (SUM(tips)/SUM(base_fare)).
pickups_hourly(hour TIMESTAMP, loc_id, pickups)
    ALL genuine pickups (45.8 M, includes trips with a faulty odometer) per zone per hour. Use for demand / busiest questions.
od_flows(origin_loc_id, dest_loc_id, time_band, is_weekend, trips, avg_miles, avg_base_fare)
    Origin-destination trip counts. time_band in ('morning_peak' 06-10, 'midday' 10-16, 'evening_peak' 16-20, 'night' 20-24, 'late_night' 00-06).
ledger(month VARCHAR 'YYYY-MM', provider_code, payment_code, row_class, n_rows, base_fare, charge_total, tips)
    EVERY raw record classified: row_class in ('valid','reversal','zero_fare','other_invalid'). Use for data-quality and refund questions.
trips_sample(pickup_time, dropoff_time, pickup_loc_id, dropoff_loc_id, provider_code, payment_code, riders, miles, duration_min, base_fare, total_amount, tip)
    5 % random sample of valid trips (2.2 M rows) - use ONLY for per-trip distributions (medians, percentiles, longest trips).
    Counts or sums from this table must be multiplied by 20 to estimate the full population; say so when you do.
""".strip()


def build():
    DB.unlink(missing_ok=True)
    con = duckdb.connect(str(DB))
    agg = ROOT / "data" / "agg"
    con.execute(f"""CREATE TABLE zones AS SELECT z.loc_id, z.borough_name AS borough, z.zone_name AS zone, z.service_zone, a.archetype
                    FROM read_csv('{ZONES_CSV}') z
                    LEFT JOIN (SELECT loc_id, archetype FROM read_csv('{ROOT}/reports/zone_archetypes.csv')) a USING (loc_id)""")
    con.execute("CREATE TABLE payment_types(payment_code INT, payment_type VARCHAR)")
    con.executemany("INSERT INTO payment_types VALUES (?, ?)", PAYMENT_TYPES)
    con.execute("CREATE TABLE providers(provider_code INT, provider VARCHAR)")
    con.executemany("INSERT INTO providers VALUES (?, ?)", PROVIDERS)
    con.execute(f"""CREATE TABLE trip_stats AS SELECT day, hr::INT AS hour, loc_id AS pickup_loc_id, pay AS payment_code,
                    trips::BIGINT AS trips, revenue, base_fare, tips, miles, minutes AS meter_minutes, tipped::BIGINT AS tipped_trips
                    FROM '{agg}/daily_*.parquet'""")
    con.execute(f"CREATE TABLE pickups_hourly AS SELECT hour, loc_id, pickups::BIGINT AS pickups FROM '{agg}/hourly_*.parquet'")
    con.execute(f"""CREATE TABLE od_flows AS SELECT origin_loc_id, dest_loc_id, band AS time_band, weekend AS is_weekend,
                    sum(trips)::BIGINT AS trips, sum(avg_dist * trips) / sum(trips) AS avg_miles, sum(avg_fare * trips) / sum(trips) AS avg_base_fare
                    FROM '{agg}/od_*.parquet' GROUP BY ALL""")
    con.execute(f"""CREATE TABLE ledger AS SELECT month, provider_code, pay AS payment_code, row_class, n_rows::BIGINT AS n_rows,
                    base_fare, charge_total, tips FROM '{agg}/ledger_*.parquet'""")
    con.execute(f"""CREATE TABLE trips_sample AS SELECT pickup_timestamp AS pickup_time, dropoff_timestamp AS dropoff_time,
                    origin_loc_id AS pickup_loc_id, dest_loc_id AS dropoff_loc_id, provider_code, fare_settlement_method AS payment_code,
                    coalesce(rider_count, 1)::INT AS riders, distance_miles AS miles, duration_min, base_fare, charge_total AS total_amount,
                    driver_tip_payment AS tip
                    FROM read_parquet(['{ROOT}/data/splits/trips_train.parquet', '{ROOT}/data/splits/trips_val.parquet',
                                       '{ROOT}/data/splits/trips_test.parquet'], union_by_name=true)""")
    for t in ["zones", "trip_stats", "pickups_hourly", "od_flows", "ledger", "trips_sample"]:
        print(f"{t:15s} {con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]:>12,}")
    con.close()


if __name__ == "__main__":
    build()
    print(f"{DB} {DB.stat().st_size / 1e6:.0f} MB")
