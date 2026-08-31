import AppKit
import Foundation

// Everything that mutates workspace access: sheet presentation, add/edit/remove
// operations against the shared workspace service, request approval, and the
// error/success reporting they share. The AppDelegate bridge that actually runs
// the Python service lives at the bottom of this file.

extension PreferencesWindow {
    // MARK: - Actions & Operations

    @objc func addWorkspaceAccessClicked() {
        showWorkspaceAccessProjectEditor(mode: .add)
    }

    func showWorkspaceAccessProjectEditor(mode: WorkspaceProjectSheetMode) {
        let controller = WorkspaceAccessProjectSheetController(mode: mode)
        self.activeWorkspaceProjectEditor = controller

        controller.onSave = { [weak self, weak controller] path, key, access, defaultExec, desc in
            guard let self = self else { return }
            var arguments: [String]
            switch mode {
            case .add:
                arguments = ["add", "--path", path, "--key", key, "--access", access, "--default-executor", defaultExec, "--description", desc]
            case .edit(let entry):
                arguments = ["update", entry.id, "--path", path, "--key", key, "--access", access, "--default-executor", defaultExec, "--description", desc]
            case .confirmRequest(let request):
                arguments = [
                    "allow-project", request.requestId,
                    "--path", path,
                    "--mode", request.mode,
                    "--client-id", request.clientId,
                    "--access", access,
                    "--key", key,
                    "--default-executor", defaultExec,
                    "--description", desc,
                ]
            }

            self.appDelegate.runWorkspaceAccessCommand(arguments) { [weak self, weak controller] data, status in
                guard let self = self else { return }
                let payload = data.flatMap {
                    try? JSONSerialization.jsonObject(with: $0) as? [String: Any]
                }
                let commandSucceeded: Bool
                switch mode {
                case .confirmRequest:
                    // The CLI uses exit 0 for a well-formed business response,
                    // including mismatch/expired results. Check the operation
                    // flag instead of closing the sheet on those failures.
                    commandSucceeded = status == 0 && payload?["project_allowed"] as? Bool == true
                case .add, .edit:
                    commandSucceeded = status == 0
                }
                if commandSucceeded {
                    controller?.closeSheet()
                    self.activeWorkspaceProjectEditor = nil
                    self.reloadWorkspaceAccess()
                } else {
                    controller?.showError(
                        self.workspaceAccessErrorMessage(
                            from: data,
                            fallback: L10n.tr("preferences.workspace_access.save_failed")
                        )
                    )
                }
            }
        }

        controller.onRemove = { [weak self] in
            if case .edit(let entry) = mode {
                self?.showRemoveConfirmation(entry)
            }
        }

        guard let win = window, let sheet = controller.window else { return }
        win.beginSheet(sheet, completionHandler: nil)
    }

