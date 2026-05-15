#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from one_c_autoresearch.cli import main

if __name__ == "__main__":
    default_queue = Path(__file__).resolve().parents[2] / "analysis" / "queue" / "tasks.jsonl"
    raise SystemExit(main(["queue", "get", "--queue-path", str(default_queue), *sys.argv[1:]]))
