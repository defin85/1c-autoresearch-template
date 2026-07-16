from __future__ import annotations

import subprocess
from pathlib import Path


def run(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def test_build_publishable_clean_v8unpack_repo(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    run(["git", "init", "-q", "-b", "main"], source)
    (source / "a.txt").write_text("vendor\n", encoding="utf-8")
    run(["git", "add", "-A"], source)
    run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "vendor"], source)
    run(["git", "tag", "vendor-baseline"], source)
    (source / "a.txt").write_text("customer\n", encoding="utf-8")
    (source / "b.txt").write_text("new\n", encoding="utf-8")
    run(["git", "add", "-A"], source)
    run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "customer"], source)
    run(["git", "branch", "v8unpack-refinement"], source)

    repo = tmp_path / "repo"
    repo.mkdir()
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_publishable_clean_v8unpack_repo.py"
    run(
        [
            "python3",
            str(script),
            "--repo-path",
            str(repo),
            "--source-repo",
            str(source),
            "--output-repo",
            "out",
            "--summary",
            "summary.json",
        ],
        repo,
    )

    out = repo / "out"
    assert run(["git", "rev-list", "--count", "HEAD"], out) == "2"
    assert run(["git", "diff", "--name-only", "vendor-baseline..customer-clean-final"], out).splitlines() == [
        "a.txt",
        "b.txt",
    ]
