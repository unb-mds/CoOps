"""
Unit tests for coops/bronze/commits.py
Tests commit extraction with GraphQL and REST methods, 
including fallback logic, active branches, and time chunks.
"""
import json
import re
import pytest
from unittest.mock import MagicMock, patch, call
from coops.bronze.commits import extract_commits, _hash_email, _sanitize_commit


class TestExtractCommits:
    """Tests for extract_commits function"""
    
    def test_extract_commits_rest_method_success(self):
        """Testa extração de commits usando método REST"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_config.org_name = "test-org"
        
        mock_repos = [
            {"name": "repo1", "full_name": "test-org/repo1"}
        ]
        
        mock_commits = [
            {
                "sha": "abc123",
                "commit": {
                    "author": {"name": "Author", "email": "a@test.com", "date": "2024-01-01T00:00:00Z"}
                },
                "author": {"login": "author_login"}
            }
        ]
        
        mock_details = {
            "stats": {"additions": 10, "deletions": 5, "total": 15}
        }
        
        mock_client.get_paginated.return_value = mock_commits
        mock_client.get_with_cache.return_value = mock_details
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json") as mock_save:
                result = extract_commits(mock_client, mock_config, method="rest")
                
                # Verifica que chamou get_paginated para commits
                assert mock_client.get_paginated.called
                call_url = mock_client.get_paginated.call_args[0][0]
                assert "test-org/repo1/commits" in call_url
                
                # Verifica que salvou arquivos: só commits_repo1.json; o
                # agregado commits_all.json não é mais escrito (#170)
                assert len(result) == 1
                assert mock_save.call_count == 1
    
    def test_extract_commits_graphql_method_success(self, capsys):
        """Testa extração de commits usando método GraphQL"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_config.org_name = "test-org"
        
        mock_repos = [
            {"name": "repo1", "full_name": "test-org/repo1"}
        ]
        
        mock_graphql_nodes = [
            {
                "oid": "abc123",
                "url": "https://github.com/test-org/repo1/commit/abc123",
                "messageHeadline": "Test commit",
                "committedDate": "2024-01-01T00:00:00Z",
                "additions": 10,
                "deletions": 5,
                "author": {
                    "name": "Author",
                    "email": "a@test.com",
                    "date": "2024-01-01T00:00:00Z",
                    "user": {"login": "author_login"}
                }
            }
        ]
        
        mock_client.graphql_commit_history.return_value = (mock_graphql_nodes, {})
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                result = extract_commits(mock_client, mock_config, method="graphql")
                
                # Verifica que chamou GraphQL
                assert mock_client.graphql_commit_history.called
                
                captured = capsys.readouterr()
                assert "via GraphQL" in captured.out
                assert len(result) > 0
    
    def test_extract_commits_graphql_falls_back_to_rest(self, capsys):
        """Testa que GraphQL faz fallback para REST quando retorna vazio"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_config.org_name = "test-org"
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        # GraphQL retorna vazio
        mock_client.graphql_commit_history.return_value = ([], {})
        
        # REST retorna commits
        mock_commits = [
            {
                "sha": "abc123",
                "commit": {
                    "author": {"name": "Author", "email": "a@test.com", "date": "2024-01-01T00:00:00Z"}
                }
            }
        ]
        
        mock_client.get_paginated.return_value = mock_commits
        mock_client.get_with_cache.return_value = {"stats": {"additions": 10, "deletions": 5, "total": 15}}
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                result = extract_commits(mock_client, mock_config, method="graphql")
                
                captured = capsys.readouterr()
                assert "Falling back to REST" in captured.out
                assert "via REST fallback" in captured.out
    
    def test_extract_commits_no_repositories(self, capsys):
        """Testa que retorna lista vazia quando não há repositórios"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        with patch('coops.bronze.commits.load_json_data', return_value=None):
            result = extract_commits(mock_client, mock_config)
            
            assert result == []
            captured = capsys.readouterr()
            assert "No repositories found" in captured.out
    
    def test_extract_commits_with_time_range(self):
        """Testa extração de commits com filtros since/until"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_config.org_name = "test-org"
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                extract_commits(
                    mock_client, 
                    mock_config, 
                    method="rest",
                    since="2024-01-01",
                    until="2024-12-31"
                )
                
                # Verifica que URL contém filtros
                call_url = mock_client.get_paginated.call_args[0][0]
                assert "since=2024-01-01" in call_url
                assert "until=2024-12-31" in call_url
    
    def test_extract_commits_with_active_branches(self, capsys):
        """Testa extração com branches ativas habilitadas"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_config.org_name = "test-org"
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        # Mock branches ativas
        mock_branches = ["feature-branch", "develop"]
        mock_client.get_active_unmerged_branches.return_value = mock_branches
        mock_client.graphql_commit_history.return_value = ([], {})
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                extract_commits(
                    mock_client,
                    mock_config,
                    method="graphql",
                    include_active_branches=True,
                    active_days=30
                )
                
                # Verifica que buscou branches ativas
                assert mock_client.get_active_unmerged_branches.called
                
                captured = capsys.readouterr()
                assert "Finding active unmerged branches" in captured.out
                assert "2 unmerged branches" in captured.out
    
    def test_extract_commits_skips_invalid_repos(self, capsys):
        """Testa que pula repositórios inválidos"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [
            None,
            {"name": "invalid"},  # Sem full_name
            {"name": "valid", "full_name": "test-org/valid"}
        ]
        
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                extract_commits(mock_client, mock_config, method="rest")
                
                captured = capsys.readouterr()
                assert "Skipping invalid repo entry" in captured.out
    
    def test_extract_commits_skips_metadata_entry(self):
        """Testa que ignora entrada _metadata"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [
            {"_metadata": {"timestamp": "2024-01-01"}},
            {"name": "repo1", "full_name": "test-org/repo1"}
        ]
        
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                result = extract_commits(mock_client, mock_config, method="rest")
                
                # Deve processar apenas 1 repo (ignorando _metadata)
                assert mock_client.get_paginated.call_count == 1
    
    def test_extract_commits_saves_per_repo_files(self):
        """Testa que salva arquivo individual por repositório"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [
            {"name": "repo1", "full_name": "test-org/repo1"},
            {"name": "repo2", "full_name": "test-org/repo2"}
        ]
        
        mock_commits = [
            {"sha": "abc", "commit": {"author": {"name": "A", "email": "a@test.com", "date": "2024-01-01"}}}
        ]
        
        mock_client.get_paginated.return_value = mock_commits
        mock_client.get_with_cache.return_value = {"stats": {"additions": 10, "deletions": 5, "total": 15}}
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json") as mock_save:
                extract_commits(mock_client, mock_config, method="rest")
                
                # 2 arquivos por repo; o agregado commits_all.json não é mais
                # escrito (#170)
                assert mock_save.call_count == 2
                
                # Verifica que salvou commits_repo1.json e commits_repo2.json
                calls = [call[0][1] for call in mock_save.call_args_list]
                assert any("commits_repo1.json" in c for c in calls)
                assert any("commits_repo2.json" in c for c in calls)
    
    def test_extract_commits_removes_stale_aggregate(self, tmp_path, monkeypatch):
        """Uma execução sobre bronze existente remove o commits_all.json antigo.

        Não basta parar de escrever (#170): a regeneração roda sobre um
        ``data/bronze/`` existente, e o agregado velho — o maior arquivo da
        árvore — continuaria contado por qualquer glob da família.
        """
        monkeypatch.chdir(tmp_path)
        bronze = tmp_path / "data" / "bronze"
        bronze.mkdir(parents=True)
        stale = bronze / "commits_all.json"
        stale.write_text('[{"stale": true}]', encoding="utf-8")

        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_config.org_name = "test-org"
        mock_client.get_paginated.return_value = []
        mock_client.get_with_cache.return_value = {}

        with patch('coops.bronze.commits.load_json_data',
                   return_value=[{"name": "repo1", "full_name": "test-org/repo1"}]):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json") as mock_save:
                extract_commits(mock_client, mock_config, method="rest")

                assert not stale.exists()
                calls = [call_[0][1] for call_ in mock_save.call_args_list]
                assert not any("_all.json" in c for c in calls)
    
    def test_extract_commits_uses_cache_flag(self):
        """Testa que respeita flag use_cache"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                extract_commits(mock_client, mock_config, use_cache=False)
                
                # Verifica que use_cache foi passado
                call_kwargs = mock_client.get_paginated.call_args[1]
                assert call_kwargs.get('use_cache') is False
    
    def test_extract_commits_rest_fetches_commit_details(self):
        """Testa que REST busca detalhes (stats) de cada commit"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_commits = [
            {"sha": "abc123", "commit": {"author": {"name": "A", "email": "a@t.com", "date": "2024-01-01"}}},
            {"sha": "def456", "commit": {"author": {"name": "B", "email": "b@t.com", "date": "2024-01-02"}}}
        ]
        
        mock_client.get_paginated.return_value = mock_commits
        mock_client.get_with_cache.return_value = {"stats": {"additions": 10, "deletions": 5, "total": 15}}
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                extract_commits(mock_client, mock_config, method="rest")
                
                # Deve buscar detalhes para cada commit (2 commits)
                assert mock_client.get_with_cache.call_count == 2
    
    def test_extract_commits_graphql_maps_fields_correctly(self):
        """Testa que GraphQL mapeia campos para formato REST compatível"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_graphql_nodes = [
            {
                "oid": "abc123",
                "url": "https://github.com/test-org/repo1/commit/abc123",
                "messageHeadline": "Test commit",
                "committedDate": "2024-01-01T00:00:00Z",
                "additions": 15,
                "deletions": 8,
                "author": {
                    "name": "Author Name",
                    "email": "author@test.com",
                    "date": "2024-01-01T00:00:00Z",
                    "user": {"login": "author_user", "databaseId": 456}
                }
            }
        ]
        
        mock_client.graphql_commit_history.return_value = (mock_graphql_nodes, {})
        
        saved_data = None
        def capture_save(data, path):
            nonlocal saved_data
            saved_data = data
            return "file.json"
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', side_effect=capture_save):
                extract_commits(mock_client, mock_config, method="graphql")
                
                # Verifica mapeamento de campos
                assert saved_data is not None
                first_commit = saved_data[0]
                assert first_commit['sha'] == 'abc123'
                assert first_commit['html_url'] == 'https://github.com/test-org/repo1/commit/abc123'
                assert first_commit['commit']['message'] == 'Test commit'
                assert first_commit['commit']['author']['name'] == 'Author Name'
                assert first_commit['commit']['author']['login'] == 'author_user'
                assert first_commit['commit']['author']['id'] == 456
                assert 'email' not in first_commit['commit']['author']
                assert first_commit['additions'] == 15
                assert first_commit['deletions'] == 8
                assert first_commit['total_changes'] == 23
    
    def test_extract_commits_graphql_handles_missing_user(self):
        """Testa que GraphQL lida com autor sem user object"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_graphql_nodes = [
            {
                "oid": "abc123",
                "url": "url",
                "messageHeadline": "Test",
                "committedDate": "2024-01-01",
                "additions": 10,
                "deletions": 5,
                "author": {
                    "name": "Author",
                    "email": "a@test.com",
                    "date": "2024-01-01",
                    "user": None  # Sem user
                }
            }
        ]
        
        mock_client.graphql_commit_history.return_value = (mock_graphql_nodes, {})
        
        saved_data = None
        def capture_save(data, path):
            nonlocal saved_data
            saved_data = data
            return "file.json"
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', side_effect=capture_save):
                extract_commits(mock_client, mock_config, method="graphql")
                
                # Deve funcionar sem erros: autor sem user vira hash de email
                assert saved_data is not None
                author = saved_data[0]['commit']['author']
                assert 'login' not in author
                assert 'id' not in author
                assert 'email' not in author
                assert author['author_email_hash'] == _hash_email('a@test.com')
    
    def test_extract_commits_rest_copies_author_login(self):
        """Testa que REST copia author.login para commit.author.login"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_commits = [
            {
                "sha": "abc123",
                "author": {"login": "username123"},
                "commit": {
                    "author": {"name": "Name", "email": "e@test.com", "date": "2024-01-01"}
                }
            }
        ]
        
        mock_client.get_paginated.return_value = mock_commits
        mock_client.get_with_cache.return_value = {"stats": {"additions": 10, "deletions": 5, "total": 15}}
        
        saved_data = None
        def capture_save(data, path):
            nonlocal saved_data
            saved_data = data
            return "file.json"
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', side_effect=capture_save):
                extract_commits(mock_client, mock_config, method="rest")
                
                # Verifica que copiou login
                assert saved_data is not None
                first_commit = saved_data[0]
                assert first_commit['commit']['author']['login'] == 'username123'
    
    def test_extract_commits_graphql_skips_repo_without_owner(self, capsys):
        """Testa que GraphQL pula repos sem owner/name identificável"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [
            {"name": "invalid-repo", "full_name": "single-name"}  # Sem '/' para split
        ]
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                extract_commits(mock_client, mock_config, method="graphql")
                
                captured = capsys.readouterr()
                assert "cannot determine owner/name for GraphQL" in captured.out
    
    def test_extract_commits_prints_total_count(self, capsys):
        """Testa que exibe contagem total de commits"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        
        mock_commits = [
            {"sha": "abc", "commit": {"author": {"name": "A", "email": "a@t.com", "date": "2024-01-01"}}},
            {"sha": "def", "commit": {"author": {"name": "B", "email": "b@t.com", "date": "2024-01-02"}}}
        ]
        
        mock_client.get_paginated.return_value = mock_commits
        mock_client.get_with_cache.return_value = {"stats": {"additions": 10, "deletions": 5, "total": 15}}
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                extract_commits(mock_client, mock_config)
                
                captured = capsys.readouterr()
                assert "Total commits extracted: 2" in captured.out
    
    def test_extract_commits_passes_graphql_parameters(self):
        """Testa que passa parâmetros corretos para graphql_commit_history"""
        mock_client = MagicMock()
        mock_config = MagicMock()
        
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]
        mock_client.graphql_commit_history.return_value = ([], {})
        mock_client.get_paginated.return_value = []
        
        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', return_value="file.json"):
                extract_commits(
                    mock_client,
                    mock_config,
                    method="graphql",
                    since="2024-01-01",
                    until="2024-12-31",
                    max_commits_per_repo=100,
                    page_size=25,
                    time_chunks=5  # Nota: Este parâmetro é ignorado pelo código que tem hardcoded time_chunks=3
                )
                
                # Verifica parâmetros
                call_kwargs = mock_client.graphql_commit_history.call_args[1]
                assert call_kwargs.get('since') == "2024-01-01"
                assert call_kwargs.get('until') == "2024-12-31"
                assert call_kwargs.get('max_commits') == 100
                assert call_kwargs.get('page_size') == 25
                # O código tem time_chunks=3 hardcoded, não usa o parâmetro
                assert call_kwargs.get('time_chunks') == 3
                assert call_kwargs.get('split_large_extractions') is True

    def test_extract_commits_rest_redacts_author_emails(self):
        """REST commits drop raw emails: linked authors keep login+id, unlinked get a hash."""
        mock_client = MagicMock()
        mock_config = MagicMock()

        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]

        mock_commits = [
            {
                "sha": "abc123",
                "author": {"login": "linked_user", "id": 4242},
                "commit": {
                    "author": {"name": "Linked", "email": "linked@test.com", "date": "2024-01-01"},
                    "committer": {"name": "Linked", "email": "committer@test.com", "date": "2024-01-01"},
                },
            },
            {
                "sha": "def456",
                "author": None,
                "commit": {
                    "author": {"name": "Unlinked", "email": "Unlinked@Test.com", "date": "2024-01-02"},
                },
            },
        ]

        mock_client.get_paginated.return_value = mock_commits
        mock_client.get_with_cache.return_value = {"stats": {"additions": 1, "deletions": 0, "total": 1}}

        saved_data = None
        def capture_save(data, path):
            nonlocal saved_data
            saved_data = data
            return "file.json"

        with patch('coops.bronze.commits.load_json_data', return_value=mock_repos):
            with patch('coops.bronze.commits.save_json_data', side_effect=capture_save):
                extract_commits(mock_client, mock_config, method="rest")

        assert saved_data is not None
        assert len(saved_data) == 2

        linked = saved_data[0]
        assert "email" not in linked["commit"]["author"]
        assert "email" not in linked["commit"]["committer"]
        assert linked["commit"]["author"]["login"] == "linked_user"
        assert linked["commit"]["author"]["id"] == 4242

        unlinked = saved_data[1]
        assert "email" not in unlinked["commit"]["author"]
        assert unlinked["commit"]["author"]["author_email_hash"] == _hash_email("unlinked@test.com")
        assert "login" not in unlinked["commit"]["author"]


