"""The asynchronous client: the same API as :class:`~sightingdb._client.SightingDB`, awaited."""

from __future__ import annotations

import asyncio
import itertools
from types import TracebackType
from datetime import datetime
from typing import Any, AsyncIterator, Iterable

import httpx

from . import _protocol as proto
from . import _routing as routing
from ._client import USER_AGENT, _chunks
from .config import Config
from .errors import BulkWriteError, NotFoundError, SightingDBError, TransportError
from .models import (
    Attribute,
    BulkWriteResult,
    ReadResult,
    RecentWrite,
    ServerInfo,
    Sighting,
)

__all__ = ["AsyncSightingDB", "AsyncWriteBatch"]


class AsyncSightingDB:
    """A connection to a SightingDB server, for asyncio.

    Every method matches :class:`~sightingdb.SightingDB` — same arguments, same
    return types — but is awaited, and requests issued together with
    ``asyncio.gather`` share one connection pool.

        >>> async with AsyncSightingDB(apikey="changeme") as db:
        ...     await db.write("feeds/ips", "127.0.0.1")
        ...     found = await db.read_many([("feeds/ips", "127.0.0.1")])

    Args:
        http_client: An ``httpx.AsyncClient`` to use instead of the one this
            client would build. Yours is not closed by :meth:`aclose`.

    See :class:`~sightingdb.SightingDB` for the rest.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        urls: Iterable[str] | None = None,
        host: str | None = None,
        port: int | None = None,
        ssl: bool | None = None,
        apikey: str | None = None,
        verify: bool | str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        node_id: str | None = None,
        config: Config | None = None,
        config_file: str | None = None,
        use_env: bool = True,
        use_file: bool = True,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or Config.resolve(
            url=url,
            urls=tuple(urls) if urls is not None else None,
            host=host,
            port=port,
            ssl=ssl,
            apikey=apikey,
            verify=verify,
            timeout=timeout,
            max_retries=max_retries,
            node_id=node_id,
            config_file=config_file,
            use_env=use_env,
            use_file=use_file,
        )
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(
            # No base_url: with more than one server the URL is chosen per
            # request. With one server this is the same thing said differently.
            headers=self._headers(),
            verify=self.config.verify,
            timeout=self.config.timeout,
            transport=transport,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"user-agent": USER_AGENT, "accept": "application/json"}
        if self.config.apikey is not None:
            headers["authorization"] = self.config.apikey
        return headers

    # -- lifecycle -------------------------------------------------------

    async def aclose(self) -> None:
        """Close the connection pool, unless the ``httpx.AsyncClient`` is yours."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncSightingDB:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        if self.fans_out:
            return (
                f"<AsyncSightingDB {len(self.config.urls)} servers: "
                + ", ".join(self.config.urls)
                + ">"
            )
        return f"<AsyncSightingDB {self.config.url}>"

    # -- writing ---------------------------------------------------------

    async def write(
        self,
        namespace: str,
        value: str,
        *,
        timestamp: Any = None,
        ttl: Any = None,
    ) -> int:
        """Record one sighting, and return how many times it has been seen."""
        return await self._send(
            proto.write(namespace, value, timestamp=timestamp, ttl=ttl)
        )

    async def write_many(
        self,
        sightings: Iterable[Sighting | tuple[str, str] | dict[str, Any]],
        *,
        strict: bool = True,
        chunk_size: int | None = None,
    ) -> BulkWriteResult:
        """Record many sightings in one request."""
        items = proto.coerce_sightings(sightings)
        if not items:
            return BulkWriteResult(written=0)

        result = BulkWriteResult(written=0)
        for chunk in _chunks(items, chunk_size):
            result = result + await self._send(proto.write_many(chunk))
        if strict and result.errors:
            raise BulkWriteError(result)
        return result

    def batch(
        self, *, chunk_size: int | None = None, strict: bool = True
    ) -> AsyncWriteBatch:
        """Collect sightings and send them together.

            >>> async with db.batch(chunk_size=1000) as batch:
            ...     async for value in feed:
            ...         await batch.add("feeds/misp/ips", value)
        """
        return AsyncWriteBatch(self, chunk_size=chunk_size, strict=strict)

    # -- reading ---------------------------------------------------------

    async def read(
        self,
        namespace: str,
        value: str,
        *,
        stats: bool = False,
        shadow: bool = True,
    ) -> Attribute:
        """Read one value. Raises :class:`~sightingdb.errors.NotFoundError` on a miss."""
        return await self._send(
            proto.read(namespace, value, stats=stats, shadow=shadow)
        )

    async def read_many(
        self,
        sightings: Iterable[Sighting | tuple[str, str] | dict[str, Any]],
        *,
        stats: bool = False,
        chunk_size: int | None = None,
    ) -> list[ReadResult]:
        """Read many values in one request, a miss being a result rather than an error."""
        items = proto.coerce_sightings(sightings)
        if not items:
            return []
        results: list[ReadResult] = []
        for chunk in _chunks(items, chunk_size):
            results.extend(await self._send(proto.read_many(chunk, stats=stats)))
        return results

    async def list_values(
        self, namespace: str, *, shadow: bool = True
    ) -> list[Attribute]:
        """Every value in a namespace."""
        return await self._send(proto.list_values(namespace, shadow=shadow))

    async def exists(self, namespace: str, value: str, *, shadow: bool = True) -> bool:
        """Whether a value has ever been seen in a namespace."""
        results = await self.read_many([Sighting(namespace, value, noshadow=not shadow)])
        return bool(results and results[0].found)

    # -- what was written lately -----------------------------------------

    async def latest(
        self,
        limit: int = 100,
        *,
        since: int | float | datetime | None = None,
        namespace: str | None = None,
    ) -> list[RecentWrite]:
        """The values written most recently, newest first, as far as this key may see.

        See :meth:`sightingdb.SightingDB.latest`. Pointed at several servers,
        they are all asked at once and the answers merged.
        """
        request = proto.latest(limit=limit, since=since, namespace=namespace)
        if not self.fans_out:
            return await self._send(request)
        return proto.merge_latest(await self._read_from_all(request), limit)

    async def follow(
        self,
        *,
        since: int | float | datetime | None = None,
        namespace: str | None = None,
        interval: float = 5.0,
    ) -> AsyncIterator[RecentWrite]:
        """Yield writes as they arrive, oldest first. See :meth:`sightingdb.SightingDB.follow`.

            >>> async for write in db.follow(namespace="feeds"):
            ...     print(write.written_at, write.namespace, write.value)
        """
        follower = proto.Follower(since)
        while True:
            answer = await self.latest(
                proto.LATEST_MAX, since=follower.cursor, namespace=namespace
            )
            for entry in follower.fresh(answer):
                yield entry
            await asyncio.sleep(interval)

    # -- namespaces ------------------------------------------------------

    async def delete(self, namespace: str, *, missing_ok: bool = False) -> None:
        """Delete a namespace and everything in it."""
        try:
            await self._send(proto.delete(namespace))
        except NotFoundError:
            if not missing_ok:
                raise

    async def info(self) -> ServerInfo:
        """What the server reports about itself."""
        return await self._send(proto.info())

    async def ping(self) -> bool:
        """Whether the server is reachable and answering."""
        try:
            await self.info()
        except SightingDBError:
            return False
        return True

    # -- transport -------------------------------------------------------

    @property
    def urls(self) -> tuple[str, ...]:
        """Every server this client talks to."""
        return self.config.urls

    @property
    def fans_out(self) -> bool:
        """Whether this client is spreading writes itself.

        ``False`` when it points at one address — which may well be a load
        balancer doing the same job on the server side.
        """
        return len(self.config.urls) > 1

    async def _send(self, request: proto.Request) -> Any:
        if not self.fans_out:
            return await self._send_to(self.config.urls[0], request)
        if request.idempotent:
            return await self._read_from_one(request)
        return await self._write_to_all(request)

    async def _write_to_all(self, request: proto.Request) -> Any:
        """Send a write to every server, and answer with the first that took it.

        Sent together rather than one after another, which is most of the point
        of the asynchronous client: the slowest server sets the pace instead of
        the sum of them all.

        See :mod:`sightingdb._routing` for why they all carry one origin.
        """
        origin = self.config.node_id or routing.default_node_id()
        results = await asyncio.gather(
            *(self._send_to(url, request, origin=origin) for url in self.config.urls),
            return_exceptions=True,
        )

        first_error: BaseException | None = None
        for result in results:
            if isinstance(result, BaseException):
                if first_error is None:
                    first_error = result
                continue
            # The first server that took it answers, with its own view of the
            # count — the only thing a single server can honestly report.
            return result

        assert first_error is not None
        raise first_error

    async def _read_from_one(self, request: proto.Request) -> Any:
        """Read from the server that owns this value, falling back if it is down.

        Tried in order rather than together: asking every server for the same
        value would multiply the load to pick one answer, and the point of
        picking by hash is that the same value always comes from the same place.
        """
        key = request.value or request.namespace
        order = routing.order_for(self.config.urls, key)

        first_error: Exception | None = None
        for url in order:
            try:
                return await self._send_to(url, request)
            except TransportError as exc:
                # Unreachable: try the next. Any other answer is this server's
                # real one and is not improved by asking someone else.
                if first_error is None:
                    first_error = exc
                continue
        assert first_error is not None
        raise first_error

    async def _read_from_all(self, request: proto.Request) -> list[Any]:
        """Ask every server at once, for a question no single one can answer alone.

        An unreachable server is left out; if none can be reached, the first
        failure is raised. Any other failure — a refused key, say — is raised
        as it is, since asking the others would not make it right.
        """
        results = await asyncio.gather(
            *(self._send_to(url, request) for url in self.config.urls),
            return_exceptions=True,
        )
        answers: list[Any] = []
        first_error: BaseException | None = None
        for result in results:
            if isinstance(result, TransportError):
                if first_error is None:
                    first_error = result
                continue
            if isinstance(result, BaseException):
                raise result
            answers.append(result)
        if not answers:
            assert first_error is not None
            raise first_error
        return answers

    async def _send_to(
        self, url: str, request: proto.Request, *, origin: str | None = None
    ) -> Any:
        max_retries = self.config.max_retries
        headers: dict[str, str] | None = None
        if origin is not None:
            headers = {
                routing.ORIGIN_HEADER: origin,
                routing.HOPS_HEADER: str(routing.DEFAULT_HOPS),
            }

        for attempt in itertools.count():
            try:
                response = await self._http.request(
                    request.method,
                    f"{url}{request.path}",
                    params=request.params or None,
                    json=request.json,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                if attempt < max_retries and proto.retry_after_exception(
                    exc, idempotent=request.idempotent
                ):
                    await asyncio.sleep(proto.backoff(attempt))
                    continue
                raise proto.transport_error(
                    exc, request, f"{url}{request.path}"
                ) from exc

            if attempt < max_retries and proto.retry_after_status(
                response.status_code, idempotent=request.idempotent
            ):
                await asyncio.sleep(proto.backoff(attempt, response))
                continue
            return proto.parse(response, request)
        raise AssertionError("unreachable")  # pragma: no cover


class AsyncWriteBatch:
    """Sightings queued up to be written together. See :meth:`AsyncSightingDB.batch`."""

    def __init__(
        self,
        client: AsyncSightingDB,
        *,
        chunk_size: int | None = None,
        strict: bool = True,
    ) -> None:
        self._client = client
        self._chunk_size = chunk_size
        self._strict = strict
        self._pending: list[Sighting] = []
        self.result = BulkWriteResult(written=0)

    async def add(
        self,
        namespace: str,
        value: str,
        *,
        timestamp: Any = None,
        ttl: Any = None,
        noshadow: bool = False,
    ) -> None:
        """Queue one sighting, sending the batch if it is now full."""
        self._pending.append(Sighting(namespace, value, timestamp, ttl, noshadow))
        if self._chunk_size and len(self._pending) >= self._chunk_size:
            await self.flush()

    async def extend(
        self, sightings: Iterable[Sighting | tuple[str, str] | dict[str, Any]]
    ) -> None:
        """Queue many sightings."""
        for sighting in proto.coerce_sightings(sightings):
            await self.add(
                sighting.namespace,
                sighting.value,
                timestamp=sighting.timestamp,
                ttl=sighting.ttl,
                noshadow=sighting.noshadow,
            )

    async def flush(self) -> BulkWriteResult:
        """Send whatever is queued, and return the total sent so far."""
        if self._pending:
            pending, self._pending = self._pending, []
            self.result = self.result + await self._client.write_many(
                pending, strict=self._strict
            )
        return self.result

    def __len__(self) -> int:
        return len(self._pending)

    async def __aenter__(self) -> AsyncWriteBatch:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            await self.flush()
