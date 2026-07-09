#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${FLUXION_INSTALL_DIR:-$HOME/.local/share/fluxion}"
WORKSPACE="${FLUXION_WORKSPACE:-$HOME}"
REF="${FLUXION_REF:-main}"
REPO_URL="${FLUXION_REPO_URL:-https://github.com/superposed-labs/fluxion-bus.git}"
PYTHON_BIN="${FLUXION_PYTHON:-}"
FORCE_FULL="${FLUXION_FORCE_FULL:-0}"

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
  --force-full         Skip the fast in-place update and run the full installer
                       (used by the app's Repair flow so a broken venv is rebuilt)
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
    --force-full) FORCE_FULL=1; shift ;;
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

# Single EXIT trap for everything the script may leave behind; individual code
# paths fill in the variables instead of installing their own (competing) traps.
LOCK_DIR=""
TMP_INSTALLER_DIR=""
cleanup() {
  # `if` (not `&&`) so a false condition can't fail the trap under set -e and
  # replace the script's real exit status.
  if [[ -n "$LOCK_DIR" ]]; then rm -rf "$LOCK_DIR"; fi
  if [[ -n "$TMP_INSTALLER_DIR" ]]; then rm -rf "$TMP_INSTALLER_DIR"; fi
}
trap cleanup EXIT

# Serialize installs into the same directory. Two app instances launching at
# login (or an install racing a repair) would otherwise interleave the
# move/extract/swap steps and corrupt the install. The lock records its owner
# pid so a lock orphaned by a killed run is reclaimed instead of wedging every
# future install.
acquire_lock() {
  local lock waited=0 owner
  lock="$(dirname "$INSTALL_DIR")/.fluxion-bootstrap.lock"
  mkdir -p "$(dirname "$lock")"
  until mkdir "$lock" 2>/dev/null; do
    owner="$(cat "$lock/pid" 2>/dev/null || true)"
    if [[ -n "$owner" ]] && ! kill -0 "$owner" 2>/dev/null; then
      printf 'Removing stale install lock left by pid %s.\n' "$owner"
      rm -rf "$lock"
      continue
    fi
    if ((waited == 0)); then
      printf 'Another Fluxion backend install is running; waiting for it to finish...\n'
    fi
    ((waited += 2))
    if ((waited >= 600)); then
      die "timed out waiting for the install lock: $lock (remove it if no install is running)"
    fi
    sleep 2
  done
  echo "$$" > "$lock/pid"
  LOCK_DIR="$lock"
}

# Repair the aftermath of a fast_update killed mid-swap (power loss, forced
# logout). If the install directory is gone, the previous tree — including the
# user's .env and data, possibly already carried into the extracted snapshot —
# is still sitting in the swap staging directories; put it back before anything
# else decides this is a fresh install. Leftover staging directories are then
# swept, except any that still hold user state (never delete those silently).
# Runs under the install lock, so it cannot race a live swap.
recover_interrupted_update() {
  local parent olddir newdir dir
  parent="$(dirname "$INSTALL_DIR")"
  olddir="$(ls -d "$parent"/.fluxion-old-* 2>/dev/null | sort | tail -n 1 || true)"
  newdir="$(ls -d "$parent"/.fluxion-update-* 2>/dev/null | sort | tail -n 1 || true)"

  if [[ ! -e "$INSTALL_DIR" && -n "$olddir" && -d "$olddir" ]]; then
    printf 'Recovering backend from an interrupted update...\n'
    if [[ -n "$newdir" && -d "$newdir" ]]; then
      [[ -e "$olddir/.venv" || ! -e "$newdir/.venv" ]] || mv "$newdir/.venv" "$olddir/.venv"
      [[ -e "$olddir/.env" || ! -e "$newdir/.env" ]] || mv "$newdir/.env" "$olddir/.env"
      [[ -e "$olddir/data" || ! -e "$newdir/data" ]] || mv "$newdir/data" "$olddir/data"
    fi
    mv "$olddir" "$INSTALL_DIR"
  fi

  for dir in "$parent"/.fluxion-old-* "$parent"/.fluxion-update-*; do
    [[ -d "$dir" ]] || continue
    if [[ -e "$dir/.env" || -e "$dir/data" ]]; then
      printf '[warn] leaving %s in place: it still holds .env/data from an earlier update.\n' "$dir" >&2
      continue
    fi
    rm -rf "$dir"
  done
}

