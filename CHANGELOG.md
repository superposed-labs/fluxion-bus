# Changelog

All notable changes to Fluxion are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Fluxion ships from `main` as a rolling release; tagged versions mark notable
milestones.

## [Unreleased]

## [1.0.2] - 2026-07-10

### Added

- **Claude model-scoped limits** — backend, web, and desktop app now support
  per-model (Fable) rate-limit tracking with CJK-aligned visual display.

### Changed

- **Automatic versioning** — package version is now derived from git tags via
  `setuptools-scm`; no manual version bumps needed for releases.
- **Smoother in-app updates** — silent backend upgrades and gentler update
  prompts in the desktop app.
- **Scheduler** — quota-refresh edge detection is debounced over two
  observations to reduce false positives.

### Fixed

- **Desktop** — the notch no longer briefly reappears when entering or exiting
  fullscreen.

## [1.0.1] - 2026-07-07

### Added

- **In-app auto-updates** — the macOS desktop app updates itself via Sparkle;
  Preferences → Check for Updates now performs a real check instead of always
  reporting the latest version.
- **Homebrew cask** — install with
  `brew install --cask superposed-labs/tap/fluxion`.

### Changed

- Release automation now signs a Sparkle appcast, bumps the Homebrew cask, and
  stamps the app's user-facing version from the git tag.

## [1.0.0] - 2026-07-06

Initial open-source release.

### Added

- **Cross-provider delegation** — a local MCP server that lets your primary
  agent delegate scoped subtasks to Codex, Claude Code, or Antigravity while
  preserving sessions and reporting progress and results.
- **MCP sub-agent tools** — `run_subagent`, `list_projects`,
  `list_agent_models`, `get_task_status`, `get_task_result`,
  `cancel_subagent_run`, and `revert_subagent_run`, backed by a project registry
  and a per-workspace authorization policy (trusted roots, write allow-list,
  deny-list).
- **Messaging gateway** — drive tasks from chat over Slack, Telegram, WeChat,
  LINE, QQ, and Feishu, with fail-closed per-channel allow-lists.
- **Web observation deck** (`fluxion-web`) — browser console for task summaries,
  logs, artifacts, and file-change review; loopback-only by default and
  token-gated (`FLUXION_UI_TOKEN`) when bound to a non-loopback address.
- **Scheduler** — run recurring and one-off tasks on a schedule.
- **Quota & usage monitoring** — reads provider-reported 5h/weekly quota
  windows, detects and notifies on resets, and can automatically issue a minimal
  call after a reset to start the next rolling window immediately.
- **macOS desktop app** — a menu-bar app that supervises the gateway and
  services, with a notch UI and localized preferences.
- **Change tracking & revert** — records file changes per run for review and
  recovery.
- **Trilingual documentation** — English, 简体中文, and 日本語 READMEs plus a
  `docs/` reference set (architecture, configuration, MCP, scheduler, quota, and
  usage statistics).

[Unreleased]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/superposed-labs/fluxion-bus/releases/tag/v1.0.0
