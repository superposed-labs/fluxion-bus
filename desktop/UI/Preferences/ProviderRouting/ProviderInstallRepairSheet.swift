import AppKit
import Foundation

// Install / repair flow for the Codex integration: builds a plan of file
// changes, shows them step by step, and applies them.

// MARK: - Codex Install / Repair Sheet Controller

class ProviderInstallRepairSheetController: NSObject, NSWindowDelegate {
    private let state: ProviderRoutingState
    private let parentWindow: NSWindow
    private let preferencesWindow: PreferencesWindow
    private let onDismiss: () -> Void
    private let onComplete: () -> Void

    private var sheetWindow: NSWindow?

    private var mode: ProviderCodexInstallMode
    private var phase: ProviderCodexInstallPhase = .confirm
    private var selectedModel: String
    private var selectedTabName: String = "config.toml"

    private var modelOptions: [ProviderCodexRoleModelOption] = []
    private var plan: ProviderCodexInstallPlan!

    private var steps: [ProviderCodexInstallStepItem] = []
    private var currentRunningStepIndex: Int = 0

    private var headerContainer: NSView!
    private var contentContainer: NSStackView!
    private var footerContainer: NSView!
    private var codeTextView: NSTextView?
    private var fileHeaderLabel: NSTextField?
    private var fileActionBadge: NSTextField?
    private var fileTabBarStack: NSStackView?

    init(
        state: ProviderRoutingState,
        plan: ProviderCodexInstallPlan,
        parentWindow: NSWindow,
        preferencesWindow: PreferencesWindow,
        onDismiss: @escaping () -> Void,
        onComplete: @escaping () -> Void
    ) {
        self.state = state
        self.parentWindow = parentWindow
        self.preferencesWindow = preferencesWindow
        self.onDismiss = onDismiss
        self.onComplete = onComplete
        self.mode = plan.mode
        self.selectedModel = plan.model
        self.plan = plan

        super.init()

        let configuredRoleModel =
            state.codex.roles.first(where: { !$0.model.isEmpty })?.model
            ?? plan.model
        buildModelOptions(configuredModel: configuredRoleModel)
    }

    private func buildModelOptions(configuredModel: String) {
        var options: [ProviderCodexRoleModelOption] = []

        // Tiers come from the backend's ranking of this vendor's current
        // lineup. They used to be matched by codename — `terra`, `sol`, `luna`
        // as substrings — which silently stops working the day a vendor names
        // its next generation something else, leaving the fallback to
        // "recommend" whatever happened to sort first, the priciest model.
        let catalogModels = state.catalogs.first(where: { $0.agent.lowercased() == "codex" })?.models ?? []
        let lineup = state.executors.first { $0.executor.lowercased() == "codex" }?.lineup ?? []

        // Internal non-role tasks are never a choice for a role thread.
        let eligible = catalogModels.filter { model in
            let id = model.id.lowercased()
            return !id.contains("auto-review") && !id.contains("eval")
                && !id.contains("internal") && !id.contains("embed")
        }
        let ranked = lineup.isEmpty
            ? Array(eligible.prefix(3)).map { $0.id }
            : lineup.filter { id in eligible.contains { $0.id == id } }

        let reasons = [
            L10n.tr("preferences.provider.codex.tier.recommended"),
            L10n.tr("preferences.provider.codex.tier.highest"),
            L10n.tr("preferences.provider.codex.tier.lowest")
        ]
        let recommendedIndex = ranked.isEmpty ? 0 : (ranked.count - 1) / 2
        var dynamicOptions: [ProviderCodexRoleModelOption] = []
        for (index, modelId) in ranked.enumerated() {
            let model = eligible.first { $0.id == modelId }
            let name = model?.label?.isEmpty == false
                ? model!.label!
                : preferencesWindow.formatModelDisplayName(modelId)
            let reason: String
            if index == recommendedIndex {
                reason = reasons[0]
            } else if index < recommendedIndex {
                reason = reasons[1]
            } else {
                reason = reasons[2]
            }
            dynamicOptions.append(ProviderCodexRoleModelOption(
                id: modelId,
                name: name,
                isRecommended: index == recommendedIndex,
                why: model?.note?.isEmpty == false ? model!.note! : reason
            ))
        }

        let knownIds = Set(dynamicOptions.map(\.id))

        if !configuredModel.isEmpty && !knownIds.contains(configuredModel) {
            options.append(ProviderCodexRoleModelOption(
                id: configuredModel,
                name: preferencesWindow.formatCandidateModelName(configuredModel),
                isRecommended: false,
                why: L10n.tr("preferences.provider.codex.install_sheet.stale.desc"),
                isStale: true
            ))
            self.selectedModel =
                dynamicOptions.first(where: \.isRecommended)?.id
                ?? dynamicOptions.first?.id
                ?? configuredModel
        } else if knownIds.contains(configuredModel) {
            self.selectedModel = configuredModel
        } else {
            self.selectedModel =
                dynamicOptions.first(where: \.isRecommended)?.id
                ?? dynamicOptions.first?.id
                ?? configuredModel
        }

        options.append(contentsOf: dynamicOptions)
        self.modelOptions = options
    }

