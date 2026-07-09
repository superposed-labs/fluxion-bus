import AppKit
import Foundation
import ServiceManagement

struct ProjectItem {
    var key: String
    var workspace: String
    var executor: String
    var description: String
}

// MARK: - Preferences Window Delegate
class PreferencesWindow: NSObject, NSWindowDelegate, NSTextFieldDelegate, NSSearchFieldDelegate {
    static let shared = PreferencesWindow()

    /// User-facing version, sourced from the bundle so the build script's
    /// version is the single source of truth.
    static var appVersion: String {
        return (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "1.0"
    }

    var window: NSWindow?

    // Preferences Window Widgets
    var appearanceSegmented: NSSegmentedControl!
    var languagePopup: NSPopUpButton!
    var languageRestartNotice: NSView!
    var silentStylePopup: NSPopUpButton!
    var peekResetPopup: NSPopUpButton!
    var displayStyleSegmented: NSSegmentedControl!
    var checkClaude: NSSwitch!
    var checkCodex: NSSwitch!
    var checkAntigravity: NSSwitch!
    var checkExecutorClaude: NSSwitch!
    var checkExecutorCodex: NSSwitch!
    var checkExecutorAntigravity: NSSwitch!
    // Executors the user enabled that are not currently installed. Their toggle
    // is forced off/disabled so the settings page stays honest, but we keep the
    // preference here so autosave does not silently discard it.
    var executorsPreservedWhileUnavailable: Set<String> = []
    var defaultExecutorPopup: NSPopUpButton!
    var checkKeychain: NSSwitch!
    var checkClaudeAutoRefresh: NSSwitch!
    var checkGroupAntigravity: NSSwitch!
    var modelsEntry: NSTextField!
    var checkWeb: NSSwitch!
    var checkScheduler: NSSwitch!
    var checkSlack: NSSwitch!
    var checkLaunchAtLogin: NSSwitch!
    var checkClaudeAutoping: NSPopUpButton!
    var checkCodexAutoping: NSPopUpButton!
    var checkAntigravityAutoping: NSPopUpButton!

    // Per-provider monitor scope. Order: Disabled, 5-Hour, Weekly, Both.
    static let autoPingTitles = [
        L10n.tr("preferences.status.disabled"),
        L10n.tr("preferences.reset.5h_resets"),
        L10n.tr("preferences.notch.weekly"),
        L10n.tr("preferences.notch.both"),
    ]
    static func autoPingModeIndex(_ mode: String) -> Int {
        switch mode.lowercased() {
        case "5h": return 1
        case "7d": return 2
        case "both", "true": return 3
        default: return 0
        }
    }
    static func autoPingModeFromIndex(_ idx: Int) -> String {
        switch idx {
        case 1: return "5h"
        case 2: return "7d"
        case 3: return "both"
        default: return "off"
        }
    }

    // Popup item order for the default-executor picker.
    static let executorProviderKeys = ["claude", "codex", "antigravity"]
    static let executorDisplayNames = ["claude": "Claude", "codex": "Codex", "antigravity": "Antigravity"]

    func defaultExecutorDisplayName() -> String {
        let key = (appDelegate.envVals["FLUXION_DEFAULT_EXECUTOR"] ?? "codex").lowercased()
        return PreferencesWindow.executorDisplayNames[key] ?? "Codex"
    }
    var checkAutoPingEnabled: NSSwitch!
    var checkSlackNotifyRefresh: NSSwitch!
    var checkTelegramNotifyRefresh: NSSwitch!
    var checkQQBotNotifyRefresh: NSSwitch!
    var checkFeishuNotifyRefresh: NSSwitch!
    var checkWeChatNotifyRefresh: NSSwitch!
    var checkLineNotifyRefresh: NSSwitch!
    var slackNotifyRefreshRow: CardRow!
    var telegramNotifyRefreshRow: CardRow!
    var qqbotNotifyRefreshRow: CardRow!
    var feishuNotifyRefreshRow: CardRow!
    var weChatNotifyRefreshRow: CardRow!
    var lineNotifyRefreshRow: CardRow!
    var checkMacOSNotifyRefresh: NSSwitch!
    var checkNotifyCreditGrant: NSSwitch!
    var checkNotifyCreditExpiry: NSSwitch!
    var checkAvailabilityButton: NSButton!
    var repositoryPathLabel: NSTextField!

    // Slack Integration entries
    var checkSlackEnabled: NSSwitch!
    var slackBotTokenEntry: NSTextField!
    var slackAppTokenEntry: NSTextField!
    var slackSigningSecretEntry: NSTextField!
    var slackChannelEntry: NSTextField!
    var slackAllowedUsersEntry: NSTextField!
    var slackPendingUsersStack: NSStackView!

    // Telegram Integration entries
    var checkTelegram: NSSwitch!
    var telegramBotTokenEntry: NSTextField!
    var telegramAllowedUsersEntry: NSTextField!
    var telegramPendingUsersStack: NSStackView!
    var telegramWorkspaceEntry: NSTextField!

    // WeChat Integration entries
    var checkWeChat: NSSwitch!
    var weChatAllowedUsersEntry: NSTextField!
    var weChatPendingUsersStack: NSStackView!
    var weChatWorkspaceEntry: NSTextField!
    var weChatMessageMaxCharsEntry: NSTextField!
    var weChatTypingHeartbeatEntry: NSTextField!

    // LINE Integration entries
    var checkLine: NSSwitch!
    var lineChannelSecretEntry: NSTextField!
    var lineChannelAccessTokenEntry: NSTextField!
    var lineAllowedUsersEntry: NSTextField!
    var linePendingUsersStack: NSStackView!
    var lineWorkspaceEntry: NSTextField!

    // QQ Integration entries
    var checkQQBot: NSSwitch!
    var qqbotAppIdEntry: NSTextField!
    var qqbotSecretEntry: NSTextField!
    var qqbotTransportPopup: NSPopUpButton!
    var checkQQBotSandbox: NSSwitch!
    var checkQQBotGroupChat: NSSwitch!
    var qqbotAllowedUsersEntry: NSTextField!
    var qqbotPendingUsersStack: NSStackView!
    var qqbotWorkspaceEntry: NSTextField!

    // Feishu Integration entries
    var checkFeishu: NSSwitch!
    var feishuAppIdEntry: NSTextField!
    var feishuSecretEntry: NSTextField!
    var checkFeishuGroupChat: NSSwitch!
    var feishuAllowedUsersEntry: NSTextField!
    var feishuPendingUsersStack: NSStackView!
    var feishuWorkspaceEntry: NSTextField!

    var lastLaunchAtLoginState: NSControl.StateValue = .off
    var weChatLoginController: WeChatLoginWindowController?

    // Visibility containers for dynamic rendering
    var keychainRow: CardRow!
    var claudeAutoRefreshRow: CardRow!
    var groupAntigravityRow: CardRow!
    var modelsRow: CardRowStacked!
    var appearanceRow: CardRowStacked!
    var silentStyleRow: CardRow!
    var peekResetRow: CardRow!
    var hideOnFullscreenRow: CardRow!
    var checkHideOnFullscreen: NSSwitch!
    var apiModelsSection: NSStackView!
    
    // Sub-agent Projects widgets
    var projectsContainerStack: NSStackView!
    var addProjectButton: NSButton!
    var addRowSep: NSView!
    var projectRowViews: [ProjectRowView] = []

    // Slack visibility rows
    var slackBotTokenRow: CardRowStacked!
    var slackAppTokenRow: CardRowStacked!
    var slackSigningSecretRow: CardRowStacked!
    var slackChannelRow: CardRowStacked!
    var slackAllowedUsersRow: CardRowStacked!
    var slackPendingUsersRow: CardRowStacked!
    var slackHintRow: CardRow!

    // Telegram visibility rows
    var telegramBotTokenRow: CardRowStacked!
    var telegramAllowedUsersRow: CardRowStacked!
    var telegramPendingUsersRow: CardRowStacked!
    var telegramWorkspaceRow: CardRowStacked!
    var telegramHintRow: CardRow!

    // WeChat visibility rows
    var weChatLoginRow: CardRow!
    var weChatAllowedUsersRow: CardRowStacked!
    var weChatPendingUsersRow: CardRowStacked!
    var weChatWorkspaceRow: CardRowStacked!
    var weChatMessageMaxCharsRow: CardRowStacked!
    var weChatTypingHeartbeatRow: CardRowStacked!
    var weChatHintRow: CardRow!

    // LINE visibility rows
    var lineWebhookUrlRow: CardRow!
    var lineChannelSecretRow: CardRowStacked!
    var lineChannelAccessTokenRow: CardRowStacked!
    var lineAllowedUsersRow: CardRowStacked!
    var linePendingUsersRow: CardRowStacked!
    var lineWorkspaceRow: CardRowStacked!
    var lineHintRow: CardRow!

    // QQ visibility rows
    var qqbotAppIdRow: CardRowStacked!
    var qqbotSecretRow: CardRowStacked!
    var qqbotTransportRow: CardRowStacked!
    var qqbotSandboxRow: CardRow!
    var qqbotGroupChatRow: CardRow!
    var qqbotAllowedUsersRow: CardRowStacked!
    var qqbotPendingUsersRow: CardRowStacked!
    var qqbotWorkspaceRow: CardRowStacked!
    var qqbotHintRow: CardRow!

    // Feishu visibility rows
    var feishuAppIdRow: CardRowStacked!
    var feishuSecretRow: CardRowStacked!
    var feishuGroupChatRow: CardRow!
    var feishuAllowedUsersRow: CardRowStacked!
    var feishuPendingUsersRow: CardRowStacked!
    var feishuWorkspaceRow: CardRowStacked!
    var feishuHintRow: CardRow!

    // Messaging page gateway warning banner — hidden unless a channel is
    // enabled while the gateway isn't running. The grace deadline suppresses
    // it while a save-triggered service bounce is settling: the process is
    // legitimately down for a few seconds then, and the card would flash a
    // false alarm.
    var gatewayBannerCard: CardView!
    var gatewayActionButton: NSButton!
    var gatewayBounceGraceUntil = Date.distantPast
    // The card only appears after several consecutive down-probes (the app
    // takes a few seconds to spawn the gateway after launch, and a single
    // probe in that window would stick a false alarm), and keeps re-probing
    // while attention is needed so it hides itself once the process is back.
    var gatewayDownProbeCount = 0
    var gatewayFollowUpScheduled = false

    // Messaging page segmented control and section stacks
    var messagingSegmentedControl: NSSegmentedControl!
    var slackSectionStack: NSStackView!
    var telegramSectionStack: NSStackView!
    var lineSectionStack: NSStackView!
    var qqbotSectionStack: NSStackView!
    var feishuSectionStack: NSStackView!
    var weChatSectionStack: NSStackView!

    // Watches the data dir so pending-user rows refresh while the window is
    // open (the gateway records rejected senders at any time).
    var pendingUsersWatcher: DispatchSourceFileSystemObject?
    var pendingUsersMtimes: [String: Date] = [:]

    // Sidebar navigation and search
    var searchField: NSSearchField!
    var sidebarNavItems: [SidebarNavItem] = []
    var pageStackViews: [String: NSStackView] = [:]
    var currentPageId: String = "general"
    var settingsScrollView: NSScrollView!
    var initialAppLanguage: String = L10n.appLanguage

    // Coalesces rapid setting changes into a single forced refresh.
    private var refreshWorkItem: DispatchWorkItem?

    // Not `private`: the section builders in PreferencesWindow+Sections.swift
    // (a separate file) read this, and Swift's `private` is file-scoped.
    var appDelegate: AppDelegate {
        return NSApp.delegate as! AppDelegate
    }

    private func buildWindowIfNeeded() {
        if window != nil { return }

        sidebarNavItems.removeAll()
        pageStackViews.removeAll()
        currentPageId = "general"
        initialAppLanguage = L10n.appLanguage

        // Window creation
        let win = ClickToUnfocusWindow(
            contentRect: NSRect(x: 0, y: 0, width: 760, height: 580),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        win.title = L10n.tr("preferences.title")
        win.titlebarAppearsTransparent = true
        win.titleVisibility = .hidden
        win.backgroundColor = Palette.windowBackground
        win.isOpaque = true
        win.delegate = self
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 760, height: 580)

        // Window chrome (titlebar + split body + footer)
        _ = buildContentLayout(in: win)

        // Build each settings section into their respective page stack views
        appDelegate.loadAutoPingModes()
        let availability = appDelegate.loadAvailabilitySnapshot()

        if let generalStack = pageStackViews["general"] {
            buildLanguageSection(into: generalStack)
            buildMenuBarSection(into: generalStack)
            buildStartupSection(into: generalStack)
            buildRepositorySection(into: generalStack)
        }
        if let agentsStack = pageStackViews["agents"] {
            buildUsageSection(into: agentsStack, availability: availability)
            buildExecutorsSection(into: agentsStack, availability: availability)
            buildApiModelsSection(into: agentsStack)
            buildSubagentProjectsSection(into: agentsStack)
        }
        if let automationStack = pageStackViews["automation"] {
            buildQuotaResetSection(into: automationStack)
        }
        if let messagingStack = pageStackViews["messaging"] {
            buildGatewayStatusBanner(into: messagingStack)

            let segmented = NSSegmentedControl(
                labels: [
                    L10n.tr("integration.tab.slack"),
                    L10n.tr("integration.tab.telegram"),
                    L10n.tr("integration.tab.line"),
                    L10n.tr("integration.tab.qq"),
                    L10n.tr("integration.tab.wechat"),
                    L10n.tr("integration.tab.feishu"),
                ],
                trackingMode: .selectOne,
                target: self,
                action: #selector(messagingSegmentChanged(_:))
            )
            segmented.segmentStyle = .rounded
            segmented.selectedSegment = 0
            segmented.translatesAutoresizingMaskIntoConstraints = false
            if #available(macOS 10.13, *) {
                segmented.segmentDistribution = .fillEqually
            }

            messagingStack.addArrangedSubview(segmented)
            segmented.widthAnchor.constraint(equalTo: messagingStack.widthAnchor, constant: -44).isActive = true
            messagingStack.setCustomSpacing(20, after: segmented)
            self.messagingSegmentedControl = segmented

            buildSlackSection(into: messagingStack)
            buildTelegramSection(into: messagingStack)
            buildLineSection(into: messagingStack)
            buildQQBotSection(into: messagingStack)
            buildWeChatSection(into: messagingStack)
            buildFeishuSection(into: messagingStack)
        }
        if let servicesStack = pageStackViews["services"] {
            buildCompanionServicesSection(into: servicesStack)
        }

        // Default select the first page
        switchPage(to: "general")

        // Initialize visibility without animation
        updateVisibility(animated: false)

        win.center()
        self.window = win
    }

