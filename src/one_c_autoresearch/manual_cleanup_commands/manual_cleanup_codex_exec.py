#!/usr/bin/env python3
"""Run Codex Exec with structured output for one manual-cleanup pass."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


OUT_DIR = Path("analysis/detailed-register-reverse-review")
TRACE_DIR = Path("analysis/cache/manual-markup-traces")
REPO_SKILL_DIR = Path(".agents/skills/1c-autoresearch-queue-worker")
CODEX_SKILL_ALIAS = Path(".codex/skills/1c-autoresearch-queue-worker")
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["row_id", "decision", "review_basis", "notes", "source_paths", "evidence_refs"],
                "properties": {
                    "row_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["remove_noise", "keep_customization", "manual_review"]},
                    "review_basis": {"type": "string", "enum": ["probe_only", "source_context", "free_read"]},
                    "notes": {"type": "string"},
                    "source_paths": {"type": "array", "items": {"type": "string"}},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


def run_capture(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def ensure_codex_skill_alias() -> None:
    """Expose repo-local queue-worker skill at the path Codex Exec may probe."""

    source = REPO_SKILL_DIR
    alias = CODEX_SKILL_ALIAS
    if alias.exists() or alias.is_symlink():
        return
    if not source.exists():
        return
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(Path("..") / ".." / source)


def probe_data(row_id: str) -> dict:
    return json.loads(run_capture(["python3", "-m", "one_c_autoresearch", "manual-cleanup", "probe-row", "--row-id", row_id]))


def cascade_needs_source_context(probe: dict) -> bool:
    return (
        probe.get("suggested_decision") in {"keep_customization", "manual_review"}
        or bool(probe.get("context_diff_paths"))
        or probe.get("strategy") != "template_mxl"
    )


def reject_cascade_manual_review(decisions_file: Path) -> None:
    data = json.loads(decisions_file.read_text(encoding="utf-8"))
    manual = [
        item.get("row_id", "")
        for item in data.get("decisions", [])
        if item.get("decision") == "manual_review"
    ]
    if manual:
        raise SystemExit(f"cascade-review must resolve rows, not return manual_review: {', '.join(manual)}")


def ensure_no_probe_only_for_context_rows(decisions_file: Path, probes: dict[str, dict]) -> None:
    data = json.loads(decisions_file.read_text(encoding="utf-8"))
    bad = []
    for item in data.get("decisions", []):
        probe = probes.get(item.get("row_id", ""), {})
        if cascade_needs_source_context(probe) and item.get("review_basis") == "probe_only":
            bad.append(item.get("row_id", ""))
    if bad:
        raise SystemExit(f"cascade-review rows require source_context/free_read basis: {', '.join(bad)}")


def source_context_data(row_id: str) -> dict:
    return json.loads(run_capture(["python3", "-m", "one_c_autoresearch", "manual-cleanup", "source-context", "--row-id", row_id]))


def ensure_free_read_for_binary_or_truncated(decisions_file: Path, probes: dict[str, dict], source_contexts: dict[str, dict]) -> None:
    data = json.loads(decisions_file.read_text(encoding="utf-8"))
    bad = []
    for item in data.get("decisions", []):
        row_id = item.get("row_id", "")
        probe = probes.get(row_id, {})
        source_context = source_contexts.get(row_id, {})
        if (probe.get("strategy") == "binary_payload" or source_context.get("normalized_diff_truncated")) and item.get("review_basis") != "free_read":
            bad.append(row_id)
    if bad:
        raise SystemExit(f"cascade-review binary/truncated rows require free_read basis: {', '.join(bad)}")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_readonly_diff_read(command: str) -> bool:
    return (
        (
            "git -C analysis/cache/noise/clean-rebase-v8unpack/repo" in command
            or "git -C analysis/cache/clean-rebase/repo" in command
            or "analysis/cache/noise/clean-rebase-v8unpack/repo/" in command
            or "analysis/cache/clean-rebase/repo/" in command
        )
        and (" diff" in command or " show" in command or "sed -n" in command)
    )


def ensure_row_free_read_evidence(row_ids: list[str], source_contexts: dict[str, dict], commands: list[str]) -> None:
    missing = []
    readonly_commands = [command for command in commands if is_readonly_diff_read(command)]
    for row_id in row_ids:
        source_context = source_contexts.get(row_id, {})
        paths = set(source_context.get("paths", []))
        paths.update(source_context.get("related_source_paths", []))
        if not any(path and path in command for path in paths for command in readonly_commands):
            missing.append(row_id)
    if missing:
        raise SystemExit(f"cascade-review rows require row-specific read-only diff/source evidence: {', '.join(missing)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--model", default=os.environ.get("CODEX_MANUAL_MODEL", ""))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--pass-id", required=True)
    parser.add_argument("--row-id-prefix", default="")
    parser.add_argument("--candidate-file", default="")
    parser.add_argument("--suggested-decision", choices=["manual_review", "keep_customization"], default="")
    parser.add_argument("--mode", choices=["probe-only", "source-review", "free-read-review", "cascade-review"], default="probe-only")
    parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def validate_decision_row_ids(decisions_file: Path, expected_rows: list[dict]) -> None:
    data = json.loads(decisions_file.read_text(encoding="utf-8"))
    actual = [item.get("row_id", "") for item in data.get("decisions", [])]
    expected = [row["row_id"] for row in expected_rows]
    duplicate = sorted({row_id for row_id in actual if actual.count(row_id) > 1})
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if duplicate or missing or extra:
        raise SystemExit(
            "decisions row_id mismatch: "
            f"duplicate={duplicate}, missing={missing}, extra={extra}"
        )


def ensure_pass_outputs_absent(paths: list[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise SystemExit(f"pass output files already exist; choose a unique --pass-id: {existing}")


def write_new_file(path: Path, text: str) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)
    except FileExistsError:
        raise SystemExit(f"pass output file already exists; choose a unique --pass-id: {path}") from None


def main() -> int:
    args = parse_args()
    if not args.model:
        raise SystemExit("model is required: pass --model or set CODEX_MANUAL_MODEL")
    if args.apply:
        raise SystemExit("--apply is disabled; run manual-cleanup apply-ready as the single writer")
    pass_id = args.pass_id
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    schema_file = OUT_DIR / f"{pass_id}.schema.json"
    decisions_file = OUT_DIR / f"{pass_id}-decisions.json"
    trace_file = TRACE_DIR / f"trace-{pass_id}.jsonl"
    stderr_file = TRACE_DIR / f"trace-{pass_id}.stderr.txt"
    trace_summary_json = TRACE_DIR / f"trace-{pass_id}.summary.json"
    trace_summary_md = TRACE_DIR / f"trace-{pass_id}.summary.md"
    ensure_pass_outputs_absent([
        schema_file,
        decisions_file,
        trace_file,
        stderr_file,
        trace_summary_json,
        trace_summary_md,
        OUT_DIR / f"{pass_id}.csv",
        OUT_DIR / f"summary-{pass_id}.md",
    ])
    ensure_codex_skill_alias()
    rows_json = run_capture(
        [
            "python3",
            "-m", "one_c_autoresearch", "manual-cleanup", "next-rows",
            "--task-id",
            args.task_id,
            "--limit",
            str(args.limit),
            *(["--row-id-prefix", args.row_id_prefix] if args.row_id_prefix else []),
            *(["--candidate-file", args.candidate_file] if args.candidate_file else []),
            *(["--suggested-decision", args.suggested_decision] if args.suggested_decision else []),
        ]
    )
    rows = json.loads(rows_json)["rows"]
    if not rows:
        raise SystemExit("no pending rows for requested pass")
    write_new_file(schema_file, json.dumps(SCHEMA, ensure_ascii=False, indent=2))

    if args.mode == "source-review":
        review_rule = (
            "- После probe для каждой строки вызови:\n"
            "  python3 -m one_c_autoresearch manual-cleanup source-context --row-id <ROW_ID>\n"
            "- Финальное решение должно опираться на source_context.diff, normalized_diff и related_source_paths.\n"
            "- Если source_context.has_normalized_diff=true, в notes обязательно опиши, что именно меняет normalized_diff.\n"
            "- Если source_context.normalized_diff_truncated=true, ставь manual_review; дочитывание разрешено только в free-read-review/cascade-review.\n"
            "- Если после чтения source_context нет честного доказательства шума или смысла, оставь manual_review.\n"
            "- Не выполняй git/rg/sed и не читай файлы напрямую; этот режим проверяет только probe_row + source_context.\n"
        )
    elif args.mode == "free-read-review":
        review_rule = (
            "- После probe можешь сам читать diff глазами read-only командами:\n"
            "  git -C analysis/cache/noise/clean-rebase-v8unpack/repo diff/show/ls-tree ...\n"
            "  git -C analysis/cache/clean-rebase/repo diff/show/ls-tree ...\n"
            "  rg/sed только по paths, clean_candidate_paths, semantic_candidate_paths, context_candidate_paths и related owner paths.\n"
            "- Не читай CSV/JSONL руками и не выходи за заданные строки.\n"
            "- Если читаешь normalized diff, в notes обязательно опиши, что именно он меняет.\n"
            "- Финальное решение должно ссылаться на конкретные diff/source paths.\n"
        )
    elif args.mode == "cascade-review":
        review_rule = (
            "- Для каждой строки сначала вызови probe_row.\n"
            "- Если probe_row.suggested_decision == remove_noise, probe_row.context_diff_paths пустой и strategy == template_mxl, можно принять remove_noise без дополнительного чтения.\n"
            "- Если probe_row.suggested_decision != remove_noise, probe_row.context_diff_paths не пустой или strategy != template_mxl, в том же сеансе обязательно вызови:\n"
            "  python3 -m one_c_autoresearch manual-cleanup source-context --row-id <ROW_ID>\n"
            "- Если source_context.has_normalized_diff=true, в notes обязательно опиши, что именно меняет normalized_diff.\n"
            "- Если source_context.normalized_diff_truncated=true или strategy == binary_payload, обязательно дочитай нужные diff/source paths read-only командами и выбери remove_noise или keep_customization.\n"
            "- После source_context можешь сам читать diff глазами read-only командами:\n"
            "  git -C analysis/cache/noise/clean-rebase-v8unpack/repo diff/show/ls-tree ...\n"
            "  git -C analysis/cache/clean-rebase/repo diff/show/ls-tree ...\n"
            "  rg/sed только по paths, clean_candidate_paths, semantic_candidate_paths, context_candidate_paths и related owner paths.\n"
            "- Пути вида Report/.../Template/... из source_context.related_source_paths лежат под analysis/cache/noise/clean-rebase-v8unpack/repo/; не читай их из корня репозитория и не подменяй Report на Reports.\n"
            "- Не используй rg --files и сложные регулярные выражения для поиска путей; нужные пути уже есть в probe_row/source_context.\n"
            "- Не читай CSV/JSONL руками и не выходи за заданные строки.\n"
            "- В cascade-review нельзя возвращать manual_review: проход должен классифицировать diff как remove_noise или keep_customization.\n"
            "- Финальное решение должно ссылаться на конкретные diff/source paths.\n"
        )
    else:
        review_rule = "- Не вызывай source_context и не читай дополнительные файлы; решай только по probe_row.\n"
    if args.mode == "cascade-review":
        decision_rule = (
            "- Решай по JSON из probe_row/source_context:\n"
            "  remove_noise, если конкретный diff доказан как технический шум или нормализованно равен типовой версии;\n"
            "  keep_customization, если конкретный diff содержит смысловое изменение.\n"
            "- В cascade-review не возвращай manual_review. Если данных не хватает, дочитай разрешенные diff/source paths read-only и выбери remove_noise или keep_customization.\n"
        )
    else:
        decision_rule = (
            "- Решай по JSON из probe_row:\n"
            "  remove_noise, только если strategy поддержана, есть nested diff и нет semantic_diff_paths/context_diff_paths;\n"
            "  keep_customization, если есть semantic_diff_paths;\n"
            "  manual_review, если strategy=unknown/unsupported или есть только context_diff_paths без semantic_diff_paths.\n"
        )
    prompt = f"""\
