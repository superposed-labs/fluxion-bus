import Foundation

// MARK: - Quota presentation types
//
// SwiftUI-independent value types describing a provider's quota at a moment in
// time. They live here (not inside the view) so the math that produces them can
// be unit-tested without standing up any UI.

// Shared with NotchWindowController (window sizing) via the free classification
// functions, so it must be module-internal rather than file-private.
enum QuotaWindowKind {
    case fiveHour
    case weekly
}

// Percent-remaining thresholds that drive quota-bar color, shared by the notch
// and the rich menu so "seeing red" happens at the same level on both surfaces.
// The notch steps brand-color → red at `criticalRemaining` with no amber tier
// (amber collides with brand hues like Claude's coral); the rich menu, whose
// bars are semantic rather than brand-colored, adds an amber band in
// [criticalRemaining, cautionRemaining).
enum QuotaLevel {
    static let criticalRemaining: Double = 15
    static let cautionRemaining: Double = 30
}

enum BindingWindowLabel: String {
    case fiveHour = "5h"
    case weekly = "wk"
}

enum ProviderDisplayMode {
    case healthy
    case credits
    case locked
    /// Depleted, but the predicted reset instant has already passed while the
    /// cached snapshot still reports the window spent — the transient gap
    /// between "countdown hit zero" and the next poll confirming recovery.
    /// Rendered as a neutral "confirming…" treatment instead of the red lock
    /// (whose finished countdown would otherwise vanish and leave the card
    /// frozen). Never asserts recovery itself: only a fresh snapshot with room
    /// flips back to `.healthy`, so this can't resurrect a false reset.
    case recovering
    case error
    /// First fetch for a provider with no cached history (e.g. just enabled):
    /// render a neutral pending card, never the red error treatment.
    case loading
}

struct QuotaWindowSnapshot {
    let window: QuotaWindow
    let kind: QuotaWindowKind
    let remaining: Double
    /// Provider-authoritative state when available. Explicit `false` keeps a
    /// rounded 100% reading usable; nil falls back to percentage semantics.
    let limitReached: Bool?
    let tag: String?
    /// True when the window is unanchored: the provider keeps reporting that it
    /// resets a full window-length from "now", so there is no real countdown to
    /// show (e.g. an idle Codex 5h window). Rendered statically instead of as a
    /// live timer that would sawtooth between polls.
    let idle: Bool

    var depleted: Bool {
        remaining <= 0 && limitReached != false
    }

    var remainingText: String {
        if remaining <= 0, limitReached == false {
            return "<1"
        }
        return QuotaFormatter.remainingPercentText(remaining)
    }
}

struct ProviderQuotaState {
    let mode: ProviderDisplayMode
    let bindingRemaining: Double
    let bindingLabel: BindingWindowLabel
    let bindingTag: String?
    let fiveHour: QuotaWindowSnapshot?
    let weekly: QuotaWindowSnapshot?
    let credits: Double?
    let creditsEnabled: Bool
    let lockReason: String
    let note: String?
    let lockResetKind: QuotaWindowKind
    /// Window whose reset the lock countdown tracks: the depleted window for
    /// single-pool providers, the earliest-recovering pool's blocking window
    /// for split-pool ones. Nil when healthy.
    let lockSnapshot: QuotaWindowSnapshot?
    /// Split-pool providers only: every pool that is individually unusable
    /// (its 5h or weekly is spent), even while the provider as a whole stays
    /// `.healthy` on the surviving pool. Empty for single-pool providers.
    let blockedPools: [PoolLockInfo]
}

/// A blocked pool's identity plus the window whose reset unblocks it, for
/// rendering per-pool lock rows/badges without re-deriving pool state.
struct PoolLockInfo {
    let tag: String
    let kind: QuotaWindowKind
    let snapshot: QuotaWindowSnapshot?
}

