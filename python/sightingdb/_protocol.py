"""The SightingDB wire protocol, with no I/O in it.

Everything that knows what a request looks like and what a reply means lives
here, so that the synchronous and asynchronous clients differ only in how they
send bytes — and cannot drift apart in what they send.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote

import httpx

from . import errors
from .models import (
    Attribute,
    BulkWriteResult,
    Miss,
    ReadResult,
    ServerInfo,
    Sighting,
    _to_epoch,
    _to_seconds,
)

__all__ = ["Request", "normalize_namespace"]

#: Statuses worth trying again. 429 and 503 mean the request was not processed,
#: so they are safe even for a write; 502 and 504 leave that open, so they are
#: retried only for reads, which cannot double-count anything.
_RETRY_ALWAYS = frozenset({429, 503})
_RETRY_IDEMPOTENT = frozenset({502, 504})

_BACKOFF_BASE = 0.25
_BACKOFF_CAP = 8.0


def normalize_namespace(namespace: str) -> str:
    """Put a namespace in the one form the server keys on.

    SightingDB treats the path it is given literally: ``foo/bar`` and
    ``foo/bar/`` are two different namespaces holding two different counts,
    which is a trap worth closing here rather than in every caller. Leading and
    trailing slashes are dropped and repeated ones collapsed, so all the
    spellings people naturally write land in the same place.
    """
    if not isinstance(namespace, str):
        raise TypeError(f"namespace must be a string, got {type(namespace).__name__}")
    cleaned = "/".join(part for part in namespace.strip().split("/") if part)
    if not cleaned:
        raise ValueError("namespace must not be empty")
    return cleaned


def _check_value(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"value must be a string, got {type(value).__name__}")
    if not value:
        # The server refuses these; catching it here means a bulk write of ten
        # thousand sightings does not get half-applied over one empty string.
        raise ValueError("value must not be empty")
    return value


def _path(prefix: str, namespace: str) -> str:
    return f"/{prefix}/{quote(namespace, safe='/')}"


@dataclass(frozen=True, slots=True)
class Request:
    """One call to make, and what to do with the answer.

    Attributes:
        idempotent: Whether sending this twice is harmless. Writes are not:
            SightingDB counts, so a retried write counts again.
    """

    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    json: Any | None = None
    idempotent: bool = True
    decode: Callable[[Any], Any] = lambda body: body
    namespace: str = ""
    value: str = ""


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


def write(
    namespace: str,
    value: str,
    *,
    timestamp: Any = None,
    ttl: Any = None,
) -> Request:
    namespace = normalize_namespace(namespace)
    params: dict[str, Any] = {"val": _check_value(value)}
    if (epoch := _to_epoch(timestamp)) is not None:
        params["timestamp"] = epoch
    if (seconds := _to_seconds(ttl)) is not None:
        params["ttl"] = seconds
    return Request(
        "GET",
        _path("w", namespace),
        params,
        idempotent=False,
        decode=lambda body: int(body.get("count", 0)),
        namespace=namespace,
        value=value,
    )


def read(
    namespace: str,
    value: str,
    *,
    stats: bool = False,
    shadow: bool = True,
) -> Request:
    namespace = normalize_namespace(namespace)
    params: dict[str, Any] = {"val": _check_value(value)}
    if not shadow:
        params["noshadow"] = ""
    return Request(
        "GET",
        _path("rs" if stats else "r", namespace),
        params,
        decode=lambda body: Attribute.from_json(body, namespace),
        namespace=namespace,
        value=value,
    )


def list_values(namespace: str, *, shadow: bool = True) -> Request:
    namespace = normalize_namespace(namespace)
    params: dict[str, Any] = {} if shadow else {"noshadow": ""}
    return Request(
        "GET",
        _path("r", namespace),
        params,
        decode=lambda body: [
            Attribute.from_json(item, namespace) for item in body.get("attributes", ())
        ],
        namespace=namespace,
    )


def delete(namespace: str) -> Request:
    namespace = normalize_namespace(namespace)
    # /d is a GET. It used to be sent as a POST here, which SightingDB answers
    # with its plain-text help page and a 200 — a delete that silently did
    # nothing and reported success.
    return Request(
        "GET",
        _path("d", namespace),
        idempotent=False,
        decode=lambda body: None,
        namespace=namespace,
    )


def info() -> Request:
    return Request("GET", "/i", decode=ServerInfo.from_json)


def _items_payload(sightings: Sequence[Sighting]) -> dict[str, Any]:
    return {"items": [s.to_json() for s in sightings]}


def read_many(sightings: Sequence[Sighting], *, stats: bool = False) -> Request:
    payload = _items_payload(sightings)
    for item in payload["items"]:
        _check_value(item["value"])

    def decode(body: Any) -> list[ReadResult]:
        results: list[ReadResult] = []
        for item, sent in zip(body.get("items", ()), payload["items"]):
            if "error" in item:
                results.append(
                    Miss(
                        namespace=item.get("namespace", sent["namespace"]),
                        value=item.get("value", sent["value"]),
                        error=item.get("error", "Value not found"),
                    )
                )
            else:
                results.append(Attribute.from_json(item, sent["namespace"]))
        return results

    return Request("POST", "/rbs" if stats else "/rb", json=payload, decode=decode)


def write_many(sightings: Sequence[Sighting]) -> Request:
    payload = _items_payload(sightings)
    for item in payload["items"]:
        _check_value(item["value"])
    return Request(
        "POST",
        "/wb",
        json=payload,
        idempotent=False,
        decode=BulkWriteResult.from_json,
    )


def coerce_sightings(items: Iterable[Any]) -> list[Sighting]:
    """Accept sightings written as objects, tuples or dictionaries."""
    coerced: list[Sighting] = []
    for item in items:
        if isinstance(item, Sighting):
            coerced.append(item)
        elif isinstance(item, Mapping):
            coerced.append(Sighting(**item))
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            coerced.append(Sighting(item[0], item[1]))
        else:
            raise TypeError(
                "expected a Sighting, a (namespace, value) pair or a mapping, "
                f"got {item!r}"
            )
    return coerced


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


def parse(response: httpx.Response, request: Request) -> Any:
    """Turn a reply into a model, or into the exception it deserves."""
    body = _decode_json(response, request)
    if response.is_success:
        return request.decode(body)
    raise _error(response, request, body)


def _decode_json(response: httpx.Response, request: Request) -> Any:
    try:
        return response.json()
    except ValueError:
        text = response.text.strip()
        if response.is_success and text.startswith("SightingDB"):
            # Unknown routes fall through to the help page, with a 200.
            raise errors.ProtocolError(
                f"{request.method} {request.path} was answered with the server's "
                "help page instead of JSON: this endpoint or method does not "
                "exist on that server."
            ) from None
        raise errors.ProtocolError(
            f"{request.method} {request.path} answered "
            f"{response.status_code} with a body that is not JSON: {text[:200]!r}"
        ) from None


def _error(
    response: httpx.Response, request: Request, body: Any
) -> errors.APIError:
    status = response.status_code
    try:
        url = str(response.request.url)
    except RuntimeError:  # a response built without its request
        url = request.path
    if isinstance(body, Mapping):
        message = str(body.get("message") or body.get("error") or response.reason_phrase)
    else:
        message = response.reason_phrase or "request failed"

    common = {"status_code": status, "body": body, "method": request.method, "url": url}
    if status == 400:
        return errors.BadRequestError(message, **common)
    if status == 401:
        return errors.AuthenticationError(
            f"{message} Set apikey=... or SIGHTINGDB_APIKEY.", **common
        )
    if status == 403:
        return errors.PermissionDeniedError(message, **common)
    if status == 404:
        namespace = request.namespace
        value = request.value
        if isinstance(body, Mapping):
            namespace = body.get("namespace", namespace)
            value = body.get("value", value)
        return errors.NotFoundError(
            message, namespace=namespace, value=value, **common
        )
    if status == 501:
        return errors.NotImplementedError(message, **common)
    if status >= 500:
        return errors.ServerError(message, **common)
    return errors.APIError(message, **common)


def transport_error(exc: Exception, request: Request, url: str) -> errors.SightingDBError:
    """Wrap a connection-level failure in something a caller can act on."""
    where = f"{request.method} {url}"
    if isinstance(exc, httpx.TimeoutException):
        return errors.TimeoutError(f"{where} timed out: {exc}")
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text:
        return errors.TransportError(
            f"{where} failed TLS verification: {exc}. SightingDB ships a "
            "self-signed certificate by default — point verify= at its "
            "cert.pem to trust it, or pass verify=False to skip the check."
        )
    return errors.TransportError(f"{where} failed: {exc}")


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


def retry_after_exception(exc: Exception, *, idempotent: bool) -> bool:
    """Whether a failed attempt can be retried without changing the outcome.

    A connection that was never established carries no risk of a double write.
    Anything later — a read timeout on a write, say — might already have been
    applied, and SightingDB counts, so those are only retried for reads.
    """
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    return idempotent and isinstance(exc, httpx.TransportError)


def retry_after_status(status: int, *, idempotent: bool) -> bool:
    if status in _RETRY_ALWAYS:
        return True
    return idempotent and status in _RETRY_IDEMPOTENT


def backoff(attempt: int, response: httpx.Response | None = None) -> float:
    """Seconds to wait before attempt ``attempt + 1``, server's advice first."""
    if response is not None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return max(0.0, min(float(header), _BACKOFF_CAP))
            except ValueError:
                pass
    window = min(_BACKOFF_CAP, _BACKOFF_BASE * (2**attempt))
    # Jitter, so that a fleet of clients does not come back in lockstep.
    return window * (0.5 + random.random() / 2)
