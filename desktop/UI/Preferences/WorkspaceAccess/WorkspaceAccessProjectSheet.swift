import AppKit
import Foundation

// The add / edit / remove-confirm sheet for a single workspace project. It
// owns folder picking, access selection and validation; the caller receives the
// result through closures and performs the actual service call.

enum WorkspaceProjectSheetMode {
    case add
    case edit(WorkspaceAccessEntryRow)
    case confirmRequest(WorkspaceAccessRequestRow)
}

final class WorkspaceAccessProjectSheetController: NSWindowController, NSOpenSavePanelDelegate {
    let mode: WorkspaceProjectSheetMode
    var onSave: ((String, String, String, String, String) -> Void)?
    var onRemove: (() -> Void)?

    private let pathLabel = WorkspaceProjectPathField()
    private let chooseButton = WorkspaceAccessStyledButton(title: "", style: .standard)
    private let nameField = WorkspaceAccessInputField()
    private var readOnlyOption: WorkspaceProjectAccessOptionRow!
    private var readWriteOption: WorkspaceProjectAccessOptionRow!
    private let cautionCard = NSView()
    private let executorPopup = NSPopUpButton()
    private let noteField = WorkspaceAccessInputField()
    private let saveButton = WorkspaceAccessStyledButton(title: "", style: .accent)
    private let cancelButton = WorkspaceAccessStyledButton(title: L10n.tr("preferences.workspace_access.sheet.btn_cancel"), style: .standard)
    private let removeButton = WorkspaceAccessStyledButton(title: "", style: .standard)

    private var currentPath: String = ""
    private var isWrite: Bool = false

    init(mode: WorkspaceProjectSheetMode) {
        self.mode = mode
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 500),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        panel.title = L10n.tr("preferences.workspace_access.title")
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

        let scrollView = NSScrollView()
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.drawsBackground = false
        scrollView.borderType = .noBorder
        scrollView.hasVerticalScroller = true
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.scrollerStyle = .overlay

        let documentView = WorkspaceProjectFormDocumentView()
        documentView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.documentView = documentView
        content.addSubview(scrollView)

        let footer = NSView()
        footer.translatesAutoresizingMaskIntoConstraints = false
        footer.wantsLayer = true
        footer.layer?.backgroundColor = Palette.windowBackground.cgColor
        content.addSubview(footer)

        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.distribution = .fill
        // Keep the form compact enough that the note section does not peek
        // underneath the fixed footer at the default sheet height.
        root.spacing = 4
        root.translatesAutoresizingMaskIntoConstraints = false
        documentView.addSubview(root)

        NSLayoutConstraint.activate([
            scrollView.topAnchor.constraint(equalTo: content.topAnchor),
            scrollView.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            scrollView.bottomAnchor.constraint(equalTo: footer.topAnchor),

            footer.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            footer.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            footer.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            footer.heightAnchor.constraint(equalToConstant: 60),

            documentView.widthAnchor.constraint(equalTo: scrollView.contentView.widthAnchor),
            root.topAnchor.constraint(equalTo: documentView.topAnchor, constant: 20),
            root.leadingAnchor.constraint(equalTo: documentView.leadingAnchor, constant: 20),
            root.trailingAnchor.constraint(equalTo: documentView.trailingAnchor, constant: -20),
            root.bottomAnchor.constraint(equalTo: documentView.bottomAnchor, constant: -20),
        ])

        // 1. Heading & Subheading
        let heading = NSTextField(labelWithString: "")
        heading.font = NSFont.systemFont(ofSize: 16, weight: .bold)
        heading.textColor = Palette.primaryText
        heading.translatesAutoresizingMaskIntoConstraints = false
        root.addArrangedSubview(heading)

