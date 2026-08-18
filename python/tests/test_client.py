"""What the synchronous client sends, and what it makes of the answers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import sightingdb
from conftest import HELP_PAGE, answering, build, json_response

ATTRIBUTE = {
    "value": "127.0.0.1",
    "first_seen": 1566624658,
    "last_seen": 1566624689,
    "count": 2,
    "tags": "",
    "ttl": 0,
    "consensus": 3,
}


# -- requests ---------------------------------------------------------------


def test_write_sends_a_get_with_the_value_in_the_query():
    db, server = answering({"message": "ok", "count": 7})
    assert db.write("feeds/ips", "127.0.0.1") == 7
    assert server.last.method == "GET"
    assert server.last.url.path == "/w/feeds/ips"
    assert server.last.url.params["val"] == "127.0.0.1"


def test_the_api_key_travels_in_the_authorization_header():
    db, server = answering({"message": "ok", "count": 1})
    db.write("ns", "v")
    assert server.last.headers["authorization"] == "secret"
    assert server.last.headers["user-agent"].startswith("sightingdb-python/")


def test_no_api_key_means_no_authorization_header():
    db, server = build(
        lambda r: json_response({"message": "ok", "count": 1}), apikey=None
    )
    with db:
        db.write("ns", "v")
    assert "authorization" not in server.last.headers


@pytest.mark.parametrize(
    "written, on_the_wire",
    [
        ("/foo/bar", "foo/bar"),
        ("foo/bar/", "foo/bar"),
        ("/foo/bar/", "foo/bar"),
        ("foo//bar", "foo/bar"),
        ("  /foo/bar  ", "foo/bar"),
    ],
)
def test_namespaces_are_normalized(written, on_the_wire):
    # foo/bar and foo/bar/ are two different namespaces to the server, each
    # with its own counts. Every spelling has to land on the same one.
    db, server = answering({"message": "ok", "count": 1})
    db.write(written, "v")
    assert server.last.url.path == f"/w/{on_the_wire}"


def test_an_empty_namespace_is_refused_before_it_is_sent():
    db, server = answering({"message": "ok", "count": 1})
    with pytest.raises(ValueError):
        db.write("///", "v")
    assert server.requests == []


def test_an_empty_value_is_refused_before_it_is_sent():
    db, server = answering({"message": "ok", "count": 1})
    with pytest.raises(ValueError):
        db.write("ns", "")
    assert server.requests == []


def test_timestamps_and_ttls_accept_datetimes_and_timedeltas():
    db, server = answering({"message": "ok", "count": 1})
    when = datetime(2020, 4, 20, 6, 32, 50, tzinfo=timezone.utc)
    db.write("ns", "v", timestamp=when, ttl=timedelta(hours=1))
    assert server.last.url.params["timestamp"] == str(int(when.timestamp()))
    assert server.last.url.params["ttl"] == "3600"


def test_a_naive_datetime_is_read_as_utc():
    db, server = answering({"message": "ok", "count": 1})
    db.write("ns", "v", timestamp=datetime(2020, 4, 20, 6, 32, 50))
    assert server.last.url.params["timestamp"] == "1587364370"


def test_reading_can_suppress_the_shadow_sighting():
    db, server = answering(ATTRIBUTE)
    db.read("ns", "v", shadow=False)
    assert "noshadow" in server.last.url.params


def test_stats_go_to_the_stats_endpoint():
    db, server = answering(dict(ATTRIBUTE, stats={"1593716400": 2}))
    attribute = db.read("ns", "v", stats=True)
    assert server.last.url.path == "/rs/ns"
    assert attribute.stats == {1593716400: 2}
    assert attribute.stats_by_hour == {
        datetime(2020, 7, 2, 19, tzinfo=timezone.utc): 2
    }


def test_delete_is_a_get_because_a_post_would_be_silently_ignored():
    # SightingDB answers an unknown method with its help page and a 200: the
    # previous client POSTed here and reported success without deleting.
    db, server = answering({"message": "ok"})
    db.delete("/foo/bar/")
    assert server.last.method == "GET"
    assert server.last.url.path == "/d/foo/bar"


def test_delete_can_tolerate_a_missing_namespace():
    db, _ = answering({"error": "Path not found", "namespace": "gone", "value": ""}, 404)
    db.delete("gone", missing_ok=True)
    with pytest.raises(sightingdb.NotFoundError):
        db.delete("gone")


# -- replies ----------------------------------------------------------------


def test_read_returns_an_attribute_with_derived_times():
    db, _ = answering(dict(ATTRIBUTE, ttl=60))
    attribute = db.read("feeds/ips", "127.0.0.1")
    assert attribute.namespace == "feeds/ips"
    assert attribute.count == 2
    assert attribute.consensus == 3
    assert attribute.first_seen_at == datetime.fromtimestamp(
        1566624658, tz=timezone.utc
    )
    assert attribute.expires_at == attribute.last_seen_at + timedelta(seconds=60)


def test_an_attribute_without_a_ttl_never_expires():
    db, _ = answering(ATTRIBUTE)
    assert db.read("ns", "v").expires_at is None


def test_read_many_reports_misses_as_results_not_errors():
    body = {
        "items": [
            ATTRIBUTE,
            {"error": "Value not found", "namespace": "ns", "value": "9.9.9.9"},
            {"error": "Path not found", "namespace": "nope", "value": "x"},
        ]
    }
    db, server = answering(body)
    results = db.read_many(
        [("ns", "127.0.0.1"), ("ns", "9.9.9.9"), ("nope", "x")]
    )

    assert server.last.url.path == "/rb"
    assert [r.found for r in results] == [True, False, False]
    assert isinstance(results[0], sightingdb.Attribute)
    assert results[1].namespace_exists is True
    assert results[2].namespace_exists is False


def test_read_many_of_nothing_asks_nothing():
    db, server = answering({"items": []})
    assert db.read_many([]) == []
    assert server.requests == []


def test_list_values_returns_every_attribute_in_the_namespace():
    db, server = answering({"attributes": [ATTRIBUTE, dict(ATTRIBUTE, value="1.1.1.1")]})
    values = db.list_values("feeds/ips")
    assert server.last.url.path == "/r/feeds/ips"
    assert [v.value for v in values] == ["127.0.0.1", "1.1.1.1"]
    assert all(v.namespace == "feeds/ips" for v in values)


def test_info_is_parsed():
    db, _ = answering(
        {
            "implementation": "SightingDB",
            "version": "0.5.3",
            "vendor": "github.com/stricaud/sightingdb",
            "author": "Sebastien Tricaud",
        }
    )
    assert db.info().version == "0.5.3"


# -- bulk writes ------------------------------------------------------------


def test_write_many_sends_one_request_and_sums_the_result():
    db, server = answering({"message": "ok", "written": 3})
    result = db.write_many(
        [
            sightingdb.Sighting("ns", "a", timestamp=5555),
            ("ns", "b"),
            {"namespace": "other", "value": "c", "ttl": 60, "noshadow": True},
        ]
    )
    assert result.written == 3
    assert result.ok and bool(result) is True
    assert server.body() == {
        "items": [
            {"namespace": "ns", "value": "a", "timestamp": 5555},
            {"namespace": "ns", "value": "b"},
            {"namespace": "other", "value": "c", "ttl": 60, "noshadow": True},
        ]
    }


def test_a_partial_bulk_write_raises_by_default():
    body = {
        "message": "partial",
        "written": 1,
        "errors": [{"namespace": "ns", "value": "x", "error": "Refusing something"}],
    }
    db, _ = answering(body)
    with pytest.raises(sightingdb.BulkWriteError) as caught:
        db.write_many([("ns", "a"), ("ns", "x")])
    assert caught.value.result.written == 1
    assert caught.value.result.errors[0].error == "Refusing something"


def test_a_partial_bulk_write_can_be_reported_instead_of_raised():
    body = {"message": "partial", "written": 1, "errors": [{"namespace": "ns", "value": "x", "error": "nope"}]}
    db, _ = answering(body)
    result = db.write_many([("ns", "a"), ("ns", "x")], strict=False)
    assert result.written == 1 and not result.ok


def test_chunking_splits_into_several_requests_and_reports_one_total():
    db, server = answering({"message": "ok", "written": 2})
    result = db.write_many([("ns", str(i)) for i in range(5)], chunk_size=2)
    assert len(server.requests) == 3
    assert [len(server.body(i)["items"]) for i in range(3)] == [2, 2, 1]
    assert result.written == 6  # the fake server claims 2 per request


def test_a_batch_sends_when_the_block_ends():
    db, server = answering({"message": "ok", "written": 2})
    with db.batch() as batch:
        batch.add("ns", "a")
        batch.add("ns", "b")
        assert len(batch) == 2
        assert server.requests == []
    assert len(server.requests) == 1
    assert batch.result.written == 2


def test_a_batch_sends_every_chunk_size_sightings():
    db, server = answering({"message": "ok", "written": 2})
    with db.batch(chunk_size=2) as batch:
        for i in range(5):
            batch.add("ns", str(i))
        assert len(server.requests) == 2
    assert len(server.requests) == 3


def test_a_batch_sends_nothing_when_the_block_fails():
    db, server = answering({"message": "ok", "written": 1})
    with pytest.raises(RuntimeError):
        with db.batch() as batch:
            batch.add("ns", "a")
            raise RuntimeError("the caller changed its mind")
    assert server.requests == []


# -- errors -----------------------------------------------------------------


@pytest.mark.parametrize(
    "status, expected",
    [
        (400, sightingdb.BadRequestError),
        (401, sightingdb.AuthenticationError),
        (403, sightingdb.PermissionDeniedError),
        (404, sightingdb.NotFoundError),
        (501, sightingdb.NotImplementedError),
        (500, sightingdb.ServerError),
    ],
)
def test_every_status_becomes_its_own_exception(status, expected):
    db, _ = answering({"message": "refused"}, status)
    with pytest.raises(expected) as caught:
        db.read("ns", "v")
    assert caught.value.status_code == status
    assert isinstance(caught.value, sightingdb.APIError)


def test_a_not_found_carries_the_namespace_and_value():
    db, _ = answering(
        {"error": "Value not found", "namespace": "ns", "value": "9.9.9.9"}, 404
    )
    with pytest.raises(sightingdb.NotFoundError) as caught:
        db.read("ns", "9.9.9.9")
    assert (caught.value.namespace, caught.value.value) == ("ns", "9.9.9.9")


def test_the_help_page_is_reported_as_a_protocol_error():
    # This is what the old client parsed as a successful delete.
    db, _ = build(lambda r: httpx.Response(200, text=HELP_PAGE))
    with db, pytest.raises(sightingdb.ProtocolError, match="help page"):
        db.delete("ns")


def test_a_connection_failure_becomes_a_transport_error():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    db, _ = build(refuse, max_retries=0)
    with db, pytest.raises(sightingdb.TransportError, match="failed"):
        db.info()


def test_a_certificate_failure_says_what_to_do_about_it():
    def refuse(request):
        raise httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
            request=request,
        )

    db, _ = build(refuse, max_retries=0)
    with db, pytest.raises(sightingdb.TransportError, match="self-signed"):
        db.info()


def test_a_timeout_is_its_own_error():
    def stall(request):
        raise httpx.ReadTimeout("too slow", request=request)

    db, _ = build(stall, max_retries=0)
    with db, pytest.raises(sightingdb.TimeoutError):
        db.info()


def test_ping_is_false_when_the_server_is_unreachable():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    db, _ = build(refuse, max_retries=0)
    with db:
        assert db.ping() is False


# -- retries ----------------------------------------------------------------


def test_a_read_is_retried_after_a_dropped_connection(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    attempts = []

    def flaky(request):
        attempts.append(request)
        if len(attempts) < 3:
            raise httpx.ReadError("connection reset", request=request)
        return json_response(ATTRIBUTE)

    db, _ = build(flaky, max_retries=2)
    with db:
        assert db.read("ns", "v").count == 2
    assert len(attempts) == 3


def test_a_write_is_not_retried_once_the_connection_was_established(monkeypatch):
    # SightingDB counts: a retried write that did land would count twice.
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    attempts = []

    def flaky(request):
        attempts.append(request)
        raise httpx.ReadTimeout("no answer", request=request)

    db, _ = build(flaky, max_retries=3)
    with db, pytest.raises(sightingdb.TimeoutError):
        db.write("ns", "v")
    assert len(attempts) == 1


def test_a_write_is_retried_when_the_connection_never_happened(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    attempts = []

    def flaky(request):
        attempts.append(request)
        if len(attempts) < 2:
            raise httpx.ConnectError("connection refused", request=request)
        return json_response({"message": "ok", "count": 1})

    db, _ = build(flaky, max_retries=2)
    with db:
        assert db.write("ns", "v") == 1
    assert len(attempts) == 2


def test_a_503_is_retried_and_a_400_is_not(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    answers = [
        httpx.Response(503, json={"message": "busy"}),
        httpx.Response(200, json=ATTRIBUTE),
    ]
    db, server = build(lambda r: answers.pop(0))
    with db:
        assert db.read("ns", "v").count == 2
    assert len(server.requests) == 2

    db, server = answering({"message": "nope"}, 400)
    with db, pytest.raises(sightingdb.BadRequestError):
        db.read("ns", "v")
    assert len(server.requests) == 1


def test_retry_after_is_honoured(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    answers = [
        httpx.Response(429, json={"message": "slow down"}, headers={"Retry-After": "2"}),
        httpx.Response(200, json=ATTRIBUTE),
    ]
    db, _ = build(lambda r: answers.pop(0))
    with db:
        db.read("ns", "v")
    assert slept == [2.0]
