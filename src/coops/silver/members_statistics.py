#!/usr/bin/env python3

from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Mapping, Optional

from coops.utils.github_api import save_json_data, parse_github_date
from coops.silver.bronze_input import load_family
from coops.silver.unattributed import (
    UNATTRIBUTED_FIELD,
    commit_author_identity,
    conversation_actor_identity,
)


def is_email_hash(identifier: str) -> bool:
    """True when ``identifier`` is an ``author_email_hash``.

    Those hashes are the 64-char SHA-256 hex digests Bronze writes for
    commit authors with no GitHub account link.
    """
    return len(identifier) == 64 and all(c in "0123456789abcdef" for c in identifier)


def choose_display_spelling(name_counts: Mapping[str, int]) -> Optional[str]:
    """Pick the one spelling of an identity's name that will be displayed.

    Deterministic rule, measured against the corpus (issue #151):

    1. prefer a spelling containing a space — ``First Last`` reads as a
       person where ``FirstLast`` reads as a handle;
    2. among the survivors, the highest occurrence count;
    3. break remaining ties lexicographically.

    Step 3 is load-bearing, not decoration: identities in the corpus reach
    it tied, and without it their label flips between regenerations. When
    no spelling contains a space, step 1 keeps every candidate and
    frequency decides alone. This is a display heuristic only — it never
    touches the identity key.
    """
    if not name_counts:
        return None
    return min(
        name_counts,
        key=lambda spelling: (
            ' ' not in spelling,       # 1. a spelling with a space wins
            -name_counts[spelling],    # 2. then the most frequent one
            spelling,                  # 3. then lexicographic order
        ),
    )


def display_name(identifier: str,
                 name_counts: Optional[Mapping[str, int]] = None) -> str:
    """Render the human-readable label for an identity key.

    The label chain inverts the tail of the identity chain, because the two
    want opposite things — ``id`` must be unique and stable, ``name`` must
    be legible:

    * identity (``id``, unchanged): login -> author_email_hash -> name
    * label (``name``, this function): login -> real name ->
      ``Unknown contributor (<first 8 hex>)``

    A login or a plain-name identity passes through unchanged. A hash
    identity renders the real name observed for it — chosen by
    :func:`choose_display_spelling` among every spelling seen across all of
    its events — and falls back to ``Unknown contributor (a1b2c3d4)`` when
    no name was ever observed. The label may repeat across identities (two
    people can share a name); the dashboard keeps them apart through ``id``.
    """
    if not is_email_hash(identifier):
        return identifier
    spelling = choose_display_spelling(name_counts or {})
    if spelling is not None:
        return spelling
    return f"Unknown contributor ({identifier[:8]})"


def observe_spelling(name_counts: Dict[str, int], name: Optional[str]) -> None:
    """Accumulate one observed spelling of an identity's real name.

    Call this at every event write site; the label is decided once, at
    aggregation, from all spellings seen — deciding per event would let
    whichever commit was read last choose the label (issue #151, step 3).
    Empty and whitespace-only names are ignored: whitespace would otherwise
    win the contains-a-space preference without being a name.
    """
    if name and name.strip():
        name_counts[name] += 1


