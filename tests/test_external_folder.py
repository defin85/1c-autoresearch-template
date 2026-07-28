import os
import time
from pathlib import Path

import pytest

from one_c_autoresearch.contracts import sha256
from one_c_autoresearch.external_folder import PreviewStore, normalize_path, serialize_declarations, strip_browser_root
from one_c_autoresearch.cli import parser
from one_c_autoresearch.sources import draft_fingerprint


def repository(root: Path) -> Path:
    research = root / "research"; research.mkdir(parents=True)
    (research / "external-artifacts.toml").write_text('schema_version = "1"\n\n[[artifacts]]\nrole = "vendor_baseline"\nkind = "epf"\nsemantic_key = "keep"\nfilename = "keep.epf"\ndeclared_size_bytes = 1\nsha256 = "' + sha256(b"k") + '"\n', encoding="utf-8")
    (research / "infobases.toml").write_text('schema_version = "1"\n', encoding="utf-8")
    return root


def test_browser_root_and_portable_paths():
    assert strip_browser_root(["Папка/Вложенная/Отчёт.EPF", "Папка/a.erf"]) == ["Вложенная/Отчёт.EPF", "a.erf"]
    for invalid in ("../a.epf", "/a.epf", "C:/a.epf", "a//b.epf", "a/\x00.epf"):
        with pytest.raises(ValueError): normalize_path(invalid)
    with pytest.raises(ValueError): strip_browser_root(["one/a.epf", "two/b.epf"])


def test_scan_is_deterministic_preserves_declarations_and_warns_duplicates(tmp_path: Path):
    repo = repository(tmp_path / "repo"); folder = tmp_path / "input"; (folder / "nested").mkdir(parents=True)
    (folder / "nested/Отчёт.EPF").write_bytes(b"same"); (folder / "other.erf").write_bytes(b"same"); (folder / "ignored.txt").write_text("x")
    drafts = tmp_path / "drafts"; drafts.mkdir()
    first = PreviewStore(repo, tmp_path / "previews", drafts).scan_folder(folder, "workflow")
    second = PreviewStore(repo, tmp_path / "previews-2", drafts).scan_folder(folder, "workflow")
    assert [(x["filename"], x["kind"], x["size_bytes"], x["sha256"], x["semantic_key"], x["status"]) for x in first["entries"]] == [(x["filename"], x["kind"], x["size_bytes"], x["sha256"], x["semantic_key"], x["status"]) for x in second["entries"]]
    assert first["ignored_unsupported_count"] == 1
    assert all(item["duplicate_of"] for item in first["entries"])
    assert '-semantic_key = "keep"' not in first["declaration_diff"]
    assert first["declaration_fingerprint"].startswith("sha256:") and first["draft_fingerprint"] == draft_fingerprint(drafts)


def test_scan_rejects_links_and_expired_preview(tmp_path: Path):
    repo = repository(tmp_path / "repo"); folder = tmp_path / "input"; folder.mkdir(); target = folder / "real.epf"; target.write_bytes(b"x")
    os.symlink(target, folder / "link.epf")
    store = PreviewStore(repo, tmp_path / "previews", tmp_path / "drafts")
    scanned = store.scan_folder(folder, "workflow")
    assert next(item for item in scanned["entries"] if item["filename"] == "link.epf")["status"] == "invalid"
    preview = store.create("workflow"); preview["expires_at"] = time.time() - 1; store.save(preview)
    with pytest.raises(RuntimeError, match="expired"): store.load(preview["preview_id"])


def test_canonical_toml_field_order():
    raw = serialize_declarations([{"role": "target_cf", "kind": "epf", "semantic_key": "folder/report", "filename": "folder/report.epf", "declared_size_bytes": 7, "sha256": sha256(b"payload")}]).decode()
    assert raw.index("role =") < raw.index("kind =") < raw.index("semantic_key =") < raw.index("filename =") < raw.index("declared_size_bytes =") < raw.index("sha256 =")


def test_confirm_commits_declaration_stages_bytes_and_preserves_existing(tmp_path: Path):
    repo = repository(tmp_path / "repo"); folder = tmp_path / "input"; folder.mkdir(); (folder / "new.epf").write_bytes(b"payload")
    drafts = tmp_path / "drafts"; drafts.mkdir(); store = PreviewStore(repo, tmp_path / "previews", drafts)
    preview = store.scan_folder(folder, "workflow")
    result = store.confirm(preview["preview_id"], [{"entry_id": preview["entries"][0]["entry_id"]}], workflow_fingerprint="workflow", expected_declaration_fingerprint=preview["declaration_fingerprint"], expected_draft_fingerprint=preview["draft_fingerprint"])
    assert result["status"] == "complete" and result["acquisition_pending"]
    declaration = (repo / "research/external-artifacts.toml").read_text(encoding="utf-8")
    assert 'semantic_key = "keep"' in declaration and 'semantic_key = "new"' in declaration
    assert list(drafts.rglob("new.epf"))[0].read_bytes() == b"payload"
    assert not (tmp_path / "previews" / preview["preview_id"] / "bytes").exists()


