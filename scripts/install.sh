#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${FLUXION_REPO_URL:-https://github.com/superposed-labs/fluxion-bus.git}"
INSTALL_DIR="${FLUXION_INSTALL_DIR:-$HOME/.local/share/fluxion}"
BIN_DIR="${FLUXION_BIN_DIR:-$HOME/.local/bin}"
APP_DIR="${FLUXION_APP_DIR:-$HOME/Applications}"
WORKSPACE="${FLUXION_WORKSPACE:-$PWD}"
REF="${FLUXION_REF:-main}"
SOURCE_ARCHIVE="${FLUXION_SOURCE_ARCHIVE:-}"
WHEELS_DIR="${FLUXION_WHEELS_DIR:-}"
BUILD_WEB=1
BUILD_DESKTOP=1
PYTHON_BIN="${FLUXION_PYTHON:-}"

usage() {
  cat <<'EOF'
Install or update Fluxion for the current user.

Usage: scripts/install.sh [options]

Options:
  --repo-url URL       Git repository URL or local repository path
  --ref NAME           Branch to install/update (default: main)
  --source-archive PATH
                       Install from a local source tarball instead of git.
                       Requires a fresh (missing or empty) install directory.
  --wheels-dir PATH    Directory of dependency wheels for offline pip install;
                       falls back to PyPI if the offline install fails
  --install-dir PATH   Managed Fluxion checkout (default: ~/.local/share/fluxion)
  --bin-dir PATH       Command symlink directory (default: ~/.local/bin)
  --workspace PATH     First authorized workspace for a new install (default: cwd)
  --app-dir PATH       macOS app destination directory (default: ~/Applications)
  --no-web             Skip frontend dependency install and build
  --no-desktop         Skip macOS menu bar app build and install
  -h, --help           Show this help

Environment variables matching the options are also supported:
FLUXION_REPO_URL, FLUXION_REF, FLUXION_SOURCE_ARCHIVE, FLUXION_WHEELS_DIR,
FLUXION_INSTALL_DIR, FLUXION_BIN_DIR, FLUXION_WORKSPACE, FLUXION_APP_DIR,
and FLUXION_PYTHON.
EOF
}

die() {
  printf 'fluxion install: error: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '\n==> %s\n' "$*"
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
    --repo-url) REPO_URL="${2:?missing value for --repo-url}"; shift 2 ;;
    --ref) REF="${2:?missing value for --ref}"; shift 2 ;;
    --source-archive) SOURCE_ARCHIVE="${2:?missing value for --source-archive}"; shift 2 ;;
    --wheels-dir) WHEELS_DIR="${2:?missing value for --wheels-dir}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:?missing value for --install-dir}"; shift 2 ;;
    --bin-dir) BIN_DIR="${2:?missing value for --bin-dir}"; shift 2 ;;
    --workspace) WORKSPACE="${2:?missing value for --workspace}"; shift 2 ;;
    --app-dir) APP_DIR="${2:?missing value for --app-dir}"; shift 2 ;;
    --no-web) BUILD_WEB=0; shift ;;
    --no-desktop) BUILD_DESKTOP=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

INSTALL_DIR="$(expand_path "$INSTALL_DIR")"
BIN_DIR="$(expand_path "$BIN_DIR")"
APP_DIR="$(expand_path "$APP_DIR")"
WORKSPACE="$(expand_path "$WORKSPACE")"
[[ -n "$SOURCE_ARCHIVE" ]] && SOURCE_ARCHIVE="$(expand_path "$SOURCE_ARCHIVE")"
[[ -n "$WHEELS_DIR" ]] && WHEELS_DIR="$(expand_path "$WHEELS_DIR")"

if [[ -n "$SOURCE_ARCHIVE" ]]; then
  [[ -f "$SOURCE_ARCHIVE" ]] || die "source archive not found: $SOURCE_ARCHIVE"
else
  command -v git >/dev/null 2>&1 || die "git is required"
fi
if [[ -n "$PYTHON_BIN" ]]; then
  command -v "$PYTHON_BIN" >/dev/null 2>&1 \
    || die "configured Python executable not found: $PYTHON_BIN"
else
  for candidate in python3.13 python3.12 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
[[ -n "$PYTHON_BIN" ]] || die "Python 3.12+ is required"
"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' \
  || die "Python 3.12+ is required"
[[ -d "$WORKSPACE" ]] || die "workspace does not exist: $WORKSPACE"

