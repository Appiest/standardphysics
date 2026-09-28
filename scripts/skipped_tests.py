"""Summarises the tests a pytest run skipped, from its JUnit XML.

    python scripts/skipped_tests.py "Root suite" reports/junit-root.xml

A skip is a pass on the job's badge, so a suite can lose most of its checks
without anything going red. This prints a Markdown section for the step
summary with the count and each skip's reason, grouped so that forty tests
waiting on the same missing Blender read as one line.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def skip_reasons(report: Path) -> Counter[str]:
    root = ET.parse(report).getroot()
    reasons = (skipped.get("message") or "no reason given" for skipped in root.iter("skipped"))
    return Counter(reason.strip() for reason in reasons)


def tests_recorded(report: Path) -> int:
    return sum(1 for _ in ET.parse(report).getroot().iter("testcase"))


def table_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def as_markdown(title: str, total: int, reasons: Counter[str]) -> str:
    skipped = sum(reasons.values())
    lines = [f"### {title}", "", f"{skipped} of {total} tests skipped."]
    if not reasons:
        return "\n".join(lines)
    rows = [f"| {count} | {table_cell(reason)} |" for reason, count in reasons.most_common()]
    return "\n".join([*lines, "", "| Tests | Reason |", "| ---: | --- |", *rows])


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: skipped_tests.py TITLE junit.xml", file=sys.stderr)
        return 2
    title, report = argv[0], Path(argv[1])
    print(as_markdown(title, tests_recorded(report), skip_reasons(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
