"""Repository-local loader for the src-layout package."""

from pathlib import Path

__path__.append(str(Path(__file__).parents[1] / "src/one_c_autoresearch"))
__version__ = "0.1.0"
