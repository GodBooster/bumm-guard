#!/usr/bin/env bash
# Run cargo in a Linux container with the repo mounted. Needed on macOS x86_64, where
# near-sandbox has no native binary, so near-workspaces tests only run on Linux.
#
#   scripts/linux-test.sh <workspace-dir> [cargo args...]
#   scripts/linux-test.sh contracts test --workspace
#   scripts/linux-test.sh tests/fixtures/near check --workspace --locked
#
# Caches cargo registry and target/ in named volumes so repeated runs are fast.
set -euo pipefail

IMAGE="${BUMM_RUST_IMAGE:-rust:1.99-slim-bookworm}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WS="${1:?workspace dir relative to repo root, e.g. contracts}"; shift
ARGS=("$@"); [ ${#ARGS[@]} -eq 0 ] && ARGS=(test --workspace)

# Docker Desktop on macOS keeps its CLI and credential helper out of PATH until linked.
if ! command -v docker >/dev/null 2>&1 && [ -d /Applications/Docker.app/Contents/Resources/bin ]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
fi

exec docker run --rm \
  -v "$REPO_ROOT":/work \
  -v bumm-guard-cargo-registry:/usr/local/cargo/registry \
  -v bumm-guard-rustup:/usr/local/rustup \
  -v bumm-guard-target:/work/"$WS"/target \
  -w /work/"$WS" \
  -e CARGO_TERM_COLOR=always \
  "$IMAGE" \
  sh -c 'apt-get update -qq >/dev/null && apt-get install -qq -y --no-install-recommends pkg-config libssl-dev ca-certificates curl >/dev/null \
    && rustup target add wasm32-unknown-unknown >/dev/null \
    && exec cargo "$@"' sh "${ARGS[@]}"