    func show() {
        appDelegate.loadEnv()
        
        let savedPageId = currentPageId
        window?.orderOut(nil)
        window = nil
        
        buildWindowIfNeeded()
        
        if !savedPageId.isEmpty {
            switchPage(to: savedPageId)
        }

        if let win = window {
            appDelegate.loadAutoPingModes()
            applyAutoPingModes()
            win.makeKeyAndOrderFront(nil)
            win.makeFirstResponder(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
        startPendingUsersWatcher()
    }

    /// Open Preferences on the Messaging page with the given channel's
    /// section visible — used when a pending-user notification is clicked.
    func showMessagingChannel(_ channel: PendingUserChannel) {
        show()
        switchPage(to: "messaging")
        messagingSegmentedControl?.selectedSegment = channel.messagingSegmentIndex
        updateMessagingSectionsVisibility()
    }

    func windowWillClose(_ notification: Notification) {
        // Force commit any active editing in text fields before closing
        window?.makeFirstResponder(nil)
        stopPendingUsersWatcher()
    }

    func windowDidBecomeKey(_ notification: Notification) {
        appDelegate.loadAutoPingModes()
        applyAutoPingModes()
        for channel in PendingUserChannel.allCases {
            rebuildPendingUsersStack(channel)
        }
        refreshGatewayBanner()
    }

    func applyAutoPingModes() {
        guard checkClaudeAutoping != nil,
              checkCodexAutoping != nil,
              checkAntigravityAutoping != nil else {
            return
        }
        let controls = [
            ("claude", checkClaudeAutoping!),
            ("codex", checkCodexAutoping!),
            ("antigravity", checkAntigravityAutoping!),
        ]
        for (provider, control) in controls {
            let mode = appDelegate.autoPingModes[provider] ?? "off"
            control.selectItem(at: PreferencesWindow.autoPingModeIndex(mode))
        }
    }

    // Save when user ends editing (loses focus or presses Enter)
    func controlTextDidEndEditing(_ obj: Notification) {
        if let textField = obj.object as? NSTextField {
            if textField == modelsEntry ||
               textField == slackBotTokenEntry ||
               textField == slackAppTokenEntry ||
               textField == slackSigningSecretEntry ||
               textField == slackAllowedUsersEntry ||
               textField == slackChannelEntry ||
               textField == telegramBotTokenEntry ||
               textField == telegramAllowedUsersEntry ||
               textField == telegramWorkspaceEntry ||
               textField == weChatAllowedUsersEntry ||
               textField == weChatWorkspaceEntry ||
               textField == weChatMessageMaxCharsEntry ||
               textField == weChatTypingHeartbeatEntry ||
               textField == lineChannelSecretEntry ||
               textField == lineChannelAccessTokenEntry ||
               textField == lineAllowedUsersEntry ||
               textField == lineWorkspaceEntry ||
               textField == qqbotAppIdEntry ||
               textField == qqbotSecretEntry ||
               textField == qqbotAllowedUsersEntry ||
               textField == qqbotWorkspaceEntry ||
               textField == feishuAppIdEntry ||
               textField == feishuSecretEntry ||
               textField == feishuAllowedUsersEntry ||
               textField == feishuWorkspaceEntry {
                autosave()
            }
        }
    }

    @objc func quitApp() {
        NSApp.terminate(nil)
    }

    @objc func checkForUpdates() {
        // User-initiated check. Sparkle presents the full UI itself — progress,
        // "you're up to date", or the new-version prompt with download/install.
        appDelegate.updaterController.checkForUpdates()
    }

    @objc func toggleAutoUpdate(_ sender: NSSwitch) {
        appDelegate.updaterController?.automaticallyChecksForUpdates = (sender.state == .on)
    }

    @objc func checkAvailability() {
        checkAvailabilityButton.isEnabled = false
        checkAvailabilityButton.title = L10n.tr("preferences.checking")
        appDelegate.runAvailabilityDetection(initialize: false) { [weak self] in
            guard let self = self else { return }
            self.window?.orderOut(nil)
            self.window = nil
            self.show()
        }
    }

    @objc func openWeChatLogin() {
        let pythonBin = (appDelegate.repoPath as NSString).appendingPathComponent(".venv/bin/python")
        guard FileManager.default.fileExists(atPath: pythonBin) else {
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = L10n.tr("preferences.python_missing.title")
            alert.informativeText = L10n.tr("preferences.python_missing.message", pythonBin)
            alert.runModal()
            return
        }

        let ctrl = WeChatLoginWindowController(
            repoPath: appDelegate.repoPath,
            envPath: appDelegate.envPath,
            pythonBin: pythonBin
        )
        self.weChatLoginController = ctrl
        ctrl.show()
    }

    @objc func changeRepository() {
        guard let selected = appDelegate.selectRepositoryForNextLaunch() else {
            return
        }
        repositoryPathLabel.stringValue = selected

        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = L10n.tr("preferences.repository_changed.title")
        alert.informativeText = L10n.tr("preferences.repository_changed.message", selected)
        alert.addButton(withTitle: L10n.tr("preferences.quit_now"))
        alert.addButton(withTitle: L10n.tr("preferences.later"))
        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            NSApp.terminate(nil)
        }
    }

    @objc func repairBackend() {
        appDelegate.repairBackendFromPreferences()
    }

    func updateVisibility(animated: Bool) {
        let targetKeychainHidden = (checkClaude.state == .off)
        let targetClaudeAutoRefreshHidden = (checkClaude.state == .off)
        let targetAntigravityHidden = (checkAntigravity.state == .off)

        let targetGroupAntigravityHidden = targetAntigravityHidden
        let targetModelsHidden = targetAntigravityHidden || (checkGroupAntigravity.state == .on)
        let targetSectionHidden = targetKeychainHidden && targetClaudeAutoRefreshHidden && targetAntigravityHidden

        // Separators: hide the separator for whichever row becomes first visible.
        claudeAutoRefreshRow.separator.isHidden = targetKeychainHidden
        groupAntigravityRow.separator.isHidden = targetKeychainHidden && targetClaudeAutoRefreshHidden

        // Separator of modelsRow: hide if every row before it is hidden.
        modelsRow.separator.isHidden = targetKeychainHidden && targetClaudeAutoRefreshHidden && targetGroupAntigravityHidden

        let targetNotchSilentStyleHidden = (displayStyleSegmented.selectedSegment == 0)
        let targetAppearanceHidden = (displayStyleSegmented.selectedSegment == 1)

        // Slack visibility
        let targetSlackHidden = (checkSlackEnabled.state == .off)
        
        // Telegram visibility
        let targetTelegramHidden = (checkTelegram.state == .off)
        
        // WeChat visibility
        let targetWeChatHidden = (checkWeChat.state == .off)

        // LINE visibility
        let targetLineHidden = (checkLine.state == .off)

        // QQ visibility
        let targetQQBotHidden = (checkQQBot.state == .off)

        // Feishu visibility
        let targetFeishuHidden = (checkFeishu.state == .off)

        if animated {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.25
                context.allowsImplicitAnimation = true

                self.keychainRow.isHidden = targetKeychainHidden
                self.claudeAutoRefreshRow.isHidden = targetClaudeAutoRefreshHidden
                self.groupAntigravityRow.isHidden = targetGroupAntigravityHidden
                self.modelsRow.isHidden = targetModelsHidden
                self.appearanceRow.isHidden = targetAppearanceHidden
                self.silentStyleRow.isHidden = targetNotchSilentStyleHidden
                self.peekResetRow.isHidden = targetNotchSilentStyleHidden
                self.hideOnFullscreenRow.isHidden = targetNotchSilentStyleHidden
                self.apiModelsSection.isHidden = targetSectionHidden

                // Slack integration fields visibility
                self.slackBotTokenRow.isHidden = targetSlackHidden
                self.slackAppTokenRow.isHidden = targetSlackHidden
                self.slackSigningSecretRow.isHidden = targetSlackHidden
                self.slackAllowedUsersRow.isHidden = targetSlackHidden
                self.slackPendingUsersRow.isHidden = targetSlackHidden
                self.slackChannelRow.isHidden = targetSlackHidden
                self.slackHintRow.isHidden = !targetSlackHidden

                // Telegram integration fields visibility
                self.telegramBotTokenRow.isHidden = targetTelegramHidden
                self.telegramAllowedUsersRow.isHidden = targetTelegramHidden
                self.telegramPendingUsersRow.isHidden = targetTelegramHidden
                self.telegramWorkspaceRow.isHidden = targetTelegramHidden
                self.telegramHintRow.isHidden = !targetTelegramHidden

                // WeChat integration fields visibility
                self.weChatLoginRow.isHidden = targetWeChatHidden
                self.weChatAllowedUsersRow.isHidden = targetWeChatHidden
                self.weChatPendingUsersRow.isHidden = targetWeChatHidden
                self.weChatWorkspaceRow.isHidden = targetWeChatHidden
                self.weChatMessageMaxCharsRow.isHidden = targetWeChatHidden
                self.weChatTypingHeartbeatRow.isHidden = targetWeChatHidden
                self.weChatHintRow.isHidden = !targetWeChatHidden

                // LINE integration fields visibility
                self.lineWebhookUrlRow.isHidden = targetLineHidden
                self.lineChannelSecretRow.isHidden = targetLineHidden
                self.lineChannelAccessTokenRow.isHidden = targetLineHidden
                self.lineAllowedUsersRow.isHidden = targetLineHidden
                self.linePendingUsersRow.isHidden = targetLineHidden
                self.lineWorkspaceRow.isHidden = targetLineHidden
                self.lineHintRow.isHidden = !targetLineHidden

                // QQ integration fields visibility
                self.qqbotTransportRow.isHidden = targetQQBotHidden
                self.qqbotAppIdRow.isHidden = targetQQBotHidden
                self.qqbotSecretRow.isHidden = targetQQBotHidden
                self.qqbotSandboxRow.isHidden = targetQQBotHidden
                self.qqbotGroupChatRow.isHidden = targetQQBotHidden
                self.qqbotAllowedUsersRow.isHidden = targetQQBotHidden
                self.qqbotPendingUsersRow.isHidden = targetQQBotHidden
                self.qqbotWorkspaceRow.isHidden = targetQQBotHidden
                self.qqbotHintRow.isHidden = !targetQQBotHidden

                // Feishu integration fields visibility
                self.feishuAppIdRow.isHidden = targetFeishuHidden
                self.feishuSecretRow.isHidden = targetFeishuHidden
                self.feishuGroupChatRow.isHidden = targetFeishuHidden
                self.feishuAllowedUsersRow.isHidden = targetFeishuHidden
                self.feishuPendingUsersRow.isHidden = targetFeishuHidden
                self.feishuWorkspaceRow.isHidden = targetFeishuHidden
                self.feishuHintRow.isHidden = !targetFeishuHidden

                self.window?.contentView?.layoutSubtreeIfNeeded()
            }
        } else {
            self.keychainRow.isHidden = targetKeychainHidden
            self.claudeAutoRefreshRow.isHidden = targetClaudeAutoRefreshHidden
            self.groupAntigravityRow.isHidden = targetGroupAntigravityHidden
            self.modelsRow.isHidden = targetModelsHidden
            self.appearanceRow.isHidden = targetAppearanceHidden
            self.silentStyleRow.isHidden = targetNotchSilentStyleHidden
            self.peekResetRow.isHidden = targetNotchSilentStyleHidden
            self.hideOnFullscreenRow.isHidden = targetNotchSilentStyleHidden
            self.apiModelsSection.isHidden = targetSectionHidden

            self.slackBotTokenRow.isHidden = targetSlackHidden
            self.slackAppTokenRow.isHidden = targetSlackHidden
            self.slackSigningSecretRow.isHidden = targetSlackHidden
            self.slackAllowedUsersRow.isHidden = targetSlackHidden
            self.slackPendingUsersRow.isHidden = targetSlackHidden
            self.slackChannelRow.isHidden = targetSlackHidden
            self.slackHintRow.isHidden = !targetSlackHidden

            self.telegramBotTokenRow.isHidden = targetTelegramHidden
            self.telegramAllowedUsersRow.isHidden = targetTelegramHidden
            self.telegramPendingUsersRow.isHidden = targetTelegramHidden
            self.telegramWorkspaceRow.isHidden = targetTelegramHidden
            self.telegramHintRow.isHidden = !targetTelegramHidden

            self.weChatLoginRow.isHidden = targetWeChatHidden
            self.weChatAllowedUsersRow.isHidden = targetWeChatHidden
            self.weChatPendingUsersRow.isHidden = targetWeChatHidden
            self.weChatWorkspaceRow.isHidden = targetWeChatHidden
            self.weChatMessageMaxCharsRow.isHidden = targetWeChatHidden
            self.weChatTypingHeartbeatRow.isHidden = targetWeChatHidden
            self.weChatHintRow.isHidden = !targetWeChatHidden

            // LINE integration fields visibility
            self.lineWebhookUrlRow.isHidden = targetLineHidden
            self.lineChannelSecretRow.isHidden = targetLineHidden
            self.lineChannelAccessTokenRow.isHidden = targetLineHidden
            self.lineAllowedUsersRow.isHidden = targetLineHidden
            self.linePendingUsersRow.isHidden = targetLineHidden
            self.lineWorkspaceRow.isHidden = targetLineHidden
            self.lineHintRow.isHidden = !targetLineHidden

            // QQ integration fields visibility
            self.qqbotTransportRow.isHidden = targetQQBotHidden
            self.qqbotAppIdRow.isHidden = targetQQBotHidden
            self.qqbotSecretRow.isHidden = targetQQBotHidden
            self.qqbotSandboxRow.isHidden = targetQQBotHidden
            self.qqbotGroupChatRow.isHidden = targetQQBotHidden
            self.qqbotAllowedUsersRow.isHidden = targetQQBotHidden
            self.qqbotPendingUsersRow.isHidden = targetQQBotHidden
            self.qqbotWorkspaceRow.isHidden = targetQQBotHidden
            self.qqbotHintRow.isHidden = !targetQQBotHidden

            // Feishu integration fields visibility
            self.feishuAppIdRow.isHidden = targetFeishuHidden
            self.feishuSecretRow.isHidden = targetFeishuHidden
            self.feishuGroupChatRow.isHidden = targetFeishuHidden
            self.feishuAllowedUsersRow.isHidden = targetFeishuHidden
            self.feishuPendingUsersRow.isHidden = targetFeishuHidden
            self.feishuWorkspaceRow.isHidden = targetFeishuHidden
            self.feishuHintRow.isHidden = !targetFeishuHidden
        }

        // Reset-notification switches only deliver once their channel is set
        // up in Messaging, so gate each on its channel toggle instead of
        // leaving a switch operable that would silently do nothing.
        updateNotifyChannelGate(
            row: slackNotifyRefreshRow, toggle: checkSlackNotifyRefresh,
            channelOn: !targetSlackHidden, descKey: "preferences.notify.slack.desc",
            channelName: "Slack")
        updateNotifyChannelGate(
            row: telegramNotifyRefreshRow, toggle: checkTelegramNotifyRefresh,
            channelOn: !targetTelegramHidden, descKey: "preferences.notify.telegram.desc",
            channelName: "Telegram")
        updateNotifyChannelGate(
            row: qqbotNotifyRefreshRow, toggle: checkQQBotNotifyRefresh,
            channelOn: !targetQQBotHidden, descKey: "preferences.notify.qq.desc",
            channelName: "QQ")
        updateNotifyChannelGate(
            row: feishuNotifyRefreshRow, toggle: checkFeishuNotifyRefresh,
            channelOn: !targetFeishuHidden, descKey: "preferences.notify.feishu.desc",
            channelName: "Feishu")
        updateNotifyChannelGate(
            row: weChatNotifyRefreshRow, toggle: checkWeChatNotifyRefresh,
            channelOn: !targetWeChatHidden, descKey: "preferences.notify.wechat.desc",
            channelName: "WeChat")
        updateNotifyChannelGate(
            row: lineNotifyRefreshRow, toggle: checkLineNotifyRefresh,
            channelOn: !targetLineHidden, descKey: "preferences.notify.line.desc",
            channelName: "LINE")

        updateMessagingSectionsVisibility()
        updateSidebarDots()
    }

