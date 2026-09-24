from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

#: Provider token for GitHub, the one provider today. Lives in the domain
#: (not in ``coops.storage``) so :class:`ProviderAccount` — a domain type —
#: does not import an adapter module to name its own vocabulary.
PROVIDER_GITHUB = "github"


@dataclass(frozen=True, slots=True)
class TenantId:
    """Opaque slug identifying the tenant: *who we report for*.

    The slug is assigned by us — ``unb-mds`` for the current deployment —
    and deliberately carries no provider meaning: it is not a GitHub
    organization name, so it survives the organization being renamed
    (#92). "unb-mds on GitHub" and "unb-mds on GitLab" are two provider
    accounts (:class:`ProviderAccount`) of *one* tenant, not two tenants.

    Normalisation: trimmed only, **case preserved**. Case-insensitivity of
    organization names is a GitHub rule about ``org_id``, not a property of
    our own slug, so the trimming/lower-casing this class did before #92
    moved to :class:`ProviderAccount`. Slugs compare exactly:
    ``TenantId("Unb-Mds") != TenantId("unb-mds")`` — a slug is a chosen
    identifier, and two spellings are two tenants.
    """

    slug: str

    def __post_init__(self) -> None:
        normalized = (self.slug or "").strip()
        if not normalized:
            raise ValueError("TenantId requires a non-empty slug")
        object.__setattr__(self, "slug", normalized)

    def __str__(self) -> str:
        return self.slug


@dataclass(frozen=True, slots=True)
class ProviderAccount:
    """One organization on one provider: *where the data came from*.

    ``ProviderAccount("github", "unb-mds")`` and
    ``ProviderAccount("gitlab", "unb-mds")`` are different accounts — same
    ``org_id``, different provider — and may belong to the same
    :class:`Tenant`. Equality and hashing are the ``(provider, org_id)``
    pair, so accounts that differ on either axis never collide.

    Normalisation:

    - ``org_id`` is trimmed and lower-cased: GitHub treats organization
      logins as case-insensitive, so one login must map to one account
      whatever case the configuration used. This rule moved here from
      ``TenantId`` in #92, because it is a GitHub rule about org names,
      not a property of the tenant slug. When a provider whose names are
      case-sensitive arrives (#49), normalisation must become
      provider-aware; today there is exactly one provider and its rule is
      lower-case.
    - ``provider`` is trimmed and lower-cased: it is our own closed
      vocabulary (``"github"``, later ``"gitlab"``), not a string a
      provider chose.
    """

    provider: str
    org_id: str

    def __post_init__(self) -> None:
        provider = (self.provider or "").strip().lower()
        if not provider:
            raise ValueError("ProviderAccount requires a non-empty provider")
        org_id = (self.org_id or "").strip().lower()
        if not org_id:
            raise ValueError("ProviderAccount requires a non-empty org_id")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "org_id", org_id)


@dataclass(frozen=True, slots=True)
class Tenant:
    """A tenant: who we report for, and the accounts that feed it.

    ``id`` is the tenant's opaque slug; ``accounts`` are the provider
    organizations whose data is reported under it. One tenant holding
    several accounts is the point of #92: when GitLab arrives, the tenant
    gains a second account rather than a second identity.

    ``accounts`` is stored as a tuple (frozen all the way down, and
    hashable). Guards:

    - at least one account — a tenant with no account has no data source
      and is indistinguishable from a configuration error;
    - no duplicates — the same ``(provider, org_id)`` listed twice is the
      same account and would count every record twice.
    """

    id: TenantId
    accounts: tuple[ProviderAccount, ...]

    def __post_init__(self) -> None:
        accounts = tuple(self.accounts or ())
        if not accounts:
            raise ValueError("Tenant requires at least one ProviderAccount")
        if len(set(accounts)) != len(accounts):
            raise ValueError(
                "Tenant accounts must be unique: the same provider account "
                "is listed twice"
            )
        object.__setattr__(self, "accounts", accounts)


def resolve_tenant(mode: str, github_org: str | None) -> Tenant:
    """Resolve the tenant to run for, from two deployment settings.

    Takes plain values rather than ``Settings`` so the domain stays free of
    infrastructure; :func:`coops.infrastructure.tenancy.resolve_tenant_from_settings`
    is the caller that knows which configuration keys produced them, and it
    is where the domain's errors are translated into configuration
    vocabulary ("check ``TENANT_MODE``"). The messages raised here describe
    the domain only — naming environment variables from ``domain/`` would
    let the config vocabulary cross the boundary it exists to guard (#187).

    ``single`` (the only mode today) resolves one tenant with one GitHub
    account, so the CLI keeps working unchanged. The slug is *bootstrapped*
    from the account's normalised login: raw-capture directories
    (``<root>/<tenant_id>/``) and raw-layer documents are keyed by the
    lower-cased org name today, and re-deriving that exact string means
    neither of them moves on upgrade. That is a convention of this
    resolver, not a property of :class:`TenantId` — the slug is opaque and
    compared exactly, and once tenants are pinned in a registry (multi
    mode, #20) a renamed organization changes the account, not the tenant.

    ``multi`` is not implemented yet; anything else is not a tenant mode.
    """
    if mode == "single":
        org = (github_org or "").strip()
        if not org:
            raise ValueError(
                "single-tenant mode resolves the tenant from the provider "
                "account's organization login, and no organization login "
                "was given"
            )
        account = ProviderAccount(PROVIDER_GITHUB, org)
        return Tenant(id=TenantId(account.org_id), accounts=(account,))
    if mode == "multi":
        raise NotImplementedError(
            "multi-tenant mode is not implemented yet: the tenant registry "
            "arrives with #20; 'single' is the only mode today"
        )
    raise ValueError(f"no tenant mode named {mode!r}: expected 'single' or 'multi'")


@dataclass(frozen=True, slots=True)
class CorrelationId:
    """Ties together the logs and records produced by one pipeline run."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("CorrelationId requires a non-empty value")

    @classmethod
    def new(cls) -> CorrelationId:
        return cls(str(uuid4()))

    def __str__(self) -> str:
        return self.value
