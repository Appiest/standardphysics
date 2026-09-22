"""Command line entry point for the independent shop-pilot evaluators.

Usage:
  python -m scripts.shop_pilot.cli freeze  --root . [--collect] --out freeze.json
  python -m scripts.shop_pilot.cli verify  --receipt R.json --artifacts-dir DIR
  python -m scripts.shop_pilot.cli evaluate --receipts R1.json R2.json --artifacts-dir DIR
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from .certificates import evaluate
from .freeze import build_freeze
from .receipt_verifier import verify_receipt_file


def _write_json(payload: Any, out: pathlib.Path | None) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True)
    if out is None:
        print(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")


def _load_receipts(paths: list[pathlib.Path]) -> list[dict[str, Any]]:
    receipts = []
    for path in paths:
        receipts.append(json.loads(path.read_text(encoding="utf-8")))
    return receipts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shop_pilot")
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze")
    freeze.add_argument("--root", type=pathlib.Path, default=pathlib.Path("."))
    freeze.add_argument("--collect", action="store_true")
    freeze.add_argument("--out", type=pathlib.Path)

    verify = sub.add_parser("verify")
    verify.add_argument("--receipt", type=pathlib.Path, required=True)
    verify.add_argument("--artifacts-dir", type=pathlib.Path)
    verify.add_argument("--policy", type=pathlib.Path)
    verify.add_argument("--contract", type=pathlib.Path)
    verify.add_argument("--git-worktree", type=pathlib.Path)
    verify.add_argument("--out", type=pathlib.Path)

    ev = sub.add_parser("evaluate")
    ev.add_argument("--receipts", type=pathlib.Path, nargs="+", required=True)
    ev.add_argument("--artifacts-dir", type=pathlib.Path, required=True)
    ev.add_argument("--policy", type=pathlib.Path)
    ev.add_argument("--contract", type=pathlib.Path)
    ev.add_argument("--git-worktree", type=pathlib.Path)
    ev.add_argument("--out", type=pathlib.Path)

    args = parser.parse_args(argv)
    if args.command == "freeze":
        _write_json(build_freeze(args.root.resolve(), collect=args.collect), args.out)
        return 0
    if args.command == "verify":
        result = verify_receipt_file(
            args.receipt,
            artifacts_dir=(args.artifacts_dir or args.receipt.parent).resolve(),
            policy_path=args.policy,
            contract_path=args.contract,
            git_worktree=args.git_worktree,
        )
        _write_json(result, args.out)
        return 0 if result["status"] == "valid" else 1
    if args.command == "evaluate":
        result = evaluate(
            _load_receipts(args.receipts),
            artifacts_dir=args.artifacts_dir.resolve(),
            policy_path=args.policy,
            contract_path=args.contract,
            git_worktree=args.git_worktree,
        )
        _write_json(result, args.out)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
