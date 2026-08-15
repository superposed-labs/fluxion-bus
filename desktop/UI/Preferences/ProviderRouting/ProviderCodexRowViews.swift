import AppKit
import Foundation

// Row and control views used by both Codex sheets (read-only config preview and
// install/repair): banner, path row, role row, model option, step row and the
// file tab button.

// MARK: - Banner View

final class ProviderBannerView: NSView {
    enum Tone {
        case ok
        case warn
        case error
        case neutral
    }

    private let tone: Tone
    private let titleText: String
    private let bodyText: String
    private let titleLabel = NSTextField(labelWithString: "")
    private let bodyLabel = NSTextField(wrappingLabelWithString: "")

    init(tone: Tone, title: String, body: String) {
        self.tone = tone
        self.titleText = title
        self.bodyText = body
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.cornerRadius = 9
        layer?.borderWidth = 0.5

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 3
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: topAnchor, constant: 11),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -11),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 14),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -14)
        ])

        titleLabel.stringValue = title
        titleLabel.font = NSFont.systemFont(ofSize: 12.5, weight: .semibold)
        titleLabel.textColor = Palette.primaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        stack.addArrangedSubview(titleLabel)

        bodyLabel.stringValue = body
        bodyLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        bodyLabel.textColor = Palette.secondaryText
        bodyLabel.cell?.wraps = true
        bodyLabel.cell?.isScrollable = false
        bodyLabel.maximumNumberOfLines = 0
        bodyLabel.lineBreakMode = .byWordWrapping
        bodyLabel.isEditable = false
        bodyLabel.isSelectable = false
        bodyLabel.isBordered = false
        bodyLabel.drawsBackground = false
        bodyLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        bodyLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
        stack.addArrangedSubview(bodyLabel)
        bodyLabel.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true

        updateColors()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func updateColors() {
        switch tone {
        case .warn:
            layer?.backgroundColor = NSColor.dynamicColor(
                light: NSColor(hex: "#FFF7EB"),
                dark: NSColor(hex: "#3A2A14").withAlphaComponent(0.6)
            ).cgColor
            layer?.borderColor = NSColor.dynamicColor(
                light: NSColor(hex: "#F59E0B").withAlphaComponent(0.45),
                dark: NSColor(hex: "#D97706").withAlphaComponent(0.5)
            ).cgColor
        case .error:
            layer?.backgroundColor = NSColor.dynamicColor(
                light: NSColor(hex: "#FEF2F2"),
                dark: NSColor(hex: "#3E1E1E").withAlphaComponent(0.6)
            ).cgColor
            layer?.borderColor = NSColor.dynamicColor(
                light: NSColor(hex: "#EF4444").withAlphaComponent(0.4),
                dark: NSColor(hex: "#DC2626").withAlphaComponent(0.5)
            ).cgColor
        case .ok:
            layer?.backgroundColor = NSColor.dynamicColor(
                light: NSColor(hex: "#F0FDF4"),
                dark: NSColor(hex: "#14381C").withAlphaComponent(0.6)
            ).cgColor
            layer?.borderColor = NSColor.dynamicColor(
                light: NSColor(hex: "#22C55E").withAlphaComponent(0.4),
                dark: NSColor(hex: "#16A34A").withAlphaComponent(0.5)
            ).cgColor
        case .neutral:
            layer?.backgroundColor = Palette.cardBackground.cgColor
            layer?.borderColor = Palette.cardBorder.cgColor
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateColors()
    }
}

// MARK: - Path Row View

final class ProviderPathRowView: NSView {
    private let labelText: String
    private let pathText: String
    private let copyLabel: String
    private let hasFinder: Bool
    private let statusView: NSView?
    private let copyButton = NSButton()
    private var copyTimer: Timer?

