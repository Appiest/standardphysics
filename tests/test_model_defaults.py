"""No shipped default or example setting points a model call at gpt-6-astra, which costs 2.5 times Opus 5.5."""

from pathlib import Path

import standardphysics_agents.models as agent_models
import standardphysics_pipeline.astra as labelling

ROOT = Path(__file__).resolve().parents[1]
RETIRED = "gpt-6-astra"
SHIPPED_SETTINGS = (".env.example", "deploy/digitalocean/env.example")


def test_the_example_settings_production_copies_never_name_gpt_6_astra():
    for name in SHIPPED_SETTINGS:
        assert RETIRED not in (ROOT / name).read_text(), name


def test_labelling_and_every_other_model_call_default_to_opus():
    assert labelling.DEFAULT_MODEL == "anthropic/claude-opus-5.5"
    assert agent_models.DEFAULT_MODEL == "anthropic/claude-opus-5.5"


def test_labelling_reads_its_own_setting_named_for_what_it_does():
    assert labelling.MODEL_ENV == "LABEL_MODEL"
