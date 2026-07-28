#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def check(repo: Path) -> int:
    repo = repo.resolve()
    environment = {**os.environ, "PYTHONPATH": str(repo / "src")}
    return subprocess.run(
        [sys.executable, "-m", "one_c_autoresearch", "--repo-path", str(repo), "doctor"],
        cwd=repo,
        env=environment,
        check=False,
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", required=True)
    return check(Path(parser.parse_args().repo_path))


if __name__ == "__main__":
    raise SystemExit(main())
