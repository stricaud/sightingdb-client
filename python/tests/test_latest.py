"""``latest()`` and ``follow()``: what was written lately, as far as a key may see.

The server does the filtering by key; what the client owns is the request, the
merge across several servers, and the polling — where ``since`` being in whole
seconds would otherwise report the same writes twice.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import datetime, timezone

import httpx
import pytest

import sightingdb
from sightingdb import _protocol as proto

from conftest import HELP_PAGE, build, json_response


def entry(namespace: str, value: str, written: int) -> dict:
    return {"namespace": namespace, "value": value, "written": written}


def page(*items: dict) -> dict:
    return {"items": list(items), "limit": 100, "capacity": 10000}


# -- the request ---------------------------------------------------------


def test_latest_asks_and_decodes():
    db, server = build(
        lambda r: json_response(
            page(entry("feeds/ips", "1.1.1.1", 20), entry("feeds/urls", "u", 10))
        )
    )
    with db:
        found = db.latest(10, since=5, namespace="/feeds//")

    assert server.last.url.path == "/_api/latest"
    params = dict(server.last.url.params)
    assert params == {"limit": "10", "since": "5", "namespace": "feeds"}
    # The key travels as on every call: the server filters by it.
    assert "authorization" in server.last.headers

    assert found == [
        sightingdb.RecentWrite("feeds/ips", "1.1.1.1", 20),
        sightingdb.RecentWrite("feeds/urls", "u", 10),
    ]
    assert found[0].written_at == datetime.fromtimestamp(20, tz=timezone.utc)


def test_since_takes_a_datetime_and_limit_is_capped():
    db, server = build(lambda r: json_response(page()))
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with db:
        db.latest(5000, since=when)
    params = dict(server.last.url.params)
    assert params["limit"] == "1000"
    assert params["since"] == str(int(when.timestamp()))


@pytest.mark.parametrize("limit", [0, -1, True, "10"])
def test_a_nonsense_limit_is_refused_before_sending(limit):
    db, server = build(lambda r: json_response(page()))
    with db, pytest.raises(ValueError):
        db.latest(limit)
    assert server.requests == []


def test_an_older_server_says_so():
    db, _ = build(lambda r: httpx.Response(200, text=HELP_PAGE))
    with db, pytest.raises(sightingdb.ProtocolError, match="does not exist"):
        db.latest()


# -- several servers -----------------------------------------------------

NODES = ("http://a.example:9999", "http://b.example:9999", "http://c.example:9999")


def galaxy(answers: dict[str, list[dict]], down: frozenset[str] = frozenset()):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = f"{request.url.scheme}://{request.url.host}:{request.url.port}"
        seen.append(host)
        if host in down:
            raise httpx.ConnectError("refused", request=request)
        return json_response(page(*answers.get(host, [])))

    return handler, seen


def fanning(handler, cls=sightingdb.SightingDB):
    return cls(
        urls=list(NODES),
        use_env=False,
        use_file=False,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )


def test_every_server_is_asked_and_the_newest_write_of_each_value_kept():
    handler, seen = galaxy(
        {
            NODES[0]: [entry("ns", "a", 30), entry("ns", "b", 10)],
            # Was down when `c` was written, and saw `a` later than node 0 did.
            NODES[1]: [entry("ns", "a", 40), entry("ns", "b", 10)],
            NODES[2]: [entry("ns", "c", 35)],
        }
    )
    with fanning(handler) as db:
        found = db.latest(10)
    assert sorted(seen) == sorted(NODES)
    assert [(e.value, e.written) for e in found] == [("a", 40), ("c", 35), ("b", 10)]

    with fanning(handler) as db:
        assert [e.value for e in db.latest(2)] == ["a", "c"]


def test_a_server_that_is_down_is_left_out_but_not_all_of_them():
    handler, _ = galaxy({NODES[0]: [entry("ns", "a", 1)]}, down=frozenset(NODES[1:]))
    with fanning(handler) as db:
        assert [e.value for e in db.latest()] == ["a"]

    handler, _ = galaxy({}, down=frozenset(NODES))
    with fanning(handler) as db, pytest.raises(sightingdb.TransportError):
        db.latest()


def test_async_merges_the_same_way():
    handler, seen = galaxy(
        {
            NODES[0]: [entry("ns", "a", 30)],
            NODES[2]: [entry("ns", "a", 50), entry("x", "y", 5)],
        }
    )

    async def go():
        async with fanning(handler, cls=sightingdb.AsyncSightingDB) as db:
            return await db.latest()

    found = asyncio.run(go())
    assert [(e.value, e.written) for e in found] == [("a", 50), ("y", 5)]
    assert sorted(seen) == sorted(NODES)


# -- following -----------------------------------------------------------


def test_the_follower_skips_what_it_already_reported_in_the_same_second():
    follower = proto.Follower(since=100)
    first = follower.fresh(
        [sightingdb.RecentWrite("ns", "b", 101), sightingdb.RecentWrite("ns", "a", 100)]
    )
    assert [e.value for e in first] == ["a", "b"], "oldest first"

    # The next poll asks since=101 and gets `b` again, plus a newcomer that
    # arrived in that same second, and `a` written again later.
    second = follower.fresh(
        [
            sightingdb.RecentWrite("ns", "a", 102),
            sightingdb.RecentWrite("ns", "c", 101),
            sightingdb.RecentWrite("ns", "b", 101),
        ]
    )
    assert [(e.value, e.written) for e in second] == [("c", 101), ("a", 102)]


def test_writes_in_the_same_second_come_out_in_the_order_they_arrived():
    follower = proto.Follower(since=0)
    # As the server lists them: newest first, including within one second.
    answer = [
        sightingdb.RecentWrite("ns", "three", 9),
        sightingdb.RecentWrite("ns", "two", 9),
        sightingdb.RecentWrite("ns", "one", 9),
    ]
    assert [e.value for e in follower.fresh(answer)] == ["one", "two", "three"]


def test_without_since_the_first_answer_is_the_baseline_not_news():
    follower = proto.Follower(since=None)
    assert follower.fresh([sightingdb.RecentWrite("ns", "old", 500)]) == []
    later = follower.fresh(
        [sightingdb.RecentWrite("ns", "old", 500), sightingdb.RecentWrite("ns", "new", 500)]
    )
    assert [e.value for e in later] == ["new"]


def test_follow_polls_and_yields_only_new_writes():
    polls = iter(
        [
            page(entry("ns", "old", 10)),
            page(entry("ns", "new", 11), entry("ns", "old", 10)),
            page(entry("ns", "newer", 12), entry("ns", "new", 11)),
        ]
    )
    db, server = build(lambda r: json_response(next(polls)))
    with db:
        got = list(itertools.islice(db.follow(namespace="ns", interval=0), 2))
    assert [e.value for e in got] == ["new", "newer"]
    # Each poll after the first asks from where the last one ended.
    sinces = [dict(r.url.params)["since"] for r in server.requests]
    assert sinces == ["0", "10", "11"]
    assert all(dict(r.url.params)["namespace"] == "ns" for r in server.requests)


def test_async_follow():
    polls = iter(
        [page(), page(entry("ns", "a", 7)), page(entry("ns", "b", 8), entry("ns", "a", 7))]
    )

    async def go():
        db, _ = build(lambda r: json_response(next(polls)), cls=sightingdb.AsyncSightingDB)
        got = []
        async with db:
            async for write in db.follow(interval=0):
                got.append(write.value)
                if len(got) == 2:
                    break
        return got

    assert asyncio.run(go()) == ["a", "b"]
