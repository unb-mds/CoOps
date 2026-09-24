"""Provider-agnostic domain entities (issues #21 and #187).

All of them are frozen, slotted dataclasses that validate in
``__post_init__`` with ``ValueError``, matching
:mod:`coops.domain.tenancy`.

Every entity carries the full tenancy triple of #92, never a bare
organization string:

- ``tenant_id`` — the :class:`~coops.domain.TenantId` being reported for;
- ``account`` — the :class:`~coops.domain.ProviderAccount` the record was
  extracted from ("unb-mds on GitHub" and "unb-mds on GitLab" are two
  accounts whose records must never merge, even for the same login and the
  same numeric id, because a GitHub id and a GitLab id are two different
  ids that happen to be equal);
- ``external_id`` — the provider's own identifier for the record, as
  detailed below.

``external_id`` — the shape is not a free choice (#187): the Mongo adapter
(#39) indexes ``{org_id, entity}`` and filters **every** query by
``org_id``, and ``external_id`` is the record's key within that scope.
Decided with that in view:

- **A plain string, exactly as the provider sent it** (stringified when
  the provider's id is numeric — GitHub's are). BSON keys and index
  equality are byte-exact, and providers disagree on id types (numeric
  today, UUIDs or paths are possible), so one ``str`` field holds every
  provider's id without the adapter guessing a type — and without an
  int/str split ever making one record addressable two ways.
- **The provider's whole id, never a composite we invent.** A composite
  (``"owner/repo"``, ``"repo#number"``) would re-embed addressing the
  account already carries and would break the day the provider re-shapes
  it; the provider's own id also survives renames, which names do not.
- **Scoped by :class:`~coops.domain.ProviderAccount`, never global** — a
  GitHub id and a colliding GitLab id never meet, because the query is
  scoped by the provider and ``org_id`` before ``external_id`` is
  compared.

  **But the account is not by itself a uniqueness scope.** Measured on
  ``fga-eps-mds``: of 123,645 distinct commit SHAs, **6,232 (5.0%) appear
  in more than one repository of the same account** — one SHA in six
  repositories at the extreme, where a cohort split a project
  (``…-FishLog``, ``…-FishWiki``, ``…-User``) and the shared history went
  with it. Forks and cherry-picks do the same.

  So the key an adapter must index and upsert on is
  ``(tenant_id, account, repo_name, external_id)``. Treating
  ``(account, external_id)`` as unique would make those 6,232 commits
  overwrite one another, leaving one repository's copy and silently
  deleting the rest — a row count that still looks plausible.
  ``(account, repo_name, external_id)`` is unique across the corpus, and
  every entity here already carries ``repo_name``, so this costs nothing
  but saying it. See #39.

Where an entity already carries the same value under its own vocabulary
(``Commit.sha``, ``FileTree.sha``) both fields exist on purpose: ``sha``
is the git-graph key that parents and trees reference, ``external_id``
is the storage key — same value for GitHub, different roles, and the
coincidence is a property of the provider, not a reason to collapse the
two contracts into one.
"""

from coops.domain.models.activity_event import ActivityEvent
from coops.domain.models.actor import Actor, display_name_of, identity_key
from coops.domain.models.commit import Commit
from coops.domain.models.file_tree import ENTRY_KINDS, FileEntry, FileTree
from coops.domain.models.issue import Issue
from coops.domain.models.member import Member
from coops.domain.models.pull_request import PullRequest
from coops.domain.models.repository import Repository

__all__ = [
    "ENTRY_KINDS",
    "ActivityEvent",
    "Actor",
    "Commit",
    "FileEntry",
    "FileTree",
    "Issue",
    "Member",
    "PullRequest",
    "Repository",
    "display_name_of",
    "identity_key",
]
