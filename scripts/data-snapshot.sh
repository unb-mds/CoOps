#!/usr/bin/env bash
#
# CoOps — Collaboration & Ops Metrics Dashboard
# Copyright (C) 2026 CoOps Contributors
# Licensed under the GNU General Public License v3.0 (or later). See LICENSE.
#
# data-snapshot.sh — keep the GitHub API corpus local and reusable across git
# worktrees, as compressed, checksummed snapshots — in TWO artifacts, never one
# (issue #109):
#
#   corpus-raw       The full, unmodified API payloads in the capture shape
#                    {tenant_id, provider, endpoint, params, etag, fetched_at,
#                    payload}. PRIVATE. It contains personal data (email
#                    addresses, full /users profiles with location/bio/company)
#                    and must never be published, committed, uploaded, attached
#                    to an issue/PR, or stored in a CI cache. Kept mode 700/600,
#                    tenant-scoped, with a stated retention policy (see below).
#
#   corpus-fixtures  The same shape with the personal data removed. Safe to
#                    share — this is the one you may publish, copy into other
#                    repositories, or attach to a regression-fixture PR. The
#                    mapper (#25) and the regression fixtures (#55/#57/#58)
#                    consume this, not corpus-raw.
#
# ---------------------------------------------------------------------------
# Restore paths
# ---------------------------------------------------------------------------
# Pack and restore each artifact with:
#
#   scripts/data-snapshot.sh pack-raw                 # corpus-raw/  -> private tarball
#   scripts/data-snapshot.sh unpack-raw               # restore the newest corpus-raw
#   scripts/data-snapshot.sh pack-fixtures            # corpus-raw/ -> sanitize -> corpus-fixtures tarball
#   scripts/data-snapshot.sh unpack-fixtures          # restore the newest corpus-fixtures
#
# corpus-fixtures is SAFE TO SHARE. corpus-raw is NOT — do not publish, commit,
# upload, attach to an issue/PR, or put it in an actions/cache (see #126).
#
# The legacy combined snapshot (cache/ + data/) is unchanged:
#
#   scripts/data-snapshot.sh pack                     # cache/ + data/ -> coops-corpus-*.tar.gz (private)
#   scripts/data-snapshot.sh unpack                   # restore the newest combined snapshot
#
# ---------------------------------------------------------------------------
# Snapshot location
# ---------------------------------------------------------------------------
# Snapshots live in a fixed directory OUTSIDE every git worktree, so all
# worktrees on the machine share one copy:
#
#   $COOPS_SNAPSHOT_DIR            if set, else
#   $XDG_DATA_HOME/coops/snapshots if XDG_DATA_HOME is set, else
#   ~/.local/share/coops/snapshots
#
# Nothing under the repository needs to be git-ignored for this location
# because it is not inside the repository. `cache/`, `corpus-raw/` and
# `corpus-fixtures/` are git-ignored in the repository's .gitignore.
#
# ---------------------------------------------------------------------------
# Privacy and retention
# ---------------------------------------------------------------------------
# corpus-raw contains personal data, so a corpus-raw snapshot MUST NOT be
# published or shared. The snapshot directory is mode 700 and the raw archive
# and its checksum are mode 600. corpus-fixtures is sanitized and may be shared,
# so its archive and checksum are mode 644.
#
# Retention: `pack-raw --retention-days N` prunes corpus-raw records whose
# `fetched_at` is older than N days before packing, and
# `coops-corpus prune --raw corpus-raw --max-age-days N` prunes the live
# corpus directly. There is no default here — set the policy you want; 30 days
# is the suggested floor for a corpus that is rebuilt on every run.
#
# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
#   pack [--source <dir>]     Create a timestamped .tar.gz of cache/ + data/
#                             (default source: the current directory), with a
#                             SHA-256 checksum recorded alongside it.
#   unpack [--into <dir>]     Restore the NEWEST combined snapshot into a
#                             target directory (default: the current worktree).
#                             Refuses to overwrite a non-empty cache/ or data/
#                             unless --force is given, and refuses a corrupt
#                             archive.
#   pack-raw [--source <dir>] [--retention-days N]
#                             Pack corpus-raw/ (the PRIVATE capture) into a
#                             corpus-raw-*.tar.gz, pruning records older than
#                             N days first when --retention-days is given.
#   unpack-raw [--into <dir>] Restore the NEWEST corpus-raw snapshot.
#   pack-fixtures [--source <dir>]
#                             Sanitize corpus-raw/ into corpus-fixtures/ (via
#                             `coops-corpus sanitize`) and pack the result.
#   unpack-fixtures [--into <dir>]
#                             Restore the NEWEST corpus-fixtures snapshot.
#   list                      Show available snapshots with size and age.
#   verify <snapshot>         Check integrity (checksum + archive structure)
#                             without extracting.
#
# Environment:
#   COOPS_SNAPSHOT_DIR   Override the snapshot directory (see above).
#
# Exit codes: 0 ok · 1 operational error (missing/corrupt corpus, etc.) ·
#             2 usage error.
#
# Requires GNU coreutils (sha256sum, tar, gzip, stat, find, du), bash ≥ 4, and
# a synced project (`uv sync`) for pack-fixtures/pack-raw --retention-days,
# which call the `coops-corpus` command.

