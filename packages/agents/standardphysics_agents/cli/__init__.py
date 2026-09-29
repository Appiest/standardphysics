"""The command line a person uses to enable a check.

Verification is the one thing in this lane an agent must not do alone, so it
gets a real interface rather than a JSON file to hand-edit. `verify` prints the
sentence from the standard and then asks for the number back. Typing it is the
confirmation: it cannot be satisfied by someone who did not read the section.
"""

from .dispatch import main
from .parser import build_parser

__all__ = ["build_parser", "main"]
