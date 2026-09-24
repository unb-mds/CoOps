"""Integration tests for MongoStorageAdapter against a real MongoDB.

Runs against the local stack from ``docker-compose.dev.yml`` (``make mongo-up``)
on ``MONGO_URI`` (default ``mongodb://localhost:27018``) — the same guard
``test_mongo_raw_store.py`` uses — and skips, naming the condition, when
MongoDB is not reachable, so CI without a database stays green. The unit
suite (``tests/unit/test_storage_datasets.py``) covers the query building
and tenant scoping against a fake collection; these are the "run it against
something real" tests for #39: the unique index exists on the real server,
and the tenant term isolates two tenants there.
"""

import os

import pytest

from coops.domain import TenantId
from coops.storage.datasets import INDEX_NAME, MongoStorageAdapter

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27018")
TEST_DATABASE = "coops_datasets_test"


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
def adapter():
    a = MongoStorageAdapter(MONGO_URI, database=TEST_DATABASE, collection="datasets")
    yield a
    a._client.drop_database(TEST_DATABASE)
    a.close()


def test_round_trip_and_tenant_isolation(adapter):
    adapter.save(TenantId("unb-mds"), "bronze", "issues_2099.1-Demo", [{"n": 1}])
    adapter.save(TenantId("other-org"), "bronze", "issues_2099.1-Demo", [{"n": 2}])

    loaded = adapter.load(TenantId("unb-mds"), "bronze", "issues_2099.1-Demo")
    assert loaded is not None
    assert loaded.tenant == TenantId("unb-mds")
    assert loaded.layer == "bronze"
    assert loaded.entity == "issues_2099.1-Demo"
    assert loaded.data == [{"n": 1}]

    other = adapter.load(TenantId("other-org"), "bronze", "issues_2099.1-Demo")
    assert other is not None
    assert other.data == [{"n": 2}]

    # The tenant filter isolates: a tenant with nothing at that address reads
    # nothing — not the other tenant's dataset — and lists nothing either.
    assert adapter.load(TenantId("third-org"), "bronze", "issues_2099.1-Demo") is None
    assert adapter.list(TenantId("third-org"), "bronze") == []
    assert adapter.list(TenantId("unb-mds"), "bronze") == ["issues_2099.1-Demo"]


def test_index_exists_on_the_three_tuple(adapter):
    indexes = {idx["name"]: idx for idx in adapter._collection.list_indexes()}
    assert INDEX_NAME in indexes

    key = list(indexes[INDEX_NAME]["key"].items())
    assert key == [
        ("tenant_id", 1),
        ("layer", 1),
        ("entity", 1),
    ]


def test_save_replaces_not_accumulates(adapter):
    adapter.save(TenantId("unb-mds"), "silver", "members_detailed", [{"m": 1}])
    adapter.save(TenantId("unb-mds"), "silver", "members_detailed", [{"m": 2}])

    loaded = adapter.load(TenantId("unb-mds"), "silver", "members_detailed")
    assert loaded is not None
    assert loaded.data == [{"m": 2}]
    # One document per key, not two.
    assert (
        adapter._collection.count_documents({"tenant_id": "unb-mds", "layer": "silver"})
        == 1
    )
