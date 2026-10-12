# SightingDB Python Client

[SightingDB](https://github.com/stricaud/sightingdb) counts things: how many
times a value was seen, when it was first seen, when it was last seen, and how
many namespaces hold it. This library is its REST API as Python objects, for
both synchronous and asyncio code.

```
pip install sightingdb-client
```

The distribution is `sightingdb-client`; the module it installs is
`sightingdb`, so `import sightingdb` is what you write. Requires Python 3.10+
and talks to SightingDB 0.5 or later.

## Writing

```python
import sightingdb

with sightingdb.SightingDB("https://localhost:9999", apikey="changeme") as db:
    db.write("feeds/misp/ips", "127.0.0.1")          # returns the new count
    db.write("feeds/misp/ips", "127.0.0.1", ttl=86400)   # expires a day after last seen
    db.write("feeds/misp/ips", "10.0.0.1", timestamp=1587364370)  # seen in the past
```

Make the client once and keep it: it holds a connection pool, and a client per
call re-runs the TLS handshake every time. `timestamp` takes Unix seconds or a
`datetime`; `ttl` takes seconds or a `timedelta`.

Many values go in one request:

```python
result = db.write_many([
    ("feeds/misp/ips", "8.8.8.8"),
    sightingdb.Sighting("feeds/misp/ips", "1.1.1.1", ttl=86400),
    {"namespace": "feeds/misp/domains", "value": "example.com"},
])
print(result.written)
```

Anything the server refuses raises `BulkWriteError`, naming the items that did
not land — pass `strict=False` to get them in `result.errors` instead.

For a feed you are streaming rather than holding in memory, batch it:

```python
with db.batch(chunk_size=1000) as batch:      # one request per 1000 sightings
    for value in feed:
        batch.add("feeds/misp/ips", value)
print(batch.result.written)
```

## Reading

```python
attribute = db.read("feeds/misp/ips", "127.0.0.1")
attribute.count          # 2
attribute.consensus      # how many namespaces hold this value
attribute.first_seen     # 1566624658
attribute.first_seen_at  # datetime(2019, 8, 24, 5, 30, 58, tzinfo=timezone.utc)
attribute.expires_at     # None when the value has no TTL
```

`read` raises `NotFoundError` for a value that was never seen. When you are
enriching a list, ask for them together instead — there a miss is an answer,
not an error:

```python
for result in db.read_many([("feeds/misp/ips", v) for v in indicators]):
    if result.found:
        print(result.value, result.count, result.consensus)
    else:
        print(result.value, result.error)   # "Value not found" / "Path not found"
```

Reading is itself recorded, as a "shadow sighting" under `_shadow/`, so that you
can see how often a value was searched for. Pass `shadow=False` (or
`Sighting(..., noshadow=True)` in a bulk read) to look without leaving a trace.

Other reads:

```python
db.read("feeds/misp/ips", "127.0.0.1", stats=True).stats_by_hour  # hourly histogram
db.list_values("feeds/misp/ips")     # every value in a namespace
db.exists("feeds/misp/ips", "8.8.8.8")
db.delete("feeds/misp/ips")          # the whole namespace, with care
db.info()                            # what the server says it is
```

Namespaces are normalized for you: `feeds/ips`, `/feeds/ips` and `/feeds/ips/`
are the same namespace here. To the server they are not — a trailing slash is a
different namespace with its own counts.

## What was written lately

`latest()` lists the most recent writes, newest first, each with the namespace
it went to. The server keeps one record and filters it **by the API key
asking**: a key granted `r:feeds` sees writes under `feeds` and never learns
that anything else was written.

```python
for write in db.latest(10):
    print(write.written_at, write.namespace, write.value)

db.latest(100, namespace="feeds")                        # one subtree
db.latest(100, since=datetime.now(timezone.utc) - timedelta(hours=1))
```

A value written several times is listed once, at its latest write. `written`
is when the write *arrived* at the server, not the sighting's own timestamp.
At most 1000 come back per call, and the server remembers its last
`latest_writes` (10000 by default) in all, shared between every key — a key
that reads only a quiet namespace may see fewer than it asked for.

`follow()` turns that into a stream: it polls and yields each new write once,
oldest first, until you stop iterating.

```python
for write in db.follow(namespace="feeds", interval=5):
    enrich(write.namespace, write.value)
```

Without `since=` it starts from whatever the server already holds at the first
poll — the server's clock, not this machine's, so clock skew cannot make it
miss writes. A value written again later is yielded again. A poll that falls
more than 1000 writes behind skips the oldest of them, so poll more often (or
narrow with `namespace=`) for a busy feed.

Pointed at several servers, `latest()` asks all of them and merges the
answers, so writes one missed while it was down still show. Needs SightingDB
0.7.8 or later; an older server raises `ProtocolError`.
`samples/latest.py` shows all of it against a running server.

## asyncio

`AsyncSightingDB` is the same API, awaited:

```python
import asyncio, sightingdb

async def main():
    async with sightingdb.AsyncSightingDB(apikey="changeme") as db:
        async with db.batch(chunk_size=1000) as batch:
            for value in feed:
                await batch.add("feeds/misp/ips", value)

        results = await db.read_many([("feeds/misp/ips", v) for v in indicators])
        counts = await asyncio.gather(*(db.write("feeds/live", v) for v in values))

        async for write in db.follow(namespace="feeds"):
            print(write.namespace, write.value)

asyncio.run(main())
```

## Configuration

Settings are resolved per field, most specific first:

1. what you pass to the client,
2. the environment: `SIGHTINGDB_URL` (or `SIGHTINGDB_HOST`, `SIGHTINGDB_PORT`,
   `SIGHTINGDB_SSL`), `SIGHTINGDB_APIKEY`, `SIGHTINGDB_VERIFY`,
   `SIGHTINGDB_TIMEOUT`, `SIGHTINGDB_MAX_RETRIES`,
3. a TOML file — `$SIGHTINGDB_CONFIG`, else `~/.sightingdb/client.toml`.

```toml
# ~/.sightingdb/client.toml
[client]
url = "https://sightingdb.example.com:9999"
apikey = "changeme"
verify = "/etc/ssl/sightingdb-ca.pem"
timeout = 10.0
max_retries = 2
```

So a config file can hold the URL while the key comes from the environment,
without either repeating the other. `use_env=False` and `use_file=False` cut a
client off from both, which is what you want in tests.

### TLS

`verify` defaults to `True`. SightingDB generates a self-signed certificate on
first run, which nothing will verify, so either point `verify` at that
certificate — `verify="/Users/you/.sightingdb/ssl/cert.pem"` — or, knowing what
it costs, pass `verify=False`.

## Errors

Everything raised derives from `SightingDBError`.

| Exception | When |
|---|---|
| `ConfigurationError` | The settings do not make sense. |
| `TransportError`, `TimeoutError` | No answer arrived: DNS, TCP, TLS, timeout. |
| `ProtocolError` | The answer was not what the API promises — usually a wrong URL, or a server too old for an endpoint. |
| `BadRequestError` (400) | The server could not make sense of the request. |
| `AuthenticationError` (401) | No API key was sent. |
| `PermissionDeniedError` (403) | The key is unknown, or not granted this namespace. |
| `NotFoundError` (404) | No such namespace, or no such value. Carries `.namespace` and `.value`. |
| `ServerError` (5xx) | The server failed to handle the request. |
| `BulkWriteError` | A bulk write landed only in part. Carries the full `.result`. |

Requests that fail in a way a retry could fix are retried with backoff. Writes
are only retried when the connection was never established: SightingDB counts,
and a retried write that did land would count twice.

## Development

```
pip install -e '.[dev]'
pytest                     # unit tests, no server needed

SIGHTINGDB_TEST_URL=https://localhost:9999 \
SIGHTINGDB_TEST_APIKEY=changeme \
SIGHTINGDB_TEST_VERIFY=false \
pytest -m live             # against a real server
```

`samples/everything.py` walks through the whole API, and
`samples/async_feed.py` shows the asyncio client ingesting and enriching a feed.

CI runs the unit tests on 3.10 through 3.14, builds the distribution, and runs
the live tests against a real SightingDB downloaded from the server's own
releases — because every bug this client has had was a disagreement with the
server that mocks could not have caught.

### Releasing

Tag it:

```
git tag python-v1.0.1 && git push origin python-v1.0.1
```

`.github/workflows/release.yml` does the rest: if `__version__.py` disagrees
with the tag it bumps, commits and moves the tag onto that commit, then tests,
builds, publishes to PyPI over Trusted Publishing, and cuts a GitHub Release
with the sdist and wheel attached. `python-v1.0.1` and `v1.0.1` are both
accepted, and `1.0.1rc1` publishes as a prerelease.

## Upgrading from 0.0.x

The 0.0.x API is gone. It predates several versions of the server, reported
failures as successes, and its `auth` object called endpoints that no longer
exist — API keys now live in the server's `acl.toml`.

| 0.0.x | 1.0 |
|---|---|
| `sightingdb.connection(host=..., apikey=...)` | `sightingdb.SightingDB(url, apikey=...)` |
| `writer.add(...)` then `writer.commit()` | `db.write_many([...])` or `with db.batch() as b: b.add(...)` |
| `writer.write_one(ns, v)` | `db.write(ns, v)` |
| `reader.add(...)` then `reader.fetch()` | `db.read_many([...])` |
| `reader.read_one(ns, v)` | `db.read(ns, v)` |
| `reader.read_one_with_stats(ns, v)` | `db.read(ns, v, stats=True)` |
| `delete(con).delete(ns)` | `db.delete(ns)` |
| `con.disable_ssl_warnings()` | nothing to disable; `httpx` does not warn |
| `sightingdb.auth(con)` | removed — keys are configured server-side |
