#!/usr/bin/env python3
"""Ingesting a feed and enriching it, with the asyncio client.

    SIGHTINGDB_APIKEY=changeme ./samples/async_feed.py

The point of the async client is here: one connection pool, many requests in
flight, and lookups that would otherwise be a serial round trip each.
"""

import asyncio
import os
import sys

import sightingdb

NAMESPACE = "tests/samples/feed"

FEED = [f"198.51.100.{n}" for n in range(50)]
TO_ENRICH = ["198.51.100.7", "198.51.100.42", "203.0.113.1"]


async def main() -> int:
    async with sightingdb.AsyncSightingDB(
        os.environ.get("SIGHTINGDB_URL", "https://localhost:9999"),
        apikey=os.environ.get("SIGHTINGDB_APIKEY", "changeme"),
        verify=False,
    ) as db:
        # Ingest: one request per thousand sightings, not one per sighting.
        async with db.batch(chunk_size=1000) as batch:
            for value in FEED:
                await batch.add(NAMESPACE, value, ttl=86400)
        print(f"ingested {batch.result.written} sightings")

        # Enrich: one bulk read answers all of them, misses included.
        results = await db.read_many(
            [sightingdb.Sighting(NAMESPACE, v, noshadow=True) for v in TO_ENRICH]
        )
        for value, result in zip(TO_ENRICH, results):
            if result.found:
                print(f"  {value}: seen {result.count}x, consensus {result.consensus}")
            else:
                print(f"  {value}: {result.error}")

        # When the calls are genuinely independent, run them at once.
        counts = await asyncio.gather(
            *(db.write(NAMESPACE, value) for value in TO_ENRICH[:2])
        )
        print(f"concurrent writes now count {counts}")

        await db.delete(NAMESPACE, missing_ok=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except sightingdb.SightingDBError as exc:
        sys.exit(f"SightingDB: {exc}")
