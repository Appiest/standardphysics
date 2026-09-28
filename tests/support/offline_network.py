"""A pytest plugin that keeps both suites off the network.

Clearing the model keys in conftest.py stops the paths we know about from
calling out, but nothing stopped the ones we don't: a client built with a
default URL, a library that phones home, a DNS lookup buried in a helper. On a
laptop those pass by reaching the internet, and in CI they hang or flake.

So while a test runs, sockets may only connect to loopback addresses, to Unix
sockets, and to nothing that needs resolving first. Anything else raises
NetworkAccessDenied and fails the test, even when the code under test catches
the error and falls back quietly, because the attempt itself is the bug.

A test that genuinely needs the network carries `@pytest.mark.network`. It is
skipped unless SP_ALLOW_NETWORK_TESTS=1 is set, so CI stays offline, and when it
does run the guard stands aside for it.

The root pytest.ini and services/api's pyproject both load this with
`-p offline_network` and register the marker. `-p no:offline_network` turns it
off for a one-off run.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest

ALLOW_NETWORK_TESTS = "SP_ALLOW_NETWORK_TESTS"
NETWORK_MARKER = "network"
LOCAL_HOST_NAMES = frozenset({"", "localhost"})


class NetworkAccessDenied(RuntimeError):
    """A test tried to reach a host that is not this machine.

    It is not an OSError on purpose: code that treats OSError as "offline, fall
    back" would swallow it, and the test would never say it tried.
    """


def is_local_host(host: str | bytes | None) -> bool:
    if host is None:
        return True
    name = host.decode() if isinstance(host, bytes) else host
    if name.lower() in LOCAL_HOST_NAMES:
        return True
    try:
        address = ipaddress.ip_address(name.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_unspecified


def is_local_address(address: Any) -> bool:
    """Unix socket paths are strings or bytes; internet addresses are (host, port, ...) tuples."""
    if not isinstance(address, tuple):
        return True
    return is_local_host(address[0])


class OfflineGuard:
    """Patches socket so non-loopback connections and lookups raise NetworkAccessDenied."""

    def __init__(self) -> None:
        self.allowed = False
        self.attempts: list[str] = []
        self._originals: dict[tuple[Any, str], Callable[..., Any]] = {}

    def refuse(self, what: str) -> NetworkAccessDenied:
        message = (
            f"{what}: tests run offline. Mock the call, or mark the test "
            f"@pytest.mark.{NETWORK_MARKER} and run it with {ALLOW_NETWORK_TESTS}=1."
        )
        self.attempts.append(message)
        return NetworkAccessDenied(message)

    def install(self) -> None:
        self._patch(socket.socket, "connect", self._guard_connect)
        self._patch(socket.socket, "connect_ex", self._guard_connect)
        self._patch(socket, "getaddrinfo", self._guard_lookup)

    def uninstall(self) -> None:
        for (owner, name), original in self._originals.items():
            setattr(owner, name, original)
        self._originals.clear()

    def take_attempts(self) -> list[str]:
        attempts, self.attempts = self.attempts, []
        return attempts

    def _patch(self, owner: Any, name: str, make_guarded: Callable[[Callable[..., Any]], Callable[..., Any]]) -> None:
        original = getattr(owner, name)
        self._originals[(owner, name)] = original
        setattr(owner, name, make_guarded(original))

    def _guard_connect(self, original: Callable[..., Any]) -> Callable[..., Any]:
        guard = self

        def connect(sock: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
            if not guard.allowed and not is_local_address(address):
                raise guard.refuse(f"refused a connection to {address!r}")
            return original(sock, address, *args, **kwargs)

        return connect

    def _guard_lookup(self, original: Callable[..., Any]) -> Callable[..., Any]:
        guard = self

        def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            if not guard.allowed and not is_local_host(host):
                raise guard.refuse(f"refused to look up {host!r}")
            return original(host, *args, **kwargs)

        return getaddrinfo


GUARD = OfflineGuard()


def network_tests_allowed() -> bool:
    return os.environ.get(ALLOW_NETWORK_TESTS) == "1"


def pytest_configure(config: pytest.Config) -> None:
    GUARD.install()


def pytest_unconfigure(config: pytest.Config) -> None:
    GUARD.uninstall()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if network_tests_allowed():
        return
    skip = pytest.mark.skip(reason=f"reaches the network; set {ALLOW_NETWORK_TESTS}=1 to run it")
    for item in items:
        if item.get_closest_marker(NETWORK_MARKER):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _offline(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail any test that tried to leave the machine, even if it swallowed the refusal."""
    GUARD.take_attempts()
    GUARD.allowed = request.node.get_closest_marker(NETWORK_MARKER) is not None
    try:
        yield
    finally:
        GUARD.allowed = False
    attempts = GUARD.take_attempts()
    if attempts:
        pytest.fail("this test tried to reach the network:\n" + "\n".join(attempts), pytrace=False)
