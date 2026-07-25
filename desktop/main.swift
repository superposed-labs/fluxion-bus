import AppKit
import Foundation
import Sparkle
import UserNotifications

// MARK: - Models
struct UsageCache: Codable {
    let enabled: Bool
    let providers: [ProviderUsage]
    let locale: String
    let generatedAt: String
    /// Last snapshots of currently-disabled providers, carried forward by the
    /// collector so re-enabling one renders its old numbers instantly instead
    /// of a loading card. Absent in caches written before this key existed.
    let retiredProviders: [ProviderUsage]?

    enum CodingKeys: String, CodingKey {
        case enabled, providers, locale
        case generatedAt = "generated_at"
        case retiredProviders = "_retired_providers"
    }
}

struct ResetCredits: Codable, Equatable {
    let count: Int
    let expiries: [Double]
}

struct ProviderUsage: Codable, Equatable {
    let provider: String
    let status: String
    let accountLabel: String?
    let windows: [QuotaWindow]
    let fetchedAt: String?
    let detail: String?
    let resets: ResetCredits?

    enum CodingKeys: String, CodingKey {
        case provider, status, windows, detail, resets
        case accountLabel = "account_label"
        case fetchedAt = "fetched_at"
    }
}

struct QuotaWindow: Codable, Equatable {
    let key: String?
    let label: String?
    let usedPercent: Double?
    let resetsAt: String?
    let windowMinutes: Int?
    let remaining: Double?
    let total: Double?
    let currency: String?
    let expiresAt: String?
    let enabled: Bool?

    enum CodingKeys: String, CodingKey {
        case key, label, remaining, total, currency, enabled
        case usedPercent = "used_percent"
        case resetsAt = "resets_at"
        case windowMinutes = "window_minutes"
        case expiresAt = "expires_at"
    }

    /// Model-scoped sub-limit (e.g. Claude's Fable weekly cap). The "scoped_"
    /// key prefix is the probe's contract for windows that subdivide an
    /// account-wide window: they only block one model, so they never drive
    /// provider-level aggregates (menu-bar headline, notch 5h/weekly picks).
    var isScoped: Bool {
        (key ?? "").hasPrefix("scoped_")
    }
}

/// OpenAI currently omits Codex's 5-hour window while continuing to return a
/// healthy weekly window. Keep this as presentation-only state: synthesizing a
/// quota window would make schedulers and reset notifications treat it as real.
func isCodexFiveHourTemporarilyUncapped(_ provider: ProviderUsage) -> Bool {
    guard provider.provider.lowercased() == "codex", provider.status == "ok" else {
        return false
    }
    let hasFiveHour = provider.windows.contains { window in
        let hay = "\((window.key ?? "").lowercased()) \((window.label ?? "").lowercased())"
        return window.windowMinutes == 300 || hay.contains("5h") || hay.contains("5-hour")
    }
    let hasWeekly = provider.windows.contains { window in
        let hay = "\((window.key ?? "").lowercased()) \((window.label ?? "").lowercased())"
        return window.windowMinutes == 10080 || hay.contains("7d") || hay.contains("week")
    }
    return hasWeekly && !hasFiveHour
}

struct AvailabilityEntry: Codable {
    let status: String
    let detail: String
    let path: String?
}

struct AvailabilitySnapshot: Codable {
    let generatedAt: String
    let executors: [String: AvailabilityEntry]
    let usage: [String: AvailabilityEntry]

    enum CodingKeys: String, CodingKey {
        case executors, usage
        case generatedAt = "generated_at"
    }
}

struct DesktopConfig: Codable {
    let repositoryPath: String
}

struct AutoPingResponse: Codable {
    let providers: [String: String]
}

// MARK: - Provider Metadata
let PROVIDER_NAMES = ["claude": "Claude", "codex": "Codex", "antigravity": "Antigravity"]

/// Brand color for a provider, rendered as a solid dot. Single source of truth
/// shared by the menu-bar title and the dropdown so the two never drift apart.
struct ProviderVisual {
    let brandColor: NSColor
}

func providerVisual(for provider: String) -> ProviderVisual {
    switch provider {
    case "claude":
        return ProviderVisual(brandColor: NSColor(hex: "#D97756"))
    case "codex":
        return ProviderVisual(brandColor: NSColor(hex: "#8B5CF6"))
    case "antigravity":
        return ProviderVisual(brandColor: NSColor(hex: "#3B82F6"))
    default:
        return ProviderVisual(brandColor: NSColor.labelColor)
    }
}

// MARK: - Usage Color Thresholds
enum UsageThreshold {
    static let high: Double = 85
    static let medium: Double = 50
}

/// Color for a "used percent" value: red when nearly exhausted, blue mid-range,
/// neutral otherwise. `nil` (unknown usage) renders neutral.
func usageColor(used: Double?) -> NSColor {
    guard let used = used else { return NSColor.labelColor }
    if used > UsageThreshold.high { return NSColor.systemRed }
    if used > UsageThreshold.medium { return NSColor.systemBlue }
    return NSColor.labelColor
}