    init(label: String, path: String, copyLabel: String = L10n.tr("preferences.provider.codex.config_sheet.copy_path"), hasFinder: Bool = false, statusView: NSView? = nil) {
        self.labelText = label
        self.pathText = path
        self.copyLabel = copyLabel
        self.hasFinder = hasFinder
        self.statusView = statusView
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false

        let rowStack = NSStackView()
        rowStack.orientation = .horizontal
        rowStack.alignment = .centerY
        rowStack.spacing = 10
        rowStack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(rowStack)

        NSLayoutConstraint.activate([
            rowStack.topAnchor.constraint(equalTo: topAnchor, constant: 7),
            rowStack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -7),
            rowStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            rowStack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -10),
            heightAnchor.constraint(greaterThanOrEqualToConstant: 34)
        ])

        let lLabel = NSTextField(labelWithString: label)
        lLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        lLabel.textColor = Palette.primaryText
        lLabel.isEditable = false
        lLabel.isSelectable = false
        lLabel.isBordered = false
        lLabel.drawsBackground = false
        lLabel.translatesAutoresizingMaskIntoConstraints = false
        lLabel.widthAnchor.constraint(equalToConstant: 106).isActive = true
        rowStack.addArrangedSubview(lLabel)

        let pLabel = NSTextField(labelWithString: path)
        pLabel.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        pLabel.textColor = Palette.secondaryText
        pLabel.isEditable = false
        pLabel.isSelectable = true
        pLabel.isBordered = false
        pLabel.drawsBackground = false
        pLabel.lineBreakMode = .byTruncatingMiddle
        pLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
        pLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        rowStack.addArrangedSubview(pLabel)

        if let status = statusView {
            rowStack.addArrangedSubview(status)
        }

        let opsStack = NSStackView()
        opsStack.orientation = .horizontal
        opsStack.spacing = 6
        opsStack.alignment = .centerY

        copyButton.title = copyLabel
        copyButton.bezelStyle = .rounded
        copyButton.controlSize = .small
        copyButton.target = self
        copyButton.action = #selector(copyAction)
        opsStack.addArrangedSubview(copyButton)

        if hasFinder {
            let finderBtn = NSButton(
                title: L10n.tr("preferences.provider.codex.config_sheet.show_in_finder"),
                target: self,
                action: #selector(showInFinderAction)
            )
            finderBtn.bezelStyle = .rounded
            finderBtn.controlSize = .small
            opsStack.addArrangedSubview(finderBtn)
        }

        rowStack.addArrangedSubview(opsStack)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    @objc private func copyAction() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(pathText, forType: .string)
        copyTimer?.invalidate()
        copyButton.title = L10n.tr("preferences.provider.codex.config_sheet.copied")
        copyTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: false) { [weak self] _ in
            guard let self = self else { return }
            self.copyButton.title = self.copyLabel
        }
    }

    @objc private func showInFinderAction() {
        let expanded = (pathText as NSString).expandingTildeInPath
        let url = URL(fileURLWithPath: expanded)
        if FileManager.default.fileExists(atPath: expanded) {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        } else {
            let parent = url.deletingLastPathComponent()
            if FileManager.default.fileExists(atPath: parent.path) {
                NSWorkspace.shared.open(parent)
            } else {
                NSWorkspace.shared.open(URL(fileURLWithPath: NSHomeDirectory()))
            }
        }
    }
}

// MARK: - Role Row View

final class ProviderRoleRowView: NSView {
    private let item: ProviderCodexRoleConfigItem

