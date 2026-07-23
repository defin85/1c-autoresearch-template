from __future__ import annotations

import json
import os
import shutil
import tempfile
import tomllib
import subprocess
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, BinaryIO

from .contracts import ROLES, atomic_json, canonical_json, confined, external_id, file_manifest, normalize_relative, reject_secrets, repository_lock, require_tracked_clean as validate_tracked_clean, sha256


EXPORTERS = ("ibcmd", "designer")
REPRESENTATIONS = ("xml-hierarchical", "v8unpack", "edt-project")
PROFILES = {f"{exporter}+form-aware/v1": exporter for exporter in EXPORTERS}
LEGACY_PROFILES = {f"{exporter}+{representation}/v1": (exporter, representation) for exporter in EXPORTERS for representation in REPRESENTATIONS}
NORMALIZER_VERSION = "3"
V8UNPACK_VERSION = "1.2.6"
EDT_VERSION = "2024.2.5+16"
MINIMUM_FREE_BYTES = 1024**3
NOISE_NAMES = {"ConfigDumpInfo.xml", ".metadata", "build", "cache", "logs", "tmp"}
_UNSET = object()


def current_profile_test(profile: dict[str, Any], profile_id: str) -> bool:
    sealed = {key: value for key, value in profile.items() if key not in {"db_password", "infobase_password", "tested_fingerprint"}}
    extensions = profile.get("extensions")
    valid_extensions = isinstance(extensions, list) and all(
        re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", str(item.get("uuid", "")).lower())
        and str(item.get("name", "")).strip()
        and isinstance(item.get("active"), bool)
        for item in extensions
    )
    return bool(profile.get("tested")) and profile.get("profile_id") == profile_id and valid_extensions and profile.get("tested_fingerprint") == "sha256:" + sha256(canonical_json(sealed))


def _immutable_role_fingerprint(path: str) -> str:
    return "sha256:" + sha256(canonical_json(file_manifest(Path(path))))


