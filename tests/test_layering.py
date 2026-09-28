"""Dependencies point one way: contracts, then pipeline and agents, then the API.

The README promises that layering, and a single stray import quietly breaks it:
an agent that reaches up into the API can no longer be installed, tested or run
without the server. So this reads the import statements of every package module,
including the ones tucked inside functions, and fails on any that point upward.

It also checks that each package declares, in its pyproject, every sibling
package it imports, so an install from the pyproject alone never comes up short.
"""

import ast
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
API = ROOT / "services" / "api"
OWN_PACKAGE = re.compile(r"standardphysics_[a-z_]+")


def _source_files(project: pathlib.Path):
    for module_root in project.glob("standardphysics_*"):
        for path in module_root.rglob("*.py"):
            if "tests" not in path.parts and not path.name.startswith("test_"):
                yield path


def _imported_top_levels(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _own_imports(project: pathlib.Path) -> dict[str, set[pathlib.Path]]:
    """Each standardphysics module a project imports, and the files that import it."""
    found: dict[str, set[pathlib.Path]] = {}
    for path in _source_files(project):
        for name in _imported_top_levels(path):
            if OWN_PACKAGE.fullmatch(name):
                found.setdefault(name, set()).add(path.relative_to(ROOT))
    return found


def _own_module(project: pathlib.Path) -> str:
    return next(project.glob("standardphysics_*")).name


def _declared(project: pathlib.Path) -> set[str]:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = pyproject["project"].get("dependencies", [])
    return {re.split(r"[\s<>=!~\[;]", item, maxsplit=1)[0].replace("-", "_") for item in requirements}


def _projects() -> list[pathlib.Path]:
    return [*sorted(path for path in PACKAGES.iterdir() if (path / "pyproject.toml").exists()), API]


def test_the_layering_test_sees_every_package():
    assert {_own_module(project) for project in _projects()} >= {
        "standardphysics_contracts", "standardphysics_pipeline", "standardphysics_agents",
        "standardphysics_fixtures", "standardphysics_api",
    }


def test_no_package_imports_the_api():
    offenders = {
        str(path)
        for project in PACKAGES.iterdir()
        for path in _own_imports(project).get("standardphysics_api", set())
    }
    assert not offenders, f"packages must not import standardphysics_api: {sorted(offenders)}"


def test_contracts_import_no_other_standardphysics_package():
    imports = _own_imports(PACKAGES / "contracts")
    imports.pop("standardphysics_contracts", None)
    assert not imports, f"contracts must stand alone: {imports}"


def test_every_package_declares_the_siblings_it_imports():
    missing = {}
    for project in _projects():
        imported = set(_own_imports(project)) - {_own_module(project)}
        undeclared = imported - _declared(project)
        if undeclared:
            missing[str(project.relative_to(ROOT))] = sorted(undeclared)
    assert not missing, f"imported but not declared in pyproject dependencies: {missing}"