    // Dims a reset-notification row while its channel is disabled. The saved
    // env value is left untouched so the preference survives re-enabling.
    private func updateNotifyChannelGate(
        row: CardRow?, toggle: NSSwitch?, channelOn: Bool, descKey: String, channelName: String
    ) {
        guard let row = row, let toggle = toggle else { return }
        toggle.isEnabled = channelOn
        var desc = L10n.tr(descKey)
        if !channelOn {
            desc += " " + L10n.tr("preferences.notify.requires_channel", channelName)
        }
        row.descLabel?.stringValue = desc
        row.titleLabel.textColor = channelOn ? Palette.primaryText : Palette.secondaryText
    }

    @objc func messagingSegmentChanged(_ sender: NSSegmentedControl) {
        let cleanQuery = searchField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !cleanQuery.isEmpty {
            let selectedIndex = sender.selectedSegment
            let messagingStacks = [
                slackSectionStack,
                telegramSectionStack,
                lineSectionStack,
                qqbotSectionStack,
                weChatSectionStack,
                feishuSectionStack
            ]
            let selectedStack = messagingStacks[selectedIndex]

            // Check if this stack matches the query
            var hasMatch = false
            if let sectionStack = selectedStack {
                let sectionMatches = sectionSearchText(in: sectionStack).contains(cleanQuery)
                if sectionMatches {
                    hasMatch = true
                } else {
                    for view in sectionStack.arrangedSubviews {
                        if let card = view as? CardView {
                            for row in card.stackView.arrangedSubviews {
                                guard let cardRow = row as? CardRowBase else { continue }
                                if settingRowSearchText(cardRow).contains(cleanQuery) {
                                    hasMatch = true
                                    break
                                }
                            }
                        }
                        if hasMatch { break }
                    }
                }
            }

            if !hasMatch {
                // Clear search if no matches in the newly selected segment
                searchField.stringValue = ""
            }
        }

        filterSettings(query: searchField.stringValue, autoSwitchMessagingTab: false)
    }

