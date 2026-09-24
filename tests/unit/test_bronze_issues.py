"""
Unit tests for coops/bronze/issues.py
Tests extraction of issues, pull requests, and issue events.
"""
import pytest
from unittest.mock import MagicMock, patch
from coops.bronze.issues import extract_issues


class TestExtractIssues:
    """Tests for extract_issues function"""
    
    def test_extract_issues_success(self):
        """Testa extração bem-sucedida de issues"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_config.org_name = "test-org"
        
        mock_repos = [
            {"name": "repo1", "full_name": "test-org/repo1"}
        ]
        
        mock_issues = [
            {
                "number": 1,
                "title": "Test issue",
                "state": "open",
                "body": "Issue body"
            }
        ]
        
        mock_client.get_paginated.side_effect = [mock_issues, []]  # issues, events
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json") as mock_save:
                result = extract_issues(mock_client, mock_config)
                
                # Deve salvar 2 arquivos: issues_repo1 e issue_events_repo1.
                # Os agregados _all não são mais escritos (#170).
                assert len(result) == 2
                assert mock_save.call_count == 2
                calls = [call_[0][1] for call_ in mock_save.call_args_list]
                assert not any("_all.json" in c for c in calls)
    
    def test_extract_issues_separates_prs_from_issues(self):
        """Testa que separa PRs de issues"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_mixed_data = [
            {"number": 1, "title": "Issue"},
            {"number": 2, "title": "PR", "pull_request": {"url": "pr_url"}}
        ]
        
        mock_client.get_paginated.side_effect = [mock_mixed_data, []]
        
        saved_data = {}
        def capture_save(data, path):
            saved_data[path] = data
            return path
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', side_effect=capture_save):
                extract_issues(mock_client, mock_config)
                
                # Verifica que salvou arquivos separados
                assert any('issues_' in k for k in saved_data.keys())
                assert any('prs_' in k for k in saved_data.keys())
    
    def test_extract_issues_no_repositories(self, capsys):
        """Testa retorno vazio quando não há repositórios"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        with patch('coops.bronze.issues.load_json_data', return_value=None):
            result = extract_issues(mock_client, mock_config)
            
            assert result == []
            captured = capsys.readouterr()
            assert "No repositories found" in captured.out
    
    def test_extract_issues_filters_event_fields(self):
        """Testa que filtra campos dos eventos para reduzir tamanho"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_events = [
            {
                "id": 123,
                "event": "closed",
                "created_at": "2024-01-01",
                "actor": {"login": "user1", "avatar_url": "url", "type": "User"},
                "issue": {"number": 1, "title": "Issue", "state": "closed"},
                "label": {"name": "bug"},  # Campo extra que deve ser filtrado
                "unnecessary_field": "data"  # Campo desnecessário
            }
        ]
        
        mock_client.get_paginated.side_effect = [[], mock_events]  # issues, events
        
        saved_data = {}
        def capture_save(data, path):
            saved_data[path] = data
            return path
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', side_effect=capture_save):
                extract_issues(mock_client, mock_config)
                
                # Verifica que evento foi filtrado
                events_key = next((k for k in saved_data.keys() if 'issue_events_' in k), None)
                assert events_key is not None
                
                filtered_event = saved_data[events_key][0]
                # Verifica campos mantidos
                assert 'id' in filtered_event
                assert 'event' in filtered_event
                assert 'created_at' in filtered_event
                assert 'repo_name' in filtered_event
                assert 'actor' in filtered_event
                assert 'issue' in filtered_event
                # Verifica campos removidos
                assert 'label' not in filtered_event
                assert 'unnecessary_field' not in filtered_event
                # Verifica que actor foi simplificado
                assert filtered_event['actor'] == {'login': 'user1'}
    
    def test_extract_issues_handles_empty_issues(self):
        """Testa que lida com repositórios sem issues"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_client.get_paginated.side_effect = [None, None]  # Sem issues, sem events
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json") as mock_save:
                result = extract_issues(mock_client, mock_config)
                
                # Sem issues e sem events: só o arquivo de events por repo
                # (salvo sempre); os agregados _all não são mais escritos (#170)
                assert len(result) == 1
                calls = [call_[0][1] for call_ in mock_save.call_args_list]
                assert any("issue_events_repo1" in c for c in calls)
                assert not any("_all.json" in c for c in calls)
    
    def test_extract_issues_saves_per_repo_files(self):
        """Testa que salva arquivos individuais por repositório"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [
            {"name": "repo1", "full_name": "test-org/repo1"},
            {"name": "repo2", "full_name": "test-org/repo2"}
        ]
        
        mock_issues = [{"number": 1, "title": "Issue"}]
        mock_events = [{"id": 1, "event": "closed", "created_at": "2024-01-01", "actor": {"login": "u"}, "issue": {"number": 1}}]
        
        mock_client.get_paginated.side_effect = [
            mock_issues, mock_events,  # repo1
            mock_issues, mock_events   # repo2
        ]
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json") as mock_save:
                extract_issues(mock_client, mock_config)
                
                # Verifica que salvou arquivos por repo
                calls = [call[0][1] for call in mock_save.call_args_list]
                assert any("issues_repo1" in c for c in calls)
                assert any("issues_repo2" in c for c in calls)
                assert any("issue_events_repo1" in c for c in calls)
                assert any("issue_events_repo2" in c for c in calls)
    
    def test_extract_issues_retires_stale_aggregates(self, tmp_path, monkeypatch):
        """Uma execução sobre um bronze existente remove os agregados _all antigos.

        Não basta parar de escrever (#170): a regeneração roda sobre um
        ``data/bronze/`` existente, e um agregado velho ao lado de arquivos
        por repositório atuais parece atual — pior do que manter ou remover.
        A remoção acontece no pipeline, onde a escrita acontecia.
        """
        monkeypatch.chdir(tmp_path)
        bronze = tmp_path / "data" / "bronze"
        bronze.mkdir(parents=True)
        for name in ("issues_all.json", "prs_all.json", "issue_events_all.json"):
            (bronze / name).write_text('[{"stale": true}]', encoding="utf-8")

        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_client.get_paginated.return_value = []

        with patch('coops.bronze.issues.load_json_data',
                   return_value=[{"name": "repo1", "full_name": "test-org/repo1"}]):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json") as mock_save:
                extract_issues(mock_client, mock_config)

                for name in ("issues_all.json", "prs_all.json", "issue_events_all.json"):
                    assert not (bronze / name).exists(), f"{name} deveria ser removido"
                calls = [call_[0][1] for call_ in mock_save.call_args_list]
                assert not any("_all.json" in c for c in calls)

    def test_extract_issues_removal_spares_silver(self, tmp_path, monkeypatch):
        """A regra de remoção não pode alcançar data/silver.

        ``language_analysis_all.json`` é um artefato Silver buscado pelo
        dashboard: compartilha o sufixo ``_all`` e nada mais. O corpus não
        exercita isso, então o fixture aqui é inventado de propósito.
        """
        monkeypatch.chdir(tmp_path)
        bronze = tmp_path / "data" / "bronze"
        bronze.mkdir(parents=True)
        for name in ("issues_all.json", "prs_all.json", "issue_events_all.json"):
            (bronze / name).write_text('[{"stale": true}]', encoding="utf-8")
        silver = tmp_path / "data" / "silver"
        silver.mkdir(parents=True)
        silver_file = silver / "language_analysis_all.json"
        payload = '[{"language": "Python", "bytes": 120}]'
        silver_file.write_text(payload, encoding="utf-8")

        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_client.get_paginated.return_value = []

        with patch('coops.bronze.issues.load_json_data',
                   return_value=[{"name": "repo1", "full_name": "test-org/repo1"}]):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json"):
                extract_issues(mock_client, mock_config)

                assert silver_file.exists()
                assert silver_file.read_text(encoding="utf-8") == payload

    def test_extract_issues_uses_cache_flag(self):
        """Testa que respeita flag use_cache"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json"):
                extract_issues(mock_client, mock_config, use_cache=False)
                
                # Verifica que use_cache foi passado
                for call in mock_client.get_paginated.call_args_list:
                    assert call[1].get('use_cache') is False
    
    def test_extract_issues_constructs_correct_urls(self):
        """Testa que constrói URLs corretas"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json"):
                extract_issues(mock_client, mock_config)
                
                # Verifica URLs chamadas
                calls = [call[0][0] for call in mock_client.get_paginated.call_args_list]
                assert any("/repos/test-org/repo1/issues" in c for c in calls)
                assert any("/repos/test-org/repo1/issues/events" in c for c in calls)
    
    def test_extract_issues_skips_invalid_repos(self, capsys):
        """Testa que pula repositórios inválidos"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [
            None,
            "invalid",
            {"name": "valid", "full_name": "test-org/valid"}
        ]
        
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json"):
                extract_issues(mock_client, mock_config)
                
                captured = capsys.readouterr()
                assert "Skipping invalid repo entry" in captured.out
    
    def test_extract_issues_skips_metadata(self):
        """Testa que ignora entrada _metadata"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [
            {"_metadata": {"timestamp": "2024-01-01"}},
            {"name": "repo1", "full_name": "test-org/repo1"}
        ]
        
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json"):
                extract_issues(mock_client, mock_config)
                
                # Deve processar apenas 1 repo (ignorando _metadata)
                # 2 chamadas por repo (issues + events)
                assert mock_client.get_paginated.call_count == 2
    
    def test_extract_issues_adds_repo_name_to_issues(self):
        """Testa que adiciona repo_name a cada issue"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "test-repo", "full_name": "test-org/test-repo"}]
        mock_issues = [{"number": 1, "title": "Issue"}]
        
        mock_client.get_paginated.side_effect = [mock_issues, []]
        
        saved_data = {}
        def capture_save(data, path):
            saved_data[path] = data
            return path
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', side_effect=capture_save):
                extract_issues(mock_client, mock_config)
                
                issues_key = next((k for k in saved_data.keys() if 'issues_test-repo' in k), None)
                assert issues_key is not None
                assert saved_data[issues_key][0]['repo_name'] == 'test-repo'
    
    def test_extract_issues_adds_repo_name_to_prs(self):
        """Testa que adiciona repo_name a cada PR"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "test-repo", "full_name": "test-org/test-repo"}]
        mock_prs = [{"number": 1, "title": "PR", "pull_request": {"url": "url"}}]
        
        mock_client.get_paginated.side_effect = [mock_prs, []]
        
        saved_data = {}
        def capture_save(data, path):
            saved_data[path] = data
            return path
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', side_effect=capture_save):
                extract_issues(mock_client, mock_config)
                
                prs_key = next((k for k in saved_data.keys() if 'prs_test-repo' in k), None)
                assert prs_key is not None
                assert saved_data[prs_key][0]['repo_name'] == 'test-repo'
    
    def test_extract_issues_prints_summary(self, capsys):
        """Testa que exibe resumo de extração"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_issues = [
            {"number": 1, "title": "Issue 1"},
            {"number": 2, "title": "PR", "pull_request": {"url": "url"}},
            {"number": 3, "title": "Issue 2"}
        ]
        
        mock_events = [
            {"id": 1, "event": "closed", "created_at": "2024-01-01", "actor": {"login": "u"}, "issue": {"number": 1}}
        ]
        
        mock_client.get_paginated.side_effect = [mock_issues, mock_events]
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json"):
                extract_issues(mock_client, mock_config)
                
                captured = capsys.readouterr()
                assert "Extracted" in captured.out
                assert "2 issues" in captured.out
                assert "1 PRs" in captured.out
                assert "1 events" in captured.out
    
    def test_extract_issues_handles_event_without_actor(self):
        """Testa que lida com eventos sem actor"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_events = [
            {"id": 1, "event": "closed", "created_at": "2024-01-01", "actor": None, "issue": {"number": 1}}
        ]
        
        mock_client.get_paginated.side_effect = [[], mock_events]
        
        saved_data = {}
        def capture_save(data, path):
            saved_data[path] = data
            return path
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', side_effect=capture_save):
                extract_issues(mock_client, mock_config)
                
                # Verifica que evento foi salvo com actor=None
                events_key = next((k for k in saved_data.keys() if 'issue_events_' in k), None)
                assert events_key is not None
                assert saved_data[events_key][0]['actor'] is None
    
    def test_extract_issues_handles_event_without_issue(self):
        """Testa que lida com eventos sem issue"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_events = [
            {"id": 1, "event": "closed", "created_at": "2024-01-01", "actor": {"login": "u"}, "issue": None}
        ]
        
        mock_client.get_paginated.side_effect = [[], mock_events]
        
        saved_data = {}
        def capture_save(data, path):
            saved_data[path] = data
            return path
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', side_effect=capture_save):
                extract_issues(mock_client, mock_config)
                
                # Verifica que evento foi salvo com issue=None
                events_key = next((k for k in saved_data.keys() if 'issue_events_' in k), None)
                assert events_key is not None
                assert saved_data[events_key][0]['issue'] is None
    
    def test_extract_issues_only_saves_pr_files_if_prs_exist(self):
        """Testa que só salva arquivo de PRs se houver PRs no repo"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        # Apenas issues, sem PRs
        mock_issues = [{"number": 1, "title": "Issue"}]
        
        mock_client.get_paginated.side_effect = [mock_issues, []]
        
        with patch('coops.bronze.issues.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.issues.save_json_data', return_value="file.json") as mock_save:
                extract_issues(mock_client, mock_config)
                
                # Deve salvar: issues_repo1, issue_events_repo1 (sem agregados _all, #170)
                # NÃO deve salvar prs_repo1
                calls = [call[0][1] for call in mock_save.call_args_list]
                assert any("issues_repo1" in c for c in calls)
                assert not any("prs_repo1" in c for c in calls)
                assert not any("_all.json" in c for c in calls)


