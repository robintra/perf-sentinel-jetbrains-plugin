#!/usr/bin/env bash
# Run a Gradle invocation, retrying only when it failed for a reason outside
# this repository.
#
# The IntelliJ Platform Gradle plugin resolves its own dependencies and the IDE
# distributions from remote repositories on every run, and those fetches time
# out often enough to redden a green branch. One observed instance:
#
#   Could not resolve org.jetbrains.intellij.plugins:structure-ide:3.330
#     > Repository Gradle Central Plugin Repository is disabled due to
#       earlier error below
#   > There are 10 more failures with identical causes.
#
# The retry is deliberately NOT blanket. A failing test must fail on the first
# attempt and stay failed: retrying a test suite until it passes is how a flaky
# test becomes invisible. Only the network and resolution signatures below are
# retried, and anything else exits immediately with Gradle's own status.

set -uo pipefail

MAX_ATTEMPTS="${GRADLE_RETRY_ATTEMPTS:-3}"
LOG="$(mktemp)"
trap 'rm -f "${LOG}"' EXIT

# Markers of a fetch that never reached us. Deliberately narrow: none of these
# can be produced by a test assertion or a Plugin Verifier finding.
RETRYABLE='Could not resolve|Could not GET|Could not download|Read timed out|SocketTimeoutException|Connection reset|Connection timed out|is disabled due to earlier error|Received status code 5|502 Bad Gateway|503 Service Unavailable|Premature end of Content-Length'

for attempt in $(seq 1 "${MAX_ATTEMPTS}"); do
  gradle --no-daemon --dependency-verification strict "$@" 2>&1 | tee "${LOG}"
  status="${PIPESTATUS[0]}"

  [ "${status}" -eq 0 ] && exit 0

  if ! grep -qE "${RETRYABLE}" "${LOG}"; then
    echo "gradle-retry: failed for a reason this script does not retry, leaving it failed" >&2
    exit "${status}"
  fi

  if [ "${attempt}" -eq "${MAX_ATTEMPTS}" ]; then
    echo "gradle-retry: ${MAX_ATTEMPTS} attempts all hit a fetch failure, giving up" >&2
    exit "${status}"
  fi

  delay=$((attempt * 15))
  echo "gradle-retry: attempt ${attempt}/${MAX_ATTEMPTS} hit a fetch failure, retrying in ${delay}s..." >&2
  sleep "${delay}"
done