acquire_lock
recover_interrupted_update

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

# Copy bundle-only assets that never live in the source archive: the packaged
# revision marker and the prebuilt Web console static files.
install_bundled_assets() {
  if ((USE_ARCHIVE)) && [[ -f "$RESOURCES_DIR/Backend/REVISION" ]]; then
    cp "$RESOURCES_DIR/Backend/REVISION" "$INSTALL_DIR/.fluxion-revision"
  fi

  local bundled_static="$RESOURCES_DIR/WebStatic"
  if [[ -d "$bundled_static" ]]; then
    printf 'Installing bundled Web console static assets...\n'
    rm -rf "$INSTALL_DIR/src/fluxion/web/static"
    mkdir -p "$INSTALL_DIR/src/fluxion/web/static"
    cp -R "$bundled_static/." "$INSTALL_DIR/src/fluxion/web/static/"
  fi
}

# Hash of the parts of pyproject.toml that decide whether the venv must be
# rebuilt: dependencies, optional dependencies, console/GUI/plugin entry points,
# and the build backend. Everything else (source code, docs, formatting) can be
# swapped in place without touching the environment. Prints nothing on failure.
deps_signature() {
  local pyproject="$1"
  [[ -f "$pyproject" ]] || return 1
  FLUXION_PYPROJECT="$pyproject" "$PYTHON_BIN" - <<'PY'
import hashlib
import json
import os
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11; caller falls back to full reinstall.
    sys.exit(1)

try:
    with open(os.environ["FLUXION_PYPROJECT"], "rb") as handle:
        data = tomllib.load(handle)
except Exception:
    sys.exit(1)

project = data.get("project", {})
build = data.get("build-system", {})
signature = {
    "build_backend": build.get("build-backend", ""),
    "build_requires": sorted(build.get("requires", [])),
    "dependencies": sorted(project.get("dependencies", [])),
    "optional": {
        name: sorted(reqs)
        for name, reqs in sorted(project.get("optional-dependencies", {}).items())
    },
    "scripts": dict(sorted(project.get("scripts", {}).items())),
    "gui_scripts": dict(sorted(project.get("gui-scripts", {}).items())),
    "entry_points": {
        group: dict(sorted(entries.items()))
        for group, entries in sorted(project.get("entry-points", {}).items())
    },
}
digest = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
print(digest)
PY
}

