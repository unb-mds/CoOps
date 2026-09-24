#!/usr/bin/env bash
#
# CoOps — Collaboration & Ops Metrics Dashboard
# Copyright (C) 2026 CoOps Contributors
# Licensed under the GNU General Public License v3.0 (or later). See LICENSE.
#
# load_mongo_snapshot.sh — import CoOps pipeline output into the local
# development MongoDB (docker-compose.dev.yml — see docs/development.md).
#
# By default this imports the SANITIZED derived output in `data/`: the JSON
# the pipeline produces with personal fields stripped. One MongoDB collection
# per file, named `<layer>_<basename>` (data/bronze/members_basic.json ->
# `bronze_members_basic`, data/silver/ai/members_ai.json -> `silver_ai_members_ai`).
#
# The RAW corpus in `cache/` is a different thing: unmodified GitHub API
# response bodies that still contain personal data (email addresses, full
# /users profiles with location, bio, company). It is PRIVATE and is only ever
# imported behind the explicit `--raw` flag (into a single `raw` collection).
# Do not republish, commit, upload or share it. The two are not interchangeable.
#
# Usage:
#   load_mongo_snapshot.sh [--raw] [DIR]
#
#   DIR is the directory to import (default: <repo>/data, or <repo>/cache with
#   --raw). Brings the stack up if needed. Targets the default no-auth setup;
#   with root auth enabled (see .env.example) pass --username/--password/
#   --authenticationDatabase admin to mongosh/mongoimport yourself.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO_ROOT/docker-compose.dev.yml")
SERVICE=mongo
DB="${MONGO_DB:-coops}"

mode=sanitized
dir=""

while (( $# > 0 )); do
    case "$1" in
        --raw) mode=raw ;;
        --db) shift; DB="$1" ;;
        -h|--help) awk 'NR >= 2 { if ($0 ~ /^#/) { sub(/^# ?/, ""); print } else { exit } }' "$0"; exit 0 ;;
        *) dir="$1" ;;
    esac
    shift
done

if [ "$mode" = raw ]; then
    src="${dir:-$REPO_ROOT/cache}"
    label="raw corpus (cache/)"
else
    src="${dir:-$REPO_ROOT/data}"
    label="sanitized derived output (data/)"
fi

if [ ! -d "$src" ]; then
    echo "error: $label not found at $src" >&2
    echo "Generate it with 'uv run coops-bronze' (then silver/gold), or restore" >&2
    echo "a snapshot with 'scripts/data-snapshot.sh unpack'." >&2
    exit 1
fi
src="$(cd "$src" && pwd)"

# Bring the stack up and wait until MongoDB answers.
"${COMPOSE[@]}" up -d

echo "Waiting for MongoDB to be ready..."
ready=""
for _ in {1..60}; do
    if "${COMPOSE[@]}" exec -T "$SERVICE" mongosh --quiet \
        --eval 'quit(db.adminCommand("ping").ok === 1 ? 0 : 1)' >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [ -z "$ready" ]; then
    echo "error: MongoDB did not become ready within 60s" >&2
    exit 1
fi

if [ "$mode" = raw ]; then
    # PRIVATE raw corpus: every cache/*.json becomes a document in `raw`.
    echo "Importing RAW corpus from $src — PRIVATE data, never republish or share."
    count=0
    for file in "$src"/*.json; do
        [ -e "$file" ] || continue
        args=(--db "$DB" --collection raw)
        [ "$count" -eq 0 ] && args+=(--drop)
        first="$(head -c 1 "$file" | tr -d '[:space:]')"
        [ "$first" = "[" ] && args+=(--jsonArray)
        "${COMPOSE[@]}" exec -T "$SERVICE" mongoimport "${args[@]}" < "$file"
        count=$((count + 1))
    done
    if [ "$count" -eq 0 ]; then
        echo "error: no .json files under $src" >&2
        exit 1
    fi
    echo "Imported $count raw response file(s) into '$DB.raw'."
else
    # Sanitized data/: one collection per file, named <layer>_<basename>.
    count=0
    while IFS= read -r -d '' file; do
        rel="${file#"$src"/}"
        collection="$(printf '%s' "$rel" | sed 's#\.json$##; s#/#_#g')"
        [ -n "$collection" ] || continue
        echo "Importing $collection"
        args=(--db "$DB" --collection "$collection" --drop)
        first="$(head -c 1 "$file" | tr -d '[:space:]')"
        [ "$first" = "[" ] && args+=(--jsonArray)
        "${COMPOSE[@]}" exec -T "$SERVICE" mongoimport "${args[@]}" < "$file"
        count=$((count + 1))
    done < <(find "$src" -type f -name '*.json' -print0 | sort -z)
    if [ "$count" -eq 0 ]; then
        echo "error: no .json files under $src" >&2
        exit 1
    fi
    echo "Imported $count sanitized collection(s) into '$DB'."
fi

echo "Collections in '$DB':"
"${COMPOSE[@]}" exec -T "$SERVICE" mongosh --quiet \
    --eval "db.getSiblingDB('$DB').getCollectionNames().sort().forEach(c => print(c))"
