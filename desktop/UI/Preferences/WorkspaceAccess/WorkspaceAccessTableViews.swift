import AppKit
import Foundation

// The Workspace Access table's own views: the column header strip and the
// expandable row cell. Column anchors are shared through WorkspaceAccessLayout
// so the header stays aligned with the rows at every window width.

final class WorkspaceTableHeaderView: NSView {
    init() {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        wantsLayer = true
        layer?.backgroundColor = Palette.chromeBackground.cgColor

        let projectLabel = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.column.project"))
        let accessLabel = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.column.access"))
        let statusLabel = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.column.status"))

        for label in [projectLabel, accessLabel, statusLabel] {
            label.font = NSFont.systemFont(ofSize: 9.5, weight: .semibold)
            label.textColor = Palette.sectionHeader
            label.isEditable = false
            label.isSelectable = false
            label.isBordered = false
            label.drawsBackground = false
            label.translatesAutoresizingMaskIntoConstraints = false
            addSubview(label)
        }

        let bottomSep = NSView()
        bottomSep.translatesAutoresizingMaskIntoConstraints = false
        bottomSep.wantsLayer = true
        bottomSep.layer?.backgroundColor = Palette.separator.cgColor
        addSubview(bottomSep)

        NSLayoutConstraint.activate([
            heightAnchor.constraint(equalToConstant: 24),

            projectLabel.leadingAnchor.constraint(
                equalTo: leadingAnchor,
                constant: WorkspaceAccessLayout.projectLeading + WorkspaceAccessLayout.tableRowInset
            ),
            projectLabel.centerYAnchor.constraint(equalTo: centerYAnchor),

            accessLabel.leadingAnchor.constraint(
                equalTo: trailingAnchor,
                constant: -(WorkspaceAccessLayout.accessTrailing + WorkspaceAccessLayout.tableRowInset)
            ),
            accessLabel.centerYAnchor.constraint(equalTo: centerYAnchor),

            statusLabel.leadingAnchor.constraint(
                equalTo: trailingAnchor,
                constant: -(WorkspaceAccessLayout.statusTrailing + WorkspaceAccessLayout.tableRowInset)
            ),
            statusLabel.centerYAnchor.constraint(equalTo: centerYAnchor),

            bottomSep.leadingAnchor.constraint(equalTo: leadingAnchor),
            bottomSep.trailingAnchor.constraint(equalTo: trailingAnchor),
            bottomSep.bottomAnchor.constraint(equalTo: bottomAnchor),
            bottomSep.heightAnchor.constraint(equalToConstant: 0.5)
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}

// MARK: - Table Row Cell View

final class WorkspaceTableRowCell: NSTableCellView {
    var entry: WorkspaceAccessEntryRow?
    var isExpanded = false
    var showTechDetails = false

    var onToggleExpand: (() -> Void)?
    var onEdit: (() -> Void)?
    var onLocate: (() -> Void)?
    var onReveal: (() -> Void)?
    var onRemove: (() -> Void)?
    var onToggleTechDetails: (() -> Void)?

    private let topRow = NSView()
    private let projectStack = NSStackView()
    private let nameRow = NSStackView()
    private let nameLabel = NSTextField(labelWithString: "")
    private let pathLabel = NSTextField(labelWithString: "")
    private var accessBadge: WorkspaceAccessBadgeView?
    private var statusBadge: WorkspaceStatusBadgeView?
    private let disclosureButton = NSButton()
    private let rowSeparator = NSView()

    private let drawerContainer = NSView()
    private let drawerTopSep = NSView()
    private let drawerStack = NSStackView()

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setupViews()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func setupViews() {
        translatesAutoresizingMaskIntoConstraints = false
        topRow.translatesAutoresizingMaskIntoConstraints = false
        addSubview(topRow)

        projectStack.orientation = .vertical
        projectStack.alignment = .leading
        projectStack.spacing = 1
        projectStack.translatesAutoresizingMaskIntoConstraints = false
        topRow.addSubview(projectStack)

        nameRow.orientation = .horizontal
        nameRow.alignment = .centerY
        nameRow.spacing = 6
        nameRow.translatesAutoresizingMaskIntoConstraints = false
        projectStack.addArrangedSubview(nameRow)

        nameLabel.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        nameLabel.textColor = Palette.primaryText
        nameLabel.lineBreakMode = .byTruncatingTail
        nameLabel.isEditable = false
        nameLabel.isSelectable = false
        nameLabel.isBordered = false
        nameLabel.drawsBackground = false
        nameLabel.setContentHuggingPriority(.defaultHigh, for: .horizontal)
        nameLabel.setContentCompressionResistancePriority(.defaultHigh, for: .horizontal)
        nameLabel.translatesAutoresizingMaskIntoConstraints = false
        nameRow.addArrangedSubview(nameLabel)

        pathLabel.font = NSFont.monospacedSystemFont(ofSize: 9.5, weight: .regular)
        pathLabel.textColor = Palette.secondaryText
        pathLabel.lineBreakMode = .byTruncatingMiddle
        pathLabel.isEditable = false
        pathLabel.isSelectable = false
        pathLabel.isBordered = false
        pathLabel.drawsBackground = false
        pathLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
        pathLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        pathLabel.translatesAutoresizingMaskIntoConstraints = false
        projectStack.addArrangedSubview(pathLabel)

        disclosureButton.bezelStyle = .regularSquare
        disclosureButton.isBordered = false
        disclosureButton.title = ""
        disclosureButton.target = self
        disclosureButton.action = #selector(disclosureClicked)
        disclosureButton.translatesAutoresizingMaskIntoConstraints = false
        topRow.addSubview(disclosureButton)

        rowSeparator.translatesAutoresizingMaskIntoConstraints = false
        rowSeparator.wantsLayer = true
        rowSeparator.layer?.backgroundColor = Palette.separator.withAlphaComponent(0.7).cgColor
        addSubview(rowSeparator)

        drawerContainer.translatesAutoresizingMaskIntoConstraints = false
        drawerContainer.wantsLayer = true
        drawerContainer.layer?.backgroundColor = Palette.chromeBackground.cgColor
        addSubview(drawerContainer)

        drawerTopSep.translatesAutoresizingMaskIntoConstraints = false
        drawerTopSep.wantsLayer = true
        drawerTopSep.layer?.backgroundColor = Palette.separator.cgColor
        drawerContainer.addSubview(drawerTopSep)

        drawerStack.orientation = .vertical
        drawerStack.alignment = .leading
        drawerStack.distribution = .fill
        drawerStack.spacing = 10
        drawerStack.edgeInsets = NSEdgeInsets(top: 10, left: 14, bottom: 12, right: 14)
        drawerStack.translatesAutoresizingMaskIntoConstraints = false
        drawerContainer.addSubview(drawerStack)

        NSLayoutConstraint.activate([
            topRow.topAnchor.constraint(equalTo: topAnchor),
            topRow.leadingAnchor.constraint(equalTo: leadingAnchor),
            topRow.trailingAnchor.constraint(equalTo: trailingAnchor),
            topRow.heightAnchor.constraint(equalToConstant: WorkspaceAccessLayout.rowHeight),

            projectStack.leadingAnchor.constraint(equalTo: topRow.leadingAnchor, constant: WorkspaceAccessLayout.projectLeading),
            projectStack.trailingAnchor.constraint(lessThanOrEqualTo: topRow.trailingAnchor, constant: -254),
            projectStack.centerYAnchor.constraint(equalTo: topRow.centerYAnchor),
            nameRow.widthAnchor.constraint(lessThanOrEqualTo: projectStack.widthAnchor),
            pathLabel.widthAnchor.constraint(equalTo: projectStack.widthAnchor),

            disclosureButton.trailingAnchor.constraint(equalTo: topRow.trailingAnchor, constant: -WorkspaceAccessLayout.disclosureTrailing),
            disclosureButton.centerYAnchor.constraint(equalTo: topRow.centerYAnchor),
            disclosureButton.widthAnchor.constraint(equalToConstant: 20),
            disclosureButton.heightAnchor.constraint(equalToConstant: 20),

            rowSeparator.leadingAnchor.constraint(equalTo: topRow.leadingAnchor, constant: WorkspaceAccessLayout.projectLeading),
            rowSeparator.trailingAnchor.constraint(equalTo: topRow.trailingAnchor, constant: -WorkspaceAccessLayout.projectLeading),
            rowSeparator.bottomAnchor.constraint(equalTo: topRow.bottomAnchor),
            rowSeparator.heightAnchor.constraint(equalToConstant: 0.5),

            drawerContainer.topAnchor.constraint(equalTo: topRow.bottomAnchor),
            drawerContainer.leadingAnchor.constraint(equalTo: leadingAnchor),
            drawerContainer.trailingAnchor.constraint(equalTo: trailingAnchor),
            drawerContainer.bottomAnchor.constraint(equalTo: bottomAnchor),

            drawerTopSep.topAnchor.constraint(equalTo: drawerContainer.topAnchor),
            drawerTopSep.leadingAnchor.constraint(equalTo: drawerContainer.leadingAnchor),
            drawerTopSep.trailingAnchor.constraint(equalTo: drawerContainer.trailingAnchor),
            drawerTopSep.heightAnchor.constraint(equalToConstant: 0.5),

            drawerStack.topAnchor.constraint(equalTo: drawerTopSep.bottomAnchor),
            drawerStack.leadingAnchor.constraint(equalTo: drawerContainer.leadingAnchor),
            drawerStack.trailingAnchor.constraint(equalTo: drawerContainer.trailingAnchor),
            drawerStack.bottomAnchor.constraint(equalTo: drawerContainer.bottomAnchor)
        ])
    }

    func configure(
        entry: WorkspaceAccessEntryRow,
        isExpanded: Bool,
        showTechDetails: Bool
    ) {
        self.entry = entry
        self.isExpanded = isExpanded
        self.showTechDetails = showTechDetails

        nameLabel.stringValue = entry.projectName
        nameLabel.toolTip = entry.projectName
        pathLabel.stringValue = entry.path
        pathLabel.toolTip = entry.path

        accessBadge?.removeFromSuperview()
        let aBadge = WorkspaceAccessBadgeView(isWrite: entry.isWrite)
        topRow.addSubview(aBadge)
        NSLayoutConstraint.activate([
            aBadge.leadingAnchor.constraint(equalTo: topRow.trailingAnchor, constant: -WorkspaceAccessLayout.accessTrailing),
            aBadge.centerYAnchor.constraint(equalTo: topRow.centerYAnchor)
        ])
        self.accessBadge = aBadge

        statusBadge?.removeFromSuperview()
        let sBadge = WorkspaceStatusBadgeView(entry: entry)
        topRow.addSubview(sBadge)
        NSLayoutConstraint.activate([
            sBadge.leadingAnchor.constraint(equalTo: topRow.trailingAnchor, constant: -WorkspaceAccessLayout.statusTrailing),
            sBadge.centerYAnchor.constraint(equalTo: topRow.centerYAnchor)
        ])
        self.statusBadge = sBadge

        disclosureButton.image = WorkspaceAccessIcons.disclosureTriangle(isOpen: isExpanded)
        drawerContainer.isHidden = !isExpanded

        if isExpanded {
            rebuildDrawer()
        }
    }

    private func rebuildDrawer() {
        guard let entry = entry else { return }
        for view in drawerStack.arrangedSubviews {
            drawerStack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }

        // 1. Alert Banner
        if entry.isMissing {
            let alertBox = makeMessageBanner(
                message: L10n.tr("preferences.workspace_access.drawer.missing_msg"),
                isWarning: true
            )
            drawerStack.addArrangedSubview(alertBox)
            alertBox.widthAnchor.constraint(equalTo: drawerStack.widthAnchor, constant: -28).isActive = true
            alertBox.setContentHuggingPriority(.required, for: .vertical)
            alertBox.setContentCompressionResistancePriority(.required, for: .vertical)
        } else if entry.isBlocked {
            let alertBox = makeMessageBanner(
                message: L10n.tr("preferences.workspace_access.drawer.blocked_msg"),
                isWarning: true
            )
            drawerStack.addArrangedSubview(alertBox)
            alertBox.widthAnchor.constraint(equalTo: drawerStack.widthAnchor, constant: -28).isActive = true
            alertBox.setContentHuggingPriority(.required, for: .vertical)
            alertBox.setContentCompressionResistancePriority(.required, for: .vertical)
        } else if !entry.isManaged {
            let alertBox = makeMessageBanner(
                message: L10n.tr("preferences.workspace_access.drawer.managed_elsewhere_msg"),
                isWarning: false
            )
            drawerStack.addArrangedSubview(alertBox)
            alertBox.widthAnchor.constraint(equalTo: drawerStack.widthAnchor, constant: -28).isActive = true
            alertBox.setContentHuggingPriority(.required, for: .vertical)
            alertBox.setContentCompressionResistancePriority(.required, for: .vertical)
        }

        // 2. Metadata Grid
        let grid = NSStackView()
        grid.orientation = .vertical
        grid.alignment = .leading
        grid.spacing = 5
        grid.translatesAutoresizingMaskIntoConstraints = false

        let accessHuman = entry.isWrite
            ? "\(L10n.tr("preferences.workspace_access.access.read_write")) — \(L10n.tr("preferences.workspace_access.access.read_write_desc"))"
            : "\(L10n.tr("preferences.workspace_access.access.read_only")) — \(L10n.tr("preferences.workspace_access.access.read_only_desc"))"
        grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.drawer.access_label"), value: accessHuman))

        if entry.isManaged {
            let agentName = entry.defaultExecutor.isEmpty
                ? L10n.tr("preferences.workspace_access.sheet.agent_fluxion_default")
                : (PreferencesWindow.executorDisplayNames[entry.defaultExecutor.lowercased()] ?? entry.defaultExecutor)
            grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.drawer.default_agent_label"), value: agentName))
            grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.drawer.managed_by_label"), value: L10n.tr("preferences.workspace_access.drawer.managed_this_app")))
        } else {
            grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.drawer.managed_by_label"), value: L10n.tr("preferences.workspace_access.drawer.managed_env")))
        }

        if !entry.description.isEmpty {
            grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.drawer.note_label"), value: entry.description))
        }

        drawerStack.addArrangedSubview(grid)
        grid.widthAnchor.constraint(equalTo: drawerStack.widthAnchor, constant: -28).isActive = true
        grid.setContentHuggingPriority(.required, for: .vertical)
        grid.setContentCompressionResistancePriority(.required, for: .vertical)

        // 3. Action Bar
        let actionBar = NSStackView()
        actionBar.orientation = .horizontal
        actionBar.alignment = .centerY
        actionBar.spacing = 8
        actionBar.translatesAutoresizingMaskIntoConstraints = false

        if entry.isManaged {
            let editBtn = NSButton(title: L10n.tr("preferences.workspace_access.drawer.btn_edit"), target: self, action: #selector(editClicked))
            editBtn.bezelStyle = .rounded
            editBtn.controlSize = .small
            actionBar.addArrangedSubview(editBtn)

            if entry.isMissing {
                let locateBtn = NSButton(title: L10n.tr("preferences.workspace_access.drawer.btn_locate"), target: self, action: #selector(locateClicked))
                locateBtn.bezelStyle = .rounded
                locateBtn.controlSize = .small
                actionBar.addArrangedSubview(locateBtn)
            } else if FileManager.default.fileExists(atPath: entry.path) {
                let revealBtn = NSButton(title: L10n.tr("preferences.workspace_access.drawer.btn_reveal"), target: self, action: #selector(revealClicked))
                revealBtn.bezelStyle = .rounded
                revealBtn.controlSize = .small
                actionBar.addArrangedSubview(revealBtn)
            }

            let removeBtn = NSButton(title: L10n.tr("preferences.workspace_access.drawer.btn_remove"), target: self, action: #selector(removeClicked))
            removeBtn.bezelStyle = .rounded
            removeBtn.controlSize = .small
            actionBar.addArrangedSubview(removeBtn)
        } else {
            if FileManager.default.fileExists(atPath: entry.path) {
                let revealBtn = NSButton(title: L10n.tr("preferences.workspace_access.drawer.btn_reveal"), target: self, action: #selector(revealClicked))
                revealBtn.bezelStyle = .rounded
                revealBtn.controlSize = .small
                actionBar.addArrangedSubview(revealBtn)
            }
        }

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        actionBar.addArrangedSubview(spacer)

        let advBtn = NSButton(title: L10n.tr("preferences.workspace_access.drawer.btn_advanced"), target: self, action: #selector(techClicked))
        advBtn.isBordered = false
        advBtn.font = NSFont.systemFont(ofSize: 11)
        advBtn.image = WorkspaceAccessIcons.disclosureTriangle(isOpen: showTechDetails)
        advBtn.imagePosition = .imageLeading
        actionBar.addArrangedSubview(advBtn)

        drawerStack.addArrangedSubview(actionBar)
        actionBar.widthAnchor.constraint(equalTo: drawerStack.widthAnchor, constant: -28).isActive = true
        actionBar.setContentHuggingPriority(.required, for: .vertical)
        actionBar.setContentCompressionResistancePriority(.required, for: .vertical)

        // 4. Advanced Details Section
        if showTechDetails {
            let techBox = NSStackView()
            techBox.orientation = .vertical
            techBox.alignment = .leading
            techBox.spacing = 4
            techBox.translatesAutoresizingMaskIntoConstraints = false

            let sep = NSView()
            sep.translatesAutoresizingMaskIntoConstraints = false
            sep.wantsLayer = true
            sep.layer?.backgroundColor = Palette.separator.cgColor
            techBox.addArrangedSubview(sep)
            sep.widthAnchor.constraint(equalTo: techBox.widthAnchor).isActive = true
            sep.heightAnchor.constraint(equalToConstant: 0.5).isActive = true

            let sourceText = entry.isManaged
                ? "workspace_access.json (\(L10n.tr("preferences.workspace_access.tech.app_registry")))"
                : "\(environmentSourceDisplayName(entry.source)) (\(L10n.tr("preferences.workspace_access.tech.env_source")))"
            let discoveryText = entry.isManaged
                ? L10n.tr("preferences.workspace_access.tech.app_entry")
                : L10n.tr("preferences.workspace_access.tech.env_settings")

            techBox.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.tech.source"), value: sourceText, isMonospace: true))
            techBox.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.tech.policy"), value: entry.isWrite ? "workspace-write" : "read-only", isMonospace: true))
            techBox.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.tech.discovery"), value: discoveryText))
            techBox.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.tech.resolved_path"), value: entry.path, isMonospace: true))

            drawerStack.addArrangedSubview(techBox)
            techBox.widthAnchor.constraint(equalTo: drawerStack.widthAnchor, constant: -28).isActive = true
            techBox.setContentHuggingPriority(.required, for: .vertical)
            techBox.setContentCompressionResistancePriority(.required, for: .vertical)
        }
    }

    private func makeMessageBanner(message: String, isWarning: Bool) -> NSView {
        let view = NSView()
        view.translatesAutoresizingMaskIntoConstraints = false
        view.wantsLayer = true
        view.layer?.cornerRadius = 6

        let label = NSTextField(wrappingLabelWithString: message)
        label.font = NSFont.systemFont(ofSize: 11.5)
        label.isEditable = false
        label.isSelectable = false
        label.isBordered = false
        label.drawsBackground = false
        label.translatesAutoresizingMaskIntoConstraints = false

        if isWarning {
            view.layer?.backgroundColor = NSColor.systemOrange.withAlphaComponent(0.12).cgColor
            label.textColor = NSColor.systemOrange
        } else {
            view.layer?.backgroundColor = Palette.separator.withAlphaComponent(0.2).cgColor
            label.textColor = Palette.primaryText
        }

        view.addSubview(label)
        NSLayoutConstraint.activate([
            label.topAnchor.constraint(equalTo: view.topAnchor, constant: 7),
            label.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 10),
            label.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -10),
            label.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -7)
        ])
        return view
    }

    private func makeGridRow(label: String, value: String, isMonospace: Bool = false) -> NSView {
        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .top
        row.spacing = 10
        row.translatesAutoresizingMaskIntoConstraints = false

        let dt = NSTextField(labelWithString: label)
        dt.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        dt.textColor = Palette.secondaryText
        dt.alignment = .right
        dt.isEditable = false
        dt.isSelectable = false
        dt.isBordered = false
        dt.drawsBackground = false
        dt.translatesAutoresizingMaskIntoConstraints = false
        dt.widthAnchor.constraint(equalToConstant: 104).isActive = true
        row.addArrangedSubview(dt)

        let dd = NSTextField(wrappingLabelWithString: value)
        dd.font = isMonospace ? NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular) : NSFont.systemFont(ofSize: 11, weight: .regular)
        dd.textColor = Palette.primaryText
        dd.isEditable = false
        dd.isSelectable = false
        dd.isBordered = false
        dd.drawsBackground = false
        dd.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        dd.translatesAutoresizingMaskIntoConstraints = false
        row.addArrangedSubview(dd)

        return row
    }

    private func environmentSourceDisplayName(_ source: String) -> String {
        let withoutCategory = source.hasPrefix("legacy:")
            ? String(source.dropFirst("legacy:".count))
            : source
        return withoutCategory.isEmpty
            ? L10n.tr("preferences.workspace_access.tech.env_settings")
            : withoutCategory.replacingOccurrences(of: ":", with: " · ")
    }

    @objc private func disclosureClicked() { onToggleExpand?() }
    @objc private func editClicked() { onEdit?() }
    @objc private func locateClicked() { onLocate?() }
    @objc private func revealClicked() { onReveal?() }
    @objc private func removeClicked() { onRemove?() }
    @objc private func techClicked() { onToggleTechDetails?() }
}
