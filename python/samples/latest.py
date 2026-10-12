#!/usr/bin/env python3
"""What was written lately, and following it as it happens.

    SIGHTINGDB_APIKEY=changeme ./samples/latest.py           # list, then follow
    SIGHTINGDB_APIKEY=changeme ./samples/latest.py --once    # list and stop

The server keeps one record of its most recent writes and filters it by the
API key asking: every entry names the namespace the value went to, and a key is
shown only those it may read. Run this with a key scoped to one subtree
(``r:feeds``, say) and you see that subtree's writes and nothing else.

Needs SightingDB 0.7.8 or later.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import sightingdb

NAMESPACE = "tests/samples/latest"


def main() -> int:
    db = sightingdb.SightingDB(
        os.environ.get("SIGHTINGDB_URL", "https://localhost:9999"),
        apikey=os.environ.get("SIGHTINGDB_APIKEY", "changeme"),
        verify=False,
    )

    with db:
        # Something to see.
        db.write_many([(NAMESPACE, "198.51.100.7"), (NAMESPACE, "evil.example")])
        db.write(NAMESPACE, "198.51.100.7")  # written again: moves to the front

        # The ten most recent writes this key may read, newest first. A value
        # written several times is listed once, at its latest write.
        print("Latest writes:")
        for write in db.latest(10):
            print(f"  {write.written_at:%H:%M:%S}  {write.namespace}  {write.value}")

        # Narrowed to a subtree, and to the last five minutes. `since` takes a
        # datetime or Unix seconds.
        recent = db.latest(
            100,
            namespace=NAMESPACE,
            since=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        print(f"{len(recent)} value(s) written to {NAMESPACE} in the last five minutes")

        if "--once" in sys.argv:
            db.delete(NAMESPACE, missing_ok=True)
            return 0

        # Follow: yields each write as it arrives, oldest first, polling every
        # few seconds. Only what arrives from now on — pass since= to start
        # earlier. Write something from another terminal to see it appear:
        #
        #     curl -k -H 'Authorization: changeme' \
        #       'https://localhost:9999/w/tests/samples/latest?val=203.0.113.9'
        print("Following new writes (Ctrl-C to stop)...")
        try:
            for write in db.follow(interval=2):
                print(f"  new: {write.namespace}  {write.value}")
        except KeyboardInterrupt:
            pass
        finally:
            db.delete(NAMESPACE, missing_ok=True)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except sightingdb.ProtocolError as exc:
        sys.exit(f"This server has no /_api/latest (it needs SightingDB 0.7.8): {exc}")
    except sightingdb.TransportError as exc:
        sys.exit(f"Cannot reach SightingDB: {exc}")
    except sightingdb.APIError as exc:
        sys.exit(f"SightingDB refused the request: {exc}")
