"""Organization member or contributor: the union population of a tenant.

"Members" are the union of the organization's members and everyone who
contributed to the extracted repositories, one record per identity. The
identity/display-name split and its precedence live in
:mod:`coops.domain.models.actor` and are reused here unchanged: two Members
with the same ``display_name`` and different ``identity`` are two different
people (16 name strings in the corpus are each shared by several distinct
people), and a Member with ``display_name=None`` is normal, not broken.

``email_hash`` is ``None`` for every member that arrives through a REST
member/profile/contributor payload — those always carry a ``login``. The
field exists because 5.1% of commit authors have no account link at all and
their only identifier is the hash of their commit email: when the union with
commit authors lands (Phase 1, #20), those people become Members keyed by
``email_hash``, and the model must already represent them.

Members stay provider-scoped (#187): ``external_id`` is the provider's own
account id (GitHub's numeric user ``id``), and it is ``None`` for exactly
the future ``email_hash`` members above — a person the provider never
identified has no provider id, and an invented one would collide.

``person_id`` is **reserved** for linking the same human across providers
and is never populated by the GitHub mapper: the linking key arrives with
#89's replacement, and matching people by email is explicitly out (#89
removes those). It exists on the model so that landing the link is a data
change, not a schema change.
"""

from __future__ import annotations

from dataclasses import dataclass

from coops.domain.models.actor import identity_key, identity_mismatch_message
from coops.domain.tenancy import ProviderAccount, TenantId


@dataclass(frozen=True, slots=True)
class Member:
    """One person in the tenant's member/contributor population."""

    tenant_id: TenantId
    account: ProviderAccount
    identity: str
    display_name: str | None
    external_id: str | None = None
    login: str | None = None
    email_hash: str | None = None
    is_org_member: bool = False
    contributions_total: int = 0
    person_id: str | None = None

    def __post_init__(self) -> None:
        if not (self.identity or "").strip():
            raise ValueError("Member requires a non-empty identity")
        if self.external_id is not None and not self.external_id.strip():
            raise ValueError(
                "Member.external_id must be None or non-blank, never a placeholder"
            )
        if self.person_id is not None and not self.person_id.strip():
            raise ValueError(
                "Member.person_id must be None or non-blank, never a placeholder"
            )
        if self.display_name is not None and not self.display_name.strip():
            raise ValueError(
                "Member.display_name must be None or non-blank, never a placeholder"
            )
        if self.identity != identity_key(
            self.login, self.email_hash, self.display_name
        ):
            raise ValueError(
                identity_mismatch_message(
                    "Member",
                    self.identity,
                    self.login,
                    self.email_hash,
                    self.display_name,
                )
            )
        if self.contributions_total < 0:
            raise ValueError(
                f"Member.contributions_total must be >= 0, got {self.contributions_total}"
            )