/// One of a split-quota provider's pools (Antigravity GEM / EXT): its 5-hour
/// and weekly windows paired by tag. A pool is unusable ("blocked") when
/// either window is spent; the weekly outlasts the 5h, so recovery tracks the
/// weekly reset whenever the weekly is what's spent.
struct PoolQuota {
    let tag: String
    let five: QuotaWindowSnapshot?
    let weekly: QuotaWindowSnapshot?

    var fiveRemaining: Double { five?.remaining ?? 100.0 }
    var weeklyRemaining: Double { weekly?.remaining ?? 100.0 }
    var fiveZero: Bool { fiveRemaining <= 0.0 }
    var weekZero: Bool { weeklyRemaining <= 0.0 }
    var blocked: Bool { fiveZero || weekZero }
    var blockingKind: QuotaWindowKind { weekZero ? .weekly : .fiveHour }
    var blockingSnapshot: QuotaWindowSnapshot? { weekZero ? weekly : five }
}

// MARK: - Quota presenter
//
// Turns a provider's raw `QuotaWindow`s into the snapshots, lock state, and
// countdown strings the notch renders. Pure logic with no SwiftUI/AppKit
// dependency: the only ambient input is `now` (so countdown formatting is
// deterministic and testable — pass a fixed `now` in tests). The view holds a
// fresh presenter per render keyed off its ticking `now`.
struct NotchQuotaPresenter {
    let now: Date

    func windowKind(for window: QuotaWindow) -> QuotaWindowKind? {
        notchWindowKind(window)
    }

    func windowTag(for window: QuotaWindow) -> String? {
        notchWindowTag(window)
    }

    func snapshot(
        for window: QuotaWindow,
        kind: QuotaWindowKind,
        fetchedAt: String?,
        limitReached: Bool? = nil
    ) -> QuotaWindowSnapshot? {
        guard let used = window.usedPercent else { return nil }
        return QuotaWindowSnapshot(
            window: window,
            kind: kind,
            remaining: max(0.0, 100.0 - used),
            limitReached: limitReached,
            tag: windowTag(for: window),
            idle: QuotaFormatter.isWindowIdle(window, fetchedAt: fetchedAt)
        )
    }

    /// True when a depleted window's predicted reset instant has already
    /// passed but the snapshot still reports it spent — the live countdown has
    /// hit zero and we're waiting for a poll to confirm recovery. Idle
    /// (unanchored) windows have no real reset instant, so they never qualify;
    /// callers gate on `.locked` so a fresh-and-recovered window (which would
    /// no longer be depleted) never reaches here.
    func awaitingReset(_ snapshot: QuotaWindowSnapshot?) -> Bool {
        guard let snap = snapshot, !snap.idle,
              let resetDate = QuotaFormatter.parseISODate(snap.window.resetsAt) else {
            return false
        }
        return resetDate <= now
    }

    func activeWindow(for provider: ProviderUsage, kind: QuotaWindowKind) -> QuotaWindowSnapshot? {
        provider.windows
            .compactMap { window -> QuotaWindowSnapshot? in
                guard windowKind(for: window) == kind else { return nil }
                return snapshot(
                    for: window,
                    kind: kind,
                    fetchedAt: provider.fetchedAt,
                    limitReached: provider.limitReached
                )
            }
            .min(by: { $0.remaining < $1.remaining })
    }

    func getActive5hWindow(for provider: ProviderUsage) -> QuotaWindowSnapshot? {
        activeWindow(for: provider, kind: .fiveHour)
    }

    /// Model-scoped weekly caps (e.g. Claude's Fable cap), for rendering as
    /// their own rows below the regular windows. Excluded from 5h/weekly
    /// classification (notchWindowKind returns nil for them), so a spent cap
    /// can't lock the card — it reads as blocked on its own row only.
    func scopedWindows(for provider: ProviderUsage) -> [QuotaWindowSnapshot] {
        provider.windows
            .filter { $0.isScoped }
            .compactMap {
                snapshot(
                    for: $0,
                    kind: .weekly,
                    fetchedAt: provider.fetchedAt,
                    limitReached: provider.limitReached
                )
            }
    }

    func getActiveWeeklyWindow(for provider: ProviderUsage) -> QuotaWindowSnapshot? {
        activeWindow(for: provider, kind: .weekly)
    }

