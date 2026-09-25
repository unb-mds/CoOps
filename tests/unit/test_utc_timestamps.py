"""Timestamps persistidos são UTC consciente de fuso, com o offset no
próprio valor (#143).

``datetime.now()`` grava um naive local: uma execução no GitHub Actions
carimba UTC e uma execução local carimba -03:00, com nada no valor dizendo
qual — dois corpora ficam incomparáveis (#201 item 1 depende disto). Estes
testes cobrem os pontos de escrita do ``github_api``; os pontos de
``gold_aggregate`` e ``registry_manager`` têm testes nos seus próprios
arquivos.
"""

import json

from coops.utils.github_api import (
    GitHubAPIClient,
    save_json_data,
    update_data_registry,
)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_save_json_data_dict_extracted_at_is_utc(tmp_path, monkeypatch):
    """O sidecar _metadata de um dict carrega extracted_at com +00:00."""
    monkeypatch.chdir(tmp_path)
    save_json_data({"answer": 42}, "out/artifact.json")

    stored = _read(tmp_path / "out" / "artifact.json")
    assert stored["_metadata"]["extracted_at"].endswith("+00:00")


def test_save_json_data_list_extracted_at_is_utc(tmp_path, monkeypatch):
    """O sidecar _metadata de uma lista não vazia também."""
    monkeypatch.chdir(tmp_path)
    save_json_data([{"member": "a"}], "out/members.json")

    stored = _read(tmp_path / "out" / "members.json")
    assert stored[0]["_metadata"]["extracted_at"].endswith("+00:00")


def test_update_data_registry_updated_at_is_utc(tmp_path, monkeypatch):
    """updated_at do registry.json por camada carrega +00:00."""
    monkeypatch.chdir(tmp_path)
    update_data_registry("bronze", "issues", ["data/bronze/issues_x.json"])

    stored = _read(tmp_path / "data" / "bronze" / "registry.json")
    assert stored["issues"]["updated_at"].endswith("+00:00")


def test_empty_tree_response_extracted_at_is_utc(tmp_path, monkeypatch):
    """Mesmo o payload de erro da árvore carimba extracted_at com +00:00."""
    monkeypatch.chdir(tmp_path)
    client = GitHubAPIClient("token-test")
    response = client._empty_tree_response("org", "repo", "main", error="boom")

    assert response["extracted_at"].endswith("+00:00")
