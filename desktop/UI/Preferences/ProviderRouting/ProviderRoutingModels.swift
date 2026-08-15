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
    let modelHealth: ProviderModelHealthState
    let codex: ProviderCodexState

    enum CodingKeys: String, CodingKey {
        case configured
        case configFile = "config_file"
        case defaultPolicy = "default_policy"
        case tokenAvailable = "token_available"
        case routes
        case providers
        case catalogs
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

    enum CodingKeys: String, CodingKey {
        case home, installed, roles
        case managedBlock = "managed_block"
        case configPath = "config_path"
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