    func getCredits(for provider: ProviderUsage) -> Double? {
        if let creditsWindow = provider.windows.first(where: { $0.key == "ai_credits" }) {
            return creditsWindow.remaining
        }
        return nil
    }

    func creditsEnabled(for provider: ProviderUsage) -> Bool {
        guard let window = provider.windows.first(where: { $0.key == "ai_credits" }),
              (window.remaining ?? 0) > 0 else { return false }
        return provider.provider != "claude" || window.enabled == true
    }

    func poolTagRank(_ tag: String?) -> Int {
        switch tag {
        case "GEM": return 0
        case "EXT": return 1
        default: return 2
        }
    }

    /// A split-quota provider's tagged pools (Antigravity GEM/EXT), each
    /// pairing its 5-hour and weekly windows. Empty for everything else, so
    /// callers branch on `count >= 2` for split handling. Duplicate windows
    /// per tag+kind collapse to the least-remaining one, matching
    /// `activeWindow`'s conservative pick.
    func taggedPools(for provider: ProviderUsage) -> [PoolQuota] {
        guard provider.provider == "antigravity" else { return [] }
        var byTag: [String: (five: QuotaWindowSnapshot?, weekly: QuotaWindowSnapshot?)] = [:]
        for window in provider.windows {
            guard let kind = windowKind(for: window),
                  let snap = snapshot(
                    for: window,
                    kind: kind,
                    fetchedAt: provider.fetchedAt,
                    limitReached: provider.limitReached
                  ),
                  let tag = snap.tag else { continue }
            var entry = byTag[tag] ?? (nil, nil)
            switch kind {
            case .fiveHour:
                if snap.remaining < (entry.five?.remaining ?? .infinity) { entry.five = snap }
            case .weekly:
                if snap.remaining < (entry.weekly?.remaining ?? .infinity) { entry.weekly = snap }
            }
            byTag[tag] = entry
        }
        return byTag
            .map { PoolQuota(tag: $0.key, five: $0.value.five, weekly: $0.value.weekly) }
            .sorted { poolTagRank($0.tag) < poolTagRank($1.tag) }
    }

