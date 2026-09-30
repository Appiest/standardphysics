"""Dependencies point one way: contracts, then fixtures and pipeline, then agents, then the API.

The README promises that layering, and a single stray import quietly breaks it:
an agent that reaches up into the API can no longer be installed, tested or run
without the server. So this reads the import statements of every package module,
including the ones tucked inside functions, and checks each import between
packages against ALLOWED_DEPENDENCIES, the whole graph written down in one place.
An import that table does not list fails, and so does any cycle, with the
importing module and the file that holds the import named in the message.

It also checks that each package declares, in its pyproject, every sibling
package it imports, so an install from the pyproject alone never comes up short.
"""

import ast
import pathlib
import re
import tomllib
from collections.abc import Iterable, Mapping

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OWN_PACKAGE = re.compile(r"standardphysics_[a-z_]+")

CONTRACTS = "standardphysics_contracts"
FIXTURES = "standardphysics_fixtures"
PIPELINE = "standardphysics_pipeline"
AGENTS = "standardphysics_agents"
API = "standardphysics_api"

ALLOWED_DEPENDENCIES: dict[str, frozenset[str]] = {
    CONTRACTS: frozenset(),
    FIXTURES: frozenset({CONTRACTS}),
    PIPELINE: frozenset({CONTRACTS}),
    AGENTS: frozenset({CONTRACTS, FIXTURES, PIPELINE}),
    API: frozenset({CONTRACTS, FIXTURES, PIPELINE, AGENTS}),
}

ImportGraph = dict[str, dict[str, set[pathlib.Path]]]


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


def _own_imports(project: pathlib.Path, root: pathlib.Path = ROOT) -> dict[str, set[pathlib.Path]]:
    """Each standardphysics module a project imports, and the files that import it."""
    found: dict[str, set[pathlib.Path]] = {}
    for path in _source_files(project):
        for name in _imported_top_levels(path):
            if OWN_PACKAGE.fullmatch(name):
                found.setdefault(name, set()).add(path.relative_to(root))
    return found


def _own_module(project: pathlib.Path) -> str:
    return next(project.glob("standardphysics_*")).name


def _declared(project: pathlib.Path) -> set[str]:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = pyproject["project"].get("dependencies", [])
    return {re.split(r"[\s<>=!~\[;]", item, maxsplit=1)[0].replace("-", "_") for item in requirements}


def _projects(root: pathlib.Path = ROOT) -> list[pathlib.Path]:
    packages = root / "packages"
    return [*sorted(path for path in packages.iterdir() if (path / "pyproject.toml").exists()), root / "services" / "api"]


def import_graph(root: pathlib.Path = ROOT) -> ImportGraph:
    """Which sibling packages each package imports, with the files that do it."""
    graph: ImportGraph = {}
    for project in _projects(root):
        module = _own_module(project)
        imports = _own_imports(project, root)
        imports.pop(module, None)
        graph[module] = imports
    return graph


FILES_NAMED_PER_IMPORT = 3


def _describe(importer: str, imported: str, files: Iterable[pathlib.Path]) -> str:
    names = sorted(map(str, files))
    shown = ", ".join(names[:FILES_NAMED_PER_IMPORT])
    hidden = len(names) - FILES_NAMED_PER_IMPORT
    more = f" and {hidden} more" if hidden > 0 else ""
    return f"{importer} imports {imported} in {shown}{more}"


def forbidden_imports(graph: ImportGraph, allowed: Mapping[str, frozenset[str]]) -> list[str]:
    """Every import between packages that the allowed table does not list."""
    return [
        _describe(importer, imported, files)
        for importer, imports in sorted(graph.items())
        for imported, files in sorted(imports.items())
        if imported not in allowed.get(importer, frozenset())
    ]


def _starting_at_smallest(cycle: list[str]) -> tuple[str, ...]:
    start = cycle.index(min(cycle))
    return tuple(cycle[start:] + cycle[:start])


def find_cycles(edges: Mapping[str, Iterable[str]]) -> list[tuple[str, ...]]:
    """Every cycle, each listed once and starting from its alphabetically first package."""
    cycles: set[tuple[str, ...]] = set()

    def walk(path: list[str]) -> None:
        for target in sorted(edges.get(path[-1], ())):
            if target in path:
                cycles.add(_starting_at_smallest(path[path.index(target):]))
            else:
                walk([*path, target])

    for start in sorted(edges):
        walk([start])
    return sorted(cycles)


