import AppKit
import Foundation

// First-run setup for provider routing: shows what detection found and the
// exact routes it would write, then writes them on confirm. The old flow ran
// `init`, which wrote a fixed Claude-only config regardless of what was
// installed — the user's first sight of the feature was a config that named
// one agent on a machine that had three.

// MARK: - Decoded plan

struct ProviderSetupPlan: Decodable {
    let configFile: String
    let workerExecutor: String
    let workerOptions: [String]
    let executors: [ProviderSetupExecutor]
    let providers: [ProviderSetupProvider]
    let routes: [ProviderSetupRoute]
    let blockers: [String]
    let notes: [String]
    // One ready-made plan per other agent the user could pick. Detection and
    // catalog reads take seconds and do not depend on that choice, so the
    // backend derives every variant from one probe and the picker needs no
    // round trip. Optional: an older backend simply offers none.
    let alternatives: [ProviderSetupAlternative]?

    enum CodingKeys: String, CodingKey {
        case configFile = "config_file"
        case workerExecutor = "worker_executor"
        case workerOptions = "worker_options"
        case executors, providers, routes, blockers, notes, alternatives
    }
}

struct ProviderSetupAlternative: Decodable {
    let workerExecutor: String
    let providers: [ProviderSetupProvider]
    let routes: [ProviderSetupRoute]
    let notes: [String]

    enum CodingKeys: String, CodingKey {
        case workerExecutor = "worker_executor"
        case providers, routes, notes
    }
}

struct ProviderSetupExecutor: Decodable {
    let executor: String
    let installed: Bool
    let path: String?
    let catalogIds: [String]

    enum CodingKeys: String, CodingKey {
        case executor, installed, path
        case catalogIds = "catalog_ids"
    }
}

struct ProviderSetupProvider: Decodable {
    let id: String
    let executor: String
    let models: [String]
}

struct ProviderSetupRoute: Decodable {
    let role: String
    let primary: String
    let fallback: [String]
    /// Reasoning effort for the primary, where the executor takes one as a
    /// runtime option. Antigravity carries it inside the model id instead, so
    /// its routes report none and the id already reads "· High".
    let effort: String?
    let reasonCode: String
    let reason: String
    let warning: String

    enum CodingKeys: String, CodingKey {
        case role, primary, fallback, effort, reason, warning
        case reasonCode = "reason_code"
    }
}

// MARK: - Sheet

class ProviderSetupSheetController: NSObject, NSWindowDelegate {
    private let parentWindow: NSWindow
    private let preferencesWindow: PreferencesWindow
    private let onDismiss: () -> Void
    private let onApplied: () -> Void

    /// The plan as first fetched. Alternatives hang off it, so switching back
    /// to the original agent is a lookup rather than another probe.
    private let basePlan: ProviderSetupPlan
    private var plan: ProviderSetupPlan
    private var workerExecutor: String
    private var sheetWindow: NSWindow?
    private var contentStack: FlippedStackView!
    private var applyButton: ProviderAccentButton?
    private var isBusy = false
    private var errorMessage = ""

    init(
        plan: ProviderSetupPlan,
        parentWindow: NSWindow,
        preferencesWindow: PreferencesWindow,
        onDismiss: @escaping () -> Void,
        onApplied: @escaping () -> Void
    ) {
        self.basePlan = plan
        self.plan = plan
        self.workerExecutor = plan.workerExecutor
        self.parentWindow = parentWindow
        self.preferencesWindow = preferencesWindow
        self.onDismiss = onDismiss
        self.onApplied = onApplied
        super.init()
    }

