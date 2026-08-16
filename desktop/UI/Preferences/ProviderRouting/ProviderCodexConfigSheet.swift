import AppKit
import Foundation

// Read-only preview of the Codex config files Fluxion manages, opened from the
// Codex integration section. Writing them is ProviderInstallRepairSheet.swift.

// MARK: - Codex Configuration Sheet Controller (Read-Only)

class ProviderCodexConfigSheetController: NSObject, NSWindowDelegate {
    private let state: ProviderRoutingState
    private let gatewayRunning: Bool
    private let parentWindow: NSWindow
    private let preferencesWindow: PreferencesWindow
    private let onDismiss: () -> Void
    private let onRepair: () -> Void
    private let onStartGateway: () -> Void

    private var sheetWindow: NSWindow?

    init(
        state: ProviderRoutingState,
        gatewayRunning: Bool,
        parentWindow: NSWindow,
        preferencesWindow: PreferencesWindow,
        onDismiss: @escaping () -> Void,
        onRepair: @escaping () -> Void,
        onStartGateway: @escaping () -> Void
    ) {
        self.state = state
        self.gatewayRunning = gatewayRunning
        self.parentWindow = parentWindow
        self.preferencesWindow = preferencesWindow
        self.onDismiss = onDismiss
        self.onRepair = onRepair
        self.onStartGateway = onStartGateway
        super.init()
    }

    func show() {
        let windowRect = NSRect(x: 0, y: 0, width: 620, height: 660)
        let win = NSWindow(
            contentRect: windowRect,
            styleMask: [.titled],
            backing: .buffered,
            defer: false
        )
        win.delegate = self
        win.isReleasedWhenClosed = false
        win.title = ""
        win.titlebarAppearsTransparent = true
        win.titleVisibility = .hidden
        win.backgroundColor = Palette.windowBackground
        self.sheetWindow = win

        let rootView = NSView(frame: windowRect)
        rootView.translatesAutoresizingMaskIntoConstraints = false
        rootView.wantsLayer = true
        rootView.layer?.backgroundColor = Palette.windowBackground.cgColor
        win.contentView = rootView

        let headerView = buildHeaderView()
        rootView.addSubview(headerView)

        let footerView = buildFooterView()
        rootView.addSubview(footerView)

        let scrollView = NSScrollView()
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.hasVerticalScroller = true
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.drawsBackground = false
        rootView.addSubview(scrollView)

        NSLayoutConstraint.activate([
            headerView.topAnchor.constraint(equalTo: rootView.topAnchor, constant: 18),
            headerView.leadingAnchor.constraint(equalTo: rootView.leadingAnchor, constant: 24),
            headerView.trailingAnchor.constraint(equalTo: rootView.trailingAnchor, constant: -24),

            footerView.bottomAnchor.constraint(equalTo: rootView.bottomAnchor, constant: -16),
            footerView.leadingAnchor.constraint(equalTo: rootView.leadingAnchor, constant: 24),
            footerView.trailingAnchor.constraint(equalTo: rootView.trailingAnchor, constant: -24),

            scrollView.topAnchor.constraint(equalTo: headerView.bottomAnchor, constant: 12),
            scrollView.leadingAnchor.constraint(equalTo: rootView.leadingAnchor, constant: 24),
            scrollView.trailingAnchor.constraint(equalTo: rootView.trailingAnchor, constant: -24),
            scrollView.bottomAnchor.constraint(equalTo: footerView.topAnchor, constant: -12)
        ])

        let clipView = NSClipView()
        clipView.drawsBackground = false
        scrollView.contentView = clipView

        let contentStack = NSStackView()
        contentStack.orientation = .vertical
        contentStack.alignment = .leading
        contentStack.spacing = 16
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        scrollView.documentView = contentStack

        NSLayoutConstraint.activate([
            contentStack.topAnchor.constraint(equalTo: clipView.topAnchor),
            contentStack.leadingAnchor.constraint(equalTo: clipView.leadingAnchor),
            contentStack.trailingAnchor.constraint(equalTo: clipView.trailingAnchor),
            contentStack.widthAnchor.constraint(equalTo: scrollView.widthAnchor, constant: -14)
        ])

        populateContent(into: contentStack)

        parentWindow.beginSheet(win) { [weak self] _ in
            self?.sheetWindow = nil
            self?.onDismiss()
        }
    }

    func dismiss() {
        guard let win = sheetWindow else { return }
        parentWindow.endSheet(win, returnCode: .cancel)
        win.orderOut(nil)
    }

