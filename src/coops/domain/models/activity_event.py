"""One entry from a repository's event timeline.

Issue events are the activity stream behind issues and pull requests
(cross-referenced, closed, assigned, ...). ``actor`` is ``None`` when the
account that performed the action was deleted — measured: 957 event actors
in the corpus are literally ``null``. An absent actor is modelled as an
absent actor, never as a Member named "unknown": a placeholder would become
a person downstream.

``issue_number`` references the conversation the event belongs to and is
``None`` for payloads that carry no embedded issue. ``external_id`` is the
provider's own event id (GitHub's numeric issue-event ``id``) — the storage
key per #39, and the deduplication key when an event is seen twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from coops.domain.models.actor import Actor
from coops.domain.tenancy import ProviderAccount, TenantId


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """One timeline event on one repository."""

    tenant_id: TenantId
    account: ProviderAccount
    external_id: str
    repo_name: str
    event_type: str
    created_at: str
    actor: Actor | None = None
    issue_number: int | None = None

    def __post_init__(self) -> None:
        if not (self.external_id or "").strip():
            raise ValueError("ActivityEvent requires a non-empty external_id")
        if not (self.repo_name or "").strip():
            raise ValueError("ActivityEvent requires a non-empty repo_name")
        if not (self.event_type or "").strip():
            raise ValueError("ActivityEvent requires a non-empty event_type")
        if not (self.created_at or "").strip():
            raise ValueError("ActivityEvent requires a non-empty created_at")
