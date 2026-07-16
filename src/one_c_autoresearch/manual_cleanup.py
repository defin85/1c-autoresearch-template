from __future__ import annotations

import runpy
import sys


COMMAND_MODULES = {
    "apply-decisions": "manual_cleanup_apply_decisions",
    "apply-ready": "manual_cleanup_apply_ready",
    "build-candidates": "manual_cleanup_build_agent_candidates",
    "run": "manual_cleanup_codex_exec",
    "next-rows": "manual_cleanup_next_rows",
    "probe-row": "manual_cleanup_probe_row",
    "source-context": "manual_cleanup_source_context",
    "trace-summary": "manual_cleanup_trace_summary",
}


def command(args: object) -> int:
    name = str(getattr(args, "manual_cleanup_command"))
    argv = list(getattr(args, "command_args", []))
    old_argv = sys.argv
    try:
        sys.argv = [name, *argv]
        runpy.run_module(f"one_c_autoresearch.manual_cleanup_commands.{COMMAND_MODULES[name]}", run_name="__main__")
    except SystemExit as exc:
        if isinstance(exc.code, int) or exc.code is None:
            return int(exc.code or 0)
        print(str(exc.code), file=sys.stderr)
        return 1
    finally:
        sys.argv = old_argv
    return 0