class TestExtractIssuesCaps:
    """max_issues/max_prs cap what is kept; pagination is bounded only when both are set."""

    REPOS = [{"name": "repo1", "full_name": "test-org/repo1"}]
    MIXED = (
        [{"number": i, "title": f"Issue {i}"} for i in range(5)]
        + [{"number": 100 + i, "title": f"PR {i}", "pull_request": {"url": "u"}} for i in range(5)]
    )

    def _run(self, **caps):
        client = MagicMock()
        client.get_paginated.side_effect = [list(self.MIXED), []]  # issues, events
        saved = {}

        def capture_save(data, path):
            saved[path] = data
            return path

        with patch('coops.bronze.issues.load_json_data', return_value=self.REPOS):
            with patch('coops.bronze.issues.save_json_data', side_effect=capture_save):
                extract_issues(client, MagicMock(), **caps)
        issues_call = client.get_paginated.call_args_list[0]
        return issues_call.kwargs['max_pages'], saved

    def test_no_caps_fetches_every_page(self):
        max_pages, saved = self._run()
        assert max_pages is None
        assert len(saved['data/bronze/issues_repo1.json']) == 5
        assert len(saved['data/bronze/prs_repo1.json']) == 5

    def test_only_max_issues_does_not_truncate_prs(self):
        max_pages, saved = self._run(max_issues=2)
        assert max_pages is None
        assert len(saved['data/bronze/issues_repo1.json']) == 2
        assert len(saved['data/bronze/prs_repo1.json']) == 5

    def test_only_max_prs_does_not_truncate_issues(self):
        max_pages, saved = self._run(max_prs=1)
        assert max_pages is None
        assert len(saved['data/bronze/issues_repo1.json']) == 5
        assert len(saved['data/bronze/prs_repo1.json']) == 1

    @pytest.mark.parametrize("caps, expected_pages", [
        ({"max_issues": 20, "max_prs": 5}, 1),
        ({"max_issues": 20, "max_prs": 250}, 3),
    ])
    def test_both_caps_bound_pages_by_the_larger(self, caps, expected_pages):
        max_pages, saved = self._run(**caps)
        assert max_pages == expected_pages
        assert len(saved['data/bronze/issues_repo1.json']) == min(5, caps["max_issues"])
        assert len(saved['data/bronze/prs_repo1.json']) == min(5, caps["max_prs"])
