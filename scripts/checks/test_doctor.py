#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deep", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="one-c-template-doctor-") as temporary:
        target = Path(temporary) / "research"
        create = subprocess.run([
            sys.executable, str(ROOT / "scripts/bootstrap/new_research_repo.py"),
            "--target-path", str(target), "--project-id", "doctor-smoke", "--product", "Demo",
            "--baseline-version", "1", "--target-version", "2", "--next-vendor-version", "3",
        ], cwd=ROOT, check=False)
        if create.returncode:
            return create.returncode
        subprocess.run(["git", "init", "-q"], cwd=target, check=True)
        subprocess.run(["git", "add", "-A"], cwd=target, check=True)
        doctor = subprocess.run([
            sys.executable, str(ROOT / "scripts/checks/test_research_repo.py"), "--repo-path", str(target),
        ], cwd=ROOT, check=False)
        if doctor.returncode or not args.deep:
            return doctor.returncode
        python_tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=target, check=False)
        if python_tests.returncode:
            return python_tests.returncode
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("npm is required for deep fresh-repository verification")
        for command in (("ci",), ("run", "typecheck"), ("test", "--", "--run"), ("run", "build")):
            result = subprocess.run([npm, *command], cwd=target / "web/workspace", check=False)
            if result.returncode:
                return result.returncode
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
