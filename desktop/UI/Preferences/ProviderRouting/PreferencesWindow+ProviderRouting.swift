import AppKit
import Foundation

// Entry point for the Preferences "Provider Routing" page: fetching gateway
// state, deciding when to re-render, running `fluxion provider` commands, and
// the shared role/model formatters. The cards and sections it renders live in
// PreferencesWindow+ProviderCards.swift and PreferencesWindow+ProviderSections.swift.

private struct ProviderCommandError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

extension PreferencesWindow {
    func prefetchProviderRoutingState() {
        guard providerRoutingState == nil, !providerRoutingLoading else { return }
        // Fetch the cheap local state first, then continue with live catalogs
        // in the background. If the user opens Provider Routing a few seconds
        // later, the complete page is already available.
        refreshProviderRouting(includeCatalogs: true)
    }

    func prepareProviderRoutingForDisplay() {
        // Prefetch deliberately does not render the hidden page. Always paint
        // the latest in-memory snapshot when the user actually opens Provider
        // Routing, even when the live catalog is already loading or finished.
        renderProviderRouting()
        if providerRoutingState == nil {
            refreshProviderRouting(includeCatalogs: true)
        } else if !providerCatalogsLoaded && !providerCatalogsLoading {
            refreshProviderCatalogDetails()
        }
    }

    private func renderProviderRoutingIfVisible() {
        guard
            currentPageId == "provider-routing",
            window?.isVisible == true
        else { return }
        renderProviderRouting()
    }

    func buildProviderRoutingSection(into documentStack: NSStackView) {
        providerRoutingDynamicStack = NSStackView()
        providerRoutingDynamicStack.orientation = .vertical
        providerRoutingDynamicStack.alignment = .leading
        providerRoutingDynamicStack.spacing = 16
        providerRoutingDynamicStack.translatesAutoresizingMaskIntoConstraints = false
        providerRoutingDynamicStack.setContentHuggingPriority(.defaultLow, for: .horizontal)
        providerRoutingDynamicStack.setContentCompressionResistancePriority(
            .defaultLow,
            for: .horizontal)
        documentStack.addArrangedSubview(providerRoutingDynamicStack)
        let totalInset = documentStack.edgeInsets.left + documentStack.edgeInsets.right
        providerRoutingDynamicStack.widthAnchor
            .constraint(equalTo: documentStack.widthAnchor, constant: -totalInset).isActive = true

        renderProviderRouting()
    }

    func refreshProviderRouting(includeCatalogs: Bool) {
        guard !providerRoutingLoading else {
            if includeCatalogs {
                providerCatalogRefreshRequested = true
            }
            return
        }
        providerRoutingLoading = true
        renderProviderRoutingIfVisible()

        let arguments = [
            "preferences-state",
            "--skip-catalogs",
            "--skip-model-health",
        ]
        runProviderCommand(arguments) { [weak self] result in
            guard let self = self else { return }
            var loadedLocalState = false
            switch result {
            case .success(let data):
                do {
                    let localState = try JSONDecoder().decode(
                        ProviderRoutingState.self, from: data)
                    self.providerRoutingState = self.mergingLocalProviderState(localState)
                    self.providerRoutingNeedsBackendUpdate = false
                    loadedLocalState = true
                } catch {
                    self.showProviderRoutingError(error.localizedDescription)
                }
            case .failure(let error):
                if self.isProviderPreferencesUnsupported(error) {
                    self.providerRoutingNeedsBackendUpdate = true
                } else {
                    self.showProviderRoutingError(error.localizedDescription)
                }
            }

            self.providerRoutingLoading = false
            self.renderProviderRoutingIfVisible()
            let shouldLoadCatalogs = includeCatalogs || self.providerCatalogRefreshRequested
            self.providerCatalogRefreshRequested = false
            if loadedLocalState && shouldLoadCatalogs {
                self.refreshProviderCatalogDetails()
            }

            DispatchQueue.global(qos: .utility).async {
                let running = self.appDelegate.isServiceDaemonRunning("fluxion-provider")
                DispatchQueue.main.async {
                    self.providerGatewayRunning = running
                    self.updateSidebarDots()
                    self.renderProviderRoutingIfVisible()
                }
            }
        }
    }

