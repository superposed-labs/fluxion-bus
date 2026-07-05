#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH="" cd -- "$(dirname -- "$0")/.." && pwd)"
DIST_DIR="${FLUXION_DIST_DIR:-$ROOT/dist/macos}"
APP="$ROOT/desktop/Fluxion.app"
DMG="$DIST_DIR/Fluxion.dmg"
ZIP="$DIST_DIR/Fluxion.app.zip"

note() {
  printf '\n==> %s\n' "$*"
}

notary_args() {
  if [ -n "${FLUXION_NOTARY_KEYCHAIN_PROFILE:-}" ]; then
    printf '%s\n' --keychain-profile "$FLUXION_NOTARY_KEYCHAIN_PROFILE"
  elif [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ]; then
    printf '%s\n' --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_SPECIFIC_PASSWORD"
  else
    return 1
  fi
}

notarize_artifact() {
  local artifact="$1"
  local args

  if [ "${FLUXION_NOTARIZE:-0}" != "1" ]; then
    return 0
  fi

  if ! args="$(notary_args)"; then
    echo "error: FLUXION_NOTARIZE=1 requires FLUXION_NOTARY_KEYCHAIN_PROFILE or APPLE_ID, APPLE_TEAM_ID, and APPLE_APP_SPECIFIC_PASSWORD" >&2
    exit 1
  fi

  note "Submitting $(basename "$artifact") for Apple notarization"
  # shellcheck disable=SC2086
  xcrun notarytool submit "$artifact" $args --wait

  note "Stapling notarization ticket to $(basename "$artifact")"
  xcrun stapler staple "$artifact"
  xcrun stapler validate "$artifact"
}

note "Preparing distribution directory"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

if command -v npm >/dev/null 2>&1; then
  note "Building Web console static assets"
  (
    cd "$ROOT/web"
    npm ci
    npm run build
  )
else
  note "npm not found; using existing src/fluxion/web/static assets"
fi

note "Building macOS app"
# Distribution builds bundle the backend source snapshot plus dependency
# wheels so first-run setup works without git, npm, or network access.
FLUXION_BUNDLE_WHEELS="${FLUXION_BUNDLE_WHEELS:-1}" bash "$ROOT/desktop/build.sh"

if [ "${FLUXION_NOTARIZE:-0}" = "1" ]; then
  NOTARY_ZIP="$DIST_DIR/Fluxion-notary.zip"
  note "Creating temporary app archive for notarization"
  ditto -c -k --keepParent "$APP" "$NOTARY_ZIP"
  notarize_artifact "$NOTARY_ZIP"
  note "Stapling notarization ticket to Fluxion.app"
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
  rm -f "$NOTARY_ZIP"
fi

note "Creating zip artifact"
ditto -c -k --keepParent "$APP" "$ZIP"

note "Creating DMG artifact"
if command -v npx >/dev/null 2>&1; then
  APPDMG_SPEC="$DIST_DIR/appdmg.json"
  cat >"$APPDMG_SPEC" <<EOF
{
  "title": "Fluxion",
  "icon-size": 96,
  "window": {
    "size": { "width": 520, "height": 310 }
  },
  "contents": [
    { "x": 150, "y": 150, "type": "file", "path": "$APP" },
    { "x": 370, "y": 150, "type": "link", "path": "/Applications" }
  ]
}
EOF
  NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-$DIST_DIR/npm-cache}" \
    npx --yes appdmg "$APPDMG_SPEC" "$DMG"
  rm -f "$APPDMG_SPEC"
else
  note "npx not found; creating a basic DMG without custom Finder layout"
  DMG_ROOT="$DIST_DIR/dmg-root"
  mkdir -p "$DMG_ROOT"
  cp -R "$APP" "$DMG_ROOT/Fluxion.app"
  ln -s /Applications "$DMG_ROOT/Applications"
  hdiutil create \
    -volname "Fluxion" \
    -srcfolder "$DMG_ROOT" \
    -ov \
    -format UDZO \
    "$DMG" >/dev/null
  rm -rf "$DMG_ROOT"
fi

if [ -n "${FLUXION_CODESIGN_IDENTITY:-}" ]; then
  note "Signing DMG artifact"
  codesign --force --timestamp -s "$FLUXION_CODESIGN_IDENTITY" "$DMG"
fi
notarize_artifact "$DMG"

note "Artifacts"
printf '%s\n' "$DMG"
printf '%s\n' "$ZIP"
