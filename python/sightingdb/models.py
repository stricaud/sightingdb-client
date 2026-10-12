"""The shapes SightingDB reads and writes.

These are plain frozen dataclasses. They are what the client returns, so that
callers work with attributes and types rather than with dictionaries whose keys
they have to remember.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Mapping

__all__ = [
    "Sighting",
    "Attribute",
    "Miss",
    "ReadResult",
    "WriteError",
    "BulkWriteResult",
    "ServerInfo",
    "RecentWrite",
]


def _to_epoch(when: int | float | datetime | None) -> int | None:
    """Normalise a timestamp to Unix seconds, which is what the API speaks."""
    if when is None:
        return None
    if isinstance(when, datetime):
        # A naive datetime is taken as UTC: the server records UTC, and guessing
        # the local zone would silently shift sightings by hours.
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return int(when.timestamp())
    return int(when)


def _to_seconds(duration: int | float | timedelta | None) -> int | None:
    """Normalise a TTL to whole seconds."""
    if duration is None:
        return None
    if isinstance(duration, timedelta):
        return int(duration.total_seconds())
    return int(duration)


@dataclass(frozen=True, slots=True)
class Sighting:
    """One value observed in one namespace, for a bulk write.

    Args:
        namespace: Where to record it, e.g. ``"feeds/misp/ips"``.
        value: The thing observed.
        timestamp: When it was observed. Unix seconds or a
            :class:`~datetime.datetime`; ``None`` means "now", decided by the
            server.
        ttl: Expire the value this long after it was last seen. Seconds or a
            :class:`~datetime.timedelta`. ``None`` leaves any existing TTL
            alone, ``0`` clears it.
        noshadow: Do not record the shadow sighting that otherwise tracks how
            often a value was searched for.
    """

    namespace: str
    value: str
    timestamp: int | float | datetime | None = None
    ttl: int | float | timedelta | None = None
    noshadow: bool = False

    def to_json(self) -> dict[str, Any]:
        """The wire form of this sighting, as an item of a bulk request."""
        from ._protocol import normalize_namespace

        item: dict[str, Any] = {
            "namespace": normalize_namespace(self.namespace),
            "value": self.value,
        }
        timestamp = _to_epoch(self.timestamp)
        if timestamp is not None:
            item["timestamp"] = timestamp
        ttl = _to_seconds(self.ttl)
        if ttl is not None:
            item["ttl"] = ttl
        if self.noshadow:
            item["noshadow"] = True
        return item


@dataclass(frozen=True, slots=True)
class Attribute:
    """A value SightingDB has seen, and what it knows about it.

    Attributes:
        namespace: The namespace it was read from.
        value: The value itself.
        count: How many times it has been observed.
        first_seen: Unix seconds of the first observation.
        last_seen: Unix seconds of the most recent observation.
        consensus: How many namespaces hold this same value.
        ttl: Seconds after ``last_seen`` until it expires; ``0`` means never.
        tags: Free-form tags the server holds for this value.
        stats: Hourly buckets of ``{unix seconds: count}``, present only when
            the value was read with ``stats=True``.
    """

    found: ClassVar[bool] = True

    namespace: str
    value: str
    count: int
    first_seen: int
    last_seen: int
    consensus: int
    ttl: int = 0
    tags: str = ""
    stats: Mapping[int, int] | None = None

    @property
    def first_seen_at(self) -> datetime:
        """:attr:`first_seen` as a timezone-aware UTC datetime."""
        return datetime.fromtimestamp(self.first_seen, tz=timezone.utc)

    @property
    def last_seen_at(self) -> datetime:
        """:attr:`last_seen` as a timezone-aware UTC datetime."""
        return datetime.fromtimestamp(self.last_seen, tz=timezone.utc)

    @property
    def expires_at(self) -> datetime | None:
        """When this value expires, or ``None`` if it never does."""
        if not self.ttl:
            return None
        return self.last_seen_at + timedelta(seconds=self.ttl)

    @property
    def stats_by_hour(self) -> dict[datetime, int] | None:
        """:attr:`stats` keyed by UTC datetime instead of Unix seconds."""
        if self.stats is None:
            return None
        return {
            datetime.fromtimestamp(int(bucket), tz=timezone.utc): count
            for bucket, count in self.stats.items()
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any], namespace: str) -> Attribute:
        stats = data.get("stats")
        return cls(
            namespace=namespace,
            value=data.get("value", ""),
            count=int(data.get("count", 0)),
            first_seen=int(data.get("first_seen", 0)),
            last_seen=int(data.get("last_seen", 0)),
            consensus=int(data.get("consensus", 0)),
            ttl=int(data.get("ttl", 0)),
            tags=data.get("tags", "") or "",
            stats=(
                {int(k): int(v) for k, v in stats.items()}
                if isinstance(stats, Mapping)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class Miss:
    """A value a bulk read asked for and the server does not have.

    A miss is not an error: asking about a hundred indicators and hearing that
    ninety of them were never seen is a perfectly good answer. Check
    :attr:`found` — or ``isinstance(result, Attribute)`` — to tell the two
    apart.

    Attributes:
        namespace: The namespace that was looked up.
        value: The value that was looked up.
        error: Why it was not returned, as the server put it: ``"Value not
            found"`` when the namespace exists but the value does not,
            ``"Path not found"`` when the namespace itself does not exist.
    """

    found: ClassVar[bool] = False

    namespace: str
    value: str
    error: str = "Value not found"

    @property
    def namespace_exists(self) -> bool:
        """Whether the namespace itself was found, only the value missing."""
        return self.error != "Path not found"


#: What one item of a bulk read comes back as.
ReadResult = Attribute | Miss


@dataclass(frozen=True, slots=True)
class WriteError:
    """One item of a bulk write that the server rejected."""

    namespace: str
    value: str
    error: str


@dataclass(frozen=True, slots=True)
class BulkWriteResult:
    """What came back from a bulk write.

    Attributes:
        written: How many sightings were recorded.
        errors: The items that were rejected, each with the server's reason.
        message: The server's own summary: ``"ok"``, ``"partial"`` or
            ``"failed"``.
    """

    written: int
    errors: tuple[WriteError, ...] = ()
    message: str = "ok"

    @property
    def ok(self) -> bool:
        """True when every sighting was recorded."""
        return not self.errors

    def __bool__(self) -> bool:
        return self.ok

    def __add__(self, other: BulkWriteResult) -> BulkWriteResult:
        """Combine two results, so a chunked write can report one total."""
        if not isinstance(other, BulkWriteResult):
            return NotImplemented
        errors = self.errors + other.errors
        return BulkWriteResult(
            written=self.written + other.written,
            errors=errors,
            message="ok" if not errors else ("failed" if not self.written + other.written else "partial"),
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> BulkWriteResult:
        return cls(
            written=int(data.get("written", 0)),
            errors=tuple(
                WriteError(
                    namespace=e.get("namespace", ""),
                    value=e.get("value", ""),
                    error=e.get("error", ""),
                )
                for e in data.get("errors", ())
            ),
            message=data.get("message", "ok"),
        )


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """What the server reports about itself on ``/i``."""

    implementation: str = ""
    version: str = ""
    vendor: str = ""
    author: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ServerInfo:
        return cls(
            implementation=data.get("implementation", ""),
            version=data.get("version", ""),
            vendor=data.get("vendor", ""),
            author=data.get("author", ""),
            raw=dict(data),
        )


@dataclass(frozen=True, slots=True, order=False)
class RecentWrite:
    """A value written recently, and the namespace it went to.

    What :meth:`~sightingdb.SightingDB.latest` returns. The server shows only
    the writes in namespaces the API key may read, so two keys see different
    lists.

    Attributes:
        namespace: Where it was written.
        value: The value written.
        written: Unix seconds when the write *arrived* at the server — not the
            sighting's own timestamp, which a client may have set in the past.
    """

    namespace: str
    value: str
    written: int

    @property
    def written_at(self) -> datetime:
        """:attr:`written` as a timezone-aware UTC datetime."""
        return datetime.fromtimestamp(self.written, tz=timezone.utc)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> RecentWrite:
        return cls(
            namespace=str(data.get("namespace", "")),
            value=str(data.get("value", "")),
            written=int(data.get("written", 0)),
        )