    private func updatePlan() {
        preferencesWindow.loadCodexIntegrationPlan(model: selectedModel, mode: mode) {
            [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let plan):
                self.plan = plan
                self.mode = plan.mode
                if !plan.files.contains(where: { $0.name == self.selectedTabName }) {
                    self.selectedTabName = plan.files.first?.name ?? "config.toml"
                }
                self.renderAll()
            case .failure(let error):
                self.preferencesWindow.showProviderRoutingError(error.localizedDescription)
            }
        }
    }

    func show() {
        let windowRect = NSRect(x: 0, y: 0, width: 640, height: 690)
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

        headerContainer = NSView()
        headerContainer.translatesAutoresizingMaskIntoConstraints = false
        rootView.addSubview(headerContainer)

        footerContainer = NSView()
        footerContainer.translatesAutoresizingMaskIntoConstraints = false
        rootView.addSubview(footerContainer)

        let scrollView = NSScrollView()
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.hasVerticalScroller = true
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.drawsBackground = false
        rootView.addSubview(scrollView)

        NSLayoutConstraint.activate([
            headerContainer.topAnchor.constraint(equalTo: rootView.topAnchor, constant: 18),
            headerContainer.leadingAnchor.constraint(equalTo: rootView.leadingAnchor, constant: 20),
            headerContainer.trailingAnchor.constraint(equalTo: rootView.trailingAnchor, constant: -20),

            footerContainer.bottomAnchor.constraint(equalTo: rootView.bottomAnchor, constant: -14),
            footerContainer.leadingAnchor.constraint(equalTo: rootView.leadingAnchor),
            footerContainer.trailingAnchor.constraint(equalTo: rootView.trailingAnchor),

            scrollView.topAnchor.constraint(equalTo: headerContainer.bottomAnchor, constant: 12),
            scrollView.leadingAnchor.constraint(equalTo: rootView.leadingAnchor, constant: 20),
            scrollView.trailingAnchor.constraint(equalTo: rootView.trailingAnchor, constant: -20),
            scrollView.bottomAnchor.constraint(equalTo: footerContainer.topAnchor, constant: -6)
        ])

        let clipView = ProviderFlippedClipView()
        clipView.drawsBackground = false
        scrollView.contentView = clipView

        contentContainer = ProviderFlippedStackView()
        contentContainer.orientation = .vertical
        contentContainer.alignment = .leading
        contentContainer.spacing = 14
        contentContainer.translatesAutoresizingMaskIntoConstraints = false
        scrollView.documentView = contentContainer

        NSLayoutConstraint.activate([
            contentContainer.topAnchor.constraint(equalTo: clipView.topAnchor),
            contentContainer.leadingAnchor.constraint(equalTo: clipView.leadingAnchor),
            contentContainer.trailingAnchor.constraint(equalTo: clipView.trailingAnchor),
            contentContainer.widthAnchor.constraint(equalTo: scrollView.widthAnchor, constant: -14)
        ])

        renderAll()

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

    private func updateWindowHeight(targetHeight: CGFloat, animated: Bool = true) {
        guard let win = sheetWindow, win.sheetParent != nil else { return }
        let currentFrame = win.frame
        guard abs(currentFrame.height - targetHeight) > 2 else { return }
        let topY = currentFrame.maxY
        let newY = topY - targetHeight
        let newFrame = NSRect(x: currentFrame.origin.x, y: newY, width: currentFrame.width, height: targetHeight)
        if animated {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.22
                context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                win.animator().setFrame(newFrame, display: true)
            }
        } else {
            win.setFrame(newFrame, display: true)
        }
    }

    private func renderAll() {
        renderHeader()
        renderBody()
        renderFooter()

        let targetH: CGFloat = (phase == .confirm) ? 690 : 430
        updateWindowHeight(targetHeight: targetH, animated: true)
    }

    private func renderHeader() {
        for v in headerContainer.subviews { v.removeFromSuperview() }

        let headerStack = NSStackView()
        headerStack.orientation = .vertical
        headerStack.alignment = .leading
        headerStack.spacing = 2
        headerStack.translatesAutoresizingMaskIntoConstraints = false
        headerContainer.addSubview(headerStack)

        NSLayoutConstraint.activate([
            headerStack.topAnchor.constraint(equalTo: headerContainer.topAnchor),
            headerStack.bottomAnchor.constraint(equalTo: headerContainer.bottomAnchor),
            headerStack.leadingAnchor.constraint(equalTo: headerContainer.leadingAnchor),
            headerStack.trailingAnchor.constraint(equalTo: headerContainer.trailingAnchor)
        ])

        let titleText: String
        let subText: String
        let codexHome = state.codex.home ?? "~/.codex"
        let verb = modeVerb()
        let writeCount = plan.files.filter { $0.action == .write || $0.action == .rewrite }.count

        switch phase {
        case .confirm:
            switch mode {
            case .install:
                titleText = L10n.tr("preferences.provider.codex.install_sheet.title.install")
            case .missing, .corrupt:
                titleText = L10n.tr("preferences.provider.codex.install_sheet.title.repair")
            case .reinstall:
                titleText = L10n.tr("preferences.provider.codex.install_sheet.title.reinstall")
            }
            subText = String(format: L10n.tr("preferences.provider.codex.install_sheet.sub.writes_to"), codexHome.replacingOccurrences(of: NSHomeDirectory(), with: "~"))

        case .running:
            titleText = String(format: L10n.tr("preferences.provider.codex.install_sheet.running.title"), verb)
            subText = String(format: L10n.tr("preferences.provider.codex.install_sheet.running.sub"), codexHome.replacingOccurrences(of: NSHomeDirectory(), with: "~"))

        case .failed:
            titleText = String(format: L10n.tr("preferences.provider.codex.install_sheet.failed.title"), verb)
            subText = L10n.tr("preferences.provider.codex.install_sheet.failed.sub")

        case .done:
            let pastVerb: String
            switch mode {
            case .install: pastVerb = L10n.tr("preferences.provider.codex.install_sheet.past.installed")
            case .missing, .corrupt: pastVerb = L10n.tr("preferences.provider.codex.install_sheet.past.repaired")
            case .reinstall: pastVerb = L10n.tr("preferences.provider.codex.install_sheet.past.reinstalled")
            }
            titleText = String(format: L10n.tr("preferences.provider.codex.install_sheet.done.title"), pastVerb)
            let formattedModel = preferencesWindow.formatCandidateModelName(selectedModel)
            subText = String(format: L10n.tr("preferences.provider.codex.install_sheet.done.sub"), writeCount, formattedModel)
        }

        let titleLabel = NSTextField(labelWithString: titleText)
        titleLabel.font = NSFont.systemFont(ofSize: 16.5, weight: .bold)
        titleLabel.textColor = Palette.primaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        headerStack.addArrangedSubview(titleLabel)

        let subLabel = NSTextField(labelWithString: subText)
        subLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        subLabel.textColor = Palette.secondaryText
        subLabel.isEditable = false
        subLabel.isSelectable = false
        subLabel.isBordered = false
        subLabel.drawsBackground = false
        headerStack.addArrangedSubview(subLabel)
    }

    private func renderBody() {
        for v in contentContainer.arrangedSubviews {
            contentContainer.removeArrangedSubview(v)
            v.removeFromSuperview()
        }

        if phase == .confirm {
            renderConfirmBody()
        } else {
            renderProgressBody()
        }
    }

    private func renderConfirmBody() {
        let writeCount = plan.files.filter { $0.action == .write || $0.action == .rewrite }.count
        let bannerTone: ProviderBannerView.Tone = mode == .corrupt ? .error : (mode == .missing ? .warn : .neutral)

        let ledeText: String
        switch mode {
        case .install:
            ledeText = L10n.tr("preferences.provider.codex.install_sheet.lede.install")
        case .missing:
            let missingCount = plan.files.filter {
                $0.role != nil && $0.action == .write
            }.count
            ledeText = String(
                format: L10n.tr("preferences.provider.codex.install_sheet.lede.missing"),
                missingCount
            )
        case .corrupt:
            let corruptName =
                plan.files.first(where: { $0.role != nil && $0.problem != nil })?.name
                ?? L10n.tr("preferences.provider.codex.config_sheet.role.role_file")
            ledeText = String(
                format: L10n.tr("preferences.provider.codex.install_sheet.lede.corrupt"),
                corruptName
            )
        case .reinstall:
            ledeText = L10n.tr("preferences.provider.codex.install_sheet.lede.reinstall")
        }

        let banner = ProviderBannerView(
            tone: bannerTone,
            title: String(
                format: L10n.tr("preferences.provider.codex.install_sheet.banner.files_change"),
                writeCount,
                plan.files.count
            ),
            body: ledeText
        )
        contentContainer.addArrangedSubview(banner)
        banner.widthAnchor.constraint(equalTo: contentContainer.widthAnchor).isActive = true

        // Section 1: Model Selection
        let sec1 = NSStackView()
        sec1.orientation = .vertical
        sec1.alignment = .leading
        sec1.spacing = 6
        sec1.translatesAutoresizingMaskIntoConstraints = false

        let sec1Header = NSStackView()
        sec1Header.orientation = .vertical
        sec1Header.alignment = .leading
        sec1Header.spacing = 2

        let sec1Title = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.install_sheet.section.model").uppercased())
        sec1Title.font = NSFont.systemFont(ofSize: 10.5, weight: .bold)
        sec1Title.textColor = Palette.sectionHeader
        sec1Title.isEditable = false
        sec1Title.isSelectable = false
        sec1Title.isBordered = false
        sec1Title.drawsBackground = false
        sec1Header.addArrangedSubview(sec1Title)

        let sec1Hint = NSTextField(wrappingLabelWithString: L10n.tr("preferences.provider.codex.install_sheet.section.model_hint"))
        sec1Hint.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        sec1Hint.textColor = Palette.secondaryText
        sec1Hint.cell?.wraps = true
        sec1Hint.cell?.isScrollable = false
        sec1Hint.maximumNumberOfLines = 0
        sec1Hint.lineBreakMode = .byWordWrapping
        sec1Hint.isEditable = false
        sec1Hint.isSelectable = false
        sec1Hint.isBordered = false
        sec1Hint.drawsBackground = false
        sec1Hint.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        sec1Hint.setContentHuggingPriority(.defaultLow, for: .horizontal)
        sec1Header.addArrangedSubview(sec1Hint)
        sec1.addArrangedSubview(sec1Header)
        sec1Header.widthAnchor.constraint(equalTo: sec1.widthAnchor).isActive = true

        // Catalog sub-bar
        let catBar = NSStackView()
        catBar.orientation = .horizontal
        catBar.alignment = .centerY
        catBar.distribution = .fill
        catBar.translatesAutoresizingMaskIntoConstraints = false

        let isCatUnavailable = preferencesWindow.providerCatalogsUnavailable

        if isCatUnavailable {
            let errStack = NSStackView()
            errStack.orientation = .horizontal
            errStack.spacing = 5
            errStack.alignment = .centerY

            let errDot = NSView()
            errDot.translatesAutoresizingMaskIntoConstraints = false
            errDot.wantsLayer = true
            errDot.layer?.cornerRadius = 3
            errDot.layer?.backgroundColor = NSColor.systemRed.cgColor
            NSLayoutConstraint.activate([
                errDot.widthAnchor.constraint(equalToConstant: 6),
                errDot.heightAnchor.constraint(equalToConstant: 6)
            ])
            errStack.addArrangedSubview(errDot)

            let errLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.install_sheet.catalog.unavailable"))
            errLabel.font = NSFont.systemFont(ofSize: 11, weight: .medium)
            errLabel.textColor = .systemRed
            errLabel.isEditable = false
            errLabel.isSelectable = false
            errLabel.isBordered = false
            errLabel.drawsBackground = false
            errStack.addArrangedSubview(errLabel)
            catBar.addArrangedSubview(errStack)
        } else {
            let catLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.install_sheet.catalog.loaded"))
            catLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
            catLabel.textColor = Palette.secondaryText
            catLabel.isEditable = false
            catLabel.isSelectable = false
            catLabel.isBordered = false
            catLabel.drawsBackground = false
            catBar.addArrangedSubview(catLabel)
        }

        let spacer1 = NSView()
        spacer1.setContentHuggingPriority(.defaultLow, for: .horizontal)
        catBar.addArrangedSubview(spacer1)

        let refreshBtn = NSButton(
            title: isCatUnavailable
                ? L10n.tr("preferences.provider.codex.install_sheet.catalog.retry")
                : L10n.tr("preferences.provider.codex.install_sheet.catalog.refresh"),
            target: self,
            action: #selector(refreshCatalogAction)
        )
        refreshBtn.bezelStyle = .rounded
        refreshBtn.controlSize = .small
        catBar.addArrangedSubview(refreshBtn)

        sec1.addArrangedSubview(catBar)
        catBar.widthAnchor.constraint(equalTo: sec1.widthAnchor).isActive = true

        if isCatUnavailable {
            let catErrBox = ProviderBannerView(
                tone: .error,
                title: L10n.tr("preferences.provider.codex.install_sheet.catalog.unavailable"),
                body: L10n.tr("preferences.provider.codex.install_sheet.catalog.err_desc")
            )
            sec1.addArrangedSubview(catErrBox)
            catErrBox.widthAnchor.constraint(equalTo: sec1.widthAnchor).isActive = true
        } else {
            let card = CardView()
            card.translatesAutoresizingMaskIntoConstraints = false

            for (idx, opt) in modelOptions.enumerated() {
                if idx > 0 {
                    let sep = makeSeparator()
                    card.stackView.addArrangedSubview(sep)
                    sep.widthAnchor.constraint(equalTo: card.stackView.widthAnchor).isActive = true
                }
                let row = ProviderModelOptionRowView(
                    option: opt,
                    isSelected: opt.id == selectedModel
                ) { [weak self] chosen in
                    self?.selectModel(chosen.id)
                }
                card.stackView.addArrangedSubview(row)
                row.widthAnchor.constraint(equalTo: card.stackView.widthAnchor).isActive = true
            }

            sec1.addArrangedSubview(card)
            card.widthAnchor.constraint(equalTo: sec1.widthAnchor).isActive = true
        }

        contentContainer.addArrangedSubview(sec1)
        sec1.widthAnchor.constraint(equalTo: contentContainer.widthAnchor).isActive = true

        // Section 2: Files Preview
        let sec2 = NSStackView()
        sec2.orientation = .vertical
        sec2.alignment = .leading
        sec2.spacing = 6
        sec2.translatesAutoresizingMaskIntoConstraints = false

        let sec2Header = NSStackView()
        sec2Header.orientation = .horizontal
        sec2Header.alignment = .firstBaseline
        sec2Header.spacing = 6

        let sec2Title = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.install_sheet.section.files").uppercased())
        sec2Title.font = NSFont.systemFont(ofSize: 10.5, weight: .bold)
        sec2Title.textColor = Palette.sectionHeader
        sec2Title.isEditable = false
        sec2Title.isSelectable = false
        sec2Title.isBordered = false
        sec2Title.drawsBackground = false
        sec2Header.addArrangedSubview(sec2Title)

        let sec2Hint = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.install_sheet.section.files_hint"))
        sec2Hint.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        sec2Hint.textColor = Palette.secondaryText
        sec2Hint.isEditable = false
        sec2Hint.isSelectable = false
        sec2Hint.isBordered = false
        sec2Hint.drawsBackground = false
        sec2Header.addArrangedSubview(sec2Hint)
        sec2.addArrangedSubview(sec2Header)

        let previewCard = CardView()
        previewCard.translatesAutoresizingMaskIntoConstraints = false

        // Tab bar container with folder tab pattern
        let tabStripContainer = NSView()
        tabStripContainer.translatesAutoresizingMaskIntoConstraints = false
        tabStripContainer.wantsLayer = true
        tabStripContainer.layer?.backgroundColor = Palette.chromeBackground.cgColor

        let tabBottomBorder = NSView()
        tabBottomBorder.translatesAutoresizingMaskIntoConstraints = false
        tabBottomBorder.wantsLayer = true
        tabBottomBorder.layer?.backgroundColor = Palette.cardBorder.cgColor
        tabStripContainer.addSubview(tabBottomBorder)

        let tabBar = NSStackView()
        tabBar.orientation = .horizontal
        tabBar.spacing = 2
        tabBar.alignment = .bottom
        tabBar.edgeInsets = NSEdgeInsets(top: 5, left: 5, bottom: 0, right: 5)
        tabBar.translatesAutoresizingMaskIntoConstraints = false
        tabStripContainer.addSubview(tabBar)
        self.fileTabBarStack = tabBar

        NSLayoutConstraint.activate([
            tabBar.topAnchor.constraint(equalTo: tabStripContainer.topAnchor),
            tabBar.leadingAnchor.constraint(equalTo: tabStripContainer.leadingAnchor),
            tabBar.trailingAnchor.constraint(equalTo: tabStripContainer.trailingAnchor),
            tabBar.bottomAnchor.constraint(equalTo: tabStripContainer.bottomAnchor),

            tabBottomBorder.leadingAnchor.constraint(equalTo: tabStripContainer.leadingAnchor),
            tabBottomBorder.trailingAnchor.constraint(equalTo: tabStripContainer.trailingAnchor),
            tabBottomBorder.bottomAnchor.constraint(equalTo: tabStripContainer.bottomAnchor),
            tabBottomBorder.heightAnchor.constraint(equalToConstant: 0.5)
        ])

        // Sort files canonically: config.toml -> auto.toml -> worker.toml -> explorer.toml -> reviewer.toml
        let canonicalOrder = ["config.toml", "auto.toml", "worker.toml", "explorer.toml", "reviewer.toml"]
        let sortedFiles = plan.files.sorted { f1, f2 in
            let i1 = canonicalOrder.firstIndex(of: f1.name) ?? 99
            let i2 = canonicalOrder.firstIndex(of: f2.name) ?? 99
            return i1 < i2
        }

        for f in sortedFiles {
            let hasDot = f.action == .write || f.action == .rewrite
            let tabBtn = ProviderFileTabButton(
                name: f.name,
                isSelected: f.name == selectedTabName,
                hasChangeDot: hasDot
            ) { [weak self] chosenTab in
                guard let self = self else { return }
                self.selectedTabName = chosenTab
                self.renderAll()
            }
            tabBar.addArrangedSubview(tabBtn)
        }
        let tabSpacer = NSView()
        tabSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        tabBar.addArrangedSubview(tabSpacer)

        previewCard.stackView.addArrangedSubview(tabStripContainer)
        tabStripContainer.widthAnchor.constraint(equalTo: previewCard.stackView.widthAnchor).isActive = true

        // Preview sub-head
        let prevHead = NSStackView()
        prevHead.orientation = .horizontal
        prevHead.alignment = .centerY
        prevHead.distribution = .fill
        prevHead.edgeInsets = NSEdgeInsets(top: 7, left: 14, bottom: 7, right: 14)
        prevHead.translatesAutoresizingMaskIntoConstraints = false

        let headPath = NSTextField(labelWithString: "")
        headPath.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        headPath.textColor = Palette.primaryText
        headPath.isEditable = false
        headPath.isSelectable = true
        headPath.isBordered = false
        headPath.drawsBackground = false
        prevHead.addArrangedSubview(headPath)
        self.fileHeaderLabel = headPath

        let prevSpacer = NSView()
        prevSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        prevHead.addArrangedSubview(prevSpacer)

        let actBadge = NSTextField(labelWithString: "")
        actBadge.font = NSFont.systemFont(ofSize: 11, weight: .medium)
        actBadge.isEditable = false
        actBadge.isSelectable = false
        actBadge.isBordered = false
        actBadge.drawsBackground = false
        prevHead.addArrangedSubview(actBadge)
        self.fileActionBadge = actBadge

        previewCard.stackView.addArrangedSubview(prevHead)
        prevHead.widthAnchor.constraint(equalTo: previewCard.stackView.widthAnchor).isActive = true

        let previewSep2 = makeSeparator()
        previewCard.stackView.addArrangedSubview(previewSep2)
        previewSep2.widthAnchor.constraint(equalTo: previewCard.stackView.widthAnchor).isActive = true

        // Preview Text View
        let textScrollView = NSScrollView()
        textScrollView.translatesAutoresizingMaskIntoConstraints = false
        textScrollView.hasVerticalScroller = true
        textScrollView.hasHorizontalScroller = true
        textScrollView.autohidesScrollers = true
        textScrollView.drawsBackground = false

        let textView = NSTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.font = NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular)
        textView.textColor = Palette.primaryText
        textView.drawsBackground = false
        textView.textContainer?.containerSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        textView.textContainer?.widthTracksTextView = false
        textView.isHorizontallyResizable = true
        textView.isVerticallyResizable = true
        textView.textContainerInset = NSSize(width: 10, height: 8)
        textScrollView.documentView = textView
        self.codeTextView = textView

        previewCard.stackView.addArrangedSubview(textScrollView)
        NSLayoutConstraint.activate([
            textScrollView.widthAnchor.constraint(equalTo: previewCard.stackView.widthAnchor),
            textScrollView.heightAnchor.constraint(equalToConstant: 185)
        ])

        sec2.addArrangedSubview(previewCard)
        previewCard.widthAnchor.constraint(equalTo: sec2.widthAnchor).isActive = true

        contentContainer.addArrangedSubview(sec2)
        sec2.widthAnchor.constraint(equalTo: contentContainer.widthAnchor).isActive = true

        updatePreviewDisplay()
    }

    private func selectModel(_ model: String) {
        selectedModel = model
        updatePlan()
    }

    private func updatePreviewDisplay() {
        guard let currentFile = plan.files.first(where: { $0.name == selectedTabName }) ?? plan.files.first else { return }

        let cleanPath = currentFile.path.replacingOccurrences(of: NSHomeDirectory(), with: "~")
        fileHeaderLabel?.stringValue = cleanPath

        let isWrite = currentFile.action == .write || currentFile.action == .rewrite
        let dotPrefix = "● "
        fileActionBadge?.stringValue = dotPrefix + currentFile.action.localizedLabel
        fileActionBadge?.textColor = isWrite ? NSColor.systemGreen : Palette.secondaryText

        codeTextView?.string = currentFile.content
    }

    private func renderProgressBody() {
        let card = CardView()
        card.translatesAutoresizingMaskIntoConstraints = false

        let innerStack = NSStackView()
        innerStack.orientation = .vertical
        innerStack.alignment = .leading
        innerStack.spacing = 4
        innerStack.edgeInsets = NSEdgeInsets(top: 10, left: 14, bottom: 10, right: 14)
        innerStack.translatesAutoresizingMaskIntoConstraints = false
        card.stackView.addArrangedSubview(innerStack)
        innerStack.widthAnchor.constraint(equalTo: card.stackView.widthAnchor).isActive = true

        for (i, step) in steps.enumerated() {
            let row = ProviderStepRowView(step: step, index: i)
            innerStack.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: innerStack.widthAnchor).isActive = true
        }

        contentContainer.addArrangedSubview(card)
        card.widthAnchor.constraint(equalTo: contentContainer.widthAnchor).isActive = true

        if phase == .done {
            let codexHome = (state.codex.home ?? "~/.codex").replacingOccurrences(of: NSHomeDirectory(), with: "~")
            let notice = ProviderBannerView(
                tone: .warn,
                title: L10n.tr("preferences.provider.codex.install_sheet.restart_banner.title"),
                body: String(format: L10n.tr("preferences.provider.codex.install_sheet.restart_banner.body"), codexHome)
            )
            contentContainer.addArrangedSubview(notice)
            notice.widthAnchor.constraint(equalTo: contentContainer.widthAnchor).isActive = true
        }
    }

    private func renderFooter() {
        for v in footerContainer.subviews { v.removeFromSuperview() }

        let sep = NSView()
        sep.translatesAutoresizingMaskIntoConstraints = false
        sep.wantsLayer = true
        sep.layer?.backgroundColor = Palette.separator.cgColor
        footerContainer.addSubview(sep)

        let footerStack = NSStackView()
        footerStack.orientation = .horizontal
        footerStack.alignment = .centerY
        footerStack.distribution = .fill
        footerStack.translatesAutoresizingMaskIntoConstraints = false
        footerContainer.addSubview(footerStack)

        NSLayoutConstraint.activate([
            sep.topAnchor.constraint(equalTo: footerContainer.topAnchor),
            sep.leadingAnchor.constraint(equalTo: footerContainer.leadingAnchor),
            sep.trailingAnchor.constraint(equalTo: footerContainer.trailingAnchor),
            sep.heightAnchor.constraint(equalToConstant: 0.5),

            footerStack.topAnchor.constraint(equalTo: sep.bottomAnchor, constant: 12),
            footerStack.bottomAnchor.constraint(equalTo: footerContainer.bottomAnchor, constant: -2),
            footerStack.leadingAnchor.constraint(equalTo: footerContainer.leadingAnchor, constant: 20),
            footerStack.trailingAnchor.constraint(equalTo: footerContainer.trailingAnchor, constant: -20)
        ])

        let isCatUnavailable = preferencesWindow.providerCatalogsUnavailable

        switch phase {
        case .confirm:
            let spacer = NSView()
            spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
            footerStack.addArrangedSubview(spacer)

            let btnStack = NSStackView()
            btnStack.orientation = .horizontal
            btnStack.alignment = .centerY
            btnStack.spacing = 8

            let cancelBtn = NSButton(
                title: L10n.tr("preferences.provider.codex.install_sheet.cancel"),
                target: self,
                action: #selector(cancelAction)
            )
            cancelBtn.bezelStyle = .rounded
            cancelBtn.controlSize = .regular
            btnStack.addArrangedSubview(cancelBtn)

            let verbBtn = ProviderAccentButton()
            verbBtn.title = modeVerb()
            verbBtn.target = self
            verbBtn.action = #selector(startExecutionAction)
            verbBtn.isEnabled = !isCatUnavailable
            btnStack.addArrangedSubview(verbBtn)

            footerStack.addArrangedSubview(btnStack)

        case .running:
            let workingLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.install_sheet.working"))
            workingLabel.font = NSFont.systemFont(ofSize: 12, weight: .regular)
            workingLabel.textColor = Palette.secondaryText
            workingLabel.isEditable = false
            workingLabel.isSelectable = false
            workingLabel.isBordered = false
            workingLabel.drawsBackground = false
            footerStack.addArrangedSubview(workingLabel)

            let spacer = NSView()
            spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
            footerStack.addArrangedSubview(spacer)

        case .failed:
            let spacer = NSView()
            spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
            footerStack.addArrangedSubview(spacer)

            let btnStack = NSStackView()
            btnStack.orientation = .horizontal
            btnStack.alignment = .centerY
            btnStack.spacing = 8

            let logBtn = NSButton(
                title: L10n.tr("preferences.provider.codex.install_sheet.view_log"),
                target: self,
                action: #selector(viewLogAction)
            )
            logBtn.bezelStyle = .rounded
            logBtn.controlSize = .regular
            btnStack.addArrangedSubview(logBtn)

            let doneBtn = ProviderAccentButton()
            doneBtn.title = L10n.tr("preferences.provider.codex.install_sheet.done.btn")
            doneBtn.target = self
            doneBtn.action = #selector(cancelAction)
            btnStack.addArrangedSubview(doneBtn)

            footerStack.addArrangedSubview(btnStack)

        case .done:
            let spacer = NSView()
            spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
            footerStack.addArrangedSubview(spacer)

            let doneBtn = ProviderAccentButton()
            doneBtn.title = L10n.tr("preferences.provider.codex.install_sheet.done.btn")
            doneBtn.target = self
            doneBtn.action = #selector(doneAction)
            footerStack.addArrangedSubview(doneBtn)
        }
    }

    private func modeVerb() -> String {
        switch mode {
        case .install:
            return L10n.tr("preferences.provider.codex.install_sheet.verb.install")
        case .missing, .corrupt:
            return L10n.tr("preferences.provider.codex.install_sheet.verb.repair")
        case .reinstall:
            return L10n.tr("preferences.provider.codex.install_sheet.verb.reinstall")
        }
    }

    private func makeSeparator() -> NSView {
        let sep = NSView()
        sep.translatesAutoresizingMaskIntoConstraints = false
        sep.wantsLayer = true
        sep.layer?.backgroundColor = Palette.separator.cgColor
        sep.heightAnchor.constraint(equalToConstant: 0.5).isActive = true
        return sep
    }

    @objc private func refreshCatalogAction() {
        preferencesWindow.refreshProviderCatalogDetails()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            self?.buildModelOptions(configuredModel: self?.selectedModel ?? "")
            self?.updatePlan()
        }
    }

    @objc private func cancelAction() {
        dismiss()
    }

    @objc private func viewLogAction() {
        preferencesWindow.runProviderDiagnosticsAction(self)
    }

    @objc private func doneAction() {
        dismiss()
        onComplete()
    }

    @objc private func startExecutionAction() {
        phase = .running
        let roleWrites = plan.files.filter { !$0.isManaged && ($0.action == .write || $0.action == .rewrite) }.count

        steps = [
            ProviderCodexInstallStepItem(title: L10n.tr("preferences.provider.codex.install_sheet.step1"), status: .running),
            ProviderCodexInstallStepItem(title: L10n.tr("preferences.provider.codex.install_sheet.step2"), status: .idle),
            ProviderCodexInstallStepItem(title: String(format: L10n.tr("preferences.provider.codex.install_sheet.step3"), roleWrites), status: .idle),
            ProviderCodexInstallStepItem(title: L10n.tr("preferences.provider.codex.install_sheet.step4"), status: .idle),
            ProviderCodexInstallStepItem(title: L10n.tr("preferences.provider.codex.install_sheet.step5"), status: .idle),
        ]
        currentRunningStepIndex = 0

        renderAll()

        let requestedMode: String
        switch mode {
        case .install: requestedMode = "install"
        case .missing, .corrupt: requestedMode = "repair"
        case .reinstall: requestedMode = "reinstall"
        }
        preferencesWindow.runProviderCommand([
            "codex-integration-apply",
            "--model", selectedModel,
            "--mode", requestedMode,
        ]) { [weak self] result in
            guard let self = self else { return }

            switch result {
            case .success:
                for i in 0..<self.steps.count {
                    self.steps[i].status = .ok
                }
                self.phase = .done
                self.renderAll()
            case .failure(let error):
                let failIdx = min(self.currentRunningStepIndex, self.steps.count - 1)
                self.steps[failIdx].status = .error
                self.steps[failIdx].errorMessage = error.localizedDescription
                self.steps.append(ProviderCodexInstallStepItem(
                    title: L10n.tr("preferences.provider.codex.install_sheet.failed.rollback"),
                    status: .ok,
                    isRollback: true
                ))
                self.phase = .failed
                self.renderAll()
            }
        }
    }
}
