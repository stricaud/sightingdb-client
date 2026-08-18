"""The asynchronous client answers exactly like the synchronous one."""

from __future__ import annotations

import asyncio

import httpx
import pytest

import sightingdb
from conftest import HELP_PAGE, build, json_response

ATTRIBUTE = {
    "value": "127.0.0.1",
    "first_seen": 1566624658,
    "last_seen": 1566624689,
    "count": 2,
    "tags": "",
    "ttl": 0,
    "consensus": 3,
}


def async_client(handler, **kwargs):
    return build(handler, cls=sightingdb.AsyncSightingDB, **kwargs)


async def test_write_and_read():
    answers = {"/w/feeds/ips": {"message": "ok", "count": 4}, "/r/feeds/ips": ATTRIBUTE}
    db, server = async_client(lambda r: json_response(answers[r.url.path]))
    async with db:
        assert await db.write("/feeds/ips/", "127.0.0.1") == 4
        attribute = await db.read("feeds/ips", "127.0.0.1")
    assert attribute.count == 2
    assert server.requests[0].headers["authorization"] == "secret"


async def test_requests_can_be_issued_concurrently():
    db, server = async_client(lambda r: json_response(ATTRIBUTE))
    async with db:
        results = await asyncio.gather(
            *(db.read("ns", f"value-{i}") for i in range(10))
        )
    assert len(results) == 10
    assert len(server.requests) == 10


async def test_read_many_reports_misses_as_results():
    body = {
        "items": [
            ATTRIBUTE,
            {"error": "Path not found", "namespace": "nope", "value": "x"},
        ]
    }
    db, _ = async_client(lambda r: json_response(body))
    async with db:
        results = await db.read_many([("ns", "127.0.0.1"), ("nope", "x")])
    assert [r.found for r in results] == [True, False]


async def test_a_batch_sends_when_the_block_ends():
    db, server = async_client(lambda r: json_response({"message": "ok", "written": 2}))
    async with db:
        async with db.batch() as batch:
            await batch.add("ns", "a")
            await batch.add("ns", "b")
            assert server.requests == []
        assert batch.result.written == 2
    assert len(server.requests) == 1


async def test_errors_map_the_same_way():
    db, _ = async_client(lambda r: json_response({"message": "no"}, 403))
    async with db:
        with pytest.raises(sightingdb.PermissionDeniedError):
            await db.read("ns", "v")


async def test_the_help_page_is_a_protocol_error():
    db, _ = async_client(lambda r: httpx.Response(200, text=HELP_PAGE))
    async with db:
        with pytest.raises(sightingdb.ProtocolError):
            await db.delete("ns")


async def test_a_read_is_retried_after_a_dropped_connection(monkeypatch):
    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    attempts = []

    def flaky(request):
        attempts.append(request)
        if len(attempts) < 2:
            raise httpx.ReadError("connection reset", request=request)
        return json_response(ATTRIBUTE)

    db, _ = async_client(flaky, max_retries=2)
    async with db:
        assert (await db.read("ns", "v")).count == 2
    assert len(attempts) == 2


async def test_a_write_is_not_retried_once_the_connection_was_established(monkeypatch):
    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    attempts = []

    def flaky(request):
        attempts.append(request)
        raise httpx.ReadTimeout("no answer", request=request)

    db, _ = async_client(flaky, max_retries=3)
    async with db:
        with pytest.raises(sightingdb.TimeoutError):
            await db.write("ns", "v")
    assert len(attempts) == 1


async def test_both_clients_expose_the_same_methods():
    public = lambda cls: {  # noqa: E731
        name
        for name in vars(cls)
        if not name.startswith("_") and callable(getattr(cls, name))
    }
    sync = public(sightingdb.SightingDB) - {"close"}
    asynchronous = public(sightingdb.AsyncSightingDB) - {"aclose"}
    assert sync == asynchronous
