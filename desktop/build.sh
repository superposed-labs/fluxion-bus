#!/bin/bash
# Fluxion compilation script.
#
# The .app bundle is a build output and is not tracked in git. Every tracked
# input lives under desktop/ (Swift sources) and desktop/Resources/ (Info.plist
# template, AppIcon.icns, *.lproj). This script assembles them into
# desktop/Fluxion.app and ad-hoc signs the result.
#
# Versions:
#   CFBundleShortVersionString — the user-facing semver. Derived here from the
#     release tag (FLUXION_APP_VERSION, else the nearest `git describe` tag) so
#     it never needs hand-bumping. Falls back to the value in the tracked
#     desktop/Resources/Info.plist when no tag is available (dev builds).
#   CFBundleVersion — the build number, derived here from the git commit count
#     so it increments on every commit, never needs hand-bumping, and is never
#     stored in a tracked file. Falls back to 0 outside a git checkout.
set -e

# Navigate to the repository root directory
CDPATH="" cd -- "$(dirname -- "$0")/.."

APP="desktop/Fluxion.app"
SRC_PLIST="desktop/Resources/Info.plist"
WEB_INDEX="src/fluxion/web/static/index.html"

if [ ! -f "$SRC_PLIST" ]; then
    echo "error: $SRC_PLIST not found" >&2
    exit 1
fi

if [ ! -f "$WEB_INDEX" ]; then
    cat >&2 <<EOF
error: Web console assets are missing: $WEB_INDEX

Build them before building the macOS app:
  npm --prefix web ci
  npm --prefix web run build

desktop/build.sh reuses an existing Web console build and does not build it automatically.
EOF
    if ! command -v npm >/dev/null 2>&1; then
        echo "error: npm is not installed; install Node.js 18 or newer first." >&2
    fi
    exit 1
fi

# 1. Derive the build number from the git commit count
BUILD_NUM=$(git rev-list --count HEAD 2>/dev/null || echo 0)

# Derive the user-facing semver from the release tag so it never needs
# hand-bumping. Priority: explicit FLUXION_APP_VERSION (set by CI from the
# pushed tag), then the nearest git tag, then leave the tracked Info.plist
# value untouched (dev builds with no tag).
SHORT_VERSION="${FLUXION_APP_VERSION:-}"
if [ -z "$SHORT_VERSION" ]; then
    SHORT_VERSION=$(git describe --tags --abbrev=0 2>/dev/null || true)
fi
SHORT_VERSION="${SHORT_VERSION#v}"

echo "=== Fluxion Build ==="
echo "Build version (git commit count): $BUILD_NUM"
echo "Short version (semver): ${SHORT_VERSION:-<from Info.plist>}"

# 2. Assemble the bundle skeleton from tracked sources
echo "Assembling bundle from desktop/Resources/..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$SRC_PLIST" "$APP/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$BUILD_NUM" "$APP/Contents/Info.plist"
if [ -n "$SHORT_VERSION" ]; then
    plutil -replace CFBundleShortVersionString -string "$SHORT_VERSION" "$APP/Contents/Info.plist"
fi

# Sparkle auto-update is enabled only for official distribution builds. Dev and
# self-compiled builds ship without the feed URL so the updater stays inert and
# never offers to replace a locally built app with the official binary.
# package-macos-app.sh sets FLUXION_ENABLE_SPARKLE=1.
if [ "${FLUXION_ENABLE_SPARKLE:-0}" = "1" ]; then
    echo "Sparkle auto-update: enabled (distribution build)"
else
    echo "Sparkle auto-update: disabled (dev build; stripping SUFeedURL)"
    plutil -remove SUFeedURL "$APP/Contents/Info.plist" 2>/dev/null || true
