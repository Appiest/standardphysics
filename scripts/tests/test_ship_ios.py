"""scripts/ship-ios.sh, run against stand-ins for git, gh and the Xcode tools so nothing is built or uploaded.

The script is copied into a scratch checkout with its own apps/ios/project.yml,
so the build number it bumps and the log it appends to are the scratch copies.
The stand-in gh answers `gh run list` for a workflow from FAKE_RUNS_<workflow>,
a comma-separated list of run conclusions, empty when the commit has no runs.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SHIP = REPO / "scripts/ship-ios.sh"
COMMIT = "0123456789abcdef0123456789abcdef01234567"

STAND_INS = {
    "git": """[ "$1" = -C ] && shift 2
echo "git $*" >> "$STAND_IN_LOG"
case "$1" in
  status) printf '%s' "${FAKE_STATUS:-}" ;;
  merge-base) exit "${FAKE_NOT_ON_MASTER:-0}" ;;
  rev-parse) echo "$FAKE_COMMIT" ;;
esac
""",
    "gh": """echo "gh $*" >> "$STAND_IN_LOG"
while [ "$1" != --workflow ]; do shift; done
runs="FAKE_RUNS_${2%.yml}"
printf '%s' "${!runs:-}" | tr , '\\n'
""",
    "xcodebuild": """echo "xcodebuild $*" >> "$STAND_IN_LOG"
while [ $# -gt 0 ]; do
  [ "$1" = -exportPath ] && mkdir -p "$2" && touch "$2/StandardPhysics.ipa"
  shift
done
""",
    "xcodegen": "exit 0\n",
    "xcrun": 'echo "xcrun $*" >> "$STAND_IN_LOG"\n',
}


@pytest.fixture
def checkout(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STAND_INS.items():
        stand_in = bin_dir / name
        stand_in.write_text("#!/usr/bin/env bash\n" + body)
        stand_in.chmod(0o755)
    (tmp_path / "repo/scripts").mkdir(parents=True)
    shutil.copy(SHIP, tmp_path / "repo/scripts/ship-ios.sh")
    (tmp_path / "repo/apps/ios").mkdir(parents=True)
    (tmp_path / "repo/apps/ios/project.yml").write_text('    CFBundleVersion: "9"\n')
    (tmp_path / "AuthKey_TEST.p8").write_text("not a real key\n")
    return tmp_path


def ship(checkout: pathlib.Path, **environment: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{checkout / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(checkout / "build"),
        "STAND_IN_LOG": str(checkout / "calls.log"),
        "FAKE_COMMIT": COMMIT,
        "FAKE_RUNS_ci": "success",
        "FAKE_RUNS_ios": "success",
        "SP_ASC_KEY_ID": "TESTKEY",
        "SP_ASC_ISSUER_ID": "issuer",
        "SP_ASC_KEY_PATH": str(checkout / "AuthKey_TEST.p8"),
    }
    env.pop("SP_SHIP_UNVERIFIED", None)
    env.update(environment)
    script = checkout / "repo/scripts/ship-ios.sh"
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60)


def calls(checkout: pathlib.Path) -> str:
    log = checkout / "calls.log"
    return log.read_text() if log.exists() else ""


def shipped_log(checkout: pathlib.Path) -> str:
    log = checkout / "repo/apps/ios/testflight-builds.log"
    return log.read_text() if log.exists() else ""


def build_number(checkout: pathlib.Path) -> str:
    return (checkout / "repo/apps/ios/project.yml").read_text().strip()


def assert_nothing_shipped(checkout: pathlib.Path, result: subprocess.CompletedProcess) -> None:
    assert result.returncode != 0
    assert "Not shipping" in result.stderr
    assert "xcodebuild" not in calls(checkout)
    assert "xcrun" not in calls(checkout)
    assert build_number(checkout) == 'CFBundleVersion: "9"'
    assert shipped_log(checkout) == ""


def test_a_verified_commit_is_archived_uploaded_and_recorded(checkout):
    result = ship(checkout)

    assert result.returncode == 0, result.stderr
    assert f"gh run list --commit {COMMIT} --workflow ci.yml" in calls(checkout)
    assert f"gh run list --commit {COMMIT} --workflow ios.yml" in calls(checkout)
    assert "git merge-base --is-ancestor HEAD origin/master" in calls(checkout)
    assert "xcrun altool --upload-app" in calls(checkout)
    assert build_number(checkout) == 'CFBundleVersion: "10"'
    assert f"build 10 commit {COMMIT} verified" in shipped_log(checkout)


def test_uncommitted_changes_are_refused(checkout):
    result = ship(checkout, FAKE_STATUS=" M apps/ios/Sources/App.swift\n")

    assert_nothing_shipped(checkout, result)
    assert "uncommitted changes" in result.stderr


def test_a_commit_not_on_origin_master_is_refused(checkout):
    result = ship(checkout, FAKE_NOT_ON_MASTER="1")

    assert_nothing_shipped(checkout, result)
    assert "not on origin/master" in result.stderr


def test_a_failed_ci_run_is_refused(checkout):
    result = ship(checkout, FAKE_RUNS_ci="failure")

    assert_nothing_shipped(checkout, result)
    assert "ci.yml has no successful run" in result.stderr


def test_a_commit_the_ios_workflow_never_ran_on_is_refused(checkout):
    result = ship(checkout, FAKE_RUNS_ios="")

    assert_nothing_shipped(checkout, result)
    assert "ios.yml has no successful run" in result.stderr


def test_a_later_success_after_a_cancelled_run_counts(checkout):
    result = ship(checkout, FAKE_RUNS_ios="cancelled,success")

    assert result.returncode == 0, result.stderr


def test_the_override_ships_unchecked_with_a_warning_and_says_so_in_the_log(checkout):
    result = ship(
        checkout,
        SP_SHIP_UNVERIFIED="1",
        FAKE_STATUS=" M apps/ios/Sources/App.swift\n",
        FAKE_NOT_ON_MASTER="1",
        FAKE_RUNS_ci="",
        FAKE_RUNS_ios="",
    )

    assert result.returncode == 0, result.stderr
    assert "SP_SHIP_UNVERIFIED=1" in result.stderr
    assert "gh " not in calls(checkout)
    assert "xcrun altool --upload-app" in calls(checkout)
    assert f"build 10 commit {COMMIT} unverified" in shipped_log(checkout)