    func updateMessagingSectionsVisibility() {
        let selectedIndex = messagingSegmentedControl?.selectedSegment ?? 0

        let searchActive = !(searchField?.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true)
        if searchActive {
            return
        }

        slackSectionStack?.isHidden = (selectedIndex != 0)
        telegramSectionStack?.isHidden = (selectedIndex != 1)
        lineSectionStack?.isHidden = (selectedIndex != 2)
        qqbotSectionStack?.isHidden = (selectedIndex != 3)
        weChatSectionStack?.isHidden = (selectedIndex != 4)
        feishuSectionStack?.isHidden = (selectedIndex != 5)
    }

    @objc func autosave() {
        // Snapshot before saveEnv() refreshes envVals: the gateway auto-start
        // logic below needs to see which channel settings actually changed.
        let previousEnv = appDelegate.envVals
        var updates: [String: String] = [:]

        let appearances = ["native", "rich"]
        let appearanceIdx = appearanceSegmented.selectedSegment
        if appearanceIdx >= 0 && appearanceIdx < appearances.count {
            updates["FLUXION_MENU_APPEARANCE"] = appearances[appearanceIdx]
        }

        // Silent style popup index mapping
        let silentStyles = ["lowest", "all", "ambient"]
        let silentSelIdx = silentStylePopup.indexOfSelectedItem
        if silentSelIdx >= 0 && silentSelIdx < silentStyles.count {
            updates["FLUXION_NOTCH_SILENT_STYLE"] = silentStyles[silentSelIdx]
        }

        // Peek countdown window popup index mapping
        let peekResets = ["5h", "week", "both"]
        let peekResetSelIdx = peekResetPopup.indexOfSelectedItem
        if peekResetSelIdx >= 0 && peekResetSelIdx < peekResets.count {
            updates["FLUXION_NOTCH_PEEK_RESET"] = peekResets[peekResetSelIdx]
        }

        updates["FLUXION_NOTCH_HIDE_ON_FULLSCREEN"] = checkHideOnFullscreen.state == .on ? "true" : "false"

        // Notch Mode
        let isNotchEnabled = displayStyleSegmented.selectedSegment == 1
        updates["FLUXION_NOTCH_MODE"] = isNotchEnabled ? "true" : "false"

        // Active Providers
        var activeProvs: [String] = []
        if checkClaude.state == .on { activeProvs.append("claude") }
        if checkCodex.state == .on { activeProvs.append("codex") }
        if checkAntigravity.state == .on { activeProvs.append("antigravity") }
        updates["FLUXION_USAGE_PROVIDERS"] = activeProvs.joined(separator: ",")

        // Enabled task executors
        var enabledExecutors = Set<String>()
        if checkExecutorClaude.state == .on { enabledExecutors.insert("claude") }
        if checkExecutorCodex.state == .on { enabledExecutors.insert("codex") }
        if checkExecutorAntigravity.state == .on { enabledExecutors.insert("antigravity") }
        // Re-add executors that are enabled in preference but not installed:
        // their toggle was forced off/disabled, so reading it would drop them.
        enabledExecutors.formUnion(executorsPreservedWhileUnavailable)
        var orderedExecutors = PreferencesWindow.executorProviderKeys.filter { enabledExecutors.contains($0) }
        if orderedExecutors.isEmpty {
            checkExecutorAntigravity.state = .on
            orderedExecutors.append("antigravity")
        }
        updates["FLUXION_ENABLED_EXECUTORS"] = orderedExecutors.joined(separator: ",")

        // Default task executor (the gateway falls back to the first enabled
        // executor when the chosen default is disabled above).
        let defaultExecutorIdx = defaultExecutorPopup.indexOfSelectedItem
        if defaultExecutorIdx >= 0 && defaultExecutorIdx < PreferencesWindow.executorProviderKeys.count {
            updates["FLUXION_DEFAULT_EXECUTOR"] = PreferencesWindow.executorProviderKeys[defaultExecutorIdx]
        }

        // Keychain
        updates["FLUXION_CLAUDE_USAGE_KEYCHAIN"] = checkKeychain.state == .on ? "true" : "false"
        updates["FLUXION_CLAUDE_USAGE_AUTO_REFRESH"] = checkClaudeAutoRefresh.state == .on ? "true" : "false"

        // Group Models
        updates["FLUXION_ANTIGRAVITY_GROUP_MODELS"] = checkGroupAntigravity.state == .on ? "true" : "false"

        // Models shortlist
        updates["FLUXION_ANTIGRAVITY_USAGE_MODELS"] = modelsEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)

