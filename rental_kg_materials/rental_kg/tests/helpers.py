"""Shared paths and small fixtures for the unit tests."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

HAS_RDF = all(
    importlib.util.find_spec(name) is not None
    for name in ("rdflib", "pyshacl", "owlrl", "geographiclib")
)


def ecoton_row(**changes):
    """Copy of the real Ropshinskaya card with optional test changes."""
    from sources import read_rows, slice_path

    row = next(r for r in read_rows(slice_path("ecoton")) if r["id"] == "E129891108")
    return {**row, **changes}
