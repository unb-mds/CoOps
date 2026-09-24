"""The storage port: tenant-scoped persistence for Medallion datasets.

This is the port the ETL layers move onto once #30 (Bronze) and #33/#34
(Silver/Gold) land; until then nothing consumes it. It generalises
:class:`coops.storage.raw.RawStore` — the project's first port, in
production since #113 — and "generalise" here means adopting the same shape
and the same guarantees over a **different key space**, not merging the two:

==================  ==============================  ==============================
                    ``RawStore`` (raw capture)      ``StoragePort`` (this port)
==================  ==============================  ==============================
key                 ``(provider, endpoint,          ``(layer, entity)``
                    ``params_hash)`` — an HTTP      — a Medallion dataset.
                    cache key.
unit                one captured provider response  one projected dataset
payload             provider-shaped, unmodified    already-projected data
                    (#111: raw keeps)               (the projection happened
                                                     in the caller)
==================  ==============================  ==============================

The guarantees carried over from ``RawStore``, because they are proven:

- **A `TenantId` is the first, required parameter of every method**, and the
  implementation scopes the query to it. There is no method that addresses
  datasets without one, so a caller cannot read another tenant's data by
  omitting a filter.
- **`load` returns a frozen domain type** (:class:`StoredDataset`), never a
  driver type. Nothing in ``domain/`` imports a database library; the
  drivers live behind the port (``MongoStorageAdapter``, #39).
- **Structural** (`typing.Protocol`), not an ABC: an implementation is any
  object with these methods.

Key-space decisions the issue text leaves open:

- **``layer``** is one of the three Medallion layers — ``Literal["bronze",
  "silver", "gold"]`` — matching what ``data/`` actually contains. The raw
  capture tier is deliberately *not* a layer here: it has its own port.
- **``entity``** is the dataset's name within the layer: the file stem on
  disk today, e.g. ``"issues_<repository>"`` (per-repository, dots and
  underscores included — ``"commits_2099.1-Demo.App"`` is a valid name),
  ``"members_detailed"`` (organisation-wide) or ``"executive_dashboard"``.
  A port that could not express the per-repository split could not express
  what is already on disk.

Projection stays with the caller (``docs/definition-of-done.md``): the port
has exactly one write path and it moves **already-projected** data. There is
deliberately no method that accepts a whole provider response — capture is
``RawStore``'s job, and the Bronze projection discipline
(``coops.bronze.issues._project_issue`` and friends) happens before ``save``.

The ``*_all.json`` aggregates are **not addressable** through this port.
``data/bronze/issues_all.json`` and its siblings repeat every
per-repository record of their entity kind: they are publish artifacts the
fork-and-forget step assembles, not datasets, and reading one alongside the
per-repository entities double-counts — a wrong number this project has
shipped three times. :func:`validate_entity` rejects the ``_all`` suffix on
both the write and the read side, so the shape cannot be expressed here at
all. Writing the aggregates stays with the publish step, outside the port.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, cast, runtime_checkable

from coops.domain.tenancy import TenantId

#: The three Medallion layers, as they exist under ``data/``.
Layer: TypeAlias = Literal["bronze", "silver", "gold"]

#: Runtime form of :data:`Layer`, for validators and implementations.
LAYERS: frozenset[str] = frozenset({"bronze", "silver", "gold"})

#: Plain JSON-shaped data — what both a file adapter (``json.dump``) and a
#: document adapter (BSON) can round-trip. The port exchanges only this:
#: never a driver type, never a live object.
JSONValue: TypeAlias = (
    None | bool | int | float | str | Sequence["JSONValue"] | Mapping[str, "JSONValue"]
)

#: Suffix of the publish aggregates (``issues_all.json`` …). Rejected by
#: :func:`validate_entity`; see the module docstring for why.
_ALL_SUFFIX = "_all"

#: Extension the file adapter owns. An entity carrying it could address one
#: dataset two ways (``issues_x`` and ``issues_x.json``).
_JSON_SUFFIX = ".json"


def validate_layer(layer: str) -> Layer:
    """Return ``layer`` when it names a Medallion layer, else raise.

    The raw capture tier is not a storage-port layer — it has its own port
    (:mod:`coops.storage.raw`) — so ``"raw"`` raises like any other unknown
    name instead of quietly addressing a second key space.
    """
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}: expected one of {sorted(LAYERS)}")
    return cast(Layer, layer)


def validate_entity(entity: str) -> str:
    """Return the trimmed entity name, or raise ``ValueError``.

    Implementations call this on every ``save``/``load``/``list`` so the
    rules hold regardless of the caller:

    - non-empty after trimming (the name is the whole address within the
      layer — there is nothing else to key on);
    - no path separators: a file adapter maps the name onto
      ``data/{layer}/{entity}.json``, and a separator would escape the
      layer's directory;
    - no ``.json`` extension: the extension belongs to the adapter;
    - no trailing ``_all``: publish aggregates repeat every per-repository
      record and are not datasets (module docstring).
    """
    name = (entity or "").strip()
    if not name:
        raise ValueError("entity name must be a non-empty string")
    if "/" in name or "\\" in name:
        raise ValueError(f"entity name {name!r} must not contain a path separator")
    if name.endswith(_JSON_SUFFIX):
        raise ValueError(
            f"entity name {name!r} must not carry the{_JSON_SUFFIX} extension"
        )
    if name.endswith(_ALL_SUFFIX):
        raise ValueError(
            f"entity {name!r} ends in {_ALL_SUFFIX!r}: publish aggregates "
            "repeat every per-repository record and are not addressable "
            "through StoragePort"
        )
    return name


@dataclass(frozen=True, slots=True)
class StoredDataset:
    """One dataset's contents at one ``(tenant, layer, entity)`` address.

    What ``load`` hands back — a frozen domain type in the style of
    ``RawDocument``/``tenancy.TenantId``, never a driver type. Frozen is
    shallow, as with ``RawDocument``: ``data`` is plain JSON data and may
    hold mutable lists inside.

    Construction validates the address (the ``tenancy.py`` pattern), so a
    dataset that could not have been saved cannot be handed back either.
    """

    tenant: TenantId
    layer: Layer
    entity: str
    data: JSONValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "layer", validate_layer(self.layer))
        object.__setattr__(self, "entity", validate_entity(self.entity))


@runtime_checkable
class StoragePort(Protocol):
    """Tenant-scoped persistence for Medallion-layer datasets.

    Every method requires a `TenantId` as its first parameter and the
    implementation scopes the query to it, so a caller cannot read another
    tenant's datasets by omitting a filter — there is no method that
    addresses datasets without one.

    ``list`` returns **entity names**, not datasets: enumerating a layer by
    loading it would materialise the whole tier, the ``(layer, entity)``
    key space makes names the natural unit of enumeration, and a caller
    that composes ``list`` + ``load`` goes through a tenant-scoped read on
    every step, so enumeration cannot bypass tenancy. Names come back
    sorted so a file adapter and a document adapter agree on ordering.
    """

    def save(
        self,
        tenant: TenantId,
        layer: Layer,
        entity: str,
        data: JSONValue,
    ) -> None:
        """Store (or replace) the dataset at ``(tenant, layer, entity)``.

        ``data`` is already-projected, JSON-shaped data. Re-saving an
        address replaces its dataset outright — there is no merge, so no
        call can accumulate a second copy of records that are already
        there.
        """
        ...

    def load(
        self,
        tenant: TenantId,
        layer: Layer,
        entity: str,
    ) -> StoredDataset | None:
        """Return the dataset at ``(tenant, layer, entity)``, or ``None``.

        Raises `ValueError` for an unknown layer or an invalid entity name
        (including the ``_all`` aggregates), so the double-count shape is
        unreadable even from legacy files that exist on disk.
        """
        ...

    def list(self, tenant: TenantId, layer: Layer) -> Sequence[str]:
        """Entity names stored under ``(tenant, layer)``, sorted.

        Only the names; see the class docstring for why. Never includes
        another tenant's entities, and never ``_all`` aggregates (they
        cannot be saved through this port in the first place).
        """
        ...
