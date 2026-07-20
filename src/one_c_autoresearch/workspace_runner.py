from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .common import utc_now_iso, write_json
from .workspace import normalize_agent_result, process_start_marker


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    spec_path = Path(args[0]).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    run_dir = Path(spec["run_dir"]).resolve()
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(1):
            write_json(run_dir / "heartbeat.json", {"pid": os.getpid(), "process_marker": process_start_marker(os.getpid()), "run_token": spec["run_token"], "time": time.time()}, mode=0o600)

    write_json(run_dir / "heartbeat.json", {"pid": os.getpid(), "process_marker": process_start_marker(os.getpid()), "run_token": spec["run_token"], "time": time.time()}, mode=0o600)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    error = ""
    with (run_dir / "run.log").open("w", encoding="utf-8", buffering=1) as log:
        (run_dir / "run.log").chmod(0o600)
        try:
            result = subprocess.run(spec["command"], cwd=spec["cwd"], stdout=log, stderr=subprocess.STDOUT, text=True, check=False, timeout=int(spec.get("timeout_seconds") or 3600))
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
            error = "operation timeout exceeded"
            log.write(error + "\n")
        except Exception as exc:
            exit_code = 127
            error = str(exc)
            log.write(error + "\n")
    stop.set()
    thread.join(timeout=2)
    normalized = None
    if not error and exit_code == 0 and spec.get("agent_provider"):
        try:
            output_path = Path(spec.get("agent_output") or "")
            raw = output_path.read_text(encoding="utf-8", errors="replace") if output_path.is_file() else (run_dir / "run.log").read_text(encoding="utf-8", errors="replace")
            if len(raw.encode()) > 2 * 1024 * 1024:
                raise ValueError("agent output exceeds normalization bound")
            normalized = normalize_agent_result(spec["agent_provider"], raw, spec.get("source_fingerprints") or {})
        except ValueError as exc:
            exit_code, error = 65, str(exc)
    write_json(run_dir / "result.json", {
        "run_id": spec["run_id"], "run_token": spec["run_token"], "exit_code": exit_code,
        "error": error, "normalized_result": normalized, "source_fingerprints": spec.get("source_fingerprints") or {}, "finished_at": utc_now_iso(),
    }, mode=0o600)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
