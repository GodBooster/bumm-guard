#!/usr/bin/env bash
# Build the wasm files of the third-party upgrade pairs from the official near-sdk-rs example
# `examples/versioned` at the commit pinned in tests/fixtures/near_official/UPSTREAM_COMMIT.
#
#   scripts/build_official_example.sh             build versioned, versioned_v0, versioned_plain
#
# Fetches that commit, fails unless tests/fixtures/near_official/versioned/ and the licence files
# equal it byte for byte, copies our two old variants next to the official crate in its examples/,
# and builds each with `cargo +<toolchain> build --release --locked --target wasm32-unknown-unknown`
# and the lock in tests/fixtures/near_official/locks/. The wasm files land in
# tests/fixtures/near_official/target/wasm32-unknown-unknown/release/. Needs git, network, rustup
# with the toolchain (default 1.85.0, env BUMM_OFFICIAL_TOOLCHAIN) and the wasm32 target.
# BUMM_OFFICIAL_NEW_LOCK=1 builds without a lock for a crate that has none and saves the generated one
# as locks/<crate>.Cargo.lock (used once, to create the locks).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
D="$ROOT/tests/fixtures/near_official"
COMMIT="$(tr -d '[:space:]' < "$D/UPSTREAM_COMMIT")"
TC="${BUMM_OFFICIAL_TOOLCHAIN:-1.85.0}"
WORK="$D/target/upstream"
OUT="$D/target/wasm32-unknown-unknown/release"

rm -rf "$WORK"
mkdir -p "$WORK" "$OUT"
git -C "$WORK" init -q
git -C "$WORK" fetch -q --depth 1 https://github.com/near/near-sdk-rs "$COMMIT"
git -C "$WORK" -c advice.detachedHead=false checkout -q FETCH_HEAD
test "$(git -C "$WORK" rev-parse HEAD)" = "$COMMIT"
echo "near-sdk-rs at $COMMIT"

diff -r "$D/versioned" "$WORK/examples/versioned"
diff "$D/LICENSE-MIT" "$WORK/LICENSE-MIT"
diff "$D/LICENSE-APACHE" "$WORK/LICENSE-APACHE"
echo "tests/fixtures/near_official/versioned/ and the licences equal the upstream files"

for n in versioned_v0 versioned_plain; do
  cp -R "$D/$n" "$WORK/examples/$n"
done

for n in versioned versioned_v0 versioned_plain; do
  lock="$D/locks/$n.Cargo.lock"
  flags=(--release --target wasm32-unknown-unknown)
  if [ -f "$lock" ]; then
    cp "$lock" "$WORK/examples/$n/Cargo.lock"
    flags+=(--locked)
  elif [ "${BUMM_OFFICIAL_NEW_LOCK:-0}" != 1 ]; then
    echo "missing $lock" >&2; exit 1
  fi
  echo "== cargo +$TC build ${flags[*]}  (examples/$n)"
  (cd "$WORK/examples/$n" && CARGO_RESOLVER_INCOMPATIBLE_RUST_VERSIONS=fallback cargo "+$TC" build "${flags[@]}")
  [ -f "$lock" ] || cp "$WORK/examples/$n/Cargo.lock" "$lock"
  cp "$WORK/examples/$n/target/wasm32-unknown-unknown/release/$n.wasm" "$OUT/$n.wasm"
done
(cd "$OUT" && { command -v sha256sum >/dev/null && sha256sum versioned.wasm versioned_v0.wasm versioned_plain.wasm || shasum -a 256 versioned.wasm versioned_v0.wasm versioned_plain.wasm; })
