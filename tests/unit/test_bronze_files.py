"""Bronze enumeration: the aggregate is excluded and a typo cannot return zero.

The measurements quoted here come from ``fga-eps-mds`` and are what the shipped
guards did before this module existed::

    commits_*.json   487 matched, 1 aggregate,  guard '_with_stats'    excluded 0 (dead)
    prs_*.json       428 matched, 1 aggregate
    issues_*.json    285 matched, 1 aggregate,  guard 'issue_events'   excluded 0 (dead)
    structure_*.json 486 matched, 0 aggregate,  NO GUARD AT ALL        (latent)

Two of those families have no aggregate on disk today, so the corpus cannot
exercise their exclusion at all. Those cases are covered by the fixtures below,
which is the point of having both: a clean number from real data would otherwise
imply coverage that does not exist.
"""

from __future__ import annotations

import json

import pytest

from coops.bronze.files import (
    BRONZE_FAMILIES,
    DOCUMENT_FAMILIES,
    RECORD_FAMILIES,
    aggregate_name,
    bronze_files,
    bronze_records,
    bronze_repos,
    remove_aggregate,
    repo_of,
)


def _write(directory, name, payload):
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# An unknown family raises. This is the reason the module exists.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typo",
    [
        "commit",  # singular: would glob commit_*.json and match nothing
        "issue",
        "pr",
        "structures",
        "repos",
        "",
    ],
)
@pytest.mark.parametrize(
    "call",
    [bronze_files, lambda d, f: list(bronze_repos(d, f)), lambda d, f: list(bronze_records(d, f))],
    ids=["bronze_files", "bronze_repos", "bronze_records"],
)
def test_unknown_family_raises_rather_than_returning_empty(tmp_path, call, typo):
    """A plausible typo must not be answerable with a confident zero.

    ``bronze_records(path, "commit")`` globbing ``commit_*.json`` would match
    nothing and report zero records — no error, no warning, a number that reads
    as an answer. Every entry point is covered because a helper is only as safe
    as its least guarded door.
    """
    with pytest.raises(ValueError) as excinfo:
        call(tmp_path, typo)
    assert "unknown bronze family" in str(excinfo.value)


def test_exception_names_the_valid_families(tmp_path):
    """Naming what was wrong without naming what is right sends the reader back
    to the source to find out. Every family must appear in the message."""
    with pytest.raises(ValueError) as excinfo:
        bronze_files(tmp_path, "commit")
    message = str(excinfo.value)
    missing = sorted(f for f in BRONZE_FAMILIES if f not in message)
    assert not missing, f"exception message omits valid families: {missing}"


# --------------------------------------------------------------------------
# The aggregate is excluded — for EVERY family, including the two whose
# aggregate does not exist in the real corpus.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("family", sorted(BRONZE_FAMILIES))
def test_aggregate_excluded_for_every_family(tmp_path, family):
    """Both shapes present, so the assertion distinguishes 'excluded it' from
    'there was nothing to exclude'."""
    _write(tmp_path, f"{family}_alpha.json", [{"id": 1}])
    _write(tmp_path, f"{family}_beta.json", [{"id": 2}])
    _write(tmp_path, aggregate_name(family), [{"id": 1}, {"id": 2}])

    found = bronze_files(tmp_path, family)
    names = [p.name for p in found]

    assert aggregate_name(family) not in names
    assert names == [f"{family}_alpha.json", f"{family}_beta.json"]


@pytest.mark.parametrize("family", sorted(RECORD_FAMILIES))
def test_aggregate_would_have_doubled_the_count(tmp_path, family):
    """State the defect positively: without the exclusion the records repeat.

    This is what the wrong number actually looked like, three times.

    Record families only — a document family has one object per repository, so
    "the aggregate repeats the records" is not a shape it can take. The
    exclusion itself is still asserted for all six in
    ``test_aggregate_excluded_for_every_family``.
    """
    _write(tmp_path, f"{family}_alpha.json", [{"id": 1}])
    _write(tmp_path, f"{family}_beta.json", [{"id": 2}])
    _write(tmp_path, aggregate_name(family), [{"id": 1}, {"id": 2}])

    via_helper = list(bronze_records(tmp_path, family))
    unguarded = sorted(tmp_path.glob(f"{family}_*.json"))

    assert len(via_helper) == 2
    # The unguarded glob sees three files holding four records: exactly double.
    assert len(unguarded) == 3
    total = sum(len(json.loads(p.read_text())) for p in unguarded)
    assert total == 4


