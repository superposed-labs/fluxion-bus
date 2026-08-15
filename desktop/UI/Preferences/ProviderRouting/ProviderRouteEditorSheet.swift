import AppKit
import Foundation

// The per-role route editor sheet: primary model, reasoning effort, and the
// ordered fallback chain. Its rows come from ProviderRouteEditorRows.swift.

class ProviderRouteEditorSheetController: NSObject, NSWindowDelegate {
    private let route: ProviderRouteState
    private let state: ProviderRoutingState
    private let parentWindow: NSWindow
    private let preferencesWindow: PreferencesWindow
    private let onDismiss: () -> Void
    private let completion: (
        _ primaryCandidate: String,
        _ fallbackCandidates: [String],
        _ effort: String?
    ) -> Void

    private var sheetWindow: NSWindow?
    private var allCatalogGroups: [(providerDisplayName: String, items: [ProviderCatalogModelItem])] = []

    private var selectedItem: ProviderCatalogModelItem?
    private var selectedEffort: String = "High"
    private var displayedEfforts: [String] = []
    private var fallbacks: [String] = []
    private var isAddingFallback: Bool = false
    private var primaryExecutorFilter: String = "All"
    private var fallbackExecutorFilter: String = "All"

    private var originalExecutor: String = ""

    // UI containers
    private var contentStack: FlippedStackView!
    private var primarySectionContainer: FlippedStackView!
    private var runsViaView: ProviderRunsViaCalloutView!
    private var effortSectionStack: NSStackView!
    private var fallbackSectionStack: NSStackView!

    init(
        route: ProviderRouteState,
        state: ProviderRoutingState,
        parentWindow: NSWindow,
        preferencesWindow: PreferencesWindow,
        onDismiss: @escaping () -> Void,
        completion: @escaping (
            _ primaryCandidate: String,
            _ fallbackCandidates: [String],
            _ effort: String?
        ) -> Void
    ) {
        self.route = route
        self.state = state
        self.parentWindow = parentWindow
        self.preferencesWindow = preferencesWindow
        self.onDismiss = onDismiss
        self.completion = completion
        super.init()

        self.allCatalogGroups = ProviderRouteEditorSheetController.buildCatalogGroups(state: state, formatter: preferencesWindow)
        let initialCandidate = route.candidates.first ?? ""
        self.originalExecutor = preferencesWindow.formatCandidateExecutorName(initialCandidate, state: state)

        for group in allCatalogGroups {
            for item in group.items {
                let match = item.matches(candidate: initialCandidate)
                if match.matched {
                    self.selectedItem = item
                    if let persistedEffort = route.efforts?[initialCandidate] {
                        self.selectedEffort = persistedEffort.capitalized
                    } else if let eff = match.effort {
                        self.selectedEffort = eff
                    }
                    break
                }
            }
            if self.selectedItem != nil { break }
        }

        if self.selectedItem == nil {
            self.selectedItem = allCatalogGroups.first?.items.first
        }
        if let item = self.selectedItem,
           item.supportsEffort,
           !item.configurableEfforts.contains(self.selectedEffort.lowercased())
        {
            self.selectedEffort = item.configurableEfforts.first?.capitalized ?? "High"
        }
        self.primaryExecutorFilter = self.selectedItem?.providerDisplayName ?? "All"

        self.fallbacks = route.fallback
    }

    static func buildCatalogGroups(
        state: ProviderRoutingState,
        formatter: PreferencesWindow
    ) -> [(providerDisplayName: String, items: [ProviderCatalogModelItem])] {
        let preferredOrder = ["antigravity", "claude", "codex"]
        let enabledProviders = state.providers.filter(\.enabled).sorted { p1, p2 in
            let i1 = preferredOrder.firstIndex(of: p1.executor.lowercased()) ?? 99
            let i2 = preferredOrder.firstIndex(of: p2.executor.lowercased()) ?? 99
            return i1 < i2
        }

        var result: [(providerDisplayName: String, items: [ProviderCatalogModelItem])] = []

        for provider in enabledProviders {
            let exec = provider.executor.lowercased()
            let providerDisplayName = formatter.formatProviderName(exec)
            let catalog = state.catalogs.first(where: { $0.agent == provider.executor })

            var rawModelList: [(id: String, catalogModel: ProviderCatalogModelState?)] = []
            var seenRawIds = Set<String>()

            if let cat = catalog {
                for m in cat.models {
                    if !seenRawIds.contains(m.id) {
                        seenRawIds.insert(m.id)
                        rawModelList.append((id: m.id, catalogModel: m))
                    }
                }
            }

            for id in provider.models {
                if !seenRawIds.contains(id) {
                    seenRawIds.insert(id)
                    rawModelList.append((id: id, catalogModel: nil))
                }
            }

            var items: [ProviderCatalogModelItem] = []
            var seenBaseIds = Set<String>()

            func splitEffortSuffix(_ modelId: String) -> (base: String, effort: String?) {
                for effort in ["high", "medium", "low"] {
                    let suffix = "-\(effort)"
                    if modelId.hasSuffix(suffix) {
                        return (String(modelId.dropLast(suffix.count)), effort)
                    }
                }
                return (modelId, nil)
            }

            for entry in rawModelList {
                let rawId = entry.id
                let parsedModel = splitEffortSuffix(rawId)
                let baseId = parsedModel.base

                if seenBaseIds.contains(baseId) {
                    continue
                }
                seenBaseIds.insert(baseId)

                let familyCatalogModels = catalog?.models.filter {
                    splitEffortSuffix($0.id).base == baseId
                } ?? []
                let catModel = entry.catalogModel
                    ?? familyCatalogModels.first(where: { $0.id == baseId })
                    ?? familyCatalogModels.first(where: { $0.id == "\(baseId)-high" })
                    ?? familyCatalogModels.first

                var supportedEffortSet = Set<String>()
                var runtimeEffortSet = Set<String>()
                for model in familyCatalogModels {
                    // Gemini exposes effort as model-id variants. Codex and
                    // Claude expose it as a separate runtime option persisted
                    // in the route's effort map.
                    if let effort = splitEffortSuffix(model.id).effort {
                        supportedEffortSet.insert(effort)
                    }
                    for effort in model.supportedReasoningEfforts ?? [] {
                        runtimeEffortSet.insert(effort.lowercased())
                    }
                }
                for model in rawModelList where splitEffortSuffix(model.id).base == baseId {
                    if let effort = splitEffortSuffix(model.id).effort {
                        supportedEffortSet.insert(effort)
                    }
                }
                let supportedEfforts = ["low", "medium", "high"].filter {
                    supportedEffortSet.contains($0)
                }
                let runtimeEfforts = [
                    "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
                ].filter {
                    runtimeEffortSet.contains($0)
                }

                let displayName: String
                switch baseId {
                case "gemini-3.7-flash": displayName = "Gemini 3.7 Flash"
                case "gemini-3.6-flash": displayName = "Gemini 3.6 Flash"
                case "gemini-3.1-pro": displayName = "Gemini 3.1 Pro"
                case "opus", "claude-opus-5": displayName = "Claude Opus 5"
                case "sonnet", "claude-sonnet-5": displayName = "Claude Sonnet 5"
                case "haiku", "claude-haiku-4-5-20251001", "claude-haiku-4.5": displayName = "Claude Haiku 4.5"
                case "gpt-5.6-sol": displayName = "GPT-5.6 Sol"
                case "gpt-5.6-terra": displayName = "GPT-5.6 Terra"
                case "gpt-5.6-luna": displayName = "GPT-5.6 Luna"
                case "gpt-5.5": displayName = "GPT-5.5"
                case "gpt-5.4": displayName = "GPT-5.4"
                case "gpt-5.4-mini": displayName = "GPT-5.4 Mini"
                default:
                    displayName = formatter.formatCandidateModelName(baseId)
                }

                let isRetired = state.modelHealth.missing.contains(rawId)
                    || state.modelHealth.missing.contains(baseId)
                    || state.modelHealth.missing.contains("\(provider.id):\(rawId)")
                    || state.modelHealth.missing.contains("\(provider.id):\(baseId)")

                items.append(ProviderCatalogModelItem(
                    providerId: provider.id,
                    executor: provider.executor,
                    providerDisplayName: providerDisplayName,
                    baseModelId: baseId,
                    displayName: displayName,
                    supportedEfforts: supportedEfforts,
                    runtimeEfforts: runtimeEfforts,
                    inputPrice: catModel?.inputPer1M,
                    outputPrice: catModel?.outputPer1M,
                    pricingSource: catModel?.priceSource,
                    promo: catModel?.promo,
                    note: catModel?.note,
                    tag: catModel?.tag,
                    isRetired: isRetired,
                    effortCapabilitiesKnown: !supportedEfforts.isEmpty
                        || catModel?.supportedReasoningEfforts != nil
                ))
            }

            if !items.isEmpty {
                result.append((providerDisplayName: providerDisplayName, items: items))
            }
        }

        return result
    }

