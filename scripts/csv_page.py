#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


csv.field_size_limit(sys.maxsize)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print a paged preview of a large CSV file.")
    parser.add_argument("path")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--columns", default="", help="Comma-separated column list.")
    parser.add_argument("--max-cell", type=int, default=300)
    return parser.parse_args()


def trim(value: str, max_cell: int) -> str:
    if max_cell <= 0 or len(value) <= max_cell:
        return value
    return value[: max_cell - 1] + "…"


def main() -> int:
    args = parse_args()
    columns = [column.strip() for column in args.columns.split(",") if column.strip()]
    path = Path(args.path)
    shown = 0
    seen = 0

    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            return 0
        selected = columns or reader.fieldnames
        print(json.dumps({"path": str(path), "columns": selected, "offset": args.offset, "limit": args.limit}, ensure_ascii=False))
        for row in reader:
            if seen < args.offset:
                seen += 1
                continue
            if shown >= args.limit:
                break
            print(json.dumps({column: trim(row.get(column, ""), args.max_cell) for column in selected}, ensure_ascii=False))
            seen += 1
            shown += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
