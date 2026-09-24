#!/usr/bin/env python3
"""
Member analytics processing for Silver layer
Transforms raw member data into analytics-ready metrics
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Any, List, Optional
from coops.utils.github_api import save_json_data, load_json_data, parse_github_date

def _parse_capture_time(metadata: Any) -> datetime:
    """Parse the Bronze sidecar's ``extracted_at`` into a naive datetime.

    The capture instant is when the corpus was extracted; it is recorded
    by ``save_json_data`` as ``_metadata.extracted_at`` in every non-empty
    Bronze list. When it is missing or unparseable this raises
    ``ValueError`` rather than guessing, because there is no honest
    fallback: substituting ``datetime.now()`` reintroduces the wall-clock
    drift of #188 for exactly the corpora nobody tests, and a fixed
    absent value (age 0 for every member) would silently publish a
    corpus-wide lie — every member "new", every score as if the accounts
    had been created at capture. A corpus with members but no usable
    capture time is corrupt (hand-edited, written with
    ``timestamp=False``, or truncated), and corrupt input should stop the
    run instead of feeding the dashboard. An empty corpus never reaches
    this function: with no members there is nothing to age.
    """
    if not isinstance(metadata, dict):
        raise ValueError(
            "members corpus has no usable _metadata sidecar with extracted_at; "
            "account ages have no capture instant to be measured against (#188)"
        )
    raw = metadata.get('extracted_at')
    if not isinstance(raw, str) or not raw:
        raise ValueError(
            f"_metadata.extracted_at is missing or not a string: {raw!r} (#188)"
        )
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(
            f"_metadata.extracted_at is not a parseable timestamp: {raw!r} (#188)"
        ) from None
    # created_at is parsed below as a naive wall-clock reading; drop any
    # offset so the subtraction compares like with like.
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed

def _account_age_days(member_data: dict, as_of: datetime) -> int:
    """Whole days between the member's ``created_at`` and ``as_of``.

    ``as_of`` is the corpus capture time, so the value is the member's
    *age at capture*: fixed by the corpus, not by the day the Silver step
    happens to run (#188). A member with a missing or unparseable
    ``created_at`` keeps the row-level absent value 0 — one member's
    provenance is missing, which is not a reason to fail the whole corpus.
    """
    if member_data.get('created_at'):
        try:
            created_date = datetime.strptime(member_data['created_at'], '%Y-%m-%dT%H:%M:%SZ')
            return (as_of - created_date).days
        except (ValueError, TypeError):
            return 0
    return 0

def calculate_maturity_score(member_data: dict, as_of: datetime) -> float:
    """Calculate member maturity score based on various factors.

    Deterministic in the corpus: ages are measured against ``as_of`` (the
    capture time), never against the wall clock, so the same corpus scores
    the same on any day (#188).
    """

    account_age_days = _account_age_days(member_data, as_of)

    # Extract metrics
    public_repos = member_data.get('public_repos', 0)
    followers = member_data.get('followers', 0)

    # Calculate weighted score (same formula as in notebook)
    age_component = 0.5 * np.log1p(account_age_days)
    repos_component = 3 * np.log1p(public_repos)
    followers_component = 20 * np.log1p(followers)

    return age_component + repos_component + followers_component

def classify_member_status(member_data: dict, as_of: datetime) -> str:
    """Classify member as new or established, as of the capture time (#188)"""

    account_age_days = _account_age_days(member_data, as_of)

    public_repos = member_data.get('public_repos', 0)
    followers = member_data.get('followers', 0)

    # Classification logic from notebook
    if account_age_days < 365 or (public_repos < 10 and followers < 10):
        return 'new'
    else:
        return 'established'

def process_member_analytics() -> List[str]:
    """Process member data into analytics format

    Ages, and everything derived from them (``account_age_days``,
    ``maturity_score``, ``status``), are *ages at capture*: measured
    against the extraction timestamp in the Bronze sidecar
    (``_metadata.extracted_at``), never against the wall clock, so
    ``members_analytics.json`` is byte-stable for a given corpus (#188).
    A corpus that carries members but no usable ``extracted_at`` raises
    ``ValueError`` — see ``_parse_capture_time`` for why nothing is
    guessed. A corpus with no members writes the empty artifacts as
    before; it needs no capture time.
    """

    # Load bronze member data. members_analytics.json is always written (an
    # empty list when there are no members), so the dashboard can tell "no
    # members" from "not generated yet".
    members_data = load_json_data("data/bronze/members_detailed.json") or []
    if not members_data:
        print("No member data found in bronze layer")

    # Skip the metadata entry if present, keeping the capture instant it
    # records: it is the reference every account age is measured against.
    capture_time: Optional[datetime] = None
    if isinstance(members_data, list) and len(members_data) > 0 and '_metadata' in members_data[0]:
        capture_time = _parse_capture_time(members_data[0].get('_metadata'))
        members_data = members_data[1:]

    # Maturity and status need the member's profile; members whose profile
    # couldn't be fetched (profile_fetched is False) are left out.
    without_profile = [m for m in members_data if m.get('profile_fetched') is False]
    if without_profile:
        print(f"Skipping {len(without_profile)} members without a profile")
    members_data = [m for m in members_data if m.get('profile_fetched') is not False]

    processed_members = []

    if members_data:
        if capture_time is None:
            raise ValueError(
                "data/bronze/members_detailed.json carries members but no "
                "_metadata sidecar with extracted_at, so account ages have "
                "no capture instant to be measured against (#188); refusing "
                "to measure them against the wall clock"
            )
        for member in members_data:
            maturity_score = calculate_maturity_score(member, capture_time)
            status = classify_member_status(member, capture_time)

            # Create processed member record
            processed_member = {
                'login': member.get('login'),
                'id': member.get('id'),
                'name': member.get('name'),
                'public_repos': member.get('public_repos', 0),
                'followers': member.get('followers', 0),
                'following': member.get('following', 0),
                'created_at': member.get('created_at'),
                'updated_at': member.get('updated_at'),
                'maturity_score': maturity_score,
                'status': status,
                'account_age_days': _account_age_days(member, capture_time)
            }
            processed_members.append(processed_member)

    generated_files = []

    # Save processed members
    members_file = save_json_data(
        processed_members,
        "data/silver/members_analytics.json"
    )
    generated_files.append(members_file)

    # Create status distribution
    status_distribution = {}
    for member in processed_members:
        status = member['status']
        status_distribution[status] = status_distribution.get(status, 0) + 1

    distribution_file = save_json_data(
        status_distribution,
        "data/silver/member_status_distribution.json"
    )
    generated_files.append(distribution_file)

    # Create maturity bands
    # Always rewritten, so a run without members doesn't keep a stale file.
    maturity_scores = [m['maturity_score'] for m in processed_members]
    bands = {'low': 0, 'medium': 0, 'high': 0}
    if maturity_scores:
        bands = {
            'low': len([s for s in maturity_scores if s < np.percentile(maturity_scores, 33)]),
            'medium': len([s for s in maturity_scores if np.percentile(maturity_scores, 33) <= s < np.percentile(maturity_scores, 67)]),
            'high': len([s for s in maturity_scores if s >= np.percentile(maturity_scores, 67)])
        }

    bands_file = save_json_data(
        bands,
        "data/silver/maturity_bands.json"
    )
    generated_files.append(bands_file)

    print(f"Processed {len(processed_members)} members")
    return generated_files