class TestHashEmail:
    def test_hash_email_trims_and_lowercases(self):
        assert _hash_email("  Dev@Example.com ") == _hash_email("dev@example.com")
        assert len(_hash_email("dev@example.com")) == 64
        assert _hash_email("dev@example.com") != _hash_email("other@example.com")


class TestSanitizeCommit:
    def test_linked_author_keeps_login_and_id_drops_email(self):
        commit = {
            "sha": "abc123",
            "author": {"login": "alice", "id": 1001},
            "commit": {
                "author": {"name": "Alice", "email": "alice@example.com", "date": "2024-01-01"},
                "committer": {"name": "Alice", "email": "alice@example.com", "date": "2024-01-01"},
                "message": "hi",
            },
        }
        out = _sanitize_commit(commit)
        assert "email" not in out["commit"]["author"]
        assert "email" not in out["commit"]["committer"]
        assert out["commit"]["author"]["login"] == "alice"
        assert out["commit"]["author"]["id"] == 1001
        # #101/#171: a linked author now keeps the hash TOO. This assertion was
        # `"author_email_hash" not in ...` and is deliberately inverted, not
        # quietly relaxed — it encoded the gate that made the two identifier
        # spaces disjoint, which is the defect being fixed. The raw address
        # still never survives, which is what the first assertion above pins.
        assert out["commit"]["author"]["author_email_hash"] == _hash_email(
            "alice@example.com"
        )

    def test_linked_and_unlinked_authors_share_one_hash_space(self):
        """The join #171 needs: the same address hashes identically whether or
        not the author has an account.

        Before #101 the hash was kept only when `not login and numeric_id is
        None`, so no record ever carried both a login and a hash. Measured over
        all 130,186 commits in the corpus: 123,562 had an id and no hash, 6,614
        a hash and no id, and **zero had both** — so nothing downstream could
        learn that a hash and a login were the same human, and 1,311 linked
        people plus 231 unlinked hashes were counted as 1,542 contributors with
        no way to reduce it.
        """
        shared = "same.person@example.com"
        linked = _sanitize_commit(
            {
                "sha": "a",
                "author": {"login": "sameperson", "id": 7},
                "commit": {
                    "author": {"name": "Same", "email": shared, "date": "2024-01-01"},
                    "message": "x",
                },
            }
        )
        unlinked = _sanitize_commit(
            {
                "sha": "b",
                "author": None,
                "commit": {
                    "author": {"name": "Same", "email": shared, "date": "2024-01-02"},
                    "message": "y",
                },
            }
        )

        assert linked["commit"]["author"]["login"] == "sameperson"
        assert "login" not in unlinked["commit"]["author"]
        # the same person, now joinable across both records
        assert (
            linked["commit"]["author"]["author_email_hash"]
            == unlinked["commit"]["author"]["author_email_hash"]
            == _hash_email(shared)
        )

    def test_the_raw_address_never_survives_for_a_linked_author(self):
        """The rule the hash exists to serve, re-pinned now that linked authors
        carry one: publishing a hash must not become publishing an address."""
        out = _sanitize_commit(
            {
                "sha": "a",
                "author": {"login": "alice", "id": 1001},
                "commit": {
                    "author": {
                        "name": "Alice",
                        "email": "alice@example.com",
                        "date": "2024-01-01",
                    },
                    "committer": {
                        "name": "Alice",
                        "email": "alice@example.com",
                        "date": "2024-01-01",
                    },
                    "message": "hi",
                },
            }
        )
        # assert on the WHOLE serialised record, not a field we remembered to check
        assert "alice@example.com" not in json.dumps(out)
        assert "@" not in json.dumps(out)

    def test_unlinked_author_gets_email_hash_drops_email(self):
        commit = {
            "sha": "abc123",
            "author": None,
            "commit": {
                "author": {"name": "Unknown Dev", "email": " Dev@Example.com ", "date": "2024-01-01"},
                "message": "hi",
            },
        }
        out = _sanitize_commit(commit)
        assert "email" not in out["commit"]["author"]
        assert out["commit"]["author"]["author_email_hash"] == _hash_email("dev@example.com")
        assert "login" not in out["commit"]["author"]
        assert "id" not in out["commit"]["author"]

    def test_does_not_mutate_input(self):
        commit = {
            "sha": "abc123",
            "author": {"login": "alice", "id": 1},
            "commit": {"author": {"name": "A", "email": "a@test.com", "date": "d"}},
        }
        _sanitize_commit(commit)
        assert commit["commit"]["author"]["email"] == "a@test.com"

    def test_drops_verification_payload_carrying_raw_address(self):
        """The REST paths persist the raw API response, whose signed payload
        embeds the address as free text where an email-key sweep can't see it."""
        commit = {
            "sha": "abc123",
            "author": {"login": "alice", "id": 1},
            "commit": {
                "author": {"name": "A", "email": "alice@example.com", "date": "d"},
                "message": "feat: thing",
                "verification": {
                    "verified": True,
                    "reason": "valid",
                    "signature": "-----BEGIN PGP SIGNATURE-----\n...",
                    "payload": (
                        "tree 6a0d5591\nparent 870f6f57\n"
                        "author A <alice@example.com> 1700000000 +0000\n"
                        "committer GitHub <noreply@github.com> 1700000000 +0000\n"
                    ),
                },
            },
        }
        out = _sanitize_commit(commit)
        assert "verification" not in out["commit"]
        assert "alice@example.com" not in json.dumps(out)
        assert "noreply@github.com" not in json.dumps(out)

    def test_scrubs_addresses_from_commit_message_trailers(self):
        """The REST paths keep the full message, and this repo's own history
        carries Co-authored-by trailers with personal addresses."""
        commit = {
            "sha": "abc123",
            "author": {"login": "alice", "id": 1},
            "commit": {
                "author": {"name": "A", "email": "alice@example.com", "date": "d"},
                "message": (
                    "feat: add the thing\n\n"
                    "Co-authored-by: Pair Person <pair@personal.example.net>\n"
                    "Signed-off-by: Mona Octocat <mona@github.example.com>\n"
                ),
            },
        }
        out = _sanitize_commit(commit)
        message = out["commit"]["message"]
        # No address survives anywhere in the record.
        assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", json.dumps(out))
        # Credit is preserved: the trailer and the human name stay.
        assert "Co-authored-by: Pair Person" in message
        assert "Signed-off-by: Mona Octocat" in message
        assert "feat: add the thing" in message

    def test_address_shaped_author_name_blanked(self):
        """An author name that is itself an email address is replaced with
        None. The address is asserted present *before* sanitization (control),
        so a clean result afterwards proves the scrub ran rather than a broken
        probe."""
        commit = {
            "sha": "abc123",
            "author": None,
            "commit": {
                "author": {"name": "ingr.dcg@gmail.com", "email": "ingr.dcg@gmail.com", "date": "2024-01-01"},
                "message": "hi",
            },
        }
        # Control: the address is present in the name field before the scrub.
        assert commit["commit"]["author"]["name"] == "ingr.dcg@gmail.com"
        assert _EMAIL_RE.fullmatch(commit["commit"]["author"]["name"])

        out = _sanitize_commit(commit)

        assert out["commit"]["author"]["name"] is None
        # The address is gone from the record; the unlinked author still has a
        # stable identity key instead of a leaked address.
        assert "email" not in out["commit"]["author"]
        assert out["commit"]["author"]["author_email_hash"] == _hash_email("ingr.dcg@gmail.com")

    def test_address_shaped_committer_name_blanked(self):
        """The committer name is a second free-text channel for the same
        address; it is blanked the same way, while the linked author's normal
        name is left intact."""
        commit = {
            "sha": "abc123",
            "author": {"login": "alice", "id": 1},
            "commit": {
                "author": {"name": "Alice", "email": "alice@example.com", "date": "d"},
                "committer": {"name": "joaok8@gmail.com", "email": "joaok8@gmail.com", "date": "d"},
                "message": "hi",
            },
        }
        # Control: the address is present in the committer name before the scrub.
        assert commit["commit"]["committer"]["name"] == "joaok8@gmail.com"
        assert _EMAIL_RE.fullmatch(commit["commit"]["committer"]["name"])

        out = _sanitize_commit(commit)

        assert out["commit"]["committer"]["name"] is None
        assert out["commit"]["author"]["name"] == "Alice"

    def test_non_address_name_left_untouched(self):
        """Attribution matters: a plain name is never blanked, only a name that
        is itself an address."""
        commit = {
            "sha": "abc123",
            "author": {"login": "alice", "id": 1},
            "commit": {
                "author": {"name": "Alice Smith", "email": "alice@example.com", "date": "d"},
                "committer": {"name": "Bob Jones", "email": "bob@example.com", "date": "d"},
                "message": "hi",
            },
        }
        out = _sanitize_commit(commit)
        assert out["commit"]["author"]["name"] == "Alice Smith"
        assert out["commit"]["committer"]["name"] == "Bob Jones"

    def test_name_containing_address_left_untouched(self):
        """The scrub blanks a name only when the name *is* an address; a name
        that merely *contains* one survives, because blanking it would destroy
        attribution for a real person.

        This fixture is invented, and must stay invented. Across the 260,350
        name slots in the fga corpus, 149 are entirely an address and zero
        merely contain one, so the distinguishing case does not occur in real
        data and cannot be defended with it (definition-of-done: when reality
        never produced the boundary, write a synthetic fixture and say so). It
        pins ``fullmatch`` over ``search``: under ``search`` this name is
        blanked, and this test is the only thing that fails.
        """
        # Direction 1: a name that IS an address is blanked.
        address_commit = {
            "sha": "abc123",
            "author": None,
            "commit": {
                "author": {"name": "alice@example.com", "email": "alice@example.com", "date": "2024-01-01"},
                "message": "hi",
            },
        }
        assert _EMAIL_RE.fullmatch(address_commit["commit"]["author"]["name"])
        assert _sanitize_commit(address_commit)["commit"]["author"]["name"] is None

        # Direction 2: a name that merely CONTAINS an address is left alone.
        contains_commit = {
            "sha": "abc123",
            "author": {"login": "alice", "id": 1},
            "commit": {
                "author": {"name": "Alice <alice@example.com>", "email": "alice@example.com", "date": "2024-01-01"},
                "message": "hi",
            },
        }
        # Control: the address really is present inside the name, but the name
        # as a whole is not an address — the boundary ``fullmatch`` draws.
        assert not _EMAIL_RE.fullmatch(contains_commit["commit"]["author"]["name"])
        assert _EMAIL_RE.search(contains_commit["commit"]["author"]["name"])
        assert _sanitize_commit(contains_commit)["commit"]["author"]["name"] == "Alice <alice@example.com>"


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_FULL_BODY = "feat: subject\n\nThis is the body.\nSecond paragraph."


