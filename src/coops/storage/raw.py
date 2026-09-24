"""Raw-capture storage: a tenant-scoped, provider-shaped home for API payloads.

The raw layer is the durable, queryable sibling of the on-disk ``cache/``
corpus. A raw document is provider-shaped and carries no domain model::

    {tenant_id, provider, endpoint, params_hash, etag, fetched_at, payload}

``tenant_id`` is enforced *here*, in the adapter, not by the callers: every
read and write is scoped to the ``TenantId`` that is passed in, and there is no
method that can address documents without one. This is the boundary that keeps
issue #113 able to sit behind the future StoragePort (#23) without rewriting
the callers.

The ``payload`` keeps the full, unmodified API representation — including
personal data such as commit author/committer email addresses. That is
deliberate (#111): the raw tier is what makes re-keying possible later. The
scrub lives at the *Bronze* projection (``coops.bronze.commits._sanitize_commit``),
never here. Raw keeps, Bronze drops.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import pymongo

from coops.domain import TenantId
from coops.domain.tenancy import (
    PROVIDER_GITHUB as PROVIDER_GITHUB,  # noqa: PLC0414 — explicit re-export
)

#: Provider discriminator for the raw documents captured from the GitHub API.
#: Owned by the domain since #92; re-exported here because callers (and the
#: raw document schema) import it from this module.

#: Default database/collection, matching the local stack in
#: ``docker-compose.dev.yml`` and ``scripts/load_mongo_snapshot.sh``. The
#: collection is named ``raw_capture`` — not ``raw`` — because
#: ``make mongo-load-raw`` already imports the loose ``cache/*.json`` corpus
#: into a ``raw`` collection whose documents do *not* carry this shape; the two
#: must not be mixed in one collection.
DEFAULT_DATABASE = "coops"
DEFAULT_COLLECTION = "raw_capture"

#: Index name for the (tenant_id, provider, endpoint, params_hash) index.
INDEX_NAME = "tenant_provider_endpoint_params"


def raw_params_hash(params: Mapping[str, Any] | None) -> str:
    """Stable SHA-256 of the request parameters.

    Keys are sorted and values serialised through ``str`` so the hash does not
    depend on dict insertion order or on whether a caller passed ``"1"`` or
    ``1`` for the same value. ``None`` and ``{}`` hash identically.
    """
    canonical = json.dumps(
        dict(params or {}),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_fresh(
    fetched_at: str,
    max_age_seconds: float | None,
    now: datetime | None = None,
) -> bool:
    """True when ``fetched_at`` is within ``max_age_seconds`` of ``now``.

    ``max_age_seconds=None`` means "always fresh" — the document is read no
    matter its age. A missing or malformed timestamp is never fresh.
    """
    if max_age_seconds is None:
        return True
    try:
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    reference = now if now is not None else _utcnow()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - fetched) <= timedelta(seconds=max_age_seconds)


@dataclass(frozen=True)
class RawDocument:
    """One captured provider response as stored in the raw layer."""

    tenant_id: str
    provider: str
    endpoint: str
    params_hash: str
    etag: str | None
    fetched_at: str
    payload: Any


class RawStore(Protocol):
    """Thin port for the raw layer (the real StoragePort arrives with #23).

    Every method requires a ``TenantId`` and the implementation scopes the
    query to it, so a caller cannot read another tenant's documents by omitting
    a filter — there is no method that addresses documents without one.
    """

    def save(
        self,
        tenant: TenantId,
        provider: str,
        endpoint: str,
        params: Mapping[str, Any] | None,
        etag: str | None,
        payload: Any,
    ) -> None:
        """Store (or replace) the captured payload for the given key."""
        ...

    def get(
        self,
        tenant: TenantId,
        provider: str,
        endpoint: str,
        params: Mapping[str, Any] | None,
    ) -> RawDocument | None:
        """Return the stored document for the given key, or ``None``."""
        ...


class MongoRawStore:
    """``RawStore`` backed by MongoDB (see ``docker-compose.dev.yml``).

    The client connects lazily, but the index is created eagerly so a fresh
    store is immediately queryable and the index can be verified with
    ``list_indexes()``.
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
        # the save/get logic run against an in-memory collection.
        if client is None:
            if not uri:
                raise ValueError("MongoRawStore requires a uri or an injected client")
            client = pymongo.MongoClient(
                uri,
                serverSelectionTimeoutMS=server_selection_timeout_ms,
            )
        self._client = client
        self._collection = client[database][collection]
        self._collection.create_index(
            [
                ("tenant_id", pymongo.ASCENDING),
                ("provider", pymongo.ASCENDING),
                ("endpoint", pymongo.ASCENDING),
                ("params_hash", pymongo.ASCENDING),
            ],
            unique=True,
            name=INDEX_NAME,
        )

    def save(
        self,
        tenant: TenantId,
        provider: str,
        endpoint: str,
        params: Mapping[str, Any] | None,
        etag: str | None,
        payload: Any,
    ) -> None:
        tenant_id = str(tenant)
        params_hash = raw_params_hash(params)
        document = {
            "tenant_id": tenant_id,
            "provider": provider,
            "endpoint": endpoint,
            "params_hash": params_hash,
            "etag": etag,
            "fetched_at": _utcnow().isoformat(),
            "payload": payload,
        }
        # The 4-tuple is the document identity, so upsert on exactly that key.
        # `tenant_id` is injected here, never read from the payload or a caller.
        self._collection.replace_one(
            {
                "tenant_id": tenant_id,
                "provider": provider,
                "endpoint": endpoint,
                "params_hash": params_hash,
            },
            document,
            upsert=True,
        )

    def get(
        self,
        tenant: TenantId,
        provider: str,
        endpoint: str,
        params: Mapping[str, Any] | None,
    ) -> RawDocument | None:
        tenant_id = str(tenant)
        params_hash = raw_params_hash(params)
        document = self._collection.find_one(
            {
                "tenant_id": tenant_id,
                "provider": provider,
                "endpoint": endpoint,
                "params_hash": params_hash,
            }
        )
        if document is None:
            return None
        return RawDocument(
            tenant_id=document["tenant_id"],
            provider=document["provider"],
            endpoint=document["endpoint"],
            params_hash=document["params_hash"],
            etag=document.get("etag"),
            fetched_at=document["fetched_at"],
            payload=document["payload"],
        )

    def close(self) -> None:
        self._client.close()
