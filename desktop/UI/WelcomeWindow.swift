import AppKit
import Foundation

// MARK: - First-Launch Welcome Window
/// One-time setup window shown after the first availability detection on a
/// fresh install. Everything here is optional and mirrors settings that live
/// in Preferences; skipping (closing) the window keeps the conservative
/// defaults. It never reappears because first launch is detected via the
/// FLUXION_USAGE_PROVIDERS key that `fluxion.detect_cli --initialize` writes.
class WelcomeWindow: NSObject, NSWindowDelegate {
    static let shared = WelcomeWindow()

    var window: NSWindow?

    /// The window starts in `.setup` on a first run without a backend (it
    /// hosts the backend install), then advances to `.onboarding` — the
    /// original one-time settings walkthrough — once the install succeeds.
    private enum Phase { case setup, onboarding }
    private var phase: Phase = .onboarding

    // Backend-setup phase state.
    private var setupSpinner: NSProgressIndicator?
    private var setupStatusLabel: NSTextField?
    private var setupLogScroll: NSScrollView?
    private var setupLogView: NSTextView?
    private var setupPrimaryButton: NSButton?
    private var setupLaterButton: NSButton?
    private var setupCopyButton: NSButton?
    private var setupFullLog = ""
    private var installRunning = false

    // Scaffold references so content changes can re-fit the window.
    private weak var scaffoldContent: NSStackView?
    private weak var scaffoldFooter: NSView?

    private var keychainSwitch: NSSwitch?
    private var weeklySwitch: NSSwitch!
    private var autoUpdateSwitch: NSSwitch?
    private var displaySegmented: NSSegmentedControl!
    private var displayHintLabel: NSTextField!
    private var executorPopup: NSPopUpButton?
    private var snapshot: AvailabilitySnapshot?

    /// Popup item order; must match the titles in `buildDetectionRows`.
    private let executorProviders = ["claude", "codex", "antigravity"]

    // Live-preview state: switching the display style repaints immediately by
    // mutating the in-memory env (never the .env file). "Get Started" persists
    // the choice; closing any other way restores these captured originals.
    private var originalNotchMode: String?
    private var originalAppearance: String?
    private var applied = false

    var appDelegate: AppDelegate {
        return NSApp.delegate as! AppDelegate
    }

    private let windowWidth: CGFloat = 560
    // Gap kept between the window and the edges of the usable screen area so
    // it never sits flush against the menu bar or Dock on short displays.
    private let screenMargin: CGFloat = 40