    func quotaState(for provider: ProviderUsage) -> ProviderQuotaState {
        let pools = taggedPools(for: provider)
        if pools.count >= 2 {
            return splitQuotaState(for: provider, pools: pools)
        }
        let fiveHour = getActive5hWindow(for: provider)
        let weekly = getActiveWeeklyWindow(for: provider)
        let bindingSnapshot: QuotaWindowSnapshot?
        let bindingLabel: BindingWindowLabel
        switch (fiveHour, weekly) {
        case let (five?, week?):
            if week.remaining < five.remaining {
                bindingSnapshot = week
                bindingLabel = .weekly
            } else {
                bindingSnapshot = five
                bindingLabel = .fiveHour
            }
        case let (five?, nil):
            bindingSnapshot = five
            bindingLabel = .fiveHour
        case let (nil, week?):
            bindingSnapshot = week
            bindingLabel = .weekly
        case (nil, nil):
            bindingSnapshot = nil
            bindingLabel = .fiveHour
        }
        let credits = getCredits(for: provider)
        let creditsEnabled = creditsEnabled(for: provider)
        let hasCredits = (credits ?? 0) > 0 && creditsEnabled
        let bindingRemaining = bindingSnapshot?.remaining ?? 100.0
        let percentWeekZero = weekly?.depleted ?? false
        let percentFiveZero = fiveHour?.depleted ?? false
        let percentageDepleted = percentWeekZero || percentFiveZero
        let roundedAtLimit = (weekly?.remaining ?? 1) <= 0 || (fiveHour?.remaining ?? 1) <= 0
        let nearLimit = provider.limitReached == false && roundedAtLimit
        let depleted = provider.limitReached ?? percentageDepleted
        let officialBindingIsWeekly = bindingLabel == .weekly
        let weekZero = depleted && (percentWeekZero || (!percentageDepleted && officialBindingIsWeekly))
        let fiveZero = depleted && (percentFiveZero || (!percentageDepleted && !officialBindingIsWeekly))
        // The window whose reset unblocks the provider (weekly outlasts the 5h).
        let lockCandidate = weekZero ? weekly : fiveHour
        let mode: ProviderDisplayMode
        if provider.status == "loading" {
            mode = .loading
        } else if provider.status != "ok" {
            mode = .error
        } else if depleted && hasCredits {
            mode = .credits
        } else if depleted {
            // Locked — but once the blocking window's predicted reset has
            // elapsed, show the transitional recovering state instead of a red
            // lock frozen on a finished countdown, until a poll confirms.
            mode = awaitingReset(lockCandidate) ? .recovering : .locked
        } else {
            mode = .healthy
        }

        let bindingTag = bindingSnapshot?.tag
        let lockReason: String
        let note: String?
        if mode == .loading {
            lockReason = L10n.tr("notch.loading.upper")
            note = nil
        } else if mode == .error {
            lockReason = L10n.tr("notch.error.upper")
            if let detail = provider.detail {
                if detail.contains("401") || detail.contains("refresh") || detail.contains("credential") {
                    note = L10n.tr("notch.auth_required")
                } else {
                    note = L10n.tr("notch.connection_offline")
                }
            } else {
                note = L10n.tr("notch.unavailable")
            }
        } else if mode == .healthy {
            lockReason = ""
            note = nearLimit ? L10n.tr("notch.near_limit") : nil
        } else if mode == .credits {
            if weekZero && fiveZero {
                lockReason = L10n.tr("notch.all_spent")
                note = L10n.tr("notch.quota_spent_reserve")
            } else if weekZero {
                lockReason = L10n.tr("notch.weekly_cap")
                note = L10n.tr("notch.weekly_cap_reserve")
            } else {
                lockReason = L10n.tr("notch.five_hour_empty")
                note = L10n.tr("notch.five_hour_spent_reserve")
            }
        } else if mode == .recovering {
            lockReason = L10n.tr("notch.recovering.upper")
            note = L10n.tr("notch.recovering.note")
        } else {
            if weekZero && fiveZero {
                lockReason = L10n.tr("notch.all_spent")
                note = L10n.tr("notch.all_quota_spent")
            } else if weekZero {
                lockReason = L10n.tr("notch.weekly_cap")
                note = L10n.tr("notch.weekly_cap_blocks")
            } else {
                lockReason = L10n.tr("notch.five_hour_empty")
                note = L10n.tr("notch.five_hour_spent")
            }
        }

        return ProviderQuotaState(
            mode: mode,
            bindingRemaining: bindingRemaining,
            bindingLabel: bindingLabel,
            bindingTag: bindingTag,
            fiveHour: fiveHour,
            weekly: weekly,
            credits: credits,
            creditsEnabled: creditsEnabled,
            lockReason: lockReason,
            note: note,
            lockResetKind: weekZero ? .weekly : .fiveHour,
            lockSnapshot: mode == .healthy ? nil : (weekZero ? weekly : fiveHour),
            blockedPools: []
        )
    }

