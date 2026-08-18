#!/usr/bin/env python3
"""Every part of the SightingDB API, against a local server.

    SIGHTINGDB_APIKEY=changeme SIGHTINGDB_VERIFY=false ./samples/everything.py

Settings come from the arguments below, then SIGHTINGDB_*, then
~/.sightingdb/client.toml. Everything here is written under _tests/ and
deleted again at the end.
"""

import os
import sys

import sightingdb

NAMESPACE = "_tests/samples/ips"


def main() -> int:
    # verify=False skips TLS verification, which is what the self-signed
    # certificate SightingDB generates needs. In production, point verify= at
    # the server's cert.pem instead.
    db = sightingdb.SightingDB(
        os.environ.get("SIGHTINGDB_URL", "https://localhost:9999"),
        apikey=os.environ.get("SIGHTINGDB_APIKEY", "changeme"),
        verify=False,
    )

    with db:
        info = db.info()
        print(f"Talking to {info.implementation} {info.version}")

        # One value at a time. The return value is its new count.
        print("count after one write:", db.write(NAMESPACE, "127.0.0.1"))
        print("count after two:      ", db.write(NAMESPACE, "127.0.0.1"))

        # A sighting that happened in the past, and one that expires in an hour.
        db.write(NAMESPACE, "192.168.0.2", timestamp=1587364370)
        db.write(NAMESPACE, "10.0.0.1", ttl=3600)

        # Many values in one request. Anything the server refuses raises
        # BulkWriteError, naming the items that did not land.
        result = db.write_many(
            [
                (NAMESPACE, "8.8.8.8"),
                sightingdb.Sighting(NAMESPACE, "1.1.1.1", ttl=86400),
                {"namespace": "_tests/samples/domains", "value": "example.com"},
            ]
        )
        print(f"bulk write: {result.written} recorded")

        # Or collect as you go and let the batch send them.
        with db.batch(chunk_size=1000) as batch:
            for last in range(10):
                batch.add(NAMESPACE, f"203.0.113.{last}")
        print(f"batch:      {batch.result.written} recorded")

        # Reading one value. A value that was never seen raises NotFoundError.
        attribute = db.read(NAMESPACE, "127.0.0.1")
        print(
            f"{attribute.value}: seen {attribute.count} times, "
            f"first {attribute.first_seen_at:%Y-%m-%d %H:%M}, "
            f"in {attribute.consensus} namespace(s)"
        )

        try:
            db.read(NAMESPACE, "never.seen.this")
        except sightingdb.NotFoundError as exc:
            print(f"as expected: {exc.value!r} is not in {exc.namespace}")

        # Reading many at once, where a miss is an answer rather than an error.
        # noshadow=True looks without recording that we looked.
        for result in db.read_many(
            [
                sightingdb.Sighting(NAMESPACE, "127.0.0.1", noshadow=True),
                sightingdb.Sighting(NAMESPACE, "192.168.0.2", noshadow=True),
                sightingdb.Sighting(NAMESPACE, "never.seen.this", noshadow=True),
                sightingdb.Sighting("_tests/no/such/namespace", "x", noshadow=True),
            ]
        ):
            if result.found:
                print(f"  {result.value}: {result.count}")
            else:
                print(f"  {result.value}: {result.error}")

        # With the hourly histogram of when the value was seen.
        stats = db.read(NAMESPACE, "127.0.0.1", stats=True, shadow=False)
        for hour, count in (stats.stats_by_hour or {}).items():
            print(f"  {hour:%Y-%m-%d %H:00}: {count}")

        # Everything in a namespace.
        print("namespace holds:", [a.value for a in db.list_values(NAMESPACE)])

        # And clean up after ourselves.
        db.delete(NAMESPACE, missing_ok=True)
        db.delete("_tests/samples/domains", missing_ok=True)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except sightingdb.TransportError as exc:
        sys.exit(f"Cannot reach SightingDB: {exc}")
    except sightingdb.APIError as exc:
        sys.exit(f"SightingDB refused the request: {exc}")
