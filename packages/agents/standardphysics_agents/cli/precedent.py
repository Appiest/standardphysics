"""The precedents commands: verify ADA directives and sign their case references."""

from __future__ import annotations

import sys
from pathlib import Path


def list_precedents(args) -> int:
    from ..precedents import load_precedent_ledger, load_precedents

    directives = load_precedents(allow_unverified=True)
    ledger = load_precedent_ledger()
    for d in directives:
        status = "verified" if ledger.is_verified(d.directive_id) else "unverified"
        cases = f"{len(d.verified_cases)}/{len(d.case_references)} cases"
        print(f"{d.directive_id:28} {status:<10} {cases:<9} {d.title}")
    return 0


def show_precedent(args) -> int:
    from ..precedents import load_precedents

    directives = load_precedents(allow_unverified=True)
    matched = [d for d in directives if d.directive_id == args.directive_id]
    if not matched:
        print(f"Unknown directive: {args.directive_id}", file=sys.stderr)
        return 1
    d = matched[0]
    print(f"{d.directive_id}: {d.title}\n")
    print(f"  Authority:         {', '.join(d.authority)}")
    print(f"  Typologies:        {', '.join(t.value for t in d.trigger.space_typologies)}")
    print(f"  Required objects:  {', '.join(d.trigger.required_entities)}")
    print(f"\n  Requirement:\n  {d.plain_english_warning}\n")
    print("  Inspection queries:")
    for q in d.inspection_queries:
        measured_by = q.rule_id or "not measured yet"
        print(f"    [{q.query_id}] {q.target_role}: {q.metric} {q.comparison} {q.threshold:g} ({q.citation}; {measured_by})")
    print(f"\n  Remedy pattern:    {d.constraints.solution_pattern}")
    print(f"  Fixed roles:       {', '.join(d.constraints.fixed_roles) or 'none'}")
    for case in d.case_references:
        signed = case.verified_by or "unverified"
        print(f"\n  Case ({signed}): {case.case_name}, {case.citation}\n    {case.holding}")
    return 0


def _precedent_directive(directive_id: str):
    from ..precedents import load_precedents

    return next((d for d in load_precedents(allow_unverified=True) if d.directive_id == directive_id), None)


def verify_precedent(args) -> int:
    from ..precedents import record_directive_review

    directive = _precedent_directive(args.directive_id)
    if directive is None:
        print(f"Unknown directive: {args.directive_id}", file=sys.stderr)
        return 1
    print(f"{directive.directive_id}: {directive.title}")
    print(f"  Read these sections first: {', '.join(directive.authority)}")
    for q in directive.inspection_queries:
        print(f"    {q.citation}: {q.target_role} {q.metric} {q.comparison} {q.threshold:g}")
    print(f"  {directive.plain_english_warning}\n")
    typed = input("  If every number above matches the sections, type the directive id to sign it: ").strip()
    if typed != directive.directive_id:
        print(f"  Not signed. {directive.directive_id} stays off.")
        return 1
    record_directive_review(directive.directive_id, args.by)
    print(f"  {directive.directive_id} is on.")
    return 0


def sign_precedent_case(args) -> int:
    from ..precedents import sign_case_reference

    directive = _precedent_directive(args.directive_id)
    cases = [] if directive is None else directive.case_references
    if not cases:
        print(f"No case references on {args.directive_id}", file=sys.stderr)
        return 1
    for number, case in enumerate(cases, start=1):
        print(f"  {number}. {case.case_name}, {case.citation} ({case.verified_by or 'unverified'})")
    choice = input("  Which case did you read? ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(cases):
        print("  No such case. Not signed.")
        return 1
    case = cases[int(choice) - 1]
    print(f"\n  Open {case.source_url}\n  The corpus says it held: {case.holding}\n")
    typed = input("  Type the reporter citation as it appears on the opinion: ").strip()
    if typed != case.citation:
        print("  That does not match the corpus. Not signed.")
        return 1
    sign_case_reference(args.directive_id, case.citation, args.by)
    print(f"  {case.case_name} will now appear in model prompts for {args.directive_id}.")
    return 0


def benchmark_precedents(args) -> int:
    import subprocess

    benchmark_script = Path(__file__).resolve().parents[4] / "scripts/benchmark_precedent_constraints.py"
    cmd = [sys.executable, str(benchmark_script)]
    return subprocess.run(cmd).returncode
