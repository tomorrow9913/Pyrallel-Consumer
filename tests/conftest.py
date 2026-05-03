"""Shared pytest configuration for stable local test runs."""

import os

import pytest

_RUNTIME_PROFILE_ENV_DEFAULTS = {
    "EXECUTION_ROUTE_BATCH_SIZE": "64",
    "PROCESS_BATCH_SIZE": "1",
    "PROCESS_MAX_BATCH_WAIT_MS": "0",
    "PROCESS_TRANSPORT_MODE": "worker_pipes",
}

for _key, _value in _RUNTIME_PROFILE_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture(autouse=True)
def stable_runtime_profile_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests independent from a developer's local .env profile."""
    for key, value in _RUNTIME_PROFILE_ENV_DEFAULTS.items():
        monkeypatch.setenv(key, value)
