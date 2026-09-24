"""Per-repository extraction watermarks (issue #110).

A watermark is the small amount of state that lets the next Bronze run ask the
GitHub API only for what changed since the last run, instead of re-fetching
everything. One record per repository:

* ``last_run``            — ISO timestamp of the previous run's start (used as
  the ``since`` bound for commit extraction on every branch).
* ``head_shas``           — ``{branch: head_sha}``, so tree extraction can be
  skipped when a branch head has not moved.
* ``last_event_id`` — the newest issue event id seen, so only newer events are
  fetched (by paging from the newest event, since the endpoint has no ``since``
  filter) and appended.
* ``last_updated_at``     — the newest ``updated_at`` seen across issues and
  PRs, so the REST ``since`` filter returns only changed items, which are then
  merged by number.

The store is a plain JSON file in the working directory (a sibling of ``data/``
and ``cache/``, *not* inside either). It holds no API payloads and no personal
data — only shas, numeric ids and timestamps — so it is not scrub input and it
does not flow through the Bronze scrub. Everything that *does* reach
``data/bronze/`` (issues, PRs, events, commits) still goes through the normal
projection/sanitization on every path, full and incremental alike.

Watermarks compose with the other caching layers rather than bypassing them:

* **ETag revalidation (#126)** — every fetch still goes through
  ``get_with_cache``/``get_paginated``, so a ``since=…`` URL or a branch-head
  probe is cached and revalidated like any other request.
* **Raw layer (#136)** — a watermark only changes *which* URLs are requested; a
  fresh raw document still short-circuits those URLs, and a stale one still
  falls through to the API. A watermark never makes a stale raw read look
  up-to-date: freshness is governed by the raw layer's own clock, not by the
  watermark.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

#: Default on-disk location, relative to the working directory (like ``data/``
#: and ``cache/``). Deliberately outside ``data/`` so a watermark change never
#: shows up in a ``data/`` diff, and outside ``cache/`` so the raw corpus keeps
#: holding only unmodified API bodies.
DEFAULT_PATH = "watermarks.json"

#: Schema version of the on-disk file; bump when the shape changes.
VERSION = 1


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a GitHub-style ISO timestamp to an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def max_iso(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Return the later of two ISO timestamps (the original string)."""
    da = _parse_iso(a)
    db = _parse_iso(b)
    if da is None:
        return b
    if db is None:
        return a
    return a if da >= db else b


def query_since(iso: Optional[str]) -> Optional[str]:
    """Return ``iso`` minus one second, for use as a REST ``since`` value.

    GitHub's ``since`` filters are exclusive (``updated_at``/``created_at``
    *after* the given time) and the provider timestamps are second-resolution,
    so two items can share the boundary second. Subtracting a second makes the
    query over-fetch by that final second, which the merge-by-number /
    append-by-id logic then de-duplicates. This is what makes "a run after
    known activity" produce the same records as a full extraction even when the
    activity lands on the boundary second.
    """
    dt = _parse_iso(iso)
    if dt is None:
        return iso
    shifted = dt - timedelta(seconds=1)
    return shifted.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RepoWatermark:
    """Extraction watermark for one repository."""

    repo: str
    last_run: Optional[str] = None
    head_shas: Dict[str, str] = field(default_factory=dict)
    last_event_id: Optional[int] = None
    last_updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("repo")
        return data

    @classmethod
    def from_dict(cls, repo: str, data: dict) -> "RepoWatermark":
        return cls(
            repo=repo,
            last_run=data.get("last_run"),
            head_shas=dict(data.get("head_shas") or {}),
            last_event_id=data.get("last_event_id"),
            last_updated_at=data.get("last_updated_at"),
        )


class WatermarkStore:
    """Load, mutate and persist the per-repository watermark records.

    A fresh (or missing) file yields an empty store; ``get`` for an unknown
    repository returns a blank watermark, which drives a full extraction for
    that repository. Only repositories that are ``update``d are persisted, so a
    run that does not touch a repository does not write an empty record for it.
    """

    def __init__(self, path: str = DEFAULT_PATH, now: Optional[datetime] = None) -> None:
        self.path = path
        # `last_run` records the *start* of the current run, so the next run's
        # `since = last_run` over-fetches the tail of the previous run rather
        # than under-fetching anything committed while it was still running.
        # Stored as a second-resolution UTC `Z` timestamp: the value is spliced
        # into REST query strings, where a `+00:00` offset (and microseconds)
        # would be mangled by URL parsing.
        self._now_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._records: Dict[str, RepoWatermark] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            # A corrupt watermark file must not stop extraction; treat it as
            # absent so the next run performs a full extraction.
            return
        repos = raw.get("repos", {}) if isinstance(raw, dict) else {}
        for repo, data in repos.items():
            if isinstance(data, dict):
                self._records[repo] = RepoWatermark.from_dict(repo, data)

    def get(self, repo: str) -> RepoWatermark:
        """Return the stored watermark for ``repo``, or a blank one."""
        return self._records.get(repo, RepoWatermark(repo=repo))

    def update(self, repo: str, **fields) -> RepoWatermark:
        """Set the given fields on the watermark for ``repo`` and persist it.

        ``None`` values are ignored so a missing observation cannot erase an
        earlier one. ``head_shas`` is replaced as a whole (callers merge branch
        shas before calling).

        ``last_run`` is deliberately *not* set here: it marks when the previous
        run started, and extractors run in order, so a later extractor must
        still see the previous run's ``last_run`` (not the one an earlier
        extractor would stamp on the current run). It is applied to every
        touched repository once, at :meth:`save`.
        """
        wm = self.get(repo)
        for key, value in fields.items():
            if value is None:
                continue
            setattr(wm, key, value)
        self._records[repo] = wm
        return wm

    def save(self) -> None:
        """Write the current records to disk, stamping ``last_run``.

        ``last_run`` is set to this run's start for every touched repository,
        which is what the next run reads as its ``since`` bound. Best-effort:
        a failure to persist only means the next run re-fetches a little more.
        """
        payload = {"version": VERSION, "repos": {}}
        for repo, wm in self._records.items():
            wm.last_run = self._now_iso
            payload["repos"][repo] = wm.to_dict()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
