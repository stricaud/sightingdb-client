"""Exceptions raised by the SightingDB client.

Every error the client raises derives from :class:`SightingDBError`, so a caller
that only wants "did this work?" can catch that one class. Everything the
*server* refused derives from :class:`APIError` and carries the HTTP status and
the decoded body; everything that went wrong before a reply arrived derives from
:class:`TransportError`.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SightingDBError",
    "ConfigurationError",
    "TransportError",
    "TimeoutError",
    "APIError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "NotImplementedError",
    "ServerError",
    "ProtocolError",
    "BulkWriteError",
]


class SightingDBError(Exception):
    """Base class for every error raised by this library."""


class ConfigurationError(SightingDBError):
    """The client was asked to start without enough to talk to a server."""


class TransportError(SightingDBError):
    """The request never got an answer: DNS, TCP, TLS, or a dropped connection."""


class TimeoutError(TransportError):  # noqa: A001 - deliberately shadows the builtin here
    """The server did not answer within the configured timeout."""


class APIError(SightingDBError):
    """The server answered, and the answer was a refusal.

    Attributes:
        status_code: The HTTP status the server used.
        message: The human-readable message the server sent, when it sent one.
        body: The decoded JSON body, or the raw text when it was not JSON.
        method: HTTP method of the request that failed.
        url: URL of the request that failed.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        body: Any = None,
        method: str = "",
        url: str = "",
    ) -> None:
        where = f" ({method} {url})" if url else ""
        super().__init__(f"[{status_code}] {message}{where}")
        self.status_code = status_code
        self.message = message
        self.body = body
        self.method = method
        self.url = url


class BadRequestError(APIError):
    """400: the server could not make sense of the request."""


class AuthenticationError(APIError):
    """401: no API key was sent, and the server wants one."""


class PermissionDeniedError(APIError):
    """403: the API key is unknown, or not granted this namespace.

    SightingDB deliberately answers the same way for both, so that probing
    cannot tell a wrong key from a key that is merely out of scope.
    """


class NotFoundError(APIError):
    """404: no such namespace, or no such value inside it.

    Attributes:
        namespace: The namespace that was looked up.
        value: The value that was looked up, empty when a whole namespace was.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 404,
        body: Any = None,
        method: str = "",
        url: str = "",
        namespace: str = "",
        value: str = "",
    ) -> None:
        super().__init__(
            message, status_code=status_code, body=body, method=method, url=url
        )
        self.namespace = namespace
        self.value = value


class NotImplementedError(APIError):  # noqa: A001 - deliberately shadows the builtin here
    """501: the endpoint exists in the API but the server does not implement it."""


class ServerError(APIError):
    """5xx: the server failed to handle a request it accepted."""


class ProtocolError(SightingDBError):
    """The server answered with something this client cannot parse.

    In practice this means an endpoint answered with the plain-text help page
    instead of JSON, which is what SightingDB serves for a route it does not
    recognise — usually a sign of a wrong URL or a version mismatch.
    """


class BulkWriteError(SightingDBError):
    """A bulk write landed only partly, and ``strict=True`` was in effect.

    Attributes:
        result: The full :class:`~sightingdb.models.BulkWriteResult`, including
            which items were rejected and why.
    """

    def __init__(self, result: Any) -> None:
        failed = len(result.errors)
        super().__init__(
            f"{failed} of {failed + result.written} sightings were rejected: "
            + "; ".join(f"{e.namespace}={e.value!r}: {e.error}" for e in result.errors[:3])
            + ("; ..." if failed > 3 else "")
        )
        self.result = result
