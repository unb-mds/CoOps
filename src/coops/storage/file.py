"""Local-filesystem ``StoragePort`` adapter: one directory tree per tenant.

``FileStorageAdapter`` (issue #41) is the filesystem implementation of
:class:`coops.domain.ports.storage_port.StoragePort` — the plain adapter for
development and the regression harness, and the driver #30 wants Bronze's
ported output compared against, so the bytes it produces stay comparable to
the corpus the pipeline writes today (``json.dump`` with ``indent=2``,
``ensure_ascii=False``, like every other JSON writer in the ETL).

Filesystem mapping — the decision the issue leaves open:

- **A tenant is one directory tree.** The tenant's slug is a directory
  directly under the root (mirroring the raw-capture layout
  ``<root>/<tenant_id>/``), with the layer directories and dataset files
  inside it::

      <root>/<tenant-slug>/<layer>/<entity>.json

  That makes tenant isolation structural, not a query filter: every path
  the adapter addresses is built by joining *this call's* ``TenantId`` onto
  the root, so a call made with tenant A's id physically cannot reach
  tenant B's subtree, and two tenants never share a file. Slugs compare
  exactly (#92), so two spellings are two trees.

- **A tenant that was never written reads as empty.** ``load`` returns
  ``None`` and ``list`` returns ``[]`` when nothing exists under that
  slug — the same answer as an empty tenant — and the read paths never
  create anything. There is no "tenant not found" error, because the port
  has no tenant registry: absence *is* the answer.

The slug is checked to be a *single directory name* — no path separators,
not ``.`` or ``..`` — before any path is built. ``TenantId`` normalises by
trimming only and deliberately constrains no character set (#92), so this
adapter — not the domain — owns the rule: a slug like ``"../org-b"`` would
otherwise address another tenant's tree, which is exactly the leak the port
exists to prevent.

The ``*_all`` publish aggregates are rejected on every path — read and
write alike — because every method routes its address through
``validate_entity``/``validate_layer``, exactly as the port requires. That
includes files a pre-#170 run left on disk: ``load`` raises for them even
though the file exists, and ``list`` never surfaces them.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from coops.domain import TenantId
from coops.domain.ports.storage_port import (
    JSONValue,
    Layer,
    StoragePort,
    StoredDataset,
    validate_entity,
    validate_layer,
)

#: Extension this adapter owns. The port already rejects entity names
#: carrying it, so one dataset is addressable exactly one way.
_JSON_SUFFIX = ".json"

#: Suffix of the staging file for the atomic replace. It is not a dataset,
#: and the ``*.json`` enumeration in :meth:`FileStorageAdapter.list`
#: cannot match it.
_TMP_SUFFIX = ".tmp"


def _single_directory_name(slug: str) -> str:
    """Return ``slug`` when it names one path component, else raise.

    The tenant tree is ``<root>/<slug>/…``; a separator or a dot-name would
    escape the tenant's own subtree — into another tenant's, or out of the
    root entirely — so such a slug cannot address storage at all.
    """
    if "/" in slug or "\\" in slug or slug in (".", ".."):
        raise ValueError(f"tenant slug {slug!r} must be a single directory name")
    return slug


class FileStorageAdapter:
    """``StoragePort`` over a local directory: one tree per tenant.

    See the module docstring for the tenant → directory mapping and the
    never-written-tenant behaviour. The port's contract — tenant isolation
    above all — holds by construction: every method builds its path from
    its own ``tenant`` argument, and nothing here touches the filesystem
    without one.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        #: Base directory; created lazily by ``save`` only. Reads against a
        #: root that does not exist answer "nothing", not an error.
        self._root = Path(root)

    def _layer_dir(self, tenant: TenantId, layer: str) -> Path:
        """The tenant's layer directory — the isolation boundary of this
        adapter: the slug is the first path component of every address."""
        slug = _single_directory_name(str(tenant))
        return self._root / slug / validate_layer(layer)

    def _path(self, tenant: TenantId, layer: str, entity: str) -> Path:
        # Validation before any filesystem effect: an address the port
        # rejects raises here, on every method, so a rejected save leaves
        # nothing behind and a rejected load reads nothing.
        return (
            self._layer_dir(tenant, layer) / f"{validate_entity(entity)}{_JSON_SUFFIX}"
        )

    def save(
        self,
        tenant: TenantId,
        layer: Layer,
        entity: str,
        data: JSONValue,
    ) -> None:
        """Write the dataset at ``(tenant, layer, entity)``, replacing it.

        The file is staged as ``<entity>.json.tmp`` and moved into place
        with :func:`os.replace`, so a reader never observes a half-written
        dataset and a crash cannot truncate the previous one.
        """
        path = self._path(tenant, layer, entity)
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f"{path.name}{_TMP_SUFFIX}")
        with staged.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
        os.replace(staged, path)

    def load(
        self,
        tenant: TenantId,
        layer: Layer,
        entity: str,
    ) -> StoredDataset | None:
        """Return the dataset at ``(tenant, layer, entity)``, or ``None``.

        ``None`` — not an error — when nothing was ever written there,
        including the whole-tenant case where the directory does not exist.
        """
        path = self._path(tenant, layer, entity)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return StoredDataset(
            tenant=tenant, layer=layer, entity=entity, data=json.loads(raw)
        )

    def list(self, tenant: TenantId, layer: Layer) -> Sequence[str]:
        """Entity names stored under ``(tenant, layer)``, sorted.

        Only files this adapter could have written are enumerated: the
        ``*.json`` stems that :func:`validate_entity` accepts. A legacy
        ``<name>_all.json`` a pre-#170 run left in the tree is skipped
        rather than surfaced — the aggregate is not addressable through
        the port on the read side any more than on the write side.
        """
        directory = self._layer_dir(tenant, layer)
        if not directory.is_dir():
            return []
        names: list[str] = []
        for candidate in sorted(directory.glob(f"*{_JSON_SUFFIX}")):
            if not candidate.is_file():
                continue
            name = candidate.name[: -len(_JSON_SUFFIX)]
            try:
                names.append(validate_entity(name))
            except ValueError:
                continue
        return sorted(names)


if TYPE_CHECKING:
    #: Compile-time port conformance (the static half of the #91 contract):
    #: binding the adapter to its port makes any signature drift — a
    #: dropped or retyped ``tenant`` parameter — a mypy error, even though
    #: no runtime caller assigns it to ``StoragePort`` until #30 lands.
    #: Constructing the adapter touches no filesystem: ``__init__`` only
    #: stores the path.
    _conforms_to_storage_port: StoragePort = FileStorageAdapter(".")
