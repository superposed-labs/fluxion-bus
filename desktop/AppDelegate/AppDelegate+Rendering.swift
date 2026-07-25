import AppKit
import Foundation

// MARK: - Menu-Bar Rendering & Formatting
//
// Split out of main.swift. Draws the status-bar title, builds the lazy dropdown
// (NSMenuDelegate), and holds the small formatting helpers shared by both. Pure
// view code — it reads in-memory state (currentProviders/envVals) and the provider-data helpers (filteredProviders) that remain in main.swift.
class RichMenuPanelWindow: NSPanel {
    override var canBecomeKey: Bool {
        return true
    }
}

extension AppDelegate {

    /// Renders the menu-bar title from in-memory state and manages the spinner.
    /// The dropdown is built lazily via `menuNeedsUpdate(_:)` when opened.
    func render() {
        let isNotchActive = envVals["FLUXION_NOTCH_MODE"] == "true"
        statusItem.isVisible = !isNotchActive

        let appearance = envVals["FLUXION_MENU_APPEARANCE"] ?? "rich"
        let providers = filteredProviders()

        if isUpgradingBackend {
            if statusItem.isVisible, let button = statusItem.button {
                button.image = nil
                button.imagePosition = .noImage
                
                let font = NSFont(name: "Menlo", size: 12.5) ?? NSFont.monospacedSystemFont(ofSize: 12.5, weight: .regular)
                let spinner = spinnerFrames[spinnerIndex]
                
                // Calculate breathing alpha based on system time (2-second cycle)
                let t = Date().timeIntervalSinceReferenceDate
                let wave = (sin(t * Double.pi) + 1.0) / 2.0
                let alpha = CGFloat(0.3 + 0.7 * wave)
                
                // Dot color matching the neon purple notch glow
                let dotColor = NSColor(red: 0.58, green: 0.38, blue: 0.95, alpha: alpha)
                
                let attrTitle = NSMutableAttributedString()
                attrTitle.append(attachmentForDot(color: dotColor, size: 11.5))
                attrTitle.append(NSAttributedString(string: " \(spinner)", attributes: [
                    .font: font,
                    .foregroundColor: NSColor.secondaryLabelColor
                ]))
                
                button.title = attrTitle.string
                button.attributedTitle = attrTitle
                button.toolTip = L10n.tr("menu.updating_components")
                statusItem.length = 38
            }
            startSpinner()
            return
        }

        if statusItem.isVisible {
            configureStatusItemInteraction(appearance: appearance)
            drawRichTitle(providers: providers, spinner: spinnerFrames[spinnerIndex])
            if providers.contains(where: { $0.status == "loading" }) {
                startSpinner()
            } else {
                stopSpinner()
            }
        } else {
            stopSpinner()
        }

        if let notch = notchWindowController {
            let oldCodex = notch.model.providers.first(where: { $0.provider == "codex" })
            let newCodex = providers.first(where: { $0.provider == "codex" })
            if let oldResets = oldCodex?.resets, let newResets = newCodex?.resets {
                if newResets.count > oldResets.count {
                    notch.triggerCreditGrantFlash(delta: newResets.count - oldResets.count)
                    // macOS native notification for credit grant (desktop-detected).
                    if (envVals["FLUXION_MENU_MACOS_NOTIFY_REFRESH"] ?? "true").lowercased() != "false" {
                        let delta = newResets.count - oldResets.count
                        deliverLocalNotification(
                            title: L10n.tr("notification.credit_grant.title"),
                            body: "Codex granted \(delta) reset credit\(delta > 1 ? "s" : "") · \(newResets.count) now available."
                        )
                    }
                }
            }

            notch.model.providers = providers
            notch.model.silentStyle = envVals["FLUXION_NOTCH_COLLAPSED_MODE"] ?? "all"
            notch.model.gaugeStyle = envVals["FLUXION_NOTCH_GAUGE_STYLE"] ?? "ring"
            notch.model.gaugeValue = envVals["FLUXION_NOTCH_GAUGE_VALUE_POSITION"] ?? "beside"
            notch.model.expandedStyle = envVals["FLUXION_NOTCH_SINGLE_MODEL_LAYOUT"] == "compact"
                ? "compact"
                : "detailed"
            notch.model.peekReset = envVals["FLUXION_NOTCH_PEEK_WINDOWS"] ?? "both"
            notch.model.pendingUpdateVersion = updaterController?.pendingUpdateVersion
            notch.repositionWindow()
        }

        // Deliver any pending macOS notifications queued by the scheduler daemon.
        checkAndDeliverPendingNotifications()
    }