def _assert_recovered_fields(commit):
    """The message body, committer and parent shas survive in the stored record,
    in the same shape on both the GraphQL and REST paths."""
    assert commit["commit"]["message"] == _FULL_BODY
    assert commit["commit"]["committer"]["name"] == "Committer"
    assert commit["commit"]["committer"]["date"] == "2024-01-01T00:00:01Z"
    assert "email" not in commit["commit"]["committer"]
    assert commit["parents"] == ["parent1", "parent2"]


class TestRecoverableFields:
    """The fields that can only be recovered by a full re-fetch (message body,
    committer, parent shas) must reach the stored record on every path."""

    def test_graphql_path_keeps_recoverable_fields(self):
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]

        mock_graphql_nodes = [
            {
                "oid": "abc123",
                "url": "https://github.com/test-org/repo1/commit/abc123",
                "message": _FULL_BODY,
                "messageHeadline": "feat: subject",
                "committedDate": "2024-01-01T00:00:00Z",
                "author": {
                    "name": "Author",
                    "email": "author@test.com",
                    "date": "2024-01-01T00:00:00Z",
                    "user": {"login": "author_login", "databaseId": 456},
                },
                "committer": {
                    "name": "Committer",
                    "email": "committer@test.com",
                    "date": "2024-01-01T00:00:01Z",
                    "user": {"login": "committer_login", "databaseId": 789},
                },
                "parents": {"nodes": [{"oid": "parent1"}, {"oid": "parent2"}]},
                "additions": 15,
                "deletions": 8,
            }
        ]

        mock_client.graphql_commit_history.return_value = (mock_graphql_nodes, {})

        saved_data = None

        def capture_save(data, path):
            nonlocal saved_data
            saved_data = data
            return "file.json"

        with patch("coops.bronze.commits.load_json_data", return_value=mock_repos):
            with patch("coops.bronze.commits.save_json_data", side_effect=capture_save):
                extract_commits(mock_client, mock_config, method="graphql")

        assert saved_data is not None and len(saved_data) == 1
        _assert_recovered_fields(saved_data[0])

    def test_rest_path_keeps_recoverable_fields(self):
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]

        mock_commits = [
            {
                "sha": "abc123",
                "author": {"login": "author_login", "id": 456},
                "commit": {
                    "author": {"name": "Author", "email": "author@test.com", "date": "2024-01-01T00:00:00Z"},
                    "committer": {"name": "Committer", "email": "committer@test.com", "date": "2024-01-01T00:00:01Z"},
                    "message": _FULL_BODY,
                },
                "parents": [
                    {"sha": "parent1", "url": "https://api.github.com/x/parent1", "html_url": "https://github.com/x/parent1"},
                    {"sha": "parent2", "url": "https://api.github.com/x/parent2", "html_url": "https://github.com/x/parent2"},
                ],
            }
        ]

        mock_client.get_paginated.return_value = mock_commits
        mock_client.get_with_cache.return_value = {"stats": {"additions": 10, "deletions": 5, "total": 15}}

        saved_data = None

        def capture_save(data, path):
            nonlocal saved_data
            saved_data = data
            return "file.json"

        with patch("coops.bronze.commits.load_json_data", return_value=mock_repos):
            with patch("coops.bronze.commits.save_json_data", side_effect=capture_save):
                extract_commits(mock_client, mock_config, method="rest")

        assert saved_data is not None and len(saved_data) == 1
        _assert_recovered_fields(saved_data[0])

    def test_no_email_reaches_bronze_from_message_trailers_or_committer(self):
        """A commit whose message body carries Co-authored-by / Signed-off-by
        trailers and whose committer has an email must leave no address in the
        serialised Bronze projection. Asserted by regex on the output, not by
        field names."""
        mock_client = MagicMock()
        mock_config = MagicMock()
        mock_repos = [{"name": "repo1", "full_name": "test-org/repo1"}]

        mock_graphql_nodes = [
            {
                "oid": "abc123",
                "url": "https://github.com/test-org/repo1/commit/abc123",
                "message": (
                    "feat: subject\n\n"
                    "Co-authored-by: Pair Person <pair@personal.example.net>\n"
                    "Signed-off-by: Mona Octocat <mona@github.example.com>\n"
                ),
                "messageHeadline": "feat: subject",
                "committedDate": "2024-01-01T00:00:00Z",
                "author": {
                    "name": "Author",
                    "email": "author@test.com",
                    "date": "2024-01-01T00:00:00Z",
                    "user": {"login": "author_login", "databaseId": 456},
                },
                "committer": {
                    "name": "Committer",
                    "email": "committer@test.com",
                    "date": "2024-01-01T00:00:01Z",
                    "user": {"login": "committer_login", "databaseId": 789},
                },
                "parents": {"nodes": [{"oid": "parent1"}]},
                "additions": 15,
                "deletions": 8,
            }
        ]

        mock_client.graphql_commit_history.return_value = (mock_graphql_nodes, {})

        saved_data = None

        def capture_save(data, path):
            nonlocal saved_data
            saved_data = data
            return "file.json"

        with patch("coops.bronze.commits.load_json_data", return_value=mock_repos):
            with patch("coops.bronze.commits.save_json_data", side_effect=capture_save):
                extract_commits(mock_client, mock_config, method="graphql")

        assert saved_data is not None
        serialized = json.dumps(saved_data)
        # No raw address survives anywhere in the serialised record.
        assert not _EMAIL_RE.search(serialized)
        # Credit is preserved: the trailer and the human name stay.
        assert "Co-authored-by: Pair Person" in serialized
        assert "Signed-off-by: Mona Octocat" in serialized
