"""Dataset storage: the MongoDB adapter behind ``StoragePort`` (#39).

A dataset document is the port's address plus the already-projected data::

    {tenant_id, layer, entity, data}

``tenant_id`` is enforced *here*, in the adapter, not by the callers: every
read and write is scoped to the ``TenantId`` that is passed in, and there is
no method that can address a dataset without one. This is the same boundary
``MongoRawStore`` proved in production since #113, carried over the port's
``(layer, entity)`` key space instead of the raw capture key.

The compound index is ``(tenant_id, layer, entity)``, unique — not the
``{org_id, entity}`` #39 originally asked for. That issue text predates the
port design: the port's key space is ``(layer, entity)`` and the codebase's
vocabulary is ``tenant_id``, and an index without ``layer`` would collide
``bronze/members_detailed`` with ``silver/members_detailed`` — two datasets
that must coexist. Without the tenant term there would be no isolation to
index at all.

The real MongoDB behind this adapter is covered by
``tests/integration/test_mongo_raw_store.py``'s sibling pattern; the unit
suite runs the adapter against a fake pymongo collection so the query
building and tenant scoping are exercised with no database and no network.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

import pymongo

from coops.domain import TenantId
from coops.domain.ports.storage_port import (
    JSONValue,
    Layer,
    StoragePort,
    StoredDataset,
    validate_entity,
    validate_layer,
)

#: Default database/collection, matching ``MongoRawStore`` and the local stack
#: in ``docker-compose.dev.yml``. The collection is named ``datasets`` because
#: it holds Medallion datasets (one document per ``(tenant, layer, entity)``),
#: not raw captures: the two key spaces do not mix in one collection.
DEFAULT_DATABASE = "coops"
DEFAULT_COLLECTION = "datasets"

#: Index name for the (tenant_id, layer, entity) index.
INDEX_NAME = "tenant_layer_entity"


class MongoStorageAdapter:
    """``StoragePort`` backed by MongoDB (see ``docker-compose.dev.yml``).

    The client connects lazily, but the index is created eagerly so a fresh
    store is immediately queryable and the index can be verified with
    ``list_indexes()`` — the ``MongoRawStore`` pattern, unchanged.
    """

    def __init__(
        self,
        uri: str | None = None,
        database: str = DEFAULT_DATABASE,
        collection: str = DEFAULT_COLLECTION,
        *,
        client: Any = None,
        server_selection_timeout_ms: int = 2000,
    ) -> None:
        # `client` is an injection point for tests: a fake pymongo client lets
        # the save/load/list logic run against an in-memory collection.
        if client is None:
            if not uri:
                raise ValueError(
                    "MongoStorageAdapter requires a uri or an injected client"
                )
            client = pymongo.MongoClient(
                uri,
                serverSelectionTimeoutMS=server_selection_timeout_ms,
            )
        self._client = client
        self._collection = client[database][collection]
        # The 3-tuple is the document identity, so the index covers exactly
        # it: tenant first (the isolation axis), then the port's key space.
        # `layer` is not optional — see the module docstring for the
        # bronze/silver collision an index without it would invite.
        self._collection.create_index(
            [
                ("tenant_id", pymongo.ASCENDING),
                ("layer", pymongo.ASCENDING),
                ("entity", pymongo.ASCENDING),
            ],
            unique=True,
            name=INDEX_NAME,
        )

    def save(
        self,
        tenant: TenantId,
        layer: Layer,
        entity: str,
        data: JSONValue,
    ) -> None:
        """Store (or replace) the dataset at ``(tenant, layer, entity)``."""
        tenant_id = str(tenant)
        layer = validate_layer(layer)
        entity = validate_entity(entity)
        document = {
            "tenant_id": tenant_id,
            "layer": layer,
            "entity": entity,
            "data": data,
        }
        # The 3-tuple is the document identity, so upsert on exactly that key:
        # a re-save replaces outright and cannot accumulate a second copy.
        # `tenant_id` is injected here, never read from the data or a caller.
        self._collection.replace_one(
            {
                "tenant_id": tenant_id,
                "layer": layer,
                "entity": entity,
            },
            document,
            upsert=True,
        )

    def load(
        self,
        tenant: TenantId,
        layer: Layer,
        entity: str,
    ) -> StoredDataset | None:
        """Return the dataset at ``(tenant, layer, entity)``, or ``None``."""
        tenant_id = str(tenant)
        layer = validate_layer(layer)
        entity = validate_entity(entity)
        document = self._collection.find_one(
            {
                "tenant_id": tenant_id,
                "layer": layer,
                "entity": entity,
            }
        )
        if document is None:
            return None
        # The tenant handed back is the one asked for — a domain type, never
        # the stored string and never a driver document.
        return StoredDataset(
            tenant=tenant,
            layer=layer,
            entity=entity,
            data=document["data"],
        )

    def list(self, tenant: TenantId, layer: Layer) -> list[str]:
        """Entity names stored under ``(tenant, layer)``, sorted."""
        tenant_id = str(tenant)
        layer = validate_layer(layer)
        cursor = self._collection.find(
            {"tenant_id": tenant_id, "layer": layer},
            {"entity": 1, "_id": 0},
        )
        # Sorted in Python, not by the server: the port promises an order a
        # file adapter and a document adapter agree on, and codepoint order
        # is the one thing both can compute identically regardless of the
        # database's collation.
        return sorted(document["entity"] for document in cursor)

    def close(self) -> None:
        self._client.close()


if TYPE_CHECKING:
    #: Static conformance anchor, the twin of the one in ``file.py``. Widening
    #: mypy to cover ``src/coops/storage`` checks this module's own types; it
    #: does **not** by itself assert that the adapter satisfies the port.
    #: Measured 2026-09-24: renaming ``list`` to ``list_entities`` here — an
    #: adapter that no longer implements ``StoragePort`` — left mypy reporting
    #: "Success: no issues found", while the same rename in ``file.py`` failed
    #: on its anchor. This is what makes a drift a type error.
    #:
    #: A function rather than ``file.py``'s instance assignment: constructing
    #: ``FileStorageAdapter(".")`` only stores a path, but ``MongoStorageAdapter``
    #: requires a uri or a client, so an instance cannot be built for free here.
    def _conforms_to_storage_port(adapter: "MongoStorageAdapter") -> StoragePort:
        return adapter