    init(item: ProviderCodexRoleConfigItem) {
        self.item = item
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false

        let rootStack = NSStackView()
        rootStack.orientation = .vertical
        rootStack.alignment = .leading
        rootStack.spacing = 3
        rootStack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(rootStack)

        NSLayoutConstraint.activate([
            rootStack.topAnchor.constraint(equalTo: topAnchor, constant: 7),
            rootStack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -7),
            rootStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            rootStack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12)
        ])

        let headStack = NSStackView()
        headStack.orientation = .horizontal
        headStack.alignment = .centerY
        headStack.distribution = .fill
        headStack.translatesAutoresizingMaskIntoConstraints = false

        let nameLabel = NSTextField(labelWithString: item.role)
        nameLabel.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .medium)
        nameLabel.textColor = Palette.primaryText
        nameLabel.isEditable = false
        nameLabel.isSelectable = false
        nameLabel.isBordered = false
        nameLabel.drawsBackground = false
        headStack.addArrangedSubview(nameLabel)

        let statusPill = makeStatusView(status: item.status)
        headStack.addArrangedSubview(statusPill)

        rootStack.addArrangedSubview(headStack)
        headStack.widthAnchor.constraint(equalTo: rootStack.widthAnchor).isActive = true

        let roleFileRow = NSStackView()
        roleFileRow.orientation = .horizontal
        roleFileRow.alignment = .firstBaseline
        roleFileRow.spacing = 8
        roleFileRow.translatesAutoresizingMaskIntoConstraints = false

        let rfKey = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.config_sheet.role.role_file"))
        rfKey.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        rfKey.textColor = Palette.secondaryText
        rfKey.isEditable = false
        rfKey.isSelectable = false
        rfKey.isBordered = false
        rfKey.drawsBackground = false
        rfKey.widthAnchor.constraint(equalToConstant: 78).isActive = true
        roleFileRow.addArrangedSubview(rfKey)

        let rfValText: String
        switch item.status {
        case .installed:
            rfValText = "model = \(item.codexModel)  ·  model_provider = \(item.role)  ·  \(item.file)"
        case .notInstalled, .unreadable:
            rfValText = "\(L10n.tr("preferences.provider.codex.config_sheet.role.not_readable"))  ·  \(item.file)"
        }
        let rfVal = NSTextField(labelWithString: rfValText)
        rfVal.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        rfVal.textColor = item.status == .installed ? Palette.primaryText : Palette.secondaryText
        rfVal.isEditable = false
        rfVal.isSelectable = true
        rfVal.isBordered = false
        rfVal.drawsBackground = false
        roleFileRow.addArrangedSubview(rfVal)

        rootStack.addArrangedSubview(roleFileRow)
        roleFileRow.widthAnchor.constraint(equalTo: rootStack.widthAnchor).isActive = true

        let routeRow = NSStackView()
        routeRow.orientation = .horizontal
        routeRow.alignment = .firstBaseline
        routeRow.spacing = 8
        routeRow.translatesAutoresizingMaskIntoConstraints = false

        let rKey = NSTextField(labelWithString: L10n.tr("preferences.provider.codex.config_sheet.role.fluxion_route"))
        rKey.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        rKey.textColor = Palette.secondaryText
        rKey.isEditable = false
        rKey.isSelectable = false
        rKey.isBordered = false
        rKey.drawsBackground = false
        rKey.widthAnchor.constraint(equalToConstant: 78).isActive = true
        routeRow.addArrangedSubview(rKey)

        let rValText = "\(item.model)  ·  \(item.executor)  ·  \(item.route)"
        let rVal = NSTextField(labelWithString: rValText)
        rVal.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        rVal.textColor = Palette.primaryText
        rVal.isEditable = false
        rVal.isSelectable = true
        rVal.isBordered = false
        rVal.drawsBackground = false
        routeRow.addArrangedSubview(rVal)

        rootStack.addArrangedSubview(routeRow)
        routeRow.widthAnchor.constraint(equalTo: rootStack.widthAnchor).isActive = true

        if let why = item.why, !why.isEmpty {
            let whyBox = NSTextField(wrappingLabelWithString: why)
            whyBox.font = NSFont.systemFont(ofSize: 11, weight: .regular)
            whyBox.textColor = Palette.secondaryText
            whyBox.cell?.wraps = true
            whyBox.cell?.isScrollable = false
            whyBox.maximumNumberOfLines = 0
            whyBox.lineBreakMode = .byWordWrapping
            whyBox.isEditable = false
            whyBox.isSelectable = false
            whyBox.isBordered = false
            whyBox.drawsBackground = false
            rootStack.addArrangedSubview(whyBox)
            whyBox.widthAnchor.constraint(equalTo: rootStack.widthAnchor).isActive = true
        }
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func makeStatusView(status: ProviderCodexRoleStatus) -> NSView {
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.spacing = 5
        stack.alignment = .centerY

        let dot = NSView()
        dot.translatesAutoresizingMaskIntoConstraints = false
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 3
        NSLayoutConstraint.activate([
            dot.widthAnchor.constraint(equalToConstant: 6),
            dot.heightAnchor.constraint(equalToConstant: 6)
        ])

        let label = NSTextField(labelWithString: "")
        label.font = NSFont.systemFont(ofSize: 11, weight: .medium)
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false

        switch status {
        case .installed:
            dot.layer?.backgroundColor = NSColor.systemGreen.cgColor
            label.stringValue = L10n.tr("preferences.provider.codex.check.installed")
            label.textColor = .systemGreen
        case .notInstalled:
            dot.layer?.backgroundColor = Palette.secondaryText.cgColor
            label.stringValue = L10n.tr("preferences.provider.codex.check.not_installed")
            label.textColor = Palette.secondaryText
        case .unreadable:
            dot.layer?.backgroundColor = NSColor.systemRed.cgColor
            label.stringValue = L10n.tr("preferences.provider.codex.config_sheet.status.unreadable")
            label.textColor = .systemRed
        }

        stack.addArrangedSubview(dot)
        stack.addArrangedSubview(label)
        return stack
    }
}