    private func mergingLocalProviderState(
        _ localState: ProviderRoutingState
    ) -> ProviderRoutingState {
        guard let currentState = providerRoutingState else {
            return localState
        }
        return ProviderRoutingState(
            configured: localState.configured,
            configFile: localState.configFile,
            defaultPolicy: localState.defaultPolicy,
            tokenAvailable: localState.tokenAvailable,
            routes: localState.routes,
            providers: localState.providers,
            catalogs: currentState.catalogs,
            modelHealth: currentState.modelHealth,
            codex: localState.codex)
    }

    func refreshProviderCatalogDetails() {
        guard !providerCatalogsLoading else { return }
        providerCatalogsLoading = true
        providerCatalogsUnavailable = false
        renderProviderRoutingIfVisible()

        runProviderCommand(["preferences-state"]) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let data):
                do {
                    self.providerRoutingState = try JSONDecoder().decode(
                        ProviderRoutingState.self, from: data)
                    self.providerCatalogsLoaded = true
                    self.providerCatalogsUnavailable = false
                } catch {
                    self.providerCatalogsUnavailable = true
                    NSLog(
                        "FluxionPreferences: could not decode provider catalog state: %@",
                        error.localizedDescription)
                }
            case .failure(let error):
                self.providerCatalogsUnavailable = true
                NSLog(
                    "FluxionPreferences: could not refresh provider catalogs: %@",
                    error.localizedDescription)
            }
            self.providerCatalogsLoading = false
            self.renderProviderRoutingIfVisible()
            if self.providerCatalogsLoaded,
               let role = self.pendingProviderRouteEditRole
            {
                self.pendingProviderRouteEditRole = nil
                self.presentProviderRouteEditor(role: role)
            } else if self.providerCatalogsUnavailable,
                      self.pendingProviderRouteEditRole != nil
            {
                self.pendingProviderRouteEditRole = nil
                self.showProviderRoutingError(
                    L10n.tr("preferences.provider.catalogs.load_failed"))
            }
        }
    }

    func renderProviderRouting() {
        guard let stack = providerRoutingDynamicStack else { return }
        let stableWindowFrame = window?.isVisible == true ? window?.frame : nil
        defer {
            window?.contentView?.layoutSubtreeIfNeeded()
            restorePreferencesWindowFrame(stableWindowFrame)
        }
        for view in stack.arrangedSubviews {
            stack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }

        guard let state = providerRoutingState else {
            if providerRoutingNeedsBackendUpdate {
                addProviderBackendUpdateCard(into: stack)
                return
            }
            let loading = NSTextField(labelWithString:
                providerRoutingLoading
                    ? L10n.tr("preferences.provider.loading")
                    : L10n.tr("preferences.provider.unavailable"))
            loading.font = NSFont.systemFont(ofSize: 12.5)
            loading.textColor = Palette.secondaryText
            stack.addArrangedSubview(loading)
            return
        }

        // 1. Top Provider Gateway Card
        addProviderGatewayCard(state, into: stack)

        guard state.configured else {
            addProviderSetup(state, into: stack)
            return
        }

        // 2. Gemini 3.7 Flash Upgrade Banner (if eligible)
        if hasGemini37Upgrade(state) && providerGatewayRunning {
            addProviderUpgradeBanner(state, into: stack)
        }

        // 3. Role Routing Section
        addProviderRoleRoutingSection(state, into: stack)

        // 4. Codex Integration Section
        addProviderCodexSection(state, into: stack)

        // 5. Executors & Model Catalogs Section
        addProviderCatalogsSection(state, into: stack)

        // 6. Model Updates Section
        addProviderModelUpdatesSection(into: stack)
    }

    private func isProviderPreferencesUnsupported(_ error: Error) -> Bool {
        let message = error.localizedDescription
        return message.contains("invalid choice: 'preferences-state'")
            || message.contains("invalid choice: \"preferences-state\"")
    }

    private func addProviderBackendUpdateCard(into stack: NSStackView) {
        let repairButton = NSButton(
            title: L10n.tr("preferences.provider.backend_update.action"),
            target: self,
            action: #selector(repairBackend)
        )
        repairButton.bezelStyle = .rounded

        let row = CardRow(
            title: L10n.tr("preferences.provider.backend_update.title"),
            desc: L10n.tr("preferences.provider.backend_update.desc"),
            control: repairButton,
            isFirst: true
        )
        addSection(
            title: L10n.tr("preferences.provider.gateway.title"),
            rows: [row],
            into: stack
        )
    }

    // MARK: - Actions & Diagnostics

    @objc func refreshProviderCatalogs(_ sender: NSButton) {
        refreshProviderCatalogDetails()
    }

    @objc func initializeProviderRouting(_ sender: NSButton) {
        sender.isEnabled = false
        runProviderCommand(["init"]) { [weak self] result in
            sender.isEnabled = true
            switch result {
            case .success:
                self?.refreshProviderRouting(includeCatalogs: true)
            case .failure(let error):
                self?.showProviderRoutingError(error.localizedDescription)
            }
        }
    }

    @objc func toggleProviderGateway(_ sender: NSButton) {
        let enable = !providerGatewayRunning
        checkProvider?.state = enable ? .on : .off
        appDelegate.saveEnv(updates: ["FLUXION_PROVIDER_ENABLED": enable ? "true" : "false"])
        sender.isEnabled = false
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let pattern = self.appDelegate.servicePattern("fluxion-provider")
            if enable {
                self.appDelegate.startServicesIfNeeded()
            } else {
                self.appDelegate.stopServices(patterns: [pattern])
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                self.refreshProviderRouting(includeCatalogs: true)
            }
        }
    }

    @objc func runProviderDiagnosticsAction(_ sender: Any) {
        var arguments = ["doctor"]
        if providerGatewayRunning {
            arguments.append("--running")
        }
        runProviderCommand(arguments) { [weak self] result in
            guard let self = self, let win = self.window else { return }
            let alert = NSAlert()
            alert.messageText = L10n.tr("preferences.provider.diagnostics.title")
            switch result {
            case .success(let data):
                let output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                alert.informativeText = output.isEmpty
                    ? L10n.tr("preferences.provider.diagnostics.all_ok")
                    : output
                alert.alertStyle = .informational
            case .failure(let error):
                alert.informativeText = error.localizedDescription
                alert.alertStyle = .warning
            }
            alert.addButton(withTitle: L10n.tr("preferences.ok"))
            alert.beginSheetModal(for: win, completionHandler: nil)
        }
    }

    @objc func editProviderRoute(_ sender: NSButton) {
        guard let role = sender.identifier?.rawValue else { return }

        if !providerCatalogsLoaded && !providerCatalogsUnavailable {
            pendingProviderRouteEditRole = role
            if !providerCatalogsLoading {
                refreshProviderCatalogDetails()
            } else {
                renderProviderRoutingIfVisible()
            }
            return
        }

        presentProviderRouteEditor(role: role)
    }

    private func presentProviderRouteEditor(role: String) {
        guard
            activeRouteEditorSheetController == nil,
            currentPageId == "provider-routing",
            let state = providerRoutingState,
            let route = state.routes.first(where: { $0.role == role }),
            let win = window,
            win.isVisible
        else { return }

        let catalogGroups = ProviderRouteEditorSheetController.buildCatalogGroups(state: state, formatter: self)
        guard !catalogGroups.isEmpty && catalogGroups.contains(where: { !$0.items.isEmpty }) else {
            showProviderRoutingError(L10n.tr("preferences.provider.no_models"))
            return
        }

        let controller = ProviderRouteEditorSheetController(
            route: route,
            state: state,
            parentWindow: win,
            preferencesWindow: self,
            onDismiss: { [weak self] in
                self?.activeRouteEditorSheetController = nil
            }
        ) { [weak self] primaryCandidate, fallbackCandidates, effort in
            self?.saveProviderRoute(
                role: role,
                candidate: primaryCandidate,
                fallbacks: fallbackCandidates,
                effort: effort)
        }
        activeRouteEditorSheetController = controller
        controller.show()
    }

    private func saveProviderRoute(
        role: String,
        candidate: String,
        fallbacks: [String],
        effort: String?
    ) {
        var arguments = [
            "set-route", "--role", role, "--candidate", candidate, "--add-models"
        ]
        if let effort, !effort.isEmpty {
            arguments += ["--effort", "\(candidate)=\(effort)"]
        }
        for fallback in fallbacks where fallback != candidate && !fallback.isEmpty {
            arguments += ["--fallback", fallback]
        }
        runProviderCommand(arguments) { [weak self] result in
            switch result {
            case .success:
                guard let self = self else { return }
                // The route file is already validated and atomically replaced.
                // Reflect the saved values immediately. Starting another
                // `fluxion-provider` process here races with restartServices,
                // whose process match intentionally stops the gateway binary
                // and would also kill that short-lived refresh command.
                self.applySavedProviderRoute(
                    role: role,
                    candidate: candidate,
                    fallbacks: fallbacks,
                    effort: effort)
                DispatchQueue.global(qos: .userInitiated).async {
                    self.appDelegate.restartServices(
                        patterns: [self.appDelegate.servicePattern("fluxion-provider")])
                    DispatchQueue.main.async {
                        self.providerGatewayRunning =
                            self.appDelegate.isServiceDaemonRunning("fluxion-provider")
                        self.updateSidebarDots()
                        self.renderProviderRoutingIfVisible()
                        // Reconcile model health and live catalog metadata
                        // afterward; this may take seconds but no longer gates
                        // the route summary the user just changed.
                        self.refreshProviderCatalogDetails()
                    }
                }
            case .failure(let error):
                self?.showProviderRoutingError(error.localizedDescription)
            }
        }
    }

    private func applySavedProviderRoute(
        role: String,
        candidate: String,
        fallbacks: [String],
        effort: String?
    ) {
        guard let state = providerRoutingState else { return }
        let selectedCandidates = Set([candidate] + fallbacks)
        let updatedRoutes = state.routes.map { route -> ProviderRouteState in
            let followsEditedAuto = role == "auto" && route.inheritsAuto
            guard route.role == role || followsEditedAuto else {
                return route
            }
            var efforts = (route.efforts ?? [:]).filter {
                selectedCandidates.contains($0.key)
            }
            if let effort, !effort.isEmpty {
                efforts[candidate] = effort
            }
            return ProviderRouteState(
                role: route.role,
                policy: route.policy,
                candidates: [candidate],
                fallback: fallbacks,
                weights: route.weights,
                efforts: efforts,
                inheritsAuto: route.inheritsAuto)
        }
        providerRoutingState = ProviderRoutingState(
            configured: state.configured,
            configFile: state.configFile,
            defaultPolicy: state.defaultPolicy,
            tokenAvailable: state.tokenAvailable,
            routes: updatedRoutes,
            providers: state.providers,
            catalogs: state.catalogs,
            modelHealth: state.modelHealth,
            codex: state.codex)
        renderProviderRoutingIfVisible()
    }

    @objc func configureProviderCodex(_ sender: NSButton) {
        guard let state = providerRoutingState else { return }
        presentInstallRepairSheet(state: state)
    }

    func presentInstallRepairSheet(
        state: ProviderRoutingState,
        initialMode: ProviderCodexInstallMode? = nil
    ) {
        guard
            activeInstallRepairSheetController == nil,
            let win = window,
            win.isVisible
        else { return }

        if let activeConfig = activeCodexConfigSheetController {
            activeConfig.dismiss()
            activeCodexConfigSheetController = nil
        }

        let configuredModel =
            state.codex.roles.first(where: { !$0.model.isEmpty })?.model
            ?? state.catalogs.first(where: { $0.agent.lowercased() == "codex" })?.models.first?.id
            ?? "gpt-5.6-terra"
        let codexModels =
            state.catalogs.first(where: { $0.agent.lowercased() == "codex" })?.models
            ?? []
        let selectedModel =
            codexModels.contains(where: { $0.id == configuredModel })
            ? configuredModel
            : (codexModels.first(where: { $0.id == "gpt-5.6-terra" })?.id
                ?? codexModels.first?.id
                ?? configuredModel)
        loadCodexIntegrationPlan(model: selectedModel, mode: initialMode) { [weak self, weak win] result in
            guard let self = self, let win = win, win.isVisible else { return }
            switch result {
            case .success(let plan):
                let controller = ProviderInstallRepairSheetController(
                    state: state,
                    plan: plan,
                    parentWindow: win,
                    preferencesWindow: self,
                    onDismiss: { [weak self] in
                        self?.activeInstallRepairSheetController = nil
                    },
                    onComplete: { [weak self] in
                        self?.refreshProviderRouting(includeCatalogs: true)
                    }
                )
                self.activeInstallRepairSheetController = controller
                controller.show()
            case .failure(let error):
                self.showProviderRoutingError(error.localizedDescription)
            }
        }
    }

    func loadCodexIntegrationPlan(
        model: String,
        mode: ProviderCodexInstallMode?,
        completion: @escaping (Result<ProviderCodexInstallPlan, Error>) -> Void
    ) {
        let requestedMode: String
        switch mode {
        case .install: requestedMode = "install"
        case .missing, .corrupt: requestedMode = "repair"
        case .reinstall: requestedMode = "reinstall"
        case nil: requestedMode = "auto"
        }
        runProviderCommand([
            "codex-integration-plan",
            "--model", model,
            "--mode", requestedMode,
        ]) { result in
            completion(result.flatMap { data in
                do {
                    return .success(try JSONDecoder().decode(
                        ProviderCodexInstallPlan.self,
                        from: data
                    ))
                } catch {
                    return .failure(error)
                }
            })
        }
    }

    func runProviderCommand(
        _ arguments: [String],
        completion: @escaping (Result<Data, Error>) -> Void
    ) {
        let binary = (appDelegate.repoPath as NSString)
            .appendingPathComponent(".venv/bin/fluxion-provider")
        guard FileManager.default.fileExists(atPath: binary) else {
            completion(.failure(ProviderCommandError(
                message: L10n.tr("preferences.provider.cli_missing", binary))))
            return
        }

        let repoPath = appDelegate.repoPath
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            let stdout = Pipe()
            let stderr = Pipe()
            process.executableURL = URL(fileURLWithPath: binary)
            process.arguments = arguments
            process.currentDirectoryURL = URL(fileURLWithPath: repoPath)
            process.standardOutput = stdout
            process.standardError = stderr
            do {
                try process.run()
                process.waitUntilExit()
                let out = stdout.fileHandleForReading.readDataToEndOfFile()
                let errorData = stderr.fileHandleForReading.readDataToEndOfFile()
                DispatchQueue.main.async {
                    if process.terminationStatus == 0 {
                        completion(.success(out))
                    } else {
                        let message = String(data: errorData, encoding: .utf8)?
                            .trimmingCharacters(in: .whitespacesAndNewlines)
                        completion(.failure(ProviderCommandError(
                            message: message?.isEmpty == false
                                ? message!
                                : L10n.tr("preferences.provider.command_failed"))))
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    completion(.failure(error))
                }
            }
        }
    }

    func showProviderRoutingError(_ message: String) {
        guard let win = window else { return }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = L10n.tr("preferences.provider.error.title")
        alert.informativeText = message
        alert.addButton(withTitle: L10n.tr("preferences.ok"))
        alert.beginSheetModal(for: win, completionHandler: nil)
    }

    // MARK: - Formatters

    func formatRoleDisplayName(_ role: String) -> String {
        switch role {
        case "auto": return "Auto"
        case "worker": return "Worker"
        case "explorer": return "Explorer"
        case "reviewer": return "Reviewer"
        case "compaction": return "Compaction"
        case "balanced", "fluxion_balanced": return "Balanced"
        case "cmp_agy": return "Comparison · Antigravity"
        case "cmp_claude": return "Comparison · Claude Code"
        case "cmp_codex": return "Comparison · Codex CLI"
        default: return role.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    func formatRoleSlug(_ role: String) -> String {
        switch role {
        case "auto": return "fluxion_auto"
        case "worker": return "fluxion_worker"
        case "explorer": return "fluxion_explorer"
        case "reviewer": return "fluxion_reviewer"
        case "balanced": return "fluxion_balanced"
        case "compaction": return "fluxion_compaction"
        default: return role
        }
    }

    func formatRoleDescription(_ role: String) -> String {
        switch role {
        case "auto": return L10n.tr("preferences.provider.role.auto.desc")
        case "worker": return L10n.tr("preferences.provider.role.worker.desc")
        case "explorer": return L10n.tr("preferences.provider.role.explorer.desc")
        case "reviewer": return L10n.tr("preferences.provider.role.reviewer.desc")
        case "compaction": return L10n.tr("preferences.provider.role.compaction.desc")
        case "balanced", "fluxion_balanced": return L10n.tr("preferences.provider.role.balanced.desc")
        case "cmp_agy": return L10n.tr("preferences.provider.role.cmp_agy.desc")
        case "cmp_claude": return L10n.tr("preferences.provider.role.cmp_claude.desc")
        case "cmp_codex": return L10n.tr("preferences.provider.role.cmp_codex.desc")
        default: return ""
        }
    }

    func formatCandidateModelName(_ candidate: String) -> String {
        let parts = candidate.split(separator: ":", maxSplits: 1).map(String.init)
        let modelID = parts.count == 2 ? parts[1] : candidate

        switch modelID {
        case "gemini-3.6-flash-high": return "Gemini 3.6 Flash · High"
        case "gemini-3.6-flash-medium": return "Gemini 3.6 Flash · Medium"
        case "gemini-3.6-flash-low": return "Gemini 3.6 Flash · Low"
        case "gemini-3.7-flash-high": return "Gemini 3.7 Flash · High"
        case "gemini-3.7-flash-medium": return "Gemini 3.7 Flash · Medium"
        case "gemini-3.7-flash-low": return "Gemini 3.7 Flash · Low"
        case "gemini-3.1-pro-high": return "Gemini 3.1 Pro · High"
        case "gemini-3.1-pro-medium": return "Gemini 3.1 Pro · Medium"
        case "gemini-3.1-pro-low": return "Gemini 3.1 Pro · Low"
        case "gemini-3.1-pro": return "Gemini 3.1 Pro"
        case "opus", "claude-opus-5": return "Claude Opus 5"
        case "sonnet", "claude-sonnet-5": return "Claude Sonnet 5"
        case "haiku", "claude-haiku-4-5-20251001", "claude-haiku-4.5": return "Claude Haiku 4.5"
        case "gpt-5.6-sol": return "GPT-5.6 Sol"
        case "gpt-5.6-terra": return "GPT-5.6 Terra"
        case "gpt-5.6-luna": return "GPT-5.6 Luna"
        case "gpt-5.5": return "GPT-5.5"
        case "gpt-5.4": return "GPT-5.4"
        case "gpt-5.4-mini": return "GPT-5.4 Mini"
        default:
            return modelID.split(separator: "-").map { $0.capitalized }.joined(separator: " ")
        }
    }

    func formatCandidateExecutorName(_ candidate: String, state: ProviderRoutingState?) -> String {
        let parts = candidate.split(separator: ":", maxSplits: 1).map(String.init)
        if parts.count == 2 {
            if let prov = state?.providers.first(where: { $0.id == parts[0] }) {
                switch prov.executor.lowercased() {
                case "antigravity": return "Antigravity"
                case "claude": return "Claude Code"
                case "codex": return "Codex CLI"
                default: return prov.executor.capitalized
                }
            }
            switch parts[0] {
            case "local_agy": return "Antigravity"
            case "local_claude": return "Claude Code"
            case "local_codex": return "Codex CLI"
            default: return parts[0]
            }
        }
        return "Local"
    }

    func formatProviderName(_ executor: String) -> String {
        switch executor.lowercased() {
        case "antigravity": return "Antigravity"
        case "claude": return "Claude Code"
        case "codex": return "Codex CLI"
        default: return executor.capitalized
        }
    }

    private func providerCandidateName(
        _ candidate: String,
        state: ProviderRoutingState
    ) -> String {
        let model = formatCandidateModelName(candidate)
        let exec = formatCandidateExecutorName(candidate, state: state)
        return "\(model) · \(exec)"
    }

    private func providerCandidateIDs(_ state: ProviderRoutingState) -> [String] {
        var result: [String] = []
        for provider in state.providers where provider.enabled {
            var models = provider.models
            if let catalog = state.catalogs.first(where: { $0.agent == provider.executor }) {
                models.append(contentsOf: catalog.models.map(\.id))
            }
            for model in models where !model.isEmpty {
                let candidate = "\(provider.id):\(model)"
                if !result.contains(candidate) {
                    result.append(candidate)
                }
            }
        }
        return result
    }
}
