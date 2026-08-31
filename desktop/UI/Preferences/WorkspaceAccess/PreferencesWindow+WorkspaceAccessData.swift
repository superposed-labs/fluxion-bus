import AppKit
import Foundation

// Loading, filtering and presenting workspace entries: the reload cycle that
// calls the shared workspace service, the filter/count bookkeeping, and the
// NSTableView data source and delegate that turn entries into rows.

extension PreferencesWindow: NSTableViewDataSource, NSTableViewDelegate {
    // MARK: - Data Reload & Filter

    func reloadWorkspaceAccess() {
        guard workspaceAccessTable != nil else { return }
        workspaceAccessDefaultPath = nil
        workspaceAccessPhase = .loading
        updateUIState()

        appDelegate.runWorkspaceAccessCommand(["list"]) { [weak self] data, status in
            guard let self = self else { return }
            guard status == 0, let data = data,
                  let decoded = try? JSONDecoder().decode(WorkspaceAccessListResponse.self, from: data) else {
                self.workspaceAccessPhase = .failed(L10n.tr("preferences.workspace_access.failed.desc"))
                self.updateUIState()
                return
            }
            let defaultEntry = decoded.workspaces.first(where: { $0.isSystemDefault })
            let defaultPath = decoded.runtimeContext?.workspaceRoot ?? defaultEntry?.path
            let hasProjectEntryForDefault = decoded.workspaces.contains { entry in
                !entry.isAutoPing && !entry.isSystemDefault && entry.path == defaultPath
            }
            self.workspaceAccessDefaultPath = hasProjectEntryForDefault ? nil : defaultPath
            self.workspaceAccessEntries = decoded.workspaces.filter {
                !$0.isAutoPing && !$0.isSystemDefault
            }
            self.workspaceAccessRequests = decoded.pendingRequests.filter { $0.status == "pending" }
            self.workspaceAccessPhase = .ready
            self.updateUIState()

            if let focusedId = self.focusedWorkspaceAccessRequestId,
               let req = self.workspaceAccessRequests.first(where: { $0.requestId == focusedId }) {
                self.focusedWorkspaceAccessRequestId = nil
                self.showWorkspaceRequestDetailsSheet(req)
            }
        }
    }

    private func filteredWorkspaceEntries() -> [WorkspaceAccessEntryRow] {
        let query = workspaceAccessSearchQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let filter = workspaceAccessFilter

        return workspaceAccessEntries.filter { entry in
            // Filter match
            switch filter {
            case .readOnly:
                if entry.isWrite { return false }
            case .readWrite:
                if !entry.isWrite { return false }
            case .issues:
                if !entry.hasIssue { return false }
            case .all:
                break
            }

            // Search query match
            if !query.isEmpty {
                let text = "\(entry.projectName) \(entry.path) \(entry.description)".lowercased()
                if !text.contains(query) { return false }
            }
            return true
        }
    }

