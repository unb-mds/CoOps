"""A snapshot of one repository branch's paths.

Entries use Git's own object vocabulary — ``blob``, ``tree``, ``commit``
(a submodule) — which is provider-neutral here: both the REST Git Trees API
and GraphQL ``TreeEntry`` speak it. REST and GraphQL disagree on what an
entry carries, so the optional fields are the union of the two:

- REST gives ``sha`` and ``mode`` for every entry and ``size`` for blobs,
  but says nothing about binary content.
- GraphQL gives ``oid``/``byteSize``/``isBinary`` for blobs only (the tree
  walk inlines ``... on Blob``), so a directory entry has ``sha=None``.

A provider response with ``truncated: true`` is never mapped: the extraction
layer falls back to a complete GraphQL tree instead of keeping a partial
one (see ``coops.bronze.repository_structure``), so a ``FileTree`` claims
completeness by existing.
"""

from __future__ import annotations

from dataclasses import dataclass

from coops.domain.tenancy import ProviderAccount, TenantId

#: The entry kinds both provider shapes use, in Git's vocabulary.
ENTRY_KINDS = frozenset({"blob", "tree", "commit"})


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One path in a repository tree."""

    path: str
    kind: str
    sha: str | None = None
    mode: str | None = None
    size: int | None = None
    is_binary: bool | None = None

    def __post_init__(self) -> None:
        if not (self.path or "").strip():
            raise ValueError("FileEntry requires a non-empty path")
        if self.kind not in ENTRY_KINDS:
            raise ValueError(
                f"FileEntry.kind must be one of {sorted(ENTRY_KINDS)},"
                f" got {self.kind!r}"
            )


@dataclass(frozen=True, slots=True)
class FileTree:
    """The paths of one repository branch, at one point in its history.

    ``sha`` is the tree's commit sha (REST response) and ``None`` for the
    per-level entries the GraphQL walk produces; ``external_id`` carries
    the same value under the storage contract (the record's key within the
    account, #39 — see :mod:`coops.domain.models`), and is likewise
    ``None`` when the level walked carries no tree sha. ``entries`` is a
    tuple so the tree is immutable like every other domain model.
    """

    tenant_id: TenantId
    account: ProviderAccount
    repo_name: str
    branch: str | None = None
    sha: str | None = None
    external_id: str | None = None
    entries: tuple[FileEntry, ...] = ()

    def __post_init__(self) -> None:
        if not (self.repo_name or "").strip():
            raise ValueError("FileTree requires a non-empty repo_name")
        if self.external_id is not None and not self.external_id.strip():
            raise ValueError(
                "FileTree.external_id must be None or non-blank, never a placeholder"
            )
