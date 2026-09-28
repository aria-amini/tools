import pytest


@pytest.fixture(autouse=True)
def _no_injected_herdr_bin(monkeypatch):
    # The real env carries the running server's binary; tests must resolve
    # the fakes by name instead.
    monkeypatch.delenv("HERDR_BIN_PATH", raising=False)
