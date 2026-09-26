"""Shared fixtures. No test touches the internet: downloads hit a local HTTP server."""

from __future__ import annotations

import pytest

from fakeshare import FakeShare, serve


@pytest.fixture
def share():
    s = FakeShare()
    server = serve(s)
    yield s
    server.shutdown()
    server.server_close()


@pytest.fixture
def no_sleep():
    """A RetryPolicy sleep hook that records waits instead of sleeping."""
    waits: list[float] = []
    return waits