def load_contract(repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    with (repo / "research/infobases.toml").open("rb") as stream:
        infobases = tomllib.load(stream)
    with (repo / "research/external-artifacts.toml").open("rb") as stream:
        artifacts = tomllib.load(stream)
    reject_secrets(infobases, "research/infobases.toml")
    reject_secrets(artifacts, "research/external-artifacts.toml")
    return infobases, artifacts


def validate_role_contract(repo: Path, tested_profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    infobases, artifacts = load_contract(repo)
    profile_id = infobases.get("acquisition_profile")
    if profile_id not in PROFILES:
        raise ValueError(f"unsupported acquisition profile: {profile_id}")
    roles = infobases.get("roles", {})
    if tuple(roles) != ROLES:
        raise ValueError(f"exactly three ordered roles are required: {ROLES}")
    identities: set[str] = set(); roots: set[str] = set()
    project = tomllib.loads((repo / "project.toml").read_text(encoding="utf-8"))["project"]
    expected_versions = {"vendor_baseline": project.get("baseline_version"), "target_cf": project.get("target_version"), "next_vendor": project.get("next_vendor_version")}
    for role in ROLES:
        binding = roles[role]
        required = ("connection_profile", "configuration_name", "root_uuid", "version")
        if any(not str(binding.get(key, "")).strip() for key in required):
            raise ValueError(f"incomplete infobase binding: {role}")
        if binding["version"] != expected_versions[role]:
            raise ValueError(f"role version does not match project.toml: {role}")
        roots.add(str(binding["root_uuid"]).lower())
        tested = tested_profiles.get(binding["connection_profile"])
        if not tested or not current_profile_test(tested, profile_id):
            raise ValueError(f"connection was not tested for current acquisition profile: {role}")
        extensions = tested.get("extensions")
        if len({str(item["uuid"]).lower() for item in extensions}) != len(extensions):
            raise ValueError(f"duplicate extension UUID in current enumeration: {role}")
        identity = normalize_connection_identity(tested)
        if identity in identities:
            raise ValueError("three distinct normalized connection identities are required")
        identities.add(identity)
    if len(roots) != 1:
        raise ValueError("all roles must share one configuration root UUID lineage")
    seen_binding: set[tuple[str, str, str]] = set(); seen_id: dict[str, tuple[str, str]] = {}
    for item in artifacts.get("artifacts", []):
        role, kind, key = item.get("role"), item.get("kind"), item.get("semantic_key")
        if role not in ROLES or kind not in {"epf", "erf", "source-tree", "other"} or not isinstance(key, str) or not key or key != unicodedata.normalize("NFC", key):
            raise ValueError("invalid external artifact declaration")
        filename = item.get("filename")
        if not isinstance(filename, str) or normalize_relative(filename) != filename:
            raise ValueError("invalid external artifact filename")
        declared_size = item.get("declared_size_bytes")
        if not isinstance(declared_size, int) or isinstance(declared_size, bool) or declared_size <= 0:
            raise ValueError("external artifact declared_size_bytes must be a positive integer")
        expected_sha256 = item.get("sha256")
        if expected_sha256 is not None and not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", str(expected_sha256)):
            raise ValueError("invalid external artifact SHA-256")
        binding_key = (role, kind, key)
        if binding_key in seen_binding:
            raise ValueError(f"duplicate external artifact binding: {binding_key}")
        seen_binding.add(binding_key)
        identifier = external_id(kind, key)
        preimage = (kind, key)
        if identifier in seen_id and seen_id[identifier] != preimage:
            raise ValueError(f"external artifact ID collision: {identifier}")
        seen_id[identifier] = preimage
        item["external_artifact_id"] = identifier
    return {"schema_version": "2", "acquisition_profile_id": profile_id, "roles": roles, "artifacts": artifacts.get("artifacts", [])}


def normalize_connection_identity(profile: dict[str, Any]) -> str:
    kind = profile.get("kind")
    if kind == "server":
        server = str(profile.get("server", "")).strip().lower()
        reference = str(profile.get("reference", "")).strip().lower()
        if not server or not reference:
            raise ValueError("server connection identity is incomplete")
        return f"server:{server}/{reference}"
    if kind == "file":
        path = Path(str(profile.get("path", ""))).expanduser().resolve()
        return f"file:{path}"
    raise ValueError(f"unsupported connection kind: {kind}")


def _ibcmd_db_args(connection: dict[str, Any]) -> list[str]:
    required = ("dbms", "db_server", "db_name", "db_user", "db_password")
    if any(key not in connection for key in required):
        raise ValueError("incomplete ibcmd DBMS connection profile")
    return [f"--dbms={connection['dbms']}", f"--db-server={connection['db_server']}", f"--db-name={connection['db_name']}", f"--db-user={connection['db_user']}", f"--db-pwd={connection['db_password']}"]


def _run_command(run: callable, command: list[str], *, cancelled: callable | None = None, **kwargs):
    if cancelled and cancelled():
        raise InterruptedError("source acquisition cancelled")
    if run is not subprocess.run or cancelled is None:
        result = run(command, **kwargs)
        if cancelled and cancelled():
            raise InterruptedError("source acquisition cancelled")
        return result
    timeout = kwargs.pop("timeout", None)
    input_data = kwargs.pop("input", None)
    kwargs.pop("check", None)
    if input_data is not None:
        kwargs.setdefault("stdin", subprocess.PIPE)
    started = time.monotonic()
    process = subprocess.Popen(command, **kwargs)
    first_communicate = True
    while True:
        try:
            stdout, stderr = process.communicate(input=input_data if first_communicate else None, timeout=0.2)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            first_communicate = False
            if cancelled():
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.communicate()
                raise InterruptedError("source acquisition cancelled")
            if timeout is not None and time.monotonic() - started >= timeout:
                process.kill(); process.communicate()
                raise subprocess.TimeoutExpired(command, timeout)


def _toolchain_versions(profile_id: str, platform: Path, *, timeout_seconds: int, run: callable, cancelled: callable | None = None) -> dict[str, str]:
    from .source_tools import classify_version
    if profile_id in PROFILES:
        exporter, representation = PROFILES[profile_id], None
    else:
        exporter, representation = LEGACY_PROFILES[profile_id]
    ibcmd = _run_command(run, [str(platform / "ibcmd"), "--version"], cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
    status, platform_version, _reason = classify_version("ibcmd", platform / "ibcmd", ibcmd.returncode, ibcmd.stdout)
    if status != "ready":
        raise RuntimeError("ibcmd version preflight failed")
    versions = {"platform": platform_version, "exporter": exporter}
    if exporter == "designer" and not (platform / "1cv8").is_file():
        raise RuntimeError("Designer executable preflight failed")
    if representation == "v8unpack":
        executable = shutil.which("v8unpack")
        if not executable:
            raise RuntimeError("v8unpack executable is unavailable")
        result = _run_command(run, [executable, "-h"], cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
        status, version, _reason = classify_version("v8unpack", Path(executable), result.returncode, result.stdout)
        if status != "ready":
            raise RuntimeError(f"v8unpack {V8UNPACK_VERSION} is required")
        versions["converter"] = f"v8unpack {version}"
    elif representation == "edt-project":
        executable = shutil.which("1cedtcli")
        resolved = str(Path(executable).resolve()) if executable else ""
        status, version, _reason = classify_version("edt", Path(resolved), 0, "")
        if status != "ready":
            raise RuntimeError(f"1C:EDT {EDT_VERSION} is required")
        versions["converter"] = f"1C:EDT {version}"
    return versions


def preflight_connection(profile_id: str, platform: Path, connection: dict[str, Any], *, timeout_seconds: int = 120, run: callable = subprocess.run, cancelled: callable | None = None) -> dict[str, Any]:
    if profile_id not in PROFILES:
        raise ValueError("unsupported acquisition profile")
    exporter = PROFILES[profile_id]
    executable = platform / "ibcmd"
    toolchain_versions = _toolchain_versions(profile_id, platform, timeout_seconds=timeout_seconds, run=run, cancelled=cancelled)
    with tempfile.TemporaryDirectory(prefix="ibcmd-preflight-") as data_dir, tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as credentials:
        credentials.write(f"{connection.get('infobase_user', '')}\n{connection.get('infobase_password', '')}\n"); credentials.flush(); credentials.seek(0)
        command = [str(executable), "extension", "list", f"--data={data_dir}", *_ibcmd_db_args(connection)]
        listed = _run_command(run, command, cancelled=cancelled, stdin=credentials, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
    if listed.returncode:
        raise RuntimeError("infobase authentication or extension enumeration preflight failed")
    cleaned = listed.stdout.replace("Authentication in the infobase is required to perform the operation", "").replace("User:", "").replace("Password:", "")
    extensions: list[dict[str, Any]] = []
    current: dict[str, str] = {}
    for line in (line.strip() for line in cleaned.splitlines() if line.strip()):
        if ":" not in line: raise RuntimeError("unsupported ibcmd extension list output")
        key, value = (part.strip() for part in line.split(":", 1)); value = value.strip('"')
        if key == "name" and current:
            extensions.append({"name": current["name"], "version": current.get("version", ""), "active": current.get("active") == "yes"}); current = {}
        current[key] = value
    if current:
        extensions.append({"name": current["name"], "version": current.get("version", ""), "active": current.get("active") == "yes"})
    with tempfile.TemporaryDirectory(prefix="extension-identities-") as temporary:
        for index, extension in enumerate(extensions):
            output = Path(temporary) / str(index)
            command = adapter_plan(f"{exporter}+xml-hierarchical/v1", platform, connection, output, extension["name"], timeout_seconds=timeout_seconds)[0]
            exported = _run_command(run, command, cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
            if exported.returncode:
                raise RuntimeError(f"extension identity export failed: {extension['name']}")
            identity = configuration_identity(output)
            if identity["name"] != extension["name"] or extension["version"] and identity["version"] != extension["version"]:
                raise RuntimeError(f"extension identity export does not match enumeration: {extension['name']}")
            extension["uuid"] = identity["uuid"]
    if len({item["uuid"] for item in extensions}) != len(extensions):
        raise RuntimeError("extension enumeration produced duplicate UUIDs")
    tested = {key: value for key, value in connection.items() if key not in {"db_password", "infobase_password", "tested", "extensions", "tested_fingerprint", "tool_version"}}
    tested.update({"profile_id": profile_id, "tested": True, "extensions": extensions, "tool_versions": toolchain_versions})
    tested["tested_fingerprint"] = "sha256:" + sha256(canonical_json(tested))
    return tested


def configuration_uuid(root: Path) -> str:
    return configuration_identity(root)["uuid"]


def configuration_identity(root: Path) -> dict[str, str]:
    path = next((candidate for candidate in (root / "Configuration.xml", root / "src/Configuration/Configuration.mdo") if candidate.is_file()), None)
    if path is None:
        raise ValueError("exported configuration root is missing")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("DTD and external entities are forbidden in exported XML")
    import xml.etree.ElementTree as ET
    element = ET.fromstring(raw)
    configuration = element if element.tag.rsplit("}", 1)[-1] == "Configuration" else next((item for item in element if item.tag.rsplit("}", 1)[-1] == "Configuration"), None)
    identifier = str(configuration.attrib.get("uuid", "")).lower() if configuration is not None else ""
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", identifier):
        raise ValueError("exported configuration root UUID is missing or invalid")
    properties = next((item for item in configuration if item.tag.rsplit("}", 1)[-1] == "Properties"), None) if configuration is not None else None
    values = {item.tag.rsplit("}", 1)[-1].lower(): (item.text or "").strip() for item in (properties if properties is not None else configuration)}
    if not values.get("name"):
        raise ValueError("exported configuration name is missing")
    return {"uuid": identifier, "name": values["name"], "version": values.get("version", "")}


def adapter_plan(profile_id: str, platform: Path, connection: dict[str, str], output: Path, extension_name: str | None = None, *, representation: str | None = None, workspace: Path | None = None, project_name: str | None = None, timeout_seconds: int = 1800) -> list[list[str]]:
    if profile_id in PROFILES:
        exporter = PROFILES[profile_id]
        if representation not in {"xml-hierarchical", "v8unpack"}:
            raise ValueError("form-aware adapter requires a routed representation")
    elif profile_id in LEGACY_PROFILES:
        exporter, representation = LEGACY_PROFILES[profile_id]
    else:
        raise ValueError(f"unsupported acquisition profile: {profile_id}")
    output = output.resolve()
    extension = [f"--extension={extension_name}"] if extension_name else []
    if exporter == "ibcmd":
        intermediate = output.with_name(output.name + ".xml-staging") if representation == "edt-project" else output if representation != "v8unpack" else output.with_suffix(".cfe" if extension_name else ".cf")
        verb = "export" if representation != "v8unpack" else "save"
        required = ("dbms", "db_server", "db_name", "db_user", "db_password", "infobase_user", "infobase_password")
        if any(key not in connection for key in required):
            raise ValueError("incomplete ibcmd connection profile")
        commands = [[str(platform / "ibcmd"), "config", verb, *_ibcmd_db_args(connection), f"--user={connection['infobase_user']}", f"--password={connection['infobase_password']}", *(["--threads=1"] if verb == "export" else []), *extension, str(intermediate)]]
    else:
        intermediate = output.with_name(output.name + ".xml-staging") if representation == "edt-project" else output if representation != "v8unpack" else output.with_suffix(".cfe" if extension_name else ".cf")
        switch = "/DumpConfigToFiles" if representation != "v8unpack" else "/DumpCfg"
        commands = [[str(platform / "1cv8"), "DESIGNER", connection["client_connection"], f"/N{connection['infobase_user']}", f"/P{connection['infobase_password']}", switch, str(intermediate)]]
        if representation != "v8unpack":
            commands[0] += ["-Format", "Hierarchical"]
        if extension_name:
            commands[0] += ["-Extension", extension_name]
    if representation == "v8unpack":
        commands.append(["v8unpack", "-E", str(intermediate), str(output), "--temp", str(output.with_name(f".{output.name}.v8unpack-temp")), "--processes", "1"])
    elif representation == "edt-project":
        workspace = (workspace or output.with_name(f".{output.name}.edt-workspace")).resolve()
        project_name = project_name or ("base" if not extension_name else f"ext-{sha256(extension_name.encode())[:16]}")
        platform_version = ".".join(platform.name.split(".")[:3])
        commands.append(["1cedtcli", "-data", str(workspace), "-timeout", str(timeout_seconds), "-command", "import", "--version", platform_version, "--base-project-name", "base", "--configuration-files", str(intermediate), "--project-name", project_name, "--build", "false"])
        commands.append(["1cedtcli", "-data", str(workspace), "-timeout", str(timeout_seconds), "-command", "sort-project", "--project-name-list", project_name])
        commands.append(["1cedtcli", "-data", str(workspace), "-timeout", str(timeout_seconds), "-command", "clean-up-source", "--project-name", project_name])
        commands.append(["1cedtcli", "-data", str(workspace), "-timeout", str(timeout_seconds), "-command", "validate", "--file", str(workspace.parent / f"{project_name}-validation.tsv"), "--project-name-list", project_name])
    return commands


def clean_payload(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.name in NOISE_NAMES:
            shutil.rmtree(path) if path.is_dir() else path.unlink()


def reject_links_and_special_files(root: Path) -> None:
    import stat
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if path.is_symlink() or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError("source payload contains a link or special file")


def normalize_payload(root: Path) -> None:
    volatile_period = re.compile(br"(<pl:(?:begin|end)>)(\d{4}-\d{2}-\d{2})(T\d{2}:\d{2}:\d{2}</pl:(?:begin|end)>)")
    for path in root.rglob("*.xml"):
        raw = path.read_bytes()
        normalized = volatile_period.sub(br"\g<1>2000-01-01\g<3>", raw)
        if normalized != raw:
            path.write_bytes(normalized)


def _reject_secret_content(root: Path) -> None:
    secret_value = br"(?:'[^'\r\n]{8,}'|\"[^\"\r\n]{8,}\"|[A-Za-z0-9_./+=-]{8,})"
    patterns = (
        re.compile(br"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(br"(?i)\b(?:password|passwd|pwd|token|secret)\s*[:=]\s*" + secret_value),
    )
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.stat().st_size <= 16 * 1024 * 1024:
            raw = path.read_bytes()
            if any(pattern.search(raw) for pattern in patterns):
                raise ValueError(f"external artifact contains a detected secret: {path.relative_to(root).as_posix()}")


def _extract_source_tree(source: Path, destination: Path) -> None:
    import tarfile
    import zipfile
    destination.mkdir(parents=True)
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for item in sorted(archive.infolist(), key=lambda value: value.filename):
                relative = normalize_relative(item.filename.rstrip("/")) if item.filename.rstrip("/") else ""
                if not relative:
                    continue
                mode = item.external_attr >> 16
                if mode and (mode & 0o170000) not in {0, 0o040000, 0o100000}:
                    raise ValueError("source-tree archive contains a link or special file")
                target = confined(destination, relative)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as input_stream, target.open("xb") as output_stream:
                        shutil.copyfileobj(input_stream, output_stream)
    elif tarfile.is_tarfile(source):
        with tarfile.open(source, "r:*") as archive:
            for item in sorted(archive.getmembers(), key=lambda value: value.name):
                relative = normalize_relative(item.name.rstrip("/")) if item.name.rstrip("/") else ""
                if not relative:
                    continue
                target = confined(destination, relative)
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif item.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    input_stream = archive.extractfile(item)
                    if input_stream is None:
                        raise ValueError("source-tree archive member is unreadable")
                    with input_stream, target.open("xb") as output_stream:
                        shutil.copyfileobj(input_stream, output_stream)
                else:
                    raise ValueError("source-tree archive contains a link or special file")
    else:
        raise ValueError("source-tree upload must be a ZIP or TAR archive")
    if not file_manifest(destination):
        raise ValueError("source-tree archive is empty")
    _reject_secret_content(destination)


def stream_upload(stream: BinaryIO, drafts_root: Path, relative_name: str, declared_length: int, declared_sha256: str | None, expected_draft_fingerprint: str | None = None, cancelled: callable | None = None) -> dict[str, Any]:
    relative = normalize_relative(relative_name)
    target = confined(drafts_root, relative)
    if shutil.disk_usage(drafts_root).free < declared_length + 1024**3:
        raise OSError("external upload requires declared bytes plus 1 GiB free space")
    if expected_draft_fingerprint is not None and draft_fingerprint(drafts_root) != expected_draft_fingerprint:
        raise RuntimeError("stale upload draft fingerprint")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.partial")
    import hashlib
    digest = hashlib.sha256(); written = 0
    try:
        with temporary.open("xb") as output:
            while True:
                if cancelled and cancelled():
                    raise InterruptedError("upload cancelled")
                chunk = stream.read(min(1024 * 1024, declared_length - written + 1))
                if not chunk:
                    break
                written += len(chunk)
                if written > declared_length:
                    raise ValueError("upload exceeds declared length")
                digest.update(chunk); output.write(chunk)
            output.flush(); os.fsync(output.fileno())
        actual = digest.hexdigest()
        if written != declared_length or declared_sha256 and actual != declared_sha256.lower():
            raise ValueError("external upload length or SHA-256 mismatch")
        os.replace(temporary, target)
        return {"path": relative, "size_bytes": written, "sha256": actual, "draft_fingerprint": draft_fingerprint(drafts_root)}
    finally:
        temporary.unlink(missing_ok=True)


def draft_fingerprint(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    return "sha256:" + sha256(canonical_json(file_manifest(root)))


def routing_bindings(repo: Path, connections: dict[str, dict[str, Any]], upload_drafts: Path | None) -> dict[str, str]:
    from .source_routing import PROBE_CONTRACT_VERSION
    from .workflow import state_fingerprint
    safe_connections = {
        name: {key: value for key, value in profile.items() if key not in {"db_password", "infobase_password"}}
        for name, profile in sorted(connections.items())
    }
    return {
        "workflow": "sha256:" + sha256((repo / "research/workflow.toml").read_bytes()),
        "workflow_state": state_fingerprint(repo),
        "infobases": "sha256:" + sha256((repo / "research/infobases.toml").read_bytes()),
        "external_artifacts": "sha256:" + sha256((repo / "research/external-artifacts.toml").read_bytes()),
        "connections": "sha256:" + sha256(canonical_json(safe_connections)),
        "upload_draft": draft_fingerprint(upload_drafts) if upload_drafts else "sha256:" + sha256(canonical_json([])),
        "probe_contract": PROBE_CONTRACT_VERSION,
    }


def _execute_plan(commands: list[list[str]], *, run: callable, timeout_seconds: int, cancelled: callable | None, subject: str) -> None:
    for command in commands:
        result = _run_command(run, command, cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
        if result.returncode:
            raise RuntimeError(f"source_route_failed:{subject}")


def _check_cancelled(cancelled: callable | None, phase: str) -> None:
    if cancelled and cancelled():
        raise InterruptedError(f"source acquisition cancelled during {phase}")


def _verified_artifact_source(upload_drafts: Path | None, member: dict[str, Any]) -> Path:
    artifact = member["artifact"]
    if not upload_drafts:
        raise ValueError(f"external artifact upload draft is unavailable: {member['name']}")
    filename = normalize_relative(str(artifact["filename"]))
    source = confined(upload_drafts, f"{member['role']}/{member['name']}/{filename}")
    if not source.is_file() or source.stat().st_size != artifact["declared_size_bytes"]:
        raise ValueError(f"external artifact upload does not match declaration: {member['name']}")
    import hashlib
    hasher = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if artifact.get("sha256") and digest != str(artifact["sha256"]).removeprefix("sha256:").lower():
        raise ValueError(f"external artifact upload does not match declaration: {member['name']}")
    return source


def build_routing_preview(repo: Path, platform: Path, connections: dict[str, dict[str, Any]], *, timeout_seconds: int = 1800, run: callable = subprocess.run, upload_drafts: Path | None = None, cancelled: callable | None = None) -> dict[str, Any]:
    from .source_routing import analyze_forms, component_members, plan_groups
    tested = {name: {key: value for key, value in profile.items() if key not in {"db_password", "infobase_password"}} for name, profile in connections.items()}
    contract = validate_role_contract(repo, tested)
    exporter = PROFILES[contract["acquisition_profile_id"]]
    members = component_members(contract, connections)
    with tempfile.TemporaryDirectory(prefix="source-routing-preview-") as temporary:
        root = Path(temporary)
        for member in members:
            _check_cancelled(cancelled, "probe")
            if member["kind"] in {"source-tree", "other"}:
                continue
            output = root / member["role"] / member["routing_group_id"].replace(":", "-")
            output.parent.mkdir(parents=True, exist_ok=True)
            if member["kind"] in {"configuration", "extension"}:
                connection = connections[contract["roles"][member["role"]]["connection_profile"]]
                _execute_plan(
                    adapter_plan(contract["acquisition_profile_id"], platform, connection, output, member["name"], representation="xml-hierarchical", timeout_seconds=timeout_seconds),
                    run=run,
                    timeout_seconds=timeout_seconds,
                    cancelled=cancelled,
                    subject=member["routing_group_id"],
                )
            else:
                source = _verified_artifact_source(upload_drafts, member)
                _execute_plan(
                    [["v8unpack", "-E", str(source), str(output), "--temp", str(output.with_name(f".{output.name}.temp")), "--processes", "1"]],
                    run=run,
                    timeout_seconds=timeout_seconds,
                    cancelled=cancelled,
                    subject=member["routing_group_id"],
                )
            if not output.is_dir() or not any(path.is_file() for path in output.rglob("*")):
                raise ValueError(f"source_probe_empty:{member['routing_group_id']}")
            reject_links_and_special_files(output)
            clean_payload(output)
            normalize_payload(output)
            _reject_secret_content(output)
            if member["kind"] in {"configuration", "extension"}:
                identity = configuration_identity(output)
                expected = contract["roles"][member["role"]] if member["kind"] == "configuration" else member["extension"]
                expected_uuid = str(expected["root_uuid"] if member["kind"] == "configuration" else expected["uuid"]).lower()
                expected_name = expected["configuration_name"] if member["kind"] == "configuration" else expected["name"]
                if identity["uuid"] != expected_uuid or identity["name"] != expected_name or expected.get("version") and identity["version"] != expected["version"]:
                    raise ValueError(f"source_probe_identity_mismatch:{member['routing_group_id']}")
            member["probe"] = analyze_forms(output, member["component_id"])
        _check_cancelled(cancelled, "route")
        tool_versions = _toolchain_versions(contract["acquisition_profile_id"], platform, timeout_seconds=min(timeout_seconds, 120), run=run, cancelled=cancelled)
        manifest = plan_groups(members, exporter, tool_versions)
        if any(group["representation_schema"] == "v8unpack/v1" for group in manifest["groups"]):
            converter = _toolchain_versions(f"{exporter}+v8unpack/v1", platform, timeout_seconds=min(timeout_seconds, 120), run=run, cancelled=cancelled)
            for group in manifest["groups"]:
                if group["representation_schema"] == "v8unpack/v1":
                    group["converter_version"] = converter["converter"]
            manifest.pop("routing_manifest_fingerprint")
            manifest["routing_manifest_fingerprint"] = "sha256:" + sha256(canonical_json(manifest))
    bindings = routing_bindings(repo, connections, upload_drafts)
    probe_results = sorted((member["probe"] for member in members if member.get("probe")), key=lambda item: item["component_id"])
    required_tools = sorted({group["exporter"] for group in manifest["groups"] if group["exporter"] in {"ibcmd", "designer"}} | ({"v8unpack"} if any(group["representation_schema"] == "v8unpack/v1" for group in manifest["groups"]) else set()))
    preimage = {"schema_version": "1", "bindings": bindings, "probe_results": probe_results, "routing_manifest": manifest, "required_tools": required_tools}
    return {**preimage, "routing_plan_fingerprint": "sha256:" + sha256(canonical_json(preimage))}


def acquire(repo: Path, platform: Path, connections: dict[str, dict[str, Any]], *, routing_preview: dict[str, Any], normalizer_version: str = NORMALIZER_VERSION, timeout_seconds: int = 1800, run: callable = subprocess.run, upload_drafts: Path | None = None, cancelled: callable | None = None) -> dict[str, Any]:
    if shutil.disk_usage(repo).free < MINIMUM_FREE_BYTES:
        raise OSError("source acquisition requires at least 1 GiB free space")
    tested = {name: {key: value for key, value in profile.items() if key not in {"db_password", "infobase_password"}} for name, profile in connections.items()}
    contract = validate_role_contract(repo, tested)
    for role in ROLES:
        profile_name = contract["roles"][role]["connection_profile"]
        fresh = preflight_connection(contract["acquisition_profile_id"], platform, connections[profile_name], timeout_seconds=min(timeout_seconds, 120), run=run, cancelled=cancelled)
        if fresh["tested_fingerprint"] != connections[profile_name].get("tested_fingerprint"):
            raise RuntimeError(f"connection profile changed since its saved test: {role}")
    fresh_preview = build_routing_preview(repo, platform, connections, timeout_seconds=timeout_seconds, run=run, upload_drafts=upload_drafts, cancelled=cancelled)
    if routing_preview.get("routing_plan_fingerprint") != fresh_preview["routing_plan_fingerprint"]:
        raise RuntimeError("routing_preview_stale")
    from .source_routing import component_members, source_comparison_epoch
    members = component_members(contract, connections)
    groups = {item["routing_group_id"]: item for item in fresh_preview["routing_manifest"]["groups"]}
    probes = {item["component_id"]: item for item in fresh_preview["probe_results"]}
    pointer_path = repo / "research/active-source-generation.json"
    expected_generation = json.loads(pointer_path.read_text(encoding="utf-8")).get("generation_id") if pointer_path.is_file() else None
    staging_parent = repo / "sources/.staging"; staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="acquire-", dir=staging_parent) as temporary:
        staging = Path(temporary)
        work_root = staging / ".work"; work_root.mkdir()
        for role in ROLES:
            (staging / role).mkdir()
        for member in members:
            _check_cancelled(cancelled, "export")
            group = groups[member["routing_group_id"]]
            role = member["role"]
            if member["kind"] == "configuration":
                output = staging / role / "configuration"
            elif member["kind"] == "extension":
                output = staging / role / "extensions" / member["routing_group_id"].split(":", 1)[1]
            else:
                output = staging / role / "external" / member["name"]
            output.parent.mkdir(parents=True, exist_ok=True)
            work_output = work_root / role / member["routing_group_id"].replace(":", "-")
            if member["kind"] in {"configuration", "extension"}:
                connection = connections[contract["roles"][role]["connection_profile"]]
                representation = group["representation_schema"].removesuffix("/v1")
                _execute_plan(
                    adapter_plan(contract["acquisition_profile_id"], platform, connection, work_output, member["name"], representation=representation, timeout_seconds=timeout_seconds),
                    run=run,
                    timeout_seconds=timeout_seconds,
                    cancelled=cancelled,
                    subject=member["routing_group_id"],
                )
                if not work_output.is_dir() or not any(path.is_file() for path in work_output.rglob("*")):
                    raise ValueError(f"source_export_empty:{member['routing_group_id']}")
                reject_links_and_special_files(work_output)
                shutil.copytree(work_output, output)
                normalize_payload(output)
                identity = configuration_identity(output)
                if member["kind"] == "configuration":
                    expected = contract["roles"][role]
                    if (identity["uuid"], identity["name"], identity["version"]) != (str(expected["root_uuid"]).lower(), expected["configuration_name"], expected["version"]):
                        raise ValueError(f"source_identity_mismatch:{member['routing_group_id']}")
                else:
                    extension = member["extension"]
                    if identity["uuid"] != str(extension["uuid"]).lower() or identity["name"] != extension["name"] or extension.get("version") and identity["version"] != extension["version"]:
                        raise ValueError(f"source_identity_mismatch:{member['routing_group_id']}")
            else:
                source = _verified_artifact_source(upload_drafts, member)
                output.mkdir()
                if member["kind"] in {"epf", "erf"}:
                    extracted = output / "source"
                    _execute_plan(
                        [["v8unpack", "-E", str(source), str(extracted), "--temp", str(work_output.with_suffix(".temp")), "--processes", "1"]],
                        run=run,
                        timeout_seconds=timeout_seconds,
                        cancelled=cancelled,
                        subject=member["routing_group_id"],
                    )
                    if not extracted.is_dir() or not any(path.is_file() for path in extracted.rglob("*")):
                        raise ValueError(f"source_export_empty:{member['routing_group_id']}")
                    reject_links_and_special_files(extracted)
                    clean_payload(extracted); normalize_payload(extracted); _reject_secret_content(extracted)
                elif member["kind"] == "source-tree":
                    _extract_source_tree(source, output / "source")
                else:
                    shutil.copy2(source, output / normalize_relative(member["artifact"]["filename"]))
                    _reject_secret_content(output)
            _check_cancelled(cancelled, "validation")
            clean_payload(output)
            payload = file_manifest(output)
            probe = probes.get(member["component_id"], {})
            metadata = {
                "schema_version": "2",
                "component_id": member["component_id"],
                "kind": member["kind"],
                "routing_group_id": member["routing_group_id"],
                "probe_contract_version": probe.get("probe_contract_version", ""),
                "probe_fingerprint": probe.get("probe_fingerprint", ""),
                "form_counts": probe.get("form_counts", {"managed": 0, "ordinary": 0, "inconclusive": 0}),
                "routing_reason": group["routing_reason"],
                "exporter": group["exporter"],
                "representation_schema": group["representation_schema"],
                "exporter_version": group["exporter_version"],
                "converter_version": group["converter_version"],
                "payload_file_count": len(payload),
                "payload_fingerprint": "sha256:" + sha256(canonical_json(payload)),
            }
            if member["kind"] in {"configuration", "extension"}:
                metadata.update({"uuid": identity["uuid"], "name": identity["name"], "version": identity["version"]})
            if member["kind"] == "extension":
                metadata["active"] = bool(member["extension"]["active"])
            atomic_json(output / "component-manifest.json", metadata)
        shutil.rmtree(work_root)
        if validate_role_contract(repo, tested) != contract or routing_bindings(repo, connections, upload_drafts) != fresh_preview["bindings"]:
            raise RuntimeError("routing_preview_stale")
        _check_cancelled(cancelled, "publication")
        return publish_routed(repo, staging, contract, fresh_preview["routing_manifest"], normalizer_version, expected_generation, source_comparison_epoch)


def publish_routed(repo: Path, staged_roles: Path, contract: dict[str, Any], routing_manifest: dict[str, Any], normalizer_version: str, expected_generation: str | None, epoch_builder: callable) -> dict[str, Any]:
    manifest_preimage = {key: value for key, value in routing_manifest.items() if key != "routing_manifest_fingerprint"}
    if routing_manifest.get("routing_manifest_fingerprint") != "sha256:" + sha256(canonical_json(manifest_preimage)):
        raise ValueError("routing manifest fingerprint mismatch")
    with repository_lock(repo):
        pointer_path = repo / "research/active-source-generation.json"
        current = json.loads(pointer_path.read_text(encoding="utf-8")).get("generation_id") if pointer_path.is_file() else None
        if current != expected_generation:
            raise RuntimeError("stale active source generation")
        role_manifests, role_fingerprints = {}, {}
        for role in ROLES:
            role_root = confined(staged_roles, role)
            if not (role_root / "configuration").is_dir():
                raise ValueError(f"staged role is incomplete: {role}")
            clean_payload(role_root)
            role_manifests[role] = file_manifest(role_root)
            role_fingerprints[role] = "sha256:" + sha256(canonical_json(role_manifests[role]))
        safe_contract = json.loads(canonical_json(contract))
        reject_secrets(safe_contract, "source contract")
        contract_fingerprint = "sha256:" + sha256(canonical_json(safe_contract))
        routing_fingerprint = routing_manifest["routing_manifest_fingerprint"]
        preimage = {
            "source_contract_fingerprint": contract_fingerprint,
            "normalizer_version": normalizer_version,
            "roles": role_fingerprints,
            "routing_manifest_fingerprint": routing_fingerprint,
        }
        generation_id = sha256(canonical_json(preimage))
        destination = repo / "sources/generations" / generation_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = Path(tempfile.mkdtemp(prefix=f".{generation_id}.", dir=destination.parent))
            try:
                for role in ROLES:
                    shutil.copytree(staged_roles / role, temporary / role)
                (temporary / "source-contract.json").write_bytes(canonical_json(safe_contract) + b"\n")
                (temporary / "routing-manifest.json").write_bytes(canonical_json(routing_manifest) + b"\n")
                for path in temporary.rglob("*"):
                    if path.is_file():
                        with path.open("rb") as stream:
                            os.fsync(stream.fileno())
                os.replace(temporary, destination)
                parent_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        if (destination / "source-contract.json").read_bytes() != canonical_json(safe_contract) + b"\n" or (destination / "routing-manifest.json").read_bytes() != canonical_json(routing_manifest) + b"\n":
            raise RuntimeError("existing routed source generation is inconsistent")
        if any(file_manifest(destination / role) != role_manifests[role] for role in ROLES):
            raise RuntimeError("existing routed source generation role payload is inconsistent")
        components = []
        for role in ROLES:
            for manifest_path in sorted((destination / role).rglob("component-manifest.json")):
                component = json.loads(manifest_path.read_text(encoding="utf-8"))
                path = manifest_path.parent
                components.append({
                    "component_id": component["component_id"],
                    "kind": component["kind"],
                    "path": path.relative_to(destination).as_posix(),
                    "representation_schema": component["representation_schema"],
                    "routing_group_id": component["routing_group_id"],
                    "fingerprint": "sha256:" + sha256(canonical_json(file_manifest(path))),
                })
        pointer = {
            "schema_version": "2",
            "generation_id": generation_id,
            "acquisition_profile_id": contract["acquisition_profile_id"],
            "normalizer_version": normalizer_version,
            "source_contract_fingerprint": contract_fingerprint,
            "roles": role_fingerprints,
            "routing_manifest_path": "routing-manifest.json",
            "routing_manifest_fingerprint": routing_fingerprint,
            "components": components,
        }
        pointer["source_comparison_epoch_fingerprint"] = epoch_builder(pointer, routing_manifest)
        atomic_json(pointer_path, pointer)
        return pointer


def publish(repo: Path, staged_roles: Path, contract: dict[str, Any], normalizer_version: str = NORMALIZER_VERSION, expected_generation: str | None | object = _UNSET) -> dict[str, Any]:
    profile_id = contract["acquisition_profile_id"]
    if profile_id not in LEGACY_PROFILES:
        raise ValueError("routed source publication requires a routing manifest")
    representation = LEGACY_PROFILES[profile_id][1]
    with repository_lock(repo):
        pointer_path = repo / "research/active-source-generation.json"
        current_generation = json.loads(pointer_path.read_text(encoding="utf-8")).get("generation_id") if pointer_path.is_file() else None
        if expected_generation is not _UNSET and current_generation != expected_generation:
            raise RuntimeError("stale active source generation")
        role_fingerprints: dict[str, str] = {}
        role_manifests: dict[str, list[dict[str, Any]]] = {}
        for role in ROLES:
            root = confined(staged_roles, role)
            if not root.is_dir() or not (root / "configuration").is_dir():
                raise ValueError(f"staged role is incomplete: {role}")
            clean_payload(root)
            manifest = file_manifest(root)
            if not manifest:
                raise ValueError(f"staged role is empty: {role}")
            role_manifests[role] = manifest
            role_fingerprints[role] = "sha256:" + sha256(canonical_json(manifest))
        safe_contract = json.loads(canonical_json(contract))
        reject_secrets(safe_contract, "source contract")
        contract_hash = sha256(canonical_json(safe_contract))
        preimage = {"schema_version": "1", "acquisition_profile_version": "1", "normalizer_version": normalizer_version, "source_contract_sha256": contract_hash, "roles": role_fingerprints}
        generation_id = sha256(canonical_json(preimage))
        destination = repo / "sources/generations" / generation_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = Path(tempfile.mkdtemp(prefix=f".{generation_id}.", dir=destination.parent))
            try:
                for role in ROLES:
                    shutil.copytree(staged_roles / role, temporary / role)
                (temporary / "source-contract.json").write_bytes(canonical_json(safe_contract) + b"\n")
                for path in temporary.rglob("*"):
                    if path.is_file():
                        with path.open("rb") as stream: os.fsync(stream.fileno())
                os.replace(temporary, destination)
                parent_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        expected_contract = canonical_json(safe_contract) + b"\n"
        if (destination / "source-contract.json").read_bytes() != expected_contract or any(file_manifest(destination / role) != role_manifests[role] for role in ROLES):
            raise RuntimeError("existing source generation does not match its deterministic identity")
        components = []
        for role in ROLES:
            role_root = destination / role
            for kind, parent in (("configuration", role_root / "configuration"), ("extension", role_root / "extensions"), ("external", role_root / "external")):
                paths = [parent] if kind == "configuration" else sorted(path for path in parent.iterdir() if path.is_dir()) if parent.is_dir() else []
                for path in paths:
                    identity = "configuration" if kind == "configuration" else path.name
                    components.append({"component_id": f"{role}:{kind}:{identity}" if kind != "configuration" else f"{role}:configuration", "kind": kind, "path": path.relative_to(destination).as_posix(), "fingerprint": "sha256:" + sha256(canonical_json(file_manifest(path)))})
        pointer = {"schema_version": "1", "generation_id": generation_id, "acquisition_profile_id": profile_id, "representation_schema": representation, "normalizer_version": normalizer_version, "source_contract_sha256": contract_hash, "roles": role_fingerprints, "components": components}
        atomic_json(repo / "research/active-source-generation.json", pointer)
        return pointer


def validate_active(repo: Path, *, deep: bool = False, require_tracked_clean: bool = False) -> dict[str, Any]:
    import subprocess
    deep = deep or require_tracked_clean
    pointer_path = repo / "research/active-source-generation.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if pointer.get("schema_version") == "2":
        return _validate_active_routed(repo, pointer, deep=deep, require_tracked_clean=require_tracked_clean)
    if pointer.get("schema_version") != "1":
        raise ValueError("unsupported active source schema version")
    generation_id = str(pointer.get("generation_id", ""))
    if not generation_id or generation_id != Path(generation_id).name:
        raise ValueError("invalid active source generation ID")
    root = confined(repo / "sources/generations", generation_id)
    if require_tracked_clean:
        validate_tracked_clean(repo, [root, pointer_path])
    contract_path = root / "source-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    reject_secrets(contract, "active source contract")
    if sha256(canonical_json(contract)) != pointer.get("source_contract_sha256"):
        raise ValueError("active source contract hash mismatch")
    actual: dict[str, str] = {}
    for role in ROLES:
        role_root = confined(root, role)
        if not role_root.is_dir():
            raise ValueError(f"active source role is missing: {role}")
        actual[role] = _immutable_role_fingerprint(str(role_root.resolve())) if deep else str(pointer.get("roles", {}).get(role, ""))
        if not actual[role].startswith("sha256:"):
            raise ValueError(f"active source role fingerprint is missing: {role}")
    if deep and actual != pointer.get("roles"):
        raise ValueError("active source role fingerprint mismatch")
    if not isinstance(pointer.get("components"), list) or any(not str(item.get("component_id", "")) or not confined(root, str(item.get("path", ""))).is_dir() for item in pointer["components"]):
        raise ValueError("active source component catalog is missing or stale")
    if deep and any(item.get("fingerprint") != "sha256:" + sha256(canonical_json(file_manifest(confined(root, item["path"])))) for item in pointer["components"]):
        raise ValueError("active source component catalog is missing or stale")
    preimage = {"schema_version": "1", "acquisition_profile_version": "1", "normalizer_version": pointer["normalizer_version"], "source_contract_sha256": pointer["source_contract_sha256"], "roles": pointer["roles"]}
    if sha256(canonical_json(preimage)) != generation_id:
        raise ValueError("active source generation ID mismatch")
    current_infobases, current_artifacts = load_contract(repo)
    current_contract = {"schema_version": "1", "acquisition_profile_id": current_infobases.get("acquisition_profile"), "roles": current_infobases.get("roles", {}), "artifacts": current_artifacts.get("artifacts", [])}
    active_contract = {**contract, "artifacts": [{key: value for key, value in item.items() if key != "external_artifact_id"} for item in contract.get("artifacts", [])]}
    if canonical_json(current_contract) != canonical_json(active_contract):
        raise ValueError("tracked source declarations changed; reacquisition is required")
    return pointer


def _validate_active_routed(repo: Path, pointer: dict[str, Any], *, deep: bool, require_tracked_clean: bool) -> dict[str, Any]:
    generation_id = str(pointer.get("generation_id", ""))
    if len(generation_id) != 64 or generation_id != Path(generation_id).name:
        raise ValueError("invalid active source generation ID")
    root = confined(repo / "sources/generations", generation_id)
    pointer_path = repo / "research/active-source-generation.json"
    if require_tracked_clean:
        validate_tracked_clean(repo, [root, pointer_path])
    contract = json.loads((root / "source-contract.json").read_text(encoding="utf-8"))
    reject_secrets(contract, "active source contract")
    contract_fingerprint = "sha256:" + sha256(canonical_json(contract))
    if contract_fingerprint != pointer.get("source_contract_fingerprint"):
        raise ValueError("active source contract fingerprint mismatch")
    manifest = json.loads((root / "routing-manifest.json").read_text(encoding="utf-8"))
    manifest_preimage = {key: value for key, value in manifest.items() if key != "routing_manifest_fingerprint"}
    manifest_fingerprint = "sha256:" + sha256(canonical_json(manifest_preimage))
    if manifest_fingerprint != manifest.get("routing_manifest_fingerprint") or manifest_fingerprint != pointer.get("routing_manifest_fingerprint"):
        raise ValueError("active source routing manifest fingerprint mismatch")
    groups = {group["routing_group_id"]: group for group in manifest.get("groups", [])}
    if len(groups) != len(manifest.get("groups", [])):
        raise ValueError("duplicate source routing group")
    group_keys = {"routing_group_id", "kind", "members", "absent_roles", "probe_contract_version", "probe_fingerprints", "form_counts", "routing_reason", "exporter", "representation_schema", "exporter_version", "converter_version"}
    reasons = {"managed_only", "ordinary_form_present", "inconclusive_form_payload", "no_forms", "binary_container_requires_v8unpack", "declared_source_tree", "raw_artifact"}
    representations = {"xml-hierarchical/v1", "v8unpack/v1", "source-tree/v1", "raw-binary/v1"}
    for group in groups.values():
        member_roles = [member.get("role") for member in group.get("members", [])]
        if (
            set(group) != group_keys
            or group.get("routing_reason") not in reasons
            or group.get("representation_schema") not in representations
            or group.get("exporter") not in {"ibcmd", "designer", "verified-upload"}
            or sorted(member_roles + group.get("absent_roles", [])) != sorted(ROLES)
            or len(member_roles) != len(set(member_roles))
            or set(group.get("form_counts", {})) != {"managed", "ordinary", "inconclusive"}
        ):
            raise ValueError("invalid source routing group contract")
    actual_roles = {}
    catalog = {}
    for role in ROLES:
        role_root = confined(root, role)
        if not role_root.is_dir():
            raise ValueError(f"active source role is missing: {role}")
        actual_roles[role] = _immutable_role_fingerprint(str(role_root.resolve())) if deep else pointer.get("roles", {}).get(role, "")
    if any(not str(value).startswith("sha256:") for value in actual_roles.values()) or deep and actual_roles != pointer.get("roles"):
        raise ValueError("active source role fingerprint mismatch")
    for item in pointer.get("components", []):
        component_root = confined(root, str(item.get("path", "")))
        component = json.loads((component_root / "component-manifest.json").read_text(encoding="utf-8"))
        payload_manifest = [entry for entry in file_manifest(component_root) if entry["path"] != "component-manifest.json"] if deep else []
        group = groups.get(component.get("routing_group_id"))
        component_keys = {"schema_version", "component_id", "kind", "routing_group_id", "probe_contract_version", "probe_fingerprint", "form_counts", "routing_reason", "exporter", "representation_schema", "exporter_version", "converter_version", "payload_file_count", "payload_fingerprint"}
        if component.get("kind") in {"configuration", "extension"}:
            component_keys |= {"uuid", "name", "version"}
        if component.get("kind") == "extension":
            component_keys.add("active")
        counts_valid = set(component.get("form_counts", {})) == {"managed", "ordinary", "inconclusive"} and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in component.get("form_counts", {}).values())
        probe_valid = component.get("probe_contract_version") in {"", "form-probe/v1"} and (component.get("probe_fingerprint") == "" or re.fullmatch(r"sha256:[0-9a-f]{64}", str(component.get("probe_fingerprint"))) is not None)
        form_route = component.get("routing_reason") in {"managed_only", "ordinary_form_present", "inconclusive_form_payload", "no_forms"}
        if (
            set(component) != component_keys
            or component.get("schema_version") != "2"
            or not counts_valid
            or not probe_valid
            or component.get("kind") in {"configuration", "extension"} and (
                not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(component.get("uuid", "")))
                or not str(component.get("name", ""))
                or not isinstance(component.get("version"), str)
            )
            or component.get("kind") == "extension" and not isinstance(component.get("active"), bool)
            or (form_route and component.get("kind") not in {"configuration", "extension"})
            or (component.get("representation_schema") == "xml-hierarchical/v1" and component.get("routing_reason") not in {"managed_only", "no_forms"})
            or (component.get("representation_schema") == "v8unpack/v1" and component.get("routing_reason") not in {"ordinary_form_present", "inconclusive_form_payload", "binary_container_requires_v8unpack"})
            or item.get("component_id") != component.get("component_id")
            or item.get("routing_group_id") != component.get("routing_group_id")
            or item.get("representation_schema") != component.get("representation_schema")
            or not group
            or not any(member["component_id"] == component["component_id"] for member in group["members"])
            or component["representation_schema"] != group["representation_schema"]
            or component["routing_reason"] != group["routing_reason"]
            or component["exporter"] != group["exporter"]
            or component["exporter_version"] != group["exporter_version"]
            or component["converter_version"] != group["converter_version"]
            or deep and component["payload_file_count"] != len(payload_manifest)
            or deep and component["payload_fingerprint"] != "sha256:" + sha256(canonical_json(payload_manifest))
            or deep and item.get("fingerprint") != "sha256:" + sha256(canonical_json(file_manifest(component_root)))
        ):
            raise ValueError("active source component catalog is missing or stale")
        catalog[component["component_id"]] = component
    expected_members = {member["component_id"] for group in groups.values() for member in group["members"]}
    if set(catalog) != expected_members or len(catalog) != len(pointer.get("components", [])):
        raise ValueError("active source routing coverage mismatch")
    for group in groups.values():
        group_components = [catalog[member["component_id"]] for member in group["members"]]
        expected_probes = sorted(
            ({"component_id": component["component_id"], "probe_fingerprint": component["probe_fingerprint"]} for component in group_components if component["probe_fingerprint"]),
            key=lambda item: item["component_id"],
        )
        counts = {kind: sum(component["form_counts"][kind] for component in group_components) for kind in ("managed", "ordinary", "inconclusive")}
        if expected_probes != group["probe_fingerprints"] or counts != group["form_counts"]:
            raise ValueError("active source routing evidence mismatch")
    if deep and any(path.is_symlink() or path.name.startswith(".work") or path.suffix.lower() in {".cf", ".cfe", ".epf", ".erf"} for path in root.rglob("*")):
        raise ValueError("active source generation contains temporary or binary payload")
    preimage = {
        "source_contract_fingerprint": contract_fingerprint,
        "normalizer_version": pointer["normalizer_version"],
        "roles": pointer["roles"],
        "routing_manifest_fingerprint": manifest_fingerprint,
    }
    if sha256(canonical_json(preimage)) != generation_id:
        raise ValueError("active source generation ID mismatch")
    from .source_routing import source_comparison_epoch
    if pointer.get("source_comparison_epoch_fingerprint") != source_comparison_epoch(pointer, manifest):
        raise ValueError("active source comparison epoch mismatch")
    current_infobases, current_artifacts = load_contract(repo)
    current_contract = {"schema_version": contract.get("schema_version"), "acquisition_profile_id": current_infobases.get("acquisition_profile"), "roles": current_infobases.get("roles", {}), "artifacts": current_artifacts.get("artifacts", [])}
    active_contract = {**contract, "artifacts": [{key: value for key, value in item.items() if key != "external_artifact_id"} for item in contract.get("artifacts", [])]}
    if canonical_json(current_contract) != canonical_json(active_contract):
        raise ValueError("tracked source declarations changed; reacquisition is required")
    return pointer