    /// Split-pool state (Antigravity GEM + EXT): the pools lock independently,
    /// so the provider only reads as locked/credits when EVERY pool is blocked.
    /// A single blocked pool keeps the card `.healthy` — the binding excludes
    /// it (the ring headlines the surviving pool) and it is surfaced through
    /// `blockedPools` + `note` instead of a card-wide lock. When all pools are
    /// blocked, the lock countdown tracks the pool that recovers first.
    func splitQuotaState(for provider: ProviderUsage, pools: [PoolQuota]) -> ProviderQuotaState {
        let fiveHour = getActive5hWindow(for: provider)
        let weekly = getActiveWeeklyWindow(for: provider)
        let credits = getCredits(for: provider)
        let creditsEnabled = creditsEnabled(for: provider)
        let hasCredits = (credits ?? 0) > 0 && creditsEnabled
        let blocked = pools.filter { $0.blocked }
        let alive = pools.filter { !$0.blocked }
        let blockedInfo = blocked.map {
            PoolLockInfo(tag: $0.tag, kind: $0.blockingKind, snapshot: $0.blockingSnapshot)
        }

        // Recovery is the blocking window's reset; unanchored (idle) or
        // unparseable resets sort last so a real countdown wins the "first
        // back" slot.
        func recovery(_ pool: PoolQuota) -> Date {
            guard let snap = pool.blockingSnapshot, !snap.idle,
                  let date = QuotaFormatter.parseISODate(snap.window.resetsAt) else { return .distantFuture }
            return date
        }
        let earliest = blocked.min(by: { recovery($0) < recovery($1) })

        // Binding = least-remaining window among the pools the user can still
        // draw from (all pools when none is blocked, so it matches the old
        // cross-pool min in the healthy case).
        let bindingSource = alive.isEmpty ? pools : alive
        var bindingSnap: QuotaWindowSnapshot? = nil
        var bindingKind: QuotaWindowKind = .fiveHour
        for pool in bindingSource {
            for (kind, snap) in [(QuotaWindowKind.fiveHour, pool.five), (QuotaWindowKind.weekly, pool.weekly)] {
                guard let snap = snap else { continue }
                if snap.remaining < (bindingSnap?.remaining ?? .infinity) {
                    bindingSnap = snap
                    bindingKind = kind
                }
            }
        }

        let mode: ProviderDisplayMode
        if provider.status != "ok" {
            mode = .error
        } else if alive.isEmpty {
            if hasCredits {
                mode = .credits
            } else {
                // Every pool is spent; if the earliest-recovering pool's
                // predicted reset has already passed, show recovering until a
                // poll confirms rather than a red lock on a finished countdown.
                mode = awaitingReset(earliest?.blockingSnapshot) ? .recovering : .locked
            }
        } else {
            mode = .healthy
        }

        let lockReason: String
        let note: String?
        switch mode {
        case .loading:
            // Unreachable in practice (a loading placeholder has no tagged
            // pools, so it never takes the split path), covered for exhaustiveness.
            lockReason = L10n.tr("notch.loading.upper")
            note = nil
        case .error:
            lockReason = L10n.tr("notch.error.upper")
            if let detail = provider.detail {
                if detail.contains("401") || detail.contains("refresh") || detail.contains("credential") {
                    note = L10n.tr("notch.auth_required")
                } else {
                    note = L10n.tr("notch.connection_offline")
                }
            } else {
                note = L10n.tr("notch.unavailable")
            }
        case .healthy:
            lockReason = ""
            if let b = blocked.first {
                let aliveTags = alive.map { $0.tag }.joined(separator: "/")
                note = b.weekZero
                    ? L10n.tr("notch.pool.weekly_cap", b.tag, aliveTags)
                    : L10n.tr("notch.pool.five_hour_spent", b.tag, aliveTags)
            } else {
                note = nil
            }
        case .credits:
            lockReason = L10n.tr("notch.pool.back_first", earliest?.tag ?? "")
            note = L10n.tr("notch.pool.all_spent_reserve")
        case .locked:
            lockReason = L10n.tr("notch.pool.back_first", earliest?.tag ?? "")
            note = L10n.tr("notch.pool.all_spent")
        case .recovering:
            lockReason = L10n.tr("notch.recovering.upper")
            note = L10n.tr("notch.recovering.note")
        }

        return ProviderQuotaState(
            mode: mode,
            bindingRemaining: bindingSnap?.remaining ?? 100.0,
            bindingLabel: bindingKind == .weekly ? .weekly : .fiveHour,
            bindingTag: bindingSnap?.tag,
            fiveHour: fiveHour,
            weekly: weekly,
            credits: credits,
            creditsEnabled: creditsEnabled,
            lockReason: lockReason,
            note: note,
            lockResetKind: earliest?.blockingKind ?? .fiveHour,
            lockSnapshot: mode == .healthy ? nil : earliest?.blockingSnapshot,
            blockedPools: blockedInfo
        )
    }

