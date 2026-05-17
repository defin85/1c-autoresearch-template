from __future__ import annotations

import argparse
import html
import zipfile
from pathlib import Path

from .common import repo_path


DIFF_INVENTORY_HEADER = "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes"
FEATURE_MAP_HEADER = "feature_id,title,domain,source_bucket,classification,confidence,status,owner,summary,evidence_pack_path,open_questions_path,outputs,notes"
OPEN_QUESTIONS_HEADER = "question_id,feature_id,status,reason,closure_method,impact,source_ref,owner,notes"


def _write_text(path: Path, content: str, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def _sheet_xml(rows: list[list[str]]) -> str:
    row_xml: list[str] = []
    for row_number, row in enumerate(rows, 1):
        cells: list[str] = []
        for column_index, value in enumerate(row, 1):
            column_name = ""
            n = column_index
            while n:
                n, remainder = divmod(n - 1, 26)
                column_name = chr(65 + remainder) + column_name
            escaped = html.escape(value)
            cells.append(f'<c r="{column_name}{row_number}" t="inlineStr"><is><t>{escaped}</t></is></c>')
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    )


def write_minimal_xlsx(path: Path, rows: list[list[str]], force: bool = False) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", _sheet_xml(rows))
    return True


def enable_autopilot_gate(project_toml: Path) -> None:
    content = project_toml.read_text(encoding="utf-8")
    if "[autopilot]" not in content:
        content = content.rstrip() + '\n\n[autopilot]\nenabled = true\n'
    else:
        lines = content.splitlines()
        in_section = False
        changed = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "[autopilot]":
                in_section = True
                continue
            if in_section and stripped.startswith("[") and stripped.endswith("]"):
                break
            if in_section and stripped.startswith("enabled"):
                lines[index] = "enabled = true"
                changed = True
                break
        if not changed:
            for index, line in enumerate(lines):
                if line.strip() == "[autopilot]":
                    lines.insert(index + 1, "enabled = true")
                    changed = True
                    break
        content = "\n".join(lines) + "\n"
    project_toml.write_text(content, encoding="utf-8", newline="\n")


def scaffold_autopilot(args: argparse.Namespace) -> int:
    root = Path(args.repo_path).resolve() if args.repo_path else Path.cwd()
    created: list[str] = []
    skipped: list[str] = []

    files = {
        "analysis/indexes/diff-inventory.csv": DIFF_INVENTORY_HEADER + "\n",
        "analysis/indexes/feature-map.csv": FEATURE_MAP_HEADER + "\n",
        "outputs/open-questions.csv": OPEN_QUESTIONS_HEADER + "\n",
        "outputs/customization-map.md": (
            "# Customization Map\n\n"
            "Status: scaffolded\n\n"
            "This file is the final human-readable customization map. Replace the scaffold with the completed map before enabling final completion claims.\n"
        ),
        "analysis/final-audit.md": (
            "# Final Audit\n\n"
            "Status: scaffolded\n\n"
            "Coverage status: incomplete\n\n"
            "Unclassified diff entries: unknown\n\n"
            "This audit must be replaced with completed coverage evidence before final delivery.\n"
        ),
    }
    for relative, content in files.items():
        path = repo_path(root, relative)
        if _write_text(path, content, args.force):
            created.append(relative)
        else:
            skipped.append(relative)

    xlsx_files = {
        "outputs/customization-map.xlsx": [FEATURE_MAP_HEADER.split(",")],
        "outputs/open-questions.xlsx": [OPEN_QUESTIONS_HEADER.split(",")],
    }
    for relative, rows in xlsx_files.items():
        path = repo_path(root, relative)
        if write_minimal_xlsx(path, rows, force=args.force):
            created.append(relative)
        else:
            skipped.append(relative)

    if args.enable_gate:
        project = repo_path(root, "project.toml")
        if not project.exists():
            raise FileNotFoundError(f"Missing project.toml: {project}")
        enable_autopilot_gate(project)
        created.append("project.toml [autopilot].enabled=true")

    print(f"Autopilot scaffolded at: {root}")
    if created:
        print("Created or updated:")
        for item in created:
            print(f"- {item}")
    if skipped:
        print("Skipped existing files without --force:")
        for item in skipped:
            print(f"- {item}")
    if args.enable_gate:
        print("Final gate is enabled. Doctor will fail until the customization map is complete.")
    return 0