    func locateWorkspaceFolder(_ entry: WorkspaceAccessEntryRow) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: window!) { [weak self] response in
            guard response == .OK, let url = panel.url, let self = self else { return }
            let args = ["update", entry.id, "--path", url.path, "--key", entry.key, "--access", entry.access, "--default-executor", entry.defaultExecutor, "--description", entry.description]
            self.appDelegate.runWorkspaceAccessCommand(args) { [weak self] _, status in
                if status == 0 {
                    self?.reloadWorkspaceAccess()
                } else {
                    self?.showWorkspaceAccessError(L10n.tr("preferences.workspace_access.save_failed"))
                }
            }
        }
    }

    func showRemoveConfirmation(_ entry: WorkspaceAccessEntryRow) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = L10n.tr("preferences.workspace_access.remove.title", entry.projectName)
        alert.informativeText = "\(L10n.tr("preferences.workspace_access.remove.sub"))\n\n\(L10n.tr("preferences.workspace_access.remove.folder_label", entry.path))"
        alert.addButton(withTitle: L10n.tr("preferences.workspace_access.remove.btn_remove"))
        alert.addButton(withTitle: L10n.tr("preferences.workspace_access.sheet.btn_cancel"))

        guard let win = window else { return }
        alert.beginSheetModal(for: win) { [weak self] response in
            guard response == .alertFirstButtonReturn, let self = self else { return }
            self.appDelegate.runWorkspaceAccessCommand(["delete", entry.id]) { [weak self] data, status in
                guard let self = self else { return }
                if status == 0 {
                    self.reloadWorkspaceAccess()
                    if let data = data,
                       let result = try? JSONDecoder().decode(WorkspaceAccessDeleteResponse.self, from: data),
                       result.permissionStillEffective {
                        self.showStillEffectiveSheet(result: result)
                    }
                } else {
                    self.showWorkspaceAccessError(L10n.tr("preferences.workspace_access.save_failed"))
                }
            }
        }
    }

    private func showStillEffectiveSheet(result: WorkspaceAccessDeleteResponse) {
        let controller = WorkspaceAccessStillEffectiveSheetController(result: result)
        self.activeWorkspaceStillEffectiveSheet = controller
        guard let win = window, let sheet = controller.window else { return }
        win.beginSheet(sheet, completionHandler: nil)
    }

    func showWorkspaceRequestDetailsSheet(_ request: WorkspaceAccessRequestRow) {
        let controller = WorkspaceAccessRequestDetailsSheetController(request: request)
        self.activeWorkspaceRequestDetailsSheet = controller

        controller.onAllowOnce = { [weak self] in
            self?.approveWorkspaceAccessRequest(request.requestId, request: request)
        }
        controller.onAlwaysAllowProject = { [weak self] in
            self?.allowWorkspaceAccessAsProject(
                request.requestId,
                validationPath: request.path,
                validationMode: request.mode,
                validationClientId: request.clientId
            )
        }
        controller.onConfigureProject = { [weak self] in
            self?.showWorkspaceAccessProjectEditor(mode: .confirmRequest(request))
        }
        controller.onReject = { [weak self] in
            self?.denyWorkspaceAccessRequest(request.requestId)
        }

        guard let win = window, let sheet = controller.window else { return }
        win.beginSheet(sheet, completionHandler: nil)
    }

    func showWorkspaceAccessRequestDetails(_ requestId: String) {
        show()
        switchPage(to: "workspace-access")
        if let req = workspaceAccessRequests.first(where: { $0.requestId == requestId }) {
            showWorkspaceRequestDetailsSheet(req)
        } else {
            focusedWorkspaceAccessRequestId = requestId
            reloadWorkspaceAccess()
        }
    }

    func approveWorkspaceAccessRequest(
        _ requestId: String,
        request: WorkspaceAccessRequestRow? = nil,
        validationPath: String = "",
        validationMode: String = "",
        validationClientId: String = "",
        presentResult: Bool = false,
        completion: ((Bool, String) -> Void)? = nil
    ) {
        if presentResult {
            show()
            switchPage(to: "workspace-access")
        }
        var args = ["approve", requestId]
        if let r = request ?? workspaceAccessRequests.first(where: { $0.requestId == requestId }) {
            args += ["--path", r.path, "--mode", r.mode, "--client-id", r.clientId]
        } else if !validationPath.isEmpty,
                  !validationMode.isEmpty,
                  !validationClientId.isEmpty {
            args += [
                "--path", validationPath,
                "--mode", validationMode,
                "--client-id", validationClientId,
            ]
        }
        appDelegate.runWorkspaceAccessCommand(args) { [weak self] data, status in
            let payload = data.flatMap {
                try? JSONSerialization.jsonObject(with: $0) as? [String: Any]
            }
            if status == 0, payload?["approved"] as? Bool == true {
                self?.reloadWorkspaceAccess()
                if presentResult {
                    self?.showWorkspaceAccessSuccess(
                        L10n.tr("preferences.workspace_access.result.task_allowed")
                    )
                }
                completion?(true, "")
            } else {
                let message = self?.workspaceAccessErrorMessage(
                    from: data,
                    fallback: L10n.tr("preferences.workspace_access.save_failed")
                ) ?? L10n.tr("preferences.workspace_access.save_failed")
                if presentResult || self?.window?.isVisible == true {
                    self?.showWorkspaceAccessError(message)
                }
                completion?(false, message)
            }
        }
    }

    func allowWorkspaceAccessAsProject(
        _ requestId: String,
        validationPath: String,
        validationMode: String,
        validationClientId: String,
        presentResult: Bool = false,
        completion: ((Bool, String) -> Void)? = nil
    ) {
        if presentResult {
            show()
            switchPage(to: "workspace-access")
        }
        let args = [
            "allow-project", requestId,
            "--path", validationPath,
            "--mode", validationMode,
            "--client-id", validationClientId,
        ]
        appDelegate.runWorkspaceAccessCommand(args) { [weak self] data, status in
            let payload = data.flatMap {
                try? JSONSerialization.jsonObject(with: $0) as? [String: Any]
            }
            guard status == 0, payload?["project_allowed"] as? Bool == true else {
                let message = self?.workspaceAccessErrorMessage(
                    from: data,
                    fallback: L10n.tr("preferences.workspace_access.save_failed")
                ) ?? L10n.tr("preferences.workspace_access.save_failed")
                if presentResult || self?.window?.isVisible == true {
                    self?.showWorkspaceAccessError(message)
                }
                completion?(false, message)
                return
            }
            self?.reloadWorkspaceAccess()
            if presentResult {
                self?.showWorkspaceAccessSuccess(
                    L10n.tr("preferences.workspace_access.result.project_allowed")
                )
            }
            completion?(true, "")
        }
    }

    func denyWorkspaceAccessRequest(_ requestId: String) {
        appDelegate.runWorkspaceAccessCommand(["deny", requestId]) { [weak self] _, status in
            if status == 0 {
                self?.reloadWorkspaceAccess()
            } else {
                self?.showWorkspaceAccessError(L10n.tr("preferences.workspace_access.save_failed"))
            }
        }
    }

    private func showWorkspaceAccessError(_ message: String) {
        guard let win = window else { return }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = L10n.tr("preferences.workspace_access.error")
        alert.informativeText = message
        alert.addButton(withTitle: L10n.tr("preferences.ok"))
        alert.beginSheetModal(for: win, completionHandler: nil)
    }

    private func showWorkspaceAccessSuccess(_ message: String) {
        guard let win = window else { return }
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = L10n.tr("preferences.workspace_access.result.title")
        alert.informativeText = message
        alert.addButton(withTitle: L10n.tr("preferences.ok"))
        alert.beginSheetModal(for: win, completionHandler: nil)
    }

    private func workspaceAccessErrorMessage(from data: Data?, fallback: String) -> String {
        guard let data,
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let error = payload["error"] as? String,
              !error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return fallback
        }
        return error
    }
}

