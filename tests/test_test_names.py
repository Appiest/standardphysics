"""Every test module the root pytest run collects has a name of its own.

pytest imports test modules by basename, so two directories each holding a
test_surfaces.py stop the whole run at collection with an import mismatch.
The API's suite runs on its own, so its names may repeat these.
"""

from __future__ import annotations

import collections
import configparser
import pathlib

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