    func show() {
        let windowRect = NSRect(x: 0, y: 0, width: 620, height: 640)
        let win = NSWindow(
            contentRect: windowRect,
            styleMask: [.titled],
            backing: .buffered,
            defer: false)
        win.delegate = self
        win.isReleasedWhenClosed = false
        win.title = ""
        win.titlebarAppearsTransparent = true
        win.titleVisibility = .hidden
        win.backgroundColor = Palette.windowBackground
        sheetWindow = win

        let rootView = NSView(frame: windowRect)
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

            scrollView.topAnchor.constraint(equalTo: headerView.bottomAnchor, constant: 14),
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
        contentStack.spacing = 18
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        scrollView.documentView = contentStack

        NSLayoutConstraint.activate([
            contentStack.topAnchor.constraint(equalTo: clipView.topAnchor),
            contentStack.leadingAnchor.constraint(equalTo: clipView.leadingAnchor),
            contentStack.trailingAnchor.constraint(equalTo: clipView.trailingAnchor),
            contentStack.widthAnchor.constraint(equalTo: scrollView.widthAnchor, constant: -14)
        ])

        renderContent()
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

        let titleLabel = NSTextField(labelWithString: L10n.tr("preferences.provider.setup.title"))
        titleLabel.font = NSFont.systemFont(ofSize: 16.5, weight: .bold)
        titleLabel.textColor = Palette.primaryText
        header.addArrangedSubview(titleLabel)

        let descLabel = NSTextField(wrappingLabelWithString:
            L10n.tr("preferences.provider.setup.desc", plan.configFile))
        descLabel.font = NSFont.systemFont(ofSize: 11.5)
        descLabel.textColor = Palette.secondaryText
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
            action: #selector(cancelAction(_:)))
        cancelBtn.bezelStyle = .rounded
        cancelBtn.keyEquivalent = "\u{1b}"
        buttonStack.addArrangedSubview(cancelBtn)

        let applyBtn = ProviderAccentButton()
        applyBtn.title = L10n.tr("preferences.provider.setup.apply")
        applyBtn.target = self
        applyBtn.action = #selector(applyAction(_:))
        // Nothing installed means nothing this config could route to; writing
        // it would only produce failures at request time.
        applyBtn.isEnabled = plan.blockers.isEmpty
        buttonStack.addArrangedSubview(applyBtn)
        applyButton = applyBtn
        return footer
    }

    // MARK: - Content

    /// Rows are the app's standard `CardRow` inside `CardView` sections, the
    /// same vocabulary the Preferences page and the Codex install sheet use.
    /// Hand-rolled stacks here read as a different app one sheet away.
    private func renderContent() {
        for view in contentStack.arrangedSubviews {
            contentStack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }

        if !errorMessage.isEmpty {
            addMessageCard(errorMessage, tone: .error)
        }
        for blocker in plan.blockers {
            addMessageCard(blocker, tone: .error)
        }
        _ = preferencesWindow.addSection(
            title: L10n.tr("preferences.provider.setup.section.detected"),
            rows: plan.executors.enumerated().map(buildExecutorRow),
            into: contentStack)

        if plan.workerOptions.count > 1 {
            _ = preferencesWindow.addSection(
                title: L10n.tr("preferences.provider.setup.section.worker"),
                rows: [buildWorkerPickerRow()],
                into: contentStack)
        }
        if !plan.routes.isEmpty {
            _ = preferencesWindow.addSection(
                title: L10n.tr("preferences.provider.setup.section.routes"),
                rows: plan.routes.enumerated().map(buildRouteRow),
                into: contentStack)
        }
        for note in plan.notes {
            addMessageCard(note, tone: .idle)
        }
        applyButton?.isEnabled = plan.blockers.isEmpty && !isBusy
    }

    private func buildExecutorRow(
        _ index: Int,
        _ executor: ProviderSetupExecutor
    ) -> NSView {
        let control: NSView
        if !executor.installed {
            control = ProviderPillView(
                tone: .idle, text: L10n.tr("preferences.provider.setup.not_found"))
        } else {
            control = ProviderPillView(
                tone: .ok,
                text: L10n.tr(
                    "preferences.provider.setup.model_count", executor.catalogIds.count))
        }
        return CardRow(
            title: preferencesWindow.formatProviderName(executor.executor),
            desc: executor.installed ? executor.path : nil,
            control: control,
            isFirst: index == 0)
    }

