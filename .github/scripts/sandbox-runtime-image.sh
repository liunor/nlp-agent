#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_OUTPUT:?}"
: "${RUNTIME_IMAGE_NAME:?}"
[[ "$RUNTIME_IMAGE_NAME" =~ ^ghcr\.io/[a-z0-9-]+/nova-sandbox-runtime$ ]]

inspect_digest() {
  docker buildx imagetools inspect "$1" --format '{{.Manifest.Digest}}'
}

valid_digest() {
  [[ "$1" =~ ^sha256:[0-9a-f]{64}$ ]]
}

case "$1" in
  lookup)
    # Production candidates from main still use their existing per-commit build.
    if [[ "${SOURCE_BRANCH:?}" != develop ]]; then exit 0; fi
    tree=$(git rev-parse --verify 'HEAD:sandbox-runtime')
    [[ "$tree" =~ ^[0-9a-f]{40}$ ]]
    # The Dockerfile installs an apt package. Refresh it once a month even if
    # Git inputs are unchanged. Bump v1 if workflow build inputs change.
    epoch=$(date -u +%Y%m)
    cache_ref="$RUNTIME_IMAGE_NAME:test-input-v1-${epoch}-${tree}"
    printf 'cache_ref=%s\n' "$cache_ref" >> "$GITHUB_OUTPUT"
    if result=$(inspect_digest "$cache_ref" 2>&1); then
      valid_digest "$result"
      printf 'digest=%s\n' "$result" >> "$GITHUB_OUTPUT"
      echo 'Reusing reviewed test runtime build inputs.'
    elif [[ "$result" == *"$cache_ref"* ]] && [[ "$result" =~ (not\ found|MANIFEST_UNKNOWN|manifest\ unknown) ]]; then
      echo 'No published test runtime for these build inputs; building it once.'
    else
      echo '::error::Unable to check the test runtime cache tag; no deployment digest selected.'
      exit 1
    fi
    ;;
  publish)
    digest="${BUILT_DIGEST:-}"
    if [[ -n "${REUSED_DIGEST:-}" ]]; then
      digest="$REUSED_DIGEST"
      valid_digest "$digest"
      found=0
      while IFS= read -r ref; do
        [[ -n "$ref" ]] || continue
        [[ "$ref" == "$RUNTIME_IMAGE_NAME:"* && "${ref##*:}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]
        docker buildx imagetools create --prefer-index=false --tag "$ref" "$RUNTIME_IMAGE_NAME@$digest"
        [[ "$(inspect_digest "$ref")" == "$digest" ]]
        found=1
      done <<< "${RUNTIME_TAGS:-}"
      [[ "$found" == 1 ]]
    fi
    valid_digest "$digest"
    printf 'digest=%s\n' "$digest" >> "$GITHUB_OUTPUT"
    ;;
  *) exit 2 ;;
esac