    private func buildHeaderView() -> NSView {
        let header = NSStackView()
        header.orientation = .vertical
        header.alignment = .leading
        header.spacing = 4
        header.translatesAutoresizingMaskIntoConstraints = false

        let titleLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.config_sheet.title"))
        titleLabel.font = NSFont.systemFont(ofSize: 16.5, weight: .bold)
        titleLabel.textColor = Palette.primaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        header.addArrangedSubview(titleLabel)

        let subLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.config_sheet.sub"))
        subLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        subLabel.textColor = Palette.secondaryText
        subLabel.isEditable = false
        subLabel.isSelectable = false
        subLabel.isBordered = false
        subLabel.drawsBackground = false
        header.addArrangedSubview(subLabel)

        return header
    }

    private func populateContent(into stack: NSStackView) {
        let codexHome = state.codex.home ?? "~/.codex"
        let host = preferencesWindow.appDelegate.envVals["FLUXION_PROVIDER_HOST"] ?? "127.0.0.1"
        let port = preferencesWindow.appDelegate.envVals["FLUXION_PROVIDER_PORT"] ?? "8787"
        let gatewayAddr = "http://\(host):\(port)/v1"

        // No per-role placeholder names: what a role would run is whatever
        // its route resolves to, and naming a model here was a guess that went
        // stale on its own schedule. An unrouted role says so instead.
        let roleItems: [ProviderCodexRoleConfigItem] = ["auto", "worker", "explorer", "reviewer"]
            .map { buildRoleItem(roleSlug: $0) }

        let okCount = roleItems.filter { $0.status == .installed }.count
        let totalCount = roleItems.count
        let hasCorrupt = roleItems.contains { if case .unreadable = $0.status { return true } else { return false } }

        // Banner
        if hasCorrupt {
            let corruptRole = roleItems.first(where: { if case .unreadable = $0.status { return true } else { return false } })
            let banner = ProviderBannerView(
                tone: .error,
                title: L10n.tr("preferences.provider.codex.config_sheet.banner.corrupt.title"),
                body: String(format: L10n.tr("preferences.provider.codex.config_sheet.banner.corrupt.body"), corruptRole?.file ?? "reviewer.toml")
            )
            stack.addArrangedSubview(banner)
            banner.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        } else if okCount < totalCount {
            let banner = ProviderBannerView(
                tone: .warn,
                title: String(format: L10n.tr("preferences.provider.codex.config_sheet.banner.partial.title"), okCount, totalCount),
                body: L10n.tr("preferences.provider.codex.config_sheet.banner.partial.body")
            )
            stack.addArrangedSubview(banner)
            banner.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        } else if !gatewayRunning {
            let banner = ProviderBannerView(
                tone: .warn,
                title: L10n.tr("preferences.provider.codex.config_sheet.banner.offline.title"),
                body: L10n.tr("preferences.provider.codex.config_sheet.banner.offline.body")
            )
            stack.addArrangedSubview(banner)
            banner.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        }

        // Section 1: Files & Gateway
        let sec1 = NSStackView()
        sec1.orientation = .vertical
        sec1.alignment = .leading
        sec1.spacing = 6
        sec1.translatesAutoresizingMaskIntoConstraints = false

        let sec1Header = NSStackView()
        sec1Header.orientation = .horizontal
        sec1Header.alignment = .firstBaseline
        sec1Header.spacing = 6

        let sec1Title = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.config_sheet.section.files"))
        sec1Title.font = NSFont.systemFont(ofSize: 12, weight: .semibold)
        sec1Title.textColor = Palette.primaryText
        sec1Title.isEditable = false
        sec1Title.isSelectable = false
        sec1Title.isBordered = false
        sec1Title.drawsBackground = false
        sec1Header.addArrangedSubview(sec1Title)

        let sec1Hint = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.config_sheet.section.files_hint"))
        sec1Hint.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        sec1Hint.textColor = Palette.secondaryText
        sec1Hint.isEditable = false
        sec1Hint.isSelectable = false
        sec1Hint.isBordered = false
        sec1Hint.drawsBackground = false
        sec1Header.addArrangedSubview(sec1Hint)
        sec1.addArrangedSubview(sec1Header)

        let card1 = CardView()
        card1.translatesAutoresizingMaskIntoConstraints = false

        let row1 = ProviderPathRowView(
            label: L10n.tr("preferences.provider.codex.config_sheet.file.config"),
            path: state.codex.configPath.isEmpty ? "\(codexHome)/config.toml" : state.codex.configPath,
            hasFinder: true
        )
        card1.stackView.addArrangedSubview(row1)
        row1.widthAnchor.constraint(equalTo: card1.stackView.widthAnchor).isActive = true

        let sep1 = makeSeparator()
        card1.stackView.addArrangedSubview(sep1)
        sep1.widthAnchor.constraint(equalTo: card1.stackView.widthAnchor).isActive = true

        let row2 = ProviderPathRowView(
            label: L10n.tr("preferences.provider.codex.config_sheet.file.roles"),
            path: "\(codexHome)/agents/",
            hasFinder: true
        )
        card1.stackView.addArrangedSubview(row2)
        row2.widthAnchor.constraint(equalTo: card1.stackView.widthAnchor).isActive = true

        let sep2 = makeSeparator()
        card1.stackView.addArrangedSubview(sep2)
        sep2.widthAnchor.constraint(equalTo: card1.stackView.widthAnchor).isActive = true

        let statusDot = NSView()
        statusDot.translatesAutoresizingMaskIntoConstraints = false
        statusDot.wantsLayer = true
        statusDot.layer?.cornerRadius = 3
        statusDot.layer?.backgroundColor = gatewayRunning ? NSColor.systemGreen.cgColor : Palette.secondaryText.cgColor
        NSLayoutConstraint.activate([
            statusDot.widthAnchor.constraint(equalToConstant: 6),
            statusDot.heightAnchor.constraint(equalToConstant: 6)
        ])

        let statusText = gatewayRunning
            ? String(format: L10n.tr("preferences.provider.codex.config_sheet.status.responding"), okCount, totalCount)
            : L10n.tr("preferences.provider.codex.config_sheet.status.not_responding")
        let statusLabel = NSTextField(labelWithString: statusText)
        statusLabel.font = NSFont.systemFont(ofSize: 11, weight: .medium)
        statusLabel.textColor = gatewayRunning ? .systemGreen : Palette.secondaryText
        statusLabel.isEditable = false
        statusLabel.isSelectable = false
        statusLabel.isBordered = false
        statusLabel.drawsBackground = false

        let statusStack = NSStackView()
        statusStack.orientation = .horizontal
        statusStack.alignment = .centerY
        statusStack.spacing = 5
        statusStack.addArrangedSubview(statusDot)
        statusStack.addArrangedSubview(statusLabel)

        let row3 = ProviderPathRowView(
            label: L10n.tr("preferences.provider.codex.config_sheet.file.gateway"),
            path: gatewayAddr,
            copyLabel: L10n.tr("preferences.provider.copy"),
            hasFinder: false,
            statusView: statusStack
        )
        card1.stackView.addArrangedSubview(row3)
        row3.widthAnchor.constraint(equalTo: card1.stackView.widthAnchor).isActive = true

        sec1.addArrangedSubview(card1)
        card1.widthAnchor.constraint(equalTo: sec1.widthAnchor).isActive = true
        stack.addArrangedSubview(sec1)
        sec1.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true

        // Section 2: Fluxion Roles
        let sec2 = NSStackView()
        sec2.orientation = .vertical
        sec2.alignment = .leading
        sec2.spacing = 6
        sec2.translatesAutoresizingMaskIntoConstraints = false

        let sec2Header = NSStackView()
        sec2Header.orientation = .vertical
        sec2Header.alignment = .leading
        sec2Header.spacing = 2

        let sec2Title = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.config_sheet.section.roles"))
        sec2Title.font = NSFont.systemFont(ofSize: 12, weight: .semibold)
        sec2Title.textColor = Palette.primaryText
        sec2Title.isEditable = false
        sec2Title.isSelectable = false
        sec2Title.isBordered = false
        sec2Title.drawsBackground = false
        sec2Header.addArrangedSubview(sec2Title)

        let sec2Hint = NSTextField(wrappingLabelWithString: L10n.tr("preferences.provider.codex.config_sheet.section.roles_hint"))
        sec2Hint.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        sec2Hint.textColor = Palette.secondaryText
        sec2Hint.cell?.wraps = true
        sec2Hint.cell?.isScrollable = false
        sec2Hint.maximumNumberOfLines = 0
        sec2Hint.lineBreakMode = .byWordWrapping
        sec2Hint.isEditable = false
        sec2Hint.isSelectable = false
        sec2Hint.isBordered = false
        sec2Hint.drawsBackground = false
        sec2Hint.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        sec2Hint.setContentHuggingPriority(.defaultLow, for: .horizontal)
        sec2Header.addArrangedSubview(sec2Hint)
        sec2.addArrangedSubview(sec2Header)
        sec2Header.widthAnchor.constraint(equalTo: sec2.widthAnchor).isActive = true

        let card2 = CardView()
        card2.translatesAutoresizingMaskIntoConstraints = false

        for (index, item) in roleItems.enumerated() {
            if index > 0 {
                let sep = makeSeparator()
                card2.stackView.addArrangedSubview(sep)
                sep.widthAnchor.constraint(equalTo: card2.stackView.widthAnchor).isActive = true
            }
            let roleRow = ProviderRoleRowView(item: item)
            card2.stackView.addArrangedSubview(roleRow)
            roleRow.widthAnchor.constraint(equalTo: card2.stackView.widthAnchor).isActive = true
        }

        sec2.addArrangedSubview(card2)
        card2.widthAnchor.constraint(equalTo: sec2.widthAnchor).isActive = true
        stack.addArrangedSubview(sec2)
        sec2.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
    }