def process_members_statistics() -> List[str]:
    """
    Gera estatísticas individuais por membro, incluindo avg_weekly_activity.
    """

    # Carregar dados bronze: per-repository files, not the _all aggregates
    # (redundant concatenations of exactly these records — issue #170).
    issues_data = load_family("issues")
    prs_data = load_family("prs")
    commits_data = load_family("commits")
    issue_events_data = load_family("issue_events")

    generated_files = []
    
    # Estrutura para agregar eventos por membro. `name_counts` acumula as
    # grafias do nome real observadas por identidade (issue #151, etapa 3);
    # o rótulo é decidido apenas na agregação, depois de ver todos os eventos.
    def _new_entry() -> Dict[str, Any]:
        return {
            'name_counts': defaultdict(int),
            'events': [],
            'total_commits': 0,
            'total_issues_created': 0,
            'total_issues_closed': 0,
            'total_prs_created': 0,
            'total_prs_closed': 0,
            'total_comments': 0,
            'repos': set(),
            'first_activity': None,
            'last_activity': None
        }

    members_data = defaultdict(_new_entry)

    # The unattributed bucket (#154, #155): records no identity channel
    # attributes to anyone — a commit with login, name, email hash and
    # name all null, an event whose `actor` is JSON null (a deleted
    # account). It accumulates exactly like a member entry but is never
    # rendered as one: no `id`, no `name`, no averages, one marked row
    # appended after the sort. It is nobody — not a member named
    # 'unknown' (which is how `temporal_analysis` once credited 1,081
    # events to a contributor who does not exist) and not a silent drop
    # (which is what the old `'unknown'` exclusion did to 3.7% of one
    # corpus's commits).
    unattributed_bucket = _new_entry()
    
    # Processar commits
    for commit in commits_data:
        commit_date = None
        commit_obj = commit.get('commit') or {}
        author_obj = commit_obj.get('author') or {}
        if author_obj.get('date'):
            commit_date = parse_github_date(author_obj['date'])
        
        if commit_date:
            # One resolver for both Silver modules (#154): the chain
            # login (inner) > login (top level) > author_email_hash >
            # name, ending in None — no channel identifies the author,
            # so the record is carried in the unattributed bucket,
            # never a member named 'unknown' and never dropped.
            user_identifier = commit_author_identity(commit)
            
            # A bot is attributed (to the bot) and simply not a member;
            # only `None` is unattributed.
            if user_identifier is None or 'bot]' not in user_identifier:
                entry = (unattributed_bucket if user_identifier is None
                         else members_data[user_identifier])
                if user_identifier is not None:
                    entry['id'] = user_identifier
                    observe_spelling(entry['name_counts'], author_obj.get('name'))
                entry['events'].append({
                    'date': commit_date,
                    'type': 'commit',
                    'repo': commit.get('repo_name', 'unknown')
                })
                entry['total_commits'] += 1
                entry['repos'].add(commit.get('repo_name', 'unknown'))
                
                # Atualizar first/last activity
                if entry['first_activity'] is None or commit_date < entry['first_activity']:
                    entry['first_activity'] = commit_date
                if entry['last_activity'] is None or commit_date > entry['last_activity']:
                    entry['last_activity'] = commit_date
    
    # Processar issues
    for issue in issues_data:
        # Shared resolver (#155): `user: null` (a deleted account) and a
        # user object with neither login nor name both resolve to None —
        # an unattributed record, carried in the bucket rather than
        # dropped as 'unknown'.
        user_obj = issue.get('user')
        user_identifier = conversation_actor_identity(user_obj)
        
        if user_identifier is None or 'bot]' not in user_identifier:
            # Issue criada
            created_at = parse_github_date(issue.get('created_at'))
            if created_at:
                entry = (unattributed_bucket if user_identifier is None
                         else members_data[user_identifier])
                if user_identifier is not None:
                    entry['id'] = user_identifier
                    observe_spelling(entry['name_counts'], user_obj.get('name'))
                entry['events'].append({
                    'date': created_at,
                    'type': 'issue_created',
                    'repo': issue.get('repo_name', 'unknown')
                })
                entry['total_issues_created'] += 1
                entry['repos'].add(issue.get('repo_name', 'unknown'))
                
                if entry['first_activity'] is None or created_at < entry['first_activity']:
                    entry['first_activity'] = created_at
                if entry['last_activity'] is None or created_at > entry['last_activity']:
                    entry['last_activity'] = created_at
            
            # Issue fechada
            if issue.get('state') == 'closed':
                closed_at = parse_github_date(issue.get('closed_at', issue.get('updated_at')))
                if closed_at:
                    entry = (unattributed_bucket if user_identifier is None
                             else members_data[user_identifier])
                    entry['events'].append({
                        'date': closed_at,
                        'type': 'issue_closed',
                        'repo': issue.get('repo_name', 'unknown')
                    })
                    entry['total_issues_closed'] += 1
                    
                    if entry['first_activity'] is None or closed_at < entry['first_activity']:
                        entry['first_activity'] = closed_at
                    if entry['last_activity'] is None or closed_at > entry['last_activity']:
                        entry['last_activity'] = closed_at
    
    # Processar PRs
    for pr in prs_data:
        user_obj = pr.get('user')
        user_identifier = conversation_actor_identity(user_obj)
        
        if user_identifier is None or 'bot]' not in user_identifier:
            # PR criada
            created_at = parse_github_date(pr.get('created_at'))
            if created_at:
                entry = (unattributed_bucket if user_identifier is None
                         else members_data[user_identifier])
                if user_identifier is not None:
                    entry['id'] = user_identifier
                    observe_spelling(entry['name_counts'], user_obj.get('name'))
                entry['events'].append({
                    'date': created_at,
                    'type': 'pr_created',
                    'repo': pr.get('repo_name', 'unknown')
                })
                entry['total_prs_created'] += 1
                entry['repos'].add(pr.get('repo_name', 'unknown'))
                
                if entry['first_activity'] is None or created_at < entry['first_activity']:
                    entry['first_activity'] = created_at
                if entry['last_activity'] is None or created_at > entry['last_activity']:
                    entry['last_activity'] = created_at
            
            # PR fechada
            if pr.get('state') == 'closed':
                closed_at = parse_github_date(pr.get('closed_at', pr.get('updated_at')))
                if closed_at:
                    entry = (unattributed_bucket if user_identifier is None
                             else members_data[user_identifier])
                    entry['events'].append({
                        'date': closed_at,
                        'type': 'pr_closed',
                        'repo': pr.get('repo_name', 'unknown')
                    })
                    entry['total_prs_closed'] += 1
                    
                    if entry['first_activity'] is None or closed_at < entry['first_activity']:
                        entry['first_activity'] = closed_at
                    if entry['last_activity'] is None or closed_at > entry['last_activity']:
                        entry['last_activity'] = closed_at
    
    # Processar eventos de issues (comments, etc)
    for event in issue_events_data:
        # `"actor": null` — GitHub's answer for a deleted account (#155)
        # — and an actor object with no legible channel both resolve to
        # None through the shared resolver: an unattributed record.
        actor_obj = event.get('actor')
        user_identifier = conversation_actor_identity(actor_obj)
        
        if user_identifier is None or 'bot]' not in user_identifier:
            event_date = parse_github_date(event.get('created_at'))
            if event_date:
                entry = (unattributed_bucket if user_identifier is None
                         else members_data[user_identifier])
                if user_identifier is not None:
                    entry['id'] = user_identifier
                    observe_spelling(entry['name_counts'], actor_obj.get('name'))
                
                event_type = event.get('event', 'unknown')
                if 'comment' in event_type.lower():
                    entry['total_comments'] += 1
                
                entry['events'].append({
                    'date': event_date,
                    'type': f"event_{event_type}",
                    'repo': event.get('repo_name', 'unknown')
                })
                entry['repos'].add(event.get('repo_name', 'unknown'))
                
                if entry['first_activity'] is None or event_date < entry['first_activity']:
                    entry['first_activity'] = event_date
                if entry['last_activity'] is None or event_date > entry['last_activity']:
                    entry['last_activity'] = event_date
    
    # Calcular estatísticas finais para cada membro
    members_statistics = []
    
    for username, data in members_data.items():
        if data['first_activity'] and data['last_activity']:
            # Calcular período de atividade
            activity_period_days = (data['last_activity'] - data['first_activity']).days
            activity_period_weeks = activity_period_days / 7.0
            
            # Evitar divisão por zero
            if activity_period_weeks < 0.1:
                activity_period_weeks = 0.1
            
            # Calcular total de eventos
            total_events = len(data['events'])
            
            # Calcular avg_weekly_activity
            avg_weekly_activity = total_events / activity_period_weeks
            
            # Calcular avg_daily_activity
            avg_daily_activity = total_events / max(1, activity_period_days)
            
            # Calcular médias específicas por tipo
            avg_commits = data['total_commits'] / activity_period_weeks
            avg_prs = (data['total_prs_created'] + data['total_prs_closed']) / activity_period_weeks
            avg_issues = (data['total_issues_created'] + data['total_issues_closed']) / activity_period_weeks
            
            member_stats = {
                'id': username,
                # The label is decided here, at aggregation, from every
                # spelling observed for this identity — never per event.
                'name': display_name(username, data['name_counts']),
                'total_events': total_events,
                'total_commits': data['total_commits'],
                'total_issues_created': data['total_issues_created'],
                'total_issues_closed': data['total_issues_closed'],
                'total_prs_created': data['total_prs_created'],
                'total_prs_closed': data['total_prs_closed'],
                'total_comments': data['total_comments'],
                'repos': sorted(data['repos']),
                'repos_count': len(data['repos']),
                'activity_period': {
                    'first_activity': data['first_activity'].isoformat(),
                    'last_activity': data['last_activity'].isoformat(),
                    'days': activity_period_days,
                    'weeks': round(activity_period_weeks, 2)
                },
                'avg_weekly_activity': round(avg_weekly_activity, 2),
                'avg_daily_activity': round(avg_daily_activity, 2),
                'avg_commits_per_week': round(avg_commits, 2),
                'avg_prs_per_week': round(avg_prs, 2),
                'avg_issues_per_week': round(avg_issues, 2)
            }
            
            members_statistics.append(member_stats)
    
    # Ordenar por avg_weekly_activity (decrescente)
    members_statistics.sort(key=lambda x: x['avg_weekly_activity'], reverse=True)
    
    # The unattributed bucket becomes one marked row, appended AFTER the
    # sort so it never ranks among members. `id` and `name` are null —
    # the bucket is nobody, not a person named 'unknown' — and there are
    # no averages, because "nobody's commits per week" is not a
    # statistic. Consumers exclude it from member lists and member
    # counts by testing the marker; the module's own counts below
    # already do. No unattributed records, no row: a corpus without them
    # keeps byte-identical output.
    if unattributed_bucket['events']:
        members_statistics.append({
            'id': None,
            'name': None,
            UNATTRIBUTED_FIELD: True,
            'total_events': len(unattributed_bucket['events']),
            'total_commits': unattributed_bucket['total_commits'],
            'total_issues_created': unattributed_bucket['total_issues_created'],
            'total_issues_closed': unattributed_bucket['total_issues_closed'],
            'total_prs_created': unattributed_bucket['total_prs_created'],
            'total_prs_closed': unattributed_bucket['total_prs_closed'],
            'total_comments': unattributed_bucket['total_comments'],
            'repos': sorted(unattributed_bucket['repos']),
            'repos_count': len(unattributed_bucket['repos']),
        })
    
    # Salvar arquivo
    stats_file = save_json_data(
        members_statistics,
        "data/silver/members_statistics.json"
    )
    generated_files.append(stats_file)
    
    print(f"Processed member statistics: {len(members_data)} members, "
          f"{len(unattributed_bucket['events'])} unattributed records")
    
    return generated_files


if __name__ == "__main__":
    process_members_statistics()