fi
cp desktop/Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
find desktop/Resources -maxdepth 1 -name "*.lproj" -type d -exec cp -R {} "$APP/Contents/Resources/" \;
mkdir -p "$APP/Contents/Resources/Scripts"
cp scripts/bootstrap-backend.sh "$APP/Contents/Resources/Scripts/bootstrap-backend.sh"
cp scripts/install.sh "$APP/Contents/Resources/Scripts/install.sh"
chmod +x "$APP/Contents/Resources/Scripts/bootstrap-backend.sh"
chmod +x "$APP/Contents/Resources/Scripts/install.sh"
mkdir -p "$APP/Contents/Resources/WebStatic"
cp -R src/fluxion/web/static/. "$APP/Contents/Resources/WebStatic/"

# 2b. Vendor the Sparkle updater framework (in-app auto-update support).
# Fetched on demand and cached under desktop/Frameworks (gitignored) so the
# ~3 MB binary stays out of the repo. Pinned by version + tarball checksum.
SPARKLE_VERSION="2.9.4"
SPARKLE_SHA256="ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9"
SPARKLE_CACHE="desktop/Frameworks/Sparkle-$SPARKLE_VERSION"
SPARKLE_FW_SRC="$SPARKLE_CACHE/Sparkle.framework"
if [ ! -d "$SPARKLE_FW_SRC" ]; then
    echo "Fetching Sparkle $SPARKLE_VERSION..."
    mkdir -p "$SPARKLE_CACHE"
    SPARKLE_TAR="$(mktemp -t sparkle).tar.xz"
    curl -fsSL -o "$SPARKLE_TAR" \
        "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VERSION/Sparkle-$SPARKLE_VERSION.tar.xz"
    echo "$SPARKLE_SHA256  $SPARKLE_TAR" | shasum -a 256 -c - \
        || { echo "error: Sparkle tarball checksum mismatch" >&2; exit 1; }
    tar -xJf "$SPARKLE_TAR" -C "$SPARKLE_CACHE" Sparkle.framework
    rm -f "$SPARKLE_TAR"
fi
echo "Embedding Sparkle.framework..."
mkdir -p "$APP/Contents/Frameworks"
ditto "$SPARKLE_FW_SRC" "$APP/Contents/Frameworks/Sparkle.framework"