set -euo pipefail

# The raw corpus contains personal data (see the Privacy section above), so
# keep everything this script creates private by default: new directories are
# 0700 and new files are 0600. This is set BEFORE anything is created — there
# is never a window in which a raw archive is world-readable. Fixtures are
# explicitly chmod'd 644 after creation because their content is sanitized.
umask 077

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- Configuration -----------------------------------------------------------

readonly SNAPSHOT_DIR="${COOPS_SNAPSHOT_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/coops/snapshots}"
readonly PREFIX="coops-corpus"
readonly CORPUS_DIRS=(cache data)
readonly RAW_PREFIX="corpus-raw"
readonly FIXTURES_PREFIX="corpus-fixtures"
readonly RAW_DIR="corpus-raw"
readonly FIXTURES_DIR="corpus-fixtures"

# --- Output helpers ----------------------------------------------------------

info() { printf '%s\n' "$*"; }

warn() { printf 'warning: %s\n' "$*" >&2; }

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

usage() {
  awk 'NR >= 2 { if ($0 ~ /^#/) { sub(/^# ?/, ""); print } else { exit } }' "$0"
  exit 2
}

# --- Corpus helpers ----------------------------------------------------------

# Prints 0 (success) when the path exists and unpack would need --force to
# replace it: i.e. it is a non-directory (file/symlink), or a non-empty
# directory. Returns non-zero when the path is absent or an empty directory.
needs_force() {
  local path="$1"
  [[ -e "$path" || -L "$path" ]] || return 1
  [[ -d "$path" ]] || return 0
  [[ -n "$(ls -A "$path" 2>/dev/null)" ]]
}

# --- Snapshot listing --------------------------------------------------------

