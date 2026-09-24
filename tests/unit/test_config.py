"""Unit tests for coops.infrastructure.config."""

import pytest

import coops
from coops.infrastructure.config import Settings, get_settings

ENV_VARS = (
    "GITHUB_TOKEN", "COOPS_GITHUB_TOKEN", "GITHUB_ORG", "COOPS_ORG",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_MODEL", "COOPS_STORAGE",
    "MONGO_URI", "RAW_MAX_AGE_SECONDS", "TENANT_MODE", "GITHUB_API_URL",
)


@pytest.fixture(autouse=True)
def _clean_env(tmp_path, monkeypatch):
    """No env vars and no .env/.secrets from the developer's machine."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_loads_without_any_configuration():
    settings = Settings()
    assert settings.github_token is None
    assert settings.github_org is None
    assert settings.gemini_api_key is None
    assert settings.gemini_model == "gemini-3.5-flash-lite"
    assert settings.coops_storage == "data"
    assert settings.raw_max_age_seconds == 3600
    assert settings.tenant_mode == "single"


def test_reads_github_names(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_ORG", "org")
    settings = Settings()
    assert (settings.github_token, settings.github_org) == ("tok", "org")


def test_coops_names_take_precedence(monkeypatch):
    """Actions secrets/variables can't be named GITHUB_*, so COOPS_* wins."""
    monkeypatch.setenv("GITHUB_TOKEN", "actions-token")
    monkeypatch.setenv("COOPS_GITHUB_TOKEN", "pat")
    monkeypatch.setenv("GITHUB_ORG", "owner")
    monkeypatch.setenv("COOPS_ORG", "unb-mds")
    settings = Settings()
    assert (settings.github_token, settings.github_org) == ("pat", "unb-mds")


def test_empty_values_count_as_unset(tmp_path, monkeypatch):
    """`env: COOPS_ORG: ${{ vars.COOPS_ORG }}` is '' when the variable is undefined."""
    monkeypatch.setenv("COOPS_GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("COOPS_ORG", "")
    monkeypatch.setenv("GITHUB_ORG", "org")
    monkeypatch.setenv("GEMINI_MODEL", "")
    (tmp_path / ".secrets").write_text("GEMINI_API_KEY=\n")
    settings = Settings()
    assert (settings.github_token, settings.github_org) == ("tok", "org")
    assert settings.gemini_model == "gemini-3.5-flash-lite"
    assert settings.gemini_api_key is None


def test_reads_secrets_file(tmp_path):
    (tmp_path / ".secrets").write_text("GITHUB_TOKEN=from-file\nCOOPS_ORG=file-org\n")
    settings = Settings()
    assert (settings.github_token, settings.github_org) == ("from-file", "file-org")


def test_environment_overrides_secrets_file(tmp_path, monkeypatch):
    (tmp_path / ".secrets").write_text("GITHUB_ORG=file-org\n")
    monkeypatch.setenv("GITHUB_ORG", "env-org")
    assert Settings().github_org == "env-org"


def test_environment_beats_files_whatever_the_name(tmp_path, monkeypatch):
    (tmp_path / ".secrets").write_text("COOPS_ORG=file-org\n")
    monkeypatch.setenv("GITHUB_ORG", "env-org")
    assert Settings().github_org == "env-org"


def test_coops_name_wins_between_files(tmp_path):
    (tmp_path / ".env").write_text("COOPS_ORG=dotenv-org\n")
    (tmp_path / ".secrets").write_text("GITHUB_ORG=secrets-org\n")
    assert Settings().github_org == "dotenv-org"


def test_secrets_file_overrides_env_file(tmp_path):
    (tmp_path / ".env").write_text("GITHUB_ORG=dotenv-org\n")
    (tmp_path / ".secrets").write_text("GITHUB_ORG=secrets-org\n")
    assert Settings().github_org == "secrets-org"


def test_rejects_unknown_tenant_mode(monkeypatch):
    monkeypatch.setenv("TENANT_MODE", "shared")
    with pytest.raises(ValueError):
        Settings()


def test_get_settings_is_cached(monkeypatch):
    monkeypatch.setenv("GITHUB_ORG", "first")
    first = get_settings()
    monkeypatch.setenv("GITHUB_ORG", "second")
    assert get_settings() is first
    get_settings.cache_clear()
    assert get_settings().github_org == "second"


def test_reads_raw_max_age_seconds(monkeypatch):
    monkeypatch.setenv("RAW_MAX_AGE_SECONDS", "900")
    assert Settings().raw_max_age_seconds == 900


def test_version_comes_from_package_metadata():
    from importlib.metadata import version

    assert coops.__version__ == version("coops")
