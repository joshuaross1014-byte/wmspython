# ------------------------------------------------------------
# wms_connect.py
# Purpose : Reusable SQLAlchemy connection helper for a SQL Server
#           database, with read-only query and transactional DML helpers.
# Notes   : Connection target and credentials are read from environment
#           variables (or a local .env via python-dotenv) so that no
#           hostnames, usernames, or passwords are stored in source.
#           Set DB_SERVER, DB_NAME, WMS_USER, and WMS_PASS in your shell
#           or a local .env file before running.
#           load_dotenv(override=False) means any already-set environment
#           variable still wins. No-ops if python-dotenv isn't installed.
# ------------------------------------------------------------

import os
import urllib.parse
import pandas as pd
from getpass import getpass
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# JR 2026-06-02: Load wmspython\.env if present. override=False keeps any
# variable already set in the real environment as the source of truth.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
                override=False)
except ImportError:
    pass

SERVER   = os.getenv("DB_SERVER", "localhost")   # database server host/IP
DATABASE = os.getenv("DB_NAME", "WMS")           # target database name
DRIVER   = "ODBC Driver 18 for SQL Server"


def get_engine() -> Engine:
    """Return a SQLAlchemy engine connected to the WMS database."""
    user = os.getenv("WMS_USER") or input("WMS username: ")
    pwd  = os.getenv("WMS_PASS") or getpass("WMS password: ")

    odbc_str = (
        f"Driver={{{DRIVER}}};"
        f"Server={SERVER};"
        f"Database={DATABASE};"
        f"UID={user};"
        f"PWD={pwd};"
        "TrustServerCertificate=yes;"
        "Encrypt=yes;"
    )
    url = "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc_str)
    return create_engine(url, fast_executemany=True)


def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Execute a read-only query and return a pandas DataFrame.

    Use :param_name placeholders in SQL and pass a dict, e.g.:
        run_query("SELECT * FROM t WHERE id = :id WITH (NOLOCK)", {"id": 123})
    """
    engine = get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


# JR 2026-05-15: Added run_dml() so the watcher can execute DML scripts
# inside a managed transaction. engine.begin() commits on clean exit and
# rolls back on exception. Multi-result-set fetch is preserved.
def run_dml(sql: str, params: dict | None = None) -> list[pd.DataFrame]:
    """Execute a script containing DML/DDL inside a managed transaction.

    SQLAlchemy COMMITS on clean exit, ROLLBACKS on exception. Returns one
    DataFrame per SELECT result set in the batch (empty list if none).
    """
    engine = get_engine()
    results: list[pd.DataFrame] = []
    with engine.begin() as conn:                  # auto-commit boundary
        raw = conn.connection.dbapi_connection    # underlying pyodbc connection
        cur = raw.cursor()
        try:
            cur.execute(sql, params or ())
            while True:
                if cur.description is not None:
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                    results.append(pd.DataFrame.from_records(rows, columns=cols))
                if not cur.nextset():
                    break
        finally:
            cur.close()
    return results


# ------------------------------------------------------------
# Quick smoke test - run this file directly to verify connectivity
# ------------------------------------------------------------
if __name__ == "__main__":
    test_sql = """
        SELECT TOP 5
            name        AS table_name,
            create_date,
            modify_date
        FROM sys.tables WITH (NOLOCK)
        ORDER BY modify_date DESC;
    """
    df = run_query(test_sql)
    print("Connection OK. Sample result:\n")
    print(df.to_string(index=False))
