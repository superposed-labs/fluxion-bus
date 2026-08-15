import AppKit
import Foundation

// Value types shared by the two Codex sheets — install plan, file previews, role
// config — plus the enums describing install mode, phase and file actions.

enum ProviderCodexInstallMode: String, Codable {
    case install
    case missing
    case corrupt
    case reinstall
}

enum ProviderCodexInstallPhase: String, Codable {
    case confirm
    case running
    case failed
    case done
}

enum ProviderCodexFileActionKind: String, Codable {
    case write
    case rewrite
    case keep
    case verify

    var localizedLabel: String {
        switch self {
        case .write:
            return L10n.tr("preferences.provider.codex.install_sheet.act.write")
        case .rewrite:
            return L10n.tr("preferences.provider.codex.install_sheet.act.rewrite")
        case .keep:
            return L10n.tr("preferences.provider.codex.install_sheet.act.keep")
        case .verify:
            return L10n.tr("preferences.provider.codex.install_sheet.act.verify")
        }
    }
}

enum ProviderCodexRoleStatus: Equatable {
    case installed
    case notInstalled
    case unreadable(reason: String?)
}

struct ProviderCodexRoleConfigItem {
    let role: String
    let file: String
    let route: String
    let model: String
    let executor: String
    let codexModel: String
    let status: ProviderCodexRoleStatus
    let why: String?
}

struct ProviderCodexFilePreview: Codable {
    let name: String
    let path: String
    let role: String?
    let action: ProviderCodexFileActionKind
    let problem: String?
    let content: String

    var isManaged: Bool { role == nil }
}

struct ProviderCodexInstallPlan: Codable {
    let mode: ProviderCodexInstallMode
    let model: String
    let configPath: String
    let agentsDir: String
    let files: [ProviderCodexFilePreview]

    enum CodingKeys: String, CodingKey {
        case mode, model, files
        case configPath = "config_path"
        case agentsDir = "agents_dir"
    }
}

struct ProviderCodexRoleModelOption: Codable, Identifiable {
    var id: String
    var name: String
    var isRecommended: Bool
    var why: String
    var isStale: Bool

    var showsTechnicalId: Bool {
        name.trimmingCharacters(in: .whitespacesAndNewlines)
            != id.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    init(id: String, name: String, isRecommended: Bool = false, why: String = "", isStale: Bool = false) {
        self.id = id
        self.name = name
        self.isRecommended = isRecommended
        self.why = why
        self.isStale = isStale
    }
}

struct ProviderCodexInstallStepItem {
    let title: String
    var status: StepStatus
    var errorMessage: String?
    var isRollback: Bool

    enum StepStatus {
        case idle
        case running
        case ok
        case error
    }

    init(title: String, status: StepStatus = .idle, errorMessage: String? = nil, isRollback: Bool = false) {
        self.title = title
        self.status = status
        self.errorMessage = errorMessage
        self.isRollback = isRollback
    }
}