// MARK: - Polling & Refresh Constants
enum PollingConfig {
    static let defaultInterval: TimeInterval = 60.0
    static let inactiveInterval: TimeInterval = 600.0
    static let maxInterval: TimeInterval = 600.0
}

// MARK: - AppDelegate
class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, UNUserNotificationCenterDelegate {
    var statusItem: NSStatusItem!
    var statusMenu: NSMenu!
    // In-app updater (Sparkle, wrapped). Created in applicationDidFinishLaunching.
    // Inert in dev / self-compiled builds; active only in official distribution
    // builds. Backs the "Check for Updates" action and the auto-check toggle.
    var updaterController: UpdaterController!
    var richMenuWindow: NSPanel?
    var richMenuEventMonitor: Any?
    var lastRichMenuCloseTime: TimeInterval = 0
    var notchWindowController: NotchWindowController?
    var envVals: [String: String] = [:]
    var autoPingModes: [String: String] = [
        "claude": "off",
        "codex": "off",
        "antigravity": "off",
    ]
    // Assigned only by resolveRepositoryPath() (AppDelegate+Repository.swift).
    // Internal (not `private(set)`) so that resolver, now in a separate file,
    // can write it — Swift's `private` is file-scoped.
    var repoPath: String = ""
    var isUpgradingBackend: Bool = false
    /// Latest bootstrap output line during a silent upgrade, surfaced under the
    /// "updating components" dropdown entry so a long reinstall visibly makes
    /// progress instead of sitting on a static label.
    var upgradeStatusLine: String = ""
    /// The live "updating components" menu item, updated in place on each
    /// progress line — an open NSMenu tracks title changes, and
    /// rebuildDropdown only runs when the menu (re)opens. Weak: the menu owns it.
    weak var upgradeMenuItem: NSMenuItem?

    /// Latest decoded providers held in memory. This is the single source of
    /// truth for rendering — disk is only read when the cache file changes.
    var currentProviders: [ProviderUsage] = []
    /// Last snapshots of disabled providers (cache `_retired_providers`), used
    /// as an instant stand-in when a provider is re-enabled.
    var retiredProviders: [ProviderUsage] = []
    var richTodayStats: [String: ProviderHistoryStats] = [:]
    var richHistoryFetchInFlight: Bool = false
    var richHistoryRetryCount: Int = 0
    var richHistoryMessage: String?

    // Loading spinner. The timer only redraws the title string; it never
    // touches disk and only runs while at least one provider is still loading.
    var spinnerTimer: Timer?
    var spinnerIndex = 0
    let spinnerFrames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    // Dynamic background polling based on screen state and activity.
    var monitorTimer: Timer?
    var isScreenActive: Bool = true
    var terminationSignalSources: [DispatchSourceSignal] = []
    var didPrepareForTermination = false

    // Hybrid refresh state.
    var lastClaudeHistoryMtime: Date?
    var lastCodexSessionsMtime: Date?
    var lastClaudeCodeSessionsMtime: Date?
    var lastLocalAgentModeSessionsMtime: Date?
    var lastRefreshTime: Date = Date(timeIntervalSince1970: 0)
    var currentInterval: TimeInterval = PollingConfig.defaultInterval
    var isPendingRefresh: Bool = false
    var pendingForceProviders: Set<String> = []
    var lastCachedProviders: [ProviderUsage] = []
    // Multiple refresh triggers can overlap while their collector processes
    // serialize on the shared cache lock. A count, rather than a Bool, keeps the
    // panel-triggered Codex refresh deferred until every active process exits.
    var usageProbesInFlight = 0
    var codexPanelRefreshPending = false

    // Cache file watcher (event-driven updates instead of constant polling).
    // Not `private`: the watcher lives in AppDelegate+Polling.swift.
    var cacheSource: DispatchSourceFileSystemObject?
    var notificationSource: DispatchSourceFileSystemObject?
    // Set while a notification click is being handled so the reopen event
    // that accompanies app activation does not also open the console window.
    var suppressReopenUntil = Date.distantPast
    let watcherQueue = DispatchQueue(label: "com.fluxion.menu.cachewatcher")

    var claudeHistoryPath: String {
        return NSString(string: "~/.claude/history.jsonl").expandingTildeInPath
    }
    var codexSessionsPath: String {
        return NSString(string: "~/.codex/sessions").expandingTildeInPath
    }
    var claudeCodeSessionsPath: String {
        return NSString(string: "~/Library/Application Support/Claude/claude-code-sessions").expandingTildeInPath
    }
    var localAgentModeSessionsPath: String {
        return NSString(string: "~/Library/Application Support/Claude/local-agent-mode-sessions").expandingTildeInPath
    }

    // Paths
    var applicationSupportDir: URL {
        let base = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first!
        return base.appendingPathComponent("Fluxion", isDirectory: true)
    }

    var desktopConfigPath: URL {
        return applicationSupportDir.appendingPathComponent("config.json")
    }