    func show() {
        if let win = window {
            win.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        phase = .onboarding
        let win = makeWindow()
        self.window = win
        installOnboardingContent(in: win)
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// First-run entry: the backend is not installed yet, so the window opens
    /// with the install step instead of the settings walkthrough.
    func showBackendSetup() {
        if let win = window {
            win.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        phase = .setup
        let win = makeWindow()
        self.window = win
        installSetupContent(in: win)
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Swap the setup content for the settings walkthrough in place, once the
    /// backend install and the first availability detection have finished.
    func advanceToOnboarding() {
        guard let win = window, phase == .setup else {
            show()
            return
        }
        phase = .onboarding
        clearSetupReferences()
        installOnboardingContent(in: win)
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Close the setup window without onboarding — the install restored an
    /// already-configured .env, so there is nothing left to walk through.
    func finishSetupWithoutOnboarding() {
        guard phase == .setup else { return }
        window?.close()
    }

    private func makeWindow() -> NSWindow {
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: windowWidth, height: 640),
            styleMask: [.titled, .closable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        win.title = L10n.tr("welcome.title")
        win.titlebarAppearsTransparent = true
        win.titleVisibility = .hidden
        win.isReleasedWhenClosed = false
        win.delegate = self
        return win
    }

    private func installOnboardingContent(in win: NSWindow) {
        snapshot = appDelegate.loadAvailabilitySnapshot()
        originalNotchMode = appDelegate.envVals["FLUXION_NOTCH_MODE"]
        originalAppearance = appDelegate.envVals["FLUXION_MENU_APPEARANCE"]
        applied = false

        let content = makeContentStack()
        content.addArrangedSubview(buildHeader())
        addSection(title: L10n.tr("welcome.section.detection"), rows: buildDetectionRows(), into: content)
        var alertRows: [NSView] = [buildWeeklyRow()]
        if let autoUpdateRow = buildAutoUpdateRow() { alertRows.append(autoUpdateRow) }
        addSection(title: L10n.tr("welcome.section.alerts"), rows: alertRows, into: content)
        addSection(title: L10n.tr("welcome.section.display"), rows: [buildDisplayRow()], into: content)
        addSection(title: L10n.tr("welcome.section.more"), rows: buildMoreRows(), into: content)
        pinContentWidths(content)
        installScaffold(content: content, footer: buildFooter(), in: win)

        // A fresh install preselects a style the user never clicked, so apply
        // it as a preview — otherwise the segmented control would claim Notch
        // while nothing is on screen. Deferred so the notch window is created
        // after this window finishes laying out. Skip/red-button close restores
        // the captured (empty) originals in windowWillClose, as usual.
        if originalNotchMode == nil, originalAppearance == nil {
            DispatchQueue.main.async { [weak self] in
                guard self?.displaySegmented != nil else { return }
                self?.displayStyleChanged()
            }
        }
    }

    private func installSetupContent(in win: NSWindow) {
        let content = makeContentStack()
        content.addArrangedSubview(buildHeader())
        addSection(
            title: L10n.tr("welcome.setup.section"),
            rows: [buildSetupRow()],
            into: content
        )
        pinContentWidths(content)
        installScaffold(content: content, footer: buildSetupFooter(), in: win)
    }

    private func makeContentStack() -> FlippedStackView {
        let content = FlippedStackView()
        content.orientation = .vertical
        content.alignment = .leading
        content.spacing = 18
        content.edgeInsets = NSEdgeInsets(top: 20, left: 28, bottom: 20, right: 28)
        content.translatesAutoresizingMaskIntoConstraints = false
        return content
    }

    private func pinContentWidths(_ content: NSStackView) {
        for view in content.arrangedSubviews {
            view.widthAnchor.constraint(
                equalTo: content.widthAnchor,
                constant: -(content.edgeInsets.left + content.edgeInsets.right)
            ).isActive = true
        }
    }

    private func installScaffold(content: FlippedStackView, footer: NSView, in win: NSWindow) {
        // Scroll the sections so displays too short to fit the full content
        // can still reach everything; the footer is pinned separately below so
        // its buttons are always visible, never behind the Dock.
        let scroll = NSScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.drawsBackground = false
        scroll.documentView = content
        NSLayoutConstraint.activate([
            content.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            content.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            content.trailingAnchor.constraint(equalTo: scroll.contentView.trailingAnchor),
            content.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
        ])

        // Pinned footer bar with a hairline separator above it.
        let separator = NSBox()
        separator.boxType = .separator
        separator.translatesAutoresizingMaskIntoConstraints = false

        let footerBar = NSView()
        footerBar.translatesAutoresizingMaskIntoConstraints = false
        footerBar.addSubview(footer)
        NSLayoutConstraint.activate([
            footer.topAnchor.constraint(equalTo: footerBar.topAnchor, constant: 14),
            footer.bottomAnchor.constraint(equalTo: footerBar.bottomAnchor, constant: -20),
            footer.leadingAnchor.constraint(equalTo: footerBar.leadingAnchor, constant: 28),
            footer.trailingAnchor.constraint(equalTo: footerBar.trailingAnchor, constant: -28),
        ])

        let container = NSView()
        container.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(scroll)
        container.addSubview(separator)
        container.addSubview(footerBar)
        NSLayoutConstraint.activate([
            scroll.topAnchor.constraint(equalTo: container.topAnchor),
            scroll.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            scroll.bottomAnchor.constraint(equalTo: separator.topAnchor),
            separator.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            separator.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            separator.bottomAnchor.constraint(equalTo: footerBar.topAnchor),
            footerBar.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            footerBar.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            footerBar.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            container.widthAnchor.constraint(equalToConstant: windowWidth),
        ])
        win.contentView = container
        scaffoldContent = content
        scaffoldFooter = footerBar

        sizeWindowToFit(win, keepTopLeft: false)
    }

    /// Size the window to fit the assembled content, but never taller than
    /// the visible screen area (minus the menu bar / Dock and a small
    /// margin). When content exceeds that, the scroll view takes over.
    /// `keepTopLeft` re-anchors in place for in-window content changes
    /// (revealing the failure log) instead of re-centering.
    private func sizeWindowToFit(_ win: NSWindow, keepTopLeft: Bool) {
        guard let container = win.contentView,
              let content = scaffoldContent,
              let footerBar = scaffoldFooter else { return }
        container.layoutSubtreeIfNeeded()
        // Account for any potential separators/spacing and add layout breathing room.
        var extraHeight: CGFloat = 8
        for subview in container.subviews {
            if let box = subview as? NSBox, box.boxType == .separator {
                extraHeight += box.fittingSize.height
            }
        }
        var desiredHeight = content.fittingSize.height + footerBar.fittingSize.height + extraHeight
        
        // Enforce a minimum window height of 420 to keep the layout spacious
        // and prevent scrollbars on small content.
        desiredHeight = max(desiredHeight, 420)
        
        let screen = win.screen ?? NSScreen.main
        guard let visible = screen?.visibleFrame else {
            win.setContentSize(NSSize(width: windowWidth, height: desiredHeight))
            win.center()
            return
        }
        let height = min(desiredHeight, visible.height - screenMargin)
        if keepTopLeft {
            let topLeft = NSPoint(x: win.frame.minX, y: win.frame.maxY)
            win.setContentSize(NSSize(width: windowWidth, height: height))
            win.setFrameTopLeftPoint(topLeft)
        } else {
            win.setContentSize(NSSize(width: windowWidth, height: height))
            // Center within the visible frame (not the full screen) so the
            // bottom edge always clears the Dock.
            win.setFrameOrigin(NSPoint(
                x: visible.midX - windowWidth / 2,
                y: visible.midY - height / 2
            ))
        }
    }

    // MARK: - Backend Setup Phase

    private func buildSetupRow() -> NSView {
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: 14, left: 14, bottom: 14, right: 14)
        stack.translatesAutoresizingMaskIntoConstraints = false

        let message = NSTextField(
            wrappingLabelWithString: L10n.tr("repository.setup_required.message")
        )
        message.font = NSFont.systemFont(ofSize: 13)
        message.isEditable = false
        message.isSelectable = false

        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
        spinner.translatesAutoresizingMaskIntoConstraints = false
        setupSpinner = spinner

        // Reserve the line with a space so starting the install does not
        // change the row height under the user's cursor.
        let status = NSTextField(labelWithString: " ")
        status.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        status.textColor = .secondaryLabelColor
        status.lineBreakMode = .byTruncatingTail
        status.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        setupStatusLabel = status

        let statusRow = NSStackView(views: [spinner, status])
        statusRow.orientation = .horizontal
        statusRow.spacing = 8
        statusRow.translatesAutoresizingMaskIntoConstraints = false

        // Full installer output, revealed only when the install fails.
        let logScroll = NSTextView.scrollableTextView()
        let logView = logScroll.documentView as! NSTextView
        logView.isEditable = false
        logView.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        logView.textContainerInset = NSSize(width: 6, height: 6)
        logScroll.translatesAutoresizingMaskIntoConstraints = false
        logScroll.heightAnchor.constraint(equalToConstant: 160).isActive = true
        logScroll.isHidden = true
        setupLogScroll = logScroll
        setupLogView = logView

        stack.addArrangedSubview(message)
        stack.addArrangedSubview(statusRow)
        stack.addArrangedSubview(logScroll)
        let innerWidth = -(stack.edgeInsets.left + stack.edgeInsets.right)
        for view in [message, statusRow, logScroll] {
            view.widthAnchor.constraint(
                equalTo: stack.widthAnchor, constant: innerWidth
            ).isActive = true
        }
        return stack
    }

    private func buildSetupFooter() -> NSView {
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.translatesAutoresizingMaskIntoConstraints = false

        let later = NSButton(
            title: L10n.tr("preferences.later"),
            target: self,
            action: #selector(skipAndClose)
        )
        later.bezelStyle = .rounded
        setupLaterButton = later

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)

        let copyLog = NSButton(
            title: L10n.tr("welcome.setup.copy_log"),
            target: self,
            action: #selector(copySetupLog)
        )
        copyLog.bezelStyle = .rounded
        copyLog.isHidden = true
        setupCopyButton = copyLog

        let start = NSButton(
            title: L10n.tr("repository.start_setup"),
            target: self,
            action: #selector(beginBackendInstall)
        )
        start.bezelStyle = .rounded
        start.keyEquivalent = "\r"
        setupPrimaryButton = start

        stack.addArrangedSubview(later)
        stack.addArrangedSubview(spacer)
        stack.addArrangedSubview(copyLog)
        stack.addArrangedSubview(start)
        return stack
    }

    @objc private func beginBackendInstall() {
        guard !installRunning else { return }
        installRunning = true
        setupPrimaryButton?.isEnabled = false
        setupLaterButton?.isEnabled = false
        setupCopyButton?.isHidden = true
        if setupLogScroll?.isHidden == false, let win = window {
            // Retry after a failure: collapse the log back to the compact
            // installing layout.
            setupLogScroll?.isHidden = true
            sizeWindowToFit(win, keepTopLeft: true)
        }
        setupStatusLabel?.textColor = .secondaryLabelColor
        setupStatusLabel?.stringValue = L10n.tr("repository.installing.message")
        setupSpinner?.startAnimation(nil)

        appDelegate.bootstrapBackend(progress: { [weak self] line in
            self?.setupStatusLabel?.stringValue = line
        }, completion: { [weak self] ok, fullOutput in
            guard let self = self else { return }
            self.installRunning = false
            self.setupFullLog = fullOutput
            if ok {
                // Keep the spinner going through availability detection; the
                // app delegate advances (or closes) this window when done.
                self.setupStatusLabel?.stringValue = L10n.tr("welcome.setup.detecting")
                self.appDelegate.backendSetupDidSucceed(
                    windowStillOpen: self.window != nil
                )
            } else {
                self.showSetupFailure(fullOutput)
            }
        })
    }

    func showSetupFailure(_ fullOutput: String) {
        setupSpinner?.stopAnimation(nil)
        setupStatusLabel?.textColor = .systemRed
        setupStatusLabel?.stringValue = L10n.tr("repository.install.failed.title")
        setupLogView?.string = fullOutput
        if let logView = setupLogView {
            logView.scrollRangeToVisible(NSRange(location: (logView.string as NSString).length, length: 0))
        }
        setupLogScroll?.isHidden = false
        setupCopyButton?.isHidden = false
        setupPrimaryButton?.title = L10n.tr("welcome.setup.retry")
        setupPrimaryButton?.isEnabled = true
        setupLaterButton?.isEnabled = true
        if let win = window {
            sizeWindowToFit(win, keepTopLeft: true)
        }
    }

    @objc private func copySetupLog() {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(setupFullLog, forType: .string)
    }

    private func clearSetupReferences() {
        setupSpinner = nil
        setupStatusLabel = nil
        setupLogScroll = nil
        setupLogView = nil
        setupPrimaryButton = nil
        setupLaterButton = nil
        setupCopyButton = nil
    }

    // MARK: - Sections

    private func buildHeader() -> NSView {
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 6
        stack.translatesAutoresizingMaskIntoConstraints = false

        let icon = NSImageView(image: NSApp.applicationIconImage)
        icon.translatesAutoresizingMaskIntoConstraints = false
        icon.widthAnchor.constraint(equalToConstant: 72).isActive = true
        icon.heightAnchor.constraint(equalToConstant: 72).isActive = true

        let title = NSTextField(labelWithString: L10n.tr("welcome.title"))
        title.font = NSFont.systemFont(ofSize: 24, weight: .bold)
        title.alignment = .center

        let subtitle = NSTextField(wrappingLabelWithString: L10n.tr("welcome.subtitle"))
        subtitle.font = NSFont.systemFont(ofSize: 12)
        subtitle.textColor = .secondaryLabelColor
        subtitle.alignment = .center
        subtitle.isEditable = false
        subtitle.isSelectable = false

        stack.addArrangedSubview(icon)
        stack.addArrangedSubview(title)
        stack.addArrangedSubview(subtitle)
        subtitle.widthAnchor.constraint(lessThanOrEqualTo: stack.widthAnchor).isActive = true
        return stack
    }

    /// One status row per usage provider, plus the one-click Keychain opt-in
    /// when the Claude token is missing (the common case on a fresh macOS
    /// install, where Claude Code keeps its login in the Keychain).
    private func buildDetectionRows() -> [NSView] {
        var rows: [NSView] = []
        let providers: [(String, String)] = [
            ("claude", "Claude Code"),
            ("codex", "ChatGPT (Codex)"),
            ("antigravity", "Antigravity"),
        ]
        var claudeTokenMissing = false
        for (idx, (key, name)) in providers.enumerated() {
            let entry = snapshot?.usage[key]
            let ready = entry?.status == "ok"
            // Only nudge for Keychain access when Claude Code is actually
            // installed. Without this gate a machine with no Claude at all
            // still surfaces an orange "needs access" row and the Keychain
            // opt-in, which reads as if Claude were present but locked.
            if key == "claude",
               snapshot?.executors["claude"]?.status == "available",
               let entry = entry, entry.status != "ok",
               entry.detail.hasPrefix("No Claude OAuth token") {
                claudeTokenMissing = true
            }
            let needsAccess = key == "claude" && claudeTokenMissing
            let status = NSTextField(labelWithString: ready
                ? L10n.tr("welcome.status.ready")
                : (needsAccess
                    ? L10n.tr("welcome.status.needs_access")
                    : L10n.tr("welcome.status.unavailable")))
            status.font = NSFont.systemFont(ofSize: 12, weight: .medium)
            status.textColor = ready ? .systemGreen : (needsAccess ? .systemOrange : .secondaryLabelColor)
            rows.append(CardRow(title: name, desc: nil, control: status, isFirst: idx == 0))
        }
        if claudeTokenMissing {
            let toggle = NSSwitch()
            toggle.state = .on
            keychainSwitch = toggle
            rows.insert(CardRow(
                title: L10n.tr("welcome.keychain.title"),
                desc: L10n.tr("welcome.keychain.desc"),
                control: toggle,
                isFirst: false
            ), at: 1)
        }

        // First-launch detection silently picked a default executor
        // (codex → claude → antigravity); surface that choice so the user can
        // change it before their first task. CLIs that were not detected stay
        // listed but disabled, unless nothing was detected at all.
        let popup = NSPopUpButton(frame: .zero, pullsDown: false)
        popup.autoenablesItems = false
        popup.addItems(withTitles: ["Claude Code", "ChatGPT (Codex)", "Antigravity"])
        let available = executorProviders.filter { snapshot?.executors[$0]?.status == "available" }
        if !available.isEmpty {
            for (idx, provider) in executorProviders.enumerated() {
                popup.item(at: idx)?.isEnabled = available.contains(provider)
            }
        }
        let configured = (appDelegate.envVals["FLUXION_DEFAULT_EXECUTOR"] ?? "codex").lowercased()
        // Show the executor that will actually run: keep the configured default
        // when it is installed, otherwise snap to the first installed one so the
        // picker never presents a greyed, missing CLI as the selected default.
        let effective = available.contains(configured) ? configured : (available.first ?? configured)
        popup.selectItem(at: executorProviders.firstIndex(of: effective) ?? 1)
        executorPopup = popup
        rows.append(CardRow(
            title: L10n.tr("welcome.executor.title"),
            desc: L10n.tr("welcome.executor.desc"),
            control: popup,
            isFirst: false
        ))
        return rows
    }

    private func buildWeeklyRow() -> NSView {
        weeklySwitch = NSSwitch()
        // Opt-in: off by default so a fresh install never schedules reminders
        // unless the user turns them on here.
        weeklySwitch.state = .off
        return CardRow(
            title: L10n.tr("welcome.weekly.title"),
            desc: L10n.tr("welcome.weekly.desc"),
            control: weeklySwitch,
            isFirst: true
        )
    }

    /// Auto-update opt-in — shown only in distribution builds where the
    /// updater is configured. Default off; the user must turn it on here (or in
    /// Preferences) before any background check runs. Persisted by "Get Started".
    private func buildAutoUpdateRow() -> NSView? {
        guard appDelegate.updaterController?.isConfigured == true else { return nil }
        let sw = NSSwitch()
        sw.state = .off
        autoUpdateSwitch = sw
        return CardRow(
            title: L10n.tr("preferences.auto_update"),
            desc: L10n.tr("preferences.auto_update.desc"),
            control: sw,
            isFirst: false
        )
    }

    /// Whether the display the user is actually on has a physical notch.
    /// Mirrors NotchWindowController.findTargetScreen() (mouse first, then
    /// main) so the recommendation matches where the notch would render: on a
    /// docked external display Notch mode draws a floating pill instead of
    /// hiding in the safe area, which is not what we want to preselect. The
    /// >25pt threshold is the same one getPhysicalNotchInfo() uses.
    private static var hasBuiltInNotch: Bool {
        let mouseLoc = NSEvent.mouseLocation
        let screen = NSScreen.screens.first(where: { $0.frame.contains(mouseLoc) }) ?? NSScreen.main
        return (screen?.safeAreaInsets.top ?? 0) > 25
    }

    private func buildDisplayRow() -> NSView {
        displaySegmented = NSSegmentedControl(
            labels: [
                L10n.tr("welcome.display.native"),
                L10n.tr("welcome.display.rich"),
                L10n.tr("welcome.display.notch"),
            ],
            trackingMode: .selectOne,
            target: self,
            action: #selector(displayStyleChanged)
        )
        displaySegmented.segmentStyle = .rounded
        if originalNotchMode == "true" {
            displaySegmented.selectedSegment = 2
        } else if originalNotchMode == nil, originalAppearance == nil {
            // Fresh install: recommend the style that fits the hardware. A
            // built-in notch makes Notch mode nearly free (its collapsed strip
            // hides in the safe area), so it leads there; everywhere else Rich
            // shows what the app is actually for. installOnboardingContent()
            // previews whichever one this picks so the screen matches it.
            displaySegmented.selectedSegment = Self.hasBuiltInNotch ? 2 : 1
        } else {
            // Only an explicit "native" selects Native: a half-written .env
            // (notch key present, appearance key absent) must agree with the
            // renderer, which falls back to rich.
            displaySegmented.selectedSegment = originalAppearance == "native" ? 0 : 1
        }
        displaySegmented.segmentDistribution = .fillEqually
        displaySegmented.translatesAutoresizingMaskIntoConstraints = false

        // The collapsed menu-bar title is identical for Native and Rich (they
        // differ in what opens on click), so switching between them shows no
        // visible change — this hint fills that gap. Space is reserved up
        // front (alpha, not isHidden) so the window never has to resize.
        displayHintLabel = NSTextField(wrappingLabelWithString: L10n.tr("welcome.display.hint"))
        displayHintLabel.font = NSFont.systemFont(ofSize: 11)
        displayHintLabel.textColor = .secondaryLabelColor
        displayHintLabel.isEditable = false
        displayHintLabel.isSelectable = false
        displayHintLabel.alphaValue = 0

        let controlStack = NSStackView()
        controlStack.orientation = .vertical
        controlStack.alignment = .leading
        controlStack.spacing = 6
        controlStack.translatesAutoresizingMaskIntoConstraints = false
        controlStack.addArrangedSubview(displaySegmented)
        controlStack.addArrangedSubview(displayHintLabel)
        displaySegmented.widthAnchor.constraint(equalTo: controlStack.widthAnchor).isActive = true
        displayHintLabel.widthAnchor.constraint(equalTo: controlStack.widthAnchor).isActive = true

        return CardRowStacked(
            title: L10n.tr("welcome.display.title"),
            desc: L10n.tr("welcome.display.desc"),
            control: controlStack,
            isFirst: true
        )
    }

    /// Discovery-only pointers, no state: IM channels need third-party
    /// credentials so setup lives in Preferences → Messaging, and the
    /// sub-agent MCP server is configured in the MCP client, not here.
    private func buildMoreRows() -> [NSView] {
        let imButton = NSButton(
            title: L10n.tr("welcome.im.button"),
            target: self,
            action: #selector(openMessagingPreferences)
        )
        imButton.bezelStyle = .rounded
        let imRow = CardRow(
            title: L10n.tr("welcome.im.title"),
            desc: L10n.tr("welcome.im.desc"),
            control: imButton,
            isFirst: true
        )

        let mcpButton = NSButton(
            title: L10n.tr("welcome.mcp.button"),
            target: self,
            action: #selector(openMcpDocs)
        )
        mcpButton.bezelStyle = .rounded
        let mcpRow = CardRow(
            title: L10n.tr("welcome.mcp.title"),
            desc: L10n.tr("welcome.mcp.desc"),
            control: mcpButton,
            isFirst: false
        )

        return [imRow, mcpRow]
    }

    private func buildFooter() -> NSView {
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.translatesAutoresizingMaskIntoConstraints = false

        let skip = NSButton(title: L10n.tr("welcome.button.skip"), target: self, action: #selector(skipAndClose))
        skip.bezelStyle = .rounded

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)

        let start = NSButton(title: L10n.tr("welcome.button.start"), target: self, action: #selector(applyAndClose))
        start.bezelStyle = .rounded
        start.keyEquivalent = "\r"

        stack.addArrangedSubview(skip)
        stack.addArrangedSubview(spacer)
        stack.addArrangedSubview(start)
        return stack
    }

    private func addSection(title: String, rows: [NSView], into documentStack: NSStackView) {
        let card = CardView()
        for row in rows {
            card.stackView.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: card.stackView.widthAnchor).isActive = true
        }

        let sectionStack = NSStackView()
        sectionStack.orientation = .vertical
        sectionStack.alignment = .leading
        sectionStack.spacing = 7
        sectionStack.translatesAutoresizingMaskIntoConstraints = false

        let header = NSTextField(labelWithString: title.uppercased())
        header.font = NSFont.systemFont(ofSize: 11, weight: .semibold)
        header.textColor = Palette.sectionHeader

        sectionStack.addArrangedSubview(header)
        sectionStack.addArrangedSubview(card)
        card.widthAnchor.constraint(equalTo: sectionStack.widthAnchor).isActive = true
        documentStack.addArrangedSubview(sectionStack)
    }

    // MARK: - Actions

    /// Repaint the menu bar / notch with the selected style so the choice is
    /// visible while the window is still open. In-memory only — `render()`
    /// reads `envVals`, and nothing writes them back to disk.
    @objc private func displayStyleChanged() {
        previewDisplayStyle(
            notch: displaySegmented.selectedSegment == 2 ? "true" : "false",
            appearance: displaySegmented.selectedSegment == 1 ? "rich" : "native"
        )
        // Native/Rich look the same until the menu bar icon is clicked; point
        // that out. The notch preview is self-evident, so no hint there.
        displayHintLabel.animator().alphaValue = displaySegmented.selectedSegment == 2 ? 0 : 1
    }

    private func previewDisplayStyle(notch: String?, appearance: String?) {
        if let notch = notch {
            appDelegate.envVals["FLUXION_NOTCH_MODE"] = notch
        } else {
            appDelegate.envVals.removeValue(forKey: "FLUXION_NOTCH_MODE")
        }
        if let appearance = appearance {
            appDelegate.envVals["FLUXION_MENU_APPEARANCE"] = appearance
        } else {
            appDelegate.envVals.removeValue(forKey: "FLUXION_MENU_APPEARANCE")
        }

        let notchEnabled = notch == "true"
        if notchEnabled, appDelegate.notchWindowController == nil {
            appDelegate.notchWindowController = NotchWindowController()
            appDelegate.notchWindowController?.show()
        } else if !notchEnabled, let controller = appDelegate.notchWindowController {
            controller.hide()
            appDelegate.notchWindowController = nil
        }
        appDelegate.render()
    }

    // The welcome window stays open behind Preferences so toggles made here
    // can still be committed with "Get Started" afterwards.
    @objc private func openMessagingPreferences() {
        PreferencesWindow.shared.show()
        PreferencesWindow.shared.switchPage(to: "messaging")
    }

    @objc private func openMcpDocs() {
        guard let url = URL(string: "https://github.com/superposed-labs/fluxion-bus/blob/main/docs/mcp.md") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func skipAndClose() {
        window?.close()
    }

    @objc private func applyAndClose() {
        applied = true
        var updates: [String: String] = [:]
        let keychainEnabled = keychainSwitch?.state == .on

        if keychainEnabled {
            updates["FLUXION_CLAUDE_USAGE_KEYCHAIN"] = "true"
            updates["FLUXION_CLAUDE_USAGE_AUTO_REFRESH"] = "true"
            // First-launch detection could not see the Keychain token, so
            // claude was left out of the initialized provider list; add it
            // back now that the user has allowed Keychain access.
            var provs = appDelegate.activeProviderKeys()
            if !provs.contains("claude") {
                provs.insert("claude", at: 0)
                updates["FLUXION_USAGE_PROVIDERS"] = provs.joined(separator: ",")
            }
        }

        if let popup = executorPopup, popup.indexOfSelectedItem >= 0,
           popup.indexOfSelectedItem < executorProviders.count {
            updates["FLUXION_DEFAULT_EXECUTOR"] = executorProviders[popup.indexOfSelectedItem]
        }

        let notchEnabled = displaySegmented.selectedSegment == 2
        updates["FLUXION_NOTCH_MODE"] = notchEnabled ? "true" : "false"
        updates["FLUXION_MENU_APPEARANCE"] = displaySegmented.selectedSegment == 1 ? "rich" : "native"

        appDelegate.saveEnv(updates: updates)

        if let autoUpdateSwitch = autoUpdateSwitch {
            appDelegate.updaterController?.automaticallyChecksForUpdates =
                (autoUpdateSwitch.state == .on)
        }

        if weeklySwitch.state == .on {
            appDelegate.ensureNotificationPermission()
            // Watch the weekly window of every provider that reports usage
            // (including Claude when Keychain access was just granted).
            var watched = Set(
                (snapshot?.usage ?? [:])
                    .filter { $0.value.status == "ok" }
                    .map { $0.key }
            )
            if keychainEnabled { watched.insert("claude") }
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                guard let self = self else { return }
                for provider in watched.sorted() {
                    _ = self.appDelegate.setAutoPingMode(provider: provider, mode: "7d")
                }
            }
        }

        // The live preview already put the notch/menu-bar state in place;
        // saveEnv persisted the same values, so no display work is left here.

        // Re-run detection so the availability snapshot (and Preferences copy)
        // reflects the Keychain grant, then repaint with the new settings.
        appDelegate.runAvailabilityDetection(initialize: false) { [weak self] in
            guard let self = self else { return }
            self.appDelegate.render()
            self.appDelegate.refresh(force: true)
        }

        window?.close()
    }

    func windowWillClose(_ notification: Notification) {
        // Any close that isn't "Get Started" (Skip, red button) discards the
        // live preview and restores the display exactly as it was. The setup
        // phase never previews display styles, so there is nothing to
        // restore there — and its originals were never captured.
        if phase == .onboarding, !applied {
            previewDisplayStyle(notch: originalNotchMode, appearance: originalAppearance)
        }
        clearSetupReferences()
        window = nil
    }
}
