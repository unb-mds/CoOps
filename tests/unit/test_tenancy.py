from dataclasses import FrozenInstanceError

import pytest

from coops.domain.tenancy import (
    PROVIDER_GITHUB,
    CorrelationId,
    ProviderAccount,
    Tenant,
    TenantId,
    resolve_tenant,
)


def test_str_returns_slug():
    tenant_id = TenantId("test_org")
    assert str(tenant_id) == "test_org"


def test_empty_slug_raises_value_error():
    with pytest.raises(ValueError):
        TenantId("")


def test_whitespace_slug_raises_value_error():
    with pytest.raises(ValueError):
        TenantId("   ")


def test_none_slug_raises_value_error():
    with pytest.raises(ValueError):
        TenantId(None)


def test_slug_is_trimmed_but_case_is_preserved():
    """The slug is ours, not a GitHub org name: trimmed (input hygiene),
    never re-cased. GitHub's case-insensitivity rule lives in
    ProviderAccount now."""
    assert TenantId("  unb-mds \n") == TenantId("unb-mds")
    assert TenantId("  unb-mds \n").slug == "unb-mds"


def test_tenant_id_no_longer_lower_cases():
    """Two spellings are two tenants: the slug is an assigned identifier
    compared exactly (a GitHub org rename must not re-key the tenant)."""
    assert TenantId("UNB-MDS") != TenantId("unb-mds")
    assert TenantId("UNB-MDS").slug == "UNB-MDS"
    assert hash(TenantId("UNB-MDS")) != hash(TenantId("unb-mds"))


def test_tenant_id_immutability():
    tenant_id = TenantId("test_id")
    with pytest.raises(FrozenInstanceError):
        tenant_id.slug = "test_id2"


@pytest.mark.parametrize("raw", ["unb-mds", "UNB-MDS", "  Unb-Mds  ", "\tunb-mds\n"])
def test_provider_account_org_id_is_trimmed_and_lowercased(raw):
    """GitHub org names are case-insensitive: one org must map to one
    account whatever case the configuration used (#68's rule, moved here)."""
    account = ProviderAccount(PROVIDER_GITHUB, raw)
    assert account.org_id == "unb-mds"
    assert account == ProviderAccount("github", "unb-mds")
    assert hash(account) == hash(ProviderAccount("github", "unb-mds"))


@pytest.mark.parametrize("raw", [" GitHub ", "GITHUB", "\tgithub\n"])
def test_provider_account_provider_is_trimmed_and_lowercased(raw):
    assert ProviderAccount(raw, "unb-mds").provider == "github"


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_blank_provider_account_org_id_raises_value_error(raw):
    with pytest.raises(ValueError):
        ProviderAccount(PROVIDER_GITHUB, raw)


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_blank_provider_account_provider_raises_value_error(raw):
    with pytest.raises(ValueError):
        ProviderAccount(raw, "unb-mds")


def test_provider_account_immutability():
    account = ProviderAccount("github", "unb-mds")
    with pytest.raises(FrozenInstanceError):
        account.org_id = "other-org"


def test_same_org_on_different_providers_are_distinct_accounts():
    """The whole point of #92: the same org name on two providers is two
    accounts, never one merged identity."""
    github = ProviderAccount("github", "unb-mds")
    gitlab = ProviderAccount("gitlab", "unb-mds")
    assert github != gitlab
    assert github is not gitlab
    # symmetric direction, so it is not a type-ordering artefact
    assert gitlab != github


def test_same_org_on_different_providers_never_collide_as_keys():
    """Distinct as dict keys and in a set: equality *and* hash must both
    separate them — one without the other still merges the accounts."""
    github = ProviderAccount("github", "unb-mds")
    gitlab = ProviderAccount("gitlab", "unb-mds")
    assert hash(github) != hash(gitlab)
    assert len({github, gitlab}) == 2
    by_account = {github: "github", gitlab: "gitlab"}
    assert by_account[ProviderAccount("github", "unb-mds")] == "github"
    assert by_account[ProviderAccount("gitlab", "UNB-MDS")] == "gitlab"


def test_same_provider_different_orgs_are_distinct_accounts():
    """Control for the axis that is *not* under test above: same provider,
    different orgs must also stay distinct."""
    assert ProviderAccount("github", "unb-mds") != ProviderAccount(
        "github", "fga-eps-mds"
    )
    assert len({ProviderAccount("github", "a"), ProviderAccount("github", "b")}) == 2


def test_tenant_holds_id_and_accounts():
    tenant_id = TenantId("unb-mds")
    accounts = (ProviderAccount("github", "unb-mds"),)
    tenant = Tenant(id=tenant_id, accounts=accounts)
    assert tenant.id is tenant_id
    assert tenant.accounts == accounts


