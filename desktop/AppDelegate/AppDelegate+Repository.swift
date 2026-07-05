import AppKit
import Foundation

// MARK: - Repository Resolution & Selection
//
// Split out of main.swift. Locates the Fluxion source checkout (env var →
// saved config → bundled candidate → user picker) and persists the choice.
// `resolveRepositoryPath`, `saveRepositoryPath`, and `showSetupWarningIfNeeded`
// were `private` originally; they are `internal` here because they are now
// reached from main.swift (applicationDidFinishLaunching) and across files.
extension AppDelegate {

    var managedInstallPath: String {
        return NSString(string: "~/.local/share/fluxion").expandingTildeInPath
    }

    func isValidRepository(_ path: String) -> Bool {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: path, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            return false
        }
        let pyproject = (path as NSString).appendingPathComponent("pyproject.toml")
        let package = (path as NSString).appendingPathComponent("src/fluxion")
        return FileManager.default.fileExists(atPath: pyproject)
            && FileManager.default.fileExists(atPath: package, isDirectory: &isDirectory)
            && isDirectory.boolValue
    }

    func chooseRepository() -> String? {
        let panel = NSOpenPanel()
        panel.title = L10n.tr("repository.choose.title")
        panel.message = L10n.tr("repository.choose.message")
        panel.prompt = L10n.tr("repository.choose.prompt")
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = false

        while panel.runModal() == .OK {
            guard let path = panel.url?.standardizedFileURL.path else {
                return nil
            }
            if isValidRepository(path) {
                saveRepositoryPath(path)
                return path
            }
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = L10n.tr("repository.invalid.title")
            alert.informativeText = L10n.tr("repository.invalid.message")
            alert.addButton(withTitle: L10n.tr("repository.choose_again"))
            alert.addButton(withTitle: L10n.tr("repository.cancel"))
            if alert.runModal() != .alertFirstButtonReturn {
                return nil
            }
        }
        return nil
    }

    func resolveRepositoryPath() -> Bool {
        let environmentPath = ProcessInfo.processInfo.environment["FLUXION_REPO_PATH"]?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let environmentPath = environmentPath, !environmentPath.isEmpty {
            let resolved = NSString(string: environmentPath).expandingTildeInPath
            if isValidRepository(resolved) {
                repoPath = resolved
                return true
            }
            NSLog("FluxionMenu: FLUXION_REPO_PATH is not a valid Fluxion repository: %@", resolved)
        }

        if let data = try? Data(contentsOf: desktopConfigPath),
           let config = try? JSONDecoder().decode(DesktopConfig.self, from: data),
           isValidRepository(config.repositoryPath) {
            repoPath = config.repositoryPath
            return true
        }

        if isValidRepository(bundledRepoCandidate) {
            repoPath = bundledRepoCandidate
            saveRepositoryPath(repoPath)
            return true
        }

        if isValidRepository(managedInstallPath) {
            repoPath = managedInstallPath
            saveRepositoryPath(repoPath)
            return true
        }

        repoPath = managedInstallPath
        saveRepositoryPath(repoPath)
        return true
    }

    func backendMissingItems() -> [String] {
        var missing: [String] = []
        if !isValidRepository(repoPath) {
            missing.append(L10n.tr("repository.missing.backend"))
        }
        if !FileManager.default.fileExists(atPath: envPath) {
            missing.append(".env")
        }
        let pythonBin = (repoPath as NSString).appendingPathComponent(".venv/bin/python")
        if !FileManager.default.fileExists(atPath: pythonBin) {
            missing.append(".venv")
        }
        return missing
    }

    func isFirstRunBackendSetup(missing: [String]) -> Bool {
        return missing.contains(L10n.tr("repository.missing.backend"))
    }

    /// Runs the bundled bootstrap script. `progress` receives each output line
    /// on the main queue while the install runs; `completion` receives the
    /// overall result and the full combined output. Completion is driven by
    /// EOF on the output pipe (all writers, including brew/pip children, have
    /// exited) so no trailing output is lost.
    func bootstrapBackend(
        progress: ((String) -> Void)? = nil,
        completion: @escaping (Bool, String) -> Void
    ) {
        let scriptCandidates = [
            (repoPath as NSString).appendingPathComponent("scripts/bootstrap-backend.sh"),
            (bundledRepoCandidate as NSString).appendingPathComponent("scripts/bootstrap-backend.sh"),
            Bundle.main.path(forResource: "bootstrap-backend", ofType: "sh", inDirectory: "Scripts") ?? "",
        ].filter { !$0.isEmpty }

        guard let scriptPath = scriptCandidates.first(where: {
            FileManager.default.fileExists(atPath: $0)
        }) else {
            completion(false, L10n.tr("repository.install.script_missing"))
            return
        }

        let task = Process()
        let output = Pipe()
        task.executableURL = URL(fileURLWithPath: "/bin/bash")
        task.arguments = [
            scriptPath,
            "--install-dir", repoPath,
            "--workspace", FileManager.default.homeDirectoryForCurrentUser.path,
        ]
        task.standardOutput = output
        task.standardError = output

        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:"
            + (env["PATH"] ?? "")
        task.environment = env

        var buffer = Data()
        var pendingLine = ""
        let outputQueue = DispatchQueue(label: "fluxion.bootstrap-output")
        output.fileHandleForReading.readabilityHandler = { handle in
            let chunk = handle.availableData
            if chunk.isEmpty {
                handle.readabilityHandler = nil
                outputQueue.async {
                    task.waitUntilExit()
                    let text = String(data: buffer, encoding: .utf8) ?? ""
                    DispatchQueue.main.async {
                        completion(task.terminationStatus == 0, text)
                    }
                }
                return
            }
            outputQueue.async {
                buffer.append(chunk)
                guard let progress = progress,
                      let text = String(data: chunk, encoding: .utf8) else { return }
                pendingLine += text
                var lines = pendingLine.components(separatedBy: "\n")
                pendingLine = lines.removeLast()
                let report = lines
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                    .filter { !$0.isEmpty }
                guard !report.isEmpty else { return }
                DispatchQueue.main.async {
                    for line in report { progress(line) }
                }
            }
        }

        do {
            try task.run()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            completion(false, error.localizedDescription)
        }
    }

    /// Repair prompt for an existing-but-broken backend. First-run setup goes
    /// through WelcomeWindow.showBackendSetup() instead — a fresh DMG user has
    /// no existing backend to choose, so the repair choices would only
    /// confuse. Pointing the app at another checkout stays available here and
    /// in Preferences (and via FLUXION_REPO_PATH).
    @discardableResult
    func promptBackendInstall(missing: [String]) -> Bool {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = L10n.tr("repository.repair_required.title")
        alert.informativeText = L10n.tr(
            "repository.repair_required.message",
            repoPath,
            missing.joined(separator: ", ")
        )
        alert.addButton(withTitle: L10n.tr("repository.repair_backend"))
        alert.addButton(withTitle: L10n.tr("repository.choose_existing_backend"))
        alert.addButton(withTitle: L10n.tr("preferences.later"))

        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            runBackendInstallFlow()
            return false
        } else if response == .alertSecondButtonReturn {
            if let selected = chooseRepository() {
                repoPath = selected
                loadEnv()
                return backendMissingItems().isEmpty
            }
        }
        return false
    }

    /// Called by WelcomeWindow when its in-window backend install finished
    /// successfully. Persists the repository choice and brings the app
    /// runtime up; the onboarding presentation depends on whether the setup
    /// window is still on screen.
    func backendSetupDidSucceed(windowStillOpen: Bool) {
        saveRepositoryPath(repoPath)
        loadEnv()
        startRuntimeAfterBackendReady(
            onboarding: windowStillOpen ? .advanceSetupWindow : .suppress
        )
    }

    /// Mirrors the probe order in scripts/bootstrap-backend.sh, but with
    /// absolute paths only: the GUI app must not execute the /usr/bin/python3
    /// shim on a machine without Command Line Tools, because that pops
    /// Apple's developer-tools install dialog.
    func findUsablePython() -> String? {
        var candidates: [String] = []
        for dir in ["/opt/homebrew/bin", "/usr/local/bin"] {
            for name in ["python3.13", "python3.12", "python3.14", "python3"] {
                candidates.append("\(dir)/\(name)")
            }
        }
        if FileManager.default.isExecutableFile(
            atPath: "/Library/Developer/CommandLineTools/usr/bin/python3"
        ) {
            candidates.append("/usr/bin/python3")
        }

        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            let task = Process()
            task.executableURL = URL(fileURLWithPath: path)
            task.arguments = ["-c", "import sys; raise SystemExit(sys.version_info < (3, 12))"]
            task.standardOutput = FileHandle.nullDevice
            task.standardError = FileHandle.nullDevice
            guard (try? task.run()) != nil else { continue }
            task.waitUntilExit()
            if task.terminationStatus == 0 {
                return path
            }
        }
        return nil
    }

    func homebrewAvailable() -> Bool {
        return FileManager.default.isExecutableFile(atPath: "/opt/homebrew/bin/brew")
            || FileManager.default.isExecutableFile(atPath: "/usr/local/bin/brew")
    }

    func showPythonRequiredDialog() {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = L10n.tr("repository.python_required.title")
        alert.informativeText = L10n.tr("repository.python_required.message")
        alert.addButton(withTitle: L10n.tr("repository.python_required.download"))
        alert.addButton(withTitle: L10n.tr("preferences.later"))
        if alert.runModal() == .alertFirstButtonReturn,
           let url = URL(string: "https://www.python.org/downloads/macos/") {
            NSWorkspace.shared.open(url)
        }
    }

    func runBackendInstallFlow() {
        // The installer can provide Python itself only through Homebrew.
        // Without either, fail before the install starts, with guidance
        // instead of a raw script error.
        if findUsablePython() == nil && !homebrewAvailable() {
            showPythonRequiredDialog()
            return
        }

        let progress = NSAlert()
        progress.alertStyle = .informational
        progress.messageText = L10n.tr("repository.installing.title")
        progress.informativeText = L10n.tr("repository.installing.message")
        progress.addButton(withTitle: L10n.tr("preferences.ok"))
        progress.buttons.first?.isEnabled = false

        let panel = progress.window
        panel.center()
        panel.makeKeyAndOrderFront(nil)

        bootstrapBackend { [weak self] ok, output in
            guard let self = self else { return }
            panel.orderOut(nil)

            let result = NSAlert()
            result.alertStyle = ok ? .informational : .warning
            result.messageText = ok
                ? L10n.tr("repository.install.complete.title")
                : L10n.tr("repository.install.failed.title")
            result.informativeText = ok
                ? L10n.tr("repository.install.complete.message", self.repoPath)
                : L10n.tr("repository.install.failed.message", output)
            result.addButton(withTitle: L10n.tr("preferences.ok"))
            result.runModal()

            if ok {
                self.saveRepositoryPath(self.repoPath)
                self.loadEnv()
                self.runAvailabilityDetection(initialize: true) {
                    self.loadEnv()
                    self.terminateForeignServices()
                    self.startServicesIfNeeded()
                    self.refresh(force: true)
                }
            }
        }
    }

    func repairBackendFromPreferences() {
        runBackendInstallFlow()
    }

    func saveRepositoryPath(_ path: String) {
        do {
            try FileManager.default.createDirectory(
                at: applicationSupportDir,
                withIntermediateDirectories: true
            )
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(DesktopConfig(repositoryPath: path))
            try data.write(to: desktopConfigPath, options: .atomic)
        } catch {
            NSLog("FluxionMenu: failed to save desktop config: %@", error.localizedDescription)
        }
    }

    func selectRepositoryForNextLaunch() -> String? {
        return chooseRepository()
    }

    @discardableResult
    func showSetupWarningIfNeeded() -> Bool {
        let missing = backendMissingItems()
        guard !missing.isEmpty else { return true }
        if isFirstRunBackendSetup(missing: missing) {
            WelcomeWindow.shared.showBackendSetup()
            return false
        }
        return promptBackendInstall(missing: missing)
    }
}
