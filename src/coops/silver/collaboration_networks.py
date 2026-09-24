#!/usr/bin/env python3
"""
Collaboration networks processing for Silver layer
Analyzes collaboration patterns and creates network metrics
"""

from collections import defaultdict
from typing import List, Dict, Any, Set
from coops.utils.github_api import save_json_data
from coops.silver.bronze_input import load_family

def process_collaboration_networks() -> List[str]:
    """Process collaboration data into network metrics"""

    # Load bronze data: per-repository files, not the _all aggregates
    # (redundant concatenations of exactly these records — issue #170).
    issues_data = load_family("issues")
    prs_data = load_family("prs")
    commits_data = load_family("commits")
    issue_events_data = load_family("issue_events")

    # Track collaborations by repository
    repo_collaborators = defaultdict(set)

    # Collect contributors per repository
    for issue in issues_data:
        repo = issue.get('repo_name', 'unknown')
        if issue.get('user', {}).get('login'):
            repo_collaborators[repo].add(issue['user']['login'])
        if issue.get('assignee', {}) and issue['assignee'].get('login'):
            repo_collaborators[repo].add(issue['assignee']['login'])

    for pr in prs_data:
        repo = pr.get('repo_name', 'unknown')
        if pr.get('user', {}).get('login'):
            repo_collaborators[repo].add(pr['user']['login'])

    # Squad improvement: more robust commit author extraction
    for commit in commits_data:
        commits_identifier = None

        # Try the 'author' field first (GitHub user)
        commit_author = commit.get('author', {})
        if isinstance(commit_author, dict) and commit_author.get('login'):
            commits_identifier = commit_author.get('login')

        # If not found, try 'commit.author.login'
        if not commits_identifier:
            commit_info = commit.get('commit', {})
            if isinstance(commit_info, dict):
                commit_author_info = commit_info.get('author', {})
                if isinstance(commit_author_info, dict) and commit_author_info.get('login'):
                    commits_identifier = commit_author_info.get('login')

        repo = commit.get('repo_name', 'unknown')
        if commits_identifier:
            repo_collaborators[repo].add(commits_identifier)

    # Squad improvement: more robust event actor extraction
    for event in issue_events_data:
        event_actor = event.get('actor', {})
        if isinstance(event_actor, dict) and event_actor.get('login'):
            events_identifier = event_actor.get('login')
        else:
            events_identifier = None
        repo = event.get('repo_name', 'unknown')
        if events_identifier:
            repo_collaborators[repo].add(events_identifier)

    # Create collaboration edges with weights
    # Track all repo collaborations per user pair
    collaboration_repos = defaultdict(list)
    user_collaborations = defaultdict(set)

    for repo, contributors in repo_collaborators.items():
        contributors_list = sorted(contributors)

        # Create edges between all contributors in the same repo
        for i, user1 in enumerate(contributors_list):
            for user2 in contributors_list[i+1:]:
                if user1 != user2:
                    # Create bidirectional edge key
                    edge_key = tuple(sorted([user1, user2]))
                    collaboration_repos[edge_key].append(repo)

                    user_collaborations[user1].add(user2)
                    user_collaborations[user2].add(user1)

    # Convert to edge format with weights
    collaboration_edges = []
    for (source, target), repos in collaboration_repos.items():
        collaboration_edges.append({
            'source': source,
            'target': target,
            'weight': len(repos),  # Weight = number of repos they collaborate on
            'repos': repos,
            'collaboration_type': 'same_repository'
        })

    # Sort by weight descending
    collaboration_edges.sort(key=lambda x: x['weight'], reverse=True)

    generated_files = []

    # Save collaboration edges
    edges_file = save_json_data(
        collaboration_edges,
        "data/silver/collaboration_edges.json"
    )
    generated_files.append(edges_file)

    # Create user collaboration metrics
    user_metrics = []
    for user, collaborators in user_collaborations.items():
        user_metrics.append({
            'user': user,
            'collaborator_count': len(collaborators),
            'collaborators': sorted(collaborators),
            'repositories_contributed': len([repo for repo, contributors in repo_collaborators.items() if user in contributors])
        })

    user_metrics.sort(key=lambda x: x['collaborator_count'], reverse=True)

    user_metrics_file = save_json_data(
        user_metrics,
        "data/silver/user_collaboration_metrics.json"
    )
    generated_files.append(user_metrics_file)

    # Create repository collaboration analysis
    repo_analysis = []
    for repo, contributors in repo_collaborators.items():
        contributors_list = sorted(contributors)

        # Calculate potential and actual collaborations
        potential_collaborations = len(contributors_list) * (len(contributors_list) - 1) // 2
        actual_collaborations = len([e for e in collaboration_edges if repo in e['repos']])

        repo_analysis.append({
            'repo': repo,
            'contributor_count': len(contributors_list),
            'contributors': contributors_list,
            'potential_collaborations': potential_collaborations,
            'actual_collaborations': actual_collaborations,
            'collaboration_density': actual_collaborations / potential_collaborations if potential_collaborations > 0 else 0
        })

    repo_analysis.sort(key=lambda x: x['contributor_count'], reverse=True)

    repo_analysis_file = save_json_data(
        repo_analysis,
        "data/silver/repository_collaboration_analysis.json"
    )
    generated_files.append(repo_analysis_file)

    # Identify cross-repository collaborators (hubs)
    cross_repo_contributors = {}
    for user in user_collaborations.keys():
        repos_contributed = [repo for repo, contributors in repo_collaborators.items() if user in contributors]
        if len(repos_contributed) > 1:
            cross_repo_contributors[user] = {
                'user': user,
                'repositories': repos_contributed,
                'repo_count': len(repos_contributed),
                'total_collaborators': len(user_collaborations[user])
            }

    if cross_repo_contributors:
        cross_repo_list = list(cross_repo_contributors.values())
        cross_repo_list.sort(key=lambda x: x['repo_count'], reverse=True)

        cross_repo_file = save_json_data(
            cross_repo_list,
            "data/silver/cross_repository_hubs.json"
        )
        generated_files.append(cross_repo_file)

    # Create network summary statistics
    network_stats = {
        'total_users': len(user_collaborations),
        'total_collaborations': len(collaboration_edges),
        'total_repositories': len(repo_collaborators),
        'cross_repo_contributors': len(cross_repo_contributors),
        'avg_collaborators_per_user': sum(len(collaborators) for collaborators in user_collaborations.values()) / len(user_collaborations) if user_collaborations else 0,
        'avg_contributors_per_repo': sum(len(contributors) for contributors in repo_collaborators.values()) / len(repo_collaborators) if repo_collaborators else 0
    }

    stats_file = save_json_data(
        network_stats,
        "data/silver/network_statistics.json"
    )
    generated_files.append(stats_file)

    print(f"Processed collaboration networks: {network_stats['total_users']} users, {network_stats['total_collaborations']} connections")
    return generated_files
