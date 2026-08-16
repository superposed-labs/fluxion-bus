import AppKit
import Foundation

// The collapsible sections below the gateway card — role routing, Codex
// integration, executor/model catalogs, model updates, and the not-configured
// placeholder. All of them are rendered by renderProviderRouting() in
// PreferencesWindow+ProviderRouting.swift.

extension PreferencesWindow {

    // MARK: - 3. Role Routing Section

    func addProviderRoleRoutingSection(_ state: ProviderRoutingState, into stack: NSStackView) {
        let coreRoles = ["auto", "worker", "explorer", "reviewer", "compaction"]
        let mainRoutes = state.routes.filter { coreRoles.contains($0.role) }
        let advancedRoutes = state.routes.filter { !coreRoles.contains($0.role) }

        // Main Role Routing Card
        var mainRows: [NSView] = []
        for (index, route) in mainRoutes.enumerated() {
            let row = makeRouteRowView(route, state: state, isFirst: index == 0)
            mainRows.append(row)
        }

        let sectionTitle = L10n.tr("preferences.provider.section.routes")
        let sectionStack = addSection(title: sectionTitle, rows: mainRows, into: stack)

        // Advanced Routes disclosure below main card
        if !advancedRoutes.isEmpty {
            let advContainer = NSStackView()
            advContainer.orientation = .vertical
            advContainer.alignment = .leading
            advContainer.spacing = 8
            advContainer.edgeInsets = NSEdgeInsets(top: 4, left: 6, bottom: 0, right: 0)
            advContainer.translatesAutoresizingMaskIntoConstraints = false

            let advDisclosure = ProviderDisclosureButton(
                title: String(format: L10n.tr("preferences.provider.advanced_count"), advancedRoutes.count),
                isOpen: providerAdvancedRoutesOpen,
                target: self,
                action: #selector(toggleAdvancedRoutes(_:))
            )
            advContainer.addArrangedSubview(advDisclosure)

            if providerAdvancedRoutesOpen {
                var advRows: [NSView] = []
                for (index, route) in advancedRoutes.enumerated() {
                    let row = makeRouteRowView(route, state: state, isFirst: index == 0)
                    advRows.append(row)
                }
                let advCard = CardView()
                for row in advRows {
                    advCard.stackView.addArrangedSubview(row)
                    row.widthAnchor.constraint(equalTo: advCard.stackView.widthAnchor).isActive = true
                }
                advContainer.addArrangedSubview(advCard)
                advCard.widthAnchor.constraint(equalTo: advContainer.widthAnchor).isActive = true
            }

            sectionStack.addArrangedSubview(advContainer)
            advContainer.widthAnchor.constraint(equalTo: sectionStack.widthAnchor).isActive = true
        }
    }