Рабочая папка уже выбрана. Это малый пробный проход ручной очистки diff.

Задача: {args.task_id}
Pass ID: {pass_id}
Mode: {args.mode}
Строки к обработке JSON:
{json.dumps(rows, ensure_ascii=False, indent=2)}

Правила:
- Не редактируй файлы и не создавай коммиты.
- Для каждой строки обязательно вызови:
  python3 -m one_c_autoresearch manual-cleanup probe-row --row-id <ROW_ID>
- В source-review режиме для чтения исходников используй только разрешенный враппер:
  python3 -m one_c_autoresearch manual-cleanup source-context --row-id <ROW_ID>
- Не читай CSV/JSONL руками.
{review_rule}
{decision_rule}
- Для remove_noise notes обязаны явно содержать, почему это технический шум.
- Используемость объекта сама по себе не доказывает смысловой diff.
- review_basis ставь так: probe_only если хватило probe-row; source_context если хватило source-context; free_read если дополнительно смотрел git/rg/sed.
- Финальный ответ верни строго по JSON Schema.
"""
    cmd = [
        "codex",
        "exec",
        "-m",
        args.model,
        "-C",
        str(Path.cwd()),
        "-s",
        "read-only",
        "--json",
        "--output-schema",
        str(schema_file),
        "-o",
        str(decisions_file),
        prompt,
    ]
    with trace_file.open("w", encoding="utf-8") as trace:
        result = subprocess.run(cmd, text=True, stdout=trace, stderr=subprocess.PIPE, check=False)
    stderr_file.write_text(result.stderr or "", encoding="utf-8")
    if result.returncode:
        raise SystemExit(f"codex exec failed with {result.returncode}: see {stderr_file}")
    json.loads(decisions_file.read_text(encoding="utf-8"))
    validate_decision_row_ids(decisions_file, rows)
    if args.mode == "cascade-review":
        reject_cascade_manual_review(decisions_file)
    summary_args = [
        "python3",
        "-m", "one_c_autoresearch", "manual-cleanup", "trace-summary",
        "--trace",
        str(trace_file),
        "--summary-json",
        str(trace_summary_json),
        "--summary-md",
        str(trace_summary_md),
        "--fail-on-missing-probe",
        "--fail-on-direct-read",
    ]
    for row in rows:
        summary_args.extend(["--expected-row-id", row["row_id"]])
    if args.mode == "source-review":
        for row in rows:
            summary_args.extend(["--expected-source-context-row-id", row["row_id"]])
        summary_args.append("--fail-on-missing-source-context")
    elif args.mode == "cascade-review":
        probes = {row["row_id"]: probe_data(row["row_id"]) for row in rows}
        expected_source_context_rows = []
        for row in rows:
            if cascade_needs_source_context(probes[row["row_id"]]):
                expected_source_context_rows.append(row["row_id"])
                summary_args.extend(["--expected-source-context-row-id", row["row_id"]])
        if expected_source_context_rows:
            summary_args.append("--fail-on-missing-source-context")
        ensure_no_probe_only_for_context_rows(decisions_file, probes)
        source_contexts = {
            row_id: source_context_data(row_id)
            for row_id in expected_source_context_rows
        }
        ensure_free_read_for_binary_or_truncated(decisions_file, probes, source_contexts)
    trace_summary = json.loads(run_capture(summary_args))
    if trace_summary["errors"]:
        raise SystemExit(f"trace contains errors: {trace_summary['errors']}")
    if args.mode == "cascade-review":
        free_read_rows = [
            row["row_id"]
            for row in rows
            if probes[row["row_id"]].get("strategy") == "binary_payload"
            or source_contexts.get(row["row_id"], {}).get("normalized_diff_truncated")
        ]
        ensure_row_free_read_evidence(free_read_rows, source_contexts, trace_summary["commands"])
    trace_summary["decisions_sha256"] = file_sha256(decisions_file)
    trace_summary_json.write_text(json.dumps(trace_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output = {
        "pass_id": pass_id,
        "schema_file": str(schema_file),
        "decisions_file": str(decisions_file),
        "trace_file": str(trace_file),
        "stderr_file": str(stderr_file),
        "trace_summary_json": str(trace_summary_json),
        "trace_summary_md": str(trace_summary_md),
        "trace_probe_calls": trace_summary["probe_calls"],
        "trace_source_context_calls": trace_summary["source_context_calls"],
        "trace_errors": len(trace_summary["errors"]),
        "rows": len(rows),
        "applied": False,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
