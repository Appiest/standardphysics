"""Every log the Droplet keeps has a ceiling: the containers' through Docker's
json-file options in docker-compose.yml, and the scripts' own files through
the logrotate rule setup.sh installs."""

from __future__ import annotations

import pathlib
import re

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy/digitalocean"
LOG_WRITERS = [REPO / "scripts/deploy.sh", *sorted(DEPLOY.glob("*.sh"))]


def test_every_container_rotates_its_logs():
    services = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())["services"]
    for name, service in services.items():
        logging = service.get("logging", {})
        assert logging.get("driver") == "json-file", name
        assert {"max-size", "max-file"} <= logging.get("options", {}).keys(), name


def test_setup_installs_the_logrotate_rule():
    assert "logrotate.conf" in (DEPLOY / "setup.sh").read_text()


def test_every_log_file_a_deploy_script_writes_has_a_logrotate_rule():
    written = {path for script in LOG_WRITERS for path in re.findall(r"/var/log/[\w.-]+", script.read_text())}
    assert "/var/log/standardphysics-deploys.log" in written
    rules = DEPLOY / "logrotate.conf"
    rotated = set(re.findall(r"^(/\S+) \{", rules.read_text(), re.MULTILINE)) if rules.exists() else set()
    assert written <= rotated