# Prints full paths to <pattern>*.tar.gz files in a directory, newest (by
# mtime) first. `pattern` is the filename prefix, e.g. "corpus-raw-" or "".
sorted_snapshots() {
  local dir="$1" pattern="$2" f
  local -a all=()
  shopt -s nullglob
  for f in "$dir"/$pattern*.tar.gz; do
    all+=("$f")
  done
  shopt -u nullglob
  (( ${#all[@]} > 0 )) || return 0
  for f in "${all[@]}"; do
    printf '%s\t%s\n' "$(stat -c %Y "$f")" "$f"
  done | sort -rn | cut -f2-
}

newest_snapshot() {
  local pattern="$1" label="$2"
  local -a snaps=()
  local s
  while IFS= read -r s; do
    [[ -n "$s" ]] && snaps+=("$s")
  done < <(sorted_snapshots "$SNAPSHOT_DIR" "$pattern")
  if (( ${#snaps[@]} == 0 )); then
    die "no ${label:-'${pattern}'} snapshots found in $SNAPSHOT_DIR — run 'pack' first"
  fi
  printf '%s\n' "${snaps[0]}"
}

# Resolve a snapshot argument that may be a bare name or a path.
resolve_snapshot() {
  local arg="$1"
  case "$arg" in
    */*) printf '%s\n' "$arg" ;;
    *)   printf '%s\n' "$SNAPSHOT_DIR/$arg" ;;
  esac
}

# Human-readable age from an epoch-seconds timestamp.
human_age() {
  local then="$1" now secs d h m s
  now="$(date +%s)"
  secs=$(( now - then ))
  (( secs < 0 )) && secs=0
  d=$(( secs / 86400 )); secs=$(( secs % 86400 ))
  h=$(( secs / 3600 ));  secs=$(( secs % 3600 ))
  m=$(( secs / 60 ));    s=$(( secs % 60 ))
  if (( d > 0 )); then
    printf '%dd %dh' "$d" "$h"
  elif (( h > 0 )); then
    printf '%dh %dm' "$h" "$m"
  elif (( m > 0 )); then
    printf '%dm %ds' "$m" "$s"
  else
    printf '%ds' "$s"
  fi
}

# --- Snapshot creation -------------------------------------------------------

# Create (or tighten) the shared snapshot directory. It holds raw personal data,
# so it is always mode 700.
ensure_snapshot_dir() {
  mkdir -p "$SNAPSHOT_DIR" || die "cannot create snapshot directory: $SNAPSHOT_DIR"

  # A directory created just above is already 0700 thanks to `umask 077`, but
  # one left over from an earlier run may be looser. Tighten it — and say so on
  # stderr — before any archive is written into it.
  local mode
  mode="$(stat -c %a "$SNAPSHOT_DIR")"
  if (( 8#$mode & 8#077 )); then
    chmod 700 "$SNAPSHOT_DIR" || die "cannot tighten permissions on $SNAPSHOT_DIR"
    warn "tightened $SNAPSHOT_DIR from mode $mode to 700 (it holds personal data)"
  fi
}

# Pack the given directory names from `source` into a timestamped, checksummed
# tarball named `<prefix>-<stamp>.tar.gz`. `file_mode` is the mode for the
# archive and its checksum: 600 for private artifacts, 644 for shareable ones.
create_snapshot() {
  local source="$1" prefix="$2" file_mode="$3"
  shift 3
  local -a dirs=("$@")

  local d
  for d in "${dirs[@]}"; do
    [[ -d "$source/$d" ]] || die "source has no $d/ directory: $source"
  done

  ensure_snapshot_dir

  local stamp name final tmp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  name="${prefix}-${stamp}.tar.gz"
  final="$SNAPSHOT_DIR/$name"
  tmp="$SNAPSHOT_DIR/.tmp.${name}.$$"

  rm -f "$tmp"
  if ! tar -czf "$tmp" -C "$source" "${dirs[@]}"; then
    rm -f "$tmp"
    die "failed to create archive from $source"
  fi

  # Fail closed: refuse to publish an archive that is not a readable tar.gz.
  if ! tar -tzf "$tmp" >/dev/null 2>&1; then
    rm -f "$tmp"
    die "created archive failed validation: $tmp"
  fi

  chmod "$file_mode" "$tmp"
  mv "$tmp" "$final" || { rm -f "$tmp"; die "failed to move archive into place"; }

  # Record a SHA-256 checksum next to the archive, keyed by basename so the
  # pair can be moved together and still verify.
  if ! ( cd "$SNAPSHOT_DIR" && sha256sum "$name" > "$name.sha256" ); then
    rm -f "$final"
    die "failed to write checksum for $final"
  fi
  chmod "$file_mode" "$final.sha256"

  info "packed: $final"
  info "checksum: $final.sha256"
}

# Restore one named artifact directory from the newest <prefix>-* snapshot.
unpack_artifact() {
  local prefix="$1" dirname="$2" label="$3"
  shift 3
  local into="." force=0
  while (( $# > 0 )); do
    case "$1" in
      --into)
        shift
        (( $# > 0 )) || die "--into requires a directory"
        into="$1"
        ;;
      --force)
        force=1
        ;;
      *)
        die "unknown argument for unpack-${label}: $1"
        ;;
    esac
    shift
  done

  local snap
  snap="$(newest_snapshot "${prefix}-" "${prefix}")"

  # Verify integrity first; refuse a corrupt archive outright.
  verify_archive "$snap" || die "refusing to unpack corrupt archive: $snap"

  mkdir -p "$into" || die "cannot create target directory: $into"

  if needs_force "$into/$dirname" && (( force == 0 )); then
    die "$into/$dirname already exists and is non-empty — pass --force to replace it"
  fi

  local staging
  staging="$(mktemp -d "$into/.coops-unpack.XXXXXX")" \
    || die "cannot create staging directory in $into"

  if ! tar -xzf "$snap" -C "$staging"; then
    rm -rf "$staging"
    die "failed to extract $snap"
  fi

  if [[ ! -d "$staging/$dirname" ]]; then
    rm -rf "$staging"
    die "archive $snap has no top-level $dirname/ directory"
  fi

  rm -rf "$into/$dirname"
  mv "$staging/$dirname" "$into/$dirname" \
    || { rm -rf "$staging"; die "failed to move $dirname into $into"; }

  rm -rf "$staging"
  info "unpacked: $snap -> $into"
}

# --- Subcommands -------------------------------------------------------------

cmd_pack() {
  local source="."
  while (( $# > 0 )); do
    case "$1" in
      --source)
        shift
        (( $# > 0 )) || die "--source requires a directory"
        source="$1"
        ;;
      *)
        die "unknown argument for pack: $1"
        ;;
    esac
    shift
  done

  source="$(cd "$source" && pwd)" || die "cannot access source directory: $source"
  create_snapshot "$source" "$PREFIX" 600 "${CORPUS_DIRS[@]}"
}

cmd_unpack() {
  local into="." force=0
  while (( $# > 0 )); do
    case "$1" in
      --into)
        shift
        (( $# > 0 )) || die "--into requires a directory"
        into="$1"
        ;;
      --force)
        force=1
        ;;
      *)
        die "unknown argument for unpack: $1"
        ;;
    esac
    shift
  done

  local snap
  snap="$(newest_snapshot "${PREFIX}-" "${PREFIX}")"

  # Verify integrity first; refuse a corrupt archive outright.
  verify_archive "$snap" || die "refusing to unpack corrupt archive: $snap"

  mkdir -p "$into" || die "cannot create target directory: $into"

  # Refuse before touching anything when cache/ or data/ would be overwritten.
  local d
  for d in "${CORPUS_DIRS[@]}"; do
    if needs_force "$into/$d" && (( force == 0 )); then
      die "$into/$d already exists and is non-empty — pass --force to replace it"
    fi
  done

  # Extract into a staging directory first, then move each corpus directory into
  # place, so a failure can never leave a half-extracted tree behind.
  local staging
  staging="$(mktemp -d "$into/.coops-unpack.XXXXXX")" \
    || die "cannot create staging directory in $into"

  if ! tar -xzf "$snap" -C "$staging"; then
    rm -rf "$staging"
    die "failed to extract $snap"
  fi

  for d in "${CORPUS_DIRS[@]}"; do
    if [[ ! -d "$staging/$d" ]]; then
      rm -rf "$staging"
      die "archive $snap has no top-level $d/ directory"
    fi
  done

  for d in "${CORPUS_DIRS[@]}"; do
    rm -rf "$into/$d"
    mv "$staging/$d" "$into/$d" \
      || { rm -rf "$staging"; die "failed to move $d into $into"; }
  done

  rm -rf "$staging"
  info "unpacked: $snap -> $into"
}

cmd_pack_raw() {
  local source="." retention=0
  while (( $# > 0 )); do
    case "$1" in
      --source)
        shift
        (( $# > 0 )) || die "--source requires a directory"
        source="$1"
        ;;
      --retention-days)
        shift
        (( $# > 0 )) || die "--retention-days requires a number of days"
        retention="$1"
        ;;
      *)
        die "unknown argument for pack-raw: $1"
        ;;
    esac
    shift
  done

  source="$(cd "$source" && pwd)" || die "cannot access source directory: $source"

  if (( retention > 0 )); then
    info "pruning corpus-raw records older than $retention day(s)..."
    uv run --project "$REPO_ROOT" coops-corpus prune \
      --raw "$source/$RAW_DIR" --max-age-days "$retention" \
      || die "retention prune failed"
  fi

  create_snapshot "$source" "$RAW_PREFIX" 600 "$RAW_DIR"
}

cmd_unpack_raw() {
  unpack_artifact "$RAW_PREFIX" "$RAW_DIR" "raw" "$@"
}

cmd_pack_fixtures() {
  local source="."
  while (( $# > 0 )); do
    case "$1" in
      --source)
        shift
        (( $# > 0 )) || die "--source requires a directory"
        source="$1"
        ;;
      *)
        die "unknown argument for pack-fixtures: $1"
        ;;
    esac
    shift
  done

  source="$(cd "$source" && pwd)" || die "cannot access source directory: $source"

  [[ -d "$source/$RAW_DIR" ]] \
    || die "source has no $RAW_DIR/ directory: $source (run coops-bronze --capture-dir first)"

  info "sanitizing $RAW_DIR/ into $FIXTURES_DIR/ ..."
  uv run --project "$REPO_ROOT" coops-corpus sanitize \
    --raw "$source/$RAW_DIR" --out "$source/$FIXTURES_DIR" \
    || die "sanitize failed"

  create_snapshot "$source" "$FIXTURES_PREFIX" 644 "$FIXTURES_DIR"
}

cmd_unpack_fixtures() {
  unpack_artifact "$FIXTURES_PREFIX" "$FIXTURES_DIR" "fixtures" "$@"
}

cmd_list() {
  if [[ ! -d "$SNAPSHOT_DIR" ]]; then
    info "no snapshots found (directory does not exist: $SNAPSHOT_DIR)"
    return 0
  fi

  local found=0 s
  while IFS= read -r s; do
    [[ -n "$s" ]] || continue
    found=1
    printf '%-42s %10s %10s\n' \
      "$(basename "$s")" \
      "$(du -h "$s" | cut -f1)" \
      "$(human_age "$(stat -c %Y "$s")")"
  done < <(sorted_snapshots "$SNAPSHOT_DIR" "")

  if (( found == 0 )); then
    info "no snapshots found in $SNAPSHOT_DIR"
  fi
}

cmd_verify() {
  (( $# >= 1 )) || die "verify requires a snapshot name or path"
  local snap
  snap="$(resolve_snapshot "$1")"
  verify_archive "$snap" || exit 1
  info "ok: $snap"
}

# Checks a snapshot's recorded checksum and archive structure. Returns non-zero
# on any failure (with a message); extracts nothing.
verify_archive() {
  local snap="$1" checksum dir base
  [[ -f "$snap" ]] || { printf 'error: snapshot not found: %s\n' "$snap" >&2; return 1; }

  checksum="${snap}.sha256"
  [[ -f "$checksum" ]] || { printf 'error: checksum file missing: %s\n' "$checksum" >&2; return 1; }

  dir="$(dirname "$snap")"
  base="$(basename "$snap")"
  if ! ( cd "$dir" && sha256sum -c "$base.sha256" ); then
    printf 'error: checksum mismatch — snapshot is corrupt: %s\n' "$snap" >&2
    return 1
  fi

  if ! tar -tzf "$snap" >/dev/null 2>&1; then
    printf 'error: not a valid tar.gz archive: %s\n' "$snap" >&2
    return 1
  fi
}

# --- Dispatch ----------------------------------------------------------------

cmd="${1:-}"
shift || true

case "$cmd" in
  pack)            cmd_pack "$@" ;;
  unpack)          cmd_unpack "$@" ;;
  pack-raw)        cmd_pack_raw "$@" ;;
  unpack-raw)      cmd_unpack_raw "$@" ;;
  pack-fixtures)   cmd_pack_fixtures "$@" ;;
  unpack-fixtures) cmd_unpack_fixtures "$@" ;;
  list)            cmd_list "$@" ;;
  verify)          cmd_verify "$@" ;;
  -h|--help|help)  usage ;;
  "")              usage ;;
  *)               die "unknown subcommand: $cmd" ;;
esac
