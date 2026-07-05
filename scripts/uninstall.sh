#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${FLUXION_INSTALL_DIR:-$HOME/.local/share/fluxion}"
BIN_DIR="${FLUXION_BIN_DIR:-$HOME/.local/bin}"
APP_DIR="${FLUXION_APP_DIR:-$HOME/Applications}"
PURGE=0

usage() {
  cat <<'EOF'
Uninstall a user-level Fluxion installation.

Usage: scripts/uninstall.sh [options]

Options:
  --install-dir PATH   Managed Fluxion checkout (default: ~/.local/share/fluxion)
  --bin-dir PATH       Command symlink directory (default: ~/.local/bin)
  --app-dir PATH       Preferred macOS app directory to remove first (default: ~/Applications)
  --purge              Delete .env, data, and desktop config instead of backing them up
  -h, --help           Show this help

Without --purge, user configuration and data are moved to a timestamped backup
next to the managed checkout.
EOF
}

die() {
  printf 'fluxion uninstall: error: %s\n' "$*" >&2
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
    --install-dir) INSTALL_DIR="${2:?missing value for --install-dir}"; shift 2 ;;
    --bin-dir) BIN_DIR="${2:?missing value for --bin-dir}"; shift 2 ;;
    --app-dir) APP_DIR="${2:?missing value for --app-dir}"; shift 2 ;;
    --purge) PURGE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

INSTALL_DIR="$(expand_path "$INSTALL_DIR")"
BIN_DIR="$(expand_path "$BIN_DIR")"
APP_DIR="$(expand_path "$APP_DIR")"

[[ "$INSTALL_DIR" != "/" && "$INSTALL_DIR" != "$HOME" ]] \
  || die "refusing unsafe install directory: $INSTALL_DIR"

note "Stopping Fluxion processes"
for process_name in fluxion-scheduler fluxion-web fluxion-gateway; do
  pattern="$INSTALL_DIR/.venv/bin/$process_name"
  pkill -f "$pattern" >/dev/null 2>&1 || true
done

note "Removing command links"
for command_name in \
  fluxion fluxion-detect fluxion-gateway fluxion-mcp fluxion-scheduler \
  fluxion-sub fluxion-usage fluxion-web
do
  link="$BIN_DIR/$command_name"
  if [[ -L "$link" ]]; then
    target="$(readlink "$link")"
    case "$target" in
      "$INSTALL_DIR"/*) rm -f "$link" ;;
    esac
  fi
done

if [[ "$(uname -s)" == "Darwin" ]]; then
  note "Removing macOS app"
  app_paths=("$APP_DIR/Fluxion.app")
  for candidate in "$HOME/Applications/Fluxion.app" "/Applications/Fluxion.app"; do
    skip=0
    for existing in "${app_paths[@]}"; do
      if [[ "$existing" == "$candidate" ]]; then
        skip=1
        break
      fi
    done
    if (( ! skip )); then
      app_paths+=("$candidate")
    fi
  done
  for app_path in "${app_paths[@]}"; do
    rm -rf "$app_path"
  done
fi

desktop_config="$HOME/Library/Application Support/Fluxion/config.json"
if ((PURGE)); then
  note "Purging managed checkout and user data"
  rm -rf "$INSTALL_DIR"
  rm -rf "$HOME/Library/Application Support/Fluxion"
else
  backup_dir="$(dirname "$INSTALL_DIR")/fluxion-backup-$(date +%Y%m%d-%H%M%S)"
  has_backup=0
  for user_path in "$INSTALL_DIR/.env" "$INSTALL_DIR/data" "$desktop_config"; do
    if [[ -e "$user_path" ]]; then
      mkdir -p "$backup_dir"
      if [[ "$user_path" == "$desktop_config" ]]; then
        mv "$user_path" "$backup_dir/desktop-config.json"
      else
        mv "$user_path" "$backup_dir/"
      fi
      has_backup=1
    fi
  done
  rm -rf "$INSTALL_DIR"
  if ((has_backup)); then
    note "Preserved user data in $backup_dir"
  fi
fi

note "Fluxion uninstalled"