# --------------------------------------------------------------------------
# Derived variants: excluded, but by suffix, not substring.
# --------------------------------------------------------------------------


def test_with_stats_variant_is_excluded(tmp_path):
    """``commits_<repo>_with_stats.json`` is an enriched copy of
    ``commits_<repo>.json`` and sits beside it.

    The current extractor writes none and ``fga-eps-mds`` has none, so measuring
    the corpus says this guard is dead. It is not: 4f37edc/4507f9a record 142 of
    these in a repository analysed by an earlier pipeline version, with
    ``commits_2023.2_Gotinha.json`` and ``commits_2023.2_Gotinha_with_stats.json``
    side by side. The fixture invents what the corpus no longer contains.
    """
    _write(tmp_path, "commits_Gotinha.json", [{"sha": "a"}])
    _write(tmp_path, "commits_Gotinha_with_stats.json", [{"sha": "a", "stats": {}}])

    found = [p.name for p in bronze_files(tmp_path, "commits")]

    assert found == ["commits_Gotinha.json"]
    assert list(bronze_records(tmp_path, "commits")) == [("Gotinha", {"sha": "a"})]


def test_a_repo_named_with_stats_something_is_NOT_excluded(tmp_path):
    """The substring guard's false positive, asserted as a real requirement.

    ``"_with_stats" in name`` also drops ``commits_with_stats_repo.json`` — a
    repository legitimately named ``with_stats_repo``, whose data is then simply
    missing. A guard against duplicates must not silently delete originals, so
    the exclusion matches a suffix.
    """
    _write(tmp_path, "commits_with_stats_repo.json", [{"sha": "a"}])

    found = [p.name for p in bronze_files(tmp_path, "commits")]

    assert found == ["commits_with_stats_repo.json"]
    # and the old spelling would have dropped it
    assert "_with_stats" in "commits_with_stats_repo.json"


@pytest.mark.parametrize("family", sorted(BRONZE_FAMILIES))
def test_derived_suffix_excluded_for_every_family(tmp_path, family):
    _write(tmp_path, f"{family}_alpha.json", [{"id": 1}])
    _write(tmp_path, f"{family}_alpha_with_stats.json", [{"id": 1, "stats": {}}])

    assert [p.name for p in bronze_files(tmp_path, family)] == [f"{family}_alpha.json"]


# --------------------------------------------------------------------------
# Families must not bleed into one another.
# --------------------------------------------------------------------------


def test_issues_does_not_capture_issue_events(tmp_path):
    """The dead guard's premise, asserted rather than assumed.

    ``generate_members_ai.py`` carried ``"issue_events" in name`` as an exclusion
    on the ``issues`` glob. It never fired, because the prefixes diverge at
    character six. That is a property of the naming scheme, so it belongs in a
    test — if a future family breaks it, the helper must be what notices.
    """
    _write(tmp_path, "issues_alpha.json", [{"id": 1}])
    _write(tmp_path, "issue_events_alpha.json", [{"id": 2}])

    assert [p.name for p in bronze_files(tmp_path, "issues")] == ["issues_alpha.json"]
    assert [p.name for p in bronze_files(tmp_path, "issue_events")] == [
        "issue_events_alpha.json"
    ]


def test_repo_family_does_not_capture_other_families(tmp_path):
    """``repo_`` is a prefix of nothing else, but assert it rather than trust it."""
    _write(tmp_path, "repo_alpha.json", [{"id": 1}])
    for other in sorted(BRONZE_FAMILIES - {"repo"}):
        _write(tmp_path, f"{other}_alpha.json", [{"id": 9}])

    assert [p.name for p in bronze_files(tmp_path, "repo")] == ["repo_alpha.json"]


