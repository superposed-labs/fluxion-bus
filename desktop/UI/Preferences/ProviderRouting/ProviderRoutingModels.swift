import AppKit
import Foundation

// Decoded shapes of `fluxion provider ...` CLI output. Every value the
// preferences page reads from the gateway enters through one of these structs,
// so an optional field here means "an older backend may omit this", not
// "unused".

// MARK: - State Data Structures

struct ProviderRoutingState: Decodable {
    let configured: Bool
    let configFile: String
    let defaultPolicy: String?
    // Optional for compatibility with an older managed backend that does not
    // expose token_available yet.
    let tokenAvailable: Bool?
    let routes: [ProviderRouteState]
    let providers: [ProviderDefinitionState]
    let catalogs: [ProviderCatalogState]
    // Optional for compatibility with a managed backend that predates
    // executor detection; treat an absent list as "nothing detected".
    let executorStates: [ProviderExecutorState]?
    // Roles the gateway enforces read-only for. Named by the backend so the
    // list is not restated here and left to drift.
    let readOnlyRoles: [String]?
    // Routes pinned to a model the vendor has superseded. Derived from the
    // catalog rather than announced by this app, so a release the app has
    // never heard of still produces an offer.
    let upgrades: [ProviderUpgradeOffer]?
    let modelHealth: ProviderModelHealthState
    let codex: ProviderCodexState

    var executors: [ProviderExecutorState] { executorStates ?? [] }

    func isReadOnlyRole(_ role: String) -> Bool {
        (readOnlyRoles ?? ["explorer", "reviewer"]).contains(role.lowercased())
    }

    enum CodingKeys: String, CodingKey {
        case configured
        case configFile = "config_file"
        case defaultPolicy = "default_policy"
        case tokenAvailable = "token_available"
        case routes
        case providers
        case catalogs
        case executorStates = "executors"
        case readOnlyRoles = "read_only_roles"
        case upgrades
        case modelHealth = "model_health"
        case codex
    }
}

struct ProviderRouteState: Decodable {
    let role: String
    let policy: String
    let candidates: [String]
    let fallback: [String]
    let weights: [String: Double]?
    let efforts: [String: String]?
    let inheritsAuto: Bool

    enum CodingKeys: String, CodingKey {
        case role, policy, candidates, fallback, weights, efforts
        case inheritsAuto = "inherits_auto"
    }
}

struct ProviderDefinitionState: Decodable {
    let id: String
    let executor: String
    let enabled: Bool
    let models: [String]
}

struct ProviderUpgradeOffer: Decodable {
    let providerId: String
    let executor: String
    let fromModel: String
    let toModel: String
    let inputPer1M: Double?
    let outputPer1M: Double?
    /// "unchanged" or "cheaper" — a newer model that costs more is a budget
    /// decision, so the backend never offers one.
    let priceDelta: String
    let roles: [String]

    enum CodingKeys: String, CodingKey {
        case providerId = "provider_id"
        case executor, roles
        case fromModel = "from_model"
        case toModel = "to_model"
        case inputPer1M = "input_per_1m"
        case outputPer1M = "output_per_1m"
        case priceDelta = "price_delta"
    }

    var fromCandidate: String { "\(providerId):\(fromModel)" }
    var toCandidate: String { "\(providerId):\(toModel)" }
}

// Every executor Fluxion knows about, whether or not the routing config
// mentions it. Deriving the list from `providers` alone meant an installed CLI
// that was simply not set up yet had no representation in the UI at all.
struct ProviderExecutorState: Decodable {
    let executor: String
    let state: String
    let installed: Bool
    let detectDetail: String?
    let path: String?
    let providerIds: [String]
    let enabledProviderIds: [String]
    // Both optional so an older managed backend simply offers less, rather
    // than failing to decode the whole state.
    let defaultProviderId: String?
    let enforcesReadOnly: Bool?
    /// This vendor's current models, most capable first, ranked by the backend.
    /// Empty on the local-only state call, which reads no catalogs.
    let lineup: [String]?

    enum CodingKeys: String, CodingKey {
        case executor, state, installed, path
        case detectDetail = "detect_detail"
        case providerIds = "provider_ids"
        case enabledProviderIds = "enabled_provider_ids"
        case defaultProviderId = "default_provider_id"
        case enforcesReadOnly = "enforces_read_only"
        case lineup
    }

    /// The tier a role-running thread should default to: the middle of the
    /// lineup. Naming a codename instead — `terra`, `sol` — stops matching the
    /// day a vendor names its next generation something else.
    var recommendedModel: String? {
        guard let lineup, !lineup.isEmpty else { return nil }
        return lineup[(lineup.count - 1) / 2]
    }
    var topModel: String? { lineup?.first }
    var cheapestModel: String? { lineup?.last }

    /// Installed, but no provider entry names it: the one state worth an action.
    var isAddable: Bool { state == "available" }
    var isConfigured: Bool { !providerIds.isEmpty }
}

struct ProviderCatalogState: Decodable {
    let found: Bool
    let agent: String
    let source: String?
    let models: [ProviderCatalogModelState]
    let warnings: [String]?
}

struct ProviderCatalogModelState: Decodable {
    let id: String
    let label: String?
    let inputPer1M: Double?
    let outputPer1M: Double?
    let priceSource: String?
    let note: String?
    let promo: String?
    let tag: String?
    let supportedReasoningEfforts: [String]?
    let availability: String?

    enum CodingKeys: String, CodingKey {
        case id, label
        case inputPer1M = "input_per_1m"
        case outputPer1M = "output_per_1m"
        case priceSource = "price_source"
        case note, promo, tag
        case supportedReasoningEfforts = "supported_reasoning_efforts"
        case availability
    }
}

struct ProviderModelHealthState: Decodable {
    let missing: [String]
    let unverified: [ProviderUnverifiedState]?
    let notes: [String]
}

struct ProviderUnverifiedState: Decodable {
    let candidate: String
    let reason: String
}

struct ProviderCodexState: Decodable {
    let home: String?
    let installed: Bool
    let managedBlock: Bool
    let configPath: String
    let roles: [ProviderCodexRoleState]
    /// Whether the installed Codex still lets a role choose its `model_provider`.
    /// `nil` when the version could not be read. `false` means every file can be
    /// present and healthy while nothing routes, so it has to outrank `installed`
    /// everywhere that reports status.
    let routesSubAgents: Bool?
    let cliVersion: String?

    /// Installed, and on a Codex that will actually honour it.
    var routingUsable: Bool { installed && routesSubAgents != false }

    enum CodingKeys: String, CodingKey {
        case home, installed, roles
        case managedBlock = "managed_block"
        case configPath = "config_path"
        case routesSubAgents = "routes_sub_agents"
        case cliVersion = "cli_version"
    }
}

struct ProviderCodexRoleState: Decodable {
    let role: String
    let installed: Bool
    let readable: Bool?
    let error: String?
    let model: String
    let provider: String
    let path: String?

    var healthy: Bool { installed && readable != false }
}
