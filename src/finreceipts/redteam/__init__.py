"""Synthetic paired injection fixtures and an offline deterministic boundary harness."""

from .boundary import Boundary, Citation, validate_answer, validate_tool
from .fixtures import AttackPair, Document, make_pairs
from .runner import run_pair

__all__ = [
    "AttackPair",
    "Boundary",
    "Citation",
    "Document",
    "make_pairs",
    "run_pair",
    "validate_answer",
    "validate_tool",
]