def test_tenant_normalises_accounts_to_a_tuple():
    tenant = Tenant(id=TenantId("unb-mds"), accounts=[ProviderAccount("github", "unb")])
    assert tenant.accounts == (ProviderAccount("github", "unb"),)


def test_tenant_without_accounts_raises_value_error():
    with pytest.raises(ValueError):
        Tenant(id=TenantId("unb-mds"), accounts=())


def test_tenant_rejects_duplicate_accounts():
    """The same provider account listed twice would count every record
    twice; case differences must not smuggle one in."""
    with pytest.raises(ValueError):
        Tenant(
            id=TenantId("unb-mds"),
            accounts=(
                ProviderAccount("github", "unb-mds"),
                ProviderAccount("GitHub", "UNB-MDS"),
            ),
        )


def test_tenant_accepts_same_org_on_different_providers():
    tenant = Tenant(
        id=TenantId("unb-mds"),
        accounts=(
            ProviderAccount("github", "unb-mds"),
            ProviderAccount("gitlab", "unb-mds"),
        ),
    )
    assert len(tenant.accounts) == 2


def test_single_mode_resolves_the_cli_tenant_from_the_org():
    """TENANT_MODE=single keeps the CLI working unchanged: one tenant, one
    GitHub account, slug bootstrapped from the normalised login so capture
    directories and raw documents keep their on-disk identity."""
    tenant = resolve_tenant("single", "fga-eps-mds")
    assert tenant.id == TenantId("fga-eps-mds")
    assert str(tenant.id) == "fga-eps-mds"
    assert tenant.accounts == (ProviderAccount("github", "fga-eps-mds"),)


def test_single_mode_bootstraps_slug_from_normalised_login():
    """Whatever case COOPS_ORG carries, the resolved slug is the lower-cased
    login — the string today's CLI already derives for the same org."""
    assert resolve_tenant("single", "  FGA-EPS-MDS ").id == TenantId("fga-eps-mds")


@pytest.mark.parametrize("org", [None, "", "   "])
def test_single_mode_requires_an_org(org):
    """The domain message names the domain (an organization login is
    missing), not the settings keys: the configuration vocabulary is
    infrastructure's to add (#187). It still raises before
    ProviderAccount's own blank-org guard, whose message would say the
    same thing less actionably (that shadowing is why this asserts on the
    message).
    """
    with pytest.raises(ValueError, match="organization login"):
        resolve_tenant("single", org)


@pytest.mark.parametrize("mode,org", [("single", None), ("poly", "fga-eps-mds")])
def test_domain_errors_name_no_environment_variables(mode, org):
    """#187's leak: messages raised from ``domain/`` describe the domain.
    ``TENANT_MODE``/``COOPS_ORG`` are configuration vocabulary, and the
    place that translates them is
    :func:`coops.infrastructure.tenancy.resolve_tenant_from_settings`.
    """
    with pytest.raises(ValueError) as excinfo:
        resolve_tenant(mode, org)
    message = str(excinfo.value)
    assert "TENANT_MODE" not in message
    assert "COOPS_ORG" not in message
    assert "GITHUB_ORG" not in message


def test_unknown_mode_message_names_the_mode():
    with pytest.raises(ValueError, match="no tenant mode named 'poly'"):
        resolve_tenant("poly", "fga-eps-mds")


def test_multi_mode_is_not_implemented():
    with pytest.raises(NotImplementedError):
        resolve_tenant("multi", "fga-eps-mds")


def test_unknown_mode_raises_value_error():
    with pytest.raises(ValueError):
        resolve_tenant("poly", "fga-eps-mds")


def test_resolved_provider_account_round_trips():
    """The single-mode account survives being taken apart and rebuilt from
    its own fields — the shape a store or mapper will serialise."""
    (account,) = resolve_tenant("single", "fga-eps-mds").accounts
    rebuilt = ProviderAccount(account.provider, account.org_id)
    assert rebuilt == account
    assert hash(rebuilt) == hash(account)


def test_new_generates_valid_values():
    correlation_id = CorrelationId.new()
    assert correlation_id.value


def test_str_returns_correlation_id_value():
    correlation_id = CorrelationId("test_id")
    assert str(correlation_id) == "test_id"


def test_new_generates_unique_values():
    correlation_id1 = CorrelationId.new()
    correlation_id2 = CorrelationId.new()
    assert correlation_id1 != correlation_id2


def test_correlation_id_immutability():
    correlation_id = CorrelationId.new()
    with pytest.raises(FrozenInstanceError):
        correlation_id.value = "test_id_immutable"


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_correlation_id_raises_value_error(raw):
    with pytest.raises(ValueError):
        CorrelationId(raw)
