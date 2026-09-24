"""An issue: a conversation that is not a pull request.

GitHub serves issues and pull requests from the same paginated endpoint and
marks the difference with a ``pull_request`` object on the payload, so the
two entities share their field set. That shared set lives in the private
``_Conversation`` base here; :class:`PullRequest` extends it with the PR-only
facts. The base is not exported and is not part of the public vocabulary —
consume ``Issue`` or ``PullRequest``, never "whatever the endpoint sent".

Deliberately absent: ``body`` and ``milestone``, which no Silver consumer
reads and which carried every address ever found in the published data
(#133). The mapper builds issues from named fields for the same reason the
Bronze projection does — a whitelist is bounded by what we use; see
``docs/definition-of-done.md``.

``author``/``assignee`` are ``None`` when the account behind them was
deleted: an absent actor, not a person named "unknown".
"""

from __future__ import annotations

from dataclasses import dataclass

from coops.domain.models.actor import Actor
from coops.domain.tenancy import ProviderAccount, TenantId


@dataclass(frozen=True, slots=True)
class _Conversation:
    """Field set shared by issues and pull requests (not exported)."""

    tenant_id: TenantId
    account: ProviderAccount
    external_id: str
    repo_name: str
    number: int
    state: str
    title: str
    author: Actor | None = None
    assignee: Actor | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None

    def __post_init__(self) -> None:
        kind = type(self).__name__
        if not (self.external_id or "").strip():
            raise ValueError(f"{kind} requires a non-empty external_id")
        if not (self.repo_name or "").strip():
            raise ValueError(f"{kind} requires a non-empty repo_name")
        if self.number < 1:
            raise ValueError(f"{kind}.number must be >= 1, got {self.number}")
        if not (self.state or "").strip():
            raise ValueError(f"{kind} requires a non-empty state")


@dataclass(frozen=True, slots=True)
class Issue(_Conversation):
    """One issue on one repository."""
