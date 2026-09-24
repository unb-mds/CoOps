#!/usr/bin/env python3

import logging
from typing import List, Dict, Any, Optional

from coops.utils.github_api import GitHubAPIClient, OrganizationConfig, save_json_data, load_json_data, OfflineCacheMiss
from coops.bronze.watermarks import WatermarkStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_repository_structure(
    client: GitHubAPIClient, 
    config: OrganizationConfig, 
    use_cache: bool = True,
    watermarks: Optional[WatermarkStore] = None,
) -> List[str]:
    """
    Extrai estrutura de arquivos de todos os repositórios filtrados.
    
    Ordem de tentativa:
    1. REST API com recursive=1 (mais rápido)
    2. GraphQL (fallback se REST truncar)
    
    Args:
        client: Cliente da API do GitHub
        config: Configuração da organização
        use_cache: Se deve usar cache
    
    Returns:
        Lista de caminhos dos arquivos structure_{repo}.json gerados
    """
    logger.info("="*60)
    logger.info("🌳 EXTRACTING REPOSITORY STRUCTURES")
    logger.info("="*60)
    
    # Carregar repositórios filtrados
    filtered_repos = load_json_data("data/bronze/repositories_filtered.json")
    
    if not filtered_repos:
        logger.warning("⚠️  No filtered repositories found. Run repository extraction first.")
        return []
    
    # Remover metadata se existir
    if isinstance(filtered_repos, list) and len(filtered_repos) > 0:
        if isinstance(filtered_repos[0], dict) and '_metadata' in filtered_repos[0]:
            filtered_repos = filtered_repos[1:]
    
    generated_files = []
    successful = 0
    failed = 0
    
    for repo in filtered_repos:
        if not repo or not isinstance(repo, dict):
            logger.warning(f"Skipping invalid repo entry: {repo}")
            failed += 1
            continue
        
        repo_name = repo.get('name', 'unknown')
        full_name = repo.get('full_name', repo_name)
        default_branch = repo.get('default_branch', 'main')
        
        # Extrair owner do full_name
        if '/' not in full_name:
            logger.warning(f"Skipping {repo_name}: invalid full_name format")
            failed += 1
            continue
        
        owner, name_only = full_name.split('/', 1)
        
        logger.info(f"\n📂 Processing: {repo_name}")
        logger.info(f"   Owner: {owner}")
        logger.info(f"   Branch: {default_branch}")
        
        wm = watermarks.get(full_name) if watermarks is not None else None
        prior_sha = (wm.head_shas or {}).get(default_branch) if wm else None

        # Incremental extraction (issue #110): a tree only changes when its
        # branch head moves, so when the head sha is unchanged we reuse the
        # previous run's structure file instead of re-fetching the (expensive)
        # tree. The branch-head probe is a single cached request.
        if prior_sha:
            branch_data = client.get_with_cache(
                f"https://api.github.com/repos/{owner}/{name_only}/branches/{default_branch}",
                use_cache=use_cache,
            )
            head_sha = (branch_data or {}).get('commit', {}).get('sha')
            prior_path = f"data/bronze/structure_{repo_name}.json"
            if head_sha and head_sha == prior_sha and load_json_data(prior_path) is not None:
                logger.info(f"   ♻️  Head unchanged ({head_sha[:8]}); reusing {prior_path}")
                generated_files.append(prior_path)
                successful += 1
                if watermarks is not None:
                    watermarks.update(full_name)
                continue

        try:
            # 🚀 TRY REST FIRST (100x faster)
            logger.info(f"   Method: REST API (recursive=1)")
            structure = client.get_repository_tree(
                owner=owner,
                repo=name_only,
                branch=default_branch,
                use_cache=use_cache
            )
            
            # Check if truncated (fallback to GraphQL)
            if structure.get('truncated', False):
                logger.warning(f"   ⚠️  REST tree truncated, falling back to GraphQL...")
                structure = client.graphql_repository_tree(
                    owner=owner,
                    repo=name_only,
                    branch=default_branch,
                    use_cache=use_cache
                )
            
            # Validar dados mínimos
            if not structure or not structure.get('tree'):
                logger.warning(f"   ⚠️  No files found in {repo_name}")
                failed += 1
                continue
            
            total_items = len(structure['tree'])
            
            if total_items == 0:
                logger.warning(f"   ⚠️  Empty tree for {repo_name}")
                failed += 1
                continue
            
            # Adicionar metadados do repositório
            structure['repository_metadata'] = {
                'id': repo.get('id'),
                'full_name': full_name,
                'description': repo.get('description'),
                'created_at': repo.get('created_at'),
                'updated_at': repo.get('updated_at'),
                'language': repo.get('language'),
                'size': repo.get('size'),
                'stars': repo.get('stargazers_count', 0),
                'forks': repo.get('forks_count', 0),
                'open_issues': repo.get('open_issues_count', 0),
            }
            
            # Salvar structure_{repo}.json
            output_file = save_json_data(
                structure,
                f"data/bronze/structure_{repo_name}.json",
                timestamp=False
            )
            
            generated_files.append(output_file)
            successful += 1

            # Record the branch head sha so the next run can skip an unchanged tree.
            if watermarks is not None and structure.get('sha'):
                head_shas = dict((wm.head_shas or {}) if wm else {})
                head_shas[default_branch] = structure['sha']
                watermarks.update(full_name, head_shas=head_shas)
            elif watermarks is not None:
                watermarks.update(full_name)
            
            method = structure.get('method', 'unknown')
            logger.info(f"   ✅ Saved: {output_file}")
            logger.info(f"   📊 Files: {total_items} (method: {method})")
            
        except OfflineCacheMiss:
            # An offline replay miss must stop the run, not count this
            # repository as "failed" and continue (#199).
            raise
        except Exception as e:
            logger.error(f"   ❌ Error extracting {repo_name}: {str(e)}")
            failed += 1
            continue
    
    # Resumo final
    logger.info("\n" + "="*60)
    logger.info(f"✅ Successful: {successful}")
    logger.info(f"❌ Failed: {failed}")
    logger.info(f"📁 Total files generated: {len(generated_files)}")
    logger.info("="*60)
    
    return generated_files