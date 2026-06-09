"""Launch the interactive arbiter shell REPL."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _arbiter_script() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "arbiter"


def run() -> None:
    script = _arbiter_script()
    if not script.is_file():
        print(f"error: {script} not found", file=sys.stderr)
        raise SystemExit(1)
    os.execvp("bash", ["bash", str(script), *sys.argv[1:]])
