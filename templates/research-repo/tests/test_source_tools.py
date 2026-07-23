from pathlib import Path

from one_c_autoresearch.source_tools import _platform_roots, discover_tools


def executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_inventory_reports_multiple_platforms_and_stable_fingerprint(tmp_path: Path, monkeypatch):
    roots = [tmp_path / "8.3.27.1989", tmp_path / "8.5.4.1306"]
    for root in roots:
        executable(root / "ibcmd", f"echo {root.name}")
        executable(root / "1cv8", "exit 0")
    monkeypatch.setattr("one_c_autoresearch.source_tools.shutil.which", lambda _name: None)
    one, two = discover_tools(map(str, reversed(roots))), discover_tools(map(str, roots))
    assert one["inventory_fingerprint"] == two["inventory_fingerprint"]
    ibcmd = next(item for item in one["tools"] if item["tool_id"] == "ibcmd")
    assert ibcmd["status"] == "ready"
    configured = [item["version"] for item in ibcmd["instances"] if item["path"].startswith(str(tmp_path))]
    assert configured == ["8.3.27.1989", "8.5.4.1306"]


def test_inventory_marks_unsupported_v8unpack_incompatible(tmp_path: Path, monkeypatch):
    converter = executable(tmp_path / "v8unpack", "echo 'v8unpack 9.9.9'")
    monkeypatch.setattr("one_c_autoresearch.source_tools._platform_roots", lambda _roots: ([], 0))
    monkeypatch.setattr("one_c_autoresearch.source_tools.shutil.which", lambda name: str(converter) if name == "v8unpack" else None)
    item = next(value for value in discover_tools([])["tools"] if value["tool_id"] == "v8unpack")
    assert (item["status"], item["instances"][0]["version"]) == ("incompatible", "9.9.9")


def test_inventory_bounds_output(tmp_path: Path, monkeypatch):
    converter = executable(tmp_path / "v8unpack", "yes x | head -c 70000")
    monkeypatch.setattr("one_c_autoresearch.source_tools._platform_roots", lambda _roots: ([], 0))
    monkeypatch.setattr("one_c_autoresearch.source_tools.shutil.which", lambda name: str(converter) if name == "v8unpack" else None)
    result = discover_tools([])
    assert result["complete"] is False
    assert {"code": "probe_output_limit_reached", "subject": "v8unpack", "omitted_count": 0} in result["diagnostics"]


def test_inventory_priority_limit_and_tool_scoped_partial_state(tmp_path: Path, monkeypatch):
    configured = [tmp_path / f"8.3.27.{index:04d}" for index in reversed(range(20))]
    roots, omitted = _platform_roots(map(str, configured))
    assert roots[:16] == sorted(path.resolve() for path in configured)[:16]
    assert omitted >= 4
    monkeypatch.setattr("one_c_autoresearch.source_tools._platform_roots", lambda _roots: ([], 2))
    monkeypatch.setattr("one_c_autoresearch.source_tools.shutil.which", lambda _name: None)
    result = discover_tools([])
    states = {item["tool_id"]: item["status"] for item in result["tools"]}
    assert states == {"designer": "degraded", "edt": "unavailable", "ibcmd": "degraded", "v8unpack": "unavailable"}


def test_inventory_stops_at_total_scan_deadline(tmp_path: Path, monkeypatch):
    root = tmp_path / "8.3.27.1989"; root.mkdir()
    monkeypatch.setattr("one_c_autoresearch.source_tools._platform_roots", lambda _roots: ([root], 0))
    moments = iter((0.0, 999.0))
    monkeypatch.setattr("one_c_autoresearch.source_tools.time.monotonic", lambda: next(moments))
    monkeypatch.setattr("one_c_autoresearch.source_tools.shutil.which", lambda _name: None)
    result = discover_tools([])
    assert {"code": "scan_deadline_reached", "subject": "platform_roots", "omitted_count": 0} in result["diagnostics"]
    assert result["complete"] is False
