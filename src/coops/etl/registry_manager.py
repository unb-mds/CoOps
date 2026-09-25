#!/usr/bin/env python3
"""
Data registry management for tracking all generated files and their relationships
"""

import argparse
import os
import json
from datetime import datetime, timezone
from typing import Dict, List, Any
from coops.utils.github_api import load_json_data, save_json_data

#: The Bronze inputs the record-based Silver processors read: the
#: per-repository files enumerated by ``coops.silver.bronze_input``. The
#: ``<family>_all.json`` aggregates that used to be listed here were retired
#: (#170) — the registry must not advertise files the pipeline no longer
#: writes, and these processors no longer read them.
PER_REPOSITORY_BRONZE_INPUTS = [
    'data/bronze/issues_<repo>.json',
    'data/bronze/prs_<repo>.json',
    'data/bronze/commits_<repo>.json',
    'data/bronze/issue_events_<repo>.json'
]

def create_master_registry() -> str:
    """Create master registry that maps all data files across layers"""
    
    master_registry = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'layers': {
            'bronze': {},
            'silver': {},
            'gold': {}
        },
        'data_lineage': {},
        'file_inventory': []
    }
    
    # Scan bronze layer
    bronze_files = scan_data_directory('data/bronze')
    master_registry['layers']['bronze'] = categorize_bronze_files(bronze_files)
    
    # Scan silver layer
    silver_files = scan_data_directory('data/silver')
    master_registry['layers']['silver'] = categorize_silver_files(silver_files)
    
    # Create data lineage mapping
    master_registry['data_lineage'] = create_data_lineage()
    
    # Create complete file inventory
    all_files = bronze_files + silver_files
    master_registry['file_inventory'] = [{
        'file_path': f,
        'layer': 'bronze' if 'bronze' in f else 'silver' if 'silver' in f else 'gold',
        'size_bytes': os.path.getsize(f) if os.path.exists(f) else 0,
        'modified_at': datetime.fromtimestamp(os.path.getmtime(f)).isoformat() if os.path.exists(f) else None
    } for f in all_files]
    
    # Save master registry
    registry_file = save_json_data(
        master_registry,
        "data/master_registry.json",
        timestamp=False
    )
    
    return registry_file

def scan_data_directory(directory: str) -> List[str]:
    """Scan directory for JSON files"""
    files = []
    if os.path.exists(directory):
        for root, dirs, filenames in os.walk(directory):
            for filename in filenames:
                if filename.endswith('.json'):
                    files.append(os.path.join(root, filename))
    return files

def categorize_bronze_files(files: List[str]) -> Dict[str, List[str]]:
    """Categorize bronze layer files by data type"""
    categories = {
        'repositories': [],
        'members': [],
        'issues': [],
        'prs': [],
        'commits': [],
        'events': [],
        'raw': []
    }
    
    for file_path in files:
        filename = os.path.basename(file_path)
        
        if 'repo' in filename.lower():
            categories['repositories'].append(file_path)
        elif 'member' in filename.lower():
            categories['members'].append(file_path)
        elif 'issue' in filename.lower() and 'event' not in filename.lower():
            categories['issues'].append(file_path)
        elif 'pr' in filename.lower():
            categories['prs'].append(file_path)
        elif 'commit' in filename.lower():
            categories['commits'].append(file_path)
        elif 'event' in filename.lower():
            categories['events'].append(file_path)
        else:
            categories['raw'].append(file_path)
    
    return categories

def categorize_silver_files(files: List[str]) -> Dict[str, List[str]]:
    """Categorize silver layer files by analysis type"""
    categories = {
        'member_analytics': [],
        'contribution_metrics': [],
        'collaboration_networks': [],
        'temporal_analysis': [],
        'summary_statistics': []
    }
    
    for file_path in files:
        filename = os.path.basename(file_path)
        
        if 'member' in filename.lower() or 'maturity' in filename.lower():
            categories['member_analytics'].append(file_path)
        elif 'contribution' in filename.lower() or 'repository_metrics' in filename.lower():
            categories['contribution_metrics'].append(file_path)
        elif 'collaboration' in filename.lower() or 'network' in filename.lower():
            categories['collaboration_networks'].append(file_path)
        elif 'temporal' in filename.lower() or 'cycle' in filename.lower() or 'activity' in filename.lower():
            categories['temporal_analysis'].append(file_path)
        elif 'statistics' in filename.lower() or 'distribution' in filename.lower():
            categories['summary_statistics'].append(file_path)
    
    return categories

