"""Where the client gets its server URL, API key and TLS settings.

Three sources, most specific first:

1. what you passed to the client,
2. the environment (``SIGHTINGDB_URL``, ``SIGHTINGDB_APIKEY``, ...),
3. a TOML file, ``~/.sightingdb/client.toml`` unless ``SIGHTINGDB_CONFIG``
   says otherwise.

Each field is resolved on its own, so a config file holding the URL and an
environment variable holding the key work together without either having to
repeat the other.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigurationError

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]

__all__ = ["Config", "DEFAULT_URL", "DEFAULT_CONFIG_PATH"]

DEFAULT_URL = "https://localhost:9999"
DEFAULT_CONFIG_PATH = Path("~/.sightingdb/client.toml")

_ENV_PREFIX = "SIGHTINGDB_"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigurationError(f"{name}: expected a boolean, got {value!r}")


def _as_verify(value: Any, name: str) -> bool | str:
    """``verify`` is a boolean, or a path to a CA bundle to trust."""
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    if text.lower() in _TRUE:
        return True
    if text.lower() in _FALSE:
        return False
    return text


def _as_number(value: Any, name: str, kind: type) -> Any:
    try:
        return kind(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name}: expected a number, got {value!r}") from exc


@dataclass(frozen=True, slots=True)
class Config:
    """Everything a client needs to reach a server.

    Attributes:
        url: Base URL of the SightingDB server, scheme included.
        apikey: Sent as the ``Authorization`` header. ``None`` is valid against
            a server started with ``authenticate = false``.
        verify: ``True`` to verify TLS certificates, ``False`` to skip
            verification, or a path to a CA bundle to trust — which is what you
            want against SightingDB's self-signed certificate.
        timeout: Seconds before a request is given up on.
        max_retries: How many times to retry a request that failed in a way
            that could plausibly succeed on a second attempt.
    """

    url: str = DEFAULT_URL
    #: Every server to talk to. One entry is the ordinary case — a node, or a
    #: load balancer that fans out on the client's behalf. Several make the
    #: client do the fanning out itself: writes to all of them, reads to one.
    urls: tuple[str, ...] = ()
    apikey: str | None = None
    verify: bool | str = True
    timeout: float = 10.0
    max_retries: int = 2
    #: What a fanned-out write is counted for on every node it reaches.
    #:
    #: Only used when there is more than one URL. Without it each node would
    #: count the same write under its own name, and the counts would add
    #: together when the nodes synced. Defaults to this machine's hostname.
    node_id: str | None = None

    def __post_init__(self) -> None:
        # `urls` is the real list and `url` is its first entry, so anything
        # reading `config.url` — a repr, an error message, a caller written
        # before this existed — still gets something true.
        given = self.urls or (self.url,)
        normalised = tuple(_normalise_url(one) for one in given if str(one).strip())
        if not normalised:
            raise ConfigurationError("No server URL was given")

        seen: list[str] = []
        for one in normalised:
            # The same server twice would be written to twice, which counts
            # twice. Worth dropping rather than trusting the caller.
            if one not in seen:
                seen.append(one)
        object.__setattr__(self, "urls", tuple(seen))
        object.__setattr__(self, "url", seen[0])

        if self.node_id is not None:
            node_id = self.node_id.strip()
            if not node_id:
                raise ConfigurationError("node_id cannot be empty")
            if len(node_id) > 64:
                raise ConfigurationError(
                    f"node_id must be 64 characters or fewer, got {len(node_id)}"
                )
            if not all(c.isalnum() or c in "-_." for c in node_id):
                raise ConfigurationError(
                    "node_id may use letters, digits, '-', '_' and '.' only, "
                    f"got {node_id!r}"
                )
            object.__setattr__(self, "node_id", node_id)
        if self.timeout <= 0:
            raise ConfigurationError(f"timeout must be positive, got {self.timeout!r}")
        if self.max_retries < 0:
            raise ConfigurationError(
                f"max_retries cannot be negative, got {self.max_retries!r}"
            )

    # -- sources ---------------------------------------------------------

    @classmethod
    def env_settings(cls, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
        """The settings the environment specifies, and only those."""
        env = os.environ if environ is None else environ
        found: dict[str, Any] = {}

        url = env.get(f"{_ENV_PREFIX}URL")
        urls = env.get(f"{_ENV_PREFIX}URLS")
        host = env.get(f"{_ENV_PREFIX}HOST")
        if urls:
            # Comma separated, because an environment variable cannot hold a
            # list and a comma is what everything else uses for one.
            found["urls"] = tuple(
                one.strip() for one in urls.split(",") if one.strip()
            )
        elif url:
            found["url"] = url
        elif host:
            port = env.get(f"{_ENV_PREFIX}PORT")
            ssl = env.get(f"{_ENV_PREFIX}SSL")
            found["url"] = _build_url(
                host,
                _as_number(port, f"{_ENV_PREFIX}PORT", int) if port else None,
                _as_bool(ssl, f"{_ENV_PREFIX}SSL") if ssl is not None else None,
            )

        if (apikey := env.get(f"{_ENV_PREFIX}APIKEY")) is not None:
            found["apikey"] = apikey
        if (node_id := env.get(f"{_ENV_PREFIX}NODE_ID")) is not None:
            found["node_id"] = node_id
        if (verify := env.get(f"{_ENV_PREFIX}VERIFY")) is not None:
            found["verify"] = _as_verify(verify, f"{_ENV_PREFIX}VERIFY")
        if (timeout := env.get(f"{_ENV_PREFIX}TIMEOUT")) is not None:
            found["timeout"] = _as_number(timeout, f"{_ENV_PREFIX}TIMEOUT", float)
        if (retries := env.get(f"{_ENV_PREFIX}MAX_RETRIES")) is not None:
            found["max_retries"] = _as_number(
                retries, f"{_ENV_PREFIX}MAX_RETRIES", int
            )
        return found

    @classmethod
    def file_settings(cls, path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        """The settings a TOML file specifies, and only those.

        A missing file is not an error unless you named it explicitly: the
        default location is a convenience, not a requirement.
        """
        explicit = path is not None
        if path is None:
            path = os.environ.get(f"{_ENV_PREFIX}CONFIG")
            explicit = path is not None
        target = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH.expanduser()

        if not target.is_file():
            if explicit:
                raise ConfigurationError(f"No such configuration file: {target}")
            return {}
        if tomllib is None:  # pragma: no cover - only on 3.10 without tomli
            raise ConfigurationError(
                f"Reading {target} needs TOML support: pip install tomli"
            )

        try:
            with target.open("rb") as fp:
                document = tomllib.load(fp)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigurationError(f"Cannot read {target}: {exc}") from exc

        # [client] is where settings live; a flat file is accepted too, since
        # that is what people write first.
        section = document.get("client", document)
        if not isinstance(section, Mapping):
            raise ConfigurationError(f"{target}: [client] must be a table")

        found: dict[str, Any] = {}
        if "urls" in section:
            listed = section["urls"]
            if not isinstance(listed, (list, tuple)):
                raise ConfigurationError(f"{target}: urls must be a list")
            found["urls"] = tuple(str(one) for one in listed)
        elif "url" in section:
            found["url"] = str(section["url"])
        elif "host" in section:
            found["url"] = _build_url(
                str(section["host"]),
                _as_number(section["port"], f"{target}: port", int)
                if "port" in section
                else None,
                _as_bool(section["ssl"], f"{target}: ssl") if "ssl" in section else None,
            )
        if "apikey" in section:
            found["apikey"] = str(section["apikey"])
        if "node_id" in section:
            found["node_id"] = str(section["node_id"])
        if "verify" in section:
            found["verify"] = _as_verify(section["verify"], f"{target}: verify")
        if "timeout" in section:
            found["timeout"] = _as_number(section["timeout"], f"{target}: timeout", float)
        if "max_retries" in section:
            found["max_retries"] = _as_number(
                section["max_retries"], f"{target}: max_retries", int
            )
        return found

    # -- resolution ------------------------------------------------------

    @classmethod
    def resolve(
        cls,
        *,
        url: str | None = None,
        urls: Any = None,
        host: str | None = None,
        port: int | None = None,
        ssl: bool | None = None,
        apikey: str | None = None,
        verify: bool | str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        node_id: str | None = None,
        config_file: str | os.PathLike[str] | None = None,
        use_env: bool = True,
        use_file: bool = True,
    ) -> Config:
        """Build a config from arguments, environment and file, in that order."""
        if url and host:
            raise ConfigurationError("Pass either url= or host=, not both")
        if urls and (url or host):
            raise ConfigurationError("Pass either urls= or url=/host=, not both")

        settings: dict[str, Any] = {}
        if use_file or config_file is not None:
            settings.update(cls.file_settings(config_file))
        if use_env:
            settings.update(cls.env_settings())

        explicit: dict[str, Any] = {}
        if urls:
            listed = tuple(str(one) for one in urls)
            if not listed:
                raise ConfigurationError("urls= was empty")
            explicit["urls"] = listed
            # A list given here replaces whatever single URL a file or the
            # environment supplied, rather than being merged with it.
            settings.pop("url", None)
        elif url:
            explicit["url"] = url
        elif host:
            explicit["url"] = _build_url(host, port, ssl)
        elif port is not None or ssl is not None:
            # Adjusting only the port (or only the scheme) of whatever URL the
            # other sources supplied is still a well-defined request.
            base = settings.get("url", DEFAULT_URL)
            explicit["url"] = _adjust_url(base, port, ssl)
        if apikey is not None:
            explicit["apikey"] = apikey
        if verify is not None:
            explicit["verify"] = _as_verify(verify, "verify")
        if timeout is not None:
            explicit["timeout"] = float(timeout)
        if max_retries is not None:
            explicit["max_retries"] = int(max_retries)
        if node_id is not None:
            explicit["node_id"] = node_id
        settings.update(explicit)

        # A list from any source wins over a single URL from a lower-priority
        # one, for the same reason: the two describe different intentions.
        if "urls" in settings and "url" in settings and "url" not in explicit:
            settings.pop("url")

        unknown = set(settings) - {f.name for f in cls.__dataclass_fields__.values()}
        if unknown:  # pragma: no cover - defensive
            raise ConfigurationError(f"Unknown settings: {', '.join(sorted(unknown))}")
        return cls(**settings)

    def replace(self, **changes: Any) -> Config:
        """A copy of this config with some settings changed."""
        return replace(self, **changes)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        key = "None" if self.apikey is None else "'***'"
        return (
            f"Config(url={self.url!r}, apikey={key}, verify={self.verify!r}, "
            f"timeout={self.timeout!r}, max_retries={self.max_retries!r})"
        )


def _normalise_url(url: Any) -> str:
    """A URL with a scheme and no trailing slash."""
    text = str(url).strip().rstrip("/")
    if "://" not in text:
        text = f"https://{text}"
    return text


def _build_url(host: str, port: int | None, ssl: bool | None) -> str:
    scheme = "https" if ssl is None or ssl else "http"
    if "://" in host:
        return _adjust_url(host, port, ssl)
    return f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"


def _adjust_url(url: str, port: int | None, ssl: bool | None) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url if "://" in url else f"https://{url}")
    scheme = parts.scheme if ssl is None else ("https" if ssl else "http")
    host = parts.hostname or "localhost"
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    chosen = port if port is not None else parts.port
    netloc = f"{host}:{chosen}" if chosen else host
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), "", ""))