    private func buildRoleItem(roleSlug: String) -> ProviderCodexRoleConfigItem {
        let fullRole = "fluxion_\(roleSlug)"
        let file = "agents/\(roleSlug).toml"
        let codexHome = state.codex.home ?? "~/.codex"

        let codexRole = state.codex.roles.first(where: { $0.role == roleSlug || $0.role == fullRole })
        let isInstalled = codexRole?.installed ?? false
        let codexModel = codexRole?.model.isEmpty == false
            ? codexRole!.model
            : (state.executors.first { $0.executor.lowercased() == "codex" }?.recommendedModel ?? "")

        let matchingRoute = state.routes.first(where: { $0.role == roleSlug || $0.role == fullRole })
        let candidate = matchingRoute?.candidates.first ?? ""
        let routeModel = candidate.isEmpty
            ? L10n.tr("preferences.provider.codex.role.unrouted")
            : preferencesWindow.formatCandidateModelName(candidate)
        let routeExecutor = candidate.isEmpty
            ? "Antigravity"
            : preferencesWindow.formatCandidateExecutorName(candidate, state: state)

        let status: ProviderCodexRoleStatus
        let why: String?
        if isInstalled && codexRole?.readable == false {
            status = .unreadable(reason: codexRole?.error)
            why = codexRole?.error
        } else if isInstalled {
            status = .installed
            why = nil
        } else {
            status = .notInstalled
            why = "No file at \(codexHome)/\(file)."
        }

        return ProviderCodexRoleConfigItem(
            role: fullRole,
            file: file,
            route: fullRole,
            model: routeModel,
            executor: routeExecutor,
            codexModel: codexModel,
            status: status,
            why: why
        )
    }

