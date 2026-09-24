"""Unit tests for coops.raw_capture.sanitize — the corpus-fixtures artifact."""

import json
import os

from coops.raw_capture.sanitize import (
    EMAIL_RE,
    contains_email,
    redact_emails,
    sanitize_capture_record,
    sanitize_directory,
    sanitize_payload,
)


def test_redact_emails_in_free_text():
    text = "Contact jane.doe@example.com or call +1 555 0100. Reply to bob+tag@sub.example.co.uk."
    assert redact_emails(text) == (
        "Contact [email removed] or call +1 555 0100. "
        "Reply to [email removed]."
    )


def test_sanitize_payload_drops_personal_keys():
    payload = {
        "login": "octocat",
        "id": 1,
        "email": "octocat@example.com",
        "location": "Brasília",
        "bio": "lives in Brasília",
        "company": "Acme",
        "blog": "https://example.com",
        "hireable": True,
        "twitter_username": "octocat",
        "repos_url": "https://api.github.com/users/octocat/repos",
    }
    sanitized = sanitize_payload(payload)
    assert sanitized == {
        "login": "octocat",
        "id": 1,
        "repos_url": "https://api.github.com/users/octocat/repos",
    }


def test_sanitize_payload_redacts_email_in_nested_free_text():
    payload = {
        "commits": [
            {
                "message": "Merge pull request from jane@example.com\n\nSigned-off-by: Joe <joe@example.org>",
                "author": {"name": "Joe", "email": "joe@example.org"},
            }
        ]
    }
    sanitized = sanitize_payload(payload)
    assert sanitized["commits"][0]["message"] == (
        "Merge pull request from [email removed]\n\nSigned-off-by: Joe <[email removed]>"
    )
    # The `email` key is dropped by the denylist.
    assert "email" not in sanitized["commits"][0]["author"]


def test_sanitize_payload_is_case_insensitive_on_keys():
    payload = {"Email": "x@example.com", "EMAIL": "y@example.com"}
    assert sanitize_payload(payload) == {}


def test_sanitize_preserves_non_personal_values():
    payload = {
        "id": 42,
        "public_repos": 7,
        "null_value": None,
        "nested": {"count": 3},
        "list": [1, "text", None],
    }
    assert sanitize_payload(payload) == payload


def test_sanitize_capture_record_preserves_request_metadata():
    record = {
        "tenant_id": "unb-mds",
        "provider": "github",
        "endpoint": "/users/octocat",
        "params": {},
        "etag": '"abc"',
        "fetched_at": "2026-09-22T12:00:00+00:00",
        "payload": {"login": "octocat", "email": "octocat@example.com"},
    }
    sanitized = sanitize_capture_record(record)
    assert sanitized["tenant_id"] == "unb-mds"
    assert sanitized["endpoint"] == "/users/octocat"
    assert sanitized["etag"] == '"abc"'
    assert sanitized["payload"] == {"login": "octocat"}


def test_control_probe_finds_email_then_sanitize_removes_it():
    # CONTROL: the probe must first be shown capable of finding the address in
    # the raw serialised output. An absent-result check that has never found the
    # thing proves nothing.
    raw = json.dumps({"body": "ping me at control@example.com please"})
    assert contains_email(raw) is True
    assert EMAIL_RE.findall(raw) == ["control@example.com"]

    sanitized = json.dumps(sanitize_payload(json.loads(raw)))
    assert contains_email(sanitized) is False
    assert EMAIL_RE.findall(sanitized) == []


def test_sanitize_directory_mirrors_tenant_layout(tmp_path):
    raw_dir = str(tmp_path / "corpus-raw")
    out_dir = str(tmp_path / "corpus-fixtures")
    tenant_dir = os.path.join(raw_dir, "unb-mds")
    os.makedirs(tenant_dir)
    record = {
        "tenant_id": "unb-mds",
        "provider": "github",
        "endpoint": "/users/octocat",
        "params": {},
        "etag": None,
        "fetched_at": "2026-09-22T12:00:00+00:00",
        "payload": {"login": "octocat", "email": "octocat@example.com"},
    }
    with open(os.path.join(tenant_dir, "abc.json"), "w", encoding="utf-8") as f:
        json.dump(record, f)

    count = sanitize_directory(raw_dir, out_dir)

    assert count == 1
    out_file = os.path.join(out_dir, "unb-mds", "abc.json")
    assert os.path.exists(out_file)
    with open(out_file, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["payload"] == {"login": "octocat"}


def test_sanitize_directory_bare_payload_without_envelope(tmp_path):
    # A legacy cache entry has no envelope; sanitize treats it as a bare payload.
    raw_dir = str(tmp_path / "corpus-raw")
    out_dir = str(tmp_path / "corpus-fixtures")
    os.makedirs(raw_dir)
    with open(os.path.join(raw_dir, "legacy.json"), "w", encoding="utf-8") as f:
        json.dump({"body": "email legacy@example.com"}, f)

    count = sanitize_directory(raw_dir, out_dir)

    assert count == 1
    with open(os.path.join(out_dir, "legacy.json"), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["payload"]["body"] == "email [email removed]"


def test_cli_sanitize(tmp_path, capsys):
    from coops.raw_capture.cli import main

    raw_dir = str(tmp_path / "corpus-raw")
    out_dir = str(tmp_path / "corpus-fixtures")
    os.makedirs(os.path.join(raw_dir, "unb-mds"))
    record = {
        "tenant_id": "unb-mds",
        "endpoint": "/users/octocat",
        "params": {},
        "etag": None,
        "fetched_at": "2026-09-22T12:00:00+00:00",
        "payload": {"email": "octocat@example.com", "login": "octocat"},
    }
    with open(os.path.join(raw_dir, "unb-mds", "abc.json"), "w", encoding="utf-8") as f:
        json.dump(record, f)

    rc = main(["sanitize", "--raw", raw_dir, "--out", out_dir])

    assert rc == 0
    assert "sanitized 1 record(s)" in capsys.readouterr().out
    with open(os.path.join(out_dir, "unb-mds", "abc.json"), encoding="utf-8") as f:
        assert json.load(f)["payload"] == {"login": "octocat"}


def test_cli_prune(tmp_path, capsys):
    from coops.raw_capture.capture import RawCaptureWriter
    from coops.raw_capture.cli import main

    raw_dir = str(tmp_path / "corpus-raw")
    writer = RawCaptureWriter(raw_dir, "unb-mds")
    writer.write("/repos/old", {}, None, {}, fetched_at="2020-01-01T00:00:00+00:00")

    rc = main(["prune", "--raw", raw_dir, "--max-age-days", "30"])

    assert rc == 0
    assert "pruned 1 stale record(s)" in capsys.readouterr().out
    assert writer.record_files() == []
