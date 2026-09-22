"""Independent shop-pilot acceptance evaluation owned by lane Q.

This package verifies product gates from raw artifacts. It never trusts a
receipt's status text, boolean or self-reported hash: it opens the referenced
bytes, recomputes digests, re-evaluates assertions and cross-checks identities.

The G12 semantic benchmark evaluator also lives here rather than in the
redesign gate at packages/agents/.../evaluation/gate.py, which lane R owns.
"""
