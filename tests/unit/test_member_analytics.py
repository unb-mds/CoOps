import math
from datetime import datetime

import pytest
from freezegun import freeze_time

from coops.silver.member_analytics import calculate_maturity_score, classify_member_status, process_member_analytics

AS_OF = datetime(2025, 1, 1)  # the capture instant the helper tests age against

def test_calculate_maturity_score_basics():
    # Caso base: sem campos -> score baixo (somente logs com 0)
    m0 = {}
    s0 = calculate_maturity_score(m0, AS_OF)
    assert isinstance(s0, float)
    assert s0 >= 0

    # Conta com 2 anos, 5 repositórios, 3 seguidores
    m1 = {
        "created_at": "2023-01-01T00:00:00Z",
        "public_repos": 5,
        "followers": 3,
    }
    s1 = calculate_maturity_score(m1, AS_OF)
    assert s1 > s0  # deve aumentar com idade/repos/seguidores

    # Mais repositórios e seguidores => score maior
    m2 = {
        "created_at": "2020-01-01T00:00:00Z",
        "public_repos": 50,
        "followers": 30,
    }
    s2 = calculate_maturity_score(m2, AS_OF)
    assert s2 > s1

def test_calculate_maturity_score_expected_components():
    # Valida aproximação numérica do score com pesos definidos no código
    # age_component = 0.5 * log1p(account_age_days)
    # repos_component = 3 * log1p(public_repos)
    # followers_component = 20 * log1p(followers)
    m = {
        "created_at": "2024-01-01T00:00:00Z",  # 366 dias até 2025-01-01 (2024 bissexto)
        "public_repos": 10,
        "followers": 10,
    }
    score = calculate_maturity_score(m, AS_OF)

    age_days = (AS_OF - datetime(2024, 1, 1)).days  # exatamente 366
    expected = 0.5 * math.log1p(age_days) + 3 * math.log1p(10) + 20 * math.log1p(10)

    # Idade medida contra o instante de captura: o valor é exato, não uma
    # aproximação tolerante (#188).
    assert math.isclose(score, expected, rel_tol=1e-12)

def test_calculate_maturity_score_no_created_at():
    """Testa score quando created_at está ausente"""
    m = {
        "public_repos": 10,
        "followers": 5,
    }
    score = calculate_maturity_score(m, AS_OF)
    # age_component será 0, apenas repos e followers contam
    expected = 3 * math.log1p(10) + 20 * math.log1p(5)
    assert abs(score - expected) < 0.1

def test_calculate_maturity_score_invalid_date():
    """Testa score quando created_at tem formato inválido"""
    m = {
        "created_at": "invalid-date",
        "public_repos": 10,
        "followers": 5,
    }
    score = calculate_maturity_score(m, AS_OF)
    # Deve tratar como age_days = 0
    expected = 3 * math.log1p(10) + 20 * math.log1p(5)
    assert abs(score - expected) < 0.1

def test_classify_member_status_new_by_age():
    # Conta recém-criada => 'new'. A idade é medida contra o instante de
    # captura (AS_OF), então a classificação não apodrece com o calendário.
    m = {
        "created_at": "2024-11-15T00:00:00Z",
        "public_repos": 100,
        "followers": 100,
    }
    assert classify_member_status(m, AS_OF) == "new"

def test_classify_member_status_new_by_activity():
    # Conta antiga porém com pouca atividade => 'new'
    m = {
        "created_at": "2018-01-01T00:00:00Z",
        "public_repos": 2,
        "followers": 1,
    }
    assert classify_member_status(m, AS_OF) == "new"

def test_classify_member_status_established():
    # Conta não tão recente e com atividade razoável => 'established'
    m = {
        "created_at": "2020-01-01T00:00:00Z",
        "public_repos": 20,
        "followers": 15,
    }
    assert classify_member_status(m, AS_OF) == "established"

def test_classify_member_status_no_created_at():
    """Testa classificação quando created_at está ausente"""
    m = {
        "public_repos": 50,
        "followers": 50,
    }
    # Sem created_at, age_days = 0 < 365 => 'new'
    assert classify_member_status(m, AS_OF) == "new"

def test_classify_member_status_invalid_date():
    """Testa classificação quando created_at tem formato inválido"""
    m = {
        "created_at": "invalid-date",
        "public_repos": 50,
        "followers": 50,
    }
    # Data inválida tratada como age_days = 0 => 'new'
    assert classify_member_status(m, AS_OF) == "new"