    func show() {
        let windowRect = NSRect(x: 0, y: 0, width: 620, height: 680)
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
            footerView.leadingAnchor.constraint(equalTo: rootView.leadingAnchor),
            footerView.trailingAnchor.constraint(equalTo: rootView.trailingAnchor),

            scrollView.topAnchor.constraint(equalTo: headerView.bottomAnchor, constant: 12),
            scrollView.leadingAnchor.constraint(equalTo: rootView.leadingAnchor, constant: 24),
            scrollView.trailingAnchor.constraint(equalTo: rootView.trailingAnchor, constant: -24),
            scrollView.bottomAnchor.constraint(equalTo: footerView.topAnchor, constant: -8)
        ])

        let clipView = NSClipView()
        clipView.drawsBackground = false
        scrollView.contentView = clipView

        contentStack = FlippedStackView()
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

        buildPrimarySection()
        buildEffortSection()
        buildFallbacksSection()

        parentWindow.beginSheet(win) { [weak self] _ in
            self?.sheetWindow = nil
            self?.onDismiss()
        }
    }

    private func buildHeaderView() -> NSView {
        let header = NSStackView()
        header.orientation = .vertical
        header.alignment = .leading
        header.spacing = 4
        header.translatesAutoresizingMaskIntoConstraints = false

        let topRow = NSStackView()
        topRow.orientation = .horizontal
        topRow.alignment = .centerY
        topRow.spacing = 8

        let titleLabel = NSTextField(labelWithString: String(format: L10n.tr("preferences.provider.editor.title"), preferencesWindow.formatRoleDisplayName(route.role)))
        titleLabel.font = NSFont.systemFont(ofSize: 16.5, weight: .bold)
        titleLabel.textColor = Palette.primaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        topRow.addArrangedSubview(titleLabel)

        let slugBadge = ProviderBadgeView(text: preferencesWindow.formatRoleSlug(route.role))
        topRow.addArrangedSubview(slugBadge)

        header.addArrangedSubview(topRow)

        let descLabel = NSTextField(wrappingLabelWithString: L10n.tr("preferences.provider.editor.desc"))
        descLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        descLabel.textColor = Palette.secondaryText
        descLabel.isEditable = false
        descLabel.isSelectable = false
        descLabel.isBordered = false
        descLabel.drawsBackground = false
        header.addArrangedSubview(descLabel)

        return header
    }

    private func buildFooterView() -> NSView {
        let footer = NSView()
        footer.translatesAutoresizingMaskIntoConstraints = false

        let sep = NSView()
        sep.translatesAutoresizingMaskIntoConstraints = false
        sep.wantsLayer = true
        sep.layer?.backgroundColor = Palette.separator.cgColor
        footer.addSubview(sep)

        let buttonStack = NSStackView()
        buttonStack.orientation = .horizontal
        buttonStack.alignment = .centerY
        buttonStack.spacing = 10
        buttonStack.translatesAutoresizingMaskIntoConstraints = false
        footer.addSubview(buttonStack)

        NSLayoutConstraint.activate([
            sep.topAnchor.constraint(equalTo: footer.topAnchor),
            sep.leadingAnchor.constraint(equalTo: footer.leadingAnchor),
            sep.trailingAnchor.constraint(equalTo: footer.trailingAnchor),
            sep.heightAnchor.constraint(equalToConstant: 0.5),

            buttonStack.topAnchor.constraint(equalTo: sep.bottomAnchor, constant: 12),
            buttonStack.trailingAnchor.constraint(equalTo: footer.trailingAnchor, constant: -24),
            buttonStack.bottomAnchor.constraint(equalTo: footer.bottomAnchor, constant: -4)
        ])

        let cancelBtn = NSButton(
            title: L10n.tr("preferences.provider.cancel"),
            target: self,
            action: #selector(cancelAction(_:))
        )
        cancelBtn.bezelStyle = .rounded
        cancelBtn.keyEquivalent = "\u{1b}"
        buttonStack.addArrangedSubview(cancelBtn)

        let saveBtn = ProviderAccentButton()
        saveBtn.title = L10n.tr("preferences.provider.save")
        saveBtn.target = self
        saveBtn.action = #selector(saveAction(_:))
        saveBtn.keyEquivalent = "\r"
        buttonStack.addArrangedSubview(saveBtn)

        return footer
    }

    private var primaryPickerSlot = FlippedStackView()

    private func buildPrimarySection() {
        primarySectionContainer = FlippedStackView()
        primarySectionContainer.orientation = .vertical
        primarySectionContainer.alignment = .leading
        primarySectionContainer.spacing = 8
        primarySectionContainer.translatesAutoresizingMaskIntoConstraints = false

        let sectionLabel = makeSectionHeaderLabel(L10n.tr("preferences.provider.editor.primary_section"))
        primarySectionContainer.addArrangedSubview(sectionLabel)

        primaryPickerSlot.orientation = .vertical
        primaryPickerSlot.alignment = .leading
        primaryPickerSlot.spacing = 8
        primaryPickerSlot.translatesAutoresizingMaskIntoConstraints = false
        primarySectionContainer.addArrangedSubview(primaryPickerSlot)
        primaryPickerSlot.widthAnchor.constraint(equalTo: primarySectionContainer.widthAnchor).isActive = true

        renderPrimaryContent()

        contentStack.addArrangedSubview(primarySectionContainer)
        primarySectionContainer.widthAnchor.constraint(equalTo: contentStack.widthAnchor).isActive = true
    }

    private func renderPrimaryContent() {
        for v in primaryPickerSlot.arrangedSubviews {
            primaryPickerSlot.removeArrangedSubview(v)
            v.removeFromSuperview()
        }

        let picker = buildModelPickerView(
            selectedValue: selectedItem.map { "\($0.providerId):\($0.baseModelId)" },
            effort: selectedEffort,
            exclude: [],
            executorFilter: primaryExecutorFilter,
            alertCandidates: [selectedItem?.candidateId(forEffort: selectedEffort) ?? ""] + fallbacks,
            onFilterChange: { [weak self] filter in
                self?.primaryExecutorFilter = filter
                self?.renderPrimaryContent()
            },
            onPick: { [weak self] item in
                self?.selectPrimaryItem(item)
            }
        )
        primaryPickerSlot.addArrangedSubview(picker)
        picker.widthAnchor.constraint(equalTo: primaryPickerSlot.widthAnchor).isActive = true

        runsViaView = ProviderRunsViaCalloutView()
        if let sel = selectedItem {
            runsViaView.update(selectedExecutor: sel.providerDisplayName, originalExecutor: originalExecutor)
        }
        primaryPickerSlot.addArrangedSubview(runsViaView)
        runsViaView.widthAnchor.constraint(equalTo: primaryPickerSlot.widthAnchor).isActive = true
    }

    private func buildEffortSection() {
        effortSectionStack = NSStackView()
        effortSectionStack.orientation = .vertical
        effortSectionStack.alignment = .leading
        effortSectionStack.spacing = 6
        effortSectionStack.translatesAutoresizingMaskIntoConstraints = false

        renderEffortContent()

        contentStack.addArrangedSubview(effortSectionStack)
        effortSectionStack.widthAnchor.constraint(equalTo: contentStack.widthAnchor).isActive = true
    }

    private func renderEffortContent() {
        for v in effortSectionStack.arrangedSubviews {
            effortSectionStack.removeArrangedSubview(v)
            v.removeFromSuperview()
        }

        let sectionLabel = makeSectionHeaderLabel(L10n.tr("preferences.provider.editor.effort_section"))
        effortSectionStack.addArrangedSubview(sectionLabel)

        if let sel = selectedItem {
            let canPersistEffort = sel.supportsEffort
            displayedEfforts = canPersistEffort ? sel.configurableEfforts : []
            let visibleEfforts = canPersistEffort
                ? sel.configurableEfforts
                : ["low", "medium", "high"]
            let seg = NSSegmentedControl(
                labels: visibleEfforts.map(localizedEffortName),
                trackingMode: .selectOne,
                target: self,
                action: #selector(effortChanged(_:))
            )
            seg.segmentStyle = .rounded
            seg.isEnabled = canPersistEffort
            seg.selectedSegment = canPersistEffort
                ? (displayedEfforts.firstIndex(of: selectedEffort.lowercased()) ?? 0)
                : -1
            effortSectionStack.addArrangedSubview(seg)

            if !canPersistEffort {
                let reason: String
                if sel.effortCapabilitiesKnown {
                    reason = String(
                        format: L10n.tr("preferences.provider.editor.effort_none"),
                        sel.displayName)
                } else {
                    reason = L10n.tr(
                        "preferences.provider.editor.effort_capabilities_unavailable")
                }
                let noneLabel = NSTextField(wrappingLabelWithString: reason)
                noneLabel.font = NSFont.systemFont(ofSize: 12, weight: .regular)
                noneLabel.textColor = Palette.secondaryText
                noneLabel.isEditable = false
                noneLabel.isSelectable = false
                noneLabel.isBordered = false
                noneLabel.drawsBackground = false
                effortSectionStack.addArrangedSubview(noneLabel)
            }
        } else {
            displayedEfforts = []
        }
    }

    private func buildFallbacksSection() {
        fallbackSectionStack = NSStackView()
        fallbackSectionStack.orientation = .vertical
        fallbackSectionStack.alignment = .leading
        fallbackSectionStack.spacing = 8
        fallbackSectionStack.translatesAutoresizingMaskIntoConstraints = false

        renderFallbacksContent()

        contentStack.addArrangedSubview(fallbackSectionStack)
        fallbackSectionStack.widthAnchor.constraint(equalTo: contentStack.widthAnchor).isActive = true
    }

    private func renderFallbacksContent() {
        for v in fallbackSectionStack.arrangedSubviews {
            fallbackSectionStack.removeArrangedSubview(v)
            v.removeFromSuperview()
        }

        let headerStack = NSStackView()
        headerStack.orientation = .horizontal
        headerStack.alignment = .firstBaseline
        headerStack.spacing = 8

        let sectionLabel = makeSectionHeaderLabel(L10n.tr("preferences.provider.editor.fallbacks_section"))
        headerStack.addArrangedSubview(sectionLabel)

        let hintLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.editor.fallback_hint"))
        hintLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        hintLabel.textColor = Palette.secondaryText
        hintLabel.isEditable = false
        hintLabel.isSelectable = false
        hintLabel.isBordered = false
        hintLabel.drawsBackground = false
        headerStack.addArrangedSubview(hintLabel)

        fallbackSectionStack.addArrangedSubview(headerStack)

        let listCard = CardView()
        listCard.translatesAutoresizingMaskIntoConstraints = false

        if fallbacks.isEmpty && !isAddingFallback {
            let emptyLabel = NSTextField(wrappingLabelWithString: L10n.tr("preferences.provider.editor.fallback_empty"))
            emptyLabel.font = NSFont.systemFont(ofSize: 12, weight: .regular)
            emptyLabel.textColor = Palette.secondaryText
            emptyLabel.isEditable = false
            emptyLabel.isSelectable = false
            emptyLabel.isBordered = false
            emptyLabel.drawsBackground = false

            let emptyContainer = NSView()
            emptyContainer.translatesAutoresizingMaskIntoConstraints = false
            emptyContainer.addSubview(emptyLabel)
            emptyLabel.translatesAutoresizingMaskIntoConstraints = false

            NSLayoutConstraint.activate([
                emptyLabel.topAnchor.constraint(equalTo: emptyContainer.topAnchor, constant: 10),
                emptyLabel.bottomAnchor.constraint(equalTo: emptyContainer.bottomAnchor, constant: -10),
                emptyLabel.leadingAnchor.constraint(equalTo: emptyContainer.leadingAnchor, constant: 14),
                emptyLabel.trailingAnchor.constraint(equalTo: emptyContainer.trailingAnchor, constant: -14)
            ])
            listCard.stackView.addArrangedSubview(emptyContainer)
            emptyContainer.widthAnchor.constraint(equalTo: listCard.stackView.widthAnchor).isActive = true
        } else {
            for (i, fbCandidate) in fallbacks.enumerated() {
                let row = makeFallbackRowView(candidate: fbCandidate, index: i, count: fallbacks.count)
                listCard.stackView.addArrangedSubview(row)
                row.widthAnchor.constraint(equalTo: listCard.stackView.widthAnchor).isActive = true
            }
        }

        fallbackSectionStack.addArrangedSubview(listCard)
        listCard.widthAnchor.constraint(equalTo: fallbackSectionStack.widthAnchor).isActive = true

        if isAddingFallback {
            let addContainer = CardView()
            addContainer.translatesAutoresizingMaskIntoConstraints = false

            let addHead = NSStackView()
            addHead.orientation = .horizontal
            addHead.alignment = .centerY
            addHead.spacing = 8
            addHead.edgeInsets = NSEdgeInsets(top: 10, left: 14, bottom: 8, right: 14)
            addHead.translatesAutoresizingMaskIntoConstraints = false

            let addTitle = NSTextField(labelWithString: L10n.tr("preferences.provider.editor.choose_fallback"))
            addTitle.font = NSFont.systemFont(ofSize: 12.5, weight: .semibold)
            addTitle.textColor = Palette.primaryText
            addTitle.isEditable = false
            addTitle.isSelectable = false
            addTitle.isBordered = false
            addTitle.drawsBackground = false
            addHead.addArrangedSubview(addTitle)

            let spacer = NSView()
            spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
            addHead.addArrangedSubview(spacer)

            let cancelAddBtn = NSButton(
                title: L10n.tr("preferences.provider.cancel"),
                target: self,
                action: #selector(cancelAddFallbackAction(_:))
            )
            cancelAddBtn.bezelStyle = .rounded
            cancelAddBtn.controlSize = .small
            addHead.addArrangedSubview(cancelAddBtn)

            addContainer.stackView.addArrangedSubview(addHead)
            addHead.widthAnchor.constraint(equalTo: addContainer.stackView.widthAnchor).isActive = true

            let currentPrimaryCandidate = selectedItem?.candidateId(forEffort: selectedEffort) ?? ""
            var usedCandidates = [currentPrimaryCandidate] + fallbacks
            if let sel = selectedItem {
                usedCandidates.append(sel.baseModelId)
                usedCandidates.append(sel.displayName)
            }
            for fb in fallbacks {
                let parts = fb.split(separator: ":", maxSplits: 1).map(String.init)
                if parts.count == 2 {
                    usedCandidates.append(parts[1])
                }
            }

            let fallbackPicker = buildModelPickerView(
                selectedValue: nil,
                effort: "High",
                exclude: usedCandidates,
                executorFilter: fallbackExecutorFilter,
                alertCandidates: fallbacks,
                onFilterChange: { [weak self] filter in
                    self?.fallbackExecutorFilter = filter
                    self?.renderFallbacksContent()
                },
                onPick: { [weak self] item in
                    self?.addFallbackItem(item)
                }
            )
            addContainer.stackView.addArrangedSubview(fallbackPicker)
            fallbackPicker.widthAnchor.constraint(equalTo: addContainer.stackView.widthAnchor).isActive = true

            fallbackSectionStack.addArrangedSubview(addContainer)
            addContainer.widthAnchor.constraint(equalTo: fallbackSectionStack.widthAnchor).isActive = true
        } else {
            let addBtn = NSButton(
                title: L10n.tr("preferences.provider.editor.add_fallback"),
                target: self,
                action: #selector(startAddFallbackAction(_:))
            )
            addBtn.bezelStyle = .rounded
            addBtn.controlSize = .small
            fallbackSectionStack.addArrangedSubview(addBtn)
        }
    }

    private func makeFallbackRowView(candidate: String, index: Int, count: Int) -> NSView {
        let row = NSView()
        row.translatesAutoresizingMaskIntoConstraints = false

        let sep = NSView()
        sep.translatesAutoresizingMaskIntoConstraints = false
        sep.wantsLayer = true
        sep.layer?.backgroundColor = Palette.separator.cgColor
        row.addSubview(sep)
        NSLayoutConstraint.activate([
            sep.topAnchor.constraint(equalTo: row.topAnchor),
            sep.leadingAnchor.constraint(equalTo: row.leadingAnchor, constant: 14),
            sep.trailingAnchor.constraint(equalTo: row.trailingAnchor),
            sep.heightAnchor.constraint(equalToConstant: 0.5)
        ])
        sep.isHidden = (index == 0)

        let rowStack = NSStackView()
        rowStack.orientation = .horizontal
        rowStack.alignment = .centerY
        rowStack.spacing = 10
        rowStack.edgeInsets = NSEdgeInsets(top: 7, left: 14, bottom: 7, right: 14)
        rowStack.translatesAutoresizingMaskIntoConstraints = false
        row.addSubview(rowStack)
        NSLayoutConstraint.activate([
            rowStack.topAnchor.constraint(equalTo: row.topAnchor),
            rowStack.leadingAnchor.constraint(equalTo: row.leadingAnchor),
            rowStack.trailingAnchor.constraint(equalTo: row.trailingAnchor),
            rowStack.bottomAnchor.constraint(equalTo: row.bottomAnchor)
        ])

        let numBadge = NSView()
        numBadge.translatesAutoresizingMaskIntoConstraints = false
        numBadge.wantsLayer = true
        numBadge.layer?.cornerRadius = 10
        numBadge.layer?.backgroundColor = NSColor.dynamicColor(
            light: NSColor(white: 0.5, alpha: 0.12),
            dark: NSColor(white: 0.5, alpha: 0.18)
        ).cgColor

        let numLabel = NSTextField(labelWithString: "\(index + 1)")
        numLabel.font = NSFont.systemFont(ofSize: 11, weight: .semibold)
        numLabel.textColor = Palette.secondaryText
        numLabel.alignment = .center
        numLabel.isEditable = false
        numLabel.isSelectable = false
        numLabel.isBordered = false
        numLabel.drawsBackground = false
        numLabel.translatesAutoresizingMaskIntoConstraints = false
        numBadge.addSubview(numLabel)

        NSLayoutConstraint.activate([
            numBadge.widthAnchor.constraint(equalToConstant: 20),
            numBadge.heightAnchor.constraint(equalToConstant: 20),
            numLabel.centerXAnchor.constraint(equalTo: numBadge.centerXAnchor),
            numLabel.centerYAnchor.constraint(equalTo: numBadge.centerYAnchor, constant: -0.5)
        ])
        rowStack.addArrangedSubview(numBadge)

        let nameLabel = NSTextField(labelWithString: preferencesWindow.formatCandidateModelName(candidate))
        nameLabel.font = NSFont.systemFont(ofSize: 12.5, weight: .medium)
        nameLabel.textColor = Palette.primaryText
        nameLabel.isEditable = false
        nameLabel.isSelectable = false
        nameLabel.isBordered = false
        nameLabel.drawsBackground = false
        rowStack.addArrangedSubview(nameLabel)

        let isRetired = isCandidateUnavailable(candidate)
        if isRetired {
            let retBadge = ProviderTagView(text: L10n.tr("preferences.provider.editor.retired"), isBold: true, isUppercase: true)
            rowStack.addArrangedSubview(retBadge)
        }

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        rowStack.addArrangedSubview(spacer)

        let provLabel = NSTextField(labelWithString: preferencesWindow.formatCandidateExecutorName(candidate, state: state))
        provLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        provLabel.textColor = Palette.secondaryText
        provLabel.isEditable = false
        provLabel.isSelectable = false
        provLabel.isBordered = false
        provLabel.drawsBackground = false
        rowStack.addArrangedSubview(provLabel)

        let opsStack = NSStackView()
        opsStack.orientation = .horizontal
        opsStack.alignment = .centerY
        opsStack.spacing = 3

        let upBtn = NSButton(title: "↑", target: self, action: #selector(moveFallbackUpAction(_:)))
        upBtn.bezelStyle = .rounded
        upBtn.controlSize = .small
        upBtn.tag = index
        upBtn.isEnabled = (index > 0)
        upBtn.translatesAutoresizingMaskIntoConstraints = false
        upBtn.widthAnchor.constraint(equalToConstant: 24).isActive = true
        opsStack.addArrangedSubview(upBtn)

        let downBtn = NSButton(title: "↓", target: self, action: #selector(moveFallbackDownAction(_:)))
        downBtn.bezelStyle = .rounded
        downBtn.controlSize = .small
        downBtn.tag = index
        downBtn.isEnabled = (index < count - 1)
        downBtn.translatesAutoresizingMaskIntoConstraints = false
        downBtn.widthAnchor.constraint(equalToConstant: 24).isActive = true
        opsStack.addArrangedSubview(downBtn)

        let delBtn = NSButton(title: "✕", target: self, action: #selector(deleteFallbackAction(_:)))
        delBtn.bezelStyle = .rounded
        delBtn.controlSize = .small
        delBtn.tag = index
        delBtn.translatesAutoresizingMaskIntoConstraints = false
        delBtn.widthAnchor.constraint(equalToConstant: 24).isActive = true
        opsStack.addArrangedSubview(delBtn)

        rowStack.addArrangedSubview(opsStack)

        return row
    }

    private func buildModelPickerView(
        selectedValue: String?,
        effort: String,
        exclude: [String],
        executorFilter: String,
        alertCandidates: [String],
        onFilterChange: @escaping (String) -> Void,
        onPick: @escaping (ProviderCatalogModelItem) -> Void
    ) -> NSView {
        let container = NSStackView()
        container.orientation = .vertical
        container.alignment = .leading
        container.spacing = 0
        container.translatesAutoresizingMaskIntoConstraints = false
        container.wantsLayer = true
        container.layer?.cornerRadius = 8
        container.layer?.borderWidth = 0.5
        container.layer?.borderColor = Palette.cardBorder.cgColor
        container.layer?.backgroundColor = Palette.cardBackground.cgColor

        let pickedItem = allCatalogGroups
            .flatMap(\.items)
            .first(where: { item in
                let itemIdentity = "\(item.providerId):\(item.baseModelId)"
                return selectedValue != nil && itemIdentity == selectedValue
            })

        var warningExecutors = Set<String>()
        for candidate in alertCandidates where isCandidateUnavailable(candidate) {
            warningExecutors.insert(
                preferencesWindow.formatCandidateExecutorName(candidate, state: state))
        }

        let tabStripContainer = NSView()
        tabStripContainer.translatesAutoresizingMaskIntoConstraints = false
        tabStripContainer.wantsLayer = true
        tabStripContainer.layer?.backgroundColor = Palette.chromeBackground.cgColor

        let tabBottomBorder = NSView()
        tabBottomBorder.translatesAutoresizingMaskIntoConstraints = false
        tabBottomBorder.wantsLayer = true
        tabBottomBorder.layer?.backgroundColor = Palette.cardBorder.cgColor
        tabStripContainer.addSubview(tabBottomBorder)

        let tabStrip = NSStackView()
        tabStrip.orientation = .horizontal
        tabStrip.alignment = .bottom
        tabStrip.spacing = 2
        tabStrip.edgeInsets = NSEdgeInsets(top: 5, left: 5, bottom: 0, right: 5)
        tabStrip.translatesAutoresizingMaskIntoConstraints = false
        tabStripContainer.addSubview(tabStrip)

        NSLayoutConstraint.activate([
            tabStrip.topAnchor.constraint(equalTo: tabStripContainer.topAnchor),
            tabStrip.leadingAnchor.constraint(equalTo: tabStripContainer.leadingAnchor),
            tabStrip.trailingAnchor.constraint(equalTo: tabStripContainer.trailingAnchor),
            tabStrip.bottomAnchor.constraint(equalTo: tabStripContainer.bottomAnchor),

            tabBottomBorder.leadingAnchor.constraint(equalTo: tabStripContainer.leadingAnchor),
            tabBottomBorder.trailingAnchor.constraint(equalTo: tabStripContainer.trailingAnchor),
            tabBottomBorder.bottomAnchor.constraint(equalTo: tabStripContainer.bottomAnchor),
            tabBottomBorder.heightAnchor.constraint(equalToConstant: 1)
        ])

        let filterNames = ["All"] + allCatalogGroups.map(\.providerDisplayName)
        for filterName in filterNames {
            let button = ProviderExecutorFilterButton(
                title: filterName,
                isSelected: executorFilter == filterName,
                containsSelection: filterName != "All" && pickedItem?.providerDisplayName == filterName,
                hasWarning: filterName != "All" && warningExecutors.contains(filterName),
                onSelect: {
                    guard executorFilter != filterName else { return }
                    onFilterChange(filterName)
                })
            tabStrip.addArrangedSubview(button)
        }
        let tabSpacer = NSView()
        tabSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        tabStrip.addArrangedSubview(tabSpacer)
        container.addArrangedSubview(tabStripContainer)
        tabStripContainer.widthAnchor.constraint(equalTo: container.widthAnchor).isActive = true

        let visibleGroups = executorFilter == "All"
            ? allCatalogGroups
            : allCatalogGroups.filter { $0.providerDisplayName == executorFilter }

        let listStack = NSStackView()
        listStack.orientation = .vertical
        listStack.alignment = .leading
        listStack.spacing = 0
        listStack.translatesAutoresizingMaskIntoConstraints = false

        var isFirstGroup = true
        for group in visibleGroups {
            if !isFirstGroup && executorFilter == "All" {
                let groupSep = NSView()
                groupSep.translatesAutoresizingMaskIntoConstraints = false
                groupSep.wantsLayer = true
                groupSep.layer?.backgroundColor = Palette.separator.cgColor
                listStack.addArrangedSubview(groupSep)
                NSLayoutConstraint.activate([
                    groupSep.widthAnchor.constraint(equalTo: listStack.widthAnchor),
                    groupSep.heightAnchor.constraint(equalToConstant: 0.5)
                ])
            }
            isFirstGroup = false

            if executorFilter == "All" {
                let groupHeader = NSView()
                groupHeader.translatesAutoresizingMaskIntoConstraints = false
                let headerLbl = NSTextField(labelWithString: group.providerDisplayName.uppercased())
                headerLbl.font = NSFont.systemFont(ofSize: 10, weight: .bold)
                headerLbl.textColor = Palette.sectionHeader
                headerLbl.isEditable = false
                headerLbl.isSelectable = false
                headerLbl.isBordered = false
                headerLbl.drawsBackground = false
                headerLbl.translatesAutoresizingMaskIntoConstraints = false
                groupHeader.addSubview(headerLbl)

                NSLayoutConstraint.activate([
                    headerLbl.topAnchor.constraint(equalTo: groupHeader.topAnchor, constant: 8),
                    headerLbl.bottomAnchor.constraint(equalTo: groupHeader.bottomAnchor, constant: -4),
                    headerLbl.leadingAnchor.constraint(equalTo: groupHeader.leadingAnchor, constant: 14),
                    headerLbl.trailingAnchor.constraint(equalTo: groupHeader.trailingAnchor, constant: -14)
                ])
                listStack.addArrangedSubview(groupHeader)
                groupHeader.widthAnchor.constraint(equalTo: listStack.widthAnchor).isActive = true
            }

            for item in group.items {
                let itemIdentity = "\(item.providerId):\(item.baseModelId)"
                let isSelected = (selectedValue != nil && itemIdentity == selectedValue)
                let isUsed = exclude.contains { exc in
                    exc == item.baseModelId ||
                    exc == item.displayName ||
                    exc.hasSuffix(":\(item.baseModelId)") ||
                    exc.hasPrefix("\(item.providerId):\(item.baseModelId)")
                }

                let row = ProviderModelRowView(
                    item: item,
                    effort: effort,
                    isSelected: isSelected,
                    isUsed: isUsed,
                    onPick: { onPick(item) }
                )
                listStack.addArrangedSubview(row)
                row.widthAnchor.constraint(equalTo: listStack.widthAnchor).isActive = true
            }
        }

        let listScrollView = NSScrollView()
        listScrollView.translatesAutoresizingMaskIntoConstraints = false
        listScrollView.hasVerticalScroller = true
        listScrollView.hasHorizontalScroller = false
        listScrollView.autohidesScrollers = true
        listScrollView.drawsBackground = false
        listScrollView.borderType = .noBorder

        let listDocument = FlippedStackView()
        listDocument.orientation = .vertical
        listDocument.alignment = .leading
        listDocument.spacing = 0
        listDocument.translatesAutoresizingMaskIntoConstraints = false
        listDocument.addArrangedSubview(listStack)
        listScrollView.documentView = listDocument
        NSLayoutConstraint.activate([
            listStack.widthAnchor.constraint(equalTo: listDocument.widthAnchor)
        ])
        NSLayoutConstraint.activate([
            listStack.topAnchor.constraint(equalTo: listDocument.topAnchor),
            listStack.leadingAnchor.constraint(equalTo: listDocument.leadingAnchor),
            listStack.trailingAnchor.constraint(equalTo: listDocument.trailingAnchor),
            listStack.bottomAnchor.constraint(equalTo: listDocument.bottomAnchor),
            listDocument.widthAnchor.constraint(equalTo: listScrollView.contentView.widthAnchor),
        ])

        listScrollView.heightAnchor.constraint(equalToConstant: 188).isActive = true
        container.addArrangedSubview(listScrollView)
        listScrollView.widthAnchor.constraint(equalTo: container.widthAnchor).isActive = true

        DispatchQueue.main.async { [weak listScrollView, weak listDocument, weak listStack] in
            guard let listScrollView = listScrollView,
                  let listDocument = listDocument,
                  let listStack = listStack else { return }
            listDocument.layoutSubtreeIfNeeded()
            listStack.layoutSubtreeIfNeeded()
            if let selectedRow = listStack.arrangedSubviews.first(where: { ($0 as? ProviderModelRowView)?.isRowSelected == true }) {
                selectedRow.scrollToVisible(selectedRow.bounds)
                listScrollView.reflectScrolledClipView(listScrollView.contentView)
            } else {
                listScrollView.contentView.scroll(to: .zero)
                listScrollView.reflectScrolledClipView(listScrollView.contentView)
            }
        }

        if let sel = pickedItem {
            let detailSep = NSView()
            detailSep.translatesAutoresizingMaskIntoConstraints = false
            detailSep.wantsLayer = true
            detailSep.layer?.backgroundColor = Palette.separator.cgColor
            container.addArrangedSubview(detailSep)
            NSLayoutConstraint.activate([
                detailSep.widthAnchor.constraint(equalTo: container.widthAnchor),
                detailSep.heightAnchor.constraint(equalToConstant: 0.5)
            ])

            let detailBox = NSView()
            detailBox.translatesAutoresizingMaskIntoConstraints = false
            detailBox.wantsLayer = true
            detailBox.layer?.backgroundColor = Palette.chromeBackground.cgColor

            let detailInnerStack = NSStackView()
            detailInnerStack.orientation = .vertical
            detailInnerStack.alignment = .leading
            detailInnerStack.spacing = 8
            detailInnerStack.translatesAutoresizingMaskIntoConstraints = false
            detailBox.addSubview(detailInnerStack)

            NSLayoutConstraint.activate([
                detailInnerStack.topAnchor.constraint(equalTo: detailBox.topAnchor, constant: 10),
                detailInnerStack.leadingAnchor.constraint(equalTo: detailBox.leadingAnchor, constant: 14),
                detailInnerStack.trailingAnchor.constraint(equalTo: detailBox.trailingAnchor, constant: -14),
                detailInnerStack.bottomAnchor.constraint(equalTo: detailBox.bottomAnchor, constant: -14)
            ])

            if executorFilter != "All" && sel.providerDisplayName != executorFilter {
                let elsewhereLabel = NSTextField(wrappingLabelWithString: String(
                    format: L10n.tr("preferences.provider.editor.selected_elsewhere"),
                    sel.displayName,
                    sel.providerDisplayName))
                elsewhereLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
                elsewhereLabel.textColor = Palette.secondaryText
                elsewhereLabel.isEditable = false
                elsewhereLabel.isSelectable = false
                elsewhereLabel.isBordered = false
                elsewhereLabel.drawsBackground = false
                detailInnerStack.addArrangedSubview(elsewhereLabel)
            }

            let candidateId = sel.candidateId(forEffort: effort)
            let codePill = NSView()
            codePill.translatesAutoresizingMaskIntoConstraints = false
            codePill.wantsLayer = true
            codePill.layer?.cornerRadius = 4
            codePill.layer?.borderWidth = 0.5
            codePill.layer?.borderColor = Palette.cardBorder.cgColor
            codePill.layer?.backgroundColor = NSColor.dynamicColor(
                light: NSColor(white: 0.5, alpha: 0.08),
                dark: NSColor(white: 0.5, alpha: 0.14)
            ).cgColor

            let codeLbl = NSTextField(labelWithString: candidateId)
            codeLbl.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
            codeLbl.textColor = Palette.primaryText
            codeLbl.isEditable = false
            codeLbl.isSelectable = true
            codeLbl.isBordered = false
            codeLbl.drawsBackground = false
            codeLbl.translatesAutoresizingMaskIntoConstraints = false
            codePill.addSubview(codeLbl)

            NSLayoutConstraint.activate([
                codeLbl.topAnchor.constraint(equalTo: codePill.topAnchor, constant: 3),
                codeLbl.bottomAnchor.constraint(equalTo: codePill.bottomAnchor, constant: -3),
                codeLbl.leadingAnchor.constraint(equalTo: codePill.leadingAnchor, constant: 7),
                codeLbl.trailingAnchor.constraint(equalTo: codePill.trailingAnchor, constant: -7)
            ])
            detailInnerStack.addArrangedSubview(codePill)

            let metaGrid = NSStackView()
            metaGrid.orientation = .vertical
            metaGrid.alignment = .leading
            metaGrid.spacing = 4
            metaGrid.translatesAutoresizingMaskIntoConstraints = false

            metaGrid.addArrangedSubview(makeMetaRow(
                key: L10n.tr("preferences.provider.editor.executor_label"),
                value: sel.providerDisplayName
            ))

            if let inputPrice = sel.inputPrice, let outputPrice = sel.outputPrice {
                let inpStr = String(format: "$%.2f", inputPrice)
                let outStr = String(format: "$%.2f", outputPrice)
                let priceText = String(
                    format: L10n.tr("preferences.provider.editor.price_meta"),
                    inpStr,
                    outStr)
                metaGrid.addArrangedSubview(makeMetaRow(
                    key: L10n.tr("preferences.provider.editor.price_label"),
                    value: priceText
                ))

                if sel.pricingSource != nil {
                    metaGrid.addArrangedSubview(makeMetaRow(
                        key: L10n.tr("preferences.provider.editor.source_label"),
                        value: formatPricingSource(sel.pricingSource)
                    ))
                }
            }

            if let promo = sel.promo, !promo.isEmpty {
                metaGrid.addArrangedSubview(makeMetaRow(
                    key: L10n.tr("preferences.provider.editor.promotion_label"),
                    value: promo,
                    isAccent: true
                ))
            }

            if let note = sel.note, !note.isEmpty {
                metaGrid.addArrangedSubview(makeMetaRow(
                    key: L10n.tr("preferences.provider.editor.note_label"),
                    value: note
                ))
            }

            detailInnerStack.addArrangedSubview(metaGrid)
            metaGrid.widthAnchor.constraint(equalTo: detailInnerStack.widthAnchor).isActive = true

            container.addArrangedSubview(detailBox)
            detailBox.widthAnchor.constraint(equalTo: container.widthAnchor).isActive = true
        }

        return container
    }

    private func makeMetaRow(key: String, value: String, isAccent: Bool = false) -> NSView {
        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .firstBaseline
        row.spacing = 8
        row.translatesAutoresizingMaskIntoConstraints = false

        let kLabel = NSTextField(labelWithString: key)
        kLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        kLabel.textColor = Palette.secondaryText
        kLabel.isEditable = false
        kLabel.isSelectable = false
        kLabel.isBordered = false
        kLabel.drawsBackground = false
        kLabel.translatesAutoresizingMaskIntoConstraints = false
        kLabel.widthAnchor.constraint(equalToConstant: 110).isActive = true
        row.addArrangedSubview(kLabel)

        let vLabel = NSTextField(wrappingLabelWithString: value)
        vLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        vLabel.textColor = isAccent ? NSColor.controlAccentColor : Palette.primaryText
        vLabel.isEditable = false
        vLabel.isSelectable = false
        vLabel.isBordered = false
        vLabel.drawsBackground = false
        row.addArrangedSubview(vLabel)

        return row
    }

    private func formatPricingSource(_ src: String?) -> String {
        guard let s = src?.lowercased() else { return L10n.tr("preferences.provider.editor.source_exact") }
        if s.contains("family") {
            return L10n.tr("preferences.provider.editor.source_family")
        } else if s.contains("fallback") {
            return L10n.tr("preferences.provider.editor.source_fallback")
        } else {
            return L10n.tr("preferences.provider.editor.source_exact")
        }
    }

    private func isCandidateUnavailable(_ candidate: String) -> Bool {
        let parts = candidate.split(separator: ":", maxSplits: 1).map(String.init)
        let modelId = parts.count == 2 ? parts[1] : candidate
        return state.modelHealth.missing.contains(candidate)
            || state.modelHealth.missing.contains(modelId)
    }

    private func makeSectionHeaderLabel(_ title: String) -> NSTextField {
        let lbl = NSTextField(labelWithString: title.uppercased())
        lbl.font = NSFont.systemFont(ofSize: 10.5, weight: .bold)
        lbl.textColor = Palette.sectionHeader
        lbl.isEditable = false
        lbl.isSelectable = false
        lbl.isBordered = false
        lbl.drawsBackground = false
        return lbl
    }

    private func localizedEffortName(_ effort: String) -> String {
        switch effort.lowercased() {
        case "low":
            return L10n.tr("preferences.provider.editor.effort.low")
        case "medium":
            return L10n.tr("preferences.provider.editor.effort.medium")
        case "high":
            return L10n.tr("preferences.provider.editor.effort.high")
        case "xhigh":
            return L10n.tr("preferences.provider.editor.effort.xhigh")
        case "max":
            return L10n.tr("preferences.provider.editor.effort.max")
        case "ultra":
            return L10n.tr("preferences.provider.editor.effort.ultra")
        case "minimal":
            return L10n.tr("preferences.provider.editor.effort.minimal")
        default:
            return effort.capitalized
        }
    }

    private func selectPrimaryItem(_ item: ProviderCatalogModelItem) {
        selectedItem = item
        if item.supportsEffort && !item.configurableEfforts.contains(selectedEffort.lowercased()) {
            selectedEffort = item.configurableEfforts.first?.capitalized ?? "High"
        }
        renderPrimaryContent()
        renderEffortContent()
        if isAddingFallback {
            renderFallbacksContent()
        }
    }

    @objc private func effortChanged(_ sender: NSSegmentedControl) {
        guard displayedEfforts.indices.contains(sender.selectedSegment) else {
            return
        }
        selectedEffort = displayedEfforts[sender.selectedSegment].capitalized
        renderPrimaryContent()
        if isAddingFallback {
            renderFallbacksContent()
        }
    }

    @objc private func startAddFallbackAction(_ sender: Any) {
        fallbackExecutorFilter = "All"
        isAddingFallback = true
        renderFallbacksContent()
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.contentStack.layoutSubtreeIfNeeded()
            self.fallbackSectionStack.layoutSubtreeIfNeeded()
            if let addContainer = self.fallbackSectionStack.arrangedSubviews.last {
                addContainer.scrollToVisible(addContainer.bounds)
            }
        }
    }

    @objc private func cancelAddFallbackAction(_ sender: Any) {
        isAddingFallback = false
        renderFallbacksContent()
    }

    private func addFallbackItem(_ item: ProviderCatalogModelItem) {
        let candidateId = item.candidateId(forEffort: "High")
        if !fallbacks.contains(candidateId) {
            fallbacks.append(candidateId)
        }
        isAddingFallback = false
        renderFallbacksContent()
    }

    @objc private func moveFallbackUpAction(_ sender: NSButton) {
        let i = sender.tag
        guard i > 0 && i < fallbacks.count else { return }
        fallbacks.swapAt(i, i - 1)
        renderFallbacksContent()
    }

    @objc private func moveFallbackDownAction(_ sender: NSButton) {
        let i = sender.tag
        guard i >= 0 && i < fallbacks.count - 1 else { return }
        fallbacks.swapAt(i, i + 1)
        renderFallbacksContent()
    }

    @objc private func deleteFallbackAction(_ sender: NSButton) {
        let i = sender.tag
        guard i >= 0 && i < fallbacks.count else { return }
        fallbacks.remove(at: i)
        renderFallbacksContent()
    }

    @objc private func cancelAction(_ sender: Any) {
        guard let win = sheetWindow else { return }
        parentWindow.endSheet(win, returnCode: .cancel)
        win.orderOut(nil)
    }

    @objc private func saveAction(_ sender: Any) {
        guard let win = sheetWindow, let sel = selectedItem else { return }
        let primaryCandidate = sel.candidateId(forEffort: selectedEffort)
        let routeEffort = sel.storesEffortInRoute ? selectedEffort.lowercased() : nil
        completion(primaryCandidate, fallbacks, routeEffort)
        parentWindow.endSheet(win, returnCode: .OK)
        win.orderOut(nil)
    }
}
