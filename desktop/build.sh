#!/bin/bash
# Fluxion compilation script.
#
# The .app bundle is a build output and is not tracked in git. Every tracked
# input lives under desktop/ (Swift sources) and desktop/Resources/ (Info.plist
# template, AppIcon.icns, *.lproj). This script assembles them into
# desktop/Fluxion.app and ad-hoc signs the result.
#
# Versions:
#   CFBundleShortVersionString — the user-facing semver, set by hand in the
#     tracked desktop/Resources/Info.plist when cutting a release.
#   CFBundleVersion — the build number, derived here from the git commit count
#     so it increments on every commit, never needs hand-bumping, and is never
#     stored in a tracked file. Falls back to 0 outside a git checkout.
set -e

# Navigate to the repository root directory
CDPATH="" cd -- "$(dirname -- "$0")/.."

APP="desktop/Fluxion.app"
SRC_PLIST="desktop/Resources/Info.plist"

if [ ! -f "$SRC_PLIST" ]; then
    echo "error: $SRC_PLIST not found" >&2
    exit 1
fi

# 1. Derive the build number from the git commit count
BUILD_NUM=$(git rev-list --count HEAD 2>/dev/null || echo 0)
echo "=== Fluxion Build ==="
echo "Build version (git commit count): $BUILD_NUM"

# 2. Assemble the bundle skeleton from tracked sources
echo "Assembling bundle from desktop/Resources/..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$SRC_PLIST" "$APP/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$BUILD_NUM" "$APP/Contents/Info.plist"
cp desktop/Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
find desktop/Resources -maxdepth 1 -name "*.lproj" -type d -exec cp -R {} "$APP/Contents/Resources/" \;
mkdir -p "$APP/Contents/Resources/Scripts"
cp scripts/bootstrap-backend.sh "$APP/Contents/Resources/Scripts/bootstrap-backend.sh"
cp scripts/install.sh "$APP/Contents/Resources/Scripts/install.sh"
chmod +x "$APP/Contents/Resources/Scripts/bootstrap-backend.sh"
chmod +x "$APP/Contents/Resources/Scripts/install.sh"
if [ -d "src/fluxion/web/static" ]; then
    mkdir -p "$APP/Contents/Resources/WebStatic"
    cp -R src/fluxion/web/static/. "$APP/Contents/Resources/WebStatic/"
fi

# Bundle a backend source snapshot so first-run setup can install without git
# or network access. `git archive` packs committed files only, which keeps
# local junk (.venv, data, untracked experiments) out of the distributed app.
# Outside a git checkout the snapshot is skipped and the bootstrap script
# falls back to the git-based installer.
BACKEND_DIR="$APP/Contents/Resources/Backend"
if git rev-parse HEAD >/dev/null 2>&1; then
    echo "Bundling backend source snapshot..."
    mkdir -p "$BACKEND_DIR"
    git archive --format=tar.gz --prefix=fluxion/ \
        -o "$BACKEND_DIR/backend.tar.gz" HEAD
    git rev-parse HEAD > "$BACKEND_DIR/REVISION"
elif [ "${FLUXION_BUNDLE_WHEELS:-0}" = "1" ]; then
    echo "error: FLUXION_BUNDLE_WHEELS=1 requires a git checkout to snapshot the backend" >&2
    exit 1
else
    echo "warning: not a git checkout; skipping backend source snapshot" >&2
fi

# Optionally bundle dependency wheels so the backend installs fully offline.
# Distribution builds (scripts/package-macos-app.sh) enable this; local dev
# builds skip it because downloading wheels is slow and needs network anyway.
# Wheels are fetched per supported CPython version because binary wheels
# (pydantic-core, uvloop, ...) are version-specific and the end user's Python
# is unknown at build time.
if [ "${FLUXION_BUNDLE_WHEELS:-0}" = "1" ]; then
    echo "Bundling dependency wheels for offline install..."
    WHEEL_PY=""
    for candidate in python3.13 python3.12 python3.14 python3; do
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' 2>/dev/null; then
            WHEEL_PY="$candidate"
            break
        fi
    done
    if [ -z "$WHEEL_PY" ]; then
        echo "error: FLUXION_BUNDLE_WHEELS=1 requires Python 3.12+ on the build host" >&2
        exit 1
    fi
    WHEEL_REQS="$(mktemp)"
    "$WHEEL_PY" -c 'import tomllib; print("\n".join(tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]))' > "$WHEEL_REQS"
    # setuptools/wheel are needed by pip's isolated build environment when it
    # installs the project itself with --no-index.
    printf 'setuptools>=77\nwheel\n' >> "$WHEEL_REQS"
    for pyver in 3.12 3.13 3.14; do
        echo "Downloading wheels for Python $pyver..."
        "$WHEEL_PY" -m pip download --quiet \
            --only-binary=:all: \
            --python-version "$pyver" \
            --dest "$BACKEND_DIR/wheels" \
            -r "$WHEEL_REQS"
    done
    rm -f "$WHEEL_REQS"
fi

# 3. Compile Swift files
# The deployment target is pinned so the minimum supported macOS version is a
# deliberate choice, not whatever macOS the build host happens to run. APIs
# newer than this version must be wrapped in `if #available(...)`.
MACOS_MIN=12.0
echo "Compiling Swift source files (minimum macOS $MACOS_MIN)..."
swiftc -O \
    -target "$(uname -m)-apple-macos$MACOS_MIN" \
    desktop/main.swift \
    desktop/AppDelegate/AppDelegate+Repository.swift \
    desktop/AppDelegate/AppDelegate+Services.swift \
    desktop/AppDelegate/AppDelegate+Polling.swift \
    desktop/AppDelegate/AppDelegate+Rendering.swift \
    desktop/UI/Localization.swift \
    desktop/UI/QuotaFormatter.swift \
    desktop/UI/Theme.swift \
    desktop/UI/CardViews.swift \
    desktop/UI/RichMenuPanel.swift \
    desktop/UI/MainWindow.swift \
    desktop/UI/Preferences/PreferencesWindow.swift \
    desktop/UI/Preferences/PreferencesWindow+Sections.swift \
    desktop/UI/Preferences/PreferencesWindow+Integrations.swift \
    desktop/UI/Preferences/PreferencesWindow+PendingUsers.swift \
    desktop/UI/WeChatLoginWindow.swift \
    desktop/UI/WelcomeWindow.swift \
    desktop/UI/Notch/NotchQuotaPresenter.swift \
    desktop/UI/Notch/NotchControls.swift \
    desktop/UI/Notch/NotchWindowController.swift \
    desktop/UI/Notch/NotchIslandView+Collapsed.swift \
    desktop/UI/Notch/NotchIslandView+Peek.swift \
    desktop/UI/Notch/NotchIslandView+Expanded.swift \
    desktop/UI/Notch/NotchWindow.swift \
    -framework UserNotifications \
    -o "$APP/Contents/MacOS/Fluxion"

# 4. Clear quarantine attributes and apply code signature
echo "Clearing quarantine attributes..."
xattr -cr "$APP" || true

if [ -n "${FLUXION_CODESIGN_IDENTITY:-}" ]; then
    echo "Applying Developer ID codesign signature..."
    codesign --force --deep --options runtime --timestamp \
        -s "$FLUXION_CODESIGN_IDENTITY" "$APP"
else
    echo "Applying deep ad-hoc codesign signature..."
    codesign -s - --force --deep "$APP"
fi

echo "Build succeeded! (Build version: $BUILD_NUM)"
