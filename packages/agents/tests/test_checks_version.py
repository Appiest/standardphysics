"""The fingerprint of what decides a result changes when a check or a rule does, and only then."""

from standardphysics_agents import checks_version as module


def test_the_fingerprint_is_stable_within_a_process():
    assert module.checks_version() == module.checks_version()
    assert len(module.checks_version()) == 16


def test_the_fingerprint_covers_the_rule_pack_the_checks_and_the_copy():
    files = {path.relative_to(module.PACKAGE).as_posix() for path in module._deciding_files()}
    assert "rules/data/rulepack.v1.json" in files
    assert "rules/data/verification.json" in files
    assert "copy.py" in files
    assert any(name.startswith("checks/") and name.endswith(".py") for name in files)
    assert not any("__pycache__" in name for name in files)


def test_the_fingerprint_covers_the_measurements_a_recheck_runs_but_not_how_the_graph_was_built():
    files = {path.as_posix() for path in module._files()}
    assert any(name.endswith("standardphysics_pipeline/occupancy.py") for name in files)
    assert any(name.endswith("standardphysics_pipeline/measure.py") for name in files)
    assert not any("standardphysics_pipeline/discovery/" in name or "standardphysics_pipeline/textures/" in name
                   for name in files)


def test_changing_a_deciding_file_changes_the_fingerprint(tmp_path, monkeypatch):
    for name in ("checks", "rules"):
        (tmp_path / name).mkdir()
    (tmp_path / "checks" / "width.py").write_text("THRESHOLD = 36\n")
    (tmp_path / "rules" / "pack.json").write_text("{}")
    for name in ("assess.py", "findings.py", "copy.py", "compliance.py"):
        (tmp_path / name).write_text("")
    (tmp_path / "precedents").mkdir()
    monkeypatch.setattr(module, "PACKAGE", tmp_path)
    module.checks_version.cache_clear()
    before = module.checks_version()
    (tmp_path / "checks" / "width.py").write_text("THRESHOLD = 32\n")
    module.checks_version.cache_clear()
    after = module.checks_version()
    module.checks_version.cache_clear()
    assert before != after
