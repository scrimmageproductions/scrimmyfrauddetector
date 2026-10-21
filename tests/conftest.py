import pytest

from scrimmy.clients import EvidenceLedger, HostRateLimiter, ResilientHTTP, RetryPolicy


@pytest.fixture
def no_wait_limiter():
    return HostRateLimiter({}, default_interval=0.0)


@pytest.fixture
def make_http(no_wait_limiter):
    def _make(transport, max_attempts=4):
        sleeps = []
        http = ResilientHTTP(ledger=EvidenceLedger(), transport=transport, limiter=no_wait_limiter,
                             retry=RetryPolicy(max_attempts=max_attempts), sleep=sleeps.append)
        http.sleeps = sleeps
        return http

    return _make