        let subhead = NSTextField(wrappingLabelWithString: "")
        subhead.font = NSFont.systemFont(ofSize: 12, weight: .regular)
        subhead.textColor = Palette.secondaryText
        subhead.translatesAutoresizingMaskIntoConstraints = false
        root.addArrangedSubview(subhead)
        subhead.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        root.setCustomSpacing(12, after: subhead)

        switch mode {
        case .add:
            heading.stringValue = L10n.tr("preferences.workspace_access.sheet.add_title")
            subhead.stringValue = L10n.tr("preferences.workspace_access.sheet.add_sub")
        case .edit:
            heading.stringValue = L10n.tr("preferences.workspace_access.sheet.edit_title")
            subhead.stringValue = L10n.tr("preferences.workspace_access.sheet.edit_sub")
        case .confirmRequest(let req):
            heading.stringValue = L10n.tr("preferences.workspace_access.sheet.confirm_title")
            let accessName = req.isWrite
                ? L10n.tr("preferences.workspace_access.access.read_write")
                : L10n.tr("preferences.workspace_access.access.read_only")
            subhead.stringValue = L10n.tr("preferences.workspace_access.sheet.confirm_sub", req.requesterDisplayName, accessName)
        }

        // 2. Folder Section
        let folderLabel = makeSectionLabel(title: L10n.tr("preferences.workspace_access.sheet.folder_label"))
        root.addArrangedSubview(folderLabel)

        let folderRow = NSStackView()
        folderRow.orientation = .horizontal
        folderRow.alignment = .centerY
        folderRow.distribution = .fill
        folderRow.spacing = 8
        folderRow.translatesAutoresizingMaskIntoConstraints = false

        folderRow.addArrangedSubview(pathLabel)

        chooseButton.title = L10n.tr("preferences.workspace_access.sheet.choose_folder")
        chooseButton.toolTip = L10n.tr("preferences.workspace_access.sheet.choose_folder")
        chooseButton.target = self
        chooseButton.action = #selector(chooseFolderClicked)
        folderRow.addArrangedSubview(chooseButton)