    private func makeRouteRowView(
        _ route: ProviderRouteState,
        state: ProviderRoutingState,
        isFirst: Bool
    ) -> NSView {
        let rowView = NSView()
        rowView.translatesAutoresizingMaskIntoConstraints = false

        // Separator
        let separator = NSView()
        separator.translatesAutoresizingMaskIntoConstraints = false
        separator.wantsLayer = true
        separator.layer?.backgroundColor = Palette.separator.cgColor
        rowView.addSubview(separator)

        NSLayoutConstraint.activate([
            separator.topAnchor.constraint(equalTo: rowView.topAnchor),
            separator.leadingAnchor.constraint(equalTo: rowView.leadingAnchor, constant: 16),
            separator.trailingAnchor.constraint(equalTo: rowView.trailingAnchor),
            separator.heightAnchor.constraint(equalToConstant: 0.5)
        ])
        separator.isHidden = isFirst

        let contentStack = NSStackView()
        contentStack.orientation = .vertical
        contentStack.alignment = .leading
        contentStack.spacing = 3
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        rowView.addSubview(contentStack)

        NSLayoutConstraint.activate([
            contentStack.topAnchor.constraint(equalTo: rowView.topAnchor, constant: 13),
            contentStack.leadingAnchor.constraint(equalTo: rowView.leadingAnchor, constant: 16),
            contentStack.trailingAnchor.constraint(equalTo: rowView.trailingAnchor, constant: -16),
            contentStack.bottomAnchor.constraint(equalTo: rowView.bottomAnchor, constant: -13)
        ])

        let topLine = NSStackView()
        topLine.orientation = .horizontal
        topLine.alignment = .centerY
        topLine.spacing = 8
        topLine.translatesAutoresizingMaskIntoConstraints = false

        let nameLabel = NSTextField(labelWithString: formatRoleDisplayName(route.role))
        nameLabel.font = NSFont.systemFont(ofSize: 13.5, weight: .semibold)
        nameLabel.textColor = Palette.primaryText
        nameLabel.isEditable = false
        nameLabel.isSelectable = false
        nameLabel.isBordered = false
        nameLabel.drawsBackground = false
        topLine.addArrangedSubview(nameLabel)

        if !route.inheritsAuto {
            let slug = formatRoleSlug(route.role)
            let slugBadge = ProviderBadgeView(text: slug)
            topLine.addArrangedSubview(slugBadge)
        }

        if route.role == "explorer" || route.role == "reviewer" {
            let roTag = ProviderTagView(text: L10n.tr("preferences.provider.badge.readonly"), isBold: true)
            topLine.addArrangedSubview(roTag)
        }

        if route.inheritsAuto {
            let inheritLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.route.inherits_auto"))
            inheritLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
            inheritLabel.textColor = Palette.secondaryText
            inheritLabel.isEditable = false
            inheritLabel.isSelectable = false
            inheritLabel.isBordered = false
            inheritLabel.drawsBackground = false
            topLine.addArrangedSubview(inheritLabel)
        }

        let spacer = NSView()
        spacer.translatesAutoresizingMaskIntoConstraints = false
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        topLine.addArrangedSubview(spacer)

        let editButton = NSButton(
            title: !providerCatalogsLoaded && providerCatalogsLoading
                ? L10n.tr("preferences.provider.catalogs.loading_short")
                : (route.inheritsAuto
                    ? L10n.tr("preferences.provider.change")
                    : L10n.tr("preferences.provider.edit")),
            target: self,
            action: #selector(editProviderRoute(_:))
        )
        editButton.bezelStyle = .rounded
        editButton.controlSize = .small
        editButton.identifier = NSUserInterfaceItemIdentifier(route.role)
        editButton.isEnabled = providerCatalogsLoaded
            || providerCatalogsUnavailable
            || !providerCatalogsLoading
        topLine.addArrangedSubview(editButton)

        contentStack.addArrangedSubview(topLine)
        topLine.widthAnchor.constraint(equalTo: contentStack.widthAnchor).isActive = true

        var lastTopView: NSView = topLine

        // Line 2: Role Description
        let descText = formatRoleDescription(route.role)
        if !descText.isEmpty {
            let descLabel = NSTextField(wrappingLabelWithString: descText)
            descLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
            descLabel.textColor = Palette.secondaryText
            descLabel.cell?.wraps = true
            descLabel.cell?.isScrollable = false
            descLabel.maximumNumberOfLines = 0
            descLabel.lineBreakMode = .byWordWrapping
            descLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            descLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
            descLabel.isEditable = false
            descLabel.isSelectable = false
            descLabel.isBordered = false
            descLabel.drawsBackground = false
            contentStack.addArrangedSubview(descLabel)
            lastTopView = descLabel
        }

        // Line 3 & 4: Primary and Fallback key-value layout (if not inherited compaction)
        if !route.inheritsAuto {
            let kvStack = NSStackView()
            kvStack.orientation = .vertical
            kvStack.alignment = .leading
            kvStack.spacing = 5
            kvStack.translatesAutoresizingMaskIntoConstraints = false

            // Primary row
            let primaryRow = NSStackView()
            primaryRow.orientation = .horizontal
            primaryRow.alignment = .centerY
            primaryRow.spacing = 8

            let primaryKey = NSTextField(labelWithString: L10n.tr("preferences.provider.primary"))
            primaryKey.font = NSFont.systemFont(ofSize: 11, weight: .regular)
            primaryKey.textColor = Palette.sectionHeader
            primaryKey.isEditable = false
            primaryKey.isSelectable = false
            primaryKey.isBordered = false
            primaryKey.drawsBackground = false
            primaryKey.translatesAutoresizingMaskIntoConstraints = false
            primaryKey.widthAnchor.constraint(equalToConstant: 80).isActive = true
            primaryRow.addArrangedSubview(primaryKey)

            let primaryCandidate = route.candidates.first ?? ""
            let primaryModelName = formatCandidateModelNameWithoutEffort(primaryCandidate)
            let primaryValue = NSTextField(labelWithString: primaryModelName)
            primaryValue.font = NSFont.systemFont(ofSize: 12.5, weight: .medium)
            primaryValue.textColor = Palette.primaryText
            primaryValue.lineBreakMode = .byTruncatingTail
            primaryValue.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            primaryValue.isEditable = false
            primaryValue.isSelectable = false
            primaryValue.isBordered = false
            primaryValue.drawsBackground = false
            primaryRow.addArrangedSubview(primaryValue)

            // Same badge the model picker uses, and shown for every agent:
            // Claude's and Codex's efforts live in the route rather than the
            // model id, so they were previously invisible here.
            let primaryEffort = effortForCandidate(primaryCandidate, in: route)
            if !primaryEffort.isEmpty {
                primaryRow.addArrangedSubview(
                    ProviderTagView(text: primaryEffort.capitalized, isAccent: true))
            }

            let executorName = formatCandidateExecutorName(primaryCandidate, state: state)
            let execTag = ProviderTagView(text: executorName)
            primaryRow.addArrangedSubview(execTag)

            kvStack.addArrangedSubview(primaryRow)

            // Fallback row
            let fallbackRow = NSStackView()
            fallbackRow.orientation = .horizontal
            fallbackRow.alignment = .centerY
            fallbackRow.spacing = 8

            let fallbackKey = NSTextField(labelWithString: L10n.tr("preferences.provider.fallback"))
            fallbackKey.font = NSFont.systemFont(ofSize: 11, weight: .regular)
            fallbackKey.textColor = Palette.sectionHeader
            fallbackKey.isEditable = false
            fallbackKey.isSelectable = false
            fallbackKey.isBordered = false
            fallbackKey.drawsBackground = false
            fallbackKey.translatesAutoresizingMaskIntoConstraints = false
            fallbackKey.widthAnchor.constraint(equalToConstant: 80).isActive = true
            fallbackRow.addArrangedSubview(fallbackKey)

            let fallbackNames = route.fallback.map { candidate -> String in
                let name = formatCandidateModelNameWithoutEffort(candidate)
                let effort = effortForCandidate(candidate, in: route)
                return effort.isEmpty ? name : "\(name) · \(effort.capitalized)"
            }
            let fallbackString = fallbackNames.isEmpty
                ? L10n.tr("preferences.provider.none")
                : fallbackNames.joined(separator: "  →  ")

            let fallbackValue = NSTextField(labelWithString: fallbackString)
            fallbackValue.font = NSFont.systemFont(ofSize: 12, weight: .regular)
            fallbackValue.textColor = Palette.secondaryText
            fallbackValue.lineBreakMode = .byTruncatingTail
            fallbackValue.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            fallbackValue.isEditable = false
            fallbackValue.isSelectable = false
            fallbackValue.isBordered = false
            fallbackValue.drawsBackground = false
            fallbackRow.addArrangedSubview(fallbackValue)

            kvStack.addArrangedSubview(fallbackRow)

            contentStack.addArrangedSubview(kvStack)
            contentStack.setCustomSpacing(7, after: lastTopView)
        }

        return rowView
    }

