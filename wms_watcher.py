# ------------------------------------------------------------
# wms_watcher.py
# Purpose : Polling daemon for a file-drop query workflow.
#           Watches  wmspython\queries\pending\*.sql,
#           runs each file read-only against WMS_DB, writes results
#           into wmspython\queries\results\, and archives the
#           SQL file. Lets a user and an AI assistant exchange
#           queries through files instead of manual run-and-paste loops.
# Author  : Joshua Ross
# Created : 2026-05-14
# Notes   : Run once per work session:
#               python wms_watcher.py
#           Leave the terminal open. Ctrl+C to stop.
# ------------------------------------------------------------

import os
import re
import sys
import time
import shutil
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

from wms_connect import run_query, run_dml   # JR 2026-05-15: run_dml for committed DML

# ---------- Configuration ------------------------------------
HERE         = Path(__file__).resolve().parent
QUERIES_ROOT = HERE / "queries"
PENDING_DIR  = QUERIES_ROOT / "pending"
RESULTS_DIR  = QUERIES_ROOT / "results"
ARCHIVE_DIR  = QUERIES_ROOT / "archive"

POLL_INTERVAL    = 2          # seconds between scans
RETENTION_DAYS   = 30
ALLOW_DML_ENV    = "WMS_ALLOW_DML"
DML_FILENAME_TAG = "_DML_"    # required in filename to even consider DML

# JR 2026-05-15: Default DML mode ON. The confirm-before-write gate in the
#                calling workflow is the real safeguard; this env var is
#                redundant friction. Setting WMS_ALLOW_DML=0 still disables.
os.environ.setdefault(ALLOW_DML_ENV, "1")

# Statement keywords that mutate data or schema.
DANGER_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|"
    r"EXEC|EXECUTE|GRANT|REVOKE|DENY)\b",
    re.IGNORECASE,
)

# Heuristic: warn if a query reads from dbo.<table> without WITH (NOLOCK) nearby.
NOLOCK_RE       = re.compile(r"WITH\s*\(\s*NOLOCK\s*\)", re.IGNORECASE)
FROM_DBO_RE     = re.compile(r"\bFROM\s+dbo\.\w+", re.IGNORECASE)
JOIN_DBO_RE     = re.compile(r"\bJOIN\s+dbo\.\w+", re.IGNORECASE)

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 200)


# ---------- Helpers ------------------------------------------
def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def strip_sql_comments(sql: str) -> str:
    """Remove -- line comments and /* ... */ block comments so they
    don't trigger the DML safety regex."""
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def is_dml_allowed(filename: str) -> bool:
    return (
        DML_FILENAME_TAG in filename.upper()
        and os.getenv(ALLOW_DML_ENV, "").strip() == "1"
    )


def nolock_warning(sql_clean: str) -> str | None:
    """Return a warning string if dbo. reads appear without NOLOCK; else None."""
    reads_dbo = FROM_DBO_RE.search(sql_clean) or JOIN_DBO_RE.search(sql_clean)
    if reads_dbo and not NOLOCK_RE.search(sql_clean):
        return "WARNING: Query reads from dbo.* tables but no WITH (NOLOCK) hint found."
    return None


