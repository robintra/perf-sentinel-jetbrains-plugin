#!/usr/bin/env bash

set -euo pipefail
export LC_ALL=C

REPOSITORY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG=""
DRY_RUN=0
SIGNING_PROBE=""

fail() { printf 'release: %s\n' "$*" >&2; exit 1; }
usage() { printf 'Usage: %s v0.MINOR.PATCH [--dry-run]\n' "$(basename "$0")" >&2; exit 2; }
cleanup() { [ -z "$SIGNING_PROBE" ] || rm -rf -- "$SIGNING_PROBE"; }
trap cleanup EXIT HUP INT TERM

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) [ "$DRY_RUN" -eq 0 ] || usage; DRY_RUN=1 ;;
    -*) usage ;;
    *) [ -z "$TAG" ] || usage; TAG="$1" ;;
  esac
  shift
done

[ -n "$TAG" ] || usage
[[ "$TAG" =~ ^v0\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || fail "tag must be a stable tag matching v0.MINOR.PATCH"
VERSION="${TAG#v}"
cd "$REPOSITORY"

ensure_ready() {
  [ "$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)" = main ] || fail "release must run from main"
  [ -z "$(git status --porcelain=v1 --untracked-files=all)" ] || fail "working tree must be clean"

  local remote_sha local_sha status
  remote_sha="$(git ls-remote --exit-code --heads origin refs/heads/main 2>/dev/null | awk 'NR == 1 { print $1 }')" \
    || fail "origin/main cannot be resolved"
  [[ "$remote_sha" =~ ^[0-9a-f]{40}$ ]] || fail "origin/main returned an ambiguous ref"
  local_sha="$(git rev-parse --verify refs/heads/main)"
  [ "$local_sha" = "$remote_sha" ] || fail "local main and origin/main must be synchronized exactly"

  git show-ref --verify --quiet "refs/tags/$TAG" && fail "tag $TAG already exists locally"
  set +e
  git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1
  status=$?
  set -e
  [ "$status" -eq 2 ] || { [ "$status" -eq 0 ] && fail "tag $TAG already exists on origin"; fail "origin tag state cannot be verified"; }

}

verify_signing_identity() {
  local signing_key signing_format
  signing_key="$(git config --get user.signingkey 2>/dev/null || true)"
  [ -n "$signing_key" ] || fail "a signing identity is required"
  signing_format="$(git config --get gpg.format 2>/dev/null || printf openpgp)"
  [ "$signing_format" != ssh ] || {
    local signers
    signers="$(git config --path --get gpg.ssh.allowedSignersFile 2>/dev/null || true)"
    [ -n "$signers" ] && [ -r "$signers" ] || fail "SSH signing identity requires allowed signers"
  }

  SIGNING_PROBE="$(mktemp -d "${TMPDIR:-/tmp}/perf-sentinel-plugin-signing.XXXXXX")" \
    || fail "cannot create signing probe"
  git -C "$SIGNING_PROBE" init -q -b main
  git -C "$SIGNING_PROBE" config user.name "Perf Sentinel release preflight"
  git -C "$SIGNING_PROBE" config user.email "release-preflight@example.invalid"
  git -C "$SIGNING_PROBE" config user.signingkey "$signing_key"
  git -C "$SIGNING_PROBE" config gpg.format "$signing_format"
  if [ "$signing_format" = ssh ]; then
    git -C "$SIGNING_PROBE" config gpg.ssh.allowedSignersFile "$signers"
  fi
  git -C "$SIGNING_PROBE" commit --allow-empty -q -m probe
  if ! git -C "$SIGNING_PROBE" tag -s signature-probe -m signature-probe >/dev/null 2>&1 \
    || ! git -C "$SIGNING_PROBE" verify-tag signature-probe >/dev/null 2>&1; then
    fail "configured signing identity cannot create and verify a signed tag"
  fi
  cleanup
  SIGNING_PROBE=""
}

ensure_ready
VERIFIED_SHA="$(git rev-parse --verify refs/heads/main)"
verify_signing_identity
make --no-print-directory release-check VERSION="$VERSION"
ensure_ready
[ "$(git rev-parse --verify refs/heads/main)" = "$VERIFIED_SHA" ] \
  || fail "verified commit changed while release gates were running"
short_sha="${VERIFIED_SHA:0:12}"
if [ "$DRY_RUN" -eq 1 ]; then
  printf 'release: dry-run passed; no repository or remote mutation\n'
  printf 'release: would create and push signed tag %s at %s\n' "$TAG" "$short_sha"
  exit 0
fi

printf 'Type %s to confirm the signed tag push: ' "$TAG"
IFS= read -r confirmation || fail "confirmation was not provided"
[ "$confirmation" = "$TAG" ] || fail "confirmation did not exactly match $TAG; nothing was mutated"
ensure_ready
[ "$(git rev-parse --verify refs/heads/main)" = "$VERIFIED_SHA" ] \
  || fail "verified commit changed after confirmation"
git tag -s "$TAG" -m "Perf Sentinel JetBrains plugin $TAG" || fail "signed tag creation failed"
if ! git verify-tag "$TAG" >/dev/null 2>&1; then
  git tag -d "$TAG" >/dev/null 2>&1 || true
  fail "new tag signature could not be verified"
fi
[ "$(git rev-list -n 1 "$TAG")" = "$(git rev-parse refs/heads/main)" ] || fail "tag target is not main"
git push --no-follow-tags origin "refs/tags/$TAG:refs/tags/$TAG" \
  || fail "tag push failed; inspect tag state before retrying"
printf 'release: pushed signed tag %s at %s\n' "$TAG" "$short_sha"