    private func buildFooterView() -> NSView {
        let footerStack = NSStackView()
        footerStack.orientation = .horizontal
        footerStack.alignment = .centerY
        footerStack.distribution = .fill
        footerStack.translatesAutoresizingMaskIntoConstraints = false

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        footerStack.addArrangedSubview(spacer)

        let btnStack = NSStackView()
        btnStack.orientation = .horizontal
        btnStack.alignment = .centerY
        btnStack.spacing = 8

        let okCount = state.codex.roles.filter(\.healthy).count
        let totalCount = state.codex.roles.isEmpty ? 4 : state.codex.roles.count
        let needsRepair = okCount < totalCount

        if needsRepair {
            let closeBtn = NSButton(
                title: L10n.tr("preferences.provider.codex.config_sheet.close"),
                target: self,
                action: #selector(closeAction)
            )
            closeBtn.bezelStyle = .rounded
            closeBtn.controlSize = .regular
            btnStack.addArrangedSubview(closeBtn)

            let repairBtn = ProviderAccentButton()
            repairBtn.title = L10n.tr("preferences.provider.codex.install_repair")
            repairBtn.target = self
            repairBtn.action = #selector(repairAction)
            btnStack.addArrangedSubview(repairBtn)
        } else if !gatewayRunning {
            let repairBtn = NSButton(
                title: L10n.tr("preferences.provider.codex.install_repair"),
                target: self,
                action: #selector(repairAction)
            )
            repairBtn.bezelStyle = .rounded
            repairBtn.controlSize = .regular
            btnStack.addArrangedSubview(repairBtn)

            let startBtn = ProviderAccentButton()
            startBtn.title = L10n.tr("preferences.provider.codex.config_sheet.start_gateway")
            startBtn.target = self
            startBtn.action = #selector(startGatewayAction)
            btnStack.addArrangedSubview(startBtn)
        } else {
            let repairBtn = NSButton(
                title: L10n.tr("preferences.provider.codex.install_repair"),
                target: self,
                action: #selector(repairAction)
            )
            repairBtn.bezelStyle = .rounded
            repairBtn.controlSize = .regular
            btnStack.addArrangedSubview(repairBtn)

            let closeBtn = ProviderAccentButton()
            closeBtn.title = L10n.tr("preferences.provider.codex.config_sheet.close")
            closeBtn.target = self
            closeBtn.action = #selector(closeAction)
            btnStack.addArrangedSubview(closeBtn)
        }

        footerStack.addArrangedSubview(btnStack)
        return footerStack
    }

    private func makeSeparator() -> NSView {
        let sep = NSView()
        sep.translatesAutoresizingMaskIntoConstraints = false
        sep.wantsLayer = true
        sep.layer?.backgroundColor = Palette.separator.cgColor
        sep.heightAnchor.constraint(equalToConstant: 0.5).isActive = true
        return sep
    }

    @objc private func closeAction() {
        dismiss()
    }

    @objc private func repairAction() {
        dismiss()
        onRepair()
    }

    @objc private func startGatewayAction() {
        dismiss()
        onStartGateway()
    }
}
