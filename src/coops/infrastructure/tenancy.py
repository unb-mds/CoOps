"""Configuration-side tenancy: where the config vocabulary lives.

The domain's :func:`coops.domain.tenancy.resolve_tenant` describes the
domain in its errors ("no tenant mode named ``'bogus'``") and deliberately
never names environment variables — the config vocabulary crossing into
``domain/`` is the leak #187 fixes. This module is the translator: it is
infrastructure, it knows which settings produced the inputs, and it is
therefore the right place to say "check ``TENANT_MODE``".
"""

from __future__ import annotations

from coops.domain.tenancy import Tenant, resolve_tenant
from coops.infrastructure.config import Settings

__all__ = ["resolve_tenant_from_settings"]


def resolve_tenant_from_settings(settings: Settings) -> Tenant:
    """Resolve the tenant to run for, from the loaded deployment settings.

    Passes ``TENANT_MODE`` and the organization login through to the domain
    resolver and translates its errors into configuration vocabulary: the
    domain message survives (it says what is wrong), the environment
    variable names are added here (they say where to look). The original
    error is chained, so a traceback keeps the domain's diagnosis.
    """
    try:
        return resolve_tenant(settings.tenant_mode, settings.github_org)
    except ValueError as exc:
        raise ValueError(
            f"{exc}; check TENANT_MODE and, for 'single', COOPS_ORG (or GITHUB_ORG)"
        ) from exc
    except NotImplementedError as exc:
        raise NotImplementedError(f"{exc}; check TENANT_MODE") from exc