// MARK: - Model Option Row View

final class ProviderModelOptionRowView: NSControl {
    let option: ProviderCodexRoleModelOption
    var isModelSelected: Bool {
        didSet { updateAppearance() }
    }
    private let onSelect: (ProviderCodexRoleModelOption) -> Void
    private let tickView = NSView()
    private let innerTick = NSView()

    init(option: ProviderCodexRoleModelOption, isSelected: Bool, onSelect: @escaping (ProviderCodexRoleModelOption) -> Void) {
        self.option = option
        self.isModelSelected = isSelected
        self.onSelect = onSelect
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true

        let rootStack = NSStackView()
        rootStack.orientation = .horizontal
        rootStack.alignment = .top
        rootStack.spacing = 9
        rootStack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(rootStack)

        NSLayoutConstraint.activate([
            rootStack.topAnchor.constraint(equalTo: topAnchor, constant: 8),
            rootStack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -8),
            rootStack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            rootStack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12)
        ])

        tickView.translatesAutoresizingMaskIntoConstraints = false
        tickView.wantsLayer = true
        tickView.layer?.cornerRadius = 6.5
        tickView.layer?.borderWidth = 1
        NSLayoutConstraint.activate([
            tickView.widthAnchor.constraint(equalToConstant: 13),
            tickView.heightAnchor.constraint(equalToConstant: 13)
        ])

        innerTick.translatesAutoresizingMaskIntoConstraints = false
        innerTick.wantsLayer = true
        innerTick.layer?.cornerRadius = 3
        innerTick.layer?.backgroundColor = NSColor.controlAccentColor.cgColor
        tickView.addSubview(innerTick)
        NSLayoutConstraint.activate([
            innerTick.centerXAnchor.constraint(equalTo: tickView.centerXAnchor),
            innerTick.centerYAnchor.constraint(equalTo: tickView.centerYAnchor),
            innerTick.widthAnchor.constraint(equalToConstant: 6),
            innerTick.heightAnchor.constraint(equalToConstant: 6)
        ])
        rootStack.addArrangedSubview(tickView)

        let mainStack = NSStackView()
        mainStack.orientation = .vertical
        mainStack.alignment = .leading
        mainStack.spacing = 2
        mainStack.translatesAutoresizingMaskIntoConstraints = false

        let titleRow = NSStackView()
        titleRow.orientation = .horizontal
        titleRow.alignment = .centerY
        titleRow.spacing = 6

        let nameLabel = NSTextField(labelWithString: option.name)
        nameLabel.font = NSFont.systemFont(ofSize: 12.5, weight: .medium)
        nameLabel.textColor = option.isStale ? Palette.secondaryText : Palette.primaryText
        nameLabel.isEditable = false
        nameLabel.isSelectable = false
        nameLabel.isBordered = false
        nameLabel.drawsBackground = false
        titleRow.addArrangedSubview(nameLabel)

        if option.isRecommended {
            let recTag = ProviderTagView(
                text: L10n.tr("preferences.provider.codex.install_sheet.model.recommended"),
                isAccent: true,
                isBold: false,
                isUppercase: false
            )
            titleRow.addArrangedSubview(recTag)
        }

        if option.isStale {
            let staleTag = ProviderTagView(
                text: L10n.tr("preferences.provider.codex.install_sheet.stale.tag"),
                isAccent: false,
                isBold: false,
                isUppercase: false
            )
            titleRow.addArrangedSubview(staleTag)
        }

        mainStack.addArrangedSubview(titleRow)

        if !option.why.isEmpty {
            let whyLabel = NSTextField(wrappingLabelWithString: option.why)
            whyLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
            whyLabel.textColor = Palette.secondaryText
            whyLabel.cell?.wraps = true
            whyLabel.cell?.isScrollable = false
            whyLabel.maximumNumberOfLines = 0
            whyLabel.lineBreakMode = .byWordWrapping
            whyLabel.isEditable = false
            whyLabel.isSelectable = false
            whyLabel.isBordered = false
            whyLabel.drawsBackground = false
            whyLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            whyLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
            mainStack.addArrangedSubview(whyLabel)
            whyLabel.widthAnchor.constraint(equalTo: mainStack.widthAnchor).isActive = true
        }

        rootStack.addArrangedSubview(mainStack)
        mainStack.setContentHuggingPriority(.defaultLow, for: .horizontal)

        if option.showsTechnicalId {
            let spacer = NSView()
            spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
            rootStack.addArrangedSubview(spacer)

            let idBadge = NSTextField(labelWithString: option.id)
            idBadge.font = NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular)
            idBadge.textColor = Palette.secondaryText
            idBadge.isEditable = false
            idBadge.isSelectable = true
            idBadge.isBordered = false
            idBadge.drawsBackground = false
            idBadge.setContentHuggingPriority(.required, for: .horizontal)
            idBadge.setContentCompressionResistancePriority(.required, for: .horizontal)
            rootStack.addArrangedSubview(idBadge)
        }

        updateAppearance()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func mouseDown(with event: NSEvent) {
        guard !option.isStale else { return }
        onSelect(option)
    }

    private func updateAppearance() {
        tickView.layer?.borderColor = isModelSelected
            ? NSColor.controlAccentColor.cgColor
            : Palette.cardBorder.cgColor
        innerTick.isHidden = !isModelSelected
        alphaValue = option.isStale ? 0.65 : 1.0
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateAppearance()
    }
}

