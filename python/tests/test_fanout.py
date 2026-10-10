"""A client pointed at several servers does the fanning out itself.

Two properties matter more than the mechanics, and both are easy to get wrong:

* a fanned-out write must be attributed to one origin, or each server counts it
  under its own name and the counts add together when the servers sync;
* a value must always be read from the same server, or two consecutive reads
  can be served by servers at different stages of catching up and show a count
  going down.
"""

from __future__ import annotations

import httpx
import pytest

import sightingdb
from sightingdb import _routing as routing

NODES = ("http://a.example:9999", "http://b.example:9999", "http://c.example:9999")


class Galaxy:
    """Several servers behind one mock transport, remembering who was asked."""

    def __init__(self, *, down: set[str] | None = None) -> None:
        self.seen: list[httpx.Request] = []
        self.down = down or set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        host = f"{request.url.scheme}://{request.url.host}:{request.url.port}"
        if host in self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path.startswith("/w/"):
            return httpx.Response(200, json={"message": "ok", "count": 1, "new": True})
        if request.url.path == "/wb":
            return httpx.Response(200, json={"message": "ok", "written": 1, "items": []})
        return httpx.Response(
            200,
            json={
                "value": "1.2.3.4",
                "first_seen": 1,
                "last_seen": 2,
                "count": 7,
                "tags": "",
                "ttl": 0,
                "consensus": 1,
            },
        )

    def hosts(self) -> list[str]:
        return [
            f"{r.url.scheme}://{r.url.host}:{r.url.port}" for r in self.seen
        ]


def client(galaxy: Galaxy, urls=NODES, **kw) -> sightingdb.SightingDB:
    return sightingdb.SightingDB(
        urls=list(urls),
        apikey="k",
        use_env=False,
        use_file=False,
        transport=httpx.MockTransport(galaxy),
        **kw,
    )


def test_one_url_behaves_exactly_as_before():
    galaxy = Galaxy()
    db = sightingdb.SightingDB(
        url=NODES[0],
        apikey="k",
        use_env=False,
        use_file=False,
        transport=httpx.MockTransport(galaxy),
    )

    assert not db.fans_out
    assert db.urls == (NODES[0],)
    assert db.write("feeds/ips", "1.2.3.4") == 1
    assert galaxy.hosts() == [NODES[0]]

    # No forwarding headers on a single-server write: for one server the origin
    # is the server's own, and claiming otherwise would mislabel it.
    assert routing.ORIGIN_HEADER not in galaxy.seen[0].headers
    assert routing.HOPS_HEADER not in galaxy.seen[0].headers


def test_a_write_reaches_every_server():
    galaxy = Galaxy()
    db = client(galaxy)

    assert db.fans_out
    db.write("feeds/ips", "1.2.3.4")

    assert sorted(galaxy.hosts()) == sorted(NODES), "a server was missed"


def test_every_copy_of_a_write_carries_one_origin():
    """The property that stops fan-out double counting.

    Without it each server counts the same write under its own name, and the
    two counts add together when the servers sync.
    """
    galaxy = Galaxy()
    db = client(galaxy, node_id="my-client")

    db.write("feeds/ips", "1.2.3.4")

    origins = {r.headers[routing.ORIGIN_HEADER] for r in galaxy.seen}
    assert origins == {"my-client"}, "the servers were given different origins"

    # The server honours the origin only on a request that also says it was
    # forwarded, so both headers go together or neither works.
    for request in galaxy.seen:
        assert int(request.headers[routing.HOPS_HEADER]) >= 1


def test_a_bulk_write_also_carries_the_origin():
    galaxy = Galaxy()
    db = client(galaxy, node_id="my-client")

    db.write_many([("feeds/ips", "1.1.1.1"), ("feeds/ips", "2.2.2.2")])

    assert {r.url.path for r in galaxy.seen} == {"/wb"}
    assert {r.headers[routing.ORIGIN_HEADER] for r in galaxy.seen} == {"my-client"}


def test_a_write_survives_a_server_being_down():
    galaxy = Galaxy(down={NODES[1]})
    db = client(galaxy, max_retries=0)

    # The servers that took it are what the dead one catches up from, so one
    # being down is not a failed write.
    assert db.write("feeds/ips", "1.2.3.4") == 1
    assert NODES[1] in galaxy.hosts(), "the down server was not even tried"