# Bundle a backend source snapshot so first-run setup can install without git
# or network access. `git archive` packs committed files only, which keeps
# local junk (.venv, data, untracked experiments) out of the distributed app.
# Outside a git checkout the snapshot is skipped and the bootstrap script
# falls back to the git-based installer.
#
# The REVISION marker (the last commit touching backend paths) is what makes an
# installed app silently upgrade the managed backend to match its snapshot — so
# only distribution builds write it. A dev or self-compiled app carrying its own
# ad-hoc revision would otherwise fight the official install (and other local
# builds) over ~/.local/share/fluxion, reinstalling the backend on every launch
# of a differently-built app. Mirrors the Sparkle gating above; override with
# FLUXION_DISTRIBUTION=0/1.
FLUXION_DISTRIBUTION="${FLUXION_DISTRIBUTION:-${FLUXION_ENABLE_SPARKLE:-0}}"
BACKEND_DIR="$APP/Contents/Resources/Backend"
if git rev-parse HEAD >/dev/null 2>&1; then
    echo "Bundling backend source snapshot..."
    mkdir -p "$BACKEND_DIR"
    git archive --format=tar.gz --prefix=fluxion/ \
        -o "$BACKEND_DIR/backend.tar.gz" HEAD
    if [ "$FLUXION_DISTRIBUTION" = "1" ]; then
        git log -1 --format=%H -- src/fluxion pyproject.toml scripts web > "$BACKEND_DIR/REVISION"
    else
        echo "Dev build: skipping Backend/REVISION (no silent backend upgrades)"
    fi
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
    desktop/AppDelegate/AppDelegate+ReminderPrompt.swift \
    desktop/UI/Localization.swift \
    desktop/UI/UpdaterController.swift \
    desktop/UI/QuotaFormatter.swift \
    desktop/UI/Theme.swift \
    desktop/UI/CardViews.swift \
    desktop/UI/RichMenuPanel.swift \
    desktop/UI/MainWindow.swift \
    desktop/UI/Preferences/PreferencesWindow.swift \
    desktop/UI/Preferences/PreferencesWindow+Sections.swift \
    desktop/UI/Preferences/ProviderRouting/ProviderRoutingModels.swift \
    desktop/UI/Preferences/ProviderRouting/ProviderRoutingComponents.swift \
    desktop/UI/Preferences/ProviderRouting/PreferencesWindow+ProviderRouting.swift \
    desktop/UI/Preferences/ProviderRouting/PreferencesWindow+ProviderCards.swift \
    desktop/UI/Preferences/ProviderRouting/PreferencesWindow+ProviderSections.swift \
    desktop/UI/Preferences/ProviderRouting/ProviderRouteEditorRows.swift \
    desktop/UI/Preferences/ProviderRouting/ProviderRouteEditorSheet.swift \
    desktop/UI/Preferences/ProviderRouting/ProviderCodexModels.swift \
    desktop/UI/Preferences/ProviderRouting/ProviderCodexRowViews.swift \
    desktop/UI/Preferences/ProviderRouting/ProviderCodexConfigSheet.swift \
    desktop/UI/Preferences/ProviderRouting/ProviderInstallRepairSheet.swift \
    desktop/UI/Preferences/PreferencesWindow+Integrations.swift \
    desktop/UI/Preferences/PreferencesWindow+PendingUsers.swift \
    desktop/UI/WeChatLoginWindow.swift \
    desktop/UI/WelcomeWindow.swift \
    desktop/UI/Notch/NotchQuotaPresenter.swift \
    desktop/UI/Notch/NotchControls.swift \
    desktop/UI/Notch/CompactTrendBars.swift \
    desktop/UI/Notch/PoolResetHover.swift \
    desktop/UI/Notch/NotchWindowController.swift \
    desktop/UI/Notch/NotchIslandView+Collapsed.swift \
    desktop/UI/Notch/NotchIslandView+Peek.swift \
    desktop/UI/Notch/NotchIslandView+Expanded.swift \
    desktop/UI/Notch/NotchWindow.swift \
    -framework UserNotifications \
    -F "$APP/Contents/Frameworks" \
    -framework Sparkle \
    -Xlinker -rpath -Xlinker @executable_path/../Frameworks \
    -o "$APP/Contents/MacOS/Fluxion"

# 4. Clear quarantine attributes and apply code signature
echo "Clearing quarantine attributes..."
xattr -cr "$APP" || true

# Sign inside-out: Sparkle's nested helpers and framework must each carry their
# own signature before the app is sealed. `--deep` is intentionally avoided —
# Apple deprecates it for distribution and it mis-signs Sparkle's helpers.
codesign_one() {
    if [ -n "${FLUXION_CODESIGN_IDENTITY:-}" ]; then
        codesign --force --options runtime --timestamp \
            -s "$FLUXION_CODESIGN_IDENTITY" "$1"
    else
        codesign --force -s - "$1"
    fi
}

SPARKLE_FW="$APP/Contents/Frameworks/Sparkle.framework"
if [ -n "${FLUXION_CODESIGN_IDENTITY:-}" ]; then
    echo "Applying Developer ID codesign signature (inside-out)..."
else
    echo "Applying ad-hoc codesign signature (inside-out)..."
fi
codesign_one "$SPARKLE_FW/Versions/B/XPCServices/Downloader.xpc"
codesign_one "$SPARKLE_FW/Versions/B/XPCServices/Installer.xpc"
codesign_one "$SPARKLE_FW/Versions/B/Updater.app"
codesign_one "$SPARKLE_FW/Versions/B/Autoupdate"
codesign_one "$SPARKLE_FW"
codesign_one "$APP"

echo "Build succeeded! (Build version: $BUILD_NUM)"