def describe_cycle(cycle: tuple[str, ...], graph: ImportGraph) -> str:
    steps = zip(cycle, [*cycle[1:], cycle[0]], strict=True)
    return "; ".join(_describe(importer, imported, graph[importer][imported]) for importer, imported in steps)


def import_cycles(graph: ImportGraph) -> list[str]:
    return [describe_cycle(cycle, graph) for cycle in find_cycles(graph)]


def _write_package(root: pathlib.Path, project: str, module: str, imports: Iterable[str]) -> None:
    package = root / project / module
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("".join(f"import {name}\n" for name in imports), encoding="utf-8")
    (root / project / "pyproject.toml").write_text('[project]\nname = "fake"\n', encoding="utf-8")


def _fake_repo(root: pathlib.Path, imports: Mapping[str, Iterable[str]]) -> pathlib.Path:
    """A tree shaped like this repo, where each package's __init__ imports what it is given."""
    for module in (CONTRACTS, FIXTURES, PIPELINE, AGENTS):
        _write_package(root, f"packages/{module.removeprefix('standardphysics_')}", module, imports.get(module, ()))
    _write_package(root, "services/api", API, imports.get(API, ()))
    return root


@pytest.fixture(scope="module")
def repo_graph() -> ImportGraph:
    return import_graph()


def test_the_layering_test_sees_every_package(repo_graph):
    assert set(repo_graph) == set(ALLOWED_DEPENDENCIES)


def test_the_allowed_dependencies_have_no_cycle():
    assert find_cycles(ALLOWED_DEPENDENCIES) == []


def test_every_import_between_packages_is_an_allowed_edge(repo_graph):
    offenders = forbidden_imports(repo_graph, ALLOWED_DEPENDENCIES)
    assert not offenders, "imports outside ALLOWED_DEPENDENCIES:\n" + "\n".join(offenders)


def test_no_packages_import_each_other_in_a_cycle(repo_graph):
    cycles = import_cycles(repo_graph)
    assert not cycles, "package import cycles:\n" + "\n".join(cycles)


def test_the_checker_names_an_import_outside_the_allowed_edges(tmp_path):
    graph = import_graph(_fake_repo(tmp_path, {PIPELINE: [CONTRACTS, API]}))

    assert forbidden_imports(graph, ALLOWED_DEPENDENCIES) == [
        "standardphysics_pipeline imports standardphysics_api in "
        "packages/pipeline/standardphysics_pipeline/__init__.py"
    ]


def test_the_checker_rejects_any_import_from_a_package_the_table_does_not_know(tmp_path):
    graph = import_graph(_fake_repo(tmp_path, {}))
    graph["standardphysics_newcomer"] = {CONTRACTS: {pathlib.Path("packages/newcomer/x.py")}}

    assert forbidden_imports(graph, ALLOWED_DEPENDENCIES) == [
        "standardphysics_newcomer imports standardphysics_contracts in packages/newcomer/x.py"
    ]


def test_the_checker_names_every_step_of_a_cycle(tmp_path):
    graph = import_graph(_fake_repo(tmp_path, {PIPELINE: [AGENTS], AGENTS: [PIPELINE, CONTRACTS]}))

    assert import_cycles(graph) == [
        "standardphysics_agents imports standardphysics_pipeline in "
        "packages/agents/standardphysics_agents/__init__.py; "
        "standardphysics_pipeline imports standardphysics_agents in "
        "packages/pipeline/standardphysics_pipeline/__init__.py"
    ]


def test_find_cycles_lists_a_longer_cycle_once():
    edges = {"c": ["a"], "a": ["b"], "b": ["c", "d"], "d": []}

    assert find_cycles(edges) == [("a", "b", "c")]


def test_every_package_declares_the_siblings_it_imports(repo_graph):
    missing = {}
    for project in _projects():
        undeclared = set(repo_graph[_own_module(project)]) - _declared(project)
        if undeclared:
            missing[str(project.relative_to(ROOT))] = sorted(undeclared)
    assert not missing, f"imported but not declared in pyproject dependencies: {missing}"
