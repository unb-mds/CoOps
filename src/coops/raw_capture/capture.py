"""Managed capture of the raw GitHub API corpus (corpus-raw).

The pipeline talks to GitHub through :class:`coops.utils.github_api.GitHubAPIClient`.
When capture is enabled (``capture_dir`` + ``tenant_id``), every REST ``200``
and GraphQL ``200`` response is written here as one JSON record in the capture
shape #113 indexes on::

    {
      "tenant_id": "unb-mds",
      "provider":   "github",
      "endpoint":   "/repos/unb-mds/coops/commits",
      "params":     {"per_page": "50", "page": "2"},
      "etag":       "\"abc123\"",
      "fetched_at": "2026-09-22T12:00:00+00:00",
      "payload":    { ... the unmodified response body ... }
    }

REST endpoints carry the URL path in ``endpoint`` and the query string parsed
into ``params``. GraphQL responses use ``endpoint = "graphql"`` and put the
query text and variables in ``params``; they have no ETag, so ``etag`` is
``null``.

Privacy and retention
---------------------

The payload is the *unmodified* API body. It contains personal data — the
measured ``unb-mds`` corpus has an email address in 1859 of 4737 files, and
``/users/{login}`` also carries ``location``, ``bio`` and ``company``. It is
therefore PRIVATE and must never leave the machine:

* records are written under a ``700`` directory tree as ``600`` files;
* one directory per tenant (``<root>/<tenant_id>/``), so tenants never mix;
* ``prune`` enforces the stated retention policy — records whose
  ``fetched_at`` is older than the policy are deleted.

``corpus-fixtures`` (the sanitized, shareable twin) is produced from these
records by :mod:`coops.raw_capture.sanitize`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, Optional, Tuple

DEFAULT_PROVIDER = "github"

DIR_MODE = 0o700
FILE_MODE = 0o600


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    """One raw API response plus the request that produced it."""

    tenant_id: str
    provider: str
    endpoint: str
    params: Dict[str, Any]
    etag: Optional[str]
    fetched_at: str
    payload: Any

    def to_dict(self) -> Dict[str, Any]:
        """The serialised capture shape, keys in index order."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CaptureRecord":
        return cls(
            tenant_id=data["tenant_id"],
            provider=data.get("provider") or DEFAULT_PROVIDER,
            endpoint=data["endpoint"],
            params=data.get("params") or {},
            etag=data.get("etag"),
            fetched_at=data["fetched_at"],
            payload=data["payload"],
        )


class RawCaptureWriter:
    """Writes capture records tenant-scoped, mode 700/600, with retention.

    One record per ``(provider, endpoint, params)``; a later fetch of the same
    request replaces the earlier record, mirroring the cache's overwrite-on-200
    semantics, so a snapshot is always the *current* state of the corpus.
    """

    def __init__(
        self,
        root_dir: str,
        tenant_id: str,
        provider: str = DEFAULT_PROVIDER,
    ) -> None:
        self.root_dir = os.path.abspath(root_dir)
        self.tenant_id = tenant_id
        self.provider = provider
        self.tenant_dir = os.path.join(self.root_dir, tenant_id)

    @staticmethod
    def record_key(provider: str, endpoint: str, params: Optional[Dict[str, Any]]) -> str:
        """Deterministic filename key for a request (endpoint + params)."""
        canonical = json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(
            f"{provider}:{endpoint}:{canonical}".encode("utf-8")
        ).hexdigest()

    def _ensure_private_dirs(self) -> None:
        os.makedirs(self.tenant_dir, exist_ok=True)
        # A tree left over from an earlier run may be looser; tighten it before
        # a single payload is written into it. Chmod only directories we manage.
        os.chmod(self.root_dir, DIR_MODE)
        os.chmod(self.tenant_dir, DIR_MODE)

    def write(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]],
        etag: Optional[str],
        payload: Any,
        fetched_at: Optional[str] = None,
    ) -> CaptureRecord:
        self._ensure_private_dirs()
        record = CaptureRecord(
            tenant_id=self.tenant_id,
            provider=self.provider,
            endpoint=endpoint,
            params=params or {},
            etag=etag,
            fetched_at=fetched_at or _utc_now_iso(),
            payload=payload,
        )
        key = self.record_key(self.provider, endpoint, params)
        path = os.path.join(self.tenant_dir, f"{key}.json")
        # Write to a temp file with the private mode set before it is renamed
        # into place, so there is never a world-readable record on disk.
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)
        return record

    def record_files(self) -> list:
        if not os.path.isdir(self.tenant_dir):
            return []
        return sorted(
            name for name in os.listdir(self.tenant_dir) if name.endswith(".json")
        )

    def iter_records(self) -> Iterator[Tuple[str, CaptureRecord]]:
        for name in self.record_files():
            path = os.path.join(self.tenant_dir, name)
            with open(path, encoding="utf-8") as f:
                yield name, CaptureRecord.from_dict(json.load(f))

    def prune(self, max_age_days: int) -> int:
        """Delete records older than ``max_age_days``; return how many were removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        removed = 0
        for name, record in self.iter_records():
            fetched = _parse_fetched_at(record.fetched_at)
            if fetched is not None and fetched < cutoff:
                os.remove(os.path.join(self.tenant_dir, name))
                removed += 1
        return removed


def _parse_fetched_at(value: str) -> Optional[datetime]:
    """Parse an ISO timestamp, tolerating the ``Z`` suffix (3.10 lacks it)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def prune_directory(root_dir: str, max_age_days: int) -> int:
    """Prune every tenant under ``root_dir``; return the total records removed."""
    if not os.path.isdir(root_dir):
        return 0
    total = 0
    for name in sorted(os.listdir(root_dir)):
        if not os.path.isdir(os.path.join(root_dir, name)):
            continue
        total += RawCaptureWriter(root_dir, name).prune(max_age_days)
    return total