    private func updateUIState() {
        // 1. Pending Requests Banner
        renderPendingRequests()

        // 2. Keep system defaults out of the project count and project table.
        renderSystemAccess()

        // 3. Metadata Count & Segmented State
        let allCount = workspaceAccessEntries.count
        let writeCount = workspaceAccessEntries.filter { $0.isWrite }.count
        let issueCount = workspaceAccessEntries.filter { $0.hasIssue }.count
        let filtered = filteredWorkspaceEntries()
        let isFiltered = !workspaceAccessSearchQuery.isEmpty || workspaceAccessFilter != .all

        switch workspaceAccessPhase {
        case .loading:
            setWorkspaceAccessCountText(L10n.tr("preferences.workspace_access.count.loading"))
            workspaceAccessFilterSegmented?.isEnabled = false
            workspaceAccessTableScrollView?.hasVerticalScroller = false
            workspaceAccessTableScrollView?.verticalScroller?.isHidden = true
            workspaceAccessTableScrollView?.isHidden = false
            workspaceAccessEmptyCard?.isHidden = true
            workspaceAccessNoResultsCard?.isHidden = true
            workspaceAccessErrorCard?.isHidden = true

        case .failed:
            setWorkspaceAccessCountText(L10n.tr("preferences.workspace_access.count.failed"))
            workspaceAccessFilterSegmented?.isEnabled = false
            workspaceAccessTableScrollView?.hasVerticalScroller = false
            workspaceAccessTableScrollView?.verticalScroller?.isHidden = true
            workspaceAccessTableScrollView?.isHidden = true
            workspaceAccessEmptyCard?.isHidden = true
            workspaceAccessNoResultsCard?.isHidden = true
            workspaceAccessErrorCard?.isHidden = false

        case .ready:
            workspaceAccessFilterSegmented?.isEnabled = true
            workspaceAccessErrorCard?.isHidden = true

            if allCount == 0 {
                setWorkspaceAccessCountText(L10n.tr("preferences.workspace_access.count.unfiltered", 0, 0))
                workspaceAccessTableScrollView?.isHidden = true
                workspaceAccessEmptyCard?.isHidden = false
                workspaceAccessNoResultsCard?.isHidden = true
            } else if filtered.isEmpty {
                setWorkspaceAccessCountText(L10n.tr("preferences.workspace_access.count.filtered", 0, allCount))
                workspaceAccessTableScrollView?.isHidden = true
                workspaceAccessEmptyCard?.isHidden = true
                workspaceAccessNoResultsCard?.isHidden = false
                if !workspaceAccessSearchQuery.isEmpty {
                    workspaceAccessNoResultsLabel?.stringValue = L10n.tr("preferences.workspace_access.no_results.query", workspaceAccessSearchQuery)
                } else {
                    workspaceAccessNoResultsLabel?.stringValue = L10n.tr("preferences.workspace_access.no_results.filter")
                }
            } else {
                if isFiltered {
                    setWorkspaceAccessCountText(L10n.tr("preferences.workspace_access.count.filtered", filtered.count, allCount))
                } else {
                    var base = L10n.tr("preferences.workspace_access.count.unfiltered", allCount, writeCount)
                    if issueCount > 0 {
                        base += L10n.tr("preferences.workspace_access.count.issues", issueCount)
                    }
                    setWorkspaceAccessCountText(base)
                }
                workspaceAccessTableScrollView?.isHidden = false
                workspaceAccessEmptyCard?.isHidden = true
                workspaceAccessNoResultsCard?.isHidden = true
                let usesInnerTableScroll = filtered.count > 7
                workspaceAccessTableScrollView?.hasVerticalScroller = usesInnerTableScroll
                workspaceAccessTableScrollView?.autohidesScrollers = usesInnerTableScroll
                workspaceAccessTableScrollView?.verticalScroller?.isHidden = !usesInnerTableScroll

                let visibleRows = min(max(filtered.count, 3), 7)
                var desiredHeight = CGFloat(visibleRows) * WorkspaceAccessLayout.rowHeight
                if !usesInnerTableScroll {
                    desiredHeight += WorkspaceAccessLayout.fitContentInset
                }
                if let expandedId = workspaceAccessExpandedId,
                   let expanded = filtered.first(where: { $0.id == expandedId }) {
                    desiredHeight += workspaceAccessRowHeight(for: expanded) - WorkspaceAccessLayout.rowHeight
                }
                // Small and medium lists expand with the page, leaving the
                // outer preferences scroller in charge. Large lists keep a
                // bounded internal viewport instead of growing indefinitely.
                workspaceAccessTableHeightConstraint?.constant = min(desiredHeight, WorkspaceAccessLayout.tableMaxHeight)
                workspaceAccessTable?.reloadData()
            }
        }
    }

    private func setWorkspaceAccessCountText(_ text: String) {
        guard let label = workspaceAccessCountLabel else { return }

        let regularFont = NSFont.systemFont(ofSize: 11.5, weight: .regular)
        let emphasizedFont = NSFont.systemFont(ofSize: 11.5, weight: .semibold)
        let attributed = NSMutableAttributedString(
            string: text,
            attributes: [
                .font: regularFont,
                .foregroundColor: Palette.secondaryText
            ]
        )

        if let numberRegex = try? NSRegularExpression(pattern: "\\d+") {
            let fullRange = NSRange(location: 0, length: (text as NSString).length)
            for match in numberRegex.matches(in: text, range: fullRange) {
                attributed.addAttributes(
                    [
                        .font: emphasizedFont,
                        .foregroundColor: Palette.primaryText
                    ],
                    range: match.range
                )
            }
        }

        label.attributedStringValue = attributed
    }

    func filterWorkspaceAccessRows() {
        workspaceAccessSearchQuery = workspaceAccessSearchField?.stringValue ?? ""
        updateUIState()
    }

    @objc func workspaceFilterSegmentChanged(_ sender: NSSegmentedControl) {
        switch sender.selectedSegment {
        case 1: workspaceAccessFilter = .readOnly
        case 2: workspaceAccessFilter = .readWrite
        case 3: workspaceAccessFilter = .issues
        default: workspaceAccessFilter = .all
        }
        updateUIState()
    }

    @objc func clearSearchAndFilterClicked() {
        workspaceAccessSearchQuery = ""
        workspaceAccessSearchField?.stringValue = ""
        workspaceAccessFilter = .all
        workspaceAccessFilterSegmented?.selectedSegment = 0
        updateUIState()
    }

    @objc func retryLoadClicked() {
        reloadWorkspaceAccess()
    }

    @objc func openAgentsUsageClicked() {
        switchPage(to: "agents")
    }

    // MARK: - NSTableView DataSource & Delegate

    func numberOfRows(in tableView: NSTableView) -> Int {
        return tableView == workspaceAccessTable ? filteredWorkspaceEntries().count : 0
    }

    func tableView(_ tableView: NSTableView, heightOfRow row: Int) -> CGFloat {
        guard tableView == workspaceAccessTable else { return WorkspaceAccessLayout.rowHeight }
        let filtered = filteredWorkspaceEntries()
        guard row >= 0, row < filtered.count else { return WorkspaceAccessLayout.rowHeight }
        return workspaceAccessRowHeight(for: filtered[row])
    }