// MARK: - AppDelegate command bridge

extension AppDelegate {
    /// Execute the shared Python workspace service. The desktop never parses
    /// or rewrites permission files itself, so path canonicalization and write
    /// validation stay identical for CLI, Web, and MCP callers.
    func runWorkspaceAccessCommand(
        _ arguments: [String],
        completion: @escaping (Data?, Int32) -> Void
    ) {
        let pythonBin = (repoPath as NSString).appendingPathComponent(".venv/bin/python")
        guard FileManager.default.fileExists(atPath: pythonBin) else {
            DispatchQueue.main.async { completion(nil, 127) }
            return
        }
        let task = Process()
        let output = Pipe()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        task.arguments = [pythonBin, "-m", "fluxion.workspace_access_cli", "--json"] + arguments
        task.currentDirectoryURL = URL(fileURLWithPath: repoPath)
        var env = ProcessInfo.processInfo.environment
        env["FLUXION_ENV_FILE"] = envPath
        task.environment = env
        task.standardOutput = output
        task.standardError = output
        task.terminationHandler = { proc in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            DispatchQueue.main.async {
                completion(data, proc.terminationStatus)
            }
        }
        do {
            try task.run()
        } catch {
            DispatchQueue.main.async { completion(nil, 127) }
        }
    }
}
