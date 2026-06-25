# WMS SQL Server Tooling

A set of Python tools for working with a Microsoft SQL Server-backed
Warehouse Management System (WMS): reusable connection helpers, a local
MCP server for live querying, a file-drop query watcher, a two-environment
schema/data comparison report, and a codebase-audit suite that documents
stored procedures, tables, and their cross-references.

## Structure

```
wmspython/
├── wms_connect.py           # SQLAlchemy connection helper (read-only query + transactional DML)
├── cloud_connect.py         # Same helper pointed at a second (cloud) environment
├── wms_mcp_server.py        # Local MCP server exposing the DB to an AI assistant
├── cloud_mcp_server.py      # MCP server for the cloud environment
├── wms_watcher.py           # Watches a folder, auto-runs dropped .sql files, writes results
├── wms_compare.py           # Compares two environments and emits an HTML diff report
├── diag_server_compare.py   # Lightweight server-to-server comparison
├── diag_utils.py            # Output tee/retention helpers for diagnostics
├── codebase_audit_*.py      # Audit pipeline: scans objects and builds documentation artifacts
├── build_scorecard.py       # Reporting / scorecard generation
├── rebuild_productivity_scorecard.py
└── tests/
    └── test_parsers.py      # Unit tests
```

## Highlights

- **Safe-by-default reads** — query helpers and the watcher default to
  read-only; mutating SQL requires an explicit opt-in flag.
- **Managed transactions** — `run_dml()` commits on clean exit and rolls back
  on exception, returning a DataFrame per result set.
- **No secrets in source** — connection targets and credentials are read from
  environment variables (or a local `.env`).
- **Tested parsing logic** — see `tests/`.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows  (use: source .venv/bin/activate on Linux/macOS)
pip install sqlalchemy pyodbc pandas python-dotenv

cp .env.example .env          # then fill in real values
```

Requires the **ODBC Driver 18 for SQL Server**.

## Usage

```bash
# Smoke-test the connection
python wms_connect.py

# Compare two environments and write an HTML diff report
python wms_compare.py
```

> Note: This is a sanitized portfolio version. Hostnames, IPs, database
> names, logins, and credentials have been removed or replaced with
> placeholders.
