"""The synchronous client."""

from __future__ import annotations

import itertools
import time
from types import TracebackType
from typing import Any, Iterable, Iterator, Sequence

import httpx

from . import _protocol as proto
from .__version__ import __version__
from .config import Config
from .errors import BulkWriteError, NotFoundError, SightingDBError
from .models import Attribute, BulkWriteResult, ReadResult, ServerInfo, Sighting

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
        host: str | None = None,
        port: int | None = None,
        ssl: bool | None = None,
        apikey: str | None = None,
        verify: bool | str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        config: Config | None = None,
        config_file: str | None = None,
        use_env: bool = True,
        use_file: bool = True,
        http_client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or Config.resolve(
            url=url,
            host=host,
            port=port,
            ssl=ssl,
            apikey=apikey,
            verify=verify,
            timeout=timeout,
            max_retries=max_retries,
            config_file=config_file,
            use_env=use_env,
            use_file=use_file,
        )
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(
            base_url=self.config.url,
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

    def close(self) -> None:
        """Close the connection pool, unless the ``httpx.Client`` is yours."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> SightingDB:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
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
        max_retries = self.config.max_retries
        for attempt in itertools.count():
            try:
                response = self._http.request(
                    request.method,
                    request.path,
                    params=request.params or None,
                    json=request.json,
                )
            except httpx.HTTPError as exc:
                if attempt < max_retries and proto.retry_after_exception(
                    exc, idempotent=request.idempotent
                ):
                    time.sleep(proto.backoff(attempt))
                    continue
                raise proto.transport_error(
                    exc, request, f"{self.config.url}{request.path}"
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
