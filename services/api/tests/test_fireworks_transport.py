"""Fireworks replies are held to the same limits as every other model's: 64 KB and one deadline for the whole reply."""

import pytest
from model_provider import Reply, completion, serve_provider

from standardphysics_api.fireworks import ModelFailed, urllib_transport
from standardphysics_api.model_chooser import MAX_REPLY_BYTES

BODY = {"model": "test-model", "messages": [{"role": "user", "content": "Rearrange."}]}


@pytest.fixture
def provider():
    yield from serve_provider()


def _ask(provider, timeout: float = 2.0):
    return urllib_transport("POST", f"{provider.url}/chat/completions", BODY, {}, timeout)


def test_a_reply_within_the_limits_is_parsed(provider):
    provider.reply = Reply(completion("nudge the table"))
    status, reply = _ask(provider)
    assert status == 200
    assert reply["choices"][0]["message"]["content"] == "nudge the table"


@pytest.mark.parametrize("declare_length", [True, False])
def test_an_oversized_reply_is_refused(provider, declare_length):
    provider.reply = Reply(completion("x" * (MAX_REPLY_BYTES * 4)), declare_length=declare_length)
    with pytest.raises(ModelFailed, match="too large"):
        _ask(provider)


def test_a_reply_that_drips_in_past_the_deadline_times_out(provider):
    provider.reply = Reply(completion("slow"), drip=0.02)
    with pytest.raises(ModelFailed, match="too long"):
        _ask(provider, timeout=0.3)


def test_a_reply_that_is_not_json_reads_as_empty(provider):
    provider.reply = Reply(b"<html>502 Bad Gateway</html>")
    assert _ask(provider) == (200, {})
