from __future__ import annotations

from pathlib import Path

_src_package = Path(__file__).resolve().parents[1] / "src" / "one_c_autoresearch"
if _src_package.exists():
    __path__.append(str(_src_package))

__version__ = "0.2.0"
