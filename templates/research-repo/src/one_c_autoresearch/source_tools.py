from __future__ import annotations

import os
import re
import selectors
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .contracts import canonical_json, sha256


PLATFORM_LIMIT, PROBE_SECONDS, SCAN_SECONDS, OUTPUT_LIMIT = 16, 3, 15, 64 * 1024
CAPABILITIES = {
    "ibcmd": ["binary_save", "extension_enumeration", "hierarchical_export"],
    "designer": ["binary_save", "hierarchical_export"],
    "edt": ["edt_project_conversion"],
    "v8unpack": ["container_unpack"],
}
PURPOSES = {"ibcmd": "exporter", "designer": "exporter", "edt": "legacy_only", "v8unpack": "conditional_converter"}


def classify_version(tool_id: str, path: Path, exit_code: int | None, output: str, platform_version: str = "") -> tuple[str, str, str]:
    if tool_id == "designer":
        return ("ready", platform_version, "") if platform_version else ("probe_failed", "", "platform_version_unverified")
    if exit_code:
        return "probe_failed", "", "version_command_failed"
    if tool_id == "ibcmd":
        match, expected = re.search(r"\b8\.\d+\.\d+\.\d+\b", output), None
    elif tool_id == "v8unpack":
        match, expected = re.search(r"\b(?:v8unpack\s+)?(\d+\.\d+\.\d+)\b", output), "1.2.6"
    else:
        match, expected = re.search(r"(\d{4}\.\d+\.\d+\+\d+)", str(path.resolve())), "2024.2.5+16"
    if not match:
        return "probe_failed", "", "version_unparseable"
    version = match.group(1) if match.lastindex else match.group(0)
    return ("incompatible", version, "version_incompatible") if expected and version != expected else ("ready", version, "")


def _bounded_command(command: list[str], deadline: float) -> tuple[int | None, bytes, str]:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output, reason = bytearray(), ""
    started = time.monotonic()
    try:
        while process.poll() is None:
            remaining = min(deadline - time.monotonic(), PROBE_SECONDS - (time.monotonic() - started))
            if remaining <= 0:
                reason = "probe_timeout"
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                output.extend(os.read(key.fd, min(8192, OUTPUT_LIMIT + 1 - len(output))))
                if len(output) > OUTPUT_LIMIT:
                    reason = "probe_output_limit_reached"
                    break
            if reason:
                break
        if reason:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
        else:
            output.extend(process.stdout.read(OUTPUT_LIMIT + 1 - len(output)))
            if len(output) > OUTPUT_LIMIT:
                reason = "probe_output_limit_reached"
        return process.returncode, bytes(output[:OUTPUT_LIMIT]), reason
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait()


def probe_instance(path: Path, tool_id: str, deadline: float, platform_root: Path | None = None, platform_version: str = "") -> tuple[dict[str, Any], str]:
    resolved = path.resolve()
    base = {"version": "", "path": str(resolved), "platform_root": str(platform_root.resolve()) if platform_root else "", "capabilities": CAPABILITIES[tool_id]}
    if not resolved.is_file():
        return {**base, "status": "probe_failed", "reason_code": "not_regular_file"}, ""
    if not os.access(resolved, os.X_OK):
        return {**base, "status": "probe_failed", "reason_code": "not_executable"}, ""
    if tool_id == "designer":
        status, version, reason = classify_version(tool_id, resolved, 0, "", platform_version)
        return {**base, "status": status, "version": version, "reason_code": reason}, ""
    command = [str(resolved), "--version"] if tool_id == "ibcmd" else [str(resolved), "-h"]
    code, output, bounded_reason = _bounded_command(command, deadline)
    if bounded_reason:
        return {**base, "status": "probe_failed", "reason_code": bounded_reason}, bounded_reason
    text = output.decode(errors="replace")
    status, version, reason = classify_version(tool_id, resolved, code, text, platform_version)
    return {**base, "status": status, "version": version, "reason_code": reason}, ""


def _platform_roots(configured_roots: Iterable[str]) -> tuple[list[Path], int]:
    configured = sorted({Path(value).expanduser().resolve() for value in configured_roots if value})
    standard = Path("/opt/1cv8/x86_64")
    versions = sorted(path.resolve() for path in standard.glob("8.*.*.*") if path.is_dir() and re.fullmatch(r"8\.\d+\.\d+\.\d+", path.name))
    path_roots = sorted({Path(value).resolve().parent for name in ("ibcmd", "1cv8") if (value := shutil.which(name))})
    ordered = list(dict.fromkeys([*configured, *versions, *path_roots]))
    return ordered[:PLATFORM_LIMIT], max(0, len(ordered) - PLATFORM_LIMIT)


def discover_tools(configured_roots: Iterable[str]) -> dict[str, Any]:
    deadline = time.monotonic() + SCAN_SECONDS
    roots, omitted = _platform_roots(configured_roots)
    diagnostics = ([{"code": "candidate_limit_reached", "subject": "platform_roots", "omitted_count": omitted}] if omitted else [])
    instances: dict[str, list[dict[str, Any]]] = {tool: [] for tool in PURPOSES}
    seen: dict[str, set[str]] = {tool: set() for tool in PURPOSES}
    incomplete: set[str] = set()
    if omitted:
        incomplete.update({"ibcmd", "designer"})

    def add(tool: str, path: Path, root: Path | None = None, version: str = "") -> str:
        resolved = str(path.resolve())
        if resolved in seen[tool]:
            return version
        seen[tool].add(resolved)
        item, diagnostic = probe_instance(path, tool, deadline, root, version)
        instances[tool].append(item)
        if diagnostic:
            diagnostics.append({"code": diagnostic, "subject": tool, "omitted_count": 0})
            incomplete.add(tool)
        return item["version"] if item["status"] == "ready" else ""

    for root in roots:
        if time.monotonic() >= deadline:
            diagnostics.append({"code": "scan_deadline_reached", "subject": "platform_roots", "omitted_count": 0})
            incomplete.update(PURPOSES)
            break
        ibcmd = root / "ibcmd"
        if ibcmd.exists():
            add("ibcmd", ibcmd, root)
        if (root / "1cv8").exists():
            add("designer", root / "1cv8", root, root.name if re.fullmatch(r"8\.\d+\.\d+\.\d+", root.name) else "")
    edt_paths = sorted(Path("/opt/1C/1CE/components").glob("1c-edt-*-x86_64/1cedtcli"))
    if executable := shutil.which("1cedtcli"):
        edt_paths.append(Path(executable))
    for executable in edt_paths:
        add("edt", executable)
    if executable := shutil.which("v8unpack"):
        add("v8unpack", Path(executable))

    complete = not diagnostics
    tools = []
    for tool in sorted(PURPOSES):
        values = sorted(instances[tool], key=lambda item: item["path"])
        statuses = {item["status"] for item in values}
        status = "ready" if "ready" in statuses else "degraded" if "probe_failed" in statuses or tool in incomplete else "incompatible" if "incompatible" in statuses else "unavailable"
        tools.append({"tool_id": tool, "status": status, "purpose": PURPOSES[tool], "instances": values})
    diagnostics.sort(key=lambda item: (item["code"], item["subject"]))
    preimage = {"schema_version": "1", "complete": complete, "tools": tools, "diagnostics": diagnostics}
    return {**preimage, "checked_at": datetime.now(timezone.utc).isoformat(), "inventory_fingerprint": "sha256:" + sha256(canonical_json(preimage))}
