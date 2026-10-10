"""Talking to several SightingDB servers at once.

A client can point at one server — a single node, or a load balancer that fans
out for it — and nothing here applies. Pointed at several, it does the fanning
out itself: writes go to every node, reads to one.

Two things make that correct rather than merely parallel, and both are easy to
get wrong.

**A fanned-out write must be attributed to its origin.** SightingDB counts
sightings per server, so that merging two copies of a value adds separate
contributions rather than replaying increments. One write sent to two nodes
would otherwise be counted by each under *its own* name — and when those nodes
sync, the two counts add together. Three writes become six. So the write
carries ``X-SightingDB-Origin`` naming this client, and every node counts it
under that one name; merging nodes that already agree then changes nothing.

The server honours that header only on a request that also carries
``X-SightingDB-Hops``, because for a single server the origin is part of the
forwarding protocol rather than the client API. A client fanning out *is*
forwarding, so it sends both.

**A value must always be read from the same node.** Nodes drift apart
transiently while they catch up with each other, so two consecutive reads
served by different nodes can show a count going *down*. Picking the node by
hashing the value keeps each value coming from one place, and moves the
fewest values when the node list changes.
"""

from __future__ import annotations

import hashlib
import socket
from typing import Sequence

#: Names the server a fanned-out write is counted for.
ORIGIN_HEADER = "x-sightingdb-origin"

#: How many more hops a forwarded request may take. Present because the server
#: honours the origin only on a forwarded request; the value just has to leave
#: room for the nodes to pass it on if they are themselves routers.
HOPS_HEADER = "x-sightingdb-hops"

#: Hops to claim when fanning out. Enough for a node that is itself a router to
#: pass the write on, and small enough that a miswired galaxy stops.
DEFAULT_HOPS = 4


def default_node_id() -> str:
    """A name for this client, stable across its restarts.

    The hostname, because it is the one name already unique among machines and
    a node id has to be. Two clients on the same host sharing it is harmless
    and in fact right: they are one origin as far as counting goes, and each
    node still ends up with the same total.

    Cleaned to what SightingDB accepts as a node id — letters, digits, ``-``,
    ``_`` and ``.``, at most 64 characters — because an unusable one would be
    rejected by the server rather than ignored.
    """
    try:
        name = socket.gethostname().split(".")[0]
    except OSError:  # pragma: no cover - gethostname does not normally fail
        name = ""
    cleaned = "".join(c for c in name if c.isalnum() or c in "-_.")[:64]
    return cleaned or "client"


def reader_for(urls: Sequence[str], value: str) -> str:
    """Which node should serve a read of ``value``.

    Highest-random-weight: score every node against the value and take the
    highest. Each value therefore always comes from the same node while the
    list is unchanged, and adding or removing one moves only the values it
    wins rather than repointing everything.

    SHA-256 rather than something faster because appending a node's name to the
    value has to perturb the whole hash. With a weaker one the scores for
    different nodes become correlated and the choice stops being evenly spread
    — measured in SightingDB's own ``doc/sharding-experiment.py``, where a
    cheaper hash repointed half the values where an eighth was the minimum.
    """
    if len(urls) == 1:
        return urls[0]
    return max(urls, key=lambda url: _weigh(value, url))


def order_for(urls: Sequence[str], value: str) -> list[str]:
    """Every node, best first, for a read that may have to fall back.

    The same scoring as :func:`reader_for`, so the first choice agrees with it
    and the rest are a stable order to try when a node cannot be reached.
    """
    return sorted(urls, key=lambda url: _weigh(value, url), reverse=True)


def _weigh(value: str, url: str) -> int:
    digest = hashlib.sha256(f"{value}:{url}".encode()).digest()
    return int.from_bytes(digest[:8], "big")