    private func workspaceAccessRowHeight(for entry: WorkspaceAccessEntryRow) -> CGFloat {
        guard entry.id == workspaceAccessExpandedId else { return WorkspaceAccessLayout.rowHeight }
        var height: CGFloat = WorkspaceAccessLayout.rowHeight + 108
        if entry.hasIssue || !entry.isManaged {
            height += 36
        }
        if workspaceAccessShowTechDetailsIds.contains(entry.id) {
            height += 92
        }
        return height
    }

    /// Keep the complete expanded row in view after AppKit has recalculated its
    /// height. A plain scrollRowToVisible() only guarantees that the row's
    /// original header is visible, which leaves disclosure details underneath
    /// the window footer for rows near the bottom of the list.
    private func ensureExpandedWorkspaceRowIsVisible(entryId: String) {
        guard let table = workspaceAccessTable,
              let tableScroll = workspaceAccessTableScrollView,
              let row = filteredWorkspaceEntries().firstIndex(where: { $0.id == entryId })
        else { return }

        window?.contentView?.layoutSubtreeIfNeeded()
        tableScroll.layoutSubtreeIfNeeded()
        table.noteHeightOfRows(withIndexesChanged: IndexSet(integer: row))
        table.layoutSubtreeIfNeeded()

        let padding: CGFloat = 14
        let rowRect = table.rect(ofRow: row)
        let rowRectWithPadding = rowRect.insetBy(dx: 0, dy: -padding)

        // Let AppKit translate the table's coordinates into the inner clip
        // view's coordinates. This keeps the behavior correct for flipped
        // views and for both overlay and legacy scrollers, while scrolling by
        // the smallest amount needed to reveal the complete expanded row.
        table.scrollToVisible(rowRectWithPadding)

        // For short and medium lists the table grows with the page, so the
        // outer preferences scroller may also need to move. Use AppKit's
        // rectangle-aware scrolling to preserve the smallest possible jump and
        // keep a little breathing room around the expanded content.
        if let documentView = settingsScrollView?.documentView {
            let rowInDocument = table.convert(rowRectWithPadding, to: documentView)
            documentView.scrollToVisible(rowInDocument)
        }
    }

    private func scheduleExpandedWorkspaceRowVisibility(for entryId: String) {
        // The table height and its document view are updated during the current
        // layout pass. Waiting one main-queue turn ensures rect(ofRow:) sees the
        // expanded height instead of the collapsed header height.
        DispatchQueue.main.async { [weak self] in
            guard let self = self,
                  self.workspaceAccessExpandedId == entryId
            else { return }
            self.ensureExpandedWorkspaceRowIsVisible(entryId: entryId)
        }
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard tableView == workspaceAccessTable else { return nil }
        let filtered = filteredWorkspaceEntries()
        guard row >= 0, row < filtered.count else { return nil }
        let entry = filtered[row]

        let cellId = NSUserInterfaceItemIdentifier("WorkspaceTableRowCell")
        let cell: WorkspaceTableRowCell
        if let reused = tableView.makeView(withIdentifier: cellId, owner: self) as? WorkspaceTableRowCell {
            cell = reused
        } else {
            cell = WorkspaceTableRowCell()
            cell.identifier = cellId
        }

        let isExpanded = (entry.id == workspaceAccessExpandedId)
        let showTech = workspaceAccessShowTechDetailsIds.contains(entry.id)

        cell.configure(
            entry: entry,
            isExpanded: isExpanded,
            showTechDetails: showTech
        )

        cell.onToggleExpand = { [weak self] in
            guard let self = self else { return }
            if self.workspaceAccessExpandedId == entry.id {
                self.workspaceAccessExpandedId = nil
            } else {
                self.workspaceAccessExpandedId = entry.id
            }
            self.updateUIState()
            if self.workspaceAccessExpandedId == entry.id {
                self.scheduleExpandedWorkspaceRowVisibility(for: entry.id)
            }
        }

        cell.onEdit = { [weak self] in
            self?.showWorkspaceAccessProjectEditor(mode: .edit(entry))
        }

        cell.onLocate = { [weak self] in
            self?.locateWorkspaceFolder(entry)
        }

        cell.onReveal = {
            NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: entry.path)
        }

        cell.onRemove = { [weak self] in
            self?.showRemoveConfirmation(entry)
        }

        cell.onToggleTechDetails = { [weak self] in
            guard let self = self else { return }
            if self.workspaceAccessShowTechDetailsIds.contains(entry.id) {
                self.workspaceAccessShowTechDetailsIds.remove(entry.id)
            } else {
                self.workspaceAccessShowTechDetailsIds.insert(entry.id)
            }
            self.updateUIState()
            if self.workspaceAccessExpandedId == entry.id {
                self.scheduleExpandedWorkspaceRowVisibility(for: entry.id)
            }
        }

        return cell
    }
}
