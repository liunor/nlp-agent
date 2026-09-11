#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 COMMAND [ARGUMENT ...]" >&2
  exit 64
fi

max_attempts="${TRANSIENT_REGISTRY_MAX_ATTEMPTS:-5}"
retry_delay_seconds="${TRANSIENT_REGISTRY_RETRY_DELAY_SECONDS:-10}"
attempt=1
status=1

is_transient_registry_error() {
  [[ "$1" =~ (EOF|unexpected[[:space:]]+EOF|timeout|timed[[:space:]]+out|deadline[[:space:]]+exceeded|connection[[:space:]]+(reset|refused)|network[[:space:]]+is[[:space:]]+unreachable|TLS[[:space:]]+handshake[[:space:]]+timeout|(^|[^0-9])(429|502|503|504)([^0-9]|$)|temporarily[[:space:]]+unavailable) ]]
}

while [ "$attempt" -le "$max_attempts" ]; do
  output=""
  if output="$("$@" 2>&1)"; then
    if [ -n "$output" ]; then
      printf '%s\n' "$output"
    fi
    exit 0
  else
    status=$?
  fi

  printf '%s\n' "$output" >&2
  if ! is_transient_registry_error "$output"; then
    exit "$status"
  fi
  if [ "$attempt" -eq "$max_attempts" ]; then
    exit "$status"
  fi

  delay_seconds=$((attempt * retry_delay_seconds))
  echo "::warning::Transient registry error; retrying in ${delay_seconds}s (attempt ${attempt}/${max_attempts})." >&2
  sleep "$delay_seconds"
  attempt=$((attempt + 1))
done

exit "$status"