def test_process_member_analytics_empty_data(monkeypatch):
    """Testa processamento com dados vazios"""
    def fake_load(path):
        return None

    monkeypatch.setattr("coops.silver.member_analytics.load_json_data", fake_load)

    saved_data = {}

    def fake_save(data, path, timestamp=True):
        saved_data[path] = data
        return path

    monkeypatch.setattr("coops.silver.member_analytics.save_json_data", fake_save)

    result = process_member_analytics()
    # Always written, so the dashboard shows "no members", not "not generated"
    assert "data/silver/members_analytics.json" in result
    assert saved_data["data/silver/members_analytics.json"] == []
    # no stale bands from an earlier run
    assert saved_data["data/silver/maturity_bands.json"] == {"low": 0, "medium": 0, "high": 0}

def test_process_member_analytics_with_members(monkeypatch):
    """Testa processamento completo com membros"""
    fake_members = [
        {
            "login": "alice",
            "id": 1,
            "name": "Alice",
            "company": "CompanyA",
            "location": "USA",
            "email": "alice@example.com",
            "bio": "Developer",
            "public_repos": 10,
            "followers": 5,
            "following": 3,
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2024-12-01T00:00:00Z",
        },
        {
            "login": "bob",
            "id": 2,
            "name": "Bob",
            "company": None,
            "location": None,
            "email": None,
            "bio": None,
            "public_repos": 50,
            "followers": 30,
            "following": 20,
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2024-12-01T00:00:00Z",
        },
    ]

    # O sidecar do Bronze carrega o instante de captura contra o qual as
    # idades são medidas (#188).
    fake_corpus = [
        {"_metadata": {"extracted_at": "2025-01-01T00:00:00",
                       "file_path": "data/bronze/members_detailed.json",
                       "record_count": 2}},
        *fake_members,
    ]

    saved_data = {}

    def fake_load(path):
        return fake_corpus

    def fake_save(data, path, timestamp=True):
        saved_data[path] = data
        return path

    monkeypatch.setattr("coops.silver.member_analytics.load_json_data", fake_load)
    monkeypatch.setattr("coops.silver.member_analytics.save_json_data", fake_save)

    files = process_member_analytics()

    # Verifica que 3 arquivos foram gerados
    assert len(files) == 3
    assert any("members_analytics.json" in f for f in files)
    assert any("member_status_distribution.json" in f for f in files)
    assert any("maturity_bands.json" in f for f in files)

    # Verifica os dados salvos
    members_analytics = saved_data["data/silver/members_analytics.json"]
    assert len(members_analytics) == 2
    assert members_analytics[0]["login"] == "alice"
    assert members_analytics[0]["status"] == "established"  # 2 anos, 10 repos
    assert members_analytics[1]["login"] == "bob"
    assert members_analytics[1]["status"] == "established"  # 5 anos, 50 repos

    # Verifica distribuição de status
    status_dist = saved_data["data/silver/member_status_distribution.json"]
    assert status_dist["established"] == 2

    # Verifica maturity bands
    maturity_bands = saved_data["data/silver/maturity_bands.json"]
    assert "low" in maturity_bands
    assert "medium" in maturity_bands
    assert "high" in maturity_bands
    assert maturity_bands["low"] + maturity_bands["medium"] + maturity_bands["high"] == 2

def test_process_member_analytics_with_metadata(monkeypatch):
    """Testa processamento quando dados contêm metadata"""
    fake_members = [
        {
            "login": "charlie",
            "id": 3,
            "name": "Charlie",
            "public_repos": 5,
            "followers": 2,
            "following": 1,
            "created_at": "2024-06-01T00:00:00Z",
            "updated_at": "2024-12-01T00:00:00Z",
        },
    ]

    fake_corpus = [
        # Sidecar como o save_json_data escreve: extracted_at é o instante
        # de captura contra o qual a idade é medida (#188).
        {"_metadata": {"extracted_at": "2025-01-01T00:00:00",
                       "file_path": "data/bronze/members_detailed.json",
                       "record_count": 1}},
        *fake_members,
    ]

    saved_data = {}

    def fake_load(path):
        return fake_corpus

    def fake_save(data, path, timestamp=True):
        saved_data[path] = data
        return path

    monkeypatch.setattr("coops.silver.member_analytics.load_json_data", fake_load)
    monkeypatch.setattr("coops.silver.member_analytics.save_json_data", fake_save)

    files = process_member_analytics()

    # Verifica que apenas 1 membro foi processado (metadata ignorada)
    members_analytics = saved_data["data/silver/members_analytics.json"]
    assert len(members_analytics) == 1
    assert members_analytics[0]["login"] == "charlie"
    assert members_analytics[0]["status"] == "new"  # < 1 ano na captura