# --------------------------------------------------------------------------
# repo_of strips the prefix, not every occurrence.
# --------------------------------------------------------------------------


def test_repo_of_strips_prefix_not_every_occurrence(tmp_path):
    """``str.replace`` mangles a repository whose name contains the family token.

    The three shipped call sites all used
    ``name.replace("commits_", "").replace(".json", "")``. ``fga-eps-mds`` has no
    such repository, which is precisely why the bug would have shipped unseen —
    so the fixture invents the case reality has not produced yet.
    """
    assert repo_of("commits_2024-commits_api.json", "commits") == "2024-commits_api"
    assert repo_of("issues_my-issues_tracker.json", "issues") == "my-issues_tracker"

    # The old spelling, for contrast — this is what it would have returned.
    old = "commits_2024-commits_api.json".replace("commits_", "").replace(".json", "")
    assert old == "2024-api"
    assert old != repo_of("commits_2024-commits_api.json", "commits")


def test_repo_of_rejects_a_file_of_another_family():
    with pytest.raises(ValueError, match="not a commits file"):
        repo_of("prs_alpha.json", "commits")


def test_repo_of_rejects_an_unknown_family():
    with pytest.raises(ValueError, match="unknown bronze family"):
        repo_of("commits_alpha.json", "commit")


# --------------------------------------------------------------------------
# Ordering, because #172 made determinism a property we assert.
# --------------------------------------------------------------------------


def test_enumeration_is_sorted(tmp_path):
    """Directory order is not stable across filesystems; artifacts built from an
    unstable order differ byte-for-byte between runs over identical input."""
    for name in ["commits_zeta.json", "commits_alpha.json", "commits_mu.json"]:
        _write(tmp_path, name, [{"id": 1}])

    assert [p.name for p in bronze_files(tmp_path, "commits")] == [
        "commits_alpha.json",
        "commits_mu.json",
        "commits_zeta.json",
    ]


# --------------------------------------------------------------------------
# bronze_records: metadata sidecars, repo attribution, loud failure.
# --------------------------------------------------------------------------


def test_records_strip_metadata_sidecars(tmp_path):
    _write(tmp_path, "commits_alpha.json", [{"_metadata": {"n": 1}}, {"sha": "a"}])

    records = list(bronze_records(tmp_path, "commits"))

    assert records == [("alpha", {"sha": "a"})]


def test_records_carry_their_repository(tmp_path):
    _write(tmp_path, "commits_alpha.json", [{"sha": "a"}])
    _write(tmp_path, "commits_beta.json", [{"sha": "b"}])

    assert list(bronze_records(tmp_path, "commits")) == [
        ("alpha", {"sha": "a"}),
        ("beta", {"sha": "b"}),
    ]