    // MARK: - 4. Codex Integration Section

    func addProviderCodexSection(_ state: ProviderRoutingState, into stack: NSStackView) {
        let card = CardView()
        card.translatesAutoresizingMaskIntoConstraints = false

        let innerStack = NSStackView()
        innerStack.orientation = .vertical
        innerStack.alignment = .leading
        innerStack.spacing = 0
        innerStack.translatesAutoresizingMaskIntoConstraints = false
        card.stackView.addArrangedSubview(innerStack)
        innerStack.widthAnchor.constraint(equalTo: card.stackView.widthAnchor).isActive = true

        // Header row
        let headerRow = NSView()
        headerRow.translatesAutoresizingMaskIntoConstraints = false

        let rightStack = NSStackView()
        rightStack.orientation = .horizontal
        rightStack.alignment = .centerY
        rightStack.spacing = 8
        rightStack.translatesAutoresizingMaskIntoConstraints = false
        headerRow.addSubview(rightStack)

        let pillTone: ProviderPillTone = state.codex.installed
            ? (providerGatewayRunning ? .ok : .idle)
            : .warn
        let pillText = state.codex.installed
            ? (providerGatewayRunning ? L10n.tr("preferences.provider.codex.healthy") : L10n.tr("preferences.provider.codex.idle"))
            : L10n.tr("preferences.provider.codex.needs_repair")
        let pill = ProviderPillView(tone: pillTone, text: pillText)
        rightStack.addArrangedSubview(pill)

        let manageBtn = NSButton(
            title: providerCodexIntegrationOpen
                ? L10n.tr("preferences.provider.codex.hide")
                : L10n.tr("preferences.provider.codex.manage"),
            target: self,
            action: #selector(toggleCodexIntegration(_:))
        )
        manageBtn.bezelStyle = .rounded
        manageBtn.controlSize = .small
        rightStack.addArrangedSubview(manageBtn)

        NSLayoutConstraint.activate([
            rightStack.trailingAnchor.constraint(equalTo: headerRow.trailingAnchor, constant: -16),
            rightStack.centerYAnchor.constraint(equalTo: headerRow.centerYAnchor)
        ])

        let labelStack = NSStackView()
        labelStack.orientation = .vertical
        labelStack.alignment = .leading
        labelStack.spacing = 2
        labelStack.translatesAutoresizingMaskIntoConstraints = false
        headerRow.addSubview(labelStack)

        NSLayoutConstraint.activate([
            labelStack.leadingAnchor.constraint(equalTo: headerRow.leadingAnchor, constant: 16),
            labelStack.topAnchor.constraint(equalTo: headerRow.topAnchor, constant: 13),
            labelStack.bottomAnchor.constraint(equalTo: headerRow.bottomAnchor, constant: -13),
            labelStack.trailingAnchor.constraint(lessThanOrEqualTo: rightStack.leadingAnchor, constant: -12)
        ])

        let titleLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.title"))
        titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .medium)
        titleLabel.textColor = Palette.primaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        labelStack.addArrangedSubview(titleLabel)

        let installedRoles = state.codex.roles.filter(\.healthy).count
        let totalRoles = state.codex.roles.isEmpty ? 4 : state.codex.roles.count
        let connStatus = providerGatewayRunning
            ? L10n.tr("preferences.provider.codex.check.connected")
            : L10n.tr("preferences.provider.codex.check.disconnected")
        let tokenStatus = state.tokenAvailable == true
            ? L10n.tr("preferences.provider.codex.check.available")
            : L10n.tr("preferences.provider.codex.check.missing")
        let summaryText = L10n.tr(
            "preferences.provider.codex.summary",
            installedRoles,
            totalRoles,
            tokenStatus,
            connStatus)

        let summaryLabel = NSTextField(labelWithString: summaryText)
        summaryLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        summaryLabel.textColor = Palette.secondaryText
        summaryLabel.isEditable = false
        summaryLabel.isSelectable = false
        summaryLabel.isBordered = false
        summaryLabel.drawsBackground = false
        labelStack.addArrangedSubview(summaryLabel)

        innerStack.addArrangedSubview(headerRow)
        headerRow.widthAnchor.constraint(equalTo: innerStack.widthAnchor).isActive = true