        // Autostart Services
        updates["FLUXION_MENU_AUTOSTART_WEB"] = checkWeb.state == .on ? "true" : "false"
        updates["FLUXION_MENU_AUTOSTART_SCHEDULER"] = checkScheduler.state == .on ? "true" : "false"
        updates["FLUXION_SCHEDULER_ENABLED"] = checkScheduler.state == .on ? "true" : "false"
        updates["FLUXION_MENU_AUTOSTART_GATEWAY"] = checkSlack.state == .on ? "true" : "false"

        // Quota Reset Automation is stored as managed scheduler rules.
        let claudeMode = PreferencesWindow.autoPingModeFromIndex(
            checkClaudeAutoping.indexOfSelectedItem)
        let codexMode = PreferencesWindow.autoPingModeFromIndex(
            checkCodexAutoping.indexOfSelectedItem)
        let antigravityMode = PreferencesWindow.autoPingModeFromIndex(
            checkAntigravityAutoping.indexOfSelectedItem)
        
        updates["FLUXION_AUTOPING_ENABLED"] = checkAutoPingEnabled.state == .on ? "true" : "false"
        updates["FLUXION_MENU_SLACK_NOTIFY_REFRESH"] = checkSlackNotifyRefresh.state == .on ? "true" : "false"
        updates["FLUXION_MENU_TELEGRAM_NOTIFY_REFRESH"] = checkTelegramNotifyRefresh.state == .on ? "true" : "false"
        updates["FLUXION_MENU_QQBOT_NOTIFY_REFRESH"] = checkQQBotNotifyRefresh.state == .on ? "true" : "false"
        updates["FLUXION_MENU_FEISHU_NOTIFY_REFRESH"] = checkFeishuNotifyRefresh.state == .on ? "true" : "false"
        updates["FLUXION_MENU_WECHAT_NOTIFY_REFRESH"] = checkWeChatNotifyRefresh.state == .on ? "true" : "false"
        updates["FLUXION_MENU_LINE_NOTIFY_REFRESH"] = checkLineNotifyRefresh.state == .on ? "true" : "false"
        updates["FLUXION_MENU_MACOS_NOTIFY_REFRESH"] = checkMacOSNotifyRefresh.state == .on ? "true" : "false"
        updates["FLUXION_NOTIFY_CREDIT_GRANT"] = checkNotifyCreditGrant.state == .on ? "true" : "false"
        updates["FLUXION_NOTIFY_CREDIT_EXPIRY"] = checkNotifyCreditExpiry.state == .on ? "true" : "false"

