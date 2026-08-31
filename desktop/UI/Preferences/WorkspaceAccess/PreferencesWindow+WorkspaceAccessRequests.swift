import AppKit
import Foundation

// Rendering of the pending access-request card above the table: the single
// request layout, the compact multi-request items, and the overflow path that
// moves the list into its own sheet.

extension PreferencesWindow {
    // MARK: - Pending Requests Render

    func renderPendingRequests() {
        guard let stack = workspaceAccessRequestsStack, let card = workspaceAccessRequestsCard else { return }
        for view in stack.arrangedSubviews {
            stack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }

        let pending = workspaceAccessRequests.filter { $0.status == "pending" }
        if pending.isEmpty {
            card.isHidden = true
            return
        }
        card.isHidden = false

        if pending.count == 1 {
            let req = pending[0]
            stack.addArrangedSubview(makeSingleRequestView(req))
        } else {
            // Header
            let head = NSStackView()
            head.orientation = .horizontal
            head.alignment = .centerY
            head.spacing = 8
            head.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)
            head.translatesAutoresizingMaskIntoConstraints = false

            let pill = WorkspacePillView(text: L10n.tr("preferences.workspace_access.requests.pending_count_pill", pending.count), isWarning: true)
            head.addArrangedSubview(pill)

            let title = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.requests.title"))
            title.font = NSFont.systemFont(ofSize: 12.5, weight: .bold)
            title.textColor = Palette.primaryText
            head.addArrangedSubview(title)

            let spacer = NSView()
            spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
            head.addArrangedSubview(spacer)

            let hint = NSTextField(labelWithString: L10n.tr("preferences.workspace_access.requests.hint_expires"))
            hint.font = NSFont.systemFont(ofSize: 11)
            hint.textColor = Palette.secondaryText
            head.addArrangedSubview(hint)

            stack.addArrangedSubview(head)
            head.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true

            // Inline items (at most 3)
            for req in pending.prefix(3) {
                let sep = NSView()
                sep.translatesAutoresizingMaskIntoConstraints = false
                sep.wantsLayer = true
                sep.layer?.backgroundColor = Palette.separator.cgColor
                stack.addArrangedSubview(sep)
                sep.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
                sep.heightAnchor.constraint(equalToConstant: 0.5).isActive = true

                let itemView = makeMultiRequestItemView(req)
                stack.addArrangedSubview(itemView)
                itemView.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
            }

            if pending.count > 3 {
                let sep = NSView()
                sep.translatesAutoresizingMaskIntoConstraints = false
                sep.wantsLayer = true
                sep.layer?.backgroundColor = Palette.separator.cgColor
                stack.addArrangedSubview(sep)
                sep.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
                sep.heightAnchor.constraint(equalToConstant: 0.5).isActive = true

                let moreBtn = NSButton(
                    title: L10n.tr("preferences.workspace_access.requests.btn_show_more", pending.count - 3),
                    target: self,
                    action: #selector(showAllPendingRequestsClicked)
                )
                moreBtn.isBordered = false
                moreBtn.alignment = .left
                moreBtn.font = NSFont.systemFont(ofSize: 11.5, weight: .medium)
                moreBtn.contentTintColor = NSColor.controlAccentColor
                moreBtn.translatesAutoresizingMaskIntoConstraints = false

                let moreContainer = NSView()
                moreContainer.translatesAutoresizingMaskIntoConstraints = false
                moreContainer.addSubview(moreBtn)
                NSLayoutConstraint.activate([
                    moreContainer.heightAnchor.constraint(equalToConstant: 32),
                    moreBtn.leadingAnchor.constraint(equalTo: moreContainer.leadingAnchor, constant: 12),
                    moreBtn.centerYAnchor.constraint(equalTo: moreContainer.centerYAnchor)
                ])
                stack.addArrangedSubview(moreContainer)
                moreContainer.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
            }
        }
    }

    private func makeSingleRequestView(_ req: WorkspaceAccessRequestRow) -> NSView {
        let view = NSStackView()
        view.orientation = .vertical
        view.alignment = .leading
        view.spacing = 0
        view.edgeInsets = NSEdgeInsets(top: 10, left: 12, bottom: 12, right: 12)
        view.translatesAutoresizingMaskIntoConstraints = false

        let top = NSStackView()
        top.orientation = .horizontal
        top.alignment = .centerY
        top.spacing = 8
        top.translatesAutoresizingMaskIntoConstraints = false

        let pill = WorkspacePillView(text: L10n.tr("preferences.workspace_access.requests.pending_pill"), isWarning: true)
        top.addArrangedSubview(pill)

        let name = NSTextField(labelWithString: req.projectName)
        name.font = NSFont.systemFont(ofSize: 12.5, weight: .semibold)
        name.textColor = Palette.primaryText
        top.addArrangedSubview(name)

        let badge = WorkspaceAccessBadgeView(isWrite: req.isWrite)
        top.addArrangedSubview(badge)

        view.addArrangedSubview(top)
        view.setCustomSpacing(3, after: top)

        let path = NSTextField(labelWithString: req.path)
        path.font = NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular)
        path.textColor = Palette.secondaryText.withAlphaComponent(0.85)
        path.lineBreakMode = .byTruncatingMiddle
        view.addArrangedSubview(path)
        view.setCustomSpacing(5, after: path)

        let meta = makeWorkspaceRequestMetaLabel(req: req)
        view.addArrangedSubview(meta)
        view.setCustomSpacing(8, after: meta)

        let bulletsContainer = NSView()
        bulletsContainer.translatesAutoresizingMaskIntoConstraints = false

        let bullets = NSStackView()
        bullets.orientation = .vertical
        bullets.alignment = .leading
        bullets.spacing = 2
        bullets.translatesAutoresizingMaskIntoConstraints = false

        for key in ["bullet1", "bullet2", "bullet3", "bullet4"] {
            let item = NSTextField(wrappingLabelWithString: "•  \(L10n.tr("preferences.workspace_access.requests.\(key)"))")
            item.font = NSFont.systemFont(ofSize: 10.5, weight: .regular)
            item.textColor = Palette.secondaryText
            item.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            bullets.addArrangedSubview(item)
        }
        bulletsContainer.addSubview(bullets)

        NSLayoutConstraint.activate([
            bullets.topAnchor.constraint(equalTo: bulletsContainer.topAnchor),
            bullets.bottomAnchor.constraint(equalTo: bulletsContainer.bottomAnchor),
            bullets.leadingAnchor.constraint(equalTo: bulletsContainer.leadingAnchor, constant: 14),
            bullets.trailingAnchor.constraint(equalTo: bulletsContainer.trailingAnchor),
        ])

        view.addArrangedSubview(bulletsContainer)
        bulletsContainer.widthAnchor.constraint(equalTo: view.widthAnchor, constant: -24).isActive = true
        view.setCustomSpacing(10, after: bulletsContainer)

        let actions = NSStackView()
        actions.orientation = .horizontal
        actions.alignment = .centerY
        actions.spacing = 7
        actions.translatesAutoresizingMaskIntoConstraints = false

        let allowBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_allow_once"), style: .accent, request: req) { [weak self] r in
            self?.approveWorkspaceAccessRequest(r.requestId, request: r)
        }
        allowBtn.keyEquivalent = "\r"
        actions.addArrangedSubview(allowBtn)

        let addBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_add_project"), style: .standard, request: req) { [weak self] r in
            self?.allowWorkspaceAccessAsProject(
                r.requestId,
                validationPath: r.path,
                validationMode: r.mode,
                validationClientId: r.clientId
            )
        }
        actions.addArrangedSubview(addBtn)

        let rejectBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_reject"), style: .standard, request: req) { [weak self] r in
            self?.denyWorkspaceAccessRequest(r.requestId)
        }
        actions.addArrangedSubview(rejectBtn)

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        actions.addArrangedSubview(spacer)

        let detailsBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_details"), style: .plainLink, request: req) { [weak self] r in
            self?.showWorkspaceRequestDetailsSheet(r)
        }
        actions.addArrangedSubview(detailsBtn)

        view.addArrangedSubview(actions)
        actions.widthAnchor.constraint(equalTo: view.widthAnchor, constant: -24).isActive = true
        return view
    }

    private func makeMultiRequestItemView(_ req: WorkspaceAccessRequestRow) -> NSView {
        let view = NSStackView()
        view.orientation = .vertical
        view.alignment = .leading
        view.spacing = 0
        view.edgeInsets = NSEdgeInsets(top: 9, left: 12, bottom: 9, right: 12)
        view.translatesAutoresizingMaskIntoConstraints = false

        let top = NSStackView()
        top.orientation = .horizontal
        top.alignment = .centerY
        top.spacing = 8
        top.translatesAutoresizingMaskIntoConstraints = false

        let name = NSTextField(labelWithString: req.projectName)
        name.font = NSFont.systemFont(ofSize: 12.5, weight: .semibold)
        name.textColor = Palette.primaryText
        top.addArrangedSubview(name)

        let badge = WorkspaceAccessBadgeView(isWrite: req.isWrite)
        top.addArrangedSubview(badge)

        view.addArrangedSubview(top)
        view.setCustomSpacing(3, after: top)

        let path = NSTextField(labelWithString: req.path)
        path.font = NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular)
        path.textColor = Palette.secondaryText.withAlphaComponent(0.85)
        path.lineBreakMode = .byTruncatingMiddle
        view.addArrangedSubview(path)
        view.setCustomSpacing(4, after: path)

        let meta = makeWorkspaceRequestMetaLabel(req: req)
        view.addArrangedSubview(meta)
        view.setCustomSpacing(6, after: meta)

        let summary = NSTextField(wrappingLabelWithString: L10n.tr("preferences.workspace_access.requests.allow_once_summary"))
        summary.font = NSFont.systemFont(ofSize: 10.5)
        summary.textColor = Palette.secondaryText
        view.addArrangedSubview(summary)
        summary.widthAnchor.constraint(equalTo: view.widthAnchor, constant: -24).isActive = true
        view.setCustomSpacing(8, after: summary)

        let actions = NSStackView()
        actions.orientation = .horizontal
        actions.alignment = .centerY
        actions.spacing = 7
        actions.translatesAutoresizingMaskIntoConstraints = false

        let allowBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_allow_once"), style: .accent, request: req) { [weak self] r in
            self?.approveWorkspaceAccessRequest(r.requestId, request: r)
        }
        actions.addArrangedSubview(allowBtn)

        let addBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_add_project"), style: .standard, request: req) { [weak self] r in
            self?.allowWorkspaceAccessAsProject(
                r.requestId,
                validationPath: r.path,
                validationMode: r.mode,
                validationClientId: r.clientId
            )
        }
        actions.addArrangedSubview(addBtn)

        let rejectBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_reject"), style: .standard, request: req) { [weak self] r in
            self?.denyWorkspaceAccessRequest(r.requestId)
        }
        actions.addArrangedSubview(rejectBtn)

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        actions.addArrangedSubview(spacer)

        let detailsBtn = WorkspaceRequestActionButton(title: L10n.tr("preferences.workspace_access.requests.btn_details"), style: .plainLink, request: req) { [weak self] r in
            self?.showWorkspaceRequestDetailsSheet(r)
        }
        actions.addArrangedSubview(detailsBtn)

        view.addArrangedSubview(actions)
        actions.widthAnchor.constraint(equalTo: view.widthAnchor, constant: -24).isActive = true
        return view
    }

    @objc private func showAllPendingRequestsClicked() {
        let controller = WorkspaceAccessAllRequestsSheetController(requests: workspaceAccessRequests)
        controller.onAllowOnce = { [weak self] req in
            self?.approveWorkspaceAccessRequest(req.requestId, request: req)
        }
        controller.onAddProject = { [weak self] req in
            self?.allowWorkspaceAccessAsProject(
                req.requestId,
                validationPath: req.path,
                validationMode: req.mode,
                validationClientId: req.clientId
            )
        }
        controller.onReject = { [weak self] req in
            self?.denyWorkspaceAccessRequest(req.requestId)
        }
        controller.onDetails = { [weak self] req in
            self?.showWorkspaceRequestDetailsSheet(req)
        }
        self.activeWorkspaceAccessAllRequestsSheet = controller
        guard let win = window, let sheet = controller.window else { return }
        win.beginSheet(sheet, completionHandler: nil)
    }
}