def test_process_member_analytics_member_without_created_at(monkeypatch):
    """Testa processamento de membro sem created_at"""
    fake_members = [
        {
            "login": "dave",
            "id": 4,
            "public_repos": 100,
            "followers": 50,
            "following": 25,
        },
    ]

    fake_corpus = [
        {"_metadata": {"extracted_at": "2025-01-01T00:00:00",
                       "file_path": "data/bronze/members_detailed.json",
                       "record_count": 1}},
        *fake_members,
    ]

    saved_data = {}

    def fake_load(path):
        return fake_corpus

    def fake_save(data, path, timestamp=True):
        saved_data[path] = data
        return path

    monkeypatch.setattr("coops.silver.member_analytics.load_json_data", fake_load)
    monkeypatch.setattr("coops.silver.member_analytics.save_json_data", fake_save)

    files = process_member_analytics()

    members_analytics = saved_data["data/silver/members_analytics.json"]
    assert len(members_analytics) == 1
    assert members_analytics[0]["account_age_days"] == 0
    assert members_analytics[0]["status"] == "new"  # age = 0 < 365

def test_process_member_analytics_skips_members_without_profile(monkeypatch):
    """Members whose profile couldn't be fetched have no maturity data."""
    fake_members = [
        {"login": "with_profile", "id": 1, "public_repos": 20, "followers": 20,
         "created_at": "2015-01-01T00:00:00Z", "profile_fetched": True},
        {"login": "no_profile", "profile_fetched": False},
        {"login": "legacy", "public_repos": 1, "followers": 0, "created_at": "2024-06-01T00:00:00Z"},
    ]
    fake_corpus = [
        {"_metadata": {"extracted_at": "2025-01-01T00:00:00",
                       "file_path": "data/bronze/members_detailed.json",
                       "record_count": 3}},
        *fake_members,
    ]
    saved_data = {}

    def fake_save(data, path, timestamp=True):
        saved_data[path] = data
        return path

    monkeypatch.setattr("coops.silver.member_analytics.load_json_data", lambda path: fake_corpus)
    monkeypatch.setattr("coops.silver.member_analytics.save_json_data", fake_save)

    process_member_analytics()

    analytics = saved_data["data/silver/members_analytics.json"]
    assert [m["login"] for m in analytics] == ["with_profile", "legacy"]
    for field in ("email", "location", "bio", "company"):
        assert field not in analytics[0]


def test_process_member_analytics_carries_id(monkeypatch):
    """The `id` field from Bronze survives into Silver member analytics."""
    fake_members = [
        {
            "login": "alice",
            "id": 42,
            "name": "Alice",
            "public_repos": 10,
            "followers": 5,
            "following": 3,
            "created_at": "2023-01-01T00:00:00Z",
        },
    ]

    fake_corpus = [
        {"_metadata": {"extracted_at": "2025-01-01T00:00:00",
                       "file_path": "data/bronze/members_detailed.json",
                       "record_count": 1}},
        *fake_members,
    ]

    saved_data = {}

    def fake_load(path):
        return fake_corpus

    def fake_save(data, path, timestamp=True):
        saved_data[path] = data
        return path

    monkeypatch.setattr("coops.silver.member_analytics.load_json_data", fake_load)
    monkeypatch.setattr("coops.silver.member_analytics.save_json_data", fake_save)

    process_member_analytics()

    analytics = saved_data["data/silver/members_analytics.json"]
    assert len(analytics) == 1
    assert analytics[0]["id"] == 42
    assert analytics[0]["name"] == "Alice"


# ---------------------------------------------------------------------------
# Issue #188: ages are ages at capture, not ages "whenever Silver ran"
# ---------------------------------------------------------------------------

# extracted_at como o save_json_data escreve: datetime.now().isoformat().
CAPTURE = "2026-09-23T16:38:31.075055"


def _bronze_corpus(extracted_at, members):
    """A Bronze members file as save_json_data writes it: sidecar first."""
    return [
        {
            "_metadata": {
                "extracted_at": extracted_at,
                "file_path": "data/bronze/members_detailed.json",
                "record_count": len(members),
            }
        },
        *members,
    ]


def _run_analytics(monkeypatch, corpus):
    """Run the Silver member step over `corpus`; return what it saved."""
    saved = {}

    monkeypatch.setattr(
        "coops.silver.member_analytics.load_json_data", lambda path: corpus
    )

    def fake_save(data, path, timestamp=True):
        saved[path] = data
        return path

    monkeypatch.setattr("coops.silver.member_analytics.save_json_data", fake_save)
    process_member_analytics()
    return saved