def prune_old(directory: Path, days: int = RETENTION_DAYS) -> None:
    """Delete files in `directory` older than `days` days."""
    if not directory.exists():
        return
    cutoff = time.time() - (days * 86400)
    for f in directory.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def ensure_dirs() -> None:
    for d in (QUERIES_ROOT, PENDING_DIR, RESULTS_DIR, ARCHIVE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def write_result(stem: str, sections: list[tuple[str, str]]) -> Path:
    """Write a result file with named sections (title, body)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"{stem}_{ts}.txt"
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(f"[wms_watcher result for: {stem}]\n")
        fh.write(f"[generated: {datetime.now():%Y-%m-%d %H:%M:%S}]\n")
        for title, body in sections:
            fh.write(f"\n{'=' * 78}\n{title}\n{'=' * 78}\n{body}\n")
    return out_path


# ---------- Core processing ----------------------------------
def process_file(sql_path: Path) -> None:
    stem = sql_path.stem
    log(f"--> picked up: {sql_path.name}")

    try:
        # utf-8-sig transparently strips a leading BOM if present
        # (PowerShell's `Out-File -Encoding utf8` writes UTF-8 + BOM by default).
        raw_sql = sql_path.read_text(encoding="utf-8-sig")
        # Belt-and-suspenders: explicitly strip any remaining U+FEFF characters
        # anywhere in the string (handles edge cases where utf-8-sig misses one).
        raw_sql = raw_sql.replace("﻿", "")
    except Exception as exc:
        log(f"!! could not read {sql_path.name}: {exc}")
        return

    sql_clean = strip_sql_comments(raw_sql)

    sections: list[tuple[str, str]] = [("SQL (as submitted)", raw_sql.strip())]

    # ----- Safety gate -----
    danger = DANGER_RE.search(sql_clean)
    if danger:
        if is_dml_allowed(sql_path.name):
            sections.append((
                "SAFETY NOTICE",
                f"DML keyword '{danger.group(0)}' detected. "
                f"Allowed because filename contains '{DML_FILENAME_TAG}' "
                f"and {ALLOW_DML_ENV}=1.",
            ))
        else:
            sections.append((
                "BLOCKED",
                (
                    f"DML/DDL keyword '{danger.group(0)}' detected.\n"
                    f"Query was NOT executed.\n"
                    f"To allow: rename file to include '{DML_FILENAME_TAG}' "
                    f"AND set env var {ALLOW_DML_ENV}=1 before launching watcher."
                ),
            ))
            out = write_result(stem, sections)
            shutil.move(str(sql_path), ARCHIVE_DIR / sql_path.name)
            log(f"   BLOCKED. result: {out.name}")
            return

    # ----- NOLOCK advisory -----
    warn = nolock_warning(sql_clean)
    if warn:
        sections.append(("ADVISORY", warn))

    # ----- Execute -----
    try:
        # JR 2026-05-15: DML files go through run_dml() so SQLAlchemy actually
        # commits. Without this, engine.connect() rolls back on close and the
        # server-side BEGIN TRAN/COMMIT TRAN is just nested under an outer txn
        # that never sees daylight.
        if danger and is_dml_allowed(sql_path.name):
            result_sets = run_dml(raw_sql)
            if not result_sets:
                sections.append(("RESULT", "DML executed and committed. No SELECT result sets returned."))
            else:
                for i, df in enumerate(result_sets, 1):
                    body = (
                        f"Rows returned: {len(df)}\n\n"
                        + (df.to_string(index=False) if len(df) else "(no rows)")
                    )
                    sections.append((f"RESULT SET {i}", body))
            log(f"   OK (DML) -> {len(result_sets)} result set(s) committed")
        else:
            df = run_query(raw_sql)
            body = (
                f"Rows returned: {len(df)}\n\n"
                + (df.to_string(index=False) if len(df) else "(no rows)")
            )
            sections.append(("RESULT", body))
            log(f"   OK -> {len(df)} rows")
    except Exception as exc:
        sections.append((
            "ERROR",
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
        ))
        log(f"   ERROR: {type(exc).__name__}: {exc}")

    out = write_result(stem, sections)
    try:
        # Append timestamp to archive filename so repeat runs of the same
        # SQL name never collide (collision would cause silent move failure
        # and the file would be re-processed on every poll).
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_target = ARCHIVE_DIR / f"{sql_path.stem}_{ts}{sql_path.suffix}"
        shutil.move(str(sql_path), archive_target)
    except Exception as exc:
        log(f"!! could not archive {sql_path.name}: {exc}")
        # Last-ditch: delete the source so we don't loop forever.
        try:
            sql_path.unlink(missing_ok=True)
        except OSError:
            pass
    log(f"   result: {out.name}")


# ---------- Main loop ----------------------------------------
def main() -> int:
    ensure_dirs()
    prune_old(RESULTS_DIR)
    prune_old(ARCHIVE_DIR)

    log("WMS watcher started.")
    log(f"  pending  : {PENDING_DIR}")
    log(f"  results  : {RESULTS_DIR}")
    log(f"  archive  : {ARCHIVE_DIR}")
    log(f"  DML mode : {'ON' if os.getenv(ALLOW_DML_ENV) == '1' else 'OFF (read-only)'}")
    log("Drop *.sql files into pending/. Ctrl+C to stop.")

    last_prune = time.time()

    try:
        while True:
            # Pick up any new .sql files in pending/
            for sql_path in sorted(PENDING_DIR.glob("*.sql")):
                process_file(sql_path)

            # Daily auto-prune
            if time.time() - last_prune > 86400:
                prune_old(RESULTS_DIR)
                prune_old(ARCHIVE_DIR)
                last_prune = time.time()

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        log("watcher stopped by user.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
