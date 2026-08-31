import AppKit
import Foundation

// Decoded shapes of the shared `fluxion workspace ...` service output plus the
// small value types the Workspace Access page keeps in memory. An optional
// field here means "an older backend may omit this", not "sometimes absent".

enum WorkspaceAccessLayout {
    static let rowHeight: CGFloat = 50
    static let projectLeading: CGFloat = 12
    static let accessTrailing: CGFloat = 242
    static let statusTrailing: CGFloat = 126
    // AppKit keeps a small horizontal inset on view-based rows. Mirror it in
    // the header so the visible column anchors line up at every width.
    static let tableRowInset: CGFloat = 8
    static let disclosureTrailing: CGFloat = 8
    static let tableMaxHeight: CGFloat = 430
    static let fitContentInset: CGFloat = 4
}

// MARK: - Models & Data Structures

struct WorkspaceAccessEntryRow: Decodable {
    let id: String
    let key: String
    let path: String
    let access: String
    let source: String
    let status: String
    let defaultExecutor: String
    let description: String
    let appEntryId: String?
    let managed: Bool

    enum CodingKeys: String, CodingKey {
        case id, key, path, access, source, status, description, managed
        case defaultExecutor = "default_executor"
        case appEntryId = "app_entry_id"
    }

    var isManaged: Bool {
        return managed || appEntryId != nil || source == "app"
    }

    var projectName: String {
        let folderName = (path as NSString).lastPathComponent
        if key.isEmpty || key.hasPrefix("legacy-") {
            return folderName.isEmpty ? path : folderName
        }
        return key
    }

    var isWrite: Bool {
        return access.lowercased() == "read-write" || access.lowercased() == "workspace-write"
    }

    var isMissing: Bool {
        return status == "missing"
    }

    var isBlocked: Bool {
        return status == "denied" || status == "blocked"
    }

    var hasIssue: Bool {
        return isMissing || isBlocked
    }

    /// Auto Ping is an internal service workspace, not a user project. Keep
    /// this classification next to the decoded row so every presentation
    /// surface can avoid exposing it as an editable project.
    var isAutoPing: Bool {
        if key.lowercased() == "autoping" || source.lowercased() == "autoping" {
            return true
        }
        return source
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .contains("autoping")
    }

    /// The fallback workspace is an effective system default. It is shown in
    /// a separate non-editable card instead of looking like a normal project.
    var isSystemDefault: Bool {
        guard !isManaged else { return false }
        return source
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .contains("legacy:FLUXION_WORKSPACE_ROOT")
    }
}

struct WorkspaceAccessRequestRow: Decodable {
    let requestId: String
    let clientId: String
    let path: String
    let mode: String
    let status: String
    let createdAt: String
    let expiresAt: String
    let approvedAt: String?
    let consumedAt: String?
    let lastNotifiedAt: String?

    enum CodingKeys: String, CodingKey {
        case clientId = "client_id"
        case path, mode, status
        case requestId = "authorization_request_id"
        case createdAt = "created_at"
        case expiresAt = "expires_at"
        case approvedAt = "approved_at"
        case consumedAt = "consumed_at"
        case lastNotifiedAt = "last_notified_at"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        requestId = try container.decodeIfPresent(String.self, forKey: .requestId) ?? ""
        clientId = try container.decodeIfPresent(String.self, forKey: .clientId) ?? ""
        path = try container.decodeIfPresent(String.self, forKey: .path) ?? ""
        mode = try container.decodeIfPresent(String.self, forKey: .mode) ?? "read-only"
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "pending"
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        expiresAt = try container.decodeIfPresent(String.self, forKey: .expiresAt) ?? ""
        approvedAt = try container.decodeIfPresent(String.self, forKey: .approvedAt)
        consumedAt = try container.decodeIfPresent(String.self, forKey: .consumedAt)
        lastNotifiedAt = try container.decodeIfPresent(String.self, forKey: .lastNotifiedAt)
    }

    var pending: Bool {
        return status == "pending"
    }

    var projectName: String {
        return (path as NSString).lastPathComponent
    }

    var isWrite: Bool {
        return mode.lowercased() == "workspace-write" || mode.lowercased() == "write" || mode.lowercased() == "read-write"
    }

    var requesterDisplayName: String {
        switch clientId.lowercased() {
        case "codex": return "Codex"
        case "claude", "claude code": return "Claude Code"
        case "antigravity": return "Antigravity"
        default: return clientId.isEmpty ? "Agent" : clientId
        }
    }
}

enum WorkspaceAccessTimeFormatter {
    static func formatExpires(from isoString: String) -> String {
        guard !isoString.isEmpty, let date = SharedDateFormatters.parseISO(isoString) else {
            return isoString
        }
        let now = Date()
        let diff = date.timeIntervalSince(now)
        if diff <= 0 {
            return L10n.tr("preferences.workspace_access.requests.expired")
        }
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = Locale(identifier: L10n.resolvedAppLanguage)
        formatter.unitsStyle = .full
        formatter.dateTimeStyle = .numeric
        return formatter.localizedString(for: date, relativeTo: now)
    }

    static func formatRelativeAt(from isoString: String) -> String? {
        guard !isoString.isEmpty, let date = SharedDateFormatters.parseISO(isoString) else {
            return nil
        }
        let now = Date()
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = Locale(identifier: L10n.resolvedAppLanguage)
        formatter.unitsStyle = .full
        formatter.dateTimeStyle = .numeric
        return formatter.localizedString(for: date, relativeTo: now)
    }
}

struct WorkspaceAccessRuntimeContext: Decodable {
    let workspaceRoot: String?

    enum CodingKeys: String, CodingKey {
        case workspaceRoot = "workspace_root"
    }
}

struct WorkspaceAccessListResponse: Decodable {
    let workspaces: [WorkspaceAccessEntryRow]
    let pendingRequests: [WorkspaceAccessRequestRow]
    let configPath: String?
    let runtimeContext: WorkspaceAccessRuntimeContext?

    enum CodingKeys: String, CodingKey {
        case workspaces
        case pendingRequests = "pending_requests"
        case configPath = "config_path"
        case runtimeContext = "runtime_context"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        workspaces = try container.decodeIfPresent([WorkspaceAccessEntryRow].self, forKey: .workspaces) ?? []
        pendingRequests = try container.decodeIfPresent([WorkspaceAccessRequestRow].self, forKey: .pendingRequests) ?? []
        configPath = try container.decodeIfPresent(String.self, forKey: .configPath)
        runtimeContext = try container.decodeIfPresent(WorkspaceAccessRuntimeContext.self, forKey: .runtimeContext)
    }
}

struct WorkspaceAccessDeleteResponse: Decodable {
    let permissionStillEffective: Bool
    let path: String
    let removedAccess: String?
    let remainingAccess: String?
    let remainingSource: String?
    let remainingPolicy: String?
    let remainingRoot: String?

    enum CodingKeys: String, CodingKey {
        case permissionStillEffective = "permission_still_effective"
        case path
        case removedAccess = "removed_access"
        case remainingAccess = "remaining_access"
        case remainingSource = "remaining_source"
        case remainingPolicy = "remaining_policy"
        case remainingRoot = "remaining_root"
    }
}

enum WorkspaceAccessPhase {
    case loading
    case failed(String)
    case ready
}

enum WorkspaceAccessFilterOption: Int {
    case all = 0
    case readOnly = 1
    case readWrite = 2
    case issues = 3
}
