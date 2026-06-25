# ------------------------------------------------------------
# cloud_connect.py
# Purpose : Connection helper for a second (cloud) WMS server, mapped
#           exactly like wms_connect.py but pointed at a different host.
#           Demonstrates running the same tooling against multiple
#           environments without touching the primary connector.
# Notes   : Separate file by design — keeps wms_connect.py untouched.
#           Connection target and credentials come from environment
#           variables (CLOUD_DB_SERVER, CLOUD_DB_NAME, WMS_USER, WMS_PASS)
#           so nothing sensitive is stored in source.
# ------------------------------------------------------------

import os
import urllib.parse
import pandas as pd
from getpass import getpass
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Load wmspython\.env if present (same file the prod connector uses).
# override=False keeps any variable already set in the real environment.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
                override=False)
except ImportError:
    pass

SERVER   = os.getenv("CLOUD_DB_SERVER", "localhost")  # cloud database server host/IP
DATABASE = os.getenv("CLOUD_DB_NAME", "WMS")          # target database name
DRIVER   = "ODBC Driver 18 for SQL Server"


def get_engine() -> Engine:
    """Return a SQLAlchemy engine connected to the cloud WMS database."""
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
    """Execute a read-only query against the CLOUD DB and return a DataFrame."""
    engine = get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


if __name__ == "__main__":
    test_sql = """
        SELECT TOP 5 name AS table_name, create_date, modify_date
        FROM sys.tables WITH (NOLOCK)
        ORDER BY modify_date DESC;
    """
    df = run_query(test_sql)
    print(f"CLOUD connection OK ({SERVER}/{DATABASE}). Sample result:\n")
    print(df.to_string(index=False))