        // Expanded checklist & footer
        if providerCodexIntegrationOpen {
            let checks: [(name: String, ok: Bool, isMono: Bool)] = [
                (L10n.tr("preferences.provider.codex.check.provider_config"), state.codex.managedBlock, false),
                ("fluxion_auto", state.codex.roles.first(where: { $0.role == "auto" })?.healthy ?? false, true),
                ("fluxion_worker", state.codex.roles.first(where: { $0.role == "worker" })?.healthy ?? false, true),
                ("fluxion_explorer", state.codex.roles.first(where: { $0.role == "explorer" })?.healthy ?? false, true),
                ("fluxion_reviewer", state.codex.roles.first(where: { $0.role == "reviewer" })?.healthy ?? false, true),
                (
                    L10n.tr("preferences.provider.codex.check.auth_token"),
                    state.tokenAvailable == true,
                    false
                ),
                (L10n.tr("preferences.provider.codex.check.gateway_conn"), providerGatewayRunning, false),
            ]

            for check in checks {
                let row = NSView()
                row.translatesAutoresizingMaskIntoConstraints = false

                let checkSep = NSView()
                checkSep.translatesAutoresizingMaskIntoConstraints = false
                checkSep.wantsLayer = true
                checkSep.layer?.backgroundColor = Palette.separator.cgColor
                row.addSubview(checkSep)
                NSLayoutConstraint.activate([
                    checkSep.topAnchor.constraint(equalTo: row.topAnchor),
                    checkSep.leadingAnchor.constraint(equalTo: row.leadingAnchor, constant: 16),
                    checkSep.trailingAnchor.constraint(equalTo: row.trailingAnchor),
                    checkSep.heightAnchor.constraint(equalToConstant: 0.5)
                ])

                let title = NSTextField(labelWithString: check.name)
                title.font = check.isMono
                    ? NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
                    : NSFont.systemFont(ofSize: 12.5, weight: .regular)
                title.textColor = Palette.primaryText
                title.isEditable = false
                title.isSelectable = false
                title.isBordered = false
                title.drawsBackground = false
                title.translatesAutoresizingMaskIntoConstraints = false
                row.addSubview(title)

                let statusStack = NSStackView()
                statusStack.orientation = .horizontal
                statusStack.alignment = .centerY
                statusStack.spacing = 6
                statusStack.translatesAutoresizingMaskIntoConstraints = false
                row.addSubview(statusStack)

                let statusDot = NSView()
                statusDot.translatesAutoresizingMaskIntoConstraints = false
                statusDot.wantsLayer = true
                statusDot.layer?.cornerRadius = 3
                statusDot.layer?.backgroundColor = check.ok
                    ? NSColor.systemGreen.cgColor
                    : Palette.secondaryText.cgColor
                NSLayoutConstraint.activate([
                    statusDot.widthAnchor.constraint(equalToConstant: 6),
                    statusDot.heightAnchor.constraint(equalToConstant: 6)
                ])
                statusStack.addArrangedSubview(statusDot)

                let statusLabelText = check.name == L10n.tr("preferences.provider.codex.check.gateway_conn")
                    ? (providerGatewayRunning ? L10n.tr("preferences.provider.codex.check.connected") : L10n.tr("preferences.provider.codex.check.disconnected"))
                    : (check.name == L10n.tr("preferences.provider.codex.check.auth_token")
                        ? (check.ok ? L10n.tr("preferences.provider.codex.check.available") : L10n.tr("preferences.provider.codex.check.missing"))
                        : (check.ok ? L10n.tr("preferences.provider.codex.check.installed") : L10n.tr("preferences.provider.codex.check.not_installed")))

                let statusLabel = NSTextField(labelWithString: statusLabelText)
                statusLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
                statusLabel.textColor = check.ok ? .systemGreen : Palette.secondaryText
                statusLabel.isEditable = false
                statusLabel.isSelectable = false
                statusLabel.isBordered = false
                statusLabel.drawsBackground = false
                statusStack.addArrangedSubview(statusLabel)

                NSLayoutConstraint.activate([
                    title.leadingAnchor.constraint(equalTo: row.leadingAnchor, constant: 16),
                    title.centerYAnchor.constraint(equalTo: row.centerYAnchor),
                    title.trailingAnchor.constraint(lessThanOrEqualTo: statusStack.leadingAnchor, constant: -12),

                    statusStack.trailingAnchor.constraint(equalTo: row.trailingAnchor, constant: -16),
                    statusStack.centerYAnchor.constraint(equalTo: row.centerYAnchor),

                    row.heightAnchor.constraint(equalToConstant: 34)
                ])

                innerStack.addArrangedSubview(row)
                row.widthAnchor.constraint(equalTo: innerStack.widthAnchor).isActive = true
            }

            // Footer row
            let footRow = NSView()
            footRow.translatesAutoresizingMaskIntoConstraints = false

            let footSep = NSView()
            footSep.translatesAutoresizingMaskIntoConstraints = false
            footSep.wantsLayer = true
            footSep.layer?.backgroundColor = Palette.separator.cgColor
            footRow.addSubview(footSep)
            NSLayoutConstraint.activate([
                footSep.topAnchor.constraint(equalTo: footRow.topAnchor),
                footSep.leadingAnchor.constraint(equalTo: footRow.leadingAnchor, constant: 16),
                footSep.trailingAnchor.constraint(equalTo: footRow.trailingAnchor),
                footSep.heightAnchor.constraint(equalToConstant: 0.5)
            ])

            let footStack = NSStackView()
            footStack.orientation = .horizontal
            footStack.alignment = .centerY
            footStack.spacing = 14
            footStack.translatesAutoresizingMaskIntoConstraints = false
            footRow.addSubview(footStack)
            NSLayoutConstraint.activate([
                footStack.topAnchor.constraint(equalTo: footRow.topAnchor, constant: 15),
                footStack.bottomAnchor.constraint(equalTo: footRow.bottomAnchor, constant: -15),
                footStack.leadingAnchor.constraint(equalTo: footRow.leadingAnchor, constant: 16),
                footStack.trailingAnchor.constraint(equalTo: footRow.trailingAnchor, constant: -16)
            ])

            let footDesc = NSTextField(wrappingLabelWithString: L10n.tr("preferences.provider.codex.footer_desc"))
            footDesc.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
            footDesc.textColor = Palette.secondaryText
            footDesc.isEditable = false
            footDesc.isSelectable = false
            footDesc.isBordered = false
            footDesc.drawsBackground = false
            footStack.addArrangedSubview(footDesc)
            footDesc.setContentHuggingPriority(.defaultLow, for: .horizontal)

            let btnStack = NSStackView()
            btnStack.orientation = .horizontal
            btnStack.alignment = .centerY
            btnStack.spacing = 8
            btnStack.translatesAutoresizingMaskIntoConstraints = false

            let viewConfigBtn = NSButton(
                title: L10n.tr("preferences.provider.codex.view_config"),
                target: self,
                action: #selector(viewCodexConfigAction(_:))
            )
            viewConfigBtn.bezelStyle = .rounded
            viewConfigBtn.controlSize = .regular
            btnStack.addArrangedSubview(viewConfigBtn)

            let repairBtn = NSButton(
                title: L10n.tr("preferences.provider.codex.install_repair"),
                target: self,
                action: #selector(configureProviderCodex(_:))
            )
            repairBtn.bezelStyle = .rounded
            repairBtn.controlSize = .regular
            btnStack.addArrangedSubview(repairBtn)

            footStack.addArrangedSubview(btnStack)

            innerStack.addArrangedSubview(footRow)
            footRow.widthAnchor.constraint(equalTo: innerStack.widthAnchor).isActive = true
        }