# Fast in-place update: when the app ships a new source snapshot whose
# dependency/entry-point signature is unchanged, the existing editable venv is
# still valid (its .pth points at the fixed install path). We can then swap the
# source tree without rebuilding the venv or running pip, turning an ~18s
# reinstall into a sub-second update. Returns 0 when it performed the update, or
# 1 to fall through to the full reinstall path. The original directory is only
# moved aside after eligibility is confirmed, so any failure rolls back to it.
fast_update() {
  ((FORCE_FULL == 0)) || return 1
  ((USE_ARCHIVE)) || return 1
  [[ -f "$BUNDLED_ARCHIVE" ]] || return 1
  [[ -f "$INSTALL_DIR/pyproject.toml" ]] || return 1
  [[ -x "$INSTALL_DIR/.venv/bin/python" ]] || return 1
  # Editable entry points already generated in the venv => a plain code swap keeps them working.
  [[ -x "$INSTALL_DIR/.venv/bin/fluxion" ]] || return 1
  "$INSTALL_DIR/.venv/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' || return 1

  local parent stamp newdir olddir old_sig new_sig
  parent="$(dirname "$INSTALL_DIR")"
  stamp="$(date +%Y%m%d-%H%M%S)"
  newdir="$parent/.fluxion-update-$stamp"
  olddir="$parent/.fluxion-old-$stamp"

  # Extract the new snapshot next to the install (same filesystem) without
  # touching the live directory yet.
  rm -rf "$newdir"
  if ! mkdir -p "$newdir" \
    || ! tar -xzf "$BUNDLED_ARCHIVE" -C "$newdir" --strip-components=1; then
    rm -rf "$newdir"
    return 1
  fi
  if [[ ! -f "$newdir/pyproject.toml" || ! -d "$newdir/src/fluxion" ]]; then
    rm -rf "$newdir"
    return 1
  fi

  old_sig="$(deps_signature "$INSTALL_DIR/pyproject.toml")" || { rm -rf "$newdir"; return 1; }
  new_sig="$(deps_signature "$newdir/pyproject.toml")" || { rm -rf "$newdir"; return 1; }
  if [[ -z "$new_sig" || "$old_sig" != "$new_sig" ]]; then
    printf 'Backend dependencies changed; using the full reinstall path.\n'
    rm -rf "$newdir"
    return 1
  fi

  printf 'Backend dependencies unchanged; performing fast in-place update...\n'

  # The original is now moved aside as a whole (mirroring the full path), so it
  # stays intact until the swap succeeds.
  if ! mv "$INSTALL_DIR" "$olddir"; then
    rm -rf "$newdir"
    return 1
  fi

  # Carry preserved state into the new tree. Same-filesystem renames are atomic
  # and instant; the venv keeps its baked-in absolute paths because the final
  # install path is unchanged.
  local swap_err=0
  set +e
  mv "$olddir/.venv" "$newdir/.venv" || swap_err=1
  if ((swap_err == 0)) && [[ -e "$olddir/.env" ]]; then
    mv "$olddir/.env" "$newdir/.env" || swap_err=1
  fi
  if ((swap_err == 0)) && [[ -e "$olddir/data" ]]; then
    rm -rf "$newdir/data"
    mv "$olddir/data" "$newdir/data" || swap_err=1
  fi
  if ((swap_err == 0)); then
    mv "$newdir" "$INSTALL_DIR" || swap_err=1
  fi

  if ((swap_err)); then
    printf '[warn] fast update failed mid-swap; restoring previous backend.\n' >&2
    # Move any relocated state back into the untouched original, then restore it.
    [[ -e "$olddir/.venv" ]] || mv "$newdir/.venv" "$olddir/.venv" 2>/dev/null
    [[ -e "$olddir/.env" || ! -e "$newdir/.env" ]] || mv "$newdir/.env" "$olddir/.env" 2>/dev/null
    [[ -e "$olddir/data" || ! -e "$newdir/data" ]] || mv "$newdir/data" "$olddir/data" 2>/dev/null
    # Only discard the staging tree once it provably holds no user state — if a
    # move-back above failed, deleting it would take the venv/.env/data with it.
    if [[ -e "$newdir/.venv" || -e "$newdir/.env" || -e "$newdir/data" ]]; then
      printf '[warn] leaving %s in place: it still holds state that could not be moved back.\n' "$newdir" >&2
    else
      rm -rf "$newdir"
    fi
    [[ -e "$INSTALL_DIR" ]] || mv "$olddir" "$INSTALL_DIR"
    set -e
    return 1
  fi
  set -e

  # Old tree is now just stale source; drop it in the background.
  rm -rf "$olddir" >/dev/null 2>&1 &

  install_bundled_assets
  printf 'Fast backend update complete: %s\n' "$INSTALL_DIR"
  return 0
}

if fast_update; then
  exit 0
fi

prepare_install_dir

if [[ -f "$SCRIPT_DIR/install.sh" ]]; then
  INSTALLER="$SCRIPT_DIR/install.sh"
elif ((USE_ARCHIVE)); then
  die "bundled source snapshot found but install.sh is missing next to it: $SCRIPT_DIR"
else
  TMP_INSTALLER_DIR="$(mktemp -d)"
  INSTALLER="$TMP_INSTALLER_DIR/install.sh"
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

install_bundled_assets
