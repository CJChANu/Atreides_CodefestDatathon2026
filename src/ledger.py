"""Full-population money ledger: every raw row classified (valid / reversal / zero fare / invalid) with $ totals.

Feeds the business dashboard (Track 6) and the mobility assistant (Track 5).
Output: data/agg/ledger_<month>.parquet     Usage: python src/ledger.py
"""
import subprocess, zipfile
import duckdb
from ingest import RAW_ZIP, OUT, COLS, CLEAN, month_bounds


def process(member):
    m = member.split("_")[-1].removesuffix(".csv")
    start, end = month_bounds(m)
    con = duckdb.connect()
    proc = subprocess.Popen(["unzip", "-p", str(RAW_ZIP), member], stdout=subprocess.PIPE)
    types = ", ".join(f"'{k}': '{v}'" for k, v in COLS.items())
    con.execute(f"""CREATE TABLE t AS SELECT * FROM read_csv('/dev/fd/{proc.stdout.fileno()}', header=true,
                    columns={{{types}}}, ignore_errors=true)""")
    proc.wait()
    clean = CLEAN.format(start=start, end=end)
    con.execute(f"""COPY (SELECT '{m}' AS month, provider_code, fare_settlement_method AS pay,
                    CASE WHEN base_fare < 0 OR charge_total < 0 THEN 'reversal'
                         WHEN base_fare = 0 THEN 'zero_fare'
                         WHEN {clean} THEN 'valid'
                         ELSE 'other_invalid' END AS row_class,
                    count(*) AS n_rows, sum(base_fare) AS base_fare, sum(charge_total) AS charge_total,
                    sum(driver_tip_payment) AS tips
                    FROM t GROUP BY ALL) TO '{OUT}/agg/ledger_{m}.parquet'""")
    print(m, flush=True)


if __name__ == "__main__":
    for mem in sorted(n for n in zipfile.ZipFile(RAW_ZIP).namelist()
                      if n.endswith(".csv") and not n.startswith("__MACOSX")):
        process(mem)
