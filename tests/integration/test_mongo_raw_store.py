"""Integration tests for MongoRawStore against a real MongoDB.

Runs against the local stack from ``docker-compose.dev.yml`` (``make mongo-up``)
on ``MONGO_URI`` (default ``mongodb://localhost:27018``). Skips — naming the
condition — when MongoDB is not reachable, so CI without a database stays green.

These are the "run it against something real" tests for #113: they prove the
index exists and that the tenant filter isolates two tenants, against the real
server rather than a fake collection.
"""

import os

import pytest

from coops.domain import TenantId
from coops.storage.raw import INDEX_NAME, MongoRawStore

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27018")
TEST_DATABASE = "coops_raw_test"


def _mongo_available() -> bool:
    import pymongo
    from pymongo.errors import PyMongoError

    try:
        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=1000)
        client.admin.command("ping")
        client.close()
        return True
    except PyMongoError:
        return False


pytestmark = pytest.mark.skipif(
    not _mongo_available(),
    reason=f"MongoDB not reachable at {MONGO_URI} (start it with `make mongo-up`)",
)


@pytest.fixture
def store():
    s = MongoRawStore(MONGO_URI, database=TEST_DATABASE, collection="raw_capture")
    yield s
    s._client.drop_database(TEST_DATABASE)
    s.close()


def test_round_trip_and_tenant_isolation(store):
    endpoint = "https://api.github.com/repos/unb-mds/CoOps/commits"

    store.save(
        TenantId("unb-mds"),
        "github",
        endpoint,
        {"per_page": "50", "page": "1"},
        '"etag-unb"',
        [{"sha": "unb-1"}],
    )
    store.save(
        TenantId("other-org"),
        "github",
        endpoint,
        {"per_page": "50", "page": "1"},
        '"etag-other"',
        [{"sha": "other-1"}],
    )

    doc = store.get(
        TenantId("unb-mds"), "github", endpoint, {"per_page": "50", "page": "1"}
    )
    assert doc is not None
    assert doc.tenant_id == "unb-mds"
    assert doc.provider == "github"
    assert doc.endpoint == endpoint
    assert doc.etag == '"etag-unb"'
    assert doc.payload == [{"sha": "unb-1"}]
    assert doc.fetched_at
    # The document shape is exactly the 7 fields from #113.
    assert set(doc.__dataclass_fields__) == {
        "tenant_id",
        "provider",
        "endpoint",
        "params_hash",
        "etag",
        "fetched_at",
        "payload",
    }

    other = store.get(
        TenantId("other-org"), "github", endpoint, {"per_page": "50", "page": "1"}
    )
    assert other is not None
    assert other.payload == [{"sha": "other-1"}]

    # The tenant filter isolates: a tenant cannot read another's documents even
    # for the identical endpoint/params.
    assert (
        store.get(
            TenantId("third-org"), "github", endpoint, {"per_page": "50", "page": "1"}
        )
        is None
    )
    assert (
        store.get(
            TenantId("unb-mds"), "github", endpoint, {"per_page": "50", "page": "2"}
        )
        is None
    )


def test_index_exists_on_the_four_tuple(store):
    indexes = {idx["name"]: idx for idx in store._collection.list_indexes()}
    assert INDEX_NAME in indexes

    key = list(indexes[INDEX_NAME]["key"].items())
    assert key == [
        ("tenant_id", 1),
        ("provider", 1),
        ("endpoint", 1),
        ("params_hash", 1),
    ]


def test_upsert_replaces_same_key(store):
    endpoint = "https://api.github.com/repos/unb-mds/CoOps/issues"
    store.save(TenantId("unb-mds"), "github", endpoint, {}, None, {"state": "open"})
    store.save(TenantId("unb-mds"), "github", endpoint, {}, None, {"state": "closed"})

    doc = store.get(TenantId("unb-mds"), "github", endpoint, {})
    assert doc.payload == {"state": "closed"}
    # One document per key, not two.
    assert store._collection.count_documents({"tenant_id": "unb-mds"}) == 1