def create_data_lineage() -> Dict[str, Any]:
    """Create data lineage mapping showing dependencies"""
    
    lineage = {
        'bronze_to_silver': {
            'members_analytics': {
                'inputs': ['data/bronze/members_detailed.json'],
                'outputs': [
                    'data/silver/members_analytics.json',
                    'data/silver/member_status_distribution.json',
                    'data/silver/maturity_bands.json'
                ]
            },
            'contribution_metrics': {
                'inputs': list(PER_REPOSITORY_BRONZE_INPUTS),
                'outputs': [
                    'data/silver/contribution_metrics.json',
                    'data/silver/repository_metrics.json',
                    'data/silver/contribution_distribution.json'
                ]
            },
            'collaboration_networks': {
                'inputs': list(PER_REPOSITORY_BRONZE_INPUTS),
                'outputs': [
                    'data/silver/collaboration_edges.json',
                    'data/silver/user_collaboration_metrics.json',
                    'data/silver/repository_collaboration_analysis.json',
                    'data/silver/cross_repository_hubs.json',
                    'data/silver/network_statistics.json'
                ]
            },
            'temporal_analysis': {
                'inputs': list(PER_REPOSITORY_BRONZE_INPUTS),
                'outputs': [
                    'data/silver/temporal_events.json',
                    'data/silver/daily_activity_summary.json',
                    'data/silver/activity_heatmap.json',
                    'data/silver/cycle_times.json',
                    'data/silver/temporal_statistics.json'
                ]
            }
        },
        'extraction_dependencies': {
            'repositories': {
                'depends_on': [],
                'required_for': ['issues', 'commits']
            },
            'members': {
                'depends_on': [],
                'required_for': []
            },
            'issues': {
                'depends_on': ['repositories'],
                'required_for': []
            },
            'commits': {
                'depends_on': ['repositories'],
                'required_for': []
            }
        }
    }
    
    return lineage

def generate_data_catalog() -> str:
    """Generate comprehensive data catalog with descriptions"""
    
    catalog = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'bronze_layer': {
            'description': 'Raw data extracted directly from GitHub API',
            'entities': {
                'repositories_raw.json': 'Complete raw repository data from GitHub API',
                'repositories_filtered.json': 'Filtered repositories excluding forks and blacklisted repos',
                'repositories_detailed.json': 'Detailed repository information with additional metadata',
                'members_basic.json': 'Basic organization member information',
                'members_detailed.json': 'Detailed member profiles with statistics',
                'issues_<repo>.json': 'Issues for each repository, one file per repository',
                'prs_<repo>.json': 'Pull requests for each repository, one file per repository',
                'commits_<repo>.json': 'Commits for each repository, one file per repository',
                'issue_events_<repo>.json': 'Issue and PR events for each repository, one file per repository'
            }
        },
        'silver_layer': {
            'description': 'Normalized and processed data ready for analytics',
            'entities': {
                'members_analytics.json': 'Member profiles with maturity scores and classifications',
                'member_status_distribution.json': 'Distribution of new vs established members',
                'maturity_bands.json': 'Categorization of members by maturity levels',
                'contribution_metrics.json': 'Comprehensive contribution statistics per user',
                'repository_metrics.json': 'Activity metrics aggregated by repository',
                'contribution_distribution.json': 'Statistical analysis of contribution patterns',
                'collaboration_edges.json': 'Network edges representing user collaborations',
                'user_collaboration_metrics.json': 'Individual user collaboration statistics',
                'repository_collaboration_analysis.json': 'Collaboration analysis per repository',
                'cross_repository_hubs.json': 'Users contributing across multiple repositories',
                'network_statistics.json': 'Overall network topology statistics',
                'temporal_events.json': 'Time-ordered sequence of all activities',
                'daily_activity_summary.json': 'Daily aggregated activity metrics',
                'activity_heatmap.json': 'Hour/day activity distribution for heatmaps',
                'cycle_times.json': 'Issue and PR resolution time analysis',
                'temporal_statistics.json': 'Overall temporal pattern statistics'
            }
        },
        'usage_patterns': {
            'dashboard_visualization': [
                'data/silver/members_analytics.json',
                'data/silver/contribution_metrics.json',
                'data/silver/daily_activity_summary.json',
                'data/silver/repository_metrics.json'
            ],
            'network_analysis': [
                'data/silver/collaboration_edges.json',
                'data/silver/user_collaboration_metrics.json',
                'data/silver/cross_repository_hubs.json'
            ],
            'temporal_analysis': [
                'data/silver/temporal_events.json',
                'data/silver/activity_heatmap.json',
                'data/silver/cycle_times.json'
            ],
            'academic_research': [
                'data/silver/contribution_distribution.json',
                'data/silver/network_statistics.json',
                'data/silver/temporal_statistics.json'
            ]
        }
    }
    
    catalog_file = save_json_data(
        catalog,
        "data/data_catalog.json",
        timestamp=False
    )
    
    return catalog_file

def main():
    """Create the master data registry and the data catalog."""
    argparse.ArgumentParser(
        description="Rebuild data/master_registry.json and data/data_catalog.json."
    ).parse_args()

    print("📋 Creating data registry and catalog...")

    master_registry_file = create_master_registry()
    print(f"✅ Created master registry: {master_registry_file}")

    catalog_file = generate_data_catalog()
    print(f"✅ Created data catalog: {catalog_file}")

    print("\n📊 Data management system initialized successfully!")


if __name__ == "__main__":
    main()