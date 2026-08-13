#!/bin/bash
set -euo pipefail
# The heaviest jobs unpack several JetBrains IDEs and ran out of the 32 GB left by the previous
# cleanup, which killed the runner worker itself before any log was archived.
# /opt/hostedtoolcache and /usr/share/dotnet are deliberately kept: setup-java and setup-dotnet
# run before this script and install into them.
sudo rm -rf /usr/local/lib/android /usr/local/.ghcup /usr/share/swift
sudo rm -rf /opt/ghc /usr/share/miniconda /usr/local/share/powershell /opt/microsoft
docker image prune --all --force >/dev/null 2>&1 || true
df -h /
