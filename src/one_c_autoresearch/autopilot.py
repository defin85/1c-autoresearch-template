from __future__ import annotations

import argparse
import html
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .common import repo_path


DIFF_INVENTORY_HEADER = "diff_id,source,change_type,path,object_kind,object_name,area,feature_id,classification,confidence,status,summary,evidence_ref,notes"
FEATURE_MAP_HEADER = "feature_id,title,domain,source_bucket,classification,confidence,status,owner,summary,evidence_pack_path,open_questions_path,outputs,notes"
OPEN_QUESTIONS_HEADER = "question_id,feature_id,status,reason,closure_method,impact,source_ref,owner,notes"
INFOBASE_QUESTIONS_HEADER = "question_id,subject_card_slug,feature_id,object_or_setting,check_target,reason,closing_result,risk_if_open,source_ref,status"
FINAL_DIFF_INVENTORY_HEADER = (
    DIFF_INVENTORY_HEADER
    + ",reverse_status,reverse_confidence,reverse_scenario_id,final_feature_id,"
    + "final_status,final_action,blocking_reason"
)
EXCEL_MAX_CELL_CHARS = 32767
XLSX_REQUIRED_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
    "xl/_rels/workbook.xml.rels",
    "xl/worksheets/sheet1.xml",
}
XLSX_EMPTY_TRAILING_CELL_LIMIT = 1000
XLSX_TRAILING_COLUMN_TOLERANCE = 100
INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _excel_column_number(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref)
    if not match:
        return 0
    number = 0
    for char in match.group(1):
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def _excel_column_name(column_index: int) -> str:
    column_name = ""
    while column_index:
        column_index, remainder = divmod(column_index - 1, 26)
        column_name = chr(65 + remainder) + column_name
    return column_name


def _write_text(path: Path, content: str, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def _sheet_xml(rows: list[list[str]]) -> tuple[str, str]:
    row_xml: list[str] = []
    shared_string_ids: dict[str, int] = {}
    shared_strings: list[str] = []
    total_string_count = 0
    for row_number, row in enumerate(rows, 1):
        cells: list[str] = []
        for column_index, value in enumerate(row, 1):
            column_name = _excel_column_name(column_index)
            text = excel_cell_text(value)
            if text not in shared_string_ids:
                shared_string_ids[text] = len(shared_strings)
                shared_strings.append(text)
            total_string_count += 1
            cells.append(f'<c r="{column_name}{row_number}" t="s"><v>{shared_string_ids[text]}</v></c>')
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    max_column_count = max((len(row) for row in rows), default=0)
    dimension = f'A1:{_excel_column_name(max_column_count)}{len(rows)}' if rows and max_column_count else "A1"
    shared_strings_xml = _shared_strings_xml(shared_strings, total_string_count)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    ), shared_strings_xml


def excel_cell_text(value: str) -> str:
    text = INVALID_XML_CHARS.sub("", str(value or ""))
    if len(text) <= EXCEL_MAX_CELL_CHARS:
        return text
    suffix = "... [truncated]"
    return text[: EXCEL_MAX_CELL_CHARS - len(suffix)] + suffix


def _shared_strings_xml(strings: list[str], total_count: int) -> str:
    items: list[str] = []
    for value in strings:
        space = ' xml:space="preserve"' if value[:1].isspace() or value[-1:].isspace() else ""
        items.append(f'<si><t{space}>{html.escape(value)}</t></si>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{total_count}" uniqueCount="{len(strings)}">'
        f'{"".join(items)}'
        "</sst>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def validate_minimal_xlsx(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            missing = sorted(XLSX_REQUIRED_PARTS - names)
            if missing:
                errors.append(f"missing workbook parts: {', '.join(missing)}")
                return errors
            for part in XLSX_REQUIRED_PARTS:
                try:
                    ET.fromstring(zf.read(part))
                except ET.ParseError as exc:
                    errors.append(f"{part} XML does not parse: {exc}")
            try:
                sheet_root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            except ET.ParseError:
                return errors
            namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            if "xl/sharedStrings.xml" in names:
                try:
                    shared_strings_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                    for index, text_node in enumerate(shared_strings_root.findall(".//main:t", namespace)):
                        text = text_node.text or ""
                        if len(text) > EXCEL_MAX_CELL_CHARS:
                            errors.append(f"shared string {index} exceeds Excel limit: {len(text)} > {EXCEL_MAX_CELL_CHARS}")
                except ET.ParseError as exc:
                    errors.append(f"xl/sharedStrings.xml XML does not parse: {exc}")
            cells = sheet_root.findall(".//main:c", namespace)
            valued_max_column = 0
            empty_trailing_cells = 0
            max_column = 0
            for cell in cells:
                cell_ref = cell.attrib.get("r", "<unknown>")
                column_number = _excel_column_number(cell_ref)
                max_column = max(max_column, column_number)
                text = "".join(node.text or "" for node in cell.findall(".//main:t", namespace))
                if len(text) > EXCEL_MAX_CELL_CHARS:
                    errors.append(f"cell {cell_ref} exceeds Excel limit: {len(text)} > {EXCEL_MAX_CELL_CHARS}")
                has_value = cell.find("main:v", namespace) is not None or cell.find("main:is", namespace) is not None
                if has_value:
                    valued_max_column = max(valued_max_column, column_number)
            if valued_max_column:
                for cell in cells:
                    cell_ref = cell.attrib.get("r", "")
                    column_number = _excel_column_number(cell_ref)
                    has_value = cell.find("main:v", namespace) is not None or cell.find("main:is", namespace) is not None
                    if not has_value and column_number > valued_max_column + XLSX_TRAILING_COLUMN_TOLERANCE:
                        empty_trailing_cells += 1
            if empty_trailing_cells > XLSX_EMPTY_TRAILING_CELL_LIMIT:
                errors.append(
                    "worksheet contains excessive empty trailing cells: "
                    f"{empty_trailing_cells} cells beyond valued column {valued_max_column}, max column {max_column}"
                )
    except Exception as exc:
        errors.append(f"not a readable XLSX archive: {exc}")
    return errors


def write_minimal_xlsx(path: Path, rows: list[list[str]], force: bool = False, sheet_name: str = "Sheet1") -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_xml, shared_strings_xml = _sheet_xml(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{html.escape(sheet_name, quote=True)}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr("xl/sharedStrings.xml", shared_strings_xml)
        zf.writestr("xl/styles.xml", _styles_xml())
        zf.writestr(
            "docProps/core.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:creator>one_c_autoresearch</dc:creator>"
            "<cp:lastModifiedBy>one_c_autoresearch</cp:lastModifiedBy>"
            "</cp:coreProperties>",
        )
        zf.writestr(
            "docProps/app.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            "<Application>one_c_autoresearch</Application>"
            "</Properties>",
        )
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
        "analysis/indexes/final-diff-inventory.csv": FINAL_DIFF_INVENTORY_HEADER + "\n",
        "analysis/indexes/final-feature-map.csv": FEATURE_MAP_HEADER + "\n",
        "outputs/open-questions.csv": OPEN_QUESTIONS_HEADER + "\n",
        "outputs/infobase-questions.csv": INFOBASE_QUESTIONS_HEADER + "\n",
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
