"""Tests against a real SightingDB.

These are skipped unless you point them at a server::

    SIGHTINGDB_TEST_URL=https://localhost:9999 \
    SIGHTINGDB_TEST_APIKEY=changeme \
    SIGHTINGDB_TEST_VERIFY=false \
    pytest -m live

Everything is written under ``_tests/`` and deleted again afterwards.
"""

from __future__ import annotations

import os
import uuid

import pytest

import sightingdb

pytestmark = pytest.mark.live

URL = os.environ.get("SIGHTINGDB_TEST_URL")
APIKEY = os.environ.get("SIGHTINGDB_TEST_APIKEY")
VERIFY = os.environ.get("SIGHTINGDB_TEST_VERIFY", "true")

if not URL:  # pragma: no cover - collection-time decision
    pytest.skip("set SIGHTINGDB_TEST_URL to run the live tests", allow_module_level=True)


@pytest.fixture
def namespace():
    return f"_tests/client/{uuid.uuid4().hex[:12]}"


@pytest.fixture
def db(namespace):
    client = sightingdb.SightingDB(
        URL, apikey=APIKEY, verify=VERIFY, use_env=False, use_file=False
    )
    with client:
        yield client
        client.delete(namespace, missing_ok=True)


def test_the_server_identifies_itself(db):
    assert db.info().implementation == "SightingDB"
    assert db.ping() is True


def test_writing_counts(db, namespace):
    assert db.write(namespace, "127.0.0.1") == 1
    assert db.write(namespace, "127.0.0.1") == 2
    attribute = db.read(namespace, "127.0.0.1", shadow=False)
    assert attribute.count == 2
    assert attribute.consensus >= 1


def test_a_timestamp_sets_first_seen(db, namespace):
    db.write(namespace, "127.0.0.1", timestamp=5555)
    assert db.read(namespace, "127.0.0.1", shadow=False).first_seen == 5555


def test_a_ttl_is_recorded(db, namespace):
    db.write(namespace, "expiring", ttl=3600)
    attribute = db.read(namespace, "expiring", shadow=False)
    assert attribute.ttl == 3600
    assert attribute.expires_at is not None


def test_trailing_slashes_do_not_split_the_namespace(db, namespace):
    db.write(namespace, "1.2.3.4")
    db.write(f"/{namespace}/", "1.2.3.4")
    assert db.read(namespace, "1.2.3.4", shadow=False).count == 2


def test_reading_a_value_that_was_never_seen_raises(db, namespace):
    db.write(namespace, "present")
    with pytest.raises(sightingdb.NotFoundError):
        db.read(namespace, "absent", shadow=False)


def test_bulk_write_then_bulk_read(db, namespace):
    result = db.write_many(
        [
            sightingdb.Sighting(namespace, "a", timestamp=1587364370),
            (namespace, "b"),
            (namespace, "b"),
        ]
    )
    assert result.written == 3 and result.ok

    found = db.read_many(
        [
            sightingdb.Sighting(namespace, "a", noshadow=True),
            sightingdb.Sighting(namespace, "b", noshadow=True),
            sightingdb.Sighting(namespace, "never-seen", noshadow=True),
            sightingdb.Sighting("_tests/no/such/namespace", "x", noshadow=True),
        ]
    )
    assert [r.found for r in found] == [True, True, False, False]
    assert found[0].first_seen == 1587364370
    assert found[1].count == 2
    assert found[2].namespace_exists is True
    assert found[3].namespace_exists is False


def test_a_bulk_write_that_is_refused_is_reported(db, namespace):
    # A timestamp the server cannot represent is rejected per item.
    result = db.write_many(
        [(namespace, "fine"), sightingdb.Sighting(namespace, "bad", timestamp=2**62)],
        strict=False,
    )
    assert result.written == 1
    assert result.errors and result.errors[0].value == "bad"


def test_statistics_come_back_with_the_value(db, namespace):
    db.write(namespace, "counted")
    attribute = db.read(namespace, "counted", stats=True, shadow=False)
    assert attribute.stats and sum(attribute.stats.values()) >= 1


def test_listing_a_namespace(db, namespace):
    db.write_many([(namespace, "a"), (namespace, "b")])
    assert {a.value for a in db.list_values(namespace)} == {"a", "b"}


def test_deleting_a_namespace(db, namespace):
    db.write(namespace, "gone-soon")
    db.delete(namespace)
    with pytest.raises(sightingdb.NotFoundError):
        db.list_values(namespace)
    db.delete(namespace, missing_ok=True)


def test_a_wrong_api_key_is_refused(namespace):
    with sightingdb.SightingDB(
        URL, apikey="definitely-not-a-key", verify=VERIFY, use_env=False, use_file=False
    ) as db:
        with pytest.raises(sightingdb.PermissionDeniedError):
            db.write(namespace, "nope")


async def test_the_async_client_writes_and_reads(namespace):
    async with sightingdb.AsyncSightingDB(
        URL, apikey=APIKEY, verify=VERIFY, use_env=False, use_file=False
    ) as db:
        try:
            await db.write(namespace, "127.0.0.1")
            attribute = await db.read(namespace, "127.0.0.1", shadow=False)
            assert attribute.count == 1
        finally:
            await db.delete(namespace, missing_ok=True)
