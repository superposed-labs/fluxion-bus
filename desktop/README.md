# Fluxion macOS App

The native Fluxion menu bar app displays provider quota, opens the local
console, controls companion services, and configures the messaging channels
(Slack, Telegram, LINE, QQ, Feishu, WeChat). It is macOS only and requires
macOS 12 or newer; the Launch at Login toggle requires macOS 13+.

## Build

From the Fluxion repository root:

```bash
./desktop/build.sh
```

The script assembles, compiles, and ad-hoc signs `desktop/Fluxion.app` from the
tracked sources under `desktop/` and `desktop/Resources/`. The bundle itself is
a build output and is not tracked in git, so run this once after cloning.

## Release package

For user-facing distribution, build a precompiled app bundle and DMG:

```bash
scripts/package-macos-app.sh
```

The package script builds the Web console static assets into the Python package,
builds `Fluxion.app`, then writes:

```text
dist/macos/Fluxion.dmg
dist/macos/Fluxion.app.zip
```

GitHub Actions also runs this flow for tagged releases. The DMG lets users
install the desktop app without Xcode Command Line Tools or a local Swift
compiler.

The current prebuilt DMG is unsigned and not notarized. macOS Gatekeeper may
block it on first launch. Users who downloaded it from the official GitHub
Release and verified `SHA256SUMS` can open it without Terminal by trying once,
then going to **System Settings -> Privacy & Security**, finding the Fluxion
warning near the bottom, and clicking **Open Anyway** before launching it again.
Advanced users can instead remove the quarantine flag:

```bash
xattr -dr com.apple.quarantine /Applications/Fluxion.app
```

## Run from the repository

```bash
open desktop/Fluxion.app
```

When the app remains under `desktop/`, it can discover the surrounding Fluxion
repository automatically.

## Install in Applications

After building:

```bash
cp -R desktop/Fluxion.app /Applications/Fluxion.app
open /Applications/Fluxion.app
```

On first launch from `/Applications`, if no existing backend is configured, the
app uses the managed backend path:

```text
~/.local/share/fluxion
```

and offers **Install / Repair**. That flow runs the backend installer with the
desktop app build disabled, creates or repairs `.venv`, initializes `.env`, and
links Fluxion commands into `~/.local/bin`. If Python 3.12+ is unavailable and
Homebrew is installed, it installs `python@3.13` automatically.

The app stores only the selected backend path in:

```text
~/Library/Application Support/Fluxion/config.json
```

The backend directory continues to own `.env`, `.venv`, and `data/`. Change the
selected backend from **Preferences → Backend**; restart the app to apply the
change.

If the selected backend does not contain `.env` or `.venv`, the app displays an
Install / Repair prompt.

## Development override

To run the app binary against a specific checkout without changing the saved
configuration:

```bash
FLUXION_REPO_PATH="$PWD" desktop/Fluxion.app/Contents/MacOS/Fluxion
```

Resolution order:

1. `FLUXION_REPO_PATH`
2. `~/Library/Application Support/Fluxion/config.json`
3. Repository surrounding `desktop/Fluxion.app`
4. `~/.local/share/fluxion`

The current build is ad-hoc signed and intended for local/source distribution.
A Developer ID signature and notarization are still required for normal public
distribution.

The repository-level [`scripts/install.sh`](../scripts/install.sh) automates the
user-level build and installs the app into `~/Applications/Fluxion.app`.
