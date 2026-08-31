import AppKit
import Foundation

// Entry point for the Preferences "Workspace Access" page: it lays out the
// section chrome and the placeholder cards shown while loading, when the list
// is empty, and when the service call fails. Data loading lives in
// PreferencesWindow+WorkspaceAccessData, request cards in
// ...+WorkspaceAccessRequests, and mutations in ...+WorkspaceAccessActions.

extension PreferencesWindow {

    // MARK: Section Builder

    func buildWorkspaceAccessSection(into documentStack: NSStackView) {
        // Clear previous state
        workspaceAccessEntries = []
        workspaceAccessRequests = []
        workspaceAccessPhase = .loading
        workspaceAccessFilter = .all
        workspaceAccessSearchQuery = ""
        workspaceAccessExpandedId = nil
        workspaceAccessShowTechDetailsIds = []
        workspaceAccessDefaultPath = nil

        // Root container in document stack
        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 10
        root.translatesAutoresizingMaskIntoConstraints = false
        documentStack.addArrangedSubview(root)
        let totalInset = documentStack.edgeInsets.left + documentStack.edgeInsets.right
        root.widthAnchor.constraint(equalTo: documentStack.widthAnchor, constant: -totalInset).isActive = true
        self.workspaceAccessContainer = root

        // 1. Pending Requests Card (hidden when empty)
        let reqCard = AccentBannerCardView()
        reqCard.translatesAutoresizingMaskIntoConstraints = false
        reqCard.isHidden = true
        root.addArrangedSubview(reqCard)
        reqCard.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        self.workspaceAccessRequestsCard = reqCard

        let reqStack = NSStackView()
        reqStack.orientation = .vertical
        reqStack.alignment = .leading
        reqStack.spacing = 0
        reqStack.translatesAutoresizingMaskIntoConstraints = false
        reqCard.stackView.addArrangedSubview(reqStack)
        reqStack.widthAnchor.constraint(equalTo: reqCard.stackView.widthAnchor).isActive = true
        self.workspaceAccessRequestsStack = reqStack

        // 2. Toolbar (Search + Add Project)
        let toolbar = NSStackView()
        toolbar.orientation = .horizontal
        toolbar.alignment = .centerY
        toolbar.spacing = 8
        toolbar.translatesAutoresizingMaskIntoConstraints = false

        workspaceAccessSearchField = NSSearchField()
        workspaceAccessSearchField.placeholderString = L10n.tr("preferences.workspace_access.search_placeholder")
        workspaceAccessSearchField.delegate = self
        workspaceAccessSearchField.translatesAutoresizingMaskIntoConstraints = false
        toolbar.addArrangedSubview(workspaceAccessSearchField)

        let addBtn = NSButton(title: L10n.tr("preferences.workspace_access.btn_add_project"), target: self, action: #selector(addWorkspaceAccessClicked))
        addBtn.bezelStyle = .rounded
        addBtn.controlSize = .regular
        addBtn.keyEquivalent = ""
        toolbar.addArrangedSubview(addBtn)

        root.addArrangedSubview(toolbar)
        toolbar.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        workspaceAccessSearchField.widthAnchor.constraint(equalTo: toolbar.widthAnchor, constant: -130).isActive = true

        // 3. Metadata Line (Count info + Filter Segmented Control)
        let metaBar = NSStackView()
        metaBar.orientation = .horizontal
        metaBar.alignment = .centerY
        metaBar.spacing = 8
        metaBar.translatesAutoresizingMaskIntoConstraints = false

        workspaceAccessCountLabel = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.count.loading"))
        workspaceAccessCountLabel.font = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        workspaceAccessCountLabel.textColor = Palette.secondaryText
        workspaceAccessCountLabel.isEditable = false
        workspaceAccessCountLabel.isSelectable = false
        workspaceAccessCountLabel.isBordered = false
        workspaceAccessCountLabel.drawsBackground = false
        workspaceAccessCountLabel.setContentHuggingPriority(.required, for: .horizontal)
        workspaceAccessCountLabel.setContentCompressionResistancePriority(.required, for: .horizontal)
        workspaceAccessCountLabel.translatesAutoresizingMaskIntoConstraints = false
        metaBar.addArrangedSubview(workspaceAccessCountLabel)

        // Keep the summary anchored to the leading edge and let the filter
        // control sit at the trailing edge, matching the design's clear
        // left/right hierarchy even when the window becomes wider.
        let metaSpacer = NSView()
        metaSpacer.translatesAutoresizingMaskIntoConstraints = false
        metaSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        metaSpacer.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        metaBar.addArrangedSubview(metaSpacer)

        let filterSeg = NSSegmentedControl(
            labels: [
                L10n.tr("preferences.workspace_access.filter.all"),
                L10n.tr("preferences.workspace_access.filter.read_only"),
                L10n.tr("preferences.workspace_access.filter.read_write"),
                L10n.tr("preferences.workspace_access.filter.issues")
            ],
            trackingMode: .selectOne,
            target: self,
            action: #selector(workspaceFilterSegmentChanged(_:))
        )
        filterSeg.segmentStyle = .rounded
        filterSeg.controlSize = .small
        filterSeg.selectedSegment = 0
        filterSeg.translatesAutoresizingMaskIntoConstraints = false
        metaBar.addArrangedSubview(filterSeg)
        self.workspaceAccessFilterSegmented = filterSeg

        root.addArrangedSubview(metaBar)
        metaBar.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        // 4. Main State Container Card (Table / Empty / No Results / Error)
        let tableCard = CardView()
        tableCard.translatesAutoresizingMaskIntoConstraints = false
        root.addArrangedSubview(tableCard)
        tableCard.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        self.workspaceAccessTableCard = tableCard

        let tableHeader = WorkspaceTableHeaderView()
        tableCard.stackView.addArrangedSubview(tableHeader)
        tableHeader.widthAnchor.constraint(equalTo: tableCard.stackView.widthAnchor).isActive = true

        workspaceAccessTable = NSTableView()
        workspaceAccessTable.delegate = self
        workspaceAccessTable.dataSource = self
        workspaceAccessTable.style = .fullWidth
        workspaceAccessTable.headerView = nil
        workspaceAccessTable.rowHeight = WorkspaceAccessLayout.rowHeight
        workspaceAccessTable.usesAlternatingRowBackgroundColors = false
        workspaceAccessTable.allowsEmptySelection = true
        workspaceAccessTable.selectionHighlightStyle = .none
        workspaceAccessTable.gridStyleMask = []
        workspaceAccessTable.intercellSpacing = .zero
        workspaceAccessTable.addTableColumn(NSTableColumn(identifier: NSUserInterfaceItemIdentifier("main")))
        workspaceAccessTable.translatesAutoresizingMaskIntoConstraints = false

        let tableScroll = NSScrollView()
        tableScroll.hasVerticalScroller = true
        tableScroll.autohidesScrollers = true
        tableScroll.scrollerStyle = .overlay
        tableScroll.drawsBackground = false
        let scroller = CleanScroller()
        scroller.controlSize = .small
        tableScroll.verticalScroller = scroller
        tableScroll.documentView = workspaceAccessTable
        tableScroll.translatesAutoresizingMaskIntoConstraints = false
        let tableHeight = tableScroll.heightAnchor.constraint(equalToConstant: 296)
        tableHeight.isActive = true
        self.workspaceAccessTableHeightConstraint = tableHeight

        tableCard.stackView.addArrangedSubview(tableScroll)
        tableScroll.widthAnchor.constraint(equalTo: tableCard.stackView.widthAnchor).isActive = true
        self.workspaceAccessTableScrollView = tableScroll

        // Alternate state views inside tableCard
        workspaceAccessEmptyCard = makeEmptyCardView()
        tableCard.stackView.addArrangedSubview(workspaceAccessEmptyCard)
        workspaceAccessEmptyCard.widthAnchor.constraint(equalTo: tableCard.stackView.widthAnchor).isActive = true
        workspaceAccessEmptyCard.isHidden = true

        workspaceAccessNoResultsCard = makeNoResultsCardView()
        tableCard.stackView.addArrangedSubview(workspaceAccessNoResultsCard)
        workspaceAccessNoResultsCard.widthAnchor.constraint(equalTo: tableCard.stackView.widthAnchor).isActive = true
        workspaceAccessNoResultsCard.isHidden = true

        workspaceAccessErrorCard = makeErrorCardView()
        tableCard.stackView.addArrangedSubview(workspaceAccessErrorCard)
        workspaceAccessErrorCard.widthAnchor.constraint(equalTo: tableCard.stackView.widthAnchor).isActive = true
        workspaceAccessErrorCard.isHidden = true

        // 5. System location (shown separately from user projects)
        let systemCard = CardView()
        systemCard.translatesAutoresizingMaskIntoConstraints = false
        systemCard.isHidden = true
        root.addArrangedSubview(systemCard)
        systemCard.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        let systemContent = NSView()
        systemContent.translatesAutoresizingMaskIntoConstraints = false
        systemCard.stackView.addArrangedSubview(systemContent)
        systemContent.widthAnchor.constraint(equalTo: systemCard.stackView.widthAnchor).isActive = true

        let systemStack = NSStackView()
        systemStack.orientation = .vertical
        systemStack.alignment = .leading
        systemStack.spacing = 8
        systemStack.translatesAutoresizingMaskIntoConstraints = false
        systemContent.addSubview(systemStack)
        NSLayoutConstraint.activate([
            systemStack.topAnchor.constraint(equalTo: systemContent.topAnchor, constant: 14),
            systemStack.leadingAnchor.constraint(equalTo: systemContent.leadingAnchor, constant: 22),
            systemStack.trailingAnchor.constraint(equalTo: systemContent.trailingAnchor, constant: -22),
            systemStack.bottomAnchor.constraint(equalTo: systemContent.bottomAnchor, constant: -14)
        ])
        self.workspaceAccessSystemCard = systemCard
        self.workspaceAccessSystemStack = systemStack

        // 6. Page Footer Note
        let footStack = NSStackView()
        footStack.orientation = .vertical
        footStack.alignment = .leading
        footStack.spacing = 4
        footStack.edgeInsets = NSEdgeInsets(top: 8, left: 4, bottom: 4, right: 4)
        footStack.translatesAutoresizingMaskIntoConstraints = false

        let footNote = NSTextField(wrappingLabelWithString: L10n.tr("preferences.workspace_access.footer.note"))
        footNote.font = NSFont.systemFont(ofSize: 11)
        footNote.textColor = Palette.secondaryText
        footNote.translatesAutoresizingMaskIntoConstraints = false
        footStack.addArrangedSubview(footNote)
        footNote.widthAnchor.constraint(equalTo: footStack.widthAnchor).isActive = true

        let agentsLink = NSButton(title: L10n.tr("preferences.workspace_access.footer.link"), target: self, action: #selector(openAgentsUsageClicked))
        agentsLink.isBordered = false
        agentsLink.font = NSFont.systemFont(ofSize: 11)
        agentsLink.contentTintColor = NSColor.controlAccentColor
        footStack.addArrangedSubview(agentsLink)

        root.addArrangedSubview(footStack)
        footStack.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        reloadWorkspaceAccess()
    }

    // MARK: - State Subview Builders

    private func makeEmptyCardView() -> NSView {
        let view = NSView()
        view.translatesAutoresizingMaskIntoConstraints = false

        let icon = NSImageView()
        icon.image = WorkspaceAccessIcons.folderGlyph()
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(icon)

        let title = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.empty.title"))
        title.font = NSFont.systemFont(ofSize: 14, weight: .bold)
        title.textColor = Palette.primaryText
        title.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(title)

        let desc = NSTextField(wrappingLabelWithString: L10n.tr("preferences.workspace_access.empty.desc"))
        desc.font = NSFont.systemFont(ofSize: 12)
        desc.textColor = Palette.secondaryText
        desc.alignment = .center
        desc.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(desc)

        let btn = NSButton(title: L10n.tr("preferences.workspace_access.btn_add_project"), target: self, action: #selector(addWorkspaceAccessClicked))
        btn.bezelStyle = .rounded
        btn.controlSize = .regular
        btn.keyEquivalent = "\r"
        btn.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(btn)

        NSLayoutConstraint.activate([
            view.heightAnchor.constraint(equalToConstant: 240),

            icon.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            icon.topAnchor.constraint(equalTo: view.topAnchor, constant: 32),
            icon.widthAnchor.constraint(equalToConstant: 36),
            icon.heightAnchor.constraint(equalToConstant: 36),

            title.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            title.topAnchor.constraint(equalTo: icon.bottomAnchor, constant: 10),

            desc.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            desc.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 6),
            desc.widthAnchor.constraint(equalToConstant: 360),

            btn.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            btn.topAnchor.constraint(equalTo: desc.bottomAnchor, constant: 14)
        ])
        return view
    }

    private func makeNoResultsCardView() -> NSView {
        let view = NSView()
        view.translatesAutoresizingMaskIntoConstraints = false

        let label = NSTextField(wrappingLabelWithString: "")
        label.font = NSFont.systemFont(ofSize: 12.5)
        label.textColor = Palette.secondaryText
        label.alignment = .center
        label.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(label)
        self.workspaceAccessNoResultsLabel = label

        let clearBtn = NSButton(title: L10n.tr("preferences.workspace_access.btn_clear_filter"), target: self, action: #selector(clearSearchAndFilterClicked))
        clearBtn.bezelStyle = .rounded
        clearBtn.controlSize = .small
        clearBtn.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(clearBtn)

        NSLayoutConstraint.activate([
            view.heightAnchor.constraint(equalToConstant: 180),

            label.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            label.topAnchor.constraint(equalTo: view.topAnchor, constant: 48),
            label.widthAnchor.constraint(equalToConstant: 380),

            clearBtn.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            clearBtn.topAnchor.constraint(equalTo: label.bottomAnchor, constant: 12)
        ])
        return view
    }

    private func makeErrorCardView() -> NSView {
        let view = NSView()
        view.translatesAutoresizingMaskIntoConstraints = false

        let title = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.failed.title"))
        title.font = NSFont.systemFont(ofSize: 13.5, weight: .bold)
        title.textColor = Palette.primaryText
        title.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(title)

        let desc = NSTextField(wrappingLabelWithString: L10n.tr("preferences.workspace_access.failed.desc"))
        desc.font = NSFont.systemFont(ofSize: 12)
        desc.textColor = Palette.secondaryText
        desc.alignment = .center
        desc.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(desc)

        let retryBtn = NSButton(title: L10n.tr("preferences.workspace_access.btn_try_again"), target: self, action: #selector(retryLoadClicked))
        retryBtn.bezelStyle = .rounded
        retryBtn.controlSize = .regular
        retryBtn.keyEquivalent = "\r"
        retryBtn.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(retryBtn)

        NSLayoutConstraint.activate([
            view.heightAnchor.constraint(equalToConstant: 200),

            title.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            title.topAnchor.constraint(equalTo: view.topAnchor, constant: 40),

            desc.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            desc.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 6),
            desc.widthAnchor.constraint(equalToConstant: 380),

            retryBtn.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            retryBtn.topAnchor.constraint(equalTo: desc.bottomAnchor, constant: 14)
        ])
        return view
    }

    func renderSystemAccess() {
        guard let card = workspaceAccessSystemCard,
              let stack = workspaceAccessSystemStack
        else { return }

        for view in stack.arrangedSubviews {
            stack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }

        guard let path = workspaceAccessDefaultPath, !path.isEmpty else {
            card.isHidden = true
            return
        }
        card.isHidden = false

        let header = NSStackView()
        header.orientation = .vertical
        header.alignment = .leading
        header.spacing = 4
        header.translatesAutoresizingMaskIntoConstraints = false

        let title = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.system.title"))
        title.font = NSFont.systemFont(ofSize: 12.5, weight: .semibold)
        title.textColor = Palette.primaryText
        title.isEditable = false
        title.isSelectable = false
        title.isBordered = false
        title.drawsBackground = false
        header.addArrangedSubview(title)

        let description = NSTextField(
            wrappingLabelWithString: L10n.tr("preferences.workspace_access.system.default.desc")
        )
        description.font = NSFont.systemFont(ofSize: 11)
        description.textColor = Palette.secondaryText
        description.isEditable = false
        description.isSelectable = false
        description.isBordered = false
        description.drawsBackground = false
        header.addArrangedSubview(description)

        stack.addArrangedSubview(header)
        header.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true

        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 8
        row.translatesAutoresizingMaskIntoConstraints = false

        let icon = NSImageView()
        icon.image = WorkspaceAccessIcons.folderGlyph()
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.translatesAutoresizingMaskIntoConstraints = false
        row.addArrangedSubview(icon)
        icon.widthAnchor.constraint(equalToConstant: 22).isActive = true
        icon.heightAnchor.constraint(equalToConstant: 22).isActive = true

        let pathStack = NSStackView()
        pathStack.orientation = .vertical
        pathStack.alignment = .leading
        pathStack.spacing = 1
        pathStack.translatesAutoresizingMaskIntoConstraints = false

        let name = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.system.default.name"))
        name.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
        name.textColor = Palette.primaryText
        name.isEditable = false
        name.isSelectable = false
        name.isBordered = false
        name.drawsBackground = false
        pathStack.addArrangedSubview(name)

        let pathLabel = NSTextField(labelWithString: path)
        pathLabel.font = NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular)
        pathLabel.textColor = Palette.secondaryText
        pathLabel.isEditable = false
        pathLabel.isSelectable = false
        pathLabel.isBordered = false
        pathLabel.drawsBackground = false
        pathLabel.usesSingleLineMode = true
        pathLabel.lineBreakMode = .byTruncatingMiddle
        pathLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        pathLabel.toolTip = path
        pathStack.addArrangedSubview(pathLabel)
        pathStack.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        row.addArrangedSubview(pathStack)

        let spacer = NSView()
        spacer.translatesAutoresizingMaskIntoConstraints = false
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        spacer.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        row.addArrangedSubview(spacer)

        row.addArrangedSubview(
            WorkspacePillView(text: L10n.tr("preferences.workspace_access.system.default.badge"))
        )

        stack.addArrangedSubview(row)
        row.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
    }
}