        // Slack Integration
        updates["FLUXION_SLACK_ENABLED"] = checkSlackEnabled.state == .on ? "true" : "false"
        updates["SLACK_BOT_TOKEN"] = slackBotTokenEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["SLACK_APP_TOKEN"] = slackAppTokenEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["SLACK_SIGNING_SECRET"] = slackSigningSecretEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_SLACK_ALLOWED_USERS"] = slackAllowedUsersEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_SCHEDULER_SLACK_CHANNEL"] = slackChannelEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)

        // Telegram Integration
        updates["FLUXION_TELEGRAM_ENABLED"] = checkTelegram.state == .on ? "true" : "false"
        updates["TELEGRAM_BOT_TOKEN"] = telegramBotTokenEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_TELEGRAM_ALLOWED_USERS"] = telegramAllowedUsersEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_TELEGRAM_DEFAULT_WORKSPACE"] = telegramWorkspaceEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)

        // WeChat Integration
        updates["FLUXION_WECHAT_ENABLED"] = checkWeChat.state == .on ? "true" : "false"
        updates["FLUXION_WECHAT_ALLOWED_USERS"] = weChatAllowedUsersEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_WECHAT_DEFAULT_WORKSPACE"] = weChatWorkspaceEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_WECHAT_MESSAGE_MAX_CHARS"] = weChatMessageMaxCharsEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_WECHAT_TYPING_HEARTBEAT_SEC"] = weChatTypingHeartbeatEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)

        // LINE Integration
        updates["FLUXION_LINE_ENABLED"] = checkLine.state == .on ? "true" : "false"
        updates["LINE_CHANNEL_SECRET"] = lineChannelSecretEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["LINE_CHANNEL_ACCESS_TOKEN"] = lineChannelAccessTokenEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_LINE_ALLOWED_USERS"] = lineAllowedUsersEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_LINE_DEFAULT_WORKSPACE"] = lineWorkspaceEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)

        // QQ Integration
        updates["FLUXION_QQBOT_ENABLED"] = checkQQBot.state == .on ? "true" : "false"
        updates["FLUXION_QQBOT_TRANSPORT"] = qqbotTransportPopup.titleOfSelectedItem ?? "websocket"
        updates["QQBOT_APP_ID"] = qqbotAppIdEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["QQBOT_CLIENT_SECRET"] = qqbotSecretEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_QQBOT_SANDBOX"] = checkQQBotSandbox.state == .on ? "true" : "false"
        updates["FLUXION_QQBOT_ALLOW_GROUP_CHAT"] = checkQQBotGroupChat.state == .on ? "true" : "false"
        updates["FLUXION_QQBOT_ALLOWED_USERS"] = qqbotAllowedUsersEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_QQBOT_DEFAULT_WORKSPACE"] = qqbotWorkspaceEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)

        // Feishu Integration
        updates["FLUXION_FEISHU_ENABLED"] = checkFeishu.state == .on ? "true" : "false"
        updates["FEISHU_APP_ID"] = feishuAppIdEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FEISHU_APP_SECRET"] = feishuSecretEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_FEISHU_ALLOW_GROUP_CHAT"] = checkFeishuGroupChat.state == .on ? "true" : "false"
        updates["FLUXION_FEISHU_ALLOWED_USERS"] = feishuAllowedUsersEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        updates["FLUXION_FEISHU_DEFAULT_WORKSPACE"] = feishuWorkspaceEntry.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)

        // Sub-agent Projects
        var items: [ProjectItem] = []
        for row in projectRowViews {
            let key = row.keyField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            let ws = row.workspaceField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            if key.isEmpty || ws.isEmpty { continue }
            let execIdx = row.executorPopUp.indexOfSelectedItem
            let exec: String
            switch execIdx {
            case 1: exec = "claude"
            case 2: exec = "codex"
            case 3: exec = "antigravity"
            default: exec = "default"
            }
            let desc = row.descField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            items.append(ProjectItem(key: key, workspace: ws, executor: exec, description: desc))
        }
        updates["FLUXION_PROJECTS"] = serializeProjects(items)

        // Channels only deliver messages while the gateway runs, but its
        // autostart switch lives on the Services page and defaults to off — a
        // first-time user who just configured a channel here would otherwise
        // save into silence. Enabling a channel (or supplying its credentials)
        // is an explicit request for messaging, so pull the gateway up with
        // it; saveEnv()'s service bounce below then starts it with the fresh
        // config. A deliberate autostart=off survives unrelated saves because
        // this only fires on those two transitions.
        if updates["FLUXION_MENU_AUTOSTART_GATEWAY"] != "true",
           channelActivationRequested(previous: previousEnv, updates: updates) {
            updates["FLUXION_MENU_AUTOSTART_GATEWAY"] = "true"
            checkSlack.state = .on
        }

        appDelegate.saveEnv(updates: updates)

        // envVals now reflects the save; keep the project rows' "Default (…)"
        // popup item pointing at the actual global default.
        for row in projectRowViews {
            row.updateDefaultExecutorTitle(defaultExecutorDisplayName())
        }

        // saveEnv() bounces the gateway in the background, so it is
        // legitimately down for the next few seconds. Open the grace window
        // before probing; a mid-bounce "not running" then stays quiet and
        // re-probes itself after the deadline.
        gatewayBounceGraceUntil = Date().addingTimeInterval(10.0)
        refreshGatewayBanner(afterDelay: 2.0)

        // Dynamic Island/Notch Window toggle
        if isNotchEnabled {
            if appDelegate.notchWindowController == nil {
                DispatchQueue.main.async {
                    self.appDelegate.notchWindowController = NotchWindowController()
                    self.appDelegate.notchWindowController?.show()
                    self.appDelegate.render() // Push current data to notch immediately
                }
            }
        } else {
            if let notch = appDelegate.notchWindowController {
                notch.hide()
                appDelegate.notchWindowController = nil
            }
        }
        var autoPingSaveFailed = false
        for (provider, mode) in [
            ("claude", claudeMode),
            ("codex", codexMode),
            ("antigravity", antigravityMode),
        ] where appDelegate.autoPingModes[provider] != mode {
            if !appDelegate.setAutoPingMode(provider: provider, mode: mode) {
                autoPingSaveFailed = true
            }
        }
        if autoPingSaveFailed {
            applyAutoPingModes()
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = L10n.tr("preferences.autoping_failed.title")
            alert.informativeText = L10n.tr("preferences.autoping_failed.message")
            alert.addButton(withTitle: L10n.tr("preferences.ok"))
            if let win = window {
                alert.beginSheetModal(for: win, completionHandler: nil)
            } else {
                alert.runModal()
            }
        }

        // Launch at Login (Only run if state actually changed to avoid synchronous system SMAppService blocking lag!)
        // SMAppService needs macOS 13+; on older systems the toggle is disabled.
        if #available(macOS 13.0, *), checkLaunchAtLogin.state != lastLaunchAtLoginState {
            let loginService = SMAppService.mainApp
            if checkLaunchAtLogin.state == .on {
                if loginService.status != .enabled {
                    do {
                        try loginService.register()
                        NSLog("FluxionMenu: Registered launch at login successfully.")
                    } catch {
                        NSLog("FluxionMenu: Failed to register launch at login: %@", error.localizedDescription)
                    }
                }
            } else {
                if loginService.status == .enabled {
                    do {
                        try loginService.unregister()
                        NSLog("FluxionMenu: Unregistered launch at login successfully.")
                    } catch {
                        NSLog("FluxionMenu: Failed to unregister launch at login: %@", error.localizedDescription)
                    }
                }
            }
            lastLaunchAtLoginState = checkLaunchAtLogin.state
        }

        // Update visibility of dependent rows instantly (no layout flight animation)
        updateVisibility(animated: false)
        if let search = searchField,
           !search.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            filterSettings(query: search.stringValue)
        }

        // Reflect the new settings in the menu bar immediately (no subprocess)…
        appDelegate.render()

        // …and coalesce rapid toggles into a single forced refresh.
        refreshWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in
            self?.appDelegate.triggerRefresh()
        }
        refreshWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4, execute: work)
    }

    private func selectedAppLanguage() -> String {
        let languages = ["system", "zh-Hans", "en", "ja"]
        let idx = languagePopup?.indexOfSelectedItem ?? 0
        guard idx >= 0 && idx < languages.count else { return "system" }
        return languages[idx]
    }

    @objc func languageChanged() {
        let next = selectedAppLanguage()
        UserDefaults.standard.set(next, forKey: L10n.languageDefaultsKey)
        UserDefaults.standard.synchronize()
        languageRestartNotice?.isHidden = (next == initialAppLanguage)

        if let search = searchField,
           !search.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            filterSettings(query: search.stringValue)
        }
    }

    @objc func restartAppNow() {
        let appPath = Bundle.main.bundlePath
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        let escapedPath = appPath.replacingOccurrences(of: "'", with: "'\\''")
        task.arguments = ["-c", "sleep 0.4; /usr/bin/open '\(escapedPath)'"]

        do {
            try task.run()
        } catch {
            NSLog("FluxionMenu: failed to schedule app relaunch: %@", error.localizedDescription)
        }
        NSApp.terminate(nil)
    }

    func switchPage(to pageId: String) {
        currentPageId = pageId
        applyPageSelection()

        // Pending users can be recorded while the window stays key, so reload
        // them from disk whenever the messaging page is (re)entered.
        if pageId == "messaging" {
            for channel in PendingUserChannel.allCases {
                rebuildPendingUsersStack(channel)
            }
        }

        if let search = searchField {
            filterSettings(query: search.stringValue)
        }

        // Flush layout so controls (especially NSSwitch) that just became
        // visible get correct frames before their first draw.
        window?.contentView?.layoutSubtreeIfNeeded()
        scheduleScrollSettingsToTop()
    }

    func scheduleScrollSettingsToTop() {
        scrollSettingsToTop()
        DispatchQueue.main.async { [weak self] in
            self?.scrollSettingsToTop()
        }
    }

    func scrollSettingsToTop() {
        guard let scrollView = settingsScrollView else {
            return
        }
        window?.contentView?.layoutSubtreeIfNeeded()
        scrollView.contentView.scroll(to: .zero)
        scrollView.reflectScrolledClipView(scrollView.contentView)
    }

    private func applyPageSelection() {
        for item in sidebarNavItems {
            item.isActive = (item.id == currentPageId)
        }

        // Dynamic stack visibility swap
        for (id, stack) in pageStackViews {
            stack.isHidden = (id != currentPageId)
        }
    }

    func filterSettings(query: String, autoSwitchMessagingTab: Bool = true) {
        let cleanQuery = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        resetSearchVisibility()
        
        guard !cleanQuery.isEmpty else {
            applyPageSelection()
            updateMessagingSectionsVisibility()
            return
        }

        // Find which messaging segment matches if the query matches anything in the messaging stacks
        let messagingStacks = [
            slackSectionStack,
            telegramSectionStack,
            lineSectionStack,
            qqbotSectionStack,
            weChatSectionStack,
            feishuSectionStack
        ]

        if autoSwitchMessagingTab {
            var messagingMatchedSegment: Int? = nil
            for (idx, stack) in messagingStacks.enumerated() {
                guard let sectionStack = stack else { continue }
                let sectionMatches = sectionSearchText(in: sectionStack).contains(cleanQuery)
                var hasMatch = false

                if sectionMatches {
                    hasMatch = true
                } else {
                    for view in sectionStack.arrangedSubviews {
                        if let card = view as? CardView {
                            for row in card.stackView.arrangedSubviews {
                                guard let cardRow = row as? CardRowBase else { continue }
                                if settingRowSearchText(cardRow).contains(cleanQuery) {
                                    hasMatch = true
                                    break
                                }
                            }
                        }
                        if hasMatch { break }
                    }
                }

                if hasMatch {
                    if messagingMatchedSegment == nil {
                        messagingMatchedSegment = idx
                    }
                    // If the currently selected segment matches, we prefer to stay on it
                    if idx == messagingSegmentedControl?.selectedSegment {
                        messagingMatchedSegment = idx
                        break
                    }
                }
            }

            // If we found a matching segment and it's different from the current one, switch to it
            if let targetSegment = messagingMatchedSegment, targetSegment != messagingSegmentedControl?.selectedSegment {
                messagingSegmentedControl?.selectedSegment = targetSegment
            }
        }

        var pageHasMatches: [String: Bool] = [:]

        for (pageId, stack) in pageStackViews {
            var pageMatched = false

            for (sectionStack, card) in settingSections(in: stack) {
                let sectionMatches = sectionSearchText(in: sectionStack).contains(cleanQuery)
                var matchedRowCount = 0

                for row in card.stackView.arrangedSubviews {
                    guard let cardRow = row as? CardRowBase else { continue }

                    let baseVisible = !cardRow.isHidden
                    let rowMatches = sectionMatches || settingRowSearchText(cardRow).contains(cleanQuery)
                    let shouldShow = baseVisible && rowMatches

                    cardRow.isHidden = !shouldShow
                    if shouldShow {
                        matchedRowCount += 1
                        pageMatched = true
                    }
                }

                sectionStack.isHidden = (matchedRowCount == 0)
            }

            pageHasMatches[pageId] = pageMatched
        }

        let matchingPageIds = sidebarNavItems
            .map { $0.id }
            .filter { pageHasMatches[$0] == true }

        for item in sidebarNavItems {
            // Keep the full navigation visible when there are no matches, so
            // the user is not trapped in an empty sidebar.
            item.isHidden = !matchingPageIds.isEmpty && pageHasMatches[item.id] != true
        }

        if !matchingPageIds.contains(currentPageId), let firstMatch = matchingPageIds.first {
            currentPageId = firstMatch
        }

        applyPageSelection()

        // Force non-selected messaging stacks to be hidden during search
        if let selectedIdx = messagingSegmentedControl?.selectedSegment {
            for (idx, stack) in messagingStacks.enumerated() {
                if idx != selectedIdx {
                    stack?.isHidden = true
                }
            }
        }
    }

    private func resetSearchVisibility() {
        for item in sidebarNavItems {
            item.isHidden = false
        }

        for stack in pageStackViews.values {
            for (sectionStack, card) in settingSections(in: stack) {
                sectionStack.isHidden = false
                for row in card.stackView.arrangedSubviews {
                    row.isHidden = false
                }
            }
        }

        updateVisibility(animated: false)
        languageRestartNotice?.isHidden = (selectedAppLanguage() == initialAppLanguage)
    }

    private func settingSections(in stack: NSStackView) -> [(sectionStack: NSStackView, card: CardView)] {
        var sections: [(sectionStack: NSStackView, card: CardView)] = []

        for subview in stack.arrangedSubviews {
            guard let sectionStack = subview as? NSStackView,
                  sectionStack.arrangedSubviews.count >= 2,
                  let card = sectionStack.arrangedSubviews[1] as? CardView else {
                continue
            }

            sections.append((sectionStack, card))
        }

        return sections
    }

    private func settingRowSearchText(_ row: CardRowBase) -> String {
        return [
            row.titleLabel.stringValue,
            row.descLabel?.stringValue ?? ""
        ].joined(separator: " ").lowercased()
    }

    private func sectionSearchText(in sectionStack: NSStackView) -> String {
        guard let headerContainer = sectionStack.arrangedSubviews.first else {
            return ""
        }

        return textFieldValues(in: headerContainer).joined(separator: " ").lowercased()
    }

    private func textFieldValues(in view: NSView) -> [String] {
        var values: [String] = []

        if let textField = view as? NSTextField {
            values.append(textField.stringValue)
        }

        for subview in view.subviews {
            values.append(contentsOf: textFieldValues(in: subview))
        }

        return values
    }

    func controlTextDidChange(_ obj: Notification) {
        if let textField = obj.object as? NSTextField, textField == searchField {
            filterSettings(query: textField.stringValue)
        }
    }

    func updateSidebarDots() {
        let hasAgents = (checkClaude?.state == .on) || (checkCodex?.state == .on) || (checkAntigravity?.state == .on)
        let hasMessaging = (checkSlackEnabled?.state == .on) || (checkTelegram?.state == .on) || (checkWeChat?.state == .on) || (checkLine?.state == .on) || (checkQQBot?.state == .on) || (checkFeishu?.state == .on)
        let hasServices = (checkWeb?.state == .on) || (checkScheduler?.state == .on) || (checkSlack?.state == .on)
        
        for item in sidebarNavItems {
            switch item.id {
            case "agents": item.showDot = hasAgents
            case "messaging": item.showDot = hasMessaging
            case "services": item.showDot = hasServices
            default: item.showDot = false
            }
        }
    }

    // MARK: - Sub-agent Projects Helpers
    @objc func addProjectClicked() {
        addProjectRow(key: "", workspace: "", executor: "default", description: "")
        autosave()
    }
    
    func addProjectRow(key: String, workspace: String, executor: String, description: String) {
        let rowView = ProjectRowView(
            key: key,
            workspace: workspace,
            executor: executor,
            description: description,
            onChanged: { [weak self] in
                self?.autosave()
            },
            onRemove: { [weak self] row in
                self?.removeProjectRow(row)
            }
        )
        projectRowViews.append(rowView)
        projectsContainerStack.addArrangedSubview(rowView)
        rowView.widthAnchor.constraint(equalTo: projectsContainerStack.widthAnchor).isActive = true
        rowView.updateDefaultExecutorTitle(defaultExecutorDisplayName())

        updateSeparators()
    }
    
    func removeProjectRow(_ rowView: ProjectRowView) {
        if let index = projectRowViews.firstIndex(where: { $0 === rowView }) {
            projectRowViews.remove(at: index)
        }
        rowView.removeFromSuperview()
        updateSeparators()
        autosave()
    }
    
    func updateSeparators() {
        for (index, rowView) in projectRowViews.enumerated() {
            rowView.separator.isHidden = (index == 0)
        }
        if addRowSep != nil {
            addRowSep.isHidden = projectRowViews.isEmpty
        }
    }
    
    func parseProjects(from raw: String) -> [ProjectItem] {
        var items: [ProjectItem] = []
        let parts = raw.components(separatedBy: ",")
        for part in parts {
            let trimmed = part.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty { continue }
            guard let eqRange = trimmed.range(of: "=") else { continue }
            let key = String(trimmed[..<eqRange.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
            let rest = String(trimmed[eqRange.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
            
            let subparts = rest.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            guard !subparts.isEmpty else { continue }
            let workspace = subparts[0]
            if key.isEmpty || workspace.isEmpty { continue }
            
            var executor = "default"
            var description = ""
            for i in 1..<subparts.count {
                let attr = subparts[i]
                guard let subEqRange = attr.range(of: "=") else { continue }
                let attrKey = String(attr[..<subEqRange.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
                let attrVal = String(attr[subEqRange.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
                if attrKey == "executor" || attrKey == "default_executor" {
                    executor = attrVal.lowercased()
                } else if attrKey == "description" {
                    description = attrVal
                }
            }
            items.append(ProjectItem(key: key, workspace: workspace, executor: executor, description: description))
        }
        return items
    }
    
    func serializeProjects(_ items: [ProjectItem]) -> String {
        var projectStrings: [String] = []
        for item in items {
            let key = item.key.trimmingCharacters(in: .whitespacesAndNewlines)
            let workspace = item.workspace.trimmingCharacters(in: .whitespacesAndNewlines)
            if key.isEmpty || workspace.isEmpty { continue }
            var parts = [workspace]
            let executor = item.executor.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
            if executor != "default" && !executor.isEmpty {
                parts.append("executor=\(executor)")
            }
            let desc = item.description.trimmingCharacters(in: .whitespacesAndNewlines)
            if !desc.isEmpty {
                parts.append("description=\(desc)")
            }
            projectStrings.append("\(key)=\(parts.joined(separator: "|"))")
        }
        return projectStrings.joined(separator: ",")
    }
}

// MARK: - Project Row View
class ProjectRowView: NSView, NSTextFieldDelegate {
    let keyField: NSTextField
    let workspaceField: NSTextField
    let chooseButton: NSButton
    let executorPopUp: NSPopUpButton
    let descField: NSTextField
    let removeButton: NSButton
    let separator = NSView()
    
    var onChanged: (() -> Void)?
    var onRemove: ((ProjectRowView) -> Void)?
    
    init(key: String, workspace: String, executor: String, description: String, onChanged: (() -> Void)?, onRemove: ((ProjectRowView) -> Void)?) {
        self.onChanged = onChanged
        self.onRemove = onRemove
        
        keyField = NSTextField()
        keyField.stringValue = key
        keyField.placeholderString = L10n.tr("preferences.project.key")
        keyField.bezelStyle = .roundedBezel
        keyField.controlSize = .small
        keyField.font = NSFont.systemFont(ofSize: 11.5)
        
        workspaceField = NSTextField()
        workspaceField.stringValue = workspace
        workspaceField.placeholderString = L10n.tr("preferences.project.workspace_path")
        workspaceField.bezelStyle = .roundedBezel
        workspaceField.controlSize = .small
        workspaceField.font = NSFont.systemFont(ofSize: 11.5)
        
        chooseButton = NSButton(title: L10n.tr("preferences.project.choose"), target: nil, action: nil)
        chooseButton.bezelStyle = .rounded
        chooseButton.controlSize = .small
        chooseButton.font = NSFont.systemFont(ofSize: 11.5)
        
        executorPopUp = NSPopUpButton(frame: .zero, pullsDown: false)
        executorPopUp.addItems(withTitles: [L10n.tr("preferences.project.default_executor"), "Claude", "Codex", "Antigravity"])
        executorPopUp.controlSize = .small
        executorPopUp.font = NSFont.systemFont(ofSize: 11.5)
        
        let lowerExec = executor.lowercased()
        if lowerExec == "claude" {
            executorPopUp.selectItem(at: 1)
        } else if lowerExec == "codex" {
            executorPopUp.selectItem(at: 2)
        } else if lowerExec == "antigravity" {
            executorPopUp.selectItem(at: 3)
        } else {
            executorPopUp.selectItem(at: 0)
        }
        
        descField = NSTextField()
        descField.stringValue = description
        descField.placeholderString = L10n.tr("preferences.project.description")
        descField.bezelStyle = .roundedBezel
        descField.controlSize = .small
        descField.font = NSFont.systemFont(ofSize: 11.5)
        
        removeButton = NSButton(title: "—", target: nil, action: nil)
        removeButton.bezelStyle = .rounded
        removeButton.controlSize = .small
        
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        
        separator.translatesAutoresizingMaskIntoConstraints = false
        separator.wantsLayer = true
        separator.layer?.backgroundColor = Palette.separator.cgColor
        addSubview(separator)
        
        NSLayoutConstraint.activate([
            separator.topAnchor.constraint(equalTo: topAnchor),
            separator.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            separator.trailingAnchor.constraint(equalTo: trailingAnchor),
            separator.heightAnchor.constraint(equalToConstant: 0.5)
        ])
        
        let hStack = NSStackView(views: [keyField, workspaceField, chooseButton, executorPopUp, descField, removeButton])
        hStack.orientation = .horizontal
        hStack.spacing = 8
        hStack.alignment = .centerY
        hStack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(hStack)
        
        NSLayoutConstraint.activate([
            hStack.topAnchor.constraint(equalTo: topAnchor, constant: 8),
            hStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 16),
            hStack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -16),
            hStack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -8),
            
            keyField.widthAnchor.constraint(equalToConstant: 65),
            chooseButton.widthAnchor.constraint(equalToConstant: 65),
            executorPopUp.widthAnchor.constraint(equalToConstant: 118),
            removeButton.widthAnchor.constraint(equalToConstant: 25),
            workspaceField.widthAnchor.constraint(equalTo: descField.widthAnchor, multiplier: 1.3)
        ])
        
        keyField.delegate = self
        workspaceField.delegate = self
        descField.delegate = self
        
        chooseButton.target = self
        chooseButton.action = #selector(chooseWorkspace)
        
        executorPopUp.target = self
        executorPopUp.action = #selector(executorChanged)
        
        removeButton.target = self
        removeButton.action = #selector(removeClicked)
    }
    
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    /// Rename the first popup item to "Default (Codex)" etc. so the inherited
    /// global default is visible instead of an opaque "Default".
    func updateDefaultExecutorTitle(_ name: String) {
        let base = L10n.tr("preferences.project.default_executor")
        executorPopUp.item(at: 0)?.title = name.isEmpty ? base : "\(base) (\(name))"
    }
    
    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        separator.layer?.backgroundColor = Palette.separator.cgColor
    }
    
    @objc private func chooseWorkspace() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = L10n.tr("repository.choose.short")
        
        if let window = self.window {
            panel.beginSheetModal(for: window) { [weak self] response in
                if response == .OK, let url = panel.url {
                    self?.workspaceField.stringValue = url.path
                    self?.onChanged?()
                }
            }
        } else {
            if panel.runModal() == .OK, let url = panel.url {
                workspaceField.stringValue = url.path
                onChanged?()
            }
        }
    }
    
    @objc private func executorChanged() {
        onChanged?()
    }
    
    @objc private func removeClicked() {
        onRemove?(self)
    }
    
    func controlTextDidEndEditing(_ obj: Notification) {
        onChanged?()
    }
}
