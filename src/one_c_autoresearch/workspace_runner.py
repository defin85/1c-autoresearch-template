from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .common import utc_now_iso, write_json


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    spec_path = Path(args[0]).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    run_dir = Path(spec["run_dir"]).resolve()
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(1):
            write_json(run_dir / "heartbeat.json", {"pid": os.getpid(), "run_token": spec["run_token"], "time": time.time()}, mode=0o600)

    write_json(run_dir / "heartbeat.json", {"pid": os.getpid(), "run_token": spec["run_token"], "time": time.time()}, mode=0o600)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    error = ""
    with (run_dir / "run.log").open("w", encoding="utf-8", buffering=1) as log:
        try:
            result = subprocess.run(spec["command"], cwd=spec["cwd"], stdout=log, stderr=subprocess.STDOUT, text=True, check=False)
            exit_code = result.returncode
        except Exception as exc:
            exit_code = 127
            error = str(exc)
            log.write(error + "\n")
    stop.set()
    thread.join(timeout=2)
    write_json(run_dir / "result.json", {
        "run_id": spec["run_id"], "run_token": spec["run_token"], "exit_code": exit_code,
        "error": error, "finished_at": utc_now_iso(),
    }, mode=0o600)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
