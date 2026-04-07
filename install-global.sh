#!/usr/bin/env bash
set -euo pipefail

# Build claw and symlink it into a user-global bin directory.
#
# Usage:
#   ./install-global.sh
#   ./install-global.sh --release
#   ./install-global.sh --link-dir "$HOME/bin"
#
# Env:
#   CLAW_LINK_DIR   Override link directory (default: $HOME/.local/bin)

BUILD_PROFILE="debug"
LINK_DIR="${CLAW_LINK_DIR:-$HOME/.local/bin}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --release)
            BUILD_PROFILE="release"
            ;;
        --debug)
            BUILD_PROFILE="debug"
            ;;
        --link-dir)
            shift
            LINK_DIR="${1:-}"
            if [ -z "${LINK_DIR}" ]; then
                echo "error: --link-dir requires a value" >&2
                exit 2
            fi
            ;;
        -h|--help)
            cat <<'EOF'
Usage: ./install-global.sh [options]

Options:
  --release            Build release profile
  --debug              Build debug profile (default)
  --link-dir <dir>     Directory to place the claw symlink (default: $HOME/.local/bin)
  -h, --help           Show help
EOF
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            exit 2
            ;;
    esac
    shift
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUST_DIR="${SCRIPT_DIR}/rust"

if [ ! -f "${RUST_DIR}/Cargo.toml" ]; then
    echo "error: rust workspace not found at ${RUST_DIR}" >&2
    exit 1
fi

echo "==> Building claw (${BUILD_PROFILE})"
if [ "${BUILD_PROFILE}" = "release" ]; then
    (cd "${RUST_DIR}" && cargo build --workspace --release)
else
    (cd "${RUST_DIR}" && cargo build --workspace)
fi

CLAW_BIN="${RUST_DIR}/target/${BUILD_PROFILE}/claw"
if [ ! -x "${CLAW_BIN}" ]; then
    echo "error: built binary not found: ${CLAW_BIN}" >&2
    exit 1
fi

mkdir -p "${LINK_DIR}"
ln -sf "${CLAW_BIN}" "${LINK_DIR}/claw"

echo "==> Linked"
echo "  ${LINK_DIR}/claw -> ${CLAW_BIN}"

if ! command -v claw >/dev/null 2>&1; then
    cat <<EOF
warning: \`claw\` is not on PATH yet.
Add this line to your shell profile:
  export PATH="${LINK_DIR}:\$PATH"
EOF
fi

echo "==> Done"
echo "Try: claw --version"
