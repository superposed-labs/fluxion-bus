#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${FLUXION_INSTALL_DIR:-$HOME/.local/share/fluxion}"
WORKSPACE="${FLUXION_WORKSPACE:-$HOME}"
REF="${FLUXION_REF:-main}"
REPO_URL="${FLUXION_REPO_URL:-https://github.com/superposed-labs/fluxion-bus.git}"
PYTHON_BIN="${FLUXION_PYTHON:-}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

usage() {
  cat <<'EOF'
Install or repair the Fluxion backend used by the macOS app.

When run from the app bundle, the backend is installed from the bundled
source snapshot (and dependency wheels, when present) without git or network
access. An install directory that is already a git checkout keeps using the
git update path. Without a bundled snapshot, the backend is cloned from git.

Usage: scripts/bootstrap-backend.sh [options]

Options:
  --install-dir PATH   Managed Fluxion checkout (default: ~/.local/share/fluxion)
  --workspace PATH     First authorized workspace for a new install (default: ~)
  --ref NAME           Branch or tag to install/update (default: main)
  --repo-url URL       Git repository URL (default: Fluxion GitHub repository)
  -h, --help           Show this help
EOF
}

die() {
  printf 'fluxion backend bootstrap: error: %s\n' "$*" >&2
  exit 1
}

expand_path() {
  case "$1" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${1#~/}" ;;
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$PWD" "$1" ;;
  esac
}

while (($#)); do
  case "$1" in
    --install-dir) INSTALL_DIR="${2:?missing value for --install-dir}"; shift 2 ;;
    --workspace) WORKSPACE="${2:?missing value for --workspace}"; shift 2 ;;
    --ref) REF="${2:?missing value for --ref}"; shift 2 ;;
    --repo-url) REPO_URL="${2:?missing value for --repo-url}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

INSTALL_DIR="$(expand_path "$INSTALL_DIR")"
WORKSPACE="$(expand_path "$WORKSPACE")"

SCRIPT_DIR="$(CDPATH="" cd -- "$(dirname -- "$0")" && pwd)"
RESOURCES_DIR="$(CDPATH="" cd -- "$SCRIPT_DIR/.." && pwd)"
BUNDLED_ARCHIVE="$RESOURCES_DIR/Backend/backend.tar.gz"
BUNDLED_WHEELS="$RESOURCES_DIR/Backend/wheels"

# Prefer the bundled source snapshot: no git, no network. A managed dir that
# is already a git checkout (developer setup) keeps the git update path so
# repairs do not silently replace it with the app's bundled version.
USE_ARCHIVE=0
if [[ -f "$BUNDLED_ARCHIVE" && ! -d "$INSTALL_DIR/.git" ]]; then
  USE_ARCHIVE=1
fi

find_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || return 1
    "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'
    return $?
  fi

  for candidate in python3.13 python3.12 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
      PYTHON_BIN="$candidate"
      return 0
    fi
  done
  return 1
}

ensure_python() {
  if find_python; then
    return 0
  fi

  if command -v brew >/dev/null 2>&1; then
    printf 'Python 3.12+ not found; installing python@3.13 with Homebrew...\n'
    brew install python@3.13
    PYTHON_BIN="$(brew --prefix python@3.13)/bin/python3.13"
    find_python || die "Homebrew installed python@3.13, but it is not runnable"
    return 0
  fi

  die "Python 3.12+ is required and Homebrew was not found. Install Homebrew or Python 3.12+, then retry."
}

ensure_git() {
  if command -v git >/dev/null 2>&1; then
    return 0
  fi

  if command -v brew >/dev/null 2>&1; then
    printf 'git not found; installing git with Homebrew...\n'
    brew install git
    command -v git >/dev/null 2>&1 || die "Homebrew installed git, but it is not on PATH"
    return 0
  fi

  die "git is required and Homebrew was not found. Install Homebrew or git, then retry."
}

ensure_python
((USE_ARCHIVE)) || ensure_git

PREINSTALL_BACKUP=""
prepare_install_dir() {
  if [[ -e "$INSTALL_DIR" && ! -d "$INSTALL_DIR/.git" ]]; then
    local stamp backup
    stamp="$(date +%Y%m%d-%H%M%S)"
    backup="$(dirname "$INSTALL_DIR")/fluxion-preinstall-backup-$stamp"
    printf 'Existing non-git backend directory found; moving it to %s\n' "$backup"
    mv "$INSTALL_DIR" "$backup"
    PREINSTALL_BACKUP="$backup"
  fi
}

restore_preserved_state() {
  [[ -n "$PREINSTALL_BACKUP" && -d "$PREINSTALL_BACKUP" ]] || return 0

  if [[ -f "$PREINSTALL_BACKUP/.env" ]]; then
    printf 'Restoring preserved .env from preinstall backup...\n'
    cp "$PREINSTALL_BACKUP/.env" "$INSTALL_DIR/.env"
  fi

  if [[ -d "$PREINSTALL_BACKUP/data" ]]; then
    printf 'Restoring preserved data from preinstall backup...\n'
    mkdir -p "$INSTALL_DIR/data"
    cp -R "$PREINSTALL_BACKUP/data/." "$INSTALL_DIR/data/"
  fi
}

prepare_install_dir

if [[ -f "$SCRIPT_DIR/install.sh" ]]; then
  INSTALLER="$SCRIPT_DIR/install.sh"
elif ((USE_ARCHIVE)); then
  die "bundled source snapshot found but install.sh is missing next to it: $SCRIPT_DIR"
else
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  INSTALLER="$tmp/install.sh"
  curl -fsSL "https://raw.githubusercontent.com/superposed-labs/fluxion-bus/$REF/scripts/install.sh" -o "$INSTALLER"
fi

if ((USE_ARCHIVE)); then
  # The Web console assets are copied from the bundle below, so the archive
  # install never needs npm either.
  installer_args=(
    --source-archive "$BUNDLED_ARCHIVE"
    --install-dir "$INSTALL_DIR"
    --workspace "$WORKSPACE"
    --no-web
    --no-desktop
  )
  if [[ -d "$BUNDLED_WHEELS" ]]; then
    installer_args+=(--wheels-dir "$BUNDLED_WHEELS")
  fi
else
  installer_args=(
    --repo-url "$REPO_URL"
    --ref "$REF"
    --install-dir "$INSTALL_DIR"
    --workspace "$WORKSPACE"
    --no-desktop
  )
fi

FLUXION_PYTHON="$PYTHON_BIN" bash "$INSTALLER" "${installer_args[@]}"

restore_preserved_state

if ((USE_ARCHIVE)) && [[ -f "$RESOURCES_DIR/Backend/REVISION" ]]; then
  cp "$RESOURCES_DIR/Backend/REVISION" "$INSTALL_DIR/.fluxion-revision"
fi

BUNDLED_STATIC="$RESOURCES_DIR/WebStatic"
if [[ -d "$BUNDLED_STATIC" ]]; then
  printf 'Installing bundled Web console static assets...\n'
  rm -rf "$INSTALL_DIR/src/fluxion/web/static"
  mkdir -p "$INSTALL_DIR/src/fluxion/web/static"
  cp -R "$BUNDLED_STATIC/." "$INSTALL_DIR/src/fluxion/web/static/"
fi