    private func buildWorkerPickerRow() -> NSView {
        let popup = NSPopUpButton(frame: .zero, pullsDown: false)
        for option in plan.workerOptions {
            popup.addItem(withTitle: preferencesWindow.formatProviderName(option))
            popup.lastItem?.representedObject = option
        }
        if let index = plan.workerOptions.firstIndex(of: workerExecutor) {
            popup.selectItem(at: index)
        }
        popup.target = self
        popup.action = #selector(workerChanged(_:))
        popup.isEnabled = !isBusy
        return CardRow(
            title: L10n.tr("preferences.provider.setup.worker.title"),
            desc: L10n.tr("preferences.provider.setup.worker.desc"),
            control: popup,
            isFirst: true)
    }

    private func buildRouteRow(_ index: Int, _ route: ProviderSetupRoute) -> NSView {
        let control = NSStackView()
        control.orientation = .horizontal
        control.alignment = .centerY
        control.spacing = 6
        control.translatesAutoresizingMaskIntoConstraints = false

        if !route.warning.isEmpty {
            control.addArrangedSubview(ProviderPillView(
                tone: .warn, text: L10n.tr("preferences.provider.setup.will_be_refused")))
        }
        // One effort, one source, and the same badge the model picker and the
        // role list use. Antigravity spells it into the model id and the others
        // store it beside the route, but that is a difference in delivery, not
        // in what the user is choosing, so the name is rendered without its
        // suffix and the plan's effort becomes the badge for all three.
        let modelLabel = NSTextField(
            labelWithString: preferencesWindow.formatCandidateModelNameWithoutEffort(route.primary))
        modelLabel.font = NSFont.systemFont(ofSize: 12.5)
        modelLabel.textColor = Palette.primaryText
        modelLabel.isEditable = false
        modelLabel.isSelectable = false
        control.addArrangedSubview(modelLabel)

        if let effort = route.effort, !effort.isEmpty {
            control.addArrangedSubview(
                ProviderTagView(text: effort.capitalized, isAccent: true))
        }
        if let executor = executorFor(candidate: route.primary) {
            control.addArrangedSubview(
                ProviderTagView(text: preferencesWindow.formatProviderName(executor)))
        }

        // Reason and fallback are different kinds of fact — why this agent, and
        // what happens if it is unavailable — so they get their own lines.
        var lines = [localizedReason(route)]
        if !route.fallback.isEmpty {
            lines.append(L10n.tr(
                "preferences.provider.setup.fallback",
                route.fallback
                    .map { preferencesWindow.formatCandidateModelName($0) }
                    .joined(separator: ", ")))
        }
        if !route.warning.isEmpty {
            lines.append(route.warning)
        }
        return CardRow(
            title: preferencesWindow.formatRoleDisplayName(route.role),
            desc: lines.joined(separator: "\n"),
            control: control,
            isFirst: index == 0)
    }

    /// Adds the card *before* constraining its width to the stack. A constraint
    /// between two views is only legal once they share an ancestor, so building
    /// the card and activating that constraint cannot happen in one step.
    private func addMessageCard(_ text: String, tone: ProviderPillTone) {
        let card = AccentBannerCardView()
        card.accentColor = tone.textColor
        card.translatesAutoresizingMaskIntoConstraints = false
        card.stackView.edgeInsets = NSEdgeInsets(top: 11, left: 16, bottom: 11, right: 16)

        let label = NSTextField(wrappingLabelWithString: text)
        label.font = NSFont.systemFont(ofSize: 11.5)
        label.textColor = tone == .error ? tone.textColor : Palette.secondaryText
        label.translatesAutoresizingMaskIntoConstraints = false
        card.stackView.addArrangedSubview(label)

        contentStack.addArrangedSubview(card)
        NSLayoutConstraint.activate([
            card.widthAnchor.constraint(equalTo: contentStack.widthAnchor),
            // Against the card, not its inset stack: the edge insets already
            // provide the padding, so subtracting them twice would leave the
            // text wrapping in half the available width.
            label.widthAnchor.constraint(equalTo: card.widthAnchor, constant: -32)
        ])
    }

