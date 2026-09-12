"""Safe SQL execution for the mobility assistant.

Defence in depth - any one layer is enough to stop a harmful query:
  1. the database is opened READ-ONLY with external file/network access disabled and the config locked;
  2. exactly one statement is allowed and DuckDB's own parser must classify it as SELECT;
  3. a deny-list rejects table functions / keywords that reach outside the warehouse (files, extensions, settings);
  4. results are capped at MAX_ROWS and every query has a wall-clock timeout.
"""
import re
import threading
from pathlib import Path
import duckdb

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "warehouse.duckdb"
MAX_ROWS = 200
TIMEOUT_S = 15
DENY = re.compile(
    r"\b(read_csv\w*|read_parquet|read_json\w*|read_text|read_blob|parquet_scan|csv_scan|glob|sniff_csv|"
    r"copy|attach|detach|install|load|pragma|set|reset|export|import|call|checkpoint|vacuum|"
    r"create|insert|update|delete|drop|alter|truncate|replace|grant|merge|getenv|current_setting|duckdb_\w+)\b",
    re.IGNORECASE)


class UnsafeQuery(ValueError):
    pass


def connect():
    con = duckdb.connect(str(DB_PATH), read_only=True, config={"enable_external_access": False})
    con.execute("SET enable_progress_bar = false")
    if not con.execute("SELECT current_setting('lock_configuration')").fetchone()[0]:
        con.execute("SET lock_configuration = true")   # DuckDB shares one instance per file, so later connections are already locked
    return con


def check(sql: str) -> str:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise UnsafeQuery("empty query")
    try:
        stmts = duckdb.extract_statements(sql)
    except duckdb.Error as e:
        raise UnsafeQuery(f"could not parse SQL: {e}") from None
    if len(stmts) != 1:
        raise UnsafeQuery("only one statement is allowed")
    if stmts[0].type != duckdb.StatementType.SELECT:
        raise UnsafeQuery(f"only read-only SELECT queries are allowed (got {stmts[0].type.name})")
    # strip string literals before the keyword scan so e.g. WHERE zone = 'Union Sq' is not flagged
    hit = DENY.search(re.sub(r"'(?:[^']|'')*'", "''", sql))
    if hit:
        raise UnsafeQuery(f"'{hit.group(0)}' is not allowed in assistant queries")
    return sql


def run(con, sql: str):
    """Validate and execute. Returns (columns, rows, truncated)."""
    sql = check(sql)
    timer = threading.Timer(TIMEOUT_S, con.interrupt)
    timer.start()
    try:
        cur = con.execute(f"SELECT * FROM ({sql}) AS q LIMIT {MAX_ROWS + 1}")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    except duckdb.InterruptException:
        raise UnsafeQuery(f"query exceeded the {TIMEOUT_S}s time limit - try a narrower question") from None
    finally:
        timer.cancel()
    return cols, rows[:MAX_ROWS], len(rows) > MAX_ROWS
