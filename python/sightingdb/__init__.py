"""The Python client for SightingDB.

SightingDB counts things: how many times a value was seen, when it was first
seen, when it was last seen, and how many namespaces hold it. This library is
that REST API as Python objects.

    import sightingdb

    with sightingdb.SightingDB("https://localhost:9999", apikey="changeme") as db:
        db.write("feeds/misp/ips", "127.0.0.1")
        attribute = db.read("feeds/misp/ips", "127.0.0.1")
        print(attribute.count, attribute.first_seen_at)

The same API is available for asyncio as
:class:`~sightingdb.AsyncSightingDB`, with every call awaited.
"""

from .__version__ import (
    __author__,
    __author_email__,
    __description__,
    __license__,
    __url__,
    __version__,
)
from ._async_client import AsyncSightingDB, AsyncWriteBatch
from ._client import SightingDB, WriteBatch
from .config import Config
from .errors import (
    APIError,
    AuthenticationError,
    BadRequestError,
    BulkWriteError,
    ConfigurationError,
    NotFoundError,
    NotImplementedError,
    PermissionDeniedError,
    ProtocolError,
    ServerError,
    SightingDBError,
    TimeoutError,
    TransportError,
)
from .models import (
    Attribute,
    BulkWriteResult,
    Miss,
    ReadResult,
    RecentWrite,
    ServerInfo,
    Sighting,
    WriteError,
)

__all__ = [
    # clients
    "SightingDB",
    "AsyncSightingDB",
    "WriteBatch",
    "AsyncWriteBatch",
    "Config",
    # models
    "Sighting",
    "Attribute",
    "Miss",
    "ReadResult",
    "BulkWriteResult",
    "WriteError",
    "ServerInfo",
    "RecentWrite",
    # errors
    "SightingDBError",
    "ConfigurationError",
    "TransportError",
    "TimeoutError",
    "ProtocolError",
    "APIError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "NotImplementedError",
    "ServerError",
    "BulkWriteError",
    # metadata
    "__version__",
]