def _capture_member():
    """An invented member (handles/names are not real people)."""
    return {
        "login": "sable",
        "id": 91,
        "name": "Sable Rowan",
        "public_repos": 12,
        "followers": 40,
        "following": 4,
        "created_at": "2020-03-04T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }


def test_output_identical_under_different_wall_clocks(monkeypatch):
    """The point of #188: run the whole computation twice with a different
    wall clock each time and every emitted record — the full member record,
    the status distribution and the maturity bands — must be identical.
    With ``datetime.now()`` in the computation the two runs disagree; with
    ages measured against the captured ``extracted_at`` they cannot."""
    corpus = _bronze_corpus(CAPTURE, [_capture_member(), _capture_member()])

    with freeze_time("2024-01-01"):
        first = _run_analytics(monkeypatch, corpus)
    with freeze_time("2030-06-15"):
        second = _run_analytics(monkeypatch, corpus)

    # Whole artifacts, not a slice of a record.
    assert first == second

    # Guard against an inert comparison: the run really produced records,
    # and the two clocks would give this member different ages (they
    # straddle the capture date), so the equality above discriminates.
    records = first["data/silver/members_analytics.json"]
    assert len(records) == 2
    assert records[0]["login"] == "sable"
    age_at_first_clock = (datetime(2024, 1, 1) - datetime(2020, 3, 4)).days
    age_at_second_clock = (datetime(2030, 6, 15) - datetime(2020, 3, 4)).days
    assert age_at_first_clock != age_at_second_clock


def test_account_age_days_is_age_at_capture_not_age_today(monkeypatch):
    """An artifact captured a year ago yields the age as of that capture,
    not as of the day the Silver step happens to run (#188)."""
    corpus = _bronze_corpus("2025-09-23T10:00:00", [_capture_member()])

    # O passo Silver roda um ano depois da captura.
    with freeze_time("2026-09-23"):
        saved = _run_analytics(monkeypatch, corpus)

    record = saved["data/silver/members_analytics.json"][0]
    expected = (datetime(2025, 9, 23, 10, 0, 0) - datetime(2020, 3, 4)).days
    assert record["account_age_days"] == expected

    # Os dois relógios discordam deste membro em exatamente 365 dias, então
    # a igualdade acima distingue "idade na captura" de "idade hoje".
    today_based = (datetime(2026, 9, 23) - datetime(2020, 3, 4)).days
    assert today_based == expected + 365


def test_members_without_sidecar_refuse_to_run(monkeypatch):
    """A corpus with members but no _metadata sidecar has no capture
    instant: the run fails loudly instead of silently ageing against the
    wall clock — the exact fallback #188 removes (#188)."""
    corpus = [_capture_member()]  # members, mas sem sidecar

    with pytest.raises(ValueError, match=r"extracted_at"):
        _run_analytics(monkeypatch, corpus)


@pytest.mark.parametrize(
    "bad_extracted_at",
    ["not-a-timestamp", "", None, 20260923],
    ids=["garbage-string", "empty-string", "null", "not-a-string"],
)
def test_unparseable_extracted_at_refuses_to_run(monkeypatch, bad_extracted_at):
    """A sidecar whose extracted_at cannot be parsed is the same defect —
    an unknown capture instant — and is refused the same way (#188)."""
    corpus = [
        {"_metadata": {"extracted_at": bad_extracted_at}},
        _capture_member(),
    ]

    with pytest.raises(ValueError, match=r"extracted_at"):
        _run_analytics(monkeypatch, corpus)


def test_maturity_score_stable_across_days(monkeypatch):
    """maturity_score inherits the capture-time age through
    ``age_component``: same corpus, same score, any day (#188)."""
    corpus = _bronze_corpus(CAPTURE, [_capture_member()])
    member = _capture_member()

    scores = []
    for frozen in ("2024-01-01", "2030-06-15"):
        with freeze_time(frozen):
            saved = _run_analytics(monkeypatch, corpus)
        scores.append(saved["data/silver/members_analytics.json"][0]["maturity_score"])

    assert scores[0] == scores[1]

    # E é a fórmula com a idade na captura — não dois erros iguais.
    capture = datetime.fromisoformat(CAPTURE)
    created = datetime.strptime(member["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    age_at_capture = (capture - created).days
    expected = (
        0.5 * math.log1p(age_at_capture)
        + 3 * math.log1p(member["public_repos"])
        + 20 * math.log1p(member["followers"])
    )
    assert math.isclose(scores[0], expected, rel_tol=1e-12)
