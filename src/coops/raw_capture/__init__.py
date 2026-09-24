"""Managed capture of the raw GitHub API corpus (issue #109).

Two artifacts, never one:

* **corpus-raw** — the full, unmodified API payloads in the capture shape
  ``{tenant_id, provider, endpoint, params, etag, fetched_at, payload}``.
  PRIVATE: it contains personal data (email addresses, full ``/users``
  profiles) and must never be published, committed, uploaded or attached to
  an issue or PR. Kept mode ``700``/``600``, tenant-scoped, with a stated
  retention policy. See :mod:`coops.raw_capture.capture`.

* **corpus-fixtures** — the same shape with the personal data removed, safe
  to share and copy into other repositories (the mapper, regression
  fixtures). See :mod:`coops.raw_capture.sanitize`.
"""

from .capture import CaptureRecord, RawCaptureWriter, prune_directory

__all__ = ["CaptureRecord", "RawCaptureWriter", "prune_directory"]
