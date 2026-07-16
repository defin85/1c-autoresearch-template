#!/usr/bin/env python3
"""Summarize Codex Exec JSONL trace for manual cleanup tooling."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


FORBIDDEN_DIRECT_READS = (
    "manual-markup-queue.jsonl",
    "markup.csv",
    "template-noise-candidates.csv",
    "final-diff-inventory.csv",
)


def walk(value: Any) -> list[Any]:
    out = [value]
    if isinstance(value, dict):
        for child in value.values():
            out.extend(walk(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(walk(child))
    return out


def read_events(path: Path) -> tuple[list[dict], int]:
    events = []
    invalid = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(event, dict):
            events.append(event)
    return events, invalid


def event_type(event: dict) -> str:
    for key in ("type", "event", "name", "kind"):
        if event.get(key):
            return str(event[key])
    return "unknown"


def strings(event: dict) -> list[str]:
    return [item for item in walk(event) if isinstance(item, str)]


def command_strings(events: list[dict]) -> list[str]:
    commands = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if isinstance(item, dict) and isinstance(item.get("command"), str):
            commands.append(item["command"])
    return commands


def is_probe_call(command: str) -> bool:
    return "manual_cleanup_probe_row.py --row-id" in command or "manual-cleanup probe-row --row-id" in command


def is_source_context_call(command: str) -> bool:
    return "manual_cleanup_source_context.py --row-id" in command or "manual-cleanup source-context --row-id" in command


def failed_commands(events: list[dict]) -> list[str]:
    errors = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("status") != "failed":
            continue
        command = item.get("command", "")
        exit_code = item.get("exit_code", "")
        errors.append(f"exit_code={exit_code}: {command}")
    return errors


def duration_values(events: list[dict]) -> list[float]:
    values = []
    for item in walk(events):
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if "duration" in str(key).lower() and isinstance(value, (int, float)):
                values.append(float(value))
    return values


def write_markdown(path: Path, summary: dict) -> None:
    lines = [
        f"# Trace summary: {summary['trace']}",
        "",
        f"- events: {summary['event_count']}",
        f"- invalid_json_lines: {summary['invalid_json_lines']}",
        f"- probe_calls: {summary['probe_calls']}",
        f"- source_context_calls: {summary['source_context_calls']}",
        f"- expected_rows: {len(summary['expected_row_ids'])}",
        f"- missing_probe_rows: {', '.join(summary['missing_probe_rows']) or '-'}",
        f"- missing_source_context_rows: {', '.join(summary['missing_source_context_rows']) or '-'}",
        f"- suspicious_direct_reads: {len(summary['suspicious_direct_reads'])}",
        f"- unexpected_commands: {len(summary['unexpected_commands'])}",
        f"- errors: {len(summary['errors'])}",
        f"- max_duration: {summary['max_duration']}",
        "",
        "## Commands",
        "",
    ]
    lines.extend(f"- `{command}`" for command in summary["commands"][:50])
    if len(summary["commands"]) > 50:
        lines.append(f"- ... {len(summary['commands']) - 50} more")
    if summary["errors"]:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- `{error}`" for error in summary["errors"][:20])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--summary-md", type=Path)
    parser.add_argument("--expected-row-id", action="append", default=[])
    parser.add_argument("--expected-source-context-row-id", action="append", default=[])
    parser.add_argument("--fail-on-missing-probe", action="store_true")
    parser.add_argument("--fail-on-missing-source-context", action="store_true")
    parser.add_argument("--fail-on-direct-read", action="store_true")
    parser.add_argument("--fail-on-unexpected-command", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    events, invalid = read_events(args.trace)
    commands = command_strings(events)
    probe_commands = [command for command in commands if is_probe_call(command)]
    source_context_commands = [command for command in commands if is_source_context_call(command)]
    probe_blob = "\n".join(probe_commands)
    missing = [row_id for row_id in args.expected_row_id if row_id not in probe_blob]
    source_context_blob = "\n".join(source_context_commands)
    expected_source_context = args.expected_source_context_row_id
    missing_source_context = [row_id for row_id in expected_source_context if row_id not in source_context_blob]
    suspicious = [
        command
        for command in commands
        if any(name in command for name in FORBIDDEN_DIRECT_READS)
        and "manual_cleanup_probe_row.py" not in command
        and "manual-cleanup probe-row" not in command
        and "manual_cleanup_next_rows.py" not in command
        and "manual-cleanup next-rows" not in command
    ]
    unexpected = [
        command
        for command in commands
        if not is_probe_call(command)
        and not is_source_context_call(command)
    ]
    durations = duration_values(events)
    summary = {
        "trace": str(args.trace),
        "event_count": len(events),
        "invalid_json_lines": invalid,
        "event_types": dict(Counter(event_type(event) for event in events)),
        "commands": commands,
        "probe_calls": len(probe_commands),
        "source_context_calls": len(source_context_commands),
        "expected_row_ids": args.expected_row_id,
        "expected_source_context_row_ids": expected_source_context,
        "missing_probe_rows": missing,
        "missing_source_context_rows": missing_source_context,
        "suspicious_direct_reads": suspicious,
        "unexpected_commands": unexpected,
        "errors": failed_commands(events),
        "duration_count": len(durations),
        "total_duration": sum(durations) if durations else 0,
        "max_duration": max(durations) if durations else 0,
    }
    if args.summary_json:
        args.summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.summary_md:
        write_markdown(args.summary_md, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_missing_probe and missing:
        return 1
    if args.fail_on_missing_source_context and missing_source_context:
        return 1
    if args.fail_on_direct_read and suspicious:
        return 1
    if args.fail_on_unexpected_command and unexpected:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
