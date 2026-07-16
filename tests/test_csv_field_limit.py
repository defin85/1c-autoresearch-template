import csv
import subprocess
import sys
from pathlib import Path

import one_c_autoresearch  # noqa: F401


def test_package_raises_csv_field_limit_for_large_evidence_fields() -> None:
    assert csv.field_size_limit() > 131_072


def test_csv_page_reads_large_fields(tmp_path: Path) -> None:
    path = tmp_path / "large.csv"
    path.write_text("id,payload\n1,small\n2," + ("x" * 200_000) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/csv_page.py",
            str(path),
            "--offset",
            "1",
            "--limit",
            "1",
            "--columns",
            "id,payload",
            "--max-cell",
            "10",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert '"id": "2"' in result.stdout
    assert "xxxxxxxxx…" in result.stdout
