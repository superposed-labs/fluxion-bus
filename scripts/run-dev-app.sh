#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH="" cd -- "$(dirname -- "$0")/.." && pwd)"
APP="$ROOT/desktop/Fluxion.app"
APP_BIN="$APP/Contents/MacOS/Fluxion"
BUILD_WEB=1
REBUILD_APP=0

usage() {
  cat <<'EOF'
Run the macOS app against this working tree instead of the managed backend.

Usage: scripts/run-dev-app.sh [options]

Options:
  --skip-web      Reuse the current src/fluxion/web/static build
  --rebuild-app   Recompile the native macOS app before launching
  -h, --help      Show this help

The production backend selection in ~/Library/Application Support/Fluxion/config.json
is not changed. FLUXION_REPO_PATH applies only to the launched development process.
EOF
}

die() {
  printf 'fluxion dev app: error: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '\n==> %s\n' "$*"
}

while (($#)); do
  case "$1" in
    --skip-web) BUILD_WEB=0; shift ;;
    --rebuild-app) REBUILD_APP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ "$(uname -s)" == "Darwin" ]] || die "the desktop app requires macOS"
[[ -f "$ROOT/.env" ]] || die "missing $ROOT/.env"
[[ -x "$ROOT/.venv/bin/fluxion-web" ]] \
  || die "missing workspace virtualenv; create .venv and install Fluxion first"

# Two Fluxion app processes would compete for the same UI/service lifecycle and
# make it unclear which repository owns port 8765. Never kill the production
# instance implicitly; make the developer choose when to switch environments.
if pgrep -f '/Fluxion\.app/Contents/MacOS/Fluxion$' >/dev/null 2>&1; then
  die "Fluxion is already running; quit it before starting the workspace app"
fi

if ((BUILD_WEB)); then
  command -v npm >/dev/null 2>&1 || die "npm is required to build the Web console"
  [[ -d "$ROOT/web/node_modules" ]] \
    || die "web/node_modules is missing; run 'npm --prefix web ci' once"
  note "Building Web console"
  npm --prefix "$ROOT/web" run build
fi

if ((REBUILD_APP)) || [[ ! -x "$APP_BIN" ]]; then
  note "Building macOS app"
  bash "$ROOT/desktop/build.sh"
fi

[[ -x "$APP_BIN" ]] || die "app binary not found after build: $APP_BIN"

note "Launching workspace app"
printf 'Backend: %s\n' "$ROOT"
printf 'Data:    %s\n' "$ROOT/data"
printf 'Config:  %s\n' "$ROOT/.env"

cd "$ROOT"
export FLUXION_REPO_PATH="$ROOT"
exec "$APP_BIN"