def test_cli_has_exact_folder_commands():
    assert parser().parse_args(["external-artifacts", "folder-preview", "/tmp/input"]).external_command == "folder-preview"
    assert parser().parse_args(["external-artifacts", "folder-confirm", "a" * 32, "--expected-fingerprint", "sha256:x"]).external_command == "folder-confirm"


def test_confirmation_is_stale_safe_and_does_not_change_active_pointer(tmp_path: Path):
    repo = repository(tmp_path / "repo"); active = repo / "research/active-source-generation.json"; active.write_text('{"generation_id":"prior"}\n')
    folder = tmp_path / "input"; folder.mkdir(); source = folder / "report.epf"; source.write_bytes(b"one")
    drafts = tmp_path / "drafts"; drafts.mkdir(); store = PreviewStore(repo, tmp_path / "previews", drafts); preview = store.scan_folder(folder, "workflow")
    source.write_bytes(b"two")
    with pytest.raises(RuntimeError, match="changed"):
        store.confirm(preview["preview_id"], [{"entry_id": preview["entries"][0]["entry_id"]}], workflow_fingerprint="workflow", expected_declaration_fingerprint=preview["declaration_fingerprint"], expected_draft_fingerprint=preview["draft_fingerprint"])
    assert active.read_text() == '{"generation_id":"prior"}\n' and 'semantic_key = "report"' not in (repo / "research/external-artifacts.toml").read_text()


def test_incomplete_staging_retries_idempotently(tmp_path: Path, monkeypatch):
    repo = repository(tmp_path / "repo"); folder = tmp_path / "input"; folder.mkdir(); (folder / "report.epf").write_bytes(b"payload")
    drafts = tmp_path / "drafts"; drafts.mkdir(); store = PreviewStore(repo, tmp_path / "previews", drafts); preview = store.scan_folder(folder, "workflow"); choice = [{"entry_id": preview["entries"][0]["entry_id"]}]
    from one_c_autoresearch import external_folder
    original_hash = external_folder._hash_stream; calls = 0
    def interrupted(stream, output=None):
        nonlocal calls
        calls += 1
        if calls == 3: raise OSError("interrupted")
        return original_hash(stream, output)
    monkeypatch.setattr(external_folder, "_hash_stream", interrupted)
    first = store.confirm(preview["preview_id"], choice, workflow_fingerprint="workflow", expected_declaration_fingerprint=preview["declaration_fingerprint"], expected_draft_fingerprint=preview["draft_fingerprint"])
    assert first["status"] == "staging_incomplete" and first["missing_external_ids"]
    monkeypatch.setattr(external_folder, "_hash_stream", original_hash)
    second = store.confirm(preview["preview_id"], choice, workflow_fingerprint="workflow", expected_declaration_fingerprint=preview["declaration_fingerprint"], expected_draft_fingerprint=preview["draft_fingerprint"])
    assert second["status"] == "complete" and not second["missing_external_ids"]


def test_browser_manifest_rejects_traversal_duplicates_quotas_and_space(tmp_path: Path, monkeypatch):
    repo = repository(tmp_path / "repo"); drafts = tmp_path / "drafts"; drafts.mkdir(); store = PreviewStore(repo, tmp_path / "previews", drafts)
    with pytest.raises(ValueError, match="unsafe"):
        store.create_browser(["root/../escape.epf"], [1], "workflow")
    with pytest.raises(ValueError, match="duplicate"):
        store.create_browser(["root/A.epf", "root/a.epf"], [1, 1], "workflow")
    monkeypatch.setattr("one_c_autoresearch.external_folder.MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="size"):
        store.create_browser(["root/a.epf"], [2], "workflow")
    monkeypatch.setattr("one_c_autoresearch.external_folder.MAX_FILE_BYTES", 2 * 1024**3)
    monkeypatch.setattr("one_c_autoresearch.external_folder.shutil.disk_usage", lambda _: type("Usage", (), {"free": 0})())
    with pytest.raises(OSError, match="1 GiB"):
        store.create_browser(["root/a.epf"], [1], "workflow")


def test_cli_scan_marks_hardlinks_invalid_and_cleanup_removes_expired(tmp_path: Path):
    repo = repository(tmp_path / "repo"); folder = tmp_path / "input"; folder.mkdir(); first = folder / "first.epf"; first.write_bytes(b"x"); os.link(first, folder / "second.epf")
    root = tmp_path / "previews"; store = PreviewStore(repo, root, tmp_path / "drafts"); value = store.scan_folder(folder, "workflow")
    assert {item["status"] for item in value["entries"]} == {"invalid"}
    value["expires_at"] = time.time() - 1; store.save(value)
    assert store.cleanup() == 1 and not (root / value["preview_id"]).exists()