def test_a_write_that_reached_nobody_raises():
    galaxy = Galaxy(down=set(NODES))
    db = client(galaxy, max_retries=0)

    # Reporting success for a write that exists nowhere would be the one
    # unrecoverable thing: no later sync can invent it.
    with pytest.raises(sightingdb.TransportError):
        db.write("feeds/ips", "1.2.3.4")


def test_a_read_goes_to_one_server_only():
    galaxy = Galaxy()
    db = client(galaxy)

    db.read("feeds/ips", "1.2.3.4")

    assert len(galaxy.seen) == 1, "a read was fanned out"


def test_a_value_always_reads_from_the_same_server():
    """Servers drift apart while catching up, so a value moving between them
    could show its count going down."""
    for value in ("1.2.3.4", "evil.example", "deadbeef", "x"):
        chosen = set()
        for _ in range(5):
            galaxy = Galaxy()
            client(galaxy).read("feeds/ips", value)
            chosen.update(galaxy.hosts())
        assert len(chosen) == 1, f"{value} was read from {chosen}"


def test_reads_are_spread_across_the_servers():
    """Sticky per value, but not all on one server."""
    used = set()
    for n in range(60):
        galaxy = Galaxy()
        client(galaxy).read("feeds/ips", f"value-{n}")
        used.update(galaxy.hosts())
    assert used == set(NODES), f"only {used} were ever read from"


def test_a_read_falls_back_when_its_server_is_down():
    value = "1.2.3.4"
    first = routing.reader_for(NODES, value)

    galaxy = Galaxy(down={first})
    db = client(galaxy, max_retries=0)

    result = db.read("feeds/ips", value)
    assert result.count == 7
    assert first in galaxy.hosts(), "the owning server was not tried first"
    assert len(galaxy.hosts()) == 2, "it did not stop at the first that answered"


def test_the_same_server_twice_is_written_to_once():
    """Writing twice to one server would count twice."""
    galaxy = Galaxy()
    db = client(galaxy, urls=(NODES[0], NODES[0] + "/", NODES[1]))

    assert db.urls == (NODES[0], NODES[1])
    db.write("feeds/ips", "1.2.3.4")
    assert sorted(galaxy.hosts()) == sorted([NODES[0], NODES[1]])


# ---------------------------------------------------------------------------
# The asynchronous client does the same, and sends the copies together
# ---------------------------------------------------------------------------


def async_client(galaxy: Galaxy, urls=NODES, **kw):
    return sightingdb.AsyncSightingDB(
        urls=list(urls),
        apikey="k",
        use_env=False,
        use_file=False,
        transport=httpx.MockTransport(galaxy),
        **kw,
    )


@pytest.mark.asyncio
async def test_async_write_reaches_every_server_with_one_origin():
    galaxy = Galaxy()
    async with async_client(galaxy, node_id="my-client") as db:
        assert db.fans_out
        await db.write("feeds/ips", "1.2.3.4")

    assert sorted(galaxy.hosts()) == sorted(NODES)
    assert {r.headers[routing.ORIGIN_HEADER] for r in galaxy.seen} == {"my-client"}


@pytest.mark.asyncio
async def test_async_read_goes_to_one_server():
    galaxy = Galaxy()
    async with async_client(galaxy) as db:
        await db.read("feeds/ips", "1.2.3.4")
    assert len(galaxy.seen) == 1


@pytest.mark.asyncio
async def test_async_write_survives_a_server_being_down():
    galaxy = Galaxy(down={NODES[1]})
    async with async_client(galaxy, max_retries=0) as db:
        assert await db.write("feeds/ips", "1.2.3.4") == 1


@pytest.mark.asyncio
async def test_async_write_that_reached_nobody_raises():
    galaxy = Galaxy(down=set(NODES))
    async with async_client(galaxy, max_retries=0) as db:
        with pytest.raises(sightingdb.TransportError):
            await db.write("feeds/ips", "1.2.3.4")


@pytest.mark.asyncio
async def test_async_and_sync_pick_the_same_server_for_a_value():
    """Both clients must route alike, or moving between them moves the reads."""
    for value in ("1.2.3.4", "evil.example", "zzz"):
        sync_galaxy = Galaxy()
        client(sync_galaxy).read("feeds/ips", value)

        async_galaxy = Galaxy()
        async with async_client(async_galaxy) as db:
            await db.read("feeds/ips", value)

        assert sync_galaxy.hosts() == async_galaxy.hosts(), value
