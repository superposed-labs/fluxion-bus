import AppKit
import Foundation

// Sheets driven by pending access requests: the per-request detail view, the
// "grant is still effective" notice shown after a removal, and the list sheet
// used once more requests are pending than the inline card can show.

final class WorkspaceAccessRequestDetailsSheetController: NSWindowController {
    let request: WorkspaceAccessRequestRow
    var onAllowOnce: (() -> Void)?
    var onAlwaysAllowProject: (() -> Void)?
    var onConfigureProject: (() -> Void)?
    var onReject: (() -> Void)?

    init(request: WorkspaceAccessRequestRow) {
        self.request = request
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 480, height: 420),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        panel.title = L10n.tr("preferences.workspace_access.reqdetail.title")
        panel.isReleasedWhenClosed = false
        super.init(window: panel)
        configureUI()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func configureUI() {
        guard let content = window?.contentView else { return }
        content.wantsLayer = true
        content.layer?.backgroundColor = Palette.windowBackground.cgColor

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 13
        root.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(root)

        NSLayoutConstraint.activate([
            root.topAnchor.constraint(equalTo: content.topAnchor, constant: 18),
            root.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 22),
            root.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -22),
            root.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -18)
        ])

        // Top Header with Close Button
        let headerRow = NSStackView()
        headerRow.orientation = .horizontal
        headerRow.alignment = .centerY
        headerRow.translatesAutoresizingMaskIntoConstraints = false

        let heading = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.reqdetail.title"))
        heading.font = NSFont.systemFont(ofSize: 16, weight: .bold)
        heading.textColor = Palette.primaryText
        headerRow.addArrangedSubview(heading)

        let headerSpacer = NSView()
        headerSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        headerRow.addArrangedSubview(headerSpacer)

        let closeBtn = NSButton(title: "✕", target: self, action: #selector(cancelClicked))
        closeBtn.isBordered = false
        closeBtn.font = NSFont.systemFont(ofSize: 13, weight: .regular)
        closeBtn.contentTintColor = Palette.secondaryText
        closeBtn.toolTip = L10n.tr("preferences.workspace_access.sheet.btn_cancel")
        headerRow.addArrangedSubview(closeBtn)

        root.addArrangedSubview(headerRow)
        headerRow.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        let subhead = NSTextField(wrappingLabelWithString: L10n.tr("preferences.workspace_access.reqdetail.sub"))
        subhead.font = NSFont.systemFont(ofSize: 12, weight: .regular)
        subhead.textColor = Palette.secondaryText
        root.addArrangedSubview(subhead)
        subhead.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        // Grid of properties
        let grid = NSStackView()
        grid.orientation = .vertical
        grid.alignment = .leading
        grid.spacing = 6
        grid.translatesAutoresizingMaskIntoConstraints = false

        grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.reqdetail.requested_by"), value: request.requesterDisplayName))
        grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.reqdetail.project"), value: request.projectName))
        grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.reqdetail.folder"), value: request.path, isMonospace: true))
        grid.addArrangedSubview(makeAccessGridRow(label: L10n.tr("preferences.workspace_access.reqdetail.access_asked"), isWrite: request.isWrite))
        if !request.createdAt.isEmpty {
            let createdVal = WorkspaceAccessTimeFormatter.formatRelativeAt(from: request.createdAt) ?? request.createdAt
            grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.reqdetail.requested"), value: createdVal))
        }
        if !request.expiresAt.isEmpty {
            let expiresVal = WorkspaceAccessTimeFormatter.formatExpires(from: request.expiresAt)
            grid.addArrangedSubview(makeGridRow(label: L10n.tr("preferences.workspace_access.reqdetail.expires"), value: expiresVal))
        }

        root.addArrangedSubview(grid)
        grid.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        // Explanatory bullets for "Allow This Task"
        let bulletsTitle = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.reqdetail.what_allow_once"))
        bulletsTitle.font = NSFont.systemFont(ofSize: 12, weight: .semibold)
        bulletsTitle.textColor = Palette.primaryText
        root.addArrangedSubview(bulletsTitle)

        let bullets = NSStackView()
        bullets.orientation = .vertical
        bullets.alignment = .leading
        bullets.spacing = 3
        bullets.translatesAutoresizingMaskIntoConstraints = false

        for key in ["bullet1", "bullet2", "bullet3", "bullet4"] {
            let item = NSTextField(wrappingLabelWithString: "•  \(L10n.tr("preferences.workspace_access.requests.\(key)"))")
            item.font = NSFont.systemFont(ofSize: 10.5)
            item.textColor = Palette.secondaryText
            item.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            bullets.addArrangedSubview(item)
        }

        let bulletsContainer = NSView()
        bulletsContainer.translatesAutoresizingMaskIntoConstraints = false
        bulletsContainer.addSubview(bullets)
        NSLayoutConstraint.activate([
            bullets.topAnchor.constraint(equalTo: bulletsContainer.topAnchor),
            bullets.bottomAnchor.constraint(equalTo: bulletsContainer.bottomAnchor),
            bullets.leadingAnchor.constraint(equalTo: bulletsContainer.leadingAnchor, constant: 12),
            bullets.trailingAnchor.constraint(equalTo: bulletsContainer.trailingAnchor),
        ])
        root.addArrangedSubview(bulletsContainer)
        bulletsContainer.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        let note = NSTextField(wrappingLabelWithString: L10n.tr("preferences.workspace_access.reqdetail.note"))
        note.font = NSFont.systemFont(ofSize: 11)
        note.textColor = Palette.secondaryText
        root.addArrangedSubview(note)
        note.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        // Actions
        let buttonRow = NSStackView()
        buttonRow.orientation = .horizontal
        buttonRow.alignment = .centerY
        buttonRow.spacing = 8
        buttonRow.translatesAutoresizingMaskIntoConstraints = false

        let rejectBtn = WorkspaceAccessStyledButton(title: L10n.tr("preferences.workspace_access.requests.btn_reject"), style: .standard, target: self, action: #selector(rejectClicked))
        buttonRow.addArrangedSubview(rejectBtn)

        let configBtn = WorkspaceAccessStyledButton(title: L10n.tr("preferences.workspace_access.requests.btn_configure_project"), style: .plainLink, target: self, action: #selector(configureProjectClicked))
        buttonRow.addArrangedSubview(configBtn)

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        buttonRow.addArrangedSubview(spacer)

        let addBtn = WorkspaceAccessStyledButton(title: L10n.tr("preferences.workspace_access.requests.btn_add_project"), style: .standard, target: self, action: #selector(alwaysAllowProjectClicked))
        buttonRow.addArrangedSubview(addBtn)

        let allowBtn = WorkspaceAccessStyledButton(title: L10n.tr("preferences.workspace_access.requests.btn_allow_once"), style: .accent, target: self, action: #selector(allowOnceClicked))
        allowBtn.keyEquivalent = "\r"
        buttonRow.addArrangedSubview(allowBtn)

        root.addArrangedSubview(buttonRow)
        buttonRow.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
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
        dt.widthAnchor.constraint(equalToConstant: 76).isActive = true
        row.addArrangedSubview(dt)

        let dd = NSTextField(wrappingLabelWithString: value)
        dd.font = isMonospace ? NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular) : NSFont.systemFont(ofSize: 11.5, weight: .regular)
        dd.textColor = Palette.primaryText
        dd.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        row.addArrangedSubview(dd)

        return row
    }

    private func makeAccessGridRow(label: String, isWrite: Bool) -> NSView {
        let row = NSStackView()
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 10
        row.translatesAutoresizingMaskIntoConstraints = false

        let dt = NSTextField(labelWithString: label)
        dt.font = NSFont.systemFont(ofSize: 11, weight: .regular)
        dt.textColor = Palette.secondaryText
        dt.alignment = .right
        dt.widthAnchor.constraint(equalToConstant: 76).isActive = true
        row.addArrangedSubview(dt)

        let badge = WorkspaceAccessBadgeView(isWrite: isWrite)
        row.addArrangedSubview(badge)

        return row
    }

    @objc private func cancelClicked() {
        closeSheet()
    }

    @objc private func rejectClicked() {
        closeSheet()
        onReject?()
    }

    @objc private func alwaysAllowProjectClicked() {
        closeSheet()
        onAlwaysAllowProject?()
    }

    @objc private func configureProjectClicked() {
        closeSheet()
        onConfigureProject?()
    }

    @objc private func allowOnceClicked() {
        closeSheet()
        onAllowOnce?()
    }

    override func cancelOperation(_ sender: Any?) {
        closeSheet()
    }

    func closeSheet() {
        if let sheet = window, let parent = sheet.sheetParent {
            parent.endSheet(sheet)
        } else {
            window?.close()
        }
    }
}

// MARK: - Still Effective Notification Sheet

final class WorkspaceAccessStillEffectiveSheetController: NSWindowController {
    init(result: WorkspaceAccessDeleteResponse) {
        let projectName = (result.path as NSString).lastPathComponent
        let removedAccess = Self.accessDisplayName(result.removedAccess ?? "read-write")
        let remainingAccess = Self.accessDisplayName(result.remainingAccess ?? "read-only")
        let remainingRoot = Self.abbreviatedPath(result.remainingRoot ?? result.path)
        let source = Self.sourceDisplayName(result.remainingSource ?? "")
        let reason: String
        if result.remainingPolicy == "trusted-git-read" {
            reason = L10n.tr(
                "preferences.workspace_access.still_effective.reason.trusted",
                remainingRoot
            )
        } else {
            reason = L10n.tr(
                "preferences.workspace_access.still_effective.reason.other",
                source,
                remainingRoot
            )
        }

        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 250),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        panel.title = L10n.tr("preferences.workspace_access.still_effective.title")
        panel.isReleasedWhenClosed = false
        super.init(window: panel)

        guard let content = window?.contentView else { return }
        content.wantsLayer = true
        content.layer?.backgroundColor = Palette.windowBackground.cgColor

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 12
        root.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(root)

        NSLayoutConstraint.activate([
            root.topAnchor.constraint(equalTo: content.topAnchor, constant: 20),
            root.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            root.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            root.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -20)
        ])

        let heading = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.still_effective.title"))
        heading.font = NSFont.systemFont(ofSize: 15, weight: .bold)
        heading.textColor = Palette.primaryText
        root.addArrangedSubview(heading)

        let banner = NSView()
        banner.translatesAutoresizingMaskIntoConstraints = false
        banner.wantsLayer = true
        banner.layer?.cornerRadius = 6
        banner.layer?.backgroundColor = NSColor.systemOrange.withAlphaComponent(0.12).cgColor

        let bannerLabel = NSTextField(
            wrappingLabelWithString: L10n.tr(
                "preferences.workspace_access.still_effective.banner_desc",
                projectName,
                removedAccess,
                remainingAccess
            )
        )
        bannerLabel.font = NSFont.systemFont(ofSize: 11.5)
        bannerLabel.textColor = NSColor.systemOrange
        bannerLabel.translatesAutoresizingMaskIntoConstraints = false
        banner.addSubview(bannerLabel)

        NSLayoutConstraint.activate([
            bannerLabel.topAnchor.constraint(equalTo: banner.topAnchor, constant: 8),
            bannerLabel.leadingAnchor.constraint(equalTo: banner.leadingAnchor, constant: 10),
            bannerLabel.trailingAnchor.constraint(equalTo: banner.trailingAnchor, constant: -10),
            bannerLabel.bottomAnchor.constraint(equalTo: banner.bottomAnchor, constant: -8)
        ])
        root.addArrangedSubview(banner)
        banner.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        let note = NSTextField(
            wrappingLabelWithString: "\(reason)\n\n\(L10n.tr("preferences.workspace_access.still_effective.note"))"
        )
        note.font = NSFont.systemFont(ofSize: 11)
        note.textColor = Palette.secondaryText
        root.addArrangedSubview(note)
        note.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true

        let buttonRow = NSStackView()
        buttonRow.orientation = .horizontal
        buttonRow.alignment = .centerY
        buttonRow.translatesAutoresizingMaskIntoConstraints = false

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        buttonRow.addArrangedSubview(spacer)

        let doneBtn = NSButton(title: L10n.tr("preferences.workspace_access.still_effective.btn_done"), target: self, action: #selector(doneClicked))
        doneBtn.bezelStyle = .rounded
        doneBtn.keyEquivalent = "\r"
        buttonRow.addArrangedSubview(doneBtn)

        root.addArrangedSubview(buttonRow)
        buttonRow.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
    }

    private static func accessDisplayName(_ access: String) -> String {
        access == "read-write"
            ? L10n.tr("preferences.workspace_access.access.read_write")
            : L10n.tr("preferences.workspace_access.access.read_only")
    }

    private static func abbreviatedPath(_ rawPath: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.standardizedFileURL.path
        if rawPath == home {
            return "~"
        }
        let prefix = home + "/"
        if rawPath.hasPrefix(prefix) {
            return "~/" + String(rawPath.dropFirst(prefix.count))
        }
        return rawPath
    }

    private static func sourceDisplayName(_ source: String) -> String {
        if source == "legacy:trusted_workspace_roots" {
            return "FLUXION_TRUSTED_WORKSPACE_ROOTS"
        }
        if source.hasPrefix("legacy:") {
            return String(source.dropFirst("legacy:".count))
        }
        return source.isEmpty
            ? L10n.tr("preferences.workspace_access.still_effective.source.unknown")
            : source
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    @objc private func doneClicked() {
        if let sheet = window, let parent = sheet.sheetParent {
            parent.endSheet(sheet)
        } else {
            window?.close()
        }
    }
}

// MARK: - All Pending Requests Sheet (when >3 exist)

final class WorkspaceAccessAllRequestsSheetController: NSWindowController {
    let requests: [WorkspaceAccessRequestRow]
    var onAllowOnce: ((WorkspaceAccessRequestRow) -> Void)?
    var onAddProject: ((WorkspaceAccessRequestRow) -> Void)?
    var onReject: ((WorkspaceAccessRequestRow) -> Void)?
    var onDetails: ((WorkspaceAccessRequestRow) -> Void)?

    init(requests: [WorkspaceAccessRequestRow]) {
        self.requests = requests
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 460),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        panel.title = L10n.tr("preferences.workspace_access.requests.all_title")
        panel.isReleasedWhenClosed = false
        super.init(window: panel)
        configureUI()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func configureUI() {
        guard let content = window?.contentView else { return }
        content.wantsLayer = true
        content.layer?.backgroundColor = Palette.windowBackground.cgColor

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 12
        root.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(root)

        NSLayoutConstraint.activate([
            root.topAnchor.constraint(equalTo: content.topAnchor, constant: 20),
            root.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            root.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            root.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -20)
        ])

        let heading = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.requests.all_title"))
        heading.font = NSFont.systemFont(ofSize: 16, weight: .bold)
        heading.textColor = Palette.primaryText
        root.addArrangedSubview(heading)

        let subhead = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.requests.all_desc"))
        subhead.font = NSFont.systemFont(ofSize: 12)
        subhead.textColor = Palette.secondaryText
        root.addArrangedSubview(subhead)

        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.drawsBackground = false
        scroll.translatesAutoresizingMaskIntoConstraints = false

        let listStack = FlippedStackView()
        listStack.orientation = .vertical
        listStack.alignment = .leading
        listStack.spacing = 10
        listStack.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = listStack

        for req in requests {
            let card = makeRequestCard(req)
            listStack.addArrangedSubview(card)
            card.widthAnchor.constraint(equalTo: listStack.widthAnchor).isActive = true
        }

        root.addArrangedSubview(scroll)
        NSLayoutConstraint.activate([
            scroll.widthAnchor.constraint(equalTo: root.widthAnchor),
            scroll.heightAnchor.constraint(equalToConstant: 300),
            listStack.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor)
        ])

        let buttonRow = NSStackView()
        buttonRow.orientation = .horizontal
        buttonRow.alignment = .centerY
        buttonRow.translatesAutoresizingMaskIntoConstraints = false

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        buttonRow.addArrangedSubview(spacer)

        let closeBtn = WorkspaceAccessStyledButton(title: L10n.tr("preferences.workspace_access.sheet.btn_close"), style: .standard, target: self, action: #selector(closeClicked))
        closeBtn.keyEquivalent = "\r"
        buttonRow.addArrangedSubview(closeBtn)

        root.addArrangedSubview(buttonRow)
        buttonRow.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
    }

    private func makeRequestCard(_ req: WorkspaceAccessRequestRow) -> NSView {
        let card = CardView()
        card.translatesAutoresizingMaskIntoConstraints = false

        let contentStack = NSStackView()
        contentStack.orientation = .vertical
        contentStack.alignment = .leading
        contentStack.spacing = 0
        contentStack.edgeInsets = NSEdgeInsets(top: 10, left: 12, bottom: 10, right: 12)
        contentStack.translatesAutoresizingMaskIntoConstraints = false

        let topRow = NSStackView()
        topRow.orientation = .horizontal
        topRow.alignment = .centerY
        topRow.spacing = 8
        topRow.translatesAutoresizingMaskIntoConstraints = false

        let name = NSTextField(labelWithString: req.projectName)
        name.font = NSFont.systemFont(ofSize: 12.5, weight: .semibold)
        name.textColor = Palette.primaryText
        topRow.addArrangedSubview(name)

        let badge = WorkspaceAccessBadgeView(isWrite: req.isWrite)
        topRow.addArrangedSubview(badge)

        contentStack.addArrangedSubview(topRow)
        contentStack.setCustomSpacing(3, after: topRow)

        let path = NSTextField(labelWithString: req.path)
        path.font = NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular)
        path.textColor = Palette.secondaryText.withAlphaComponent(0.85)
        path.lineBreakMode = .byTruncatingMiddle
        contentStack.addArrangedSubview(path)
        contentStack.setCustomSpacing(4, after: path)

        let meta = makeWorkspaceRequestMetaLabel(req: req)
        contentStack.addArrangedSubview(meta)
        contentStack.setCustomSpacing(8, after: meta)

        let actions = NSStackView()
        actions.orientation = .horizontal
        actions.alignment = .centerY
        actions.spacing = 6
        actions.translatesAutoresizingMaskIntoConstraints = false

        let allowBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_allow_once"), style: .accent, request: req) { [weak self] r in
            self?.closeSheet()
            self?.onAllowOnce?(r)
        }
        actions.addArrangedSubview(allowBtn)

        let addBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_add_project"), style: .standard, request: req) { [weak self] r in
            self?.closeSheet()
            self?.onAddProject?(r)
        }
        actions.addArrangedSubview(addBtn)

        let rejectBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_reject"), style: .standard, request: req) { [weak self] r in
            self?.closeSheet()
            self?.onReject?(r)
        }
        actions.addArrangedSubview(rejectBtn)

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        actions.addArrangedSubview(spacer)

        let detailsBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_details"), style: .plainLink, request: req) { [weak self] r in
            self?.closeSheet()
            self?.onDetails?(r)
        }
        actions.addArrangedSubview(detailsBtn)

        contentStack.addArrangedSubview(actions)
        actions.widthAnchor.constraint(equalTo: contentStack.widthAnchor).isActive = true

        card.stackView.addArrangedSubview(contentStack)
        contentStack.widthAnchor.constraint(equalTo: card.stackView.widthAnchor).isActive = true
        return card
    }

    @objc private func closeClicked() {
        closeSheet()
    }

    func closeSheet() {
        if let sheet = window, let parent = sheet.sheetParent {
            parent.endSheet(sheet)
        } else {
            window?.close()
        }
    }
}
