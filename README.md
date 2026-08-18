SightingDB Client Tools and Libraries
=====================================

Client libraries for [SightingDB](https://github.com/stricaud/sightingdb), a
database for sightings: how many times a value was seen, when it was first
seen, when it was last seen, and how many namespaces hold it.

* [python/](python/) — the Python client, for synchronous and asyncio code.
  `pip install sightingdb-client`, then see [python/README.md](python/README.md).

Configuration
-------------

Each client reads its settings from its own arguments first, then from
`SIGHTINGDB_*` environment variables, then from `~/.sightingdb/client.toml`:

```toml
[client]
url = "https://localhost:9999"
apikey = "changeme"
# true, false, or a path to the CA bundle to trust
verify = false
```

`client.toml.example` in this directory is that file, ready to copy.