        addProviderCardSection(
            title: L10n.tr("preferences.provider.section.codex"),
            card: card,
            into: stack)
    }

    private func addProviderCardSection(
        title: String,
        card: CardView,
        into stack: NSStackView
    ) {
        let sectionStack = NSStackView()
        sectionStack.orientation = .vertical
        sectionStack.alignment = .leading
        sectionStack.spacing = 7
        sectionStack.translatesAutoresizingMaskIntoConstraints = false

        let header = NSTextField(labelWithString: title.uppercased())
        header.font = NSFont.systemFont(ofSize: 11, weight: .semibold)
        header.textColor = Palette.sectionHeader
        header.isEditable = false
        header.isSelectable = false
        header.isBordered = false
        header.drawsBackground = false

        let headerContainer = NSView()
        headerContainer.translatesAutoresizingMaskIntoConstraints = false
        header.translatesAutoresizingMaskIntoConstraints = false
        headerContainer.addSubview(header)
        NSLayoutConstraint.activate([
            header.topAnchor.constraint(equalTo: headerContainer.topAnchor),
            header.leadingAnchor.constraint(equalTo: headerContainer.leadingAnchor, constant: 6),
            header.trailingAnchor.constraint(equalTo: headerContainer.trailingAnchor),
            header.bottomAnchor.constraint(equalTo: headerContainer.bottomAnchor),
        ])

        sectionStack.addArrangedSubview(headerContainer)
        sectionStack.addArrangedSubview(card)
        headerContainer.widthAnchor.constraint(equalTo: sectionStack.widthAnchor).isActive = true
        card.widthAnchor.constraint(equalTo: sectionStack.widthAnchor).isActive = true

        stack.addArrangedSubview(sectionStack)
        sectionStack.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
    }

    @objc func viewCodexConfigAction(_ sender: Any) {
        guard let state = providerRoutingState else { return }
        presentCodexConfigSheet(state: state)
    }

    func presentCodexConfigSheet(state: ProviderRoutingState) {
        guard
            activeCodexConfigSheetController == nil,
            activeInstallRepairSheetController == nil,
            let win = window,
            win.isVisible
        else { return }

        let controller = ProviderCodexConfigSheetController(
            state: state,
            gatewayRunning: providerGatewayRunning,
            parentWindow: win,
            preferencesWindow: self,
            onDismiss: { [weak self] in
                self?.activeCodexConfigSheetController = nil
            },
            onRepair: { [weak self] in
                guard let self = self, let currentState = self.providerRoutingState else { return }
                self.presentInstallRepairSheet(state: currentState)
            },
            onStartGateway: { [weak self] in
                guard let self = self else { return }
                self.toggleProviderGateway(NSButton())
            }
        )
        activeCodexConfigSheetController = controller
        controller.show()
    }

    // MARK: - 5. Executors & Model Catalogs Section

    func addProviderCatalogsSection(_ state: ProviderRoutingState, into stack: NSStackView) {
        var rows: [NSView] = []

        let preferredOrder = ["antigravity", "claude", "codex"]
        // One row per known executor rather than per configured provider. This
        // section is named for executors, and an agent CLI that is installed
        // but absent from the routing config used to be invisible here — the
        // only place it could have been noticed.
        var executorNames = state.executors.map { $0.executor.lowercased() }
        for provider in state.providers where !executorNames.contains(provider.executor.lowercased()) {
            executorNames.append(provider.executor.lowercased())
        }
        executorNames.sort { name1, name2 in
            (preferredOrder.firstIndex(of: name1) ?? 99) < (preferredOrder.firstIndex(of: name2) ?? 99)
        }

        for (index, executorName) in executorNames.enumerated() {
            let provider = state.providers.first { $0.executor.lowercased() == executorName }
            let detected = state.executors.first { $0.executor.lowercased() == executorName }
            let catalog = state.catalogs.first { $0.agent.lowercased() == executorName }
            let count = catalog?.models.count ?? provider?.models.count ?? 0

            let rowView = NSView()
            rowView.translatesAutoresizingMaskIntoConstraints = false

            let sep = NSView()
            sep.translatesAutoresizingMaskIntoConstraints = false
            sep.wantsLayer = true
            sep.layer?.backgroundColor = Palette.separator.cgColor
            rowView.addSubview(sep)
            NSLayoutConstraint.activate([
                sep.topAnchor.constraint(equalTo: rowView.topAnchor),
                sep.leadingAnchor.constraint(equalTo: rowView.leadingAnchor, constant: 16),
                sep.trailingAnchor.constraint(equalTo: rowView.trailingAnchor),
                sep.heightAnchor.constraint(equalToConstant: 0.5)
            ])
            sep.isHidden = (index == 0)

            // Right-side catalog source/status tag.
            let rightStack = NSStackView()
            rightStack.orientation = .horizontal
            rightStack.alignment = .centerY
            rightStack.spacing = 6
            rightStack.translatesAutoresizingMaskIntoConstraints = false
            rowView.addSubview(rightStack)

            let tagText: String
            if provider == nil {
                tagText = detected?.installed == true
                    ? L10n.tr("preferences.provider.executor.tag.unrouted")
                    : L10n.tr("preferences.provider.executor.tag.not_installed")
            } else if executorName == "claude" {
                tagText = L10n.tr("preferences.provider.catalog.alias_table")
            } else if catalog != nil {
                tagText = L10n.tr("preferences.provider.catalog.live_catalog")
            } else if providerCatalogsLoading {
                tagText = L10n.tr("preferences.provider.catalog.loading")
            } else {
                tagText = L10n.tr("preferences.provider.catalog.configured")
            }
            let tagView = ProviderTagView(text: tagText)
            rightStack.addArrangedSubview(tagView)

            // Installed but unrouted is the one row the user can act on here.
            if provider == nil, detected?.isAddable == true {
                let addButton = NSButton(
                    title: L10n.tr("preferences.provider.executor.add"),
                    target: self,
                    action: #selector(addProviderExecutorAction(_:)))
                addButton.bezelStyle = .rounded
                addButton.controlSize = .small
                addButton.identifier = NSUserInterfaceItemIdentifier(executorName)
                rightStack.addArrangedSubview(addButton)
            }

            NSLayoutConstraint.activate([
                rightStack.trailingAnchor.constraint(equalTo: rowView.trailingAnchor, constant: -16),
                rightStack.centerYAnchor.constraint(equalTo: rowView.centerYAnchor)
            ])

            let labelStack = NSStackView()
            labelStack.orientation = .vertical
            labelStack.alignment = .leading
            labelStack.spacing = 2
            labelStack.translatesAutoresizingMaskIntoConstraints = false
            rowView.addSubview(labelStack)

            NSLayoutConstraint.activate([
                labelStack.leadingAnchor.constraint(equalTo: rowView.leadingAnchor, constant: 16),
                labelStack.topAnchor.constraint(equalTo: rowView.topAnchor, constant: 12),
                labelStack.bottomAnchor.constraint(equalTo: rowView.bottomAnchor, constant: -12),
                labelStack.trailingAnchor.constraint(lessThanOrEqualTo: rightStack.leadingAnchor, constant: -12)
            ])

            let titleLabel = NSTextField(labelWithString: formatProviderName(executorName))
            titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .medium)
            titleLabel.textColor = Palette.primaryText
            titleLabel.isEditable = false
            titleLabel.isSelectable = false
            titleLabel.isBordered = false
            titleLabel.drawsBackground = false
            labelStack.addArrangedSubview(titleLabel)

            let descText: String
            if provider == nil {
                descText = detected?.installed == true
                    ? L10n.tr(
                        "preferences.provider.executor.unrouted_desc",
                        detected?.path ?? "")
                    : L10n.tr("preferences.provider.executor.not_installed_desc")
            } else if executorName == "claude" {
                descText = L10n.tr("preferences.provider.catalog.aliases")
            } else if catalog != nil {
                descText = String(
                    format: L10n.tr("preferences.provider.catalog.summary"),
                    count)
            } else if providerCatalogsLoading {
                descText = L10n.tr(
                    "preferences.provider.catalog.loading_desc",
                    provider?.models.count ?? 0)
            } else {
                descText = L10n.tr(
                    "preferences.provider.catalog.configured_desc",
                    provider?.models.count ?? 0)
            }
            let descLabel = NSTextField(labelWithString: descText)
            descLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
            descLabel.textColor = Palette.secondaryText
            descLabel.isEditable = false
            descLabel.isSelectable = false
            descLabel.isBordered = false
            descLabel.drawsBackground = false
            labelStack.addArrangedSubview(descLabel)

            rows.append(rowView)
        }

        // Footer row with Refresh button
        let footRow = NSView()
        footRow.translatesAutoresizingMaskIntoConstraints = false

        let footSep = NSView()
        footSep.translatesAutoresizingMaskIntoConstraints = false
        footSep.wantsLayer = true
        footSep.layer?.backgroundColor = Palette.separator.cgColor
        footRow.addSubview(footSep)
        NSLayoutConstraint.activate([
            footSep.topAnchor.constraint(equalTo: footRow.topAnchor),
            footSep.leadingAnchor.constraint(equalTo: footRow.leadingAnchor, constant: 16),
            footSep.trailingAnchor.constraint(equalTo: footRow.trailingAnchor),
            footSep.heightAnchor.constraint(equalToConstant: 0.5)
        ])

        let footStack = NSStackView()
        footStack.orientation = .horizontal
        footStack.alignment = .centerY
        footStack.spacing = 10
        footStack.edgeInsets = NSEdgeInsets(top: 10, left: 16, bottom: 11, right: 16)
        footStack.translatesAutoresizingMaskIntoConstraints = false
        footRow.addSubview(footStack)
        NSLayoutConstraint.activate([
            footStack.topAnchor.constraint(equalTo: footRow.topAnchor),
            footStack.leadingAnchor.constraint(equalTo: footRow.leadingAnchor),
            footStack.trailingAnchor.constraint(equalTo: footRow.trailingAnchor),
            footStack.bottomAnchor.constraint(equalTo: footRow.bottomAnchor)
        ])

        let footerText: String
        if providerCatalogsLoading {
            footerText = L10n.tr("preferences.provider.catalogs.refreshing")
        } else if providerCatalogsUnavailable {
            footerText = L10n.tr("preferences.provider.catalogs.unavailable")
        } else if providerCatalogsLoaded {
            footerText = L10n.tr("preferences.provider.catalogs.last_refreshed")
        } else {
            footerText = L10n.tr("preferences.provider.catalogs.not_loaded")
        }
        let footDesc = NSTextField(labelWithString: footerText)
        footDesc.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        footDesc.textColor = Palette.secondaryText
        footDesc.isEditable = false
        footDesc.isSelectable = false
        footDesc.isBordered = false
        footDesc.drawsBackground = false
        footStack.addArrangedSubview(footDesc)

        let footSpacer = NSView()
        footSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        footSpacer.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        footStack.addArrangedSubview(footSpacer)

        let refreshBtn = NSButton(
            title: L10n.tr("preferences.provider.catalogs.refresh"),
            target: self,
            action: #selector(refreshProviderCatalogs(_:))
        )
        refreshBtn.bezelStyle = .rounded
        refreshBtn.controlSize = .regular
        refreshBtn.isEnabled = !providerCatalogsLoading
        footStack.addArrangedSubview(refreshBtn)

        rows.append(footRow)

        let sectionTitle = L10n.tr("preferences.provider.section.catalogs")
        addSection(title: sectionTitle, rows: rows, into: stack)
    }

    @objc func addProviderExecutorAction(_ sender: NSButton) {
        guard let executor = sender.identifier?.rawValue, !executor.isEmpty else { return }
        addProviderExecutor(executor, reopeningRole: nil)
    }

    // MARK: - 6. Model Updates Section

    func addProviderModelUpdatesSection(into stack: NSStackView) {
        let currentDrift = (appDelegate.envVals["FLUXION_PROVIDER_CODEX_CATALOG_DRIFT"] ?? "warn").lowercased()
        let selectedIndex: Int
        switch currentDrift {
        case "off": selectedIndex = 1
        case "refresh": selectedIndex = 2
        default: selectedIndex = 0
        }

        let options: [(title: String, tag: String?, desc: String?)] = [
            (L10n.tr("preferences.provider.updates.opt1"), L10n.tr("preferences.provider.badge.recommended"), nil),
            (L10n.tr("preferences.provider.updates.opt2"), nil, nil),
            (L10n.tr("preferences.provider.updates.opt3"), nil, L10n.tr("preferences.provider.updates.opt3_desc"))
        ]

        var rows: [NSView] = []
        for (index, opt) in options.enumerated() {
            let isSelected = (index == selectedIndex)
            let rowView = makeRadioRowView(
                title: opt.title,
                tag: opt.tag,
                desc: opt.desc,
                isSelected: isSelected,
                index: index,
                isFirst: index == 0
            )
            rows.append(rowView)
        }

        let sectionTitle = L10n.tr("preferences.provider.section.updates")
        addSection(title: sectionTitle, rows: rows, into: stack)
    }

    private func makeRadioRowView(
        title: String,
        tag: String?,
        desc: String?,
        isSelected: Bool,
        index: Int,
        isFirst: Bool
    ) -> NSView {
        let rowView = NSView()
        rowView.translatesAutoresizingMaskIntoConstraints = false

        let sep = NSView()
        sep.translatesAutoresizingMaskIntoConstraints = false
        sep.wantsLayer = true
        sep.layer?.backgroundColor = Palette.separator.cgColor
        rowView.addSubview(sep)
        NSLayoutConstraint.activate([
            sep.topAnchor.constraint(equalTo: rowView.topAnchor),
            sep.leadingAnchor.constraint(equalTo: rowView.leadingAnchor, constant: 16),
            sep.trailingAnchor.constraint(equalTo: rowView.trailingAnchor),
            sep.heightAnchor.constraint(equalToConstant: 0.5)
        ])
        sep.isHidden = isFirst

        // Radio tick circle pinned strictly to trailingAnchor
        let tickCircle = NSView()
        tickCircle.translatesAutoresizingMaskIntoConstraints = false
        tickCircle.wantsLayer = true
        tickCircle.layer?.cornerRadius = 7.5
        tickCircle.layer?.borderWidth = isSelected ? 0 : 1
        tickCircle.layer?.borderColor = isSelected
            ? NSColor.clear.cgColor
            : NSColor.dynamicColor(
                light: NSColor(white: 0.72, alpha: 1.0),
                dark: NSColor(white: 0.38, alpha: 1.0)
            ).cgColor
        tickCircle.layer?.backgroundColor = isSelected ? NSColor.controlAccentColor.cgColor : NSColor.clear.cgColor
        rowView.addSubview(tickCircle)

        if isSelected {
            let innerDot = NSView()
            innerDot.translatesAutoresizingMaskIntoConstraints = false
            innerDot.wantsLayer = true
            innerDot.layer?.cornerRadius = 2.5
            innerDot.layer?.backgroundColor = NSColor.white.cgColor
            tickCircle.addSubview(innerDot)
            NSLayoutConstraint.activate([
                innerDot.centerXAnchor.constraint(equalTo: tickCircle.centerXAnchor),
                innerDot.centerYAnchor.constraint(equalTo: tickCircle.centerYAnchor),
                innerDot.widthAnchor.constraint(equalToConstant: 5),
                innerDot.heightAnchor.constraint(equalToConstant: 5)
            ])
        }

        NSLayoutConstraint.activate([
            tickCircle.trailingAnchor.constraint(equalTo: rowView.trailingAnchor, constant: -16),
            tickCircle.centerYAnchor.constraint(equalTo: rowView.centerYAnchor),
            tickCircle.widthAnchor.constraint(equalToConstant: 15),
            tickCircle.heightAnchor.constraint(equalToConstant: 15)
        ])

        // Text stack pinned strictly from leading (16) to right before tickCircle (-12)
        let textStack = NSStackView()
        textStack.orientation = .vertical
        textStack.alignment = .leading
        textStack.spacing = 3
        textStack.translatesAutoresizingMaskIntoConstraints = false
        rowView.addSubview(textStack)

        NSLayoutConstraint.activate([
            textStack.leadingAnchor.constraint(equalTo: rowView.leadingAnchor, constant: 16),
            textStack.topAnchor.constraint(equalTo: rowView.topAnchor, constant: 12),
            textStack.bottomAnchor.constraint(equalTo: rowView.bottomAnchor, constant: -12),
            textStack.trailingAnchor.constraint(lessThanOrEqualTo: tickCircle.leadingAnchor, constant: -12)
        ])

        let titleStack = NSStackView()
        titleStack.orientation = .horizontal
        titleStack.alignment = .centerY
        titleStack.spacing = 8
        titleStack.translatesAutoresizingMaskIntoConstraints = false

        let titleLabel = NSTextField(wrappingLabelWithString: title)
        titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .regular)
        titleLabel.textColor = Palette.primaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        titleStack.addArrangedSubview(titleLabel)

        if let tag = tag {
            let tagView = ProviderTagView(text: tag, isAccent: true, isBold: true)
            titleStack.addArrangedSubview(tagView)
        }
        textStack.addArrangedSubview(titleStack)

        if let desc = desc {
            let descLabel = NSTextField(wrappingLabelWithString: desc)
            descLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
            descLabel.textColor = Palette.secondaryText
            descLabel.isEditable = false
            descLabel.isSelectable = false
            descLabel.isBordered = false
            descLabel.drawsBackground = false
            textStack.addArrangedSubview(descLabel)
        }

        // Overlay click button
        let overlayBtn = NSButton(frame: .zero)
        overlayBtn.isTransparent = true
        overlayBtn.target = self
        overlayBtn.action = #selector(radioOptionClicked(_:))
        overlayBtn.tag = index
        overlayBtn.translatesAutoresizingMaskIntoConstraints = false
        rowView.addSubview(overlayBtn)
        NSLayoutConstraint.activate([
            overlayBtn.topAnchor.constraint(equalTo: rowView.topAnchor),
            overlayBtn.leadingAnchor.constraint(equalTo: rowView.leadingAnchor),
            overlayBtn.trailingAnchor.constraint(equalTo: rowView.trailingAnchor),
            overlayBtn.bottomAnchor.constraint(equalTo: rowView.bottomAnchor)
        ])

        return rowView
    }

    @objc func radioOptionClicked(_ sender: NSButton) {
        let value: String
        switch sender.tag {
        case 1: value = "off"
        case 2: value = "refresh"
        default: value = "warn"
        }
        appDelegate.saveEnv(updates: ["FLUXION_PROVIDER_CODEX_CATALOG_DRIFT": value])
        renderProviderRouting()
    }

    // MARK: - Setup Not Configured

    func addProviderSetup(_ state: ProviderRoutingState, into stack: NSStackView) {
        let button = NSButton(
            title: L10n.tr("preferences.provider.setup"),
            target: self,
            action: #selector(initializeProviderRouting(_:)))
        button.bezelStyle = .rounded
        button.controlSize = .small
        let row = CardRow(
            title: L10n.tr("preferences.provider.not_configured.title"),
            desc: L10n.tr("preferences.provider.not_configured.desc", state.configFile),
            control: button,
            isFirst: true)
        addSection(
            title: L10n.tr("preferences.provider.section.routes"),
            rows: [row],
            into: stack)
    }
}
