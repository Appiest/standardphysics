"""Password guessing costs the guesser, not the server.

Sign-in is throttled per account and per network at once, and an attempt is
counted the moment it is let in, so a burst of parallel requests can't all slip
past the check before any of them is recorded.
"""

import threading

import pytest

from conftest import OWNER_EMAIL, sign_up
from standardphysics_api import accounts
from standardphysics_api.auth import SIGN_IN_ATTEMPTS, SIGN_IN_ATTEMPTS_PER_ADDRESS, AttemptLimiter
from standardphysics_api.errors import ApiProblem


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rotating_unknown_emails_from_one_network_is_throttled(make_client):
    with make_client(sign_in_as_owner=False) as browser:
        codes = [
            browser.post("/api/auth/sign-in", json={"email": f"nobody{n}@example.com", "password": "guess"}).status_code
            for n in range(SIGN_IN_ATTEMPTS_PER_ADDRESS + 1)
        ]
    assert codes[:SIGN_IN_ATTEMPTS_PER_ADDRESS] == [401] * SIGN_IN_ATTEMPTS_PER_ADDRESS
    assert codes[-1] == 429


def test_a_throttled_attempt_costs_no_scrypt(make_client, monkeypatch):
    with make_client(sign_in_as_owner=False) as browser:
        for n in range(SIGN_IN_ATTEMPTS_PER_ADDRESS):
            browser.post("/api/auth/sign-in", json={"email": f"nobody{n}@example.com", "password": "guess"})
        calls = count_scrypt_calls(monkeypatch)
        response = browser.post("/api/auth/sign-in", json={"email": "one-more@example.com", "password": "guess"})
    assert response.status_code == 429
    assert calls == []


def test_an_unknown_email_costs_exactly_what_a_wrong_password_does(make_client, monkeypatch):
    with make_client(sign_in_as_owner=False) as browser:
        sign_up(browser)
        browser.post("/api/auth/sign-in", json={"email": "warm-up@example.com", "password": "guess"})
        calls = count_scrypt_calls(monkeypatch)
        browser.post("/api/auth/sign-in", json={"email": OWNER_EMAIL, "password": "wrong-password"})
        wrong_password = len(calls)
        browser.post("/api/auth/sign-in", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert wrong_password == 1
    assert len(calls) - wrong_password == 1


def test_parallel_wrong_passwords_cannot_exceed_the_account_limit(make_client):
    attempts = SIGN_IN_ATTEMPTS * 2
    with make_client(sign_in_as_owner=False) as browser:
        sign_up(browser)
        browser.cookies.clear()
        codes: list[int] = []
        start = threading.Barrier(attempts)

        def guess() -> None:
            start.wait()
            response = browser.post("/api/auth/sign-in", json={"email": OWNER_EMAIL, "password": "wrong-password"})
            codes.append(response.status_code)

        threads = [threading.Thread(target=guess) for _ in range(attempts)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert codes.count(401) == SIGN_IN_ATTEMPTS
    assert codes.count(429) == attempts - SIGN_IN_ATTEMPTS


def test_concurrent_admissions_never_exceed_the_limit():
    limiter = AttemptLimiter(limit=5, window=60)
    admitted: list[bool] = []
    start = threading.Barrier(50)

    def attempt() -> None:
        start.wait()
        try:
            limiter.admit("owner@example.com")
            admitted.append(True)
        except ApiProblem:
            admitted.append(False)

    threads = [threading.Thread(target=attempt) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert admitted.count(True) == 5


def test_one_full_key_refuses_the_attempt_without_charging_the_others():
    limiter = AttemptLimiter(limit=1, window=60)
    limiter.admit("address")
    with pytest.raises(ApiProblem):
        limiter.admit("address", "fresh@example.com")
    limiter.admit("fresh@example.com")


def test_the_limiter_stays_bounded_under_many_keys():
    limiter = AttemptLimiter(limit=3, window=60, max_keys=100)
    for n in range(10_000):
        limiter.admit(f"address-{n}")
    assert len(limiter) <= 100


def test_keys_nobody_has_used_for_a_window_are_evicted():
    clock = Clock()
    limiter = AttemptLimiter(limit=3, window=60, clock=clock)
    for n in range(50):
        limiter.admit(f"address-{n}")
    clock.now += 61
    limiter.admit("someone-new")
    assert len(limiter) == 1


def test_a_window_later_the_same_key_is_let_in_again():
    clock = Clock()
    limiter = AttemptLimiter(limit=1, window=60, clock=clock)
    limiter.admit("address")
    with pytest.raises(ApiProblem):
        limiter.admit("address")
    clock.now += 61
    limiter.admit("address")


def count_scrypt_calls(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = accounts._scrypt

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(accounts, "_scrypt", counted)
    return calls
