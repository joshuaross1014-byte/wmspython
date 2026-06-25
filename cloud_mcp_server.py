# ------------------------------------------------------------
# cloud_mcp_server.py
# Purpose : Local stdio MCP server giving an MCP-capable AI assistant
#           direct access to the CLOUD WMS server database (<CLOUD_DB_SERVER>).
# Reuses  : cloud_connect.get_engine() -- same ODBC Driver 18 setup,
#           same WMS_USER / WMS_PASS credentials as the primary server,
#           but a different SERVER. The existing prod/test server files
#           are untouched.
# Access  : READ-WRITE, mapped exactly like the primary `wms` server.
#           run_sql() commits DML/DDL straight to CLOUD. There is NO
#           DB-layer safety net -- a confirm-before-write rule (explicit
#           confirmation before any write) is the only guardrail.
# Run     : python cloud_mcp_server.py   (launched by the MCP client)
# Created : 2026-06-22 (JR)
# ------------------------------------------------------------

import os
import sys
import decimal
import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

# cloud_connect lives in this same directory (sys.path[0] is the script dir).
from cloud_connect import get_engine, SERVER, DATABASE

# Fail fast if creds are missing rather than hanging on input()/getpass().
if not (os.getenv("WMS_USER") and os.getenv("WMS_PASS")):
    sys.stderr.write(
        "FATAL: WMS_USER / WMS_PASS environment variables are not set. "
        "Set them at the User level before launching the MCP server.\n"
    )
    sys.exit(1)

mcp = FastMCP("cloud")

DEFAULT_MAX_ROWS = 1000


def _jsonable(value: Any) -> Any:
    """Coerce DB values into JSON-serializable Python types."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


@mcp.tool()
def run_sql(sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> dict:
    """Execute SQL against the CLOUD WMS database and return results.

    READ-WRITE against CLOUD (server <CLOUD_DB_SERVER>). The statement runs inside a
    transaction that COMMITS on success and ROLLS BACK on error. SELECTs return
    rows; DML/DDL returns affected row counts. Handles multi-result-set batches.

    Conventions:
      - Add WITH (NOLOCK) to dbo.* reads as a habit.
      - This server commits writes directly. Confirm with the user before ANY
        INSERT/UPDATE/DELETE/MERGE/DDL/EXEC (confirm-before-write safety rule).

    Args:
        sql: The T-SQL statement or batch to execute.
        max_rows: Cap on rows returned per result set (default 1000).

    Returns:
        dict with server/database and a list of result_sets.
    """
    engine = get_engine()
    out: dict = {"server": SERVER, "database": DATABASE, "result_sets": []}

    with engine.begin() as conn:
        raw = conn.connection.dbapi_connection
        cur = raw.cursor()
        try:
            cur.execute(sql)
            while True:
                if cur.description is not None:
                    cols = [d[0] for d in cur.description]
                    fetched = cur.fetchall()
                    total = len(fetched)
                    limited = fetched[:max_rows]
                    rows = [
                        {c: _jsonable(v) for c, v in zip(cols, row)}
                        for row in limited
                    ]
                    out["result_sets"].append({
                        "type": "rows",
                        "columns": cols,
                        "row_count": total,
                        "rows": rows,
                        "truncated": total > max_rows,
                    })
                else:
                    out["result_sets"].append({
                        "type": "statement",
                        "affected_rows": cur.rowcount,
                    })
                if not cur.nextset():
                    break
        finally:
            cur.close()

    if not out["result_sets"]:
        out["result_sets"].append({"type": "statement", "affected_rows": 0})
    return out


@mcp.tool()
def list_tables(schema: str = "dbo", name_like: str = "") -> dict:
    """List tables in the CLOUD database, optionally filtered by a name pattern.

    Args:
        schema: Schema to list (default 'dbo').
        name_like: Optional substring; matches table names case-insensitively.
    """
    sql = """
        SELECT s.name AS [schema], t.name AS [table], t.create_date, t.modify_date
        FROM sys.tables t WITH (NOLOCK)
        JOIN sys.schemas s WITH (NOLOCK) ON s.schema_id = t.schema_id
        WHERE s.name = ?
          AND (? = '' OR t.name LIKE '%' + ? + '%')
        ORDER BY t.name;
    """
    engine = get_engine()
    with engine.connect() as conn:
        raw = conn.connection.dbapi_connection
        cur = raw.cursor()
        try:
            cur.execute(sql, (schema, name_like, name_like))
            cols = [d[0] for d in cur.description]
            rows = [{c: _jsonable(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
        finally:
            cur.close()
    return {"schema": schema, "count": len(rows), "tables": rows}


@mcp.tool()
def describe_table(table: str, schema: str = "dbo") -> dict:
    """Return column definitions (name, type, nullability, length) for a table."""
    sql = """
        SELECT c.COLUMN_NAME, c.DATA_TYPE, c.CHARACTER_MAXIMUM_LENGTH,
               c.NUMERIC_PRECISION, c.NUMERIC_SCALE, c.IS_NULLABLE, c.ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.COLUMNS c WITH (NOLOCK)
        WHERE c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ?
        ORDER BY c.ORDINAL_POSITION;
    """
    engine = get_engine()
    with engine.connect() as conn:
        raw = conn.connection.dbapi_connection
        cur = raw.cursor()
        try:
            cur.execute(sql, (schema, table))
            cols = [d[0] for d in cur.description]
            rows = [{c: _jsonable(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
        finally:
            cur.close()
    return {"schema": schema, "table": table, "columns": rows}


@mcp.tool()
def get_object_definition(name: str) -> dict:
    """Return the T-SQL definition of a stored procedure, view, function, or trigger
    from sys.sql_modules. Accepts bare or schema-qualified names."""
    sql = """
        SELECT OBJECT_SCHEMA_NAME(m.object_id) AS [schema],
               OBJECT_NAME(m.object_id)        AS [name],
               o.type_desc,
               m.definition
        FROM sys.sql_modules m WITH (NOLOCK)
        JOIN sys.objects o WITH (NOLOCK) ON o.object_id = m.object_id
        WHERE m.object_id = OBJECT_ID(?);
    """
    engine = get_engine()
    with engine.connect() as conn:
        raw = conn.connection.dbapi_connection
        cur = raw.cursor()
        try:
            cur.execute(sql, (name,))
            row = cur.fetchone()
            if row is None:
                return {"found": False, "name": name}
            cols = [d[0] for d in cur.description]
            rec = {c: _jsonable(v) for c, v in zip(cols, row)}
        finally:
            cur.close()
    rec["found"] = True
    return rec


@mcp.tool()
def test_connection() -> dict:
    """Confirm connectivity to CLOUD. Returns the server name, current
    database, the authenticated login, and server time."""
    engine = get_engine()
    with engine.connect() as conn:
        raw = conn.connection.dbapi_connection
        cur = raw.cursor()
        try:
            cur.execute(
                "SELECT @@SERVERNAME, DB_NAME(), SUSER_SNAME(), SYSDATETIME();"
            )
            srv, db, login, now = cur.fetchone()
        finally:
            cur.close()
    return {
        "server_name": _jsonable(srv),
        "database_name": _jsonable(db),
        "login_name": _jsonable(login),
        "server_time": _jsonable(now),
        "configured_server": SERVER,
    }


if __name__ == "__main__":
    mcp.run()   # stdio transport