// MARK: - Step Row View

final class ProviderStepRowView: NSView {
    private let step: ProviderCodexInstallStepItem
    private let index: Int
    private var spinner: NSProgressIndicator?

    init(step: ProviderCodexInstallStepItem, index: Int) {
        self.step = step
        self.index = index
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false

        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 2
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: topAnchor, constant: 6),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -6),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 4),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -4)
        ])

        let topRow = NSStackView()
        topRow.orientation = .horizontal
        topRow.alignment = .centerY
        topRow.spacing = 8

        let markContainer = NSView()
        markContainer.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            markContainer.widthAnchor.constraint(equalToConstant: 18),
            markContainer.heightAnchor.constraint(equalToConstant: 18)
        ])

        switch step.status {
        case .ok:
            let mark = NSTextField(labelWithString: "✓")
            mark.font = NSFont.systemFont(ofSize: 12, weight: .bold)
            mark.textColor = .systemGreen
            mark.alignment = .center
            mark.isEditable = false
            mark.isSelectable = false
            mark.isBordered = false
            mark.drawsBackground = false
            mark.translatesAutoresizingMaskIntoConstraints = false
            markContainer.addSubview(mark)
            NSLayoutConstraint.activate([
                mark.centerXAnchor.constraint(equalTo: markContainer.centerXAnchor),
                mark.centerYAnchor.constraint(equalTo: markContainer.centerYAnchor)
            ])
        case .running:
            let pi = NSProgressIndicator()
            pi.style = .spinning
            pi.controlSize = .small
            pi.translatesAutoresizingMaskIntoConstraints = false
            markContainer.addSubview(pi)
            NSLayoutConstraint.activate([
                pi.centerXAnchor.constraint(equalTo: markContainer.centerXAnchor),
                pi.centerYAnchor.constraint(equalTo: markContainer.centerYAnchor)
            ])
            pi.startAnimation(nil)
            self.spinner = pi
        case .error:
            let mark = NSTextField(labelWithString: "✕")
            mark.font = NSFont.systemFont(ofSize: 12, weight: .bold)
            mark.textColor = .systemRed
            mark.alignment = .center
            mark.isEditable = false
            mark.isSelectable = false
            mark.isBordered = false
            mark.drawsBackground = false
            mark.translatesAutoresizingMaskIntoConstraints = false
            markContainer.addSubview(mark)
            NSLayoutConstraint.activate([
                mark.centerXAnchor.constraint(equalTo: markContainer.centerXAnchor),
                mark.centerYAnchor.constraint(equalTo: markContainer.centerYAnchor)
            ])
        case .idle:
            if step.isRollback {
                let mark = NSTextField(labelWithString: "↺")
                mark.font = NSFont.systemFont(ofSize: 12, weight: .regular)
                mark.textColor = Palette.secondaryText
                mark.alignment = .center
                mark.isEditable = false
                mark.isSelectable = false
                mark.isBordered = false
                mark.drawsBackground = false
                mark.translatesAutoresizingMaskIntoConstraints = false
                markContainer.addSubview(mark)
                NSLayoutConstraint.activate([
                    mark.centerXAnchor.constraint(equalTo: markContainer.centerXAnchor),
                    mark.centerYAnchor.constraint(equalTo: markContainer.centerYAnchor)
                ])
            }
        }
        topRow.addArrangedSubview(markContainer)

        let titleLabel = NSTextField(labelWithString: step.title)
        titleLabel.font = NSFont.systemFont(ofSize: 12.5, weight: step.status == .running ? .medium : .regular)
        titleLabel.textColor = step.status == .error ? .systemRed : (step.status == .idle && !step.isRollback ? Palette.secondaryText : Palette.primaryText)
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        topRow.addArrangedSubview(titleLabel)

        stack.addArrangedSubview(topRow)

        if let err = step.errorMessage, !err.isEmpty {
            let errLabel = NSTextField(wrappingLabelWithString: err)
            errLabel.font = NSFont.systemFont(ofSize: 11, weight: .regular)
            errLabel.textColor = Palette.secondaryText
            errLabel.cell?.wraps = true
            errLabel.cell?.isScrollable = false
            errLabel.maximumNumberOfLines = 0
            errLabel.lineBreakMode = .byWordWrapping
            errLabel.isEditable = false
            errLabel.isSelectable = true
            errLabel.isBordered = false
            errLabel.drawsBackground = false
            stack.addArrangedSubview(errLabel)
            errLabel.leadingAnchor.constraint(equalTo: stack.leadingAnchor, constant: 26).isActive = true
            errLabel.widthAnchor.constraint(lessThanOrEqualTo: stack.widthAnchor, constant: -26).isActive = true
        }
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

