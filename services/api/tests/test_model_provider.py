"""Nothing a model server sends is trusted: replies are capped in size and time and checked for shape."""

import uuid

import pytest
from model_provider import Reply, completion, serve_provider

from standardphysics_api.errors import ApiProblem
from standardphysics_api.model_chooser import MAX_REPLY_BYTES, ModelChooser, ModelReplyError, ModelSlots

MESSAGES = [{"role": "user", "content": "Pick one."}]


@pytest.fixture
def provider():
    yield from serve_provider()


def _chooser(provider, reply_seconds: float = 2.0) -> ModelChooser:
    return ModelChooser(provider.url, "test-model", reply_seconds=reply_seconds)


def test_a_well_formed_reply_is_the_message_content(provider):
    provider.reply = Reply(completion('{"choose": [1]}'))
    assert _chooser(provider).ask(MESSAGES) == '{"choose": [1]}'


@pytest.mark.parametrize("declare_length", [True, False])
def test_an_oversized_reply_is_refused_before_it_is_parsed(provider, declare_length):
    provider.reply = Reply(completion("x" * (MAX_REPLY_BYTES * 4)), declare_length=declare_length)
    with pytest.raises(ModelReplyError, match="over 64 KB"):
        _chooser(provider).ask(MESSAGES)


@pytest.mark.parametrize("body", [
    b"<html>502 Bad Gateway</html>",
    b'{"choices": [{"message": {"content": "cut off mid',
    b'{"choices": []}',
    b'{"error": {"message": "model not loaded"}}',
    b'{"choices": [{"message": {"content": 7}}]}',
    b"[]",
])
def test_malformed_or_unexpected_replies_raise_a_model_reply_error(provider, body):
    provider.reply = Reply(body)
    with pytest.raises(ModelReplyError, match="couldn't be read"):
        _chooser(provider).ask(MESSAGES)


def test_a_provider_slower_than_the_call_timeout_times_out(provider):
    provider.reply = Reply(completion("late"), delay=1.0)
    with pytest.raises(TimeoutError):
        _chooser(provider, reply_seconds=0.2).ask(MESSAGES)


def test_a_reply_that_drips_in_past_the_deadline_times_out(provider):
    provider.reply = Reply(completion("slow"), drip=0.02)
    with pytest.raises(TimeoutError):
        _chooser(provider, reply_seconds=0.3).ask(MESSAGES)


def test_a_call_never_waits_longer_than_the_chooser_allows(provider):
    provider.reply = Reply(completion("late"), delay=1.0)
    with pytest.raises(TimeoutError):
        _chooser(provider, reply_seconds=5.0).ask(MESSAGES, seconds=0.2)


def test_the_reply_timeout_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("SP_MENU_MODEL_URL", "http://model.test/v1")
    monkeypatch.setenv("SP_MENU_MODEL", "test-model")
    assert ModelChooser.from_environment().reply_seconds == 120.0
    monkeypatch.setenv("SP_MENU_MODEL_REPLY_SECONDS", "45")
    assert ModelChooser.from_environment().reply_seconds == 45.0
    monkeypatch.setenv("SP_MENU_MODEL_REPLY_SECONDS", "3600")
    with pytest.raises(ValueError):
        ModelChooser.from_environment()


def test_slots_cap_each_owner_with_a_429_and_the_server_with_a_503():
    slots = ModelSlots(per_owner=1, total=2)
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    slots.take(first)
    with pytest.raises(ApiProblem) as owner_full:
        slots.take(first)
    slots.take(second)
    with pytest.raises(ApiProblem) as server_full:
        slots.take(third)
    assert owner_full.value.status == 429 and server_full.value.status == 503
    assert owner_full.value.headers == {"Retry-After": "30"}
    slots.give_back(first)
    with slots.held(third):
        pass
    slots.take(first)
