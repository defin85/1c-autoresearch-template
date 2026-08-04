"""Portable tooling for 1C autoresearch repositories."""

from __future__ import annotations

import csv
import sys


field_size_limit = sys.maxsize
while True:
    try:
        _ = csv.field_size_limit(field_size_limit)
        break
    except OverflowError:
        field_size_limit //= 10

__version__ = "0.3.0"