    /// The backend ships English prose alongside a code so a UI that has no
    /// string for a newly added reason still shows something true.
    private func localizedReason(_ route: ProviderSetupRoute) -> String {
        let key = "preferences.provider.setup.reason.\(route.reasonCode)"
        let localized = L10n.tr(key)
        return localized == key ? route.reason : localized
    }

    /// Rebuild the displayed plan for another agent out of what was already
    /// fetched. Nil when this backend sent no alternatives, in which case the
    /// caller falls back to asking for the plan again.
    private func planSwitchingWorker(to executor: String) -> ProviderSetupPlan? {
        if executor == basePlan.workerExecutor { return basePlan }
        guard let alternative = (basePlan.alternatives ?? [])
            .first(where: { $0.workerExecutor == executor })
        else { return nil }
        return ProviderSetupPlan(
            configFile: basePlan.configFile,
            workerExecutor: alternative.workerExecutor,
            workerOptions: basePlan.workerOptions,
            executors: basePlan.executors,
            providers: alternative.providers,
            routes: alternative.routes,
            blockers: basePlan.blockers,
            notes: alternative.notes,
            alternatives: basePlan.alternatives)
    }

    private func executorFor(candidate: String) -> String? {
        let providerId = String(candidate.split(separator: ":", maxSplits: 1).first ?? "")
        return plan.providers.first(where: { $0.id == providerId })?.executor
    }

    // MARK: - Actions

    @objc private func workerChanged(_ sender: NSPopUpButton) {
        // Indexed against the plan's own list rather than read back out of the
        // menu item: a `representedObject` that fails to come back leaves the
        // popup showing the new choice while nothing else happens, which looks
        // exactly like the picker being ignored.
        let index = sender.indexOfSelectedItem
        guard index >= 0, index < plan.workerOptions.count else { return }
        let selected = plan.workerOptions[index]
        guard selected != workerExecutor else { return }

        workerExecutor = selected
        errorMessage = ""

        // Normally instant: the backend sent a plan for every option.
        if let derived = planSwitchingWorker(to: selected) {
            plan = derived
            renderContent()
            return
        }

        isBusy = true
        renderContent()
        preferencesWindow.fetchProviderSetupPlan(workerExecutor: selected) { [weak self] result in
            guard let self = self else { return }
            self.isBusy = false
            switch result {
            case .success(let plan):
                self.plan = plan
            case .failure(let error):
                // Not an NSAlert: this sheet already owns the window's sheet
                // slot, so a second one cannot appear until this one closes.
                self.errorMessage = error.localizedDescription
            }
            self.renderContent()
        }
    }

    @objc private func cancelAction(_ sender: Any) {
        guard let win = sheetWindow else { return }
        parentWindow.endSheet(win, returnCode: .cancel)
        win.orderOut(nil)
    }

    @objc private func applyAction(_ sender: Any) {
        guard let win = sheetWindow, !isBusy else { return }
        isBusy = true
        applyButton?.isEnabled = false
        preferencesWindow.applyProviderSetupPlan(workerExecutor: workerExecutor) {
            [weak self] result in
            guard let self = self else { return }
            self.isBusy = false
            switch result {
            case .success:
                self.parentWindow.endSheet(win, returnCode: .OK)
                win.orderOut(nil)
                self.onApplied()
            case .failure(let error):
                self.applyButton?.isEnabled = true
                self.errorMessage = error.localizedDescription
                self.renderContent()
            }
        }
    }
}