        root.addArrangedSubview(folderRow)
        folderRow.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        folderRow.heightAnchor.constraint(equalToConstant: 30).isActive = true
        pathLabel.heightAnchor.constraint(equalToConstant: 30).isActive = true
        chooseButton.widthAnchor.constraint(equalToConstant: 80).isActive = true
        chooseButton.heightAnchor.constraint(equalToConstant: 28).isActive = true
        // The path field should consume the remaining row width instead of
        // keeping the intrinsic width of the empty-state placeholder.
        pathLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)
        pathLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        chooseButton.setContentHuggingPriority(.required, for: .horizontal)
        chooseButton.setContentCompressionResistancePriority(.required, for: .horizontal)
        root.setCustomSpacing(10, after: folderRow)

        // 3. Project Name Section
        let nameHeader = makeSectionLabel(
            title: L10n.tr("preferences.workspace_access.sheet.name_label"),
            hint: L10n.tr("preferences.workspace_access.sheet.name_hint")
        )
        root.addArrangedSubview(nameHeader)

        nameField.placeholderString = L10n.tr("preferences.workspace_access.sheet.name_placeholder")
        root.addArrangedSubview(nameField)
        nameField.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        nameField.heightAnchor.constraint(equalToConstant: 30).isActive = true
        root.setCustomSpacing(10, after: nameField)

        // 4. Access Level Section
        let accessHeader = makeSectionLabel(title: L10n.tr("preferences.workspace_access.sheet.access_label"))
        root.addArrangedSubview(accessHeader)

        let accessCard = CardView()
        accessCard.translatesAutoresizingMaskIntoConstraints = false

        readOnlyOption = WorkspaceProjectAccessOptionRow(
            description: L10n.tr("preferences.workspace_access.access.read_only_desc"),
            isWrite: false,
            isFirst: true
        )
        readWriteOption = WorkspaceProjectAccessOptionRow(
            description: L10n.tr("preferences.workspace_access.access.read_write_desc"),
            isWrite: true,
            isFirst: false
        )
        readOnlyOption.onSelect = { [weak self] in self?.setAccessMode(isWrite: false) }
        readWriteOption.onSelect = { [weak self] in self?.setAccessMode(isWrite: true) }

        accessCard.stackView.addArrangedSubview(readOnlyOption)
        accessCard.stackView.addArrangedSubview(readWriteOption)
        readOnlyOption.widthAnchor.constraint(equalTo: accessCard.stackView.widthAnchor).isActive = true
        readWriteOption.widthAnchor.constraint(equalTo: accessCard.stackView.widthAnchor).isActive = true
        root.addArrangedSubview(accessCard)
        accessCard.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        root.setCustomSpacing(10, after: accessCard)

        // Caution card for Read & Write
        cautionCard.translatesAutoresizingMaskIntoConstraints = false
        cautionCard.wantsLayer = true
        cautionCard.layer?.cornerRadius = 6
        cautionCard.layer?.backgroundColor = NSColor.dynamicColor(
            light: NSColor(hex: "#FEF3C7").withAlphaComponent(0.65),
            dark: NSColor(hex: "#3E2723").withAlphaComponent(0.55)
        ).cgColor

        let cautionLabel = NSTextField(wrappingLabelWithString: L10n.tr("preferences.workspace_access.access.read_write_caution"))
        cautionLabel.font = NSFont.systemFont(ofSize: 11)
        cautionLabel.textColor = Palette.primaryText
        cautionLabel.isEditable = false
        cautionLabel.isSelectable = false
        cautionLabel.isBordered = false
        cautionLabel.drawsBackground = false
        cautionLabel.translatesAutoresizingMaskIntoConstraints = false
        cautionCard.addSubview(cautionLabel)

        NSLayoutConstraint.activate([
            cautionLabel.topAnchor.constraint(equalTo: cautionCard.topAnchor, constant: 7),
            cautionLabel.leadingAnchor.constraint(equalTo: cautionCard.leadingAnchor, constant: 10),
            cautionLabel.trailingAnchor.constraint(equalTo: cautionCard.trailingAnchor, constant: -10),
            cautionLabel.bottomAnchor.constraint(equalTo: cautionCard.bottomAnchor, constant: -7)
        ])
        root.addArrangedSubview(cautionCard)
        cautionCard.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        root.setCustomSpacing(12, after: cautionCard)

        // 5. Default Agent Section
        let agentHeader = makeSectionLabel(
            title: L10n.tr("preferences.workspace_access.sheet.agent_label"),
            hint: L10n.tr("preferences.workspace_access.sheet.agent_hint")
        )
        root.addArrangedSubview(agentHeader)

        executorPopup.addItems(withTitles: [
            L10n.tr("preferences.workspace_access.sheet.agent_fluxion_default"),
            "Codex",
            "Claude Code",
            "Antigravity"
        ])
        executorPopup.controlSize = .regular
        executorPopup.translatesAutoresizingMaskIntoConstraints = false
        executorPopup.widthAnchor.constraint(equalToConstant: 240).isActive = true
        root.addArrangedSubview(executorPopup)
        root.setCustomSpacing(10, after: executorPopup)

        // 6. Note Section
        let noteHeader = makeSectionLabel(
            title: L10n.tr("preferences.workspace_access.sheet.note_label"),
            hint: L10n.tr("preferences.workspace_access.sheet.agent_hint")
        )
        root.addArrangedSubview(noteHeader)

        noteField.placeholderString = L10n.tr("preferences.workspace_access.sheet.note_placeholder")
        root.addArrangedSubview(noteField)
        noteField.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        noteField.heightAnchor.constraint(equalToConstant: 30).isActive = true
        root.setCustomSpacing(10, after: noteField)

        if case .confirmRequest = mode {
            let confirmNote = NSTextField(wrappingLabelWithString: L10n.tr("preferences.workspace_access.sheet.confirm_note"))
            confirmNote.font = NSFont.systemFont(ofSize: 11)
            confirmNote.textColor = Palette.secondaryText
            confirmNote.translatesAutoresizingMaskIntoConstraints = false
            root.addArrangedSubview(confirmNote)
            confirmNote.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true
        }

        // 7. Footer Buttons
        let footerSeparator = NSView()
        footerSeparator.translatesAutoresizingMaskIntoConstraints = false
        footerSeparator.wantsLayer = true
        footerSeparator.layer?.backgroundColor = Palette.separator.cgColor
        footer.addSubview(footerSeparator)

        let buttonRow = NSStackView()
        buttonRow.orientation = .horizontal
        buttonRow.alignment = .centerY
        buttonRow.spacing = 8
        buttonRow.translatesAutoresizingMaskIntoConstraints = false

        if case .edit = mode {
            removeButton.title = L10n.tr("preferences.workspace_access.sheet.btn_remove_project")
            removeButton.target = self
            removeButton.action = #selector(removeProjectClicked)
            buttonRow.addArrangedSubview(removeButton)
        }

        let buttonSpacer = NSView()
        buttonSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        buttonRow.addArrangedSubview(buttonSpacer)

        cancelButton.title = L10n.tr("preferences.workspace_access.sheet.btn_cancel")
        cancelButton.target = self
        cancelButton.action = #selector(cancelClicked)
        buttonRow.addArrangedSubview(cancelButton)

        saveButton.keyEquivalent = "\r"
        saveButton.target = self
        saveButton.action = #selector(saveClicked)
        switch mode {
        case .add:
            saveButton.title = L10n.tr("preferences.workspace_access.sheet.btn_add")
        case .edit:
            saveButton.title = L10n.tr("preferences.workspace_access.sheet.btn_save")
        case .confirmRequest:
            saveButton.title = L10n.tr("preferences.workspace_access.sheet.btn_allow_project")
        }
        buttonRow.addArrangedSubview(saveButton)

        footer.addSubview(buttonRow)
        NSLayoutConstraint.activate([
            footerSeparator.leadingAnchor.constraint(equalTo: footer.leadingAnchor),
            footerSeparator.trailingAnchor.constraint(equalTo: footer.trailingAnchor),
            footerSeparator.topAnchor.constraint(equalTo: footer.topAnchor),
            footerSeparator.heightAnchor.constraint(equalToConstant: 0.5),
            buttonRow.leadingAnchor.constraint(equalTo: footer.leadingAnchor, constant: 20),
            buttonRow.trailingAnchor.constraint(equalTo: footer.trailingAnchor, constant: -20),
            buttonRow.centerYAnchor.constraint(equalTo: footer.centerYAnchor),
        ])

        populateInitialValues()

        // AppKit may scroll the document to the first responder while the
        // sheet is being presented. Start every form at its visual top so the
        // title and folder section are not hidden on first open.
        DispatchQueue.main.async { [weak scrollView] in
            guard let scrollView = scrollView else { return }
            scrollView.contentView.scroll(to: .zero)
            scrollView.reflectScrolledClipView(scrollView.contentView)
            DispatchQueue.main.async { [weak scrollView] in
                guard let scrollView = scrollView else { return }
                scrollView.contentView.scroll(to: .zero)
                scrollView.reflectScrolledClipView(scrollView.contentView)
            }
        }

        // In a new project form, folder selection is the first meaningful
        // action. Do not place the caret in the project-name field before a
        // folder has been chosen.
        if case .add = mode {
            DispatchQueue.main.async { [weak self] in
                guard let self = self, self.currentPath.isEmpty else { return }
                self.window?.makeFirstResponder(self.chooseButton)
            }
        }
    }

    private func makeSectionLabel(title: String, hint: String? = nil) -> NSView {
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.alignment = .firstBaseline
        stack.spacing = 6
        stack.translatesAutoresizingMaskIntoConstraints = false

        let t = NSTextField(labelWithString: title)
        t.font = NSFont.systemFont(ofSize: 11.5, weight: .semibold)
        t.textColor = Palette.primaryText
        stack.addArrangedSubview(t)

        if let hint = hint {
            let h = NSTextField(labelWithString: hint)
            h.font = NSFont.systemFont(ofSize: 10.5, weight: .regular)
            h.textColor = Palette.secondaryText
            stack.addArrangedSubview(h)
        }
        return stack
    }

    private func populateInitialValues() {
        switch mode {
        case .add:
            setPath("")
            chooseButton.isHidden = false
            setAccessMode(isWrite: false)
            executorPopup.selectItem(at: 0)

        case .edit(let entry):
            setPath(entry.path)
            chooseButton.isHidden = true
            nameField.stringValue = entry.projectName
            setAccessMode(isWrite: entry.isWrite)
            selectExecutor(entry.defaultExecutor)
            noteField.stringValue = entry.description

        case .confirmRequest(let req):
            setPath(req.path)
            chooseButton.isHidden = true
            nameField.stringValue = req.projectName
            setAccessMode(isWrite: req.isWrite)
            selectExecutor(req.clientId)
        }
        saveButton.isEnabled = !currentPath.isEmpty
    }

    private func setPath(_ path: String) {
        currentPath = path
        pathLabel.stringValue = path.isEmpty
            ? L10n.tr("preferences.workspace_access.sheet.no_folder_selected")
            : path
        pathLabel.textColor = path.isEmpty ? Palette.secondaryText : Palette.primaryText
        pathLabel.toolTip = path.isEmpty ? nil : path
    }

    private func selectExecutor(_ name: String) {
        switch name.lowercased() {
        case "codex": executorPopup.selectItem(at: 1)
        case "claude", "claude code": executorPopup.selectItem(at: 2)
        case "antigravity": executorPopup.selectItem(at: 3)
        default: executorPopup.selectItem(at: 0)
        }
    }

    @objc private func chooseFolderClicked() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.delegate = self
        panel.beginSheetModal(for: window!) { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            self?.setPath(url.path)
            self?.saveButton.isEnabled = true
            if self?.nameField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == true {
                self?.nameField.stringValue = url.lastPathComponent
            }
            if let self = self {
                self.window?.makeFirstResponder(self.nameField)
            }
        }
    }

    private func setAccessMode(isWrite: Bool) {
        self.isWrite = isWrite
        readOnlyOption?.setSelected(!isWrite)
        readWriteOption?.setSelected(isWrite)
        cautionCard.isHidden = !isWrite
        window?.contentView?.layoutSubtreeIfNeeded()
    }

    @objc private func cancelClicked() {
        closeSheet()
    }

    @objc private func removeProjectClicked() {
        closeSheet()
        onRemove?()
    }

    @objc private func saveClicked() {
        let path = currentPath.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !path.isEmpty else {
            showError(L10n.tr("preferences.workspace_access.path_required"))
            return
        }
        let key = nameField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let access = isWrite ? "read-write" : "read-only"
        let executor: String
        switch executorPopup.indexOfSelectedItem {
        case 1: executor = "codex"
        case 2: executor = "claude"
        case 3: executor = "antigravity"
        default: executor = ""
        }
        let note = noteField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)

        saveButton.isEnabled = false
        onSave?(path, key, access, executor, note)
    }

    func showError(_ message: String) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = L10n.tr("preferences.workspace_access.error")
        alert.informativeText = message
        alert.addButton(withTitle: L10n.tr("preferences.ok"))
        alert.beginSheetModal(for: window!, completionHandler: nil)
        saveButton.isEnabled = true
    }

    func closeSheet() {
        if let sheet = window, let parent = sheet.sheetParent {
            parent.endSheet(sheet)
        } else {
            window?.close()
        }
    }
}
