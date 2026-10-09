#!/usr/bin/env bash
# Install MagView's system packages (idempotent; run as root).
#
#   scripts/setup_system_deps.sh          install whatever is missing (no-op if complete)
#   scripts/setup_system_deps.sh --check  report missing packages; exit 1 if any
#   scripts/setup_system_deps.sh --list   print the package list (for a Dockerfile)
#
# The package list lives in scripts/system-packages.txt next to this script.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIST="$HERE/system-packages.txt"
mode="${1:-install}"

[[ -f "$LIST" ]] || { echo "error: package list not found: $LIST" >&2; exit 2; }

# Strip comments and blank lines.
mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^[[:space:]]*$/d' "$LIST" | awk '{print $1}')

missing_packages() {
    local pkg status
    for pkg in "${packages[@]}"; do
        status="$(dpkg-query -W -f='${db:Status-Status}' "$pkg" 2>/dev/null || true)"
        [[ "$status" == "installed" ]] || echo "$pkg"
    done
}

case "$mode" in
    --list)
        printf '%s\n' "${packages[@]}"
        ;;
    --check)
        mapfile -t missing < <(missing_packages)
        if (( ${#missing[@]} )); then
            echo "missing ${#missing[@]}/${#packages[@]} system packages: ${missing[*]}"
            echo "fix: sudo $HERE/setup_system_deps.sh"
            exit 1
        fi
        echo "all ${#packages[@]} system packages installed"
        ;;
    install)
        if [[ $EUID -ne 0 ]]; then
            echo "error: must run as root (try: sudo $0)" >&2
            exit 1
        fi
        mapfile -t missing < <(missing_packages)
        if (( ${#missing[@]} == 0 )); then
            echo "all ${#packages[@]} system packages already installed; nothing to do"
            exit 0
        fi
        echo "installing ${#missing[@]} missing package(s): ${missing[*]}"
        apt-get update -qq
        apt-get install -y -qq --no-install-recommends "${missing[@]}"
        mapfile -t still < <(missing_packages)
        if (( ${#still[@]} )); then
            echo "error: still missing after install: ${still[*]}" >&2
            exit 1
        fi
        echo "done: all ${#packages[@]} system packages installed"
        ;;
    *)
        echo "usage: $0 [--check|--list]" >&2
        exit 2
        ;;
esac
