"""Sanitize the raw corpus into publishable fixtures (corpus-fixtures).

``corpus-raw`` holds unmodified API payloads — private, with personal data.
``corpus-fixtures`` is the same capture shape with that data removed, so it can
be shared and copied into other repositories (the mapper #25, the regression
fixtures #55/#57/#58). A fixture carrying a real address leaks with a long
tail, because fixtures get copied; sanitize on the fixture's own merits, not
on what today's dashboard happens to display.

Two layers of defence, because a sweep for field names never finds an address
sitting inside free text:

1. A denylist of personal keys — ``email``, ``location``, ``bio``, ``company``,
   ``blog``, ``hireable``, ``twitter_username`` — dropped wherever they appear.
2. A regex over every remaining string value that replaces email addresses
   with ``[email removed]``, catching addresses in commit messages, comment
   bodies, release notes and any other free text.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
REDACTED = "[email removed]"

PII_KEYS = frozenset(
    {
        "email",
        "location",
        "bio",
        "company",
        "blog",
        "hireable",
        "twitter_username",
    }
)


def redact_emails(value: str) -> str:
    """Replace every email address in ``value`` with ``[email removed]``."""
    return EMAIL_RE.sub(REDACTED, value)


def contains_email(value: str) -> bool:
    """Whether ``value`` contains an email address (the probe used in tests)."""
    return EMAIL_RE.search(value) is not None


def sanitize_payload(payload: Any) -> Any:
    """Return ``payload`` with personal keys dropped and emails redacted."""
    if isinstance(payload, dict):
        out: Dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and key.lower() in PII_KEYS:
                continue
            out[key] = sanitize_payload(value)
        return out
    if isinstance(payload, list):
        return [sanitize_payload(item) for item in payload]
    if isinstance(payload, str):
        return redact_emails(payload)
    return payload


def sanitize_capture_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize one capture envelope, preserving its request metadata."""
    sanitized = dict(record)
    sanitized["payload"] = sanitize_payload(record.get("payload"))
    return sanitized


def sanitize_directory(raw_dir: str, out_dir: str) -> int:
    """Sanitize every ``*.json`` capture record under ``raw_dir`` into ``out_dir``.

    The tenant-scoped layout is preserved (``<tenant>/<hash>.json``), so the
    fixtures restore into the same shape as the raw corpus. Returns the number
    of records written.
    """
    count = 0
    for dirpath, _dirnames, filenames in os.walk(raw_dir):
        rel = os.path.relpath(dirpath, raw_dir)
        for filename in sorted(filenames):
            if not filename.endswith(".json"):
                continue
            src = os.path.join(dirpath, filename)
            with open(src, encoding="utf-8") as f:
                record = json.load(f)
            if not isinstance(record, dict) or "payload" not in record:
                # Not a capture envelope; sanitize it as a bare payload.
                record = {"payload": record}
            sanitized = sanitize_capture_record(record)

            target_dir = os.path.join(out_dir, rel)
            os.makedirs(target_dir, exist_ok=True)
            dst = os.path.join(target_dir, filename)
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(sanitized, f, indent=2, ensure_ascii=False)
                f.write("\n")
            count += 1
    return count
