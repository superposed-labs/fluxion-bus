import AppKit
import Foundation

// The two full-width cards at the top of the provider-routing page: the gateway
// status card (endpoints, start/stop, connection details) and the model upgrade
// banner with its review sheet. The banner's contents come from the backend, so
// a vendor release it has never heard of still produces a correct offer.

extension PreferencesWindow {

    // MARK: - 1. Top Provider Gateway Card

    func addProviderGatewayCard(_ state: ProviderRoutingState, into stack: NSStackView) {
        let card = CardView()
        card.translatesAutoresizingMaskIntoConstraints = false

        let innerStack = NSStackView()
        innerStack.orientation = .vertical
        innerStack.alignment = .leading
        innerStack.spacing = 10
        innerStack.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(innerStack)
        NSLayoutConstraint.activate([
            innerStack.topAnchor.constraint(equalTo: card.topAnchor, constant: 14),
            innerStack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            innerStack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),
            innerStack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -14)
        ])

        // Line 1: Header + Inline Pill
        let headerStack = NSStackView()
        headerStack.orientation = .horizontal
        headerStack.alignment = .centerY
        headerStack.spacing = 8

        let titleLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.gateway.title"))
        titleLabel.font = NSFont.systemFont(ofSize: 15, weight: .bold)
        titleLabel.textColor = Palette.primaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        headerStack.addArrangedSubview(titleLabel)

        let pillTone: ProviderPillTone = providerGatewayRunning ? .ok : .idle
        let pillText = providerGatewayRunning
            ? L10n.tr("preferences.provider.status.running")
            : L10n.tr("preferences.provider.status.stopped")
        let pill = ProviderPillView(tone: pillTone, text: pillText)
        headerStack.addArrangedSubview(pill)
        innerStack.addArrangedSubview(headerStack)

        // Line 2: Facts / Summary
        let installedRoles = state.codex.roles.filter(\.healthy).count
        let totalRoles = state.codex.roles.isEmpty ? 4 : state.codex.roles.count
        let availableExecutors = state.providers.filter(\.enabled).count

        var factParts: [String] = []
        factParts.append(L10n.tr("preferences.provider.facts.roles", installedRoles, totalRoles))
        factParts.append(L10n.tr("preferences.provider.facts.executors", availableExecutors))
        if hasProviderUpgrade(state) && providerGatewayRunning {
            factParts.append(L10n.tr("preferences.provider.facts.update_available"))
        } else if !providerGatewayRunning {
            factParts.append(L10n.tr("preferences.provider.facts.disconnected"))
        } else if !state.modelHealth.missing.isEmpty {
            factParts.append(L10n.tr("preferences.provider.catalog.missing", state.modelHealth.missing.joined(separator: ", ")))
        }

        let factsLabel = NSTextField(wrappingLabelWithString: factParts.joined(separator: "  ·  "))
        factsLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        factsLabel.textColor = Palette.secondaryText
        factsLabel.cell?.wraps = true
        factsLabel.cell?.isScrollable = false
        factsLabel.maximumNumberOfLines = 0
        factsLabel.lineBreakMode = .byWordWrapping
        factsLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        factsLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
        factsLabel.isEditable = false
        factsLabel.isSelectable = false
        factsLabel.isBordered = false
        factsLabel.drawsBackground = false
        innerStack.addArrangedSubview(factsLabel)
        factsLabel.widthAnchor.constraint(lessThanOrEqualTo: innerStack.widthAnchor).isActive = true

        // Line 3: Actions row
        if state.configured {
            let actionsStack = NSStackView()
            actionsStack.orientation = .horizontal
            actionsStack.alignment = .centerY
            actionsStack.spacing = 10

            let restartButton = NSButton(
                title: providerGatewayRunning
                    ? L10n.tr("preferences.provider.restart")
                    : L10n.tr("preferences.provider.start"),
                target: self,
                action: #selector(toggleProviderGateway(_:))
            )
            restartButton.bezelStyle = .rounded
            restartButton.controlSize = .small
            actionsStack.addArrangedSubview(restartButton)

            let diagButton = NSButton(
                title: L10n.tr("preferences.provider.diagnostics"),
                target: self,
                action: #selector(runProviderDiagnosticsAction(_:))
            )
            diagButton.bezelStyle = .rounded
            diagButton.controlSize = .small
            actionsStack.addArrangedSubview(diagButton)

            let discButton = ProviderDisclosureButton(
                title: L10n.tr("preferences.provider.connection_details"),
                isOpen: providerConnectionDetailsOpen,
                target: self,
                action: #selector(toggleConnectionDetails(_:))
            )
            actionsStack.addArrangedSubview(discButton)
            innerStack.addArrangedSubview(actionsStack)

            // Connection Details Panel
            if providerConnectionDetailsOpen {
                let detailsBox = NSStackView()
                detailsBox.orientation = .vertical
                detailsBox.alignment = .leading
                detailsBox.spacing = 8
                detailsBox.translatesAutoresizingMaskIntoConstraints = false

                let sep = NSView()
                sep.translatesAutoresizingMaskIntoConstraints = false
                sep.wantsLayer = true
                sep.layer?.backgroundColor = Palette.separator.cgColor
                detailsBox.addArrangedSubview(sep)
                NSLayoutConstraint.activate([
                    sep.widthAnchor.constraint(equalTo: detailsBox.widthAnchor),
                    sep.heightAnchor.constraint(equalToConstant: 0.5)
                ])

                let host = appDelegate.envVals["FLUXION_PROVIDER_HOST"] ?? "127.0.0.1"
                let port = appDelegate.envVals["FLUXION_PROVIDER_PORT"] ?? "8787"
                let openaiUrl = "http://\(host):\(port)/v1"
                let anthropicUrl = "http://\(host):\(port)/v1/messages"

                detailsBox.addArrangedSubview(makeEndpointRow(
                    label: L10n.tr("preferences.provider.openai_endpoint"),
                    url: openaiUrl
                ))
                detailsBox.addArrangedSubview(makeEndpointRow(
                    label: L10n.tr("preferences.provider.anthropic_endpoint"),
                    url: anthropicUrl
                ))

                let noteLabel = NSTextField(wrappingLabelWithString: L10n.tr("preferences.provider.connection_note"))
                noteLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
                noteLabel.textColor = Palette.secondaryText
                noteLabel.isEditable = false
                noteLabel.isSelectable = false
                noteLabel.isBordered = false
                noteLabel.drawsBackground = false
                detailsBox.addArrangedSubview(noteLabel)

                innerStack.addArrangedSubview(detailsBox)
                detailsBox.widthAnchor.constraint(equalTo: innerStack.widthAnchor).isActive = true
            }
        }

        stack.addArrangedSubview(card)
        card.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
    }

    private func makeEndpointRow(label: String, url: String) -> NSView {
        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 10
        row.translatesAutoresizingMaskIntoConstraints = false

        let lbl = NSTextField(labelWithString: label)
        lbl.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        lbl.textColor = Palette.secondaryText
        lbl.isEditable = false
        lbl.isSelectable = false
        lbl.isBordered = false
        lbl.drawsBackground = false
        lbl.translatesAutoresizingMaskIntoConstraints = false
        lbl.widthAnchor.constraint(equalToConstant: 215).isActive = true
        row.addArrangedSubview(lbl)

        let urlBox = NSView()
        urlBox.translatesAutoresizingMaskIntoConstraints = false
        urlBox.wantsLayer = true
        urlBox.layer?.cornerRadius = 5
        urlBox.layer?.borderWidth = 0.5
        urlBox.layer?.borderColor = Palette.cardBorder.cgColor
        urlBox.layer?.backgroundColor = Palette.windowBackground.cgColor

        let urlLabel = NSTextField(labelWithString: url)
        urlLabel.font = NSFont.monospacedSystemFont(ofSize: 11.5, weight: .regular)
        urlLabel.textColor = Palette.primaryText
        urlLabel.lineBreakMode = .byTruncatingMiddle
        urlLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        urlLabel.isSelectable = true
        urlLabel.isEditable = false
        urlLabel.isBordered = false
        urlLabel.drawsBackground = false
        urlLabel.translatesAutoresizingMaskIntoConstraints = false
        urlBox.addSubview(urlLabel)

        NSLayoutConstraint.activate([
            urlLabel.leadingAnchor.constraint(equalTo: urlBox.leadingAnchor, constant: 8),
            urlLabel.trailingAnchor.constraint(equalTo: urlBox.trailingAnchor, constant: -8),
            urlLabel.topAnchor.constraint(equalTo: urlBox.topAnchor, constant: 4),
            urlLabel.bottomAnchor.constraint(equalTo: urlBox.bottomAnchor, constant: -4)
        ])
        row.addArrangedSubview(urlBox)

        let copyBtn = NSButton(
            title: L10n.tr("preferences.provider.copy"),
            target: self,
            action: #selector(copyEndpointUrl(_:))
        )
        copyBtn.bezelStyle = .rounded
        copyBtn.controlSize = .small
        copyBtn.identifier = NSUserInterfaceItemIdentifier(url)
        row.addArrangedSubview(copyBtn)

        return row
    }

    @objc func copyEndpointUrl(_ sender: NSButton) {
        guard let url = sender.identifier?.rawValue else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(url, forType: .string)
        let origTitle = sender.title
        sender.title = L10n.tr("preferences.provider.copied")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            sender.title = origTitle
        }
    }

    @objc func toggleConnectionDetails(_ sender: Any) {
        providerConnectionDetailsOpen.toggle()
        renderProviderRouting()
    }

    @objc func toggleAdvancedRoutes(_ sender: Any) {
        providerAdvancedRoutesOpen.toggle()
        renderProviderRouting()
    }

    @objc func toggleCodexIntegration(_ sender: Any) {
        providerCodexIntegrationOpen.toggle()
        renderProviderRouting()
    }

    // MARK: - 2. Model Upgrade Banner

    /// The upgrade to offer, if the backend found one.
    ///
    /// This was a named pair of Gemini versions and the strings to describe
    /// them, which meant every release needed a new function, new copy in three
    /// languages, and a signed build of the app before any user heard about it.
    /// The backend now derives the offer from the catalog and this renders
    /// whatever it is handed.
    func providerUpgradeOffer(_ state: ProviderRoutingState) -> ProviderUpgradeOffer? {
        (state.upgrades ?? []).first { !$0.roles.isEmpty }
    }

    func hasProviderUpgrade(_ state: ProviderRoutingState) -> Bool {
        providerUpgradeOffer(state) != nil
    }

    private func upgradeRoutes(
        _ state: ProviderRoutingState,
        offer: ProviderUpgradeOffer
    ) -> [ProviderRouteState] {
        state.routes.filter { offer.roles.contains($0.role) }
    }

    private func upgradePriceText(_ offer: ProviderUpgradeOffer) -> String {
        L10n.tr(
            offer.priceDelta == "cheaper"
                ? "preferences.provider.upgrade.sheet.price_cheaper"
                : "preferences.provider.upgrade.sheet.price_same",
            String(format: "$%.2f", offer.inputPer1M ?? 0),
            String(format: "$%.2f", offer.outputPer1M ?? 0))
    }

    func addProviderUpgradeBanner(_ state: ProviderRoutingState, into stack: NSStackView) {
        let card = AccentBannerCardView()
        card.translatesAutoresizingMaskIntoConstraints = false

        let rowStack = NSStackView()
        rowStack.orientation = .horizontal
        rowStack.alignment = .centerY
        rowStack.spacing = 16
        rowStack.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(rowStack)
        NSLayoutConstraint.activate([
            rowStack.topAnchor.constraint(equalTo: card.topAnchor, constant: 14),
            rowStack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 20),
            rowStack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),
            rowStack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -14)
        ])

        // Text content
        let textStack = NSStackView()
        textStack.orientation = .vertical
        textStack.alignment = .leading
        textStack.spacing = 4

        guard let offer = providerUpgradeOffer(state) else { return }
        let titleLabel = NSTextField(labelWithString: L10n.tr(
            "preferences.provider.upgrade.title", formatModelDisplayName(offer.toModel)))
        titleLabel.font = NSFont.systemFont(ofSize: 13.5, weight: .bold)
        titleLabel.textColor = Palette.primaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        textStack.addArrangedSubview(titleLabel)

        let routeNames = upgradeRoutes(state, offer: offer)
            .map { formatRoleDisplayName($0.role) }
            .joined(separator: ", ")

        let descLabel = NSTextField(wrappingLabelWithString: L10n.tr(
            "preferences.provider.upgrade.desc",
            routeNames,
            formatModelDisplayName(offer.fromModel)))
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
        textStack.addArrangedSubview(descLabel)

        let metaLabel = NSTextField(wrappingLabelWithString: L10n.tr(
            offer.priceDelta == "cheaper"
                ? "preferences.provider.upgrade.meta_cheaper"
                : "preferences.provider.upgrade.meta_same"))
        metaLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        metaLabel.textColor = Palette.secondaryText
        metaLabel.cell?.wraps = true
        metaLabel.cell?.isScrollable = false
        metaLabel.maximumNumberOfLines = 0
        metaLabel.lineBreakMode = .byWordWrapping
        metaLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        metaLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
        metaLabel.isEditable = false
        metaLabel.isSelectable = false
        metaLabel.isBordered = false
        metaLabel.drawsBackground = false
        textStack.addArrangedSubview(metaLabel)

        rowStack.addArrangedSubview(textStack)
        textStack.setContentHuggingPriority(.defaultLow, for: .horizontal)

        // Action button
        let reviewBtn = ProviderAccentButton()
        reviewBtn.title = L10n.tr("preferences.provider.upgrade.review")
        reviewBtn.target = self
        reviewBtn.action = #selector(reviewUpgradeAction(_:))
        rowStack.addArrangedSubview(reviewBtn)

        stack.addArrangedSubview(card)
        card.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
    }

    @objc func reviewUpgradeAction(_ sender: Any) {
        guard let state = providerRoutingState, let win = window else { return }

        guard let offer = providerUpgradeOffer(state) else { return }
        let affected = upgradeRoutes(state, offer: offer)
        let alert = NSAlert()
        alert.messageText = L10n.tr("preferences.provider.upgrade.sheet.title")
        alert.informativeText = ""
        alert.accessoryView = buildReviewUpgradeAccessoryView(
            offer: offer, affectedRoutes: affected)

        let applyTitle = String(format: L10n.tr("preferences.provider.upgrade.sheet.apply"), affected.count)
        alert.addButton(withTitle: applyTitle)
        alert.addButton(withTitle: L10n.tr("preferences.provider.upgrade.sheet.not_now"))

        alert.beginSheetModal(for: win) { [weak self] response in
            guard response == .alertFirstButtonReturn, let self = self else { return }
            self.applyUpgradeOffer(offer, to: affected)
        }
    }

    private func buildReviewUpgradeAccessoryView(
        offer: ProviderUpgradeOffer,
        affectedRoutes: [ProviderRouteState]
    ) -> NSView {
        let container = NSStackView()
        container.orientation = .vertical
        container.alignment = .leading
        container.spacing = 10
        container.translatesAutoresizingMaskIntoConstraints = false
        container.widthAnchor.constraint(equalToConstant: 440).isActive = true

        let subLabel = NSTextField(wrappingLabelWithString: L10n.tr(
            "preferences.provider.upgrade.sheet.sub", formatModelDisplayName(offer.toModel)))
        subLabel.font = NSFont.systemFont(ofSize: 12, weight: .regular)
        subLabel.textColor = Palette.secondaryText
        subLabel.isEditable = false
        subLabel.isSelectable = false
        subLabel.isBordered = false
        subLabel.drawsBackground = false
        container.addArrangedSubview(subLabel)

        // Diff Card
        let diffCard = CardView()
        diffCard.translatesAutoresizingMaskIntoConstraints = false
        let diffStack = NSStackView()
        diffStack.orientation = .vertical
        diffStack.alignment = .leading
        diffStack.spacing = 6
        diffStack.edgeInsets = NSEdgeInsets(top: 10, left: 12, bottom: 10, right: 12)
        diffStack.translatesAutoresizingMaskIntoConstraints = false
        diffCard.stackView.addArrangedSubview(diffStack)
        diffStack.widthAnchor.constraint(equalTo: diffCard.stackView.widthAnchor).isActive = true

        // Primary diff row
        let primaryRow = makeDiffRow(
            key: L10n.tr("preferences.provider.upgrade.sheet.primary_k"),
            value: L10n.tr(
                "preferences.provider.upgrade.sheet.primary_v",
                formatModelDisplayName(offer.fromModel),
                formatModelDisplayName(offer.toModel))
        )
        diffStack.addArrangedSubview(primaryRow)

        // Fallback diff row
        let fallbackRow = makeDiffRow(
            key: L10n.tr("preferences.provider.upgrade.sheet.fallback_k"),
            value: L10n.tr(
                "preferences.provider.upgrade.sheet.fallback_v",
                formatModelDisplayName(offer.fromModel))
        )
        diffStack.addArrangedSubview(fallbackRow)

        // Price diff row
        let priceRow = makeDiffRow(
            key: L10n.tr("preferences.provider.upgrade.sheet.price_k"),
            value: upgradePriceText(offer)
        )
        diffStack.addArrangedSubview(priceRow)

        container.addArrangedSubview(diffCard)
        diffCard.widthAnchor.constraint(equalTo: container.widthAnchor).isActive = true

        // Routes list
        if !affectedRoutes.isEmpty {
            let routesLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.section.routes").uppercased())
            routesLabel.font = NSFont.systemFont(ofSize: 10.5, weight: .bold)
            routesLabel.textColor = Palette.sectionHeader
            routesLabel.isEditable = false
            routesLabel.isSelectable = false
            routesLabel.isBordered = false
            routesLabel.drawsBackground = false
            container.addArrangedSubview(routesLabel)

            let routesCard = CardView()
            routesCard.translatesAutoresizingMaskIntoConstraints = false
            let routesStack = NSStackView()
            routesStack.orientation = .vertical
            routesStack.alignment = .leading
            routesStack.spacing = 0
            routesStack.translatesAutoresizingMaskIntoConstraints = false
            routesCard.stackView.addArrangedSubview(routesStack)
            routesStack.widthAnchor.constraint(equalTo: routesCard.stackView.widthAnchor).isActive = true

            for (i, route) in affectedRoutes.enumerated() {
                let rView = NSView()
                rView.translatesAutoresizingMaskIntoConstraints = false

                let sep = NSView()
                sep.translatesAutoresizingMaskIntoConstraints = false
                sep.wantsLayer = true
                sep.layer?.backgroundColor = Palette.separator.cgColor
                rView.addSubview(sep)
                NSLayoutConstraint.activate([
                    sep.topAnchor.constraint(equalTo: rView.topAnchor),
                    sep.leadingAnchor.constraint(equalTo: rView.leadingAnchor, constant: 12),
                    sep.trailingAnchor.constraint(equalTo: rView.trailingAnchor),
                    sep.heightAnchor.constraint(equalToConstant: 0.5)
                ])
                sep.isHidden = (i == 0)

                let rStack = NSStackView()
                rStack.orientation = .vertical
                rStack.alignment = .leading
                rStack.spacing = 2
                rStack.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)
                rStack.translatesAutoresizingMaskIntoConstraints = false
                rView.addSubview(rStack)
                NSLayoutConstraint.activate([
                    rStack.topAnchor.constraint(equalTo: rView.topAnchor),
                    rStack.leadingAnchor.constraint(equalTo: rView.leadingAnchor),
                    rStack.trailingAnchor.constraint(equalTo: rView.trailingAnchor),
                    rStack.bottomAnchor.constraint(equalTo: rView.bottomAnchor)
                ])

                let hStack = NSStackView()
                hStack.orientation = .horizontal
                hStack.alignment = .centerY
                hStack.spacing = 6
                let name = NSTextField(labelWithString: formatRoleDisplayName(route.role))
                name.font = NSFont.systemFont(ofSize: 12, weight: .semibold)
                name.textColor = Palette.primaryText
                let slug = ProviderBadgeView(text: formatRoleSlug(route.role))
                hStack.addArrangedSubview(name)
                hStack.addArrangedSubview(slug)
                rStack.addArrangedSubview(hStack)

                // The model being replaced heads the chain it is demoted into.
                let chainParts = [formatModelDisplayName(offer.fromModel)]
                    + route.fallback.map { formatCandidateModelName($0) }
                let chainLabel = NSTextField(wrappingLabelWithString: chainParts.joined(separator: " → "))
                chainLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
                chainLabel.textColor = Palette.secondaryText
                rStack.addArrangedSubview(chainLabel)

                routesStack.addArrangedSubview(rView)
                rView.widthAnchor.constraint(equalTo: routesStack.widthAnchor).isActive = true
            }

            container.addArrangedSubview(routesCard)
            routesCard.widthAnchor.constraint(equalTo: container.widthAnchor).isActive = true
        }

        // Note
        let noteLabel = NSTextField(wrappingLabelWithString: L10n.tr("preferences.provider.upgrade.sheet.note"))
        noteLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        noteLabel.textColor = Palette.secondaryText
        noteLabel.isEditable = false
        noteLabel.isSelectable = false
        noteLabel.isBordered = false
        noteLabel.drawsBackground = false
        container.addArrangedSubview(noteLabel)

        return container
    }

    private func makeDiffRow(key: String, value: String) -> NSView {
        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .firstBaseline
        row.spacing = 8
        row.translatesAutoresizingMaskIntoConstraints = false

        let kLabel = NSTextField(labelWithString: key)
        kLabel.font = NSFont.systemFont(ofSize: 11, weight: .semibold)
        kLabel.textColor = Palette.sectionHeader
        kLabel.isEditable = false
        kLabel.isSelectable = false
        kLabel.isBordered = false
        kLabel.drawsBackground = false
        kLabel.translatesAutoresizingMaskIntoConstraints = false
        kLabel.widthAnchor.constraint(equalToConstant: 80).isActive = true
        row.addArrangedSubview(kLabel)

        let vLabel = NSTextField(wrappingLabelWithString: value)
        vLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        vLabel.textColor = Palette.primaryText
        vLabel.isEditable = false
        vLabel.isSelectable = false
        vLabel.isBordered = false
        vLabel.drawsBackground = false
        row.addArrangedSubview(vLabel)

        return row
    }

    private func applyUpgradeOffer(_ offer: ProviderUpgradeOffer, to routes: [ProviderRouteState]) {
        applyUpgradeOffer(offer, to: routes, index: 0)
    }

    private func applyUpgradeOffer(
        _ offer: ProviderUpgradeOffer,
        to routes: [ProviderRouteState],
        index: Int
    ) {
        guard index < routes.count else {
            DispatchQueue.global(qos: .userInitiated).async { [weak self] in
                guard let self = self else { return }
                self.appDelegate.restartServices(
                    patterns: [self.appDelegate.servicePattern("fluxion-provider")])
                DispatchQueue.main.async {
                    self.refreshProviderRouting(includeCatalogs: true)
                }
            }
            return
        }

        let route = routes[index]
        let newCandidates = route.candidates.map {
            $0 == offer.fromCandidate ? offer.toCandidate : $0
        }
        var newFallbacks = route.fallback
        if let oldPrimary = route.candidates.first, !newFallbacks.contains(oldPrimary) {
            newFallbacks.insert(oldPrimary, at: 0)
        }
        var args = ["set-route", "--role", route.role, "--add-models"]
        for candidate in newCandidates {
            args += ["--candidate", candidate]
        }
        for fallback in newFallbacks {
            args += ["--fallback", fallback]
        }
        runProviderCommand(args) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success:
                self.applyUpgradeOffer(offer, to: routes, index: index + 1)
            case .failure(let error):
                self.showProviderRoutingError(error.localizedDescription)
                self.refreshProviderRouting(includeCatalogs: true)
            }
        }
    }
}
