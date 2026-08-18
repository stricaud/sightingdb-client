"""Test helpers: a fake SightingDB that answers over an httpx mock transport."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

import sightingdb

HELP_PAGE = (
    "SightingDB 0.5.3, written by Sebastien Tricaud\nREST Endpoints:\n\t/w: write (GET)\n"
)


class FakeServer:
    """Answers requests from a handler, and remembers what it was asked."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def build(handler, *, cls=sightingdb.SightingDB, **kwargs):
    """A client of ``cls`` talking to a fake server driven by ``handler``.

    ``httpx.MockTransport`` serves both the sync and the async client, so the
    two are exercised against exactly the same fake server.
    """
    server = FakeServer(handler)
    kwargs.setdefault("apikey", "secret")
    client = cls(
        "https://sightingdb.test:9999",
        use_env=False,
        use_file=False,
        transport=httpx.MockTransport(server),
        **kwargs,
    )
    return client, server


def answering(payload, status: int = 200, **kwargs):
    """A client whose server answers one canned reply to anything."""
    return build(lambda request: json_response(payload, status), **kwargs)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    """No test may be steered by the developer's own SIGHTINGDB_* settings."""
    import os

    for name in list(os.environ):
        # SIGHTINGDB_TEST_* names point the live tests at a real server and are
        # not settings the client reads, so they stay.
        if name.startswith("SIGHTINGDB_") and not name.startswith("SIGHTINGDB_TEST_"):
            monkeypatch.delenv(name, raising=False)
