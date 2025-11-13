#!/usr/bin/env python3
"""Convenience wrapper so `python3 start.py` also works from the supabase/ folder."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
START_SCRIPT = ROOT_DIR / "start.py"

if not START_SCRIPT.exists():
    sys.stderr.write(
        "Unable to locate the project start.py script. "
        "Expected at {}\n".format(START_SCRIPT)
    )
    sys.exit(1)

# Execute the real starter while preserving CLI arguments
os.chdir(ROOT_DIR)
sys.argv[0] = str(START_SCRIPT)
runpy.run_path(str(START_SCRIPT), run_name="__main__")