    // Not `private`: read by resolveRepositoryPath() in AppDelegate+Repository.swift.
    var bundledRepoCandidate: String {
        let bundleURL = Bundle.main.bundleURL
        if bundleURL.pathExtension == "app" {
            // bundleURL is .../Fluxion/desktop/Fluxion.app
            // Go 2 levels up to reach .../Fluxion
            return bundleURL.deletingLastPathComponent().deletingLastPathComponent().path
        }
        let currentFile = URL(fileURLWithPath: #file)
        return currentFile.deletingLastPathComponent().deletingLastPathComponent().path
    }

    var envPath: String {
        return (repoPath as NSString).appendingPathComponent(".env")
    }

    var dataDirPath: String {
        let dir = envVals["FLUXION_DATA_DIR"] ?? "data"
        if (dir as NSString).isAbsolutePath {
            return dir
        }
        return (repoPath as NSString).appendingPathComponent(dir)
    }

    var cachePath: String {
        return (dataDirPath as NSString).appendingPathComponent("usage_cache.json")
    }

    var availabilityPath: String {
        return (dataDirPath as NSString).appendingPathComponent("availability.json")
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        registerNotificationCategories()
        installTerminationSignalHandlers()

        guard resolveRepositoryPath() else {
            NSApp.terminate(nil)
            return
        }
        NSLog("FluxionMenu starting up. Repo path: %@", repoPath)
        NSLog("FluxionMenu cache path: %@", cachePath)

        // Install a standard main menu. As an LSUIElement accessory app we have
        // no nib-provided menu, so without this NSApp.mainMenu is nil — and the
        // Edit menu is what carries the Cmd+C/X/V/A/Z key equivalents that route
        // the matching actions (paste:, copy:, …) down the responder chain to the
        // focused text field's field editor. Missing it is why pasting into the
        // Preferences inputs with Cmd+V did nothing while typing still worked.
        setupMainMenu()

        // Start the updater. It stays inert unless this build has an SUFeedURL
        // (official distribution builds only). It never installs silently, and
        // scheduled reminders are gentle (a notification, not a modal).
        updaterController = UpdaterController()
        updaterController.onPendingUpdateChange = { [weak self] in
            DispatchQueue.main.async {
                self?.syncPendingUpdateUI()
            }
        }

        loadEnv()
        reloadCacheFromDisk()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.autosaveName = NSStatusItem.AutosaveName("FluxionStatusItem")
        let menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false
        statusMenu = menu
        statusItem.menu = menu
        
        if envVals["FLUXION_NOTCH_MODE"] == "true" {
            notchWindowController = NotchWindowController()
            notchWindowController?.show()
        }

        render()
        let backendReady = showSetupWarningIfNeeded()
        guard backendReady else {
            NSLog("FluxionMenu: backend setup pending; waiting for user action.")
            return
        }

        startRuntimeAfterBackendReady(onboarding: .showWindow)
    }

    /// How first-launch onboarding is presented once detection finishes.
    enum OnboardingPresentation {
        /// Open the welcome window (normal launch with a ready backend).
        case showWindow
        /// The welcome window is already open in backend-setup mode; swap it
        /// to the onboarding content in place.
        case advanceSetupWindow
        /// Do not surface onboarding (the user closed the setup window).
        case suppress
    }

    /// Everything the app runs once a valid backend exists: cache seeds and
    /// watchers, availability detection, service startup, and first-launch
    /// onboarding. Called at launch when the backend is already installed,
    /// and again after an in-app backend install completes.
    func startRuntimeAfterBackendReady(onboarding: OnboardingPresentation) {
        if isSilentUpgradeNeeded() {
            isUpgradingBackend = true
            let cached = filteredProviders()
            notchWindowController?.model.providers = cached
            notchWindowController?.setUpgradingBackend(true)
            if let menu = statusMenu {
                rebuildDropdown(menu)
            }
            render()
            NSLog("FluxionMenu: Starting silent backend upgrade...")
            // Services that survived a crashed session would keep executing the
            // old (deleted) source tree after the swap, and the pgrep-based
            // autostart would then skip them — stop them first; the normal
            // post-upgrade startup below brings them back on the new code.
            let patterns = serviceProcessPatterns
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                self?.stopServices(patterns: patterns)
                DispatchQueue.main.async { [weak self] in
                    self?.bootstrapBackend(progress: { [weak self] line in
                        self?.updateUpgradeProgress(line)
                    }) { [weak self] ok, output in
                        guard let self = self else { return }
                        self.isUpgradingBackend = false
                        self.upgradeStatusLine = ""
                        self.notchWindowController?.setUpgradingBackend(false)
                        if ok {
                            NSLog("FluxionMenu: Silent backend upgrade completed successfully.")
                            self.loadEnv()
                            if let menu = self.statusMenu {
                                self.rebuildDropdown(menu)
                            }
                            self.startRuntimeAfterBackendReady(onboarding: .suppress)
                        } else {
                            NSLog("FluxionMenu: Silent backend upgrade failed: %@", output)
                            WelcomeWindow.shared.showBackendSetup()
                            WelcomeWindow.shared.showSetupFailure(output)
                        }
                    }
                }
            }
            return
        }

        // Seed initial cached data and local file timestamps.
        seedInitialCachedProviders()
        seedActivityTimestamps()
        startCacheWatcher()
        startNotificationWatcher()
        setupScreenNotifications()
        resetBackgroundTimer()

        let needsInitialization = envVals["FLUXION_USAGE_PROVIDERS"] == nil
            || envVals["FLUXION_ENABLED_EXECUTORS"] == nil
            || envVals["FLUXION_DEFAULT_EXECUTOR"] == nil
        runAvailabilityDetection(initialize: needsInitialization) { [weak self] in
            guard let self = self else { return }
            self.loadEnv()
            if needsInitialization {
                // Fresh install: walk the user through the optional setup
                // (Keychain access, weekly reset alerts, display style).
                switch onboarding {
                case .showWindow:
                    WelcomeWindow.shared.show()
                case .advanceSetupWindow:
                    WelcomeWindow.shared.advanceToOnboarding()
                case .suppress:
                    break
                }
                // The welcome window is asking about these providers right now,
                // so record them as seen. Without this baseline they would all
                // look new on the next launch.
                self.seedKnownWatchableProviders()
            } else {
                if case .advanceSetupWindow = onboarding {
                    // Preserved configuration from an earlier install: nothing
                    // to onboard, so just dismiss the setup window.
                    WelcomeWindow.shared.finishSetupWithoutOnboarding()
                }
                // Not a fresh install, so an agent may have appeared since the
                // last launch with no reminder rule of its own. Skipped during
                // onboarding, where the welcome window covers this itself.
                self.promptForUnwatchedProvidersIfNeeded()
            }
            DispatchQueue.global(qos: .userInitiated).async {
                // Sweep strays from a different checkout before starting ours,
                // so a stale gateway/web (e.g. after switching repositories)
                // can't keep stealing bot messages or holding the UI port.
                self.terminateForeignServices()
                self.startServicesIfNeeded()
                DispatchQueue.main.async {
                    self.refresh(force: false)
                    self.fetchRichHistory()
                }
            }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if Date() < suppressReopenUntil {
            return true
        }
        openConsole()
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        prepareForTermination()
    }

    func installTerminationSignalHandlers() {
        guard terminationSignalSources.isEmpty else { return }

        for signalNumber in [SIGTERM, SIGINT] {
            signal(signalNumber, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
            source.setEventHandler { [weak self] in
                self?.prepareForTermination()
                NSApp.terminate(nil)
            }
            source.resume()
            terminationSignalSources.append(source)
        }
    }

    func prepareForTermination() {
        guard !didPrepareForTermination else { return }
        didPrepareForTermination = true

        stopNotificationWatcher()
        stopCacheWatcher()
        spinnerTimer?.invalidate()
        spinnerTimer = nil
        monitorTimer?.invalidate()
        monitorTimer = nil
        if let statusItem = statusItem {
            statusItem.isVisible = false
            NSStatusBar.system.removeStatusItem(statusItem)
        }
        for pattern in serviceProcessPatterns {
            signalProcesses(pattern: pattern, signal: nil)
        }
    }

    // MARK: - Environment Configuration
    func loadEnv() {
        var vals: [String: String] = [:]
        let path = envPath
        if FileManager.default.fileExists(atPath: path),
           let content = try? String(contentsOfFile: path, encoding: .utf8) {
            for line in content.components(separatedBy: .newlines) {
                let raw = line.trimmingCharacters(in: .whitespacesAndNewlines)
                if raw.isEmpty || raw.hasPrefix("#") || !raw.contains("=") {
                    continue
                }
                let parts = raw.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
                if parts.count == 2 {
                    let k = parts[0].trimmingCharacters(in: .whitespaces)
                    let v = parts[1].trimmingCharacters(in: .whitespaces)
                        .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
                    vals[k] = v
                }
            }
        }
        self.envVals = vals
    }

    func saveEnv(updates: [String: String]) {
        let path = envPath
        var lines: [String] = []
        var remainingUpdates = updates

        if FileManager.default.fileExists(atPath: path),
           let content = try? String(contentsOfFile: path, encoding: .utf8) {
            for line in content.components(separatedBy: .newlines) {
                let raw = line.trimmingCharacters(in: .whitespacesAndNewlines)
                if !raw.isEmpty && !raw.hasPrefix("#") && raw.contains("=") {
                    let parts = raw.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
                    let k = parts[0].trimmingCharacters(in: .whitespaces)
                    if let newVal = remainingUpdates[k] {
                        lines.append("\(k)=\(newVal)")
                        remainingUpdates.removeValue(forKey: k)
                        continue
                    }
                }
                lines.append(line)
            }
        }

        for (k, v) in remainingUpdates {
            lines.append("\(k)=\(v)")
        }

        let newContent = lines.joined(separator: "\n")
        try? newContent.write(toFile: path, atomically: true, encoding: .utf8)
        loadEnv()

        // The scheduler hot-reloads .env on its own (picks up edits within a
        // tick), so saving settings only needs to bounce the web and gateway
        // services, which read their config at startup. This avoids needlessly
        // restarting the scheduler — and the brief gap that came with it — on
        // every settings change.
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            self.restartServices(patterns: [
                self.servicePattern("fluxion-web"),
                self.servicePattern("fluxion-gateway"),
            ])
        }
    }

    @discardableResult
    func runAutoPingCommand(_ arguments: [String]) -> [String: String]? {
        let schedulerBin = (repoPath as NSString).appendingPathComponent(".venv/bin/fluxion-scheduler")
        guard FileManager.default.fileExists(atPath: schedulerBin) else {
            NSLog("FluxionMenu: auto-ping command unavailable at %@", schedulerBin)
            return nil
        }

        let task = Process()
        let output = Pipe()
        task.executableURL = URL(fileURLWithPath: schedulerBin)
        task.arguments = arguments
        task.currentDirectoryURL = URL(fileURLWithPath: repoPath)
        task.standardOutput = output
        task.standardError = output
        var env = ProcessInfo.processInfo.environment
        env["FLUXION_ENV_FILE"] = envPath
        task.environment = env

        do {
            try task.run()
            task.waitUntilExit()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            guard task.terminationStatus == 0,
                  let response = try? JSONDecoder().decode(AutoPingResponse.self, from: data) else {
                let detail = String(data: data, encoding: .utf8) ?? ""
                NSLog("FluxionMenu: auto-ping command failed: %@", detail)
                return nil
            }
            autoPingModes = response.providers
            return response.providers
        } catch {
            NSLog("FluxionMenu: failed to run auto-ping command: %@", error.localizedDescription)
            return nil
        }
    }

    func loadAutoPingModes() {
        _ = runAutoPingCommand(["--get-autoping"])
    }

    func setAutoPingMode(provider: String, mode: String) -> Bool {
        return runAutoPingCommand(["--set-autoping", provider, mode]) != nil
    }

    func loadAvailabilitySnapshot() -> AvailabilitySnapshot? {
        guard FileManager.default.fileExists(atPath: availabilityPath),
              let data = try? Data(contentsOf: URL(fileURLWithPath: availabilityPath)) else {
            return nil
        }
        return try? JSONDecoder().decode(AvailabilitySnapshot.self, from: data)
    }

    func runAvailabilityDetection(initialize: Bool, completion: (() -> Void)? = nil) {
        let pythonBin = (repoPath as NSString).appendingPathComponent(".venv/bin/python")
        guard FileManager.default.fileExists(atPath: pythonBin) else {
            NSLog("FluxionMenu: project Python missing at %@", pythonBin)
            DispatchQueue.main.async { completion?() }
            return
        }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        task.arguments = initialize
            ? [pythonBin, "-m", "fluxion.detect_cli", "--initialize"]
            : [pythonBin, "-m", "fluxion.detect_cli"]
        task.currentDirectoryURL = URL(fileURLWithPath: repoPath)
        var env = ProcessInfo.processInfo.environment
        env["FLUXION_ENV_FILE"] = envPath
        task.environment = env
        task.standardOutput = Pipe()
        task.standardError = Pipe()
        task.terminationHandler = { proc in
            if proc.terminationStatus != 0 {
                NSLog("FluxionMenu: fluxion-detect exited with status %d", proc.terminationStatus)
            }
            DispatchQueue.main.async { completion?() }
        }
        do {
            try task.run()
        } catch {
            NSLog("FluxionMenu: failed to launch fluxion-detect: %@", error.localizedDescription)
            DispatchQueue.main.async { completion?() }
        }
    }

    // MARK: - Cache & Rendering
    /// Reloads the in-memory providers from disk. Returns whether a valid cache
    /// was decoded.
    @discardableResult
    func reloadCacheFromDisk() -> Bool {
        let path = cachePath
        guard FileManager.default.fileExists(atPath: path),
              let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let cache = try? JSONDecoder().decode(UsageCache.self, from: data) else {
            return false
        }
        currentProviders = cache.providers
        retiredProviders = cache.retiredProviders ?? []
        return true
    }

    /// Active provider keys from settings, in display order.
    func activeProviderKeys() -> [String] {
        let activeStr = envVals["FLUXION_USAGE_PROVIDERS"] ?? "claude,codex,antigravity"
        return activeStr.components(separatedBy: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
    }

    /// The active providers resolved against the cache. An active provider
    /// missing from the cache first falls back to its retired snapshot — the
    /// last data captured before it was disabled — so re-enabling renders real
    /// numbers instantly while the probe refreshes them; only a provider with
    /// no usable history gets the synthetic "loading" entry.
    func filteredProviders() -> [ProviderUsage] {
        var filtered: [ProviderUsage] = []
        for prov in activeProviderKeys() {
            if let existing = currentProviders.first(where: { $0.provider.lowercased() == prov }) {
                filtered.append(existing)
            } else if let retired = retiredProviders.first(where: { $0.provider.lowercased() == prov }),
                      isRecentEnough(retired.fetchedAt) {
                filtered.append(retired)
            } else {
                filtered.append(ProviderUsage(
                    provider: prov,
                    status: "loading",
                    accountLabel: nil,
                    windows: [],
                    fetchedAt: nil,
                    detail: L10n.tr("menu.loading_quota"),
                    resets: nil
                ))
            }
        }
        return filtered
    }

    /// A retired snapshot older than a day is worse than a loading card: its
    /// windows have all reset since, so the numbers it shows are fiction.
    func isRecentEnough(_ fetchedAt: String?, maxAge: TimeInterval = 24 * 3600) -> Bool {
        guard let fetchedAt = fetchedAt else { return false }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let fallback = ISO8601DateFormatter()
        fallback.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: fetchedAt) ?? fallback.date(from: fetchedAt) else {
            return false
        }
        return Date().timeIntervalSince(date) < maxAge
    }

    // MARK: - Actions
    @objc func triggerRefresh() {
        refresh(force: true)
    }

    /// Refresh Codex quota when opening its expanded panel only if the cached
    /// snapshot cannot answer the reset-credit row or has aged past the normal
    /// refresh interval. If another probe is running, defer the decision until
    /// it finishes instead of launching overlapping collector processes.
    func refreshCodexQuotaForPanelIfNeeded() {
        guard activeProviderKeys().contains("codex") else { return }
        let codex = currentProviders.first { $0.provider.lowercased() == "codex" }
        let refreshAge = TimeInterval(max(30, Int(envVals["FLUXION_USAGE_REFRESH_SEC"] ?? "") ?? 60))
        let missingResets = codex?.resets == nil
        let stale = !isRecentEnough(codex?.fetchedAt, maxAge: refreshAge)
        guard codex == nil || missingResets || stale else { return }

        if usageProbesInFlight > 0 {
            codexPanelRefreshPending = true
            return
        }
        codexPanelRefreshPending = false
        refresh(force: false, forceProviders: ["codex"])
    }

    func refresh(force: Bool, forceProviders: [String] = []) {
        runUsageProbe(force: force, forceProviders: forceProviders)
        // Render immediately from the current cache + latest settings so the UI
        // feels responsive; the probe's completion handler updates it again.
        render()
    }

    /// Runs `fluxion-usage`, then reloads + re-renders when it actually exits —
    /// no fixed-delay guesswork.
    func runUsageProbe(force: Bool, forceProviders: [String] = []) {
        let usageBin = (repoPath as NSString).appendingPathComponent(".venv/bin/fluxion-usage")
        guard FileManager.default.fileExists(atPath: usageBin) else {
            NSLog("FluxionMenu: usage binary missing at %@", usageBin)
            return
        }
        var args = [usageBin]
        if force {
            args.append("--force")
        } else if !forceProviders.isEmpty {
            for provider in forceProviders {
                args.append(contentsOf: ["--force-provider", provider])
            }
        }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        task.arguments = args
        task.currentDirectoryURL = URL(fileURLWithPath: repoPath)
        var env = ProcessInfo.processInfo.environment
        env["FLUXION_ENV_FILE"] = envPath
        task.environment = env
        let errPipe = Pipe()
        task.standardOutput = Pipe()
        task.standardError = errPipe
        usageProbesInFlight += 1
        task.terminationHandler = { [weak self] proc in
            let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
            let errStr = String(data: errData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.usageProbesInFlight = max(0, self.usageProbesInFlight - 1)
                if proc.terminationStatus != 0 {
                    NSLog("FluxionMenu: fluxion-usage exited with status %d. Error: %@", proc.terminationStatus, errStr)
                }
                self.reloadCacheFromDisk()
                self.render()
                // The cache file may have just been created — attach the watcher.
                self.startCacheWatcher()
                if self.usageProbesInFlight == 0, self.codexPanelRefreshPending {
                    self.codexPanelRefreshPending = false
                    self.refreshCodexQuotaForPanelIfNeeded()
                }
            }
        }
        do {
            try task.run()
        } catch {
            usageProbesInFlight = max(0, usageProbesInFlight - 1)
            NSLog("FluxionMenu: failed to launch fluxion-usage: %@", error.localizedDescription)
        }
    }

    /// Builds the application's main menu. Only the App and Edit menus are
    /// needed: the App menu carries Cmd+Q, and the Edit menu's standard items
    /// supply the Cmd+Z/X/C/V/A key equivalents. Every editing item targets nil
    /// so AppKit dispatches its action (undo:, paste:, …) through the responder
    /// chain to whatever is first responder — the focused text field's editor.
    func setupMainMenu() {
        let mainMenu = NSMenu()

        // App menu (its title is replaced by the process name by AppKit).
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenuItem.submenu = appMenu
        appMenu.addItem(
            withTitle: L10n.tr("app.quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"
        )

        // Edit menu.
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: L10n.tr("edit.menu"))
        editMenuItem.submenu = editMenu

        editMenu.addItem(withTitle: L10n.tr("edit.undo"), action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: L10n.tr("edit.redo"), action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: L10n.tr("edit.cut"), action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: L10n.tr("edit.copy"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: L10n.tr("edit.paste"), action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: L10n.tr("edit.delete"), action: #selector(NSText.delete(_:)), keyEquivalent: "")
        editMenu.addItem(
            withTitle: L10n.tr("edit.select_all"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"
        )

        NSApp.mainMenu = mainMenu
    }

    @objc func quitApp() {
        NSApp.terminate(nil)
    }

    @objc func openPreferences() {
        PreferencesWindow.shared.show()
    }

    @objc func openConsole() {
        MainWindow.shared.show()
    }
}

// MARK: - macOS Native Notifications
struct PendingNotification: Codable {
    let title: String
    let body: String
    let timestamp: String
    // Optional pending-user metadata: the channel pending stores write these
    // so the notification can deep-link into Preferences and offer an
    // "Allow" action. Absent on scheduler notifications.
    let channel: String?
    let userId: String?

    enum CodingKeys: String, CodingKey {
        case title
        case body
        case timestamp
        case channel
        case userId = "user_id"
    }
}

extension AppDelegate {
    static let pendingUserCategoryId = "FLUXION_PENDING_USER"
    static let pendingAllowActionId = "FLUXION_PENDING_ALLOW"

    /// Declare the pending-user category so its notifications carry an
    /// "Allow" button (revealed on hover/long-press of the banner).
    func registerNotificationCategories() {
        let allow = UNNotificationAction(
            identifier: AppDelegate.pendingAllowActionId,
            title: L10n.tr("preferences.pending.allow"),
            options: [.authenticationRequired]
        )
        let pendingUser = UNNotificationCategory(
            identifier: AppDelegate.pendingUserCategoryId,
            actions: [allow],
            intentIdentifiers: [],
            options: []
        )
        let enableReminders = UNNotificationAction(
            identifier: AppDelegate.reminderPromptEnableActionId,
            title: L10n.tr("notification.reminder_coverage.enable"),
            options: []
        )
        let reminderCoverage = UNNotificationCategory(
            identifier: AppDelegate.reminderPromptCategoryId,
            actions: [enableReminders],
            intentIdentifiers: [],
            options: []
        )
        let viewUpdate = UNNotificationAction(
            identifier: UpdaterController.notificationActionIdentifier,
            title: L10n.tr("update.available.action"),
            options: [.foreground]
        )
        let updateAvailable = UNNotificationCategory(
            identifier: UpdaterController.notificationCategoryIdentifier,
            actions: [viewUpdate],
            intentIdentifiers: [],
            options: []
        )
        UNUserNotificationCenter.current().setNotificationCategories([
            pendingUser, reminderCoverage, updateAvailable,
        ])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        if #available(macOS 11.0, *) {
            completionHandler([.banner, .list, .sound, .badge])
        } else {
            completionHandler([.alert, .sound, .badge])
        }
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        suppressReopenUntil = Date().addingTimeInterval(2)
        let info = response.notification.request.content.userInfo
        let channel = (info["channel"] as? String).flatMap(PendingUserChannel.init(key:))
        let userId = (info["user_id"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let reminderProviders = info["reminder_providers"] as? [String] ?? []
        let actionIdentifier = response.actionIdentifier
        let isUpdateNotification =
            (info[UpdaterController.notificationUserInfoKey] as? Bool == true)
            || response.notification.request.content.categoryIdentifier
                == UpdaterController.notificationCategoryIdentifier

        DispatchQueue.main.async { [weak self] in
            defer { completionHandler() }
            guard let self = self else { return }
            if isUpdateNotification {
                if actionIdentifier == UNNotificationDefaultActionIdentifier
                    || actionIdentifier == UpdaterController.notificationActionIdentifier {
                    self.presentAvailableUpdate()
                }
                return
            }
            if !reminderProviders.isEmpty {
                switch actionIdentifier {
                case AppDelegate.reminderPromptEnableActionId:
                    self.enableWeeklyReminders(for: reminderProviders)
                case UNNotificationDefaultActionIdentifier:
                    // Clicking the banner itself opens the page that owns the
                    // setting, so the offer is never a dead end.
                    PreferencesWindow.shared.show()
                    PreferencesWindow.shared.switchPage(to: "automation")
                default:
                    break
                }
                return
            }
            guard let channel = channel, !userId.isEmpty else {
                // Not a pending-user notification: keep the pre-existing
                // behavior of a click opening the console window.
                if actionIdentifier == UNNotificationDefaultActionIdentifier {
                    self.openConsole()
                }
                return
            }
            switch actionIdentifier {
            case AppDelegate.pendingAllowActionId:
                PreferencesWindow.shared.approvePendingUser(userId, channel: channel)
            case UNNotificationDefaultActionIdentifier:
                PreferencesWindow.shared.showMessagingChannel(channel)
            default:
                break
            }
        }
    }

    /// Request notification permission if the user has not decided yet.
    /// Called when a feature that notifies is enabled (Welcome window, first
    /// delivery) rather than at launch, so the system prompt has context.
    func ensureNotificationPermission(completion: (() -> Void)? = nil) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
            if let error = error {
                NSLog("FluxionMenu: notification auth error: %@", error.localizedDescription)
            } else {
                NSLog("FluxionMenu: notification auth granted: %{public}@", granted ? "true" : "false")
            }
            completion?()
        }
    }

    /// Deliver a local notification through macOS Notification Center.
    func deliverLocalNotification(
        title: String,
        body: String,
        userInfo: [AnyHashable: Any] = [:],
        categoryIdentifier: String? = nil
    ) {
        ensureNotificationPermission {
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            if !userInfo.isEmpty {
                content.userInfo = userInfo
            }
            if let categoryIdentifier = categoryIdentifier {
                content.categoryIdentifier = categoryIdentifier
            }

            let request = UNNotificationRequest(
                identifier: UUID().uuidString,
                content: content,
                trigger: nil  // deliver immediately
            )
            UNUserNotificationCenter.current().add(request) { error in
                if let error = error {
                    NSLog("FluxionMenu: failed to deliver notification: %@", error.localizedDescription)
                }
            }
        }
    }

    var macOSNotificationsPath: String {
        (dataDirPath as NSString).appendingPathComponent("macos_notifications.jsonl")
    }

    func startNotificationWatcher() {
        guard notificationSource == nil else { return }
        let path = macOSNotificationsPath
        let dir = (path as NSString).deletingLastPathComponent
        try? FileManager.default.createDirectory(
            atPath: dir,
            withIntermediateDirectories: true
        )
        if !FileManager.default.fileExists(atPath: path) {
            try? "".write(toFile: path, atomically: true, encoding: .utf8)
        }

        let fd = open(path, O_EVTONLY)
        guard fd >= 0 else {
            NSLog("FluxionMenu: failed to open notification signal for watching: %@", path)
            return
        }
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd,
            eventMask: [.write, .extend, .delete, .rename],
            queue: watcherQueue
        )
        source.setEventHandler { [weak self] in
            guard let self = self else { return }
            let flags = source.data
            if flags.contains(.delete) || flags.contains(.rename) {
                DispatchQueue.main.async {
                    self.stopNotificationWatcher()
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                        self.startNotificationWatcher()
                        self.checkAndDeliverPendingNotifications()
                    }
                }
                return
            }
            DispatchQueue.main.async {
                self.checkAndDeliverPendingNotifications()
            }
        }
        source.setCancelHandler {
            close(fd)
        }
        notificationSource = source
        source.resume()
        NSLog("FluxionMenu: watching notification signal %@", path)
        checkAndDeliverPendingNotifications()
    }

    func stopNotificationWatcher() {
        notificationSource?.cancel()
        notificationSource = nil
    }

    /// Read and deliver any pending notifications written by the scheduler
    /// daemon to `data/macos_notifications.jsonl`, then truncate the file.
    func checkAndDeliverPendingNotifications() {
        guard (envVals["FLUXION_MENU_MACOS_NOTIFY_REFRESH"] ?? "true").lowercased() != "false" else {
            return
        }
        let path = macOSNotificationsPath
        guard FileManager.default.fileExists(atPath: path) else { return }
        guard let content = try? String(contentsOfFile: path, encoding: .utf8),
              !content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return
        }
        // Truncate immediately so concurrent writes from the scheduler are not lost.
        // We hold the lines in memory and deliver after truncation.
        try? "".write(toFile: path, atomically: true, encoding: .utf8)

        let decoder = JSONDecoder()
        for line in content.components(separatedBy: .newlines) {
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else { continue }
            guard let data = trimmed.data(using: .utf8),
                  let note = try? decoder.decode(PendingNotification.self, from: data) else {
                NSLog("FluxionMenu: skipping malformed notification line: %@", trimmed)
                continue
            }
            if let channelKey = note.channel, let channel = PendingUserChannel(key: channelKey),
               let userId = note.userId, !userId.isEmpty {
                // The gateway rendered the title in its own locale (usually
                // English); rebuild it here so the notification follows the
                // app language. The body is just "user id: preview" — nothing
                // to translate.
                deliverLocalNotification(
                    title: L10n.tr("notification.pending_user.title", channel.displayName),
                    body: note.body,
                    userInfo: ["channel": channelKey, "user_id": userId],
                    categoryIdentifier: AppDelegate.pendingUserCategoryId
                )
            } else {
                deliverLocalNotification(title: note.title, body: note.body)
            }
        }
    }
}

// MARK: - Main Entry Point
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
