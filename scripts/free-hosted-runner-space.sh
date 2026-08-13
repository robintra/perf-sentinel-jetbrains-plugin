#!/bin/bash
set -euo pipefail
# The heaviest jobs unpack several JetBrains IDEs and ran out of the 32 GB left by the previous
# cleanup, which killed the runner worker itself before any log was archived.
# /opt/hostedtoolcache and /usr/share/dotnet are deliberately kept: setup-java and setup-dotnet
# run before this script and install into them.
sudo rm -rf /usr/local/lib/android /usr/local/.ghcup /usr/share/swift
sudo rm -rf /opt/ghc /usr/share/miniconda /usr/local/share/powershell /opt/microsoft
# No job here calls setup-python, setup-node or setup-go, so every cached toolchain except the
# JDK that setup-java just installed is dead weight.
sudo find /opt/hostedtoolcache -mindepth 1 -maxdepth 1 ! -name 'Java*' -exec rm -rf {} + 2>/dev/null || true
docker image prune --all --force >/dev/null 2>&1 || true
df -h /