def test_unparseable_file_raises_rather_than_lowering_the_count(tmp_path):
    """Skipping a corrupt file would reduce the count silently — the same defect
    as counting the aggregate twice, pointing the other way."""
    _write(tmp_path, "commits_alpha.json", [{"sha": "a"}])
    (tmp_path / "commits_beta.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        list(bronze_records(tmp_path, "commits"))


# --------------------------------------------------------------------------
# Payload shape. A family whose shape is misread yields zero without raising —
# which is the defect this module exists to prevent, so it is tested per family.
# --------------------------------------------------------------------------


def _payload_for(family):
    """The shape this family is actually written in."""
    if family in DOCUMENT_FAMILIES:
        return {"owner": "org", "repository": "alpha"}
    return [{"id": 1}, {"id": 2}]


@pytest.mark.parametrize("family", sorted(BRONZE_FAMILIES))
def test_every_family_yields_records(tmp_path, family):
    """The regression test for the bug this module had against itself.

    ``bronze_records`` first assumed every family was a JSON list. Iterating a
    ``structure`` file then yielded its top-level *keys* — strings, all filtered
    out by the ``isinstance(record, dict)`` check — so 486 files produced zero
    records and nothing raised. Run against the real corpus it printed a clean
    table with ``structure 0`` and ``repo 0`` sitting in it.

    Parametrised over every family so a family added later cannot quietly
    default to zero.
    """
    _write(tmp_path, f"{family}_alpha.json", _payload_for(family))

    records = list(bronze_records(tmp_path, family))

    assert records, f"{family} yielded no records from a non-empty file"
    assert all(repo == "alpha" for repo, _ in records)
    assert all(isinstance(rec, dict) for _, rec in records)


def test_families_are_partitioned_by_shape():
    """Every family declares exactly one shape. A family in neither set would
    fall through to the record branch and be misread; one in both is ambiguous."""
    assert RECORD_FAMILIES | DOCUMENT_FAMILIES == BRONZE_FAMILIES
    assert not (RECORD_FAMILIES & DOCUMENT_FAMILIES)


def test_document_family_with_a_list_payload_raises(tmp_path):
    _write(tmp_path, "structure_alpha.json", [{"owner": "org"}])
    with pytest.raises(ValueError, match="expected a JSON object"):
        list(bronze_records(tmp_path, "structure"))


def test_record_family_with_an_object_payload_raises(tmp_path):
    _write(tmp_path, "commits_alpha.json", {"sha": "a"})
    with pytest.raises(ValueError, match="expected a JSON list"):
        list(bronze_records(tmp_path, "commits"))


def test_empty_directory_returns_empty_for_a_VALID_family(tmp_path):
    """The one case where empty is the honest answer, kept distinct from the
    typo case above: a valid family with no files really is zero."""
    assert bronze_files(tmp_path, "commits") == []
    assert list(bronze_records(tmp_path, "commits")) == []


# --------------------------------------------------------------------------
# Retiring the aggregates (#170): the writer removes, it does not merely
# stop writing. A regeneration runs over an existing data/bronze/, so the
# file a previous run left would survive as a stale lookalike.
# --------------------------------------------------------------------------


def test_remove_aggregate_deletes_a_present_aggregate(tmp_path):
    _write(tmp_path, "commits_all.json", [{"sha": "a"}])
    _write(tmp_path, "commits_alpha.json", [{"sha": "b"}])

    removed = remove_aggregate(tmp_path, "commits")

    assert removed == tmp_path / "commits_all.json"
    assert not (tmp_path / "commits_all.json").exists()
    # Only the aggregate: the per-repository file beside it stays.
    assert (tmp_path / "commits_alpha.json").exists()


def test_remove_aggregate_on_a_fresh_tree_is_a_no_op(tmp_path):
    """Writers call this unconditionally, including where no aggregate ever
    existed — the removal must not raise on the fresh-regeneration path."""
    assert remove_aggregate(tmp_path, "commits") is None


def test_remove_aggregate_rejects_an_unknown_family(tmp_path):
    with pytest.raises(ValueError, match="unknown bronze family"):
        remove_aggregate(tmp_path, "commit")


def test_remove_aggregate_spares_the_silver_artifact(tmp_path):
    """The removal rule is scoped to Bronze's four aggregates — nothing wider.

    ``data/silver/language_analysis_all.json`` shares the ``_all`` suffix and
    nothing else: it is a Silver artifact the dashboard fetches. No corpus
    exercises this distinction, so the fixture is invented here on purpose —
    every family is swept, which is the strongest form of the rule a caller
    could apply by mistake.
    """
    bronze = tmp_path / "data" / "bronze"
    bronze.mkdir(parents=True)
    for family in sorted(RECORD_FAMILIES):
        _write(bronze, f"{family}_all.json", [{"stale": True}])

    silver = tmp_path / "data" / "silver"
    silver.mkdir(parents=True)
    artifact = silver / "language_analysis_all.json"
    payload = json.dumps([{"language": "Python", "bytes": 120}])
    artifact.write_text(payload, encoding="utf-8")

    for family in sorted(RECORD_FAMILIES):
        remove_aggregate(bronze, family)

    for family in sorted(RECORD_FAMILIES):
        assert not (bronze / f"{family}_all.json").exists(), family
    assert artifact.exists()
    assert artifact.read_text(encoding="utf-8") == payload