// MARK: - File Tab Button

final class ProviderFileTabButton: NSControl {
    let name: String
    var isTabSelected: Bool {
        didSet { updateShapeLayer() }
    }
    let hasChangeDot: Bool
    private let onSelect: (String) -> Void
    private let dot = NSView()
    private let titleLabel = NSTextField(labelWithString: "")
    private var trackingArea: NSTrackingArea?
    private var fillLayer: CAShapeLayer?
    private var strokeLayer: CAShapeLayer?

    override var isFlipped: Bool { true }

    init(name: String, isSelected: Bool, hasChangeDot: Bool, onSelect: @escaping (String) -> Void) {
        self.name = name
        self.isTabSelected = isSelected
        self.hasChangeDot = hasChangeDot
        self.onSelect = onSelect
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true

        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = 5
        stack.edgeInsets = NSEdgeInsets(top: 5, left: 9, bottom: 6, right: 9)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: topAnchor),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor)
        ])

        titleLabel.stringValue = name
        titleLabel.font = NSFont.systemFont(ofSize: 11.5, weight: isSelected ? .semibold : .medium)
        titleLabel.textColor = isSelected ? Palette.primaryText : Palette.secondaryText
        titleLabel.isEditable = false
        titleLabel.isSelectable = false
        titleLabel.isBordered = false
        titleLabel.drawsBackground = false
        stack.addArrangedSubview(titleLabel)

        if hasChangeDot {
            dot.translatesAutoresizingMaskIntoConstraints = false
            dot.wantsLayer = true
            dot.layer?.cornerRadius = 2.5
            dot.layer?.backgroundColor = NSColor.controlAccentColor.cgColor
            NSLayoutConstraint.activate([
                dot.widthAnchor.constraint(equalToConstant: 5),
                dot.heightAnchor.constraint(equalToConstant: 5)
            ])
            stack.addArrangedSubview(dot)
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func layout() {
        super.layout()
        updateShapeLayer()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateShapeLayer()
    }

    private func updateShapeLayer() {
        fillLayer?.removeFromSuperlayer()
        fillLayer = nil
        strokeLayer?.removeFromSuperlayer()
        strokeLayer = nil

        titleLabel.font = NSFont.systemFont(ofSize: 11.5, weight: isTabSelected ? .semibold : .medium)
        titleLabel.textColor = isTabSelected ? Palette.primaryText : Palette.secondaryText

        guard isTabSelected else {
            layer?.backgroundColor = NSColor.clear.cgColor
            return
        }

        layer?.backgroundColor = NSColor.clear.cgColor

        let r: CGFloat = 6.0
        let w = bounds.width
        let h = bounds.height
        guard w > 0 && h > 0 else { return }

        // 1. Fill Layer: Solid white background extending 2pt down to completely mask out the bottom border line
        let fillPath = CGMutablePath()
        fillPath.move(to: CGPoint(x: 0, y: h + 2))
        fillPath.addLine(to: CGPoint(x: 0, y: r))
        fillPath.addArc(tangent1End: CGPoint(x: 0, y: 0), tangent2End: CGPoint(x: r, y: 0), radius: r)
        fillPath.addLine(to: CGPoint(x: w - r, y: 0))
        fillPath.addArc(tangent1End: CGPoint(x: w, y: 0), tangent2End: CGPoint(x: w, y: r), radius: r)
        fillPath.addLine(to: CGPoint(x: w, y: h + 2))
        fillPath.closeSubpath()

        let fill = CAShapeLayer()
        fill.path = fillPath
        fill.fillColor = Palette.cardBackground.cgColor
        fill.strokeColor = nil
        layer?.insertSublayer(fill, at: 0)
        self.fillLayer = fill

        // 2. Stroke Layer: 3-sided OPEN path (Left -> Top-Left Arc -> Top -> Top-Right Arc -> Right)
        let strokePath = CGMutablePath()
        strokePath.move(to: CGPoint(x: 0.25, y: h - 0.5))
        strokePath.addLine(to: CGPoint(x: 0.25, y: r))
        strokePath.addArc(tangent1End: CGPoint(x: 0.25, y: 0.25), tangent2End: CGPoint(x: r, y: 0.25), radius: r)
        strokePath.addLine(to: CGPoint(x: w - r, y: 0.25))
        strokePath.addArc(tangent1End: CGPoint(x: w - 0.25, y: 0.25), tangent2End: CGPoint(x: w - 0.25, y: r), radius: r)
        strokePath.addLine(to: CGPoint(x: w - 0.25, y: h - 0.5))

        let stroke = CAShapeLayer()
        stroke.path = strokePath
        stroke.fillColor = nil
        stroke.strokeColor = Palette.cardBorder.cgColor
        stroke.lineWidth = 0.5
        layer?.insertSublayer(stroke, at: 1)
        self.strokeLayer = stroke
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let ta = trackingArea { removeTrackingArea(ta) }
        let ta = NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeInActiveApp], owner: self, userInfo: nil)
        addTrackingArea(ta)
        trackingArea = ta
    }

    override func mouseEntered(with event: NSEvent) {
        if !isTabSelected {
            layer?.backgroundColor = NSColor.dynamicColor(
                light: NSColor(white: 0.0, alpha: 0.04),
                dark: NSColor(white: 1.0, alpha: 0.06)
            ).cgColor
            layer?.cornerRadius = 6
        }
    }

    override func mouseExited(with event: NSEvent) {
        if !isTabSelected {
            layer?.backgroundColor = NSColor.clear.cgColor
        }
    }

    override func mouseUp(with event: NSEvent) {
        let loc = convert(event.locationInWindow, from: nil)
        if bounds.contains(loc) {
            onSelect(name)
        }
    }
}
