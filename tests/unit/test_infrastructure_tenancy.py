"""The infrastructure side of tenancy error translation (#187).

The domain's ``resolve_tenant`` describes the domain in its errors; these
tests pin the other half of the seam — that ``coops.infrastructure`` is
where the configuration vocabulary ("check ``TENANT_MODE``") gets added,
and that the domain's diagnosis survives the translation.
"""

import pytest

from coops.domain.tenancy import resolve_tenant
from coops.infrastructure import Settings, resolve_tenant_from_settings


def _settings(**over):
    kwargs = {"github_token": "synthetic-token", "github_org": "test-org"}
    kwargs.update(over)
    return Settings(**kwargs)


def test_translation_resolves_the_same_tenant_as_the_domain():
    settings = _settings()
    assert resolve_tenant_from_settings(settings) == resolve_tenant(
        settings.tenant_mode, settings.github_org
    )


def test_missing_org_translation_names_the_settings_keys():
    """The mirror of the domain test: where the domain says "organization
    login", infrastructure says where to look — ``COOPS_ORG``/``GITHUB_ORG``
    appear here and only here.
    """
    with pytest.raises(ValueError) as excinfo:
        resolve_tenant_from_settings(_settings(github_org=None))
    message = str(excinfo.value)
    assert "TENANT_MODE" in message
    assert "COOPS_ORG" in message
    # The domain's diagnosis survives the translation, not replaced by it.
    assert "organization login" in message


def test_unknown_mode_translation_names_the_settings_keys():
    """``Settings`` types ``tenant_mode`` as a Literal, so pydantic rejects
    an unknown mode at load time; the wrapper's ValueError translation is
    exercised by injecting the value after validation, the way a mode the
    Literal grows to allow but the domain does not would arrive.
    """
    settings = _settings()
    settings.tenant_mode = "bogus"  # type: ignore[assignment]
    with pytest.raises(ValueError) as excinfo:
        resolve_tenant_from_settings(settings)
    message = str(excinfo.value)
    assert "TENANT_MODE" in message
    assert "no tenant mode named 'bogus'" in message


def test_translation_chains_the_domain_error():
    """The original exception stays reachable: a traceback keeps the
    domain's diagnosis, the translated message keeps the operator's.
    """
    with pytest.raises(ValueError) as excinfo:
        resolve_tenant_from_settings(_settings(github_org=None))
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_multi_mode_stays_not_implemented_through_the_translation():
    with pytest.raises(NotImplementedError, match="TENANT_MODE"):
        resolve_tenant_from_settings(_settings(tenant_mode="multi"))
