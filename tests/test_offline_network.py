"""The offline guard refuses the internet and leaves this machine alone.

The plugin lives in tests/support/offline_network.py and is loaded for this run
by pytest.ini, so these tests exercise the same guard every other test runs
under. The ones that need to see a whole pytest session, such as a swallowed
refusal failing its test, run one in a subprocess.
"""

from __future__ import annotations

import os
import pathlib
import socket
import tempfile
import threading

import pytest
from offline_network import GUARD, NetworkAccessDenied, is_local_host

pytest_plugins = ["pytester"]

SUPPORT = pathlib.Path(__file__).resolve().parent / "support"
DOCUMENTATION_ADDRESS = ("203.0.113.7", 443)
MARKER_INI = "[pytest]\nmarkers =\n    network: reaches a real host\n"


@pytest.fixture
def refusals():
    """Hands the test the refusals it caused, so the guard does not fail it for them."""
    yield GUARD
    GUARD.take_attempts()


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("127.0.0.1", True), ("127.8.9.10", True), ("::1", True), ("localhost", True), ("LOCALHOST", True),
        ("", True), ("0.0.0.0", True), ("::ffff:127.0.0.1", True), (None, True), (b"127.0.0.1", True),
        ("203.0.113.7", False), ("2001:db8::1", False), ("example.com", False), ("::ffff:203.0.113.7", False),
    ],
)
def test_only_this_machine_counts_as_local(host, local):
    assert is_local_host(host) is local


def test_a_connection_to_the_internet_is_refused_before_it_leaves(refusals):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkAccessDenied, match="203.0.113.7"):
            sock.connect(DOCUMENTATION_ADDRESS)
    assert len(refusals.attempts) == 1


def test_connect_ex_is_refused_too(refusals):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkAccessDenied):
            sock.connect_ex(DOCUMENTATION_ADDRESS)


def test_a_name_lookup_is_refused(refusals):
    with pytest.raises(NetworkAccessDenied, match="example.com"):
        socket.create_connection(("example.com", 443), timeout=1)


def test_the_refusal_is_not_an_oserror_that_fallback_code_would_swallow():
    assert not issubclass(NetworkAccessDenied, OSError)


def _echo_once(server: socket.socket) -> None:
    connection, _ = server.accept()
    with connection:
        connection.sendall(connection.recv(16))


def _tcp_client(address) -> socket.socket:
    return socket.create_connection(address[:2], timeout=5)


def _unix_client(path: str) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    client.connect(path)
    return client


def _round_trip(family: int, bind_to, open_client) -> bytes:
    with socket.socket(family, socket.SOCK_STREAM) as server:
        server.bind(bind_to)
        server.listen(1)
        threading.Thread(target=_echo_once, args=(server,), daemon=True).start()
        with open_client(server.getsockname()) as client:
            client.sendall(b"ping")
            return client.recv(16)


def test_loopback_connections_still_work():
    assert _round_trip(socket.AF_INET, ("127.0.0.1", 0), _tcp_client) == b"ping"


def test_localhost_resolves_and_connects():
    assert _round_trip(socket.AF_INET, ("127.0.0.1", 0), lambda address: _tcp_client(("localhost", address[1]))) == b"ping"


@pytest.mark.skipif(not socket.has_ipv6, reason="this machine has no IPv6")
def test_ipv6_loopback_connections_still_work():
    assert _round_trip(socket.AF_INET6, ("::1", 0), _tcp_client) == b"ping"


def test_unix_sockets_still_work():
    with tempfile.TemporaryDirectory(prefix="sp", dir="/tmp") as directory:
        assert _round_trip(socket.AF_UNIX, os.path.join(directory, "s"), _unix_client) == b"ping"


def _run_session(pytester: pytest.Pytester, monkeypatch, source: str, *args: str) -> pytest.RunResult:
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(SUPPORT), os.environ.get("PYTHONPATH", "")]))
    pytester.makeini(MARKER_INI)
    pytester.makepyfile(source)
    return pytester.runpytest_subprocess("-p", "offline_network", "-p", "no:cacheprovider", *args)


SWALLOWED_REFUSAL = """
import socket

def test_falls_back_quietly():
    try:
        socket.create_connection(("203.0.113.7", 443), timeout=1)
    except Exception:
        pass
"""


def test_a_test_that_swallows_the_refusal_still_fails(pytester, monkeypatch):
    result = _run_session(pytester, monkeypatch, SWALLOWED_REFUSAL)

    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*this test tried to reach the network*", "*203.0.113.7*"])


MARKED_TEST = """
import pytest
from offline_network import GUARD

@pytest.mark.network
def test_needs_the_internet():
    assert GUARD.allowed
"""


def test_a_network_test_is_skipped_unless_asked_for(pytester, monkeypatch):
    monkeypatch.delenv("SP_ALLOW_NETWORK_TESTS", raising=False)

    _run_session(pytester, monkeypatch, MARKED_TEST, "-rs").assert_outcomes(skipped=1)


def test_a_network_test_runs_with_the_guard_aside_when_asked_for(pytester, monkeypatch):
    monkeypatch.setenv("SP_ALLOW_NETWORK_TESTS", "1")

    _run_session(pytester, monkeypatch, MARKED_TEST).assert_outcomes(passed=1)


MODEL_TEST = """
def test_calls_a_model(with_models):
    assert with_models
"""


def test_asking_for_a_real_model_marks_the_test_as_needing_the_network(pytester, monkeypatch):
    monkeypatch.delenv("SP_ALLOW_NETWORK_TESTS", raising=False)
    pytester.makeconftest((pathlib.Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8"))

    _run_session(pytester, monkeypatch, MODEL_TEST).assert_outcomes(skipped=1)