note "Installing Fluxion into $INSTALL_DIR"
if [[ -n "$SOURCE_ARCHIVE" ]]; then
  # Archive installs are always a fresh extraction; there is no incremental
  # update path, so stale files from an older install can never linger.
  # bootstrap-backend.sh moves an existing directory aside (preserving .env
  # and data) before calling this script.
  if [[ -e "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
    die "install directory is not empty; remove it or run scripts/bootstrap-backend.sh instead: $INSTALL_DIR"
  fi
  mkdir -p "$INSTALL_DIR"
  tar -xzf "$SOURCE_ARCHIVE" -C "$INSTALL_DIR" --strip-components=1
elif [[ -d "$INSTALL_DIR/.git" ]]; then
  if ! git -C "$INSTALL_DIR" diff --quiet || ! git -C "$INSTALL_DIR" diff --cached --quiet; then
    die "managed checkout has local tracked changes; resolve them before updating: $INSTALL_DIR"
  fi
  git -C "$INSTALL_DIR" fetch origin "$REF"
  git -C "$INSTALL_DIR" checkout "$REF"
  git -C "$INSTALL_DIR" merge --ff-only FETCH_HEAD
elif [[ -e "$INSTALL_DIR" ]]; then
  die "install directory exists but is not a Git checkout: $INSTALL_DIR"
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --branch "$REF" --single-branch "$REPO_URL" "$INSTALL_DIR"
fi

note "Creating Python environment"
if [[ -x "$INSTALL_DIR/.venv/bin/python" ]] \
  && ! "$INSTALL_DIR/.venv/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
  note "Replacing Python environment older than 3.12"
  rm -rf "$INSTALL_DIR/.venv"
fi
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
fi
if [[ -n "$WHEELS_DIR" && -d "$WHEELS_DIR" ]]; then
  if ! "$INSTALL_DIR/.venv/bin/python" -m pip install \
    --no-index --find-links "$WHEELS_DIR" -e "$INSTALL_DIR"; then
    printf '[warn] offline install from bundled wheels failed; retrying from PyPI.\n' >&2
    "$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"
  fi
else
  "$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"
fi

if ((BUILD_WEB)); then
  if command -v npm >/dev/null 2>&1; then
    note "Building Web console"
    (
      cd "$INSTALL_DIR/web"
      npm ci
      npm run build
    )
  else
    printf '[warn] npm not found; skipping Web console build.\n' >&2
  fi
fi

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  note "Creating initial configuration"
  "$INSTALL_DIR/.venv/bin/fluxion" init \
    --env-file "$INSTALL_DIR/.env" \
    --workspace "$WORKSPACE"
else
  note "Preserving existing configuration at $INSTALL_DIR/.env"
fi

note "Linking commands into $BIN_DIR"
mkdir -p "$BIN_DIR"
for command_name in \
  fluxion fluxion-detect fluxion-gateway fluxion-mcp fluxion-scheduler \
  fluxion-sub fluxion-usage fluxion-web
do
  source_path="$INSTALL_DIR/.venv/bin/$command_name"
  [[ -x "$source_path" ]] || continue
  ln -sfn "$source_path" "$BIN_DIR/$command_name"
done

if [[ "$(uname -s)" == "Darwin" ]] && ((BUILD_DESKTOP)); then
  if command -v swiftc >/dev/null 2>&1 && command -v codesign >/dev/null 2>&1; then
    note "Building and installing macOS app"
    bash "$INSTALL_DIR/desktop/build.sh"
    mkdir -p "$APP_DIR"
    rm -rf "$APP_DIR/Fluxion.app"
    cp -R "$INSTALL_DIR/desktop/Fluxion.app" "$APP_DIR/Fluxion.app"

    app_support="$HOME/Library/Application Support/Fluxion"
    mkdir -p "$app_support"
    INSTALL_DIR="$INSTALL_DIR" "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

path = Path.home() / "Library" / "Application Support" / "Fluxion" / "config.json"
path.write_text(
    json.dumps({"repositoryPath": os.environ["INSTALL_DIR"]}, indent=2) + "\n",
    encoding="utf-8",
)
PY
  else
    printf '[warn] swiftc or codesign not found; skipping macOS app build.\n' >&2
  fi
fi

note "Installation complete"
printf 'Managed checkout: %s\n' "$INSTALL_DIR"
printf 'Configuration:    %s\n' "$INSTALL_DIR/.env"
printf 'Commands:         %s\n' "$BIN_DIR"
if [[ "$(uname -s)" == "Darwin" ]] && [[ -d "$APP_DIR/Fluxion.app" ]]; then
  printf 'macOS app:        %s\n' "$APP_DIR/Fluxion.app"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    printf '\nAdd this directory to PATH:\n  export PATH="%s:$PATH"\n' "$BIN_DIR"
    ;;
esac

printf '\nRun again at any time to update the managed installation.\n'
printf 'Verify with:\n  %s/fluxion doctor --workspace %q\n' "$BIN_DIR" "$WORKSPACE"
