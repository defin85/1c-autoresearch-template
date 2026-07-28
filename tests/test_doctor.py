import json
from pathlib import Path

from one_c_autoresearch.doctor import check, legacy_failures, packaged_secret_failure


REPO = Path(__file__).parents[1]


def test_doctor_finds_no_legacy_authority_after_removal():
    result = check(REPO)
    assert all(item["path"] for item in result["failures"])
    assert not any(item["code"].startswith("legacy.") for item in result["failures"])


def test_forbidden_manifest_covers_deleted_authority_surfaces():
    manifest = json.loads((REPO / "research/forbidden-authorities.json").read_text(encoding="utf-8"))
    exact = set(manifest["exact_paths"])
    assert ".agents/skills/1c-autoresearch-functional-gap-goal/SKILL.md" in exact
    assert "docs/method/1c-autoresearch-process.md" in exact
    assert "scripts/checks/test_research_repo.py" in exact
    assert "scripts/build_workspace_template.py" in exact
    assert "src/one_c_autoresearch/workspace_static/assets/index-DQRSqVwT.js" in exact
    assert "research/active-batch-generation.json" in exact


def test_forbidden_scan_reports_every_matching_authority(tmp_path):
    legacy_module = "que" + "ue.py"
    module_path = f"src/one_c_autoresearch/{legacy_module}"
    paths = ["old/exact.md", "old/prefix/item.json", module_path, "src/app.py"]
    for relative in paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('ROUTE = "/old-route"\n', encoding="utf-8")
    manifest = {
        "exact_paths": ["old/exact.md"],
        "path_prefixes": ["old/prefix/"],
        "modules": [legacy_module],
        "command_tokens": [],
        "route_tokens": ["/old-route"],
        "registration_tokens": [],
    }
    failures = legacy_failures(tmp_path, manifest, paths)
    assert {(item["code"], item["path"]) for item in failures} == {
        ("legacy.path", "old/exact.md"),
        ("legacy.path", "old/prefix/item.json"),
        ("legacy.module", module_path),
        ("legacy.command-token", module_path),
        ("legacy.command-token", "src/app.py"),
    }


def test_old_repository_gets_recreate_diagnostic(tmp_path):
    (tmp_path / "project.toml").write_text('[project]\nid="old"\n', encoding="utf-8")
    result = check(tmp_path)
    assert result["failures"] == [{"code": "contract.unsupported", "path": "research/workflow.toml", "message": "unsupported repository contract; recreate the repository instead of migrating it"}]


def test_packaged_credentials_are_detected_without_scanning_ordinary_source(tmp_path):
    key = tmp_path / "service.pem"; key.write_text("-----BEGIN PRIVATE KEY-----\nvalue\n", encoding="utf-8")
    credentials = tmp_path / "credentials.json"; credentials.write_text('{"token":"very-private-value"}', encoding="utf-8")
    source = tmp_path / "module.py"; source.write_text('password = "documented-placeholder"', encoding="utf-8")
    assert packaged_secret_failure("service.pem", key)
    assert packaged_secret_failure("credentials.json", credentials)
    assert not packaged_secret_failure("module.py", source)
