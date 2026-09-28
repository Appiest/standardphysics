"""Fails CI when a module the service leans on loses test coverage.

    python scripts/coverage_floors.py coverage.xml

There is no whole-repo threshold. Most of the repository is research code whose
coverage says little about whether the service holds up, and one number over all
of it would move for reasons nobody reviewing a change could see. The modules
below guard accounts, money, uploads and the job queue, and each was already
well tested when its floor was set: each floor is what the API suite measured
then, rounded down to a multiple of five. usdz_validation measured 95.5% but
is small enough that one more missed branch costs two points, so its floor
sits at 90, and budgets measured 100% and keeps five points of room. A floor
stops a regression; raising one is an ordinary change.

Percentages count branches as well as lines, the way `coverage report` does:
covered lines plus taken branches over all lines plus all branches. A module
missing from the report fails too, because a rename would otherwise drop its
floor without anyone noticing.

Prints a Markdown table, which CI appends to the job's step summary.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

API = "services/api/standardphysics_api"
FLOORS: dict[str, int] = {
    f"{API}/auth.py": 95,
    f"{API}/accounts.py": 95,
    f"{API}/attempt_limiter.py": 90,
    f"{API}/budgets.py": 95,
    f"{API}/store.py": 90,
    f"{API}/worker.py": 90,
    f"{API}/repository.py": 95,
    f"{API}/request_size.py": 95,
    f"{API}/usdz_validation.py": 90,
}

BRANCH_COUNTS = re.compile(r"\((\d+)/(\d+)\)")


@dataclass(frozen=True)
class ModuleCoverage:
    covered: int
    measured: int

    @property
    def percent(self) -> float:
        return 100.0 if self.measured == 0 else 100.0 * self.covered / self.measured


@dataclass(frozen=True)
class FloorResult:
    path: str
    floor: int
    coverage: ModuleCoverage | None

    @property
    def passed(self) -> bool:
        return self.coverage is not None and self.coverage.percent >= self.floor


def branch_counts(line: ET.Element) -> tuple[int, int]:
    if line.get("branch") != "true":
        return 0, 0
    match = BRANCH_COUNTS.search(line.get("condition-coverage", ""))
    return (int(match[1]), int(match[2])) if match else (0, 0)


def module_coverage(module: ET.Element) -> ModuleCoverage:
    covered = measured = 0
    for line in module.iter("line"):
        taken, branches = branch_counts(line)
        covered += (int(line.get("hits", "0")) > 0) + taken
        measured += 1 + branches
    return ModuleCoverage(covered, measured)


def read_report(report: Path) -> dict[str, ModuleCoverage]:
    root = ET.parse(report).getroot()
    return {module.get("filename", ""): module_coverage(module) for module in root.iter("class")}


def check_floors(report: dict[str, ModuleCoverage], floors: dict[str, int]) -> list[FloorResult]:
    return [FloorResult(path, floor, report.get(path)) for path, floor in floors.items()]


def describe(result: FloorResult) -> str:
    measured = "not in the report" if result.coverage is None else f"{result.coverage.percent:.1f}%"
    verdict = "ok" if result.passed else "below its floor"
    return f"| `{result.path}` | {measured} | {result.floor}% | {verdict} |"


def as_markdown(results: list[FloorResult]) -> str:
    header = ["### Coverage floors", "", "| Module | Covered | Floor | |", "| --- | ---: | ---: | --- |"]
    return "\n".join([*header, *map(describe, results)])


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: coverage_floors.py coverage.xml", file=sys.stderr)
        return 2
    results = check_floors(read_report(Path(argv[0])), FLOORS)
    print(as_markdown(results))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
