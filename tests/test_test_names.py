"""Every Python test in the repository runs, and each module the root run collects has a name of its own.

pytest imports test modules by basename, so two directories each holding a
test_surfaces.py stop the whole run at collection with an import mismatch.
The API's suite runs on its own, so its names may repeat these. A test file
outside every collected directory never runs at all, which is worse.
"""

from __future__ import annotations

import collections
import configparser
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _collected_directories() -> list[pathlib.Path]:
    config = configparser.ConfigParser()
    config.read(ROOT / "pytest.ini")
    return [ROOT / path for path in config["pytest"]["testpaths"].split()]


def test_no_two_collected_test_modules_share_a_name():
    places = collections.defaultdict(list)
    for directory in _collected_directories():
        for module in directory.rglob("test_*.py"):
            places[module.name].append(str(module.relative_to(ROOT)))
    shared = {name: paths for name, paths in places.items() if len(paths) > 1}
    assert not shared, f"rename one of each: {shared}"


API_TESTS = ROOT / "services" / "api" / "tests"


def test_every_python_test_file_is_collected_by_some_run():
    tracked = subprocess.run(
        ["git", "ls-files", "*test_*.py"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split()
    roots = [*_collected_directories(), API_TESTS]
    stray = [
        path for path in tracked
        if pathlib.Path(path).name.startswith("test_")
        and not any((ROOT / path).is_relative_to(root) for root in roots)
    ]
    assert not stray, f"add their directory to pytest.ini's testpaths: {stray}"
