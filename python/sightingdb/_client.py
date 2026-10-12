"""The synchronous client."""

from __future__ import annotations

import itertools
import time
from datetime import datetime
from types import TracebackType
from typing import Any, Iterable, Iterator, Sequence

import httpx

from . import _protocol as proto
from . import _routing as routing
from .__version__ import __version__
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

__all__ = ["SightingDB", "WriteBatch"]

USER_AGENT = f"sightingdb-python/{__version__}"


def _chunks(items: Sequence[Sighting], size: int | None) -> Iterator[Sequence[Sighting]]:
    if not size or len(items) <= size:
        yield items
        return
    for start in range(0, len(items), size):
        yield items[start : start + size]


class SightingDB:
    """A connection to a SightingDB server.

    The client holds a connection pool, so make one and keep it: building a
    client per call re-runs the TLS handshake every time. It is a context
    manager, and safe to share between threads.

        >>> with SightingDB("https://localhost:9999", apikey="changeme") as db:
        ...     db.write("feeds/ips", "127.0.0.1")
        ...     db.read("feeds/ips", "127.0.0.1").count
        1

    Args:
        url: Base URL of the server, e.g. ``https://localhost:9999``. Falls
            back to ``SIGHTINGDB_URL``, then the config file, then localhost.
        host: Hostname, as an alternative to ``url``. Combined with ``port``
            and ``ssl``.
        port: Port to combine with ``host``.
        ssl: Whether to speak HTTPS. Only meaningful with ``host``.
        apikey: The API key, sent in the ``Authorization`` header.
        verify: ``True`` to verify TLS certificates, ``False`` to skip
            verification, or a path to a CA bundle — SightingDB's own
            ``cert.pem``, for the self-signed certificate it generates.
        timeout: Seconds before giving up on a request.
        max_retries: Attempts to retry a request that failed in a way that
            could succeed on a retry. Writes are only retried when the
            connection was never established, so a sighting is never counted
            twice.
        config: A ready-made :class:`~sightingdb.config.Config`, instead of the
            arguments above.
        config_file: Path to a TOML config file. Defaults to
            ``$SIGHTINGDB_CONFIG``, then ``~/.sightingdb/client.toml``.
        use_env: Read settings from the environment. Turn off to isolate the
            client from it entirely.
        use_file: Read settings from the config file.
        http_client: An ``httpx.Client`` to use instead of the one this client
            would build. Yours is not closed by :meth:`close`.
        transport: An ``httpx`` transport to send through — a proxy, a Unix
            socket, or a mock in tests — keeping the rest of this client's
            setup.
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
        http_client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
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
        self._http = http_client or httpx.Client(
            # No base_url: with more than one server the URL is chosen per
            # request, so it is given in full each time. With one server this
            # is the same thing said differently.
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

    @property
    def urls(self) -> tuple[str, ...]:
        """Every server this client talks to."""
        return self.config.urls

    @property
    def fans_out(self) -> bool:
        """Whether this client is spreading writes itself.

        ``False`` when it points at one address — which may well be a load
        balancer doing the same job on the server side. Nothing about the API
        differs either way; this is here to be asked, not to be acted on.
        """
        return len(self.config.urls) > 1

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        """Close the connection pool, unless the ``httpx.Client`` is yours."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> SightingDB:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        if self.fans_out:
            return f"<SightingDB {len(self.config.urls)} servers: " + ", ".join(
                self.config.urls
            ) + ">"
        return f"<SightingDB {self.config.url}>"

    # -- writing ---------------------------------------------------------

    def write(
        self,
        namespace: str,
        value: str,
        *,
        timestamp: Any = None,
        ttl: Any = None,
    ) -> int:
        """Record one sighting, and return how many times it has been seen.

        Args:
            namespace: Where to record it, e.g. ``"feeds/misp/ips"``. Leading
                and trailing slashes do not matter.
            value: The thing observed.
            timestamp: When it was observed — Unix seconds or a ``datetime``.
                Defaults to now, as decided by the server.
            ttl: Expire the value this long after it was last seen — seconds or
                a ``timedelta``. ``None`` leaves any existing TTL alone, ``0``
                clears it.
        """
        return self._send(proto.write(namespace, value, timestamp=timestamp, ttl=ttl))

    def write_many(
        self,
        sightings: Iterable[Sighting | tuple[str, str] | dict[str, Any]],
        *,
        strict: bool = True,
        chunk_size: int | None = None,
    ) -> BulkWriteResult:
        """Record many sightings in one request.

        Args:
            sightings: :class:`~sightingdb.models.Sighting` objects,
                ``(namespace, value)`` pairs, or dictionaries of the same
                fields.
            strict: Raise :class:`~sightingdb.errors.BulkWriteError` if the
                server rejected any of them. With ``strict=False`` the
                rejections are reported in the result instead.
            chunk_size: Split into several requests of at most this many
                sightings. The results are summed into one.

        Returns:
            What the server recorded, and what it refused.
        """
        items = proto.coerce_sightings(sightings)
        if not items:
            return BulkWriteResult(written=0)

        result = BulkWriteResult(written=0)
        for chunk in _chunks(items, chunk_size):
            result = result + self._send(proto.write_many(chunk))
        if strict and result.errors:
            raise BulkWriteError(result)
        return result

    def batch(self, *, chunk_size: int | None = None, strict: bool = True) -> WriteBatch:
        """Collect sightings and send them together.

        The batch is sent when the ``with`` block ends, or every
        ``chunk_size`` sightings if you set it — which is how to stream a large
        feed without holding it all in memory.

            >>> with db.batch(chunk_size=1000) as batch:
            ...     for value in feed:
            ...         batch.add("feeds/misp/ips", value)
        """
        return WriteBatch(self, chunk_size=chunk_size, strict=strict)

    # -- reading ---------------------------------------------------------

    def read(
        self,
        namespace: str,
        value: str,
        *,
        stats: bool = False,
        shadow: bool = True,
    ) -> Attribute:
        """Read one value.

        Args:
            namespace: The namespace to look in.
            value: The value to look up.
            stats: Also return the hourly histogram of when it was seen.
            shadow: Whether to let the server record that this value was
                searched for. Pass ``False`` to look without leaving a trace.

        Returns:
            What the server knows about the value.

        Raises:
            NotFoundError: The namespace or the value does not exist. Use
                :meth:`read_many` to ask about several values without a miss
                being an error.
        """
        return self._send(proto.read(namespace, value, stats=stats, shadow=shadow))

    def read_many(
        self,
        sightings: Iterable[Sighting | tuple[str, str] | dict[str, Any]],
        *,
        stats: bool = False,
        chunk_size: int | None = None,
    ) -> list[ReadResult]:
        """Read many values in one request.

        Returns:
            One result per value asked about, in order: an
            :class:`~sightingdb.models.Attribute` for a value the server has,
            a :class:`~sightingdb.models.Miss` for one it does not. Both carry
            ``found``, so ``[r for r in results if r.found]`` is the usual way
            to read this.
        """
        items = proto.coerce_sightings(sightings)
        if not items:
            return []
        results: list[ReadResult] = []
        for chunk in _chunks(items, chunk_size):
            results.extend(self._send(proto.read_many(chunk, stats=stats)))
        return results

    def list_values(self, namespace: str, *, shadow: bool = True) -> list[Attribute]:
        """Every value in a namespace.

        Raises:
            NotFoundError: No such namespace.
        """
        return self._send(proto.list_values(namespace, shadow=shadow))

    def exists(self, namespace: str, value: str, *, shadow: bool = True) -> bool:
        """Whether a value has ever been seen in a namespace."""
        results = self.read_many([Sighting(namespace, value, noshadow=not shadow)])
        return bool(results and results[0].found)

    # -- what was written lately -----------------------------------------

    def latest(
        self,
        limit: int = 100,
        *,
        since: int | float | datetime | None = None,
        namespace: str | None = None,
    ) -> list[RecentWrite]:
        """The values written most recently, newest first.

        Only writes in namespaces this API key may read are listed: the server
        keeps one record and filters it per key, so two keys asking the same
        question get different answers. A value written again is listed once,
        at its latest write.

        Args:
            limit: How many. The server returns at most 1000.
            since: Only writes that arrived at or after this time — Unix
                seconds or a :class:`~datetime.datetime`.
            namespace: Only this namespace and those below it.

        Pointed at several servers, every one is asked and the answers merged,
        so writes one of them missed while it was down still show.

        Needs SightingDB 0.7.8 or later; an older server raises
        :class:`~sightingdb.ProtocolError`.
        """
        request = proto.latest(limit=limit, since=since, namespace=namespace)
        if not self.fans_out:
            return self._send(request)
        return proto.merge_latest(self._read_from_all(request), limit)

    def follow(
        self,
        *,
        since: int | float | datetime | None = None,
        namespace: str | None = None,
        interval: float = 5.0,
    ) -> Iterator[RecentWrite]:
        """Yield writes as they arrive, oldest first, until you stop iterating.

        Polls :meth:`latest` every ``interval`` seconds. Without ``since``,
        only what arrives from here on is yielded — "here" being what the
        server already holds at the first poll, so the clock on this machine
        does not matter. Pass a time to start further back. A value written
        again later is yielded again.

        At most 1000 writes are fetched per poll, so a poll that falls further
        behind than that skips the oldest of them; poll more often, or narrow
        with ``namespace``, if the feed is that busy.

            >>> for write in db.follow(namespace="feeds"):
            ...     print(write.written_at, write.namespace, write.value)
        """
        follower = proto.Follower(since)
        while True:
            answer = self.latest(
                proto.LATEST_MAX, since=follower.cursor, namespace=namespace
            )
            yield from follower.fresh(answer)
            time.sleep(interval)

    # -- namespaces ------------------------------------------------------

    def delete(self, namespace: str, *, missing_ok: bool = False) -> None:
        """Delete a namespace and everything in it.

        Args:
            namespace: The namespace to delete.
            missing_ok: Return quietly if it does not exist, instead of raising.

        Raises:
            NotFoundError: No such namespace, unless ``missing_ok``.
        """
        try:
            self._send(proto.delete(namespace))
        except NotFoundError:
            if not missing_ok:
                raise

    def info(self) -> ServerInfo:
        """What the server reports about itself."""
        return self._send(proto.info())

    def ping(self) -> bool:
        """Whether the server is reachable and answering."""
        try:
            self.info()
        except SightingDBError:
            return False
        return True

    # -- transport -------------------------------------------------------

    def _send(self, request: proto.Request) -> Any:
        if not self.fans_out:
            return self._send_to(self.config.urls[0], request)
        if request.idempotent:
            return self._read_from_one(request)
        return self._write_to_all(request)

    def _write_to_all(self, request: proto.Request) -> Any:
        """Send a write to every server, and answer with the first that took it.

        Every server, because a write that reached only one is one the others
        have to catch up — and catching up is repair rather than the normal
        path. One server refusing is not a lost sighting though: the one that
        took it is what the others converge from. What must never happen is
        reporting success when *no* server took it.
        """
        answered: Any = None
        first_error: Exception | None = None
        reached = 0

        for url in self.config.urls:
            try:
                result = self._send_to(url, request, origin=self._origin())
            except Exception as exc:  # noqa: BLE001 - re-raised below if total
                if first_error is None:
                    first_error = exc
                continue
            reached += 1
            if answered is None:
                # The first server's count, which is that server's own view —
                # the only thing any single server can honestly report.
                answered = result

        if reached == 0:
            assert first_error is not None
            raise first_error
        return answered

    def _read_from_one(self, request: proto.Request) -> Any:
        """Read from the server that owns this value, falling back if it is down.

        Owned by hash, so a value always comes from the same place: servers
        drift apart transiently while they catch up with each other, and two
        consecutive reads served by different ones can otherwise show a count
        going down.
        """
        key = request.value or request.namespace
        order = routing.order_for(self.config.urls, key)

        first_error: Exception | None = None
        for url in order:
            try:
                return self._send_to(url, request)
            except TransportError as exc:
                # Unreachable: try the next. Anything else — a refusal, a
                # missing value — is this server's real answer and is not
                # improved by asking someone else.
                if first_error is None:
                    first_error = exc
                continue
        assert first_error is not None
        raise first_error

    def _read_from_all(self, request: proto.Request) -> list[Any]:
        """Ask every server, for a question no single one can answer alone.

        A server that cannot be reached is left out rather than failing the
        call; if none can be, the first failure is raised.
        """
        answers: list[Any] = []
        first_error: Exception | None = None
        for url in self.config.urls:
            try:
                answers.append(self._send_to(url, request))
            except TransportError as exc:
                if first_error is None:
                    first_error = exc
        if not answers:
            assert first_error is not None
            raise first_error
        return answers

    def _origin(self) -> str:
        return self.config.node_id or routing.default_node_id()

    def _send_to(
        self, url: str, request: proto.Request, *, origin: str | None = None
    ) -> Any:
        max_retries = self.config.max_retries
        headers: dict[str, str] | None = None
        if origin is not None:
            # Every server counts this write under one name. Without it each
            # would count it under its own, and the counts would add together
            # when the servers synced — see sightingdb._routing.
            headers = {
                routing.ORIGIN_HEADER: origin,
                routing.HOPS_HEADER: str(routing.DEFAULT_HOPS),
            }

        for attempt in itertools.count():
            try:
                response = self._http.request(
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
                    time.sleep(proto.backoff(attempt))
                    continue
                raise proto.transport_error(
                    exc, request, f"{url}{request.path}"
                ) from exc

            if attempt < max_retries and proto.retry_after_status(
                response.status_code, idempotent=request.idempotent
            ):
                time.sleep(proto.backoff(attempt, response))
                continue
            return proto.parse(response, request)
        raise AssertionError("unreachable")  # pragma: no cover


class WriteBatch:
    """Sightings queued up to be written together. See :meth:`SightingDB.batch`."""

    def __init__(
        self,
        client: SightingDB,
        *,
        chunk_size: int | None = None,
        strict: bool = True,
    ) -> None:
        self._client = client
        self._chunk_size = chunk_size
        self._strict = strict
        self._pending: list[Sighting] = []
        self.result = BulkWriteResult(written=0)

    def add(
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
            self.flush()

    def extend(
        self, sightings: Iterable[Sighting | tuple[str, str] | dict[str, Any]]
    ) -> None:
        """Queue many sightings."""
        for sighting in proto.coerce_sightings(sightings):
            self.add(
                sighting.namespace,
                sighting.value,
                timestamp=sighting.timestamp,
                ttl=sighting.ttl,
                noshadow=sighting.noshadow,
            )

    def flush(self) -> BulkWriteResult:
        """Send whatever is queued, and return the total sent so far."""
        if self._pending:
            pending, self._pending = self._pending, []
            self.result = self.result + self._client.write_many(
                pending, strict=self._strict
            )
        return self.result

    def __len__(self) -> int:
        return len(self._pending)

    def __enter__(self) -> WriteBatch:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Only send on a clean exit: an exception mid-loop means the caller
        # never finished deciding what to record.
        if exc_type is None:
            self.flush()
