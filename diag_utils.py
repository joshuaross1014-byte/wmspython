# ------------------------------------------------------------
# diag_utils.py
# Purpose : Shared utilities for diagnostic scripts in wmspython.
#           Primary use: tee_output() writes all stdout to both
#           the terminal AND a timestamped .txt file in
#           wmspython\diag_output\ so results can be reviewed
#           after the run (and ingested by tooling without copy-paste).
# Author  : Joshua Ross
# Created : 2026-05-14
# ------------------------------------------------------------

import sys
import os
import time
from contextlib import contextmanager
from datetime import datetime

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "diag_output")
RETENTION_DAYS = 30


def _prune_old_output(days: int = RETENTION_DAYS) -> None:
    """Remove diag_output files older than `days` days."""
    if not os.path.isdir(OUTPUT_DIR):
        return
    cutoff = time.time() - (days * 86400)
    for name in os.listdir(OUTPUT_DIR):
        path = os.path.join(OUTPUT_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass  # ignore files in use or permission issues


class _Tee:
    """Write to multiple streams (e.g., stdout + file) simultaneously."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


@contextmanager
def tee_output(script_name: str):
    """Context manager that mirrors print() output to a timestamped file.

    Usage:
        from diag_utils import tee_output
        with tee_output("my_diagnostic"):
            print(...)   # goes to terminal AND diag_output\<name>_<ts>.txt
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    _prune_old_output()   # keep only the last RETENTION_DAYS days
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUTPUT_DIR, f"{script_name}_{ts}.txt")

    original_stdout = sys.stdout
    f = open(out_path, "w", encoding="utf-8")
    sys.stdout = _Tee(original_stdout, f)
    try:
        print(f"[diag run started {datetime.now():%Y-%m-%d %H:%M:%S}]")
        print(f"[output mirrored to: {out_path}]\n")
        yield out_path
    finally:
        print(f"\n[diag run completed {datetime.now():%Y-%m-%d %H:%M:%S}]")
        sys.stdout = original_stdout
        f.close()
        print(f"\nOutput saved to: {out_path}")