    func configureStatusItemInteraction(appearance: String) {
        guard let button = statusItem.button else { return }
        if appearance == "rich" {
            statusItem.menu = nil
            button.target = self
            button.action = #selector(toggleRichMenu)
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        } else {
            closeRichMenu()
            statusItem.menu = statusMenu
            button.target = nil
            button.action = nil
            button.sendAction(on: [.leftMouseUp])
        }
    }

    @objc func toggleRichMenu() {
        let now = NSDate.timeIntervalSinceReferenceDate
        if now - lastRichMenuCloseTime < 0.25 {
            return
        }
        if let win = richMenuWindow, win.isVisible {
            closeRichMenu()
        } else {
            showRichMenu()
        }
    }

    func showRichMenu() {
        guard let button = statusItem.button, let buttonWindow = button.window else { return }
        closeRichMenu()

        let providers = filteredProviders()
        let content = RichMenuPanelView(
            providers: providers,
            todayStats: richTodayStats,
            isFetchingHistory: richTodayStats.isEmpty && richHistoryMessage == nil,
            historyMessage: richHistoryMessage,
            pendingUpdateVersion: updaterController?.pendingUpdateVersion
        )
        let height = content.preferredHeight()
        let width: CGFloat = 486
        let contentFrame = NSRect(x: 0, y: 0, width: width, height: height)
        let container = RichMenuContainerView(panelView: content, frame: contentFrame)
        content.onRefresh = { [weak self] in
            self?.closeRichMenu()
            self?.triggerRefresh()
        }
        content.onConsole = { [weak self] in
            self?.closeRichMenu()
            self?.openConsole()
        }
        content.onPreferences = { [weak self] in
            self?.closeRichMenu()
            self?.openPreferences()
        }
        content.onUpdate = { [weak self] in
            self?.presentAvailableUpdate()
        }
        content.onQuit = { [weak self] in
            self?.closeRichMenu()
            self?.quitApp()
        }
        content.onClose = { [weak self] in
            self?.closeRichMenu()
        }

        let panel = RichMenuPanelWindow(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.contentView = container
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.acceptsMouseMovedEvents = true
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]

        let buttonScreenRect = buttonWindow.convertToScreen(button.convert(button.bounds, to: nil))
        let screenFrame = buttonWindow.screen?.visibleFrame ?? NSScreen.main?.visibleFrame ?? .zero
        var x = buttonScreenRect.midX - width / 2
        x = max(screenFrame.minX + 8, min(x, screenFrame.maxX - width - 8))
        let y = buttonScreenRect.minY - height - 8
        panel.setFrameOrigin(NSPoint(x: x, y: y))

        richMenuWindow = panel
        richHistoryRetryCount = 0
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(richMenuDidResignKey),
            name: NSWindow.didResignKeyNotification,
            object: panel
        )
        richMenuEventMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            self?.closeRichMenu()
        }
        panel.makeKeyAndOrderFront(nil)
        panel.makeFirstResponder(content)
        fetchRichHistory()
    }

    /// Keep the optional update affordance in both custom menu surfaces in sync.
    func syncPendingUpdateUI() {
        let version = updaterController?.pendingUpdateVersion
        notchWindowController?.model.pendingUpdateVersion = version
        if let view = (richMenuWindow?.contentView as? RichMenuContainerView)?.panelView {
            view.pendingUpdateVersion = version
            view.needsDisplay = true
        }
    }

    /// Bring Sparkle's proven user-initiated update UI forward from any surface.
    @objc func presentAvailableUpdate() {
        closeRichMenu()
        let showUpdate = { [weak self] in
            guard let self else { return }
            NSApp.activate(ignoringOtherApps: true)
            self.updaterController?.checkForUpdates()
        }
        if let notch = notchWindowController, notch.model.notchState != .collapsed {
            notch.collapse(completion: showUpdate)
        } else {
            showUpdate()
        }
    }

    func closeRichMenu() {
        if let win = richMenuWindow {
            NotificationCenter.default.removeObserver(self, name: NSWindow.didResignKeyNotification, object: win)
        }
        if let monitor = richMenuEventMonitor {
            NSEvent.removeMonitor(monitor)
            richMenuEventMonitor = nil
        }
        richMenuWindow?.orderOut(nil)
        richMenuWindow = nil
        lastRichMenuCloseTime = NSDate.timeIntervalSinceReferenceDate
    }

    @objc func richMenuDidResignKey(_ notification: Notification) {
        closeRichMenu()
    }

    func fetchRichHistory() {
        guard !richHistoryFetchInFlight else { return }
        let port = envVals["FLUXION_UI_PORT"] ?? "8765"
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/usage/history?window=1d") else { return }
        richHistoryFetchInFlight = true
        richHistoryMessage = nil
        if let view = (richMenuWindow?.contentView as? RichMenuContainerView)?.panelView {
            view.isFetchingHistory = true
            view.historyMessage = nil
            view.needsDisplay = true
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 5
        if let token = envVals["FLUXION_UI_TOKEN"] {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            struct HistoryModelStat: Codable {
                let provider: String
                let cost: Double
                let total_tokens: Int
                let generated_tokens: Int
                let input_tokens: Int
                let output_tokens: Int
                let cache_creation_tokens: Int?
                let cache_read_tokens: Int?
            }
            struct HistoryPayload: Codable {
                let by_model: [HistoryModelStat]?
            }

            var parsed: [String: ProviderHistoryStats]? = nil
            if let data = data,
               error == nil,
               (response as? HTTPURLResponse)?.statusCode == 200,
               let payload = try? JSONDecoder().decode(HistoryPayload.self, from: data),
               let byModel = payload.by_model {
                var stats: [String: ProviderHistoryStats] = [:]
                for m in byModel {
                    let key = m.provider.lowercased()
                    let existing = stats[key] ?? ProviderHistoryStats(tokens: 0, input: 0, output: 0, cacheCreation: 0, cacheRead: 0, cost: 0.0)
                    stats[key] = ProviderHistoryStats(
                        tokens: existing.tokens + m.total_tokens,
                        input: existing.input + m.input_tokens,
                        output: existing.output + m.output_tokens,
                        cacheCreation: existing.cacheCreation + (m.cache_creation_tokens ?? 0),
                        cacheRead: existing.cacheRead + (m.cache_read_tokens ?? 0),
                        cost: existing.cost + m.cost
                    )
                }
                parsed = stats
            }

            DispatchQueue.main.async {
                guard let self = self else { return }
                self.richHistoryFetchInFlight = false

                if let stats = parsed {
                    self.richTodayStats = stats
                    self.richHistoryMessage = stats.isEmpty ? L10n.tr("menu.usage.no_today") : nil
                    self.richHistoryRetryCount = 0
                    self.updateRichHistoryView()
                } else if self.richHistoryRetryCount < 6 {
                    let delays: [TimeInterval] = [1.0, 1.5, 2.5, 4.0, 6.0, 8.0]
                    let delay = delays[min(self.richHistoryRetryCount, delays.count - 1)]
                    self.richHistoryRetryCount += 1
                    DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                        guard let self = self else { return }
                        if self.richMenuWindow?.isVisible == true || self.richTodayStats.isEmpty {
                            self.fetchRichHistory()
                        }
                    }
                } else {
                    if self.richTodayStats.isEmpty {
                        self.richHistoryMessage = L10n.tr("menu.usage.unavailable")
                    }
                    self.updateRichHistoryView()
                }
            }
        }.resume()
    }

    func updateRichHistoryView() {
        guard let container = richMenuWindow?.contentView as? RichMenuContainerView else { return }
        let view = container.panelView
        view.todayStats = richTodayStats
        view.historyMessage = richHistoryMessage
        view.isFetchingHistory = richTodayStats.isEmpty && richHistoryMessage == nil
        view.providers = filteredProviders()

        let oldFrame = richMenuWindow?.frame ?? .zero
        let newHeight = view.preferredHeight()
        container.frame = NSRect(x: 0, y: 0, width: oldFrame.width, height: newHeight)
        if oldFrame.height != newHeight {
            richMenuWindow?.setFrame(
                NSRect(x: oldFrame.minX, y: oldFrame.maxY - newHeight, width: oldFrame.width, height: newHeight),
                display: true
            )
        }
        view.needsDisplay = true
    }

    func startSpinner() {
        guard spinnerTimer == nil else { return }
        let t = Timer(timeInterval: 0.12, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.spinnerIndex = (self.spinnerIndex + 1) % self.spinnerFrames.count
            if self.isUpgradingBackend {
                // The upgrade indicator (breathing dot) is drawn by render().
                self.render()
            } else {
                // Title only — no disk read, no menu rebuild.
                self.drawRichTitle(providers: self.filteredProviders(), spinner: self.spinnerFrames[self.spinnerIndex])
            }
        }
        RunLoop.main.add(t, forMode: .common)
        spinnerTimer = t
    }

    func stopSpinner() {
        spinnerTimer?.invalidate()
        spinnerTimer = nil
    }

    func drawRichTitle(providers: [ProviderUsage], spinner: String) {
        guard let button = statusItem.button else { return }

        if providers.isEmpty {
            button.title = ""
            button.attributedTitle = NSAttributedString(string: "")
            button.toolTip = nil
            statusItem.length = 0
            return
        }

        let font = NSFont(name: "Menlo", size: 12.5) ?? NSFont.monospacedSystemFont(ofSize: 12.5, weight: .regular)
        let attrTitle = NSMutableAttributedString()

        for (idx, p) in providers.enumerated() {
            if idx > 0 {
                attrTitle.append(NSAttributedString(string: "  ", attributes: [.font: font, .foregroundColor: NSColor.tertiaryLabelColor]))
            }

            let prov = p.provider
            let visual = providerVisual(for: prov)
            let symbolColor = p.status == "loading" ? NSColor.secondaryLabelColor : (p.status != "ok" ? NSColor.systemRed : visual.brandColor)
            attrTitle.append(attachmentForDot(color: symbolColor, size: 11.5))

            let valueText: String
            let valueColor: NSColor
            if p.status == "loading" {
                valueText = " \(spinner)"
                valueColor = NSColor.secondaryLabelColor
            } else if p.status == "ok", let used = headline(p) {
                let remaining = max(0.0, round(100.0 - used))
                valueText = " \(Int(remaining))%"
                valueColor = usageColor(used: used)
            } else if p.status == "ok" {
                valueText = " --"
                valueColor = NSColor.secondaryLabelColor
            } else {
                valueText = " err"
                valueColor = NSColor.systemRed
            }
            attrTitle.append(NSAttributedString(string: valueText, attributes: [
                .font: font,
                .foregroundColor: valueColor
            ]))
        }

        let plainTitle = attrTitle.string.trimmingCharacters(in: .whitespacesAndNewlines)
        button.image = nil
        button.imagePosition = .noImage
        button.title = plainTitle
        button.attributedTitle = attrTitle
        button.toolTip = plainTitle
        statusItem.length = NSStatusItem.variableLength
    }

    // MARK: - NSMenuDelegate (lazy dropdown)
    func menuNeedsUpdate(_ menu: NSMenu) {
        rebuildDropdown(menu)
    }

    /// The latest installer output line as a small, gray, tightly truncated
    /// detail row — the headline item above it stays clean, and a verbose pip
    /// line can't blow the menu out to screen width. The lowest-information
    /// parts of a pip line are dropped first ("(from …)" parentheticals and
    /// "==<version>" pins), so most lines fit without an ellipsis:
    /// "Collecting pydantic-core==2.27.2 (from pydantic)" → "Collecting pydantic-core".
    func upgradeDetailTitle() -> NSAttributedString {
        var line = upgradeStatusLine
        if let parenthetical = line.range(of: " (") {
            line = String(line[..<parenthetical.lowerBound])
        }
        if let versionPin = line.range(of: "==") {
            line = String(line[..<versionPin.lowerBound])
        }
        // Cap near the headline's width so this row never becomes the widest
        // item — otherwise one long pip line stretches the whole menu and
        // every shorter update leaves trailing blank space.
        if line.count > 26 {
            line = String(line.prefix(26)) + "…"
        }
        return NSAttributedString(string: line, attributes: [
            .font: NSFont.systemFont(ofSize: 11),
            .foregroundColor: NSColor.secondaryLabelColor,
        ])
    }

    /// Record a bootstrap progress line and refresh the live detail row in
    /// place (rebuildDropdown only runs when the menu opens, but an already
    /// open NSMenu tracks item changes).
    func updateUpgradeProgress(_ line: String) {
        upgradeStatusLine = line
        upgradeMenuItem?.attributedTitle = upgradeDetailTitle()
        upgradeMenuItem?.isHidden = line.isEmpty
    }

    func rebuildDropdown(_ menu: NSMenu) {
        menu.removeAllItems()
        menu.autoenablesItems = false

        if isUpgradingBackend {
            let updatingItem = NSMenuItem(
                title: L10n.tr("menu.updating_components"),
                action: nil,
                keyEquivalent: ""
            )
            updatingItem.isEnabled = false
            menu.addItem(updatingItem)

            // Live installer output, one small gray line under the headline.
            let detailItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
            detailItem.isEnabled = false
            detailItem.attributedTitle = upgradeDetailTitle()
            detailItem.isHidden = upgradeStatusLine.isEmpty
            menu.addItem(detailItem)
            upgradeMenuItem = detailItem

            menu.addItem(NSMenuItem.separator())

            let quitItem = NSMenuItem(title: L10n.tr("app.quit"), action: #selector(quitApp), keyEquivalent: "")
            quitItem.target = self
            menu.addItem(quitItem)
            return
        }

        let spinner = spinnerFrames[spinnerIndex]
        let filtered = filteredProviders()

        // Column geometry for the quota rows: name, bar, "NN% left", countdown.
        //
        // The columns are tab stops measured in points, not runs of padding
        // spaces. Even in a monospace face the two aren't interchangeable: Menlo
        // draws a CJK glyph at 1.66 cells, so no whole number of spaces can put
        // a "5小时" row on the same column as an ASCII one. Tab stops sidestep
        // the whole cell-counting question — every column lands where it was
        // measured to land, in any language.
        let barWidth = 10
        let rowFont = menuRowFont
        func textWidth(_ string: String) -> CGFloat {
            (string as NSString).size(withAttributes: [.font: rowFont]).width
        }
        let cell = textWidth(" ")

        // Widest name and widest countdown decide where the columns sit, so
        // nothing is ever truncated or pushed off its stop.
        var maxNameW = cell * 12
        var maxResetW: CGFloat = 0
        for p in filtered where p.status == "ok" {
            if isCodexFiveHourTemporarilyUncapped(p) {
                maxNameW = max(maxNameW, textWidth(L10n.tr("preferences.window.5h")))
            }
            if p.provider == "codex", let resets = p.resets, resets.count > 0 {
                maxNameW = max(maxNameW, textWidth(L10n.tr("menu.resets")))
            }
            for w in p.windows {
                maxNameW = max(maxNameW, textWidth(menuQuotaWindowLabel(w, provider: p.provider)))
                maxResetW = max(maxResetW, textWidth(self.resetPhrase(window: w, fetchedAt: p.fetchedAt)))
            }
        }

        let barTab = cell * 2 + maxNameW + cell
        let valueTab = barTab + textWidth(self.barStr(used: 0, width: barWidth)) + cell * 2
        let resetTab = valueTab + textWidth("100% left") + cell * 2 + maxResetW
        let rowStyle = NSMutableParagraphStyle()
        rowStyle.tabStops = [
            NSTextTab(textAlignment: .left, location: barTab, options: [:]),
            NSTextTab(textAlignment: .left, location: valueTab, options: [:]),
            // Right stop: countdowns hang off the row's trailing edge, so "46m"
            // and "2d18h" end on the same column instead of starting on it.
            NSTextTab(textAlignment: .right, location: resetTab, options: [:])
        ]
        func rowAttributes(_ color: NSColor) -> [NSAttributedString.Key: Any] {
            [.font: rowFont, .foregroundColor: color, .paragraphStyle: rowStyle]
        }

        for p in filtered {
            let label = PROVIDER_NAMES[p.provider] ?? p.provider
            var planName: String? = nil
            if let accountLabel = p.accountLabel?.trimmingCharacters(in: .whitespacesAndNewlines), !accountLabel.isEmpty {
                var tier = accountLabel
                for prefix in ["Google AI ", "Google "] where tier.lowercased().hasPrefix(prefix.lowercased()) {
                    tier = String(tier.dropFirst(prefix.count))
                    break
                }
                if p.provider == "antigravity" {
                    if tier.lowercased().hasPrefix("antigravity ") {
                        tier = String(tier.dropFirst("Antigravity ".count))
                    }
                    if tier.lowercased().hasSuffix(" quota") {
                        tier = String(tier.dropLast(" Quota".count))
                    }
                }
                planName = tier.capitalized
            }
            let tag = planName != nil ? "  \(planName!)" : ""
            let visual = providerVisual(for: p.provider)

            let headItem = NSMenuItem()
            let titleText: String
            let titleColor: NSColor

            if p.status == "loading" {
                titleText = "\(label) — Loading... \(spinner)"
                titleColor = NSColor.secondaryLabelColor
            } else if p.status != "ok" {
                titleText = p.status == "error" ? "\(label)\(tag) — Error" : "\(label)\(tag) — \(p.status)"
                titleColor = NSColor.systemRed
            } else {
                titleText = "\(label)\(tag)"
                titleColor = NSColor.labelColor
            }

            let headTitle = NSMutableAttributedString(string: titleText, attributes: [
                .font: NSFont.systemFont(ofSize: 13, weight: .medium),
                .foregroundColor: titleColor
            ])
            headItem.attributedTitle = headTitle

            let iconColor = p.status == "loading" ? NSColor.secondaryLabelColor : (p.status != "ok" ? NSColor.systemRed : visual.brandColor)
            headItem.image = dotImage(color: iconColor)
            headItem.isEnabled = true
            menu.addItem(headItem)

            if p.status != "ok" {
                if let detail = p.detail {
                    let detailItem = NSMenuItem()
                    let detailTitle = NSMutableAttributedString(string: "  \(detail)", attributes: [
                        .font: NSFont(name: "Menlo", size: 11) ?? NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
                        .foregroundColor: NSColor.secondaryLabelColor
                    ])
                    detailItem.attributedTitle = detailTitle
                    detailItem.isEnabled = true
                    menu.addItem(detailItem)
                }
                menu.addItem(NSMenuItem.separator())
                continue
            }

            let addWindowItem = { (w: QuotaWindow, displayName: String) in
                let u = w.usedPercent
                var winSymbol = "circle.fill"
                let rawKey = ((w.label ?? "") + " " + (w.key ?? "")).lowercased()
                let isTimeWindow = w.resetsAt != nil || w.windowMinutes != nil ||
                                   rawKey.contains("5h") || rawKey.contains("7d") ||
                                   rawKey.contains("hour") || rawKey.contains("day") ||
                                   rawKey.contains("week") || rawKey.contains("wk") ||
                                   displayName.lowercased().contains("hour") ||
                                   displayName.lowercased().contains("day") ||
                                   displayName.lowercased().contains("week") ||
                                   displayName.lowercased().contains("5h") ||
                                   displayName.lowercased().contains("7d") ||
                                   displayName.contains("小时") ||
                                   displayName.contains("周")

                if w.key == "ai_credits" {
                    winSymbol = "creditcard.fill"
                } else if isTimeWindow {
                    if QuotaFormatter.isWindowIdle(w, fetchedAt: p.fetchedAt) {
                        winSymbol = "moon.zzz.fill"
                    } else {
                        winSymbol = "clock.fill"
                    }
                } else if u != nil {
                    winSymbol = "gauge.medium"
                }

                let winItem = NSMenuItem()
                winItem.isEnabled = true
                let winColor = usageColor(used: u)
                winItem.image = self.imageForSymbol(winSymbol, color: winColor)

                var rowStr = ""
                if u == nil && (w.total != nil || w.remaining != nil) {
                    // No percentage to bar: the raw balance takes the value
                    // column, leaving the bar column empty.
                    let amount: String
                    if let remaining = w.remaining, let tot = w.total {
                        amount = "\(Int(remaining)) / \(Int(tot))"
                    } else if let remaining = w.remaining {
                        amount = QuotaFormatter.formatCreditBalance(remaining, currency: w.currency)
                    } else {
                        amount = "—"
                    }
                    let expiry = QuotaFormatter.formatExpiryDate(w.expiresAt)
                        .map { L10n.tr("menu.credits.expires", $0) } ?? ""
                    if !expiry.isEmpty {
                        winItem.toolTip = expiry
                    }
                    rowStr = "  \(displayName)\t\t\(amount)"
                } else {
                    let leftPct = u == nil ? "—" : "\(Int(round(100.0 - u!)))% left"
                    let reset = self.resetPhrase(window: w, fetchedAt: p.fetchedAt)
                    let bar = self.barStr(used: u, width: barWidth)
                    rowStr = "  \(displayName)\t\(bar)\t\(leftPct)\t\(reset)"
                }

                winItem.attributedTitle = NSAttributedString(string: rowStr, attributes: rowAttributes(winColor))
                menu.addItem(winItem)
            }

            if p.provider == "antigravity" {
                let visibleWindows = p.windows.filter { $0.usedPercent != nil || $0.resetsAt != nil }
                let geminiWindows = visibleWindows.filter { !self.isExternalWindow($0) }
                let externalWindows = visibleWindows.filter { self.isExternalWindow($0) }

                if !geminiWindows.isEmpty {
                    let geminiHeader = NSMenuItem()
                    geminiHeader.attributedTitle = NSMutableAttributedString(string: "  GEMINI", attributes: [
                        .font: NSFont.systemFont(ofSize: 10, weight: .bold),
                        .foregroundColor: NSColor.secondaryLabelColor
                    ])
                    geminiHeader.isEnabled = false
                    menu.addItem(geminiHeader)

                    for w in geminiWindows {
                        addWindowItem(w, menuQuotaWindowLabel(w, provider: p.provider))
                    }
                }

                if !externalWindows.isEmpty {
                    let externalHeader = NSMenuItem()
                    externalHeader.attributedTitle = NSMutableAttributedString(string: "  EXTERNAL (Claude / GPT)", attributes: [
                        .font: NSFont.systemFont(ofSize: 10, weight: .bold),
                        .foregroundColor: NSColor.secondaryLabelColor
                    ])
                    externalHeader.isEnabled = false
                    menu.addItem(externalHeader)

                    for w in externalWindows {
                        addWindowItem(w, menuQuotaWindowLabel(w, provider: p.provider))
                    }
                }
            } else {
                if isCodexFiveHourTemporarilyUncapped(p) {
                    let uncappedItem = NSMenuItem()
                    uncappedItem.isEnabled = true
                    uncappedItem.image = self.imageForSymbol("infinity", color: NSColor.secondaryLabelColor)
                    // Skips the bar column and lands on the value column. The
                    // compact phrase, not the full "Temporarily uncapped":
                    // that column is only as wide as "NN% left", and the long
                    // form would stretch the whole menu to fit one row.
                    let rowStr = "  \(L10n.tr("preferences.window.5h"))\t\t\(L10n.tr("notch.five_hour_uncapped"))"
                    uncappedItem.attributedTitle = NSAttributedString(
                        string: rowStr,
                        attributes: rowAttributes(NSColor.secondaryLabelColor)
                    )
                    menu.addItem(uncappedItem)
                }
                for w in p.windows {
                    addWindowItem(w, menuQuotaWindowLabel(w, provider: p.provider))
                }
                if p.provider == "codex", let resets = p.resets, resets.count > 0 {
                    let resetsItem = NSMenuItem()
                    resetsItem.isEnabled = true
                    
                    let exp = resets.expiries.sorted()
                    let nextMs = exp.first ?? 0.0
                    let nextDays = Int(max(0.0, round(nextMs / 86400000.0)))
                    let soon = nextDays <= 7 && !exp.isEmpty
                    
                    let itemColor = soon ? NSColor.systemOrange : NSColor.secondaryLabelColor
                    resetsItem.image = self.imageForSymbol("arrow.counterclockwise", color: itemColor)
                    
                    let availableText = L10n.tr("menu.resets.available.compact", resets.count)
                    let infoPart = soon
                        ? "\(availableText) (\(L10n.tr("menu.resets.next_expires_in", nextDays)))"
                        : availableText
                    let rowStr = "  \(L10n.tr("menu.resets"))\t\t\(infoPart)"
                    resetsItem.attributedTitle = NSAttributedString(string: rowStr, attributes: rowAttributes(itemColor))
                    menu.addItem(resetsItem)
                }
            }

            menu.addItem(NSMenuItem.separator())
        }

        // Bottom Controls
        let refreshItem = NSMenuItem(title: L10n.tr("menu.refresh_now"), action: #selector(triggerRefresh), keyEquivalent: "")
        refreshItem.target = self
        refreshItem.image = imageForSymbol("arrow.clockwise", color: .labelColor)
        menu.addItem(refreshItem)

        let consoleItem = NSMenuItem(title: L10n.tr("menu.open_console"), action: #selector(openConsole), keyEquivalent: "")
        consoleItem.target = self
        consoleItem.image = imageForSymbol("macwindow", color: .labelColor)
        menu.addItem(consoleItem)

        if let version = updaterController?.pendingUpdateVersion {
            let updateItem = NSMenuItem(
                title: L10n.tr("update.available.menu"),
                action: #selector(presentAvailableUpdate),
                keyEquivalent: ""
            )
            updateItem.target = self
            updateItem.image = dotImage(color: NSColor(hex: "#9461F2"), diameter: 6)
            updateItem.toolTip = L10n.tr("update.available.hint", version)
            menu.addItem(updateItem)
        }

        let prefsItem = NSMenuItem(title: L10n.tr("menu.preferences"), action: #selector(openPreferences), keyEquivalent: "")
        prefsItem.target = self
        prefsItem.image = imageForSymbol("slider.horizontal.3", color: .labelColor)
        menu.addItem(prefsItem)

        menu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: L10n.tr("app.quit"), action: #selector(quitApp), keyEquivalent: "")
        quitItem.target = self
        menu.addItem(quitItem)
    }

    // MARK: - Helper Formatting
    func headline(_ p: ProviderUsage) -> Double? {
        // Scoped sub-limits only block one model, so the headline keeps
        // meaning "account-wide worst window" and skips them.
        let used = p.windows.filter { !$0.isScoped }.compactMap { $0.usedPercent }
        return used.max()
    }

    func barStr(used: Double?, width: Int = 10) -> String {
        guard let used = used else {
            return String(repeating: "□", count: width)
        }
        let remaining = max(0.0, 100.0 - used)
        let fill = max(0, min(width, Int(round(remaining / 100.0 * Double(width)))))
        return String(repeating: "■", count: fill) + String(repeating: "□", count: width - fill)
    }

    /// The monospace font the quota rows are drawn in. Column positions are
    /// measured against this exact font, so the two must never drift apart.
    var menuRowFont: NSFont {
        NSFont(name: "Menlo", size: 12) ?? NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
    }

    func resetPhrase(window: QuotaWindow, fetchedAt: String?) -> String {
        guard let resetsAt = window.resetsAt else { return "" }
        if QuotaFormatter.isWindowIdle(window, fetchedAt: fetchedAt) {
            let lang = L10n.resolvedAppLanguage
            let m = window.windowMinutes ?? 0
            if m == 300 {
                switch lang {
                case "zh-Hans": return "5小时"
                case "ja": return "5時間"
                default: return "5h"
                }
            }
            if m == 10080 {
                switch lang {
                case "zh-Hans": return "7天"
                case "ja": return "7日"
                default: return "7d"
                }
            }
            if m > 0 {
                if m % 1440 == 0 {
                    let days = m / 1440
                    switch lang {
                    case "zh-Hans": return "\(days)天"
                    case "ja": return "\(days)日"
                    default: return "\(days)d"
                    }
                }
                if m % 60 == 0 {
                    let hrs = m / 60
                    switch lang {
                    case "zh-Hans": return "\(hrs)小时"
                    case "ja": return "\(hrs)時間"
                    default: return "\(hrs)h"
                    }
                }
                return "\(m)m"
            }
            let label = window.label?.lowercased() ?? ""
            if label.contains("5") || label.contains("hour") || label.contains("rolling") {
                switch lang {
                case "zh-Hans": return "5小时"
                case "ja": return "5時間"
                default: return "5h"
                }
            }
            switch lang {
            case "zh-Hans": return "7天"
            case "ja": return "7日"
            default: return "7d"
            }
        }
        guard let targetDate = QuotaFormatter.parseISODate(resetsAt) else { return "" }
        let secs = targetDate.timeIntervalSinceNow
        if secs <= 0 {
            return L10n.tr("menu.reset.now")
        }
        let mins = Int(secs / 60)
        if mins < 60 {
            return L10n.tr("menu.reset.minutes", mins)
        } else {
            let hrs = mins / 60
            let remMins = mins % 60
            if hrs < 24 {
                return L10n.tr("menu.reset.hours", hrs, remMins)
            } else {
                let days = hrs / 24
                let remHrs = hrs % 24
                return L10n.tr("menu.reset.days", days, remHrs)
            }
        }
    }

    func imageForSymbol(_ name: String, color: NSColor) -> NSImage? {
        let config = NSImage.SymbolConfiguration(pointSize: 13, weight: .regular)
        guard let baseImage = NSImage(
            systemSymbolName: name,
            accessibilityDescription: nil
        )?.withSymbolConfiguration(config) else {
            return nil
        }

        let canvasSize = NSSize(width: 18, height: 18)
        let tinted = NSImage(size: canvasSize)
        tinted.lockFocus()

        let centeredRect = NSRect(
            x: floor((canvasSize.width - baseImage.size.width) / 2.0),
            y: floor((canvasSize.height - baseImage.size.height) / 2.0),
            width: baseImage.size.width,
            height: baseImage.size.height
        )
        baseImage.draw(in: centeredRect)

        color.set()
        centeredRect.fill(using: .sourceAtop)
        tinted.unlockFocus()
        tinted.isTemplate = false
        return tinted
    }

    func dotImage(color: NSColor, diameter: CGFloat = 13) -> NSImage {
        let canvasSize = NSSize(width: 18, height: 18)
        let img = NSImage(size: canvasSize)
        img.lockFocus()
        let rect = NSRect(x: (canvasSize.width - diameter) / 2,
                          y: (canvasSize.height - diameter) / 2,
                          width: diameter, height: diameter)
        color.set()
        NSBezierPath(ovalIn: rect).fill()
        img.unlockFocus()
        img.isTemplate = false
        return img
    }

    func attachmentForDot(color: NSColor, size: CGFloat = 13) -> NSAttributedString {
        let attachment = NSTextAttachment()
        let image = dotImage(color: color)
        image.size = NSSize(width: size, height: size)
        attachment.image = image
        attachment.bounds = NSRect(x: 0, y: -2.0, width: size, height: size)
        return NSAttributedString(attachment: attachment)
    }

    func isExternalWindow(_ w: QuotaWindow) -> Bool {
        let raw = ((w.label ?? "") + " " + (w.key ?? "")).lowercased()
        return raw.contains("external") || raw.contains("claude") || raw.contains("gpt")
    }

    func compactQuotaWindowLabel(_ w: QuotaWindow) -> String {
        // Scoped sub-limits keep their model name — collapsing them into
        // "Weekly" would make them indistinguishable from the weekly row.
        if w.isScoped {
            return "\(w.label ?? "Model") · wk"
        }
        let raw = ((w.label ?? "") + " " + (w.key ?? "")).lowercased()
        if raw.contains("weekly") || raw.contains("week") || raw.contains("wk") {
            return L10n.tr("preferences.window.weekly")
        }
        if raw.contains("5") || raw.contains("hour") || raw.contains("rolling") {
            return L10n.tr("preferences.window.5h")
        }
        return w.label ?? w.key ?? ""
    }

    func menuQuotaWindowLabel(_ w: QuotaWindow, provider: String) -> String {
        guard provider == "antigravity" else {
            return compactQuotaWindowLabel(w)
        }
        let raw = ((w.label ?? "") + " " + (w.key ?? "")).lowercased()
        let weekly = raw.contains("weekly") || raw.contains("week") || raw.contains("wk")
        if raw.contains("gemini") {
            return weekly ? "Gemini · wk" : "Gemini · 5h"
        }
        if raw.contains("external") || raw.contains("claude") || raw.contains("gpt") {
            return weekly ? "Claude/GPT · wk" : "Claude/GPT · 5h"
        }
        return compactQuotaWindowLabel(w)
    }
}
