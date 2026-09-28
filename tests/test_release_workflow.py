"""The image that reaches GHCR is the image CI built once, tested and scanned.

The image job builds, smoke-tests and runs the Blender regressions against one
image, then hands on its identity: the candidate digest it pushed on master, or
an archive of the same image elsewhere. The vulnerability scan reads that
identity instead of building its own copy, and publish retags the candidate
digest instead of building again, so the bytes released are the bytes checked.

Everything the build pulls in is pinned too: base images by digest, pip at a
named version, and the downloaded XcodeGen against a sha256 sum.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
BUILD_STEP = re.compile(r"docker/build-push-action|docker (buildx )?build\b")
UNBOUNDED_PIP_UPGRADE = re.compile(r"pip install[^\n]*--upgrade pip(?!=)")


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _jobs() -> dict[str, Any]:
    return _workflow("ci.yml")["jobs"]


def _step_text(step: dict[str, Any]) -> str:
    return f"{step.get('uses', '')}\n{step.get('run', '')}\n{step.get('if', '')}"


def _job_text(job: dict[str, Any]) -> str:
    return "\n".join(_step_text(step) for step in job["steps"])


def test_only_the_image_job_builds_the_image():
    builders = [name for name, job in _jobs().items() if BUILD_STEP.search(_job_text(job))]
    assert builders == ["image"]


def test_the_image_job_hands_on_the_digest_and_id_it_tested():
    outputs = _jobs()["image"]["outputs"]
    assert "steps." in outputs["candidate"]
    assert "steps." in outputs["image-id"]


UNRELEASED_JOBS = {"publish", "reels"}


def test_publish_waits_for_every_job_behind_the_image():
    jobs = _jobs()
    assert set(jobs["publish"]["needs"]) == set(jobs) - UNRELEASED_JOBS


def test_the_reels_app_is_linted_and_typechecked():
    reels = _jobs()["reels"]
    runs = [step.get("run", "") for step in reels["steps"]]
    assert reels["defaults"]["run"]["working-directory"] == "apps/reels"
    assert runs[-3:] == ["npm ci", "npm run lint", "npm run typecheck"]


def test_publish_retags_the_candidate_digest_the_image_job_pushed():
    publish = _jobs()["publish"]
    step_env = {key: value for step in publish["steps"] for key, value in step.get("env", {}).items()}
    assert "needs.image.outputs.candidate" in step_env["CANDIDATE"]
    assert '"$CANDIDATE"' in _job_text(publish)
    assert "candidate-$GITHUB_SHA" not in _job_text(publish)


def test_the_vulnerability_scan_reads_the_tested_image():
    scan = _jobs()["image-vulnerabilities"]
    text = f"{_job_text(scan)}\n{scan['env']}"
    assert scan["name"] == "Critical vulnerabilities with a fix in the image"
    assert scan["needs"] == "image" or scan["needs"] == ["image"]
    assert "needs.image.outputs.candidate" in text
    assert "needs.image.outputs.image-id" in text
    assert 'registry:$CANDIDATE' in text
    assert text.count("--only-fixed --fail-on critical") == 2


def _global_build_args(dockerfile: str) -> dict[str, str]:
    before_first_stage = dockerfile.split("\nFROM ", 1)[0]
    return dict(re.findall(r"^ARG (\w+)=(\S+)", before_first_stage, re.MULTILINE))


def test_every_dockerfile_base_is_pinned_by_digest():
    dockerfile = (ROOT / "Dockerfile").read_text()
    build_args = _global_build_args(dockerfile)
    stages = re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    bases = [re.sub(r"\$\{(\w+)\}", lambda arg: build_args.get(arg[1], ""), image) for image in stages]
    unpinned = [image for image in bases if "@sha256:" not in image]
    assert not unpinned


def test_pip_upgrades_name_a_version():
    sources = [ROOT / "Dockerfile", *WORKFLOWS.glob("*.yml")]
    unbounded = [path.name for path in sources if UNBOUNDED_PIP_UPGRADE.search(path.read_text())]
    assert not unbounded


def test_xcodegen_is_checked_against_its_sha256_before_it_runs():
    ios = _workflow("ios.yml")
    assert re.fullmatch(r"[0-9a-f]{64}", ios["env"]["XCODEGEN_SHA256"])
    stale_check = next(
        step for step in ios["jobs"]["simulator"]["steps"] if "xcodegen.zip" in step.get("run", "")
    )
    run = stale_check["run"]
    assert run.index("shasum -a 256 -c") < run.index("unzip")
