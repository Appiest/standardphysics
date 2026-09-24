"""Actionable ADA case precedent corpus and constraint evaluation."""

from .checker import check_precedent_constraints, precedent_rejection_for
from .compiler import PrecedentCompiler
from .verification import (
    PrecedentLedger,
    PrecedentVerification,
    load_precedent_ledger,
    load_precedents,
)

__all__ = [
    "PrecedentCompiler",
    "PrecedentLedger",
    "PrecedentVerification",
    "check_precedent_constraints",
    "load_precedent_ledger",
    "load_precedents",
    "precedent_rejection_for",
]
