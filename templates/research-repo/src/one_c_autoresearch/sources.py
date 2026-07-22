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
PROFILES = {f"{exporter}+{representation}/v1": (exporter, representation) for exporter in EXPORTERS for representation in REPRESENTATIONS}
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
    return {"schema_version": "1", "acquisition_profile_id": profile_id, "roles": roles, "artifacts": artifacts.get("artifacts", [])}


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
    exporter, representation = PROFILES[profile_id]
    ibcmd = _run_command(run, [str(platform / "ibcmd"), "--version"], cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
    match = re.search(r"\b8\.\d+\.\d+\.\d+\b", ibcmd.stdout)
    if ibcmd.returncode or not match:
        raise RuntimeError("ibcmd version preflight failed")
    versions = {"platform": match.group(0), "exporter": exporter}
    if exporter == "designer" and not (platform / "1cv8").is_file():
        raise RuntimeError("Designer executable preflight failed")
    if representation == "v8unpack":
        executable = shutil.which("v8unpack")
        if not executable:
            raise RuntimeError("v8unpack executable is unavailable")
        result = _run_command(run, [executable, "-h"], cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
        converter = re.search(r"\bv8unpack\s+(\d+\.\d+\.\d+)\b", result.stdout)
        if result.returncode or not converter or converter.group(1) != V8UNPACK_VERSION:
            raise RuntimeError(f"v8unpack {V8UNPACK_VERSION} is required")
        versions["converter"] = f"v8unpack {converter.group(1)}"
    elif representation == "edt-project":
        executable = shutil.which("1cedtcli")
        resolved = str(Path(executable).resolve()) if executable else ""
        converter = re.search(r"1c-edt-([^/]+?)-x86_64(?:/|$)", resolved)
        if not converter or converter.group(1) != EDT_VERSION:
            raise RuntimeError(f"1C:EDT {EDT_VERSION} is required")
        versions["converter"] = f"1C:EDT {converter.group(1)}"
    return versions


def preflight_connection(profile_id: str, platform: Path, connection: dict[str, Any], *, timeout_seconds: int = 120, run: callable = subprocess.run, cancelled: callable | None = None) -> dict[str, Any]:
    if profile_id not in PROFILES:
        raise ValueError("unsupported acquisition profile")
    exporter = PROFILES[profile_id][0]
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


def adapter_plan(profile_id: str, platform: Path, connection: dict[str, str], output: Path, extension_name: str | None = None, *, workspace: Path | None = None, project_name: str | None = None, timeout_seconds: int = 1800) -> list[list[str]]:
    try:
        exporter, representation = PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"unsupported acquisition profile: {profile_id}") from exc
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


def normalize_payload(root: Path) -> None:
    volatile_period = re.compile(br"(<pl:(?:begin|end)>)(\d{4}-\d{2}-\d{2})(T\d{2}:\d{2}:\d{2}</pl:(?:begin|end)>)")
    for path in root.rglob("*.xml"):
        raw = path.read_bytes()
        normalized = volatile_period.sub(br"\g<1>2000-01-01\g<3>", raw)
        if normalized != raw:
            path.write_bytes(normalized)


def _reject_secret_content(root: Path) -> None:
    patterns = (re.compile(br"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), re.compile(br"(?i)\b(?:password|passwd|pwd|token|secret)\s*[:=]\s*['\"]?[^\s'\";]{8,}"))
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


def acquire(repo: Path, platform: Path, connections: dict[str, dict[str, Any]], *, normalizer_version: str = NORMALIZER_VERSION, timeout_seconds: int = 1800, run: callable = subprocess.run, upload_drafts: Path | None = None, cancelled: callable | None = None) -> dict[str, Any]:
    if shutil.disk_usage(repo).free < MINIMUM_FREE_BYTES:
        raise OSError("source acquisition requires at least 1 GiB free space")
    tested = {name: {key: value for key, value in profile.items() if key not in {"db_password", "infobase_password"}} for name, profile in connections.items()}
    contract = validate_role_contract(repo, tested)
    for role in ROLES:
        profile_name = contract["roles"][role]["connection_profile"]
        fresh = preflight_connection(contract["acquisition_profile_id"], platform, connections[profile_name], timeout_seconds=min(timeout_seconds, 120), run=run, cancelled=cancelled)
        if fresh["tested_fingerprint"] != connections[profile_name].get("tested_fingerprint"):
            raise RuntimeError(f"connection profile changed since its saved test: {role}")
    pointer_path = repo / "research/active-source-generation.json"
    expected_generation = json.loads(pointer_path.read_text(encoding="utf-8")).get("generation_id") if pointer_path.is_file() else None
    staging_parent = repo / "sources/.staging"; staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="acquire-", dir=staging_parent) as temporary:
        staging = Path(temporary)
        work_root = staging / ".work"
        for role in ROLES:
            profile_name = contract["roles"][role]["connection_profile"]
            connection = connections.get(profile_name)
            if not connection or not isinstance(connection.get("extensions"), list):
                raise ValueError(f"current extension enumeration is required: {role}")
            role_root = staging / role; role_root.mkdir()
            role_work = work_root / role
            role_work.mkdir(parents=True)
            components: list[tuple[dict[str, Any] | None, Path]] = [(None, role_root / "configuration")]
            seen_extensions: set[str] = set()
            for extension in connection["extensions"]:
                uuid = str(extension.get("uuid", "")).lower(); name = str(extension.get("name", "")).strip()
                if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", uuid) or uuid in seen_extensions or not name or not isinstance(extension.get("active"), bool):
                    raise ValueError(f"invalid or duplicate extension UUID: {role}")
                seen_extensions.add(uuid)
                components.append((extension, role_root / f"extensions/{uuid}"))
            for extension, output in components:
                extension_name = str(extension["name"]) if extension else None
                output.parent.mkdir(parents=True, exist_ok=True)
                component_name = "base" if extension is None else f"ext-{extension['uuid']}"
                representation = PROFILES[contract["acquisition_profile_id"]][1]
                work_output = role_work / (f"workspace/{component_name}" if representation == "edt-project" else f"projects/{component_name}")
                work_output.parent.mkdir(parents=True, exist_ok=True)
                for command in adapter_plan(contract["acquisition_profile_id"], platform, connection, work_output, extension_name or None, workspace=role_work / "workspace", project_name=component_name, timeout_seconds=timeout_seconds):
                    result = _run_command(run, command, cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
                    if result.returncode:
                        raise RuntimeError(f"source exporter/converter failed for {role} with exit {result.returncode}")
                if not work_output.is_dir() or not any(path.is_file() for path in work_output.rglob("*")):
                    raise ValueError(f"empty acquisition component: {role}")
                shutil.copytree(work_output, output)
                normalize_payload(output)
                identity = configuration_identity(output); actual_uuid = identity["uuid"]
                if extension is None and (actual_uuid != str(contract["roles"][role]["root_uuid"]).lower() or identity["name"] != contract["roles"][role]["configuration_name"] or identity["version"] != contract["roles"][role]["version"]):
                    raise ValueError(f"main configuration identity mismatch: {role}; expected {contract['roles'][role]['configuration_name']}/{contract['roles'][role]['root_uuid']}/{contract['roles'][role]['version']}, actual {identity['name']}/{actual_uuid}/{identity['version']}")
                if extension is not None:
                    declared_uuid = str(extension.get("uuid", "")).lower()
                    if declared_uuid != actual_uuid or identity["name"] != extension_name or extension.get("version") and identity["version"] != extension["version"]: raise ValueError(f"extension identity mismatch: {role}/{extension_name}")
                payload = file_manifest(output)
                metadata = {"schema_version": "1", "kind": "configuration" if extension_name is None else "extension", "name": contract["roles"][role]["configuration_name"] if extension_name is None else extension_name, "version": contract["roles"][role]["version"] if extension_name is None else next((item.get("version", "") for item in connection["extensions"] if item.get("name") == extension_name), ""), "representation_schema": PROFILES[contract["acquisition_profile_id"]][1], "payload_file_count": len(payload), "payload_fingerprint": "sha256:" + sha256(canonical_json(payload))}
                metadata["uuid"] = actual_uuid
                if extension_name is not None:
                    metadata["active"] = extension["active"]
                atomic_json(output / "component-manifest.json", metadata)
            for artifact in (item for item in contract["artifacts"] if item["role"] == role):
                identifier = artifact["external_artifact_id"]
                filename = normalize_relative(str(artifact.get("filename", "")))
                if not upload_drafts:
                    raise ValueError(f"external artifact upload draft is unavailable: {identifier}")
                source = confined(upload_drafts, f"{role}/{identifier}/{filename}")
                if not source.is_file():
                    raise ValueError(f"external artifact upload is missing: {identifier}")
                import hashlib
                checksum = hashlib.sha256()
                with source.open("rb") as uploaded:
                    for chunk in iter(lambda: uploaded.read(1024 * 1024), b""):
                        checksum.update(chunk)
                size = source.stat().st_size; digest = checksum.hexdigest()
                if size != artifact["declared_size_bytes"] or artifact.get("sha256") and digest != str(artifact["sha256"]).removeprefix("sha256:").lower():
                    raise ValueError(f"external artifact upload does not match declaration: {identifier}")
                target = role_root / "external" / identifier; target.mkdir(parents=True)
                source_available = False
                if artifact["kind"] == "source-tree":
                    _extract_source_tree(source, target / "source"); source_available = True
                else:
                    shutil.copy2(source, target / filename)
                    _reject_secret_content(target)
                    if artifact["kind"] in {"epf", "erf"} and PROFILES[contract["acquisition_profile_id"]][1] == "v8unpack":
                        extracted = target / "source"
                        command = ["v8unpack", "-E", str(target / filename), str(extracted), "--temp", str(role_work / f"external-{identifier}-temp"), "--processes", "1"]
                        result = _run_command(run, command, cancelled=cancelled, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout_seconds, env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
                        if result.returncode == 0 and extracted.is_dir() and any(path.is_file() for path in extracted.rglob("*")):
                            clean_payload(extracted); _reject_secret_content(extracted); source_available = True
                        else:
                            shutil.rmtree(extracted, ignore_errors=True)
                artifact_toml = f'schema_version = "1"\nid = "{identifier}"\nkind = {json.dumps(artifact["kind"])}\nsemantic_key = {json.dumps(artifact["semantic_key"], ensure_ascii=False)}\nfilename = {json.dumps(filename, ensure_ascii=False)}\nsize_bytes = {size}\nsha256 = "{digest}"\nsource_status = "{"available" if source_available else "missing"}"\n'
                (target / "artifact.toml").write_text(artifact_toml, encoding="utf-8")
        shutil.rmtree(work_root)
        if validate_role_contract(repo, tested) != contract:
            raise RuntimeError("source declarations changed during acquisition")
        return publish(repo, staging, contract, normalizer_version, expected_generation)


def publish(repo: Path, staged_roles: Path, contract: dict[str, Any], normalizer_version: str = NORMALIZER_VERSION, expected_generation: str | None | object = _UNSET) -> dict[str, Any]:
    profile_id = contract["acquisition_profile_id"]
    representation = PROFILES[profile_id][1]
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
    pointer_path = repo / "research/active-source-generation.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
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
    if not isinstance(pointer.get("components"), list) or any(not str(item.get("component_id", "")) or not confined(root, str(item.get("path", ""))).is_dir() or item.get("fingerprint") != "sha256:" + sha256(canonical_json(file_manifest(confined(root, item["path"])))) for item in pointer["components"]):
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
