# Changelog

All notable changes to Fluxion are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Fluxion ships from `main` as a rolling release; tagged versions mark notable
milestones.

## [Unreleased]

## [1.0.10] - 2026-07-22

### Fixed

- **Protected web console** — the desktop app now forwards `FLUXION_UI_TOKEN`
  to its embedded console, allowing authenticated API requests to load instead
  of returning 401 errors.
- **Usage statistics language** — the console's Stats page now consistently
  follows the selected browser language, including translated month labels and
  cache-hit percentages.
- **Scheduler startup** — desktop startup now distinguishes the scheduler
  daemon from short-lived scheduler CLI calls, preventing an intermittent loss
  of quota-reset alerts and auto-ping.

### Changed

- **Notch performance** — expanded Notch pages reuse their existing view trees
  during page flips, avoiding unnecessary date parsing and window reframing for
  smoother interaction.

## [1.0.9] - 2026-07-20

### Changed

- **Antigravity Notch rings** — dual-pool 5-hour rings now clearly identify
  which pool the headline represents, show the other pool's remaining quota or
  unlock countdown, and use consistent GEM/EXT labels across the desktop UI.

## [1.0.8] - 2026-07-20

### Added

- **Reminder coverage** — weekly quota-reset reminders default on when an
  agent is available, and Fluxion offers to cover agents detected after
  onboarding.
- **First-run display choice** — fresh installs choose the best menu-bar
  display for the active hardware: Notch on compatible built-in displays and
  the richer panel elsewhere.
- **Square app icon asset** — added a full-bleed icon variant for platforms
  that require square artwork.

### Changed

- **Antigravity quotas** — the obsolete model-grouping toggle and related
  environment settings have been removed; Fluxion now consistently uses the
  upstream grouped quota view.
- **Quota wording** — Auto-Ping, Notch, and menu surfaces now use distinct,
  context-appropriate labels for quota windows.

### Fixed

- **Desktop menu** — native quota rows now retain precise column alignment
  across English, Simplified Chinese, and Japanese.
- **Branding** — corrected a small vertical offset in the Fluxion logo paths.
- **Reminder setup** — the weekly reminder toggle no longer requests
  notification permission or promises reminders when no agent can be watched.

## [1.0.7] - 2026-07-19

### Added

- **Notch quota dashboard** — redesigned collapsed, Peek, and expanded Notch
  quota surfaces, with provider-specific layouts, richer usage analytics, and
  configurable gauge and display preferences.
- **Responsive web console** — narrow windows now prioritize the detail pane,
  clamp long summaries, and preserve full metadata through expand controls and
  tooltips.
- **Quota reset confirmation** — a neutral “confirming” state and eager refresh
  now replace a stale locked display while a predicted quota reset is verified.

### Changed

- **Menu bar** — the enhanced panel is more compact, and its cache percentage
  is now labelled “cache hit” to clarify its meaning.
- **Notch preferences** — experimental environment-variable names have been
  consolidated; existing local overrides should use the current names.

## [1.0.6] - 2026-07-15

### Added

- **Claude Fable** — Fable is available as a selectable Claude model alias in
  MCP model listings, with the correct pricing.
- **App language sync** — changing the desktop app language now also updates
  Fluxion's configured UI locale.

### Fixed

- **Usage and quotas** — Fluxion now includes archived Codex sessions, avoids
  double-counting session forks, scopes auto-ping quota checks correctly, and
  rejects stale quota-window timestamps when detecting resets.

## [1.0.5] - 2026-07-14

### Added

- **Quota health indicators** — quota bars now use clear remaining-capacity
  colors in the macOS notch and menu, making low limits easier to spot.
- **Localized reset notifications** — quota-reset alerts now use human-readable
  English, Simplified Chinese, or Japanese copy, including the detected reset
  reason.

### Fixed

- **Codex quota display** — when Codex temporarily omits its 5-hour window,
  Fluxion now shows that limit as uncapped while keeping weekly quotas and
  countdowns accurate.

## [1.0.4] - 2026-07-12

### Changed

- **Branding** — refreshed Fluxion's logo and macOS app icon, and added matching
  web favicon and touch-icon assets.

### Fixed

- **Pricing data** — newer bundled price tables now take precedence over stale
  local caches, refreshed prices are picked up by running services without a
  restart, and the usage CLI always writes to the configured cache location.
- **Scheduler** — transient usage samples with a backward `resets_at` timestamp
  are quarantined until confirmed, preventing false quota-reset alerts when the
  next poll recovers.

## [1.0.3] - 2026-07-11

### Added

- **GPT-5.6 pricing** — Sol, Terra, Luna, and alias pricing with context tiers
  and dated Codex fallback behavior. The usage UI now distinguishes cache
  writes and presents missing GPT-5.6 cache-write telemetry as an unreported
  lower-bound cost.
- **Development launcher** — a workspace-backed macOS development launcher,
  documented in CONTRIBUTING.

### Fixed

- **Desktop** — the embedded console no longer shows light legacy scrollbars on
  macOS 26 (Tahoe): release builds link the current macOS SDK, and the console
  declares its color scheme so natively painted controls follow the theme.

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

[Unreleased]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.10...HEAD
[1.0.10]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.9...v1.0.10
[1.0.9]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.8...v1.0.9
[1.0.8]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.7...v1.0.8
[1.0.7]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.6...v1.0.7
[1.0.6]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/superposed-labs/fluxion-bus/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/superposed-labs/fluxion-bus/releases/tag/v1.0.0