    func isSubscription(for provider: ProviderUsage) -> Bool {
        if let label = provider.accountLabel,
           !label.trimmingCharacters(in: .whitespaces).isEmpty {
            return true
        }
        return provider.windows.contains { window in
            let key = (window.key ?? "").lowercased()
            return key.contains("5h") || key.contains("7d") || key.contains("weekly")
        }
    }

    func timerString(for snapshot: QuotaWindowSnapshot?) -> String {
        if let snapshot = snapshot, snapshot.idle {
            let label = snapshot.kind == .fiveHour ? "5h" : "7d"
            return QuotaFormatter.windowLengthText(windowMinutes: snapshot.window.windowMinutes, fallbackLabel: label)
        }
        if let snapshot = snapshot,
           let date = QuotaFormatter.parseISODate(snapshot.window.resetsAt) {
            let diff = date.timeIntervalSince(now)
            let dur = QuotaFormatter.formatDuration(diff)
            return dur.secondary.isEmpty ? dur.primary : "\(dur.primary) \(dur.secondary)"
        }
        return "now"
    }

    /// One-unit countdown for the always-on collapsed strip. Peek and expanded
    /// keep the full two-unit value; this compact form preserves shoulder space
    /// beside the camera while still communicating the useful magnitude.
    func compactTimerString(for snapshot: QuotaWindowSnapshot?) -> String {
        if let snapshot = snapshot, snapshot.idle {
            let label = snapshot.kind == .fiveHour ? "5h" : "7d"
            return QuotaFormatter.windowLengthText(
                windowMinutes: snapshot.window.windowMinutes,
                fallbackLabel: label
            )
        }
        if let snapshot = snapshot,
           let date = QuotaFormatter.parseISODate(snapshot.window.resetsAt) {
            let diff = date.timeIntervalSince(now)
            if diff > 0, diff < 60 { return "<1m" }
            return QuotaFormatter.formatDuration(diff).primary
        }
        return "now"
    }

    func get5hResetTimer(for provider: ProviderUsage) -> String {
        timerString(for: getActive5hWindow(for: provider))
    }

    func getWeeklyResetTimer(for provider: ProviderUsage) -> String {
        timerString(for: getActiveWeeklyWindow(for: provider))
    }

    func resetsAtDate(from isoString: String?) -> Date? {
        QuotaFormatter.parseISODate(isoString)
    }

    func windowLengthText(_ snapshot: QuotaWindowSnapshot) -> String {
        let label = snapshot.kind == .fiveHour ? "5h" : "7d"
        return QuotaFormatter.windowLengthText(windowMinutes: snapshot.window.windowMinutes, fallbackLabel: label)
    }

    func formatDuration(_ seconds: TimeInterval) -> (primary: String, secondary: String) {
        QuotaFormatter.formatDuration(seconds)
    }

    func formatTokenCount(_ val: Int) -> String {
        if val >= 1_000_000 {
            return String(format: "%.1fM", Double(val) / 1_000_000.0).replacingOccurrences(of: ".0", with: "")
        }
        if val >= 1_000 {
            return "\(val / 1_000)k"
        }
        return "\(val)"
    }

    /// How badly a provider wants the user's attention — lower sorts first.
    /// Broken and blocked providers outrank any percentage; healthy providers
    /// rank by remaining quota. Drives the collapsed strip's "lowest" style and
    /// the slot the peek bubble points at before the pointer has picked one.
    func attentionRank(for provider: ProviderUsage) -> Double {
        let state = quotaState(for: provider)
        switch state.mode {
        case .error:
            return -3
        case .locked:
            return -2
        case .recovering:
            // Still critical (depleted) but on the verge of recovering — keep it
            // in the attention slot alongside locked.
            return -2
        case .credits:
            return -1
        case .healthy:
            return state.bindingRemaining
        case .loading:
            // Never let a not-yet-fetched provider win the "lowest" slot over
            // real data; it still shows when it's the only provider.
            return Double.greatestFiniteMagnitude
        }
    }
}
