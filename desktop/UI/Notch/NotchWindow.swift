import AppKit
import Foundation
import SwiftUI

// Internal (not file-private) so NotchIslandView+Expanded can read the page
// height it reports.
struct ExpandedPageHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

// Natural width of the peek tray's segment row, reported by the hidden
// measuring twin in NotchIslandView+Peek. Drives the tray width on non-notched
// displays, where there is no physical notch width to derive the tray from and
// the old fixed per-count widths clipped long content (timers + pool tags).
struct PeekContentWidthKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

// Same idea for the collapsed strip: on non-notched displays the pill hugs the
// measured row width instead of the fixed 180/280 constants, which left wide
// dead margins around the centered content.
struct CollapsedContentWidthKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}


// Quota value types (QuotaWindowKind, QuotaWindowSnapshot, ProviderQuotaState,
// …) and the math that produces them now live in NotchQuotaPresenter.swift so
// they can be unit-tested without any UI.

// MARK: - Window classification (single source of truth)
// Free functions so both the controller (window sizing) and the view (layout)
// classify windows the same way. The view's windowKind/windowTag methods
// delegate here.
func notchWindowKind(_ window: QuotaWindow) -> QuotaWindowKind? {
    // Scoped sub-limits never classify as 5h/weekly: activeWindow picks the
    // least-remaining window per kind, so a spent model cap (Fable at 0%)
    // would otherwise hijack the weekly slot and lock the whole card. They
    // render as their own rows instead (scopedWindows / scopedQuotaRow).
    if window.isScoped { return nil }
    let hay = "\((window.key ?? "").lowercased()) \((window.label ?? "").lowercased())"
    if hay.contains("5h") || hay.contains("5-hour") || hay.contains("5 hour") {
        return .fiveHour
    }
    if hay.contains("7d") || hay.contains("weekly") || hay.contains("week") {
        return .weekly
    }
    return nil
}

func notchWindowTag(_ window: QuotaWindow) -> String? {
    let hay = "\((window.key ?? "").lowercased()) \((window.label ?? "").lowercased())"
    if hay.contains("external") || hay.contains("claude/gpt") {
        return "EXT"
    }
    if hay.contains("gemini") {
        return "GEM"
    }
    return nil
}

/// True when exactly one provider is connected and it meters two tagged
/// 5-hour pools (Antigravity GEM + EXT). With a single provider the expanded
/// panel has the full width to itself, so the two pools get their own
/// side-by-side rings (solo split) instead of one ring + stacked rows. The
/// controller branches on this for window width; the view for layout.
func notchIsSoloSplit(_ providers: [ProviderUsage]) -> Bool {
    guard providers.count == 1, let p = providers.first, p.provider == "antigravity" else { return false }
    let pools = p.windows.filter { notchWindowKind($0) == .fiveHour && notchWindowTag($0) != nil && $0.usedPercent != nil }
    return pools.count >= 2
}

/// Expanded panel width. Solo split needs the 2-provider width so each pool
/// column has room for a full ring + detail band; everything else keeps the
/// existing per-count widths.
func notchExpandedWidth(providers: [ProviderUsage]) -> CGFloat {
    let count = max(1, providers.count)
    if count == 1 { return notchIsSoloSplit(providers) ? 436 : 300 }
    return count == 3 ? 564 : 436
}

/// Layout slot count: how many side-by-side units the collapsed/peek surfaces
/// size and center for. Solo split occupies two slots (GEM + EXT) even though
/// there's a single provider, so the tray gets a 2-unit silhouette with room
/// to flank both pools around the notch.
func notchLayoutCount(_ providers: [ProviderUsage]) -> Int {
    notchIsSoloSplit(providers) ? 2 : max(1, providers.count)
}


// MARK: - SwiftUI Main Notch Island View
struct NotchIslandView: View {
    @ObservedObject var model: NotchDataModel
    weak var controller: NotchWindowController?
    
    /// Neon purple of the backend-upgrade indicator; matches the menu bar dot
    /// drawn in AppDelegate+Rendering.
    static let upgradeTint = Color(red: 0.58, green: 0.38, blue: 0.95)

    /// Current time, updated every second when the notch is visible.
    /// Reading `now` inside time-display functions makes them proper @State
    /// dependencies so SwiftUI re-evaluates them on every tick.
    @State var now: Date = Date()
    let timer = Timer.publish(every: 1.0, on: .main, in: .common).autoconnect()
    
    var appDelegate: AppDelegate {
        return NSApp.delegate as! AppDelegate
    }
    
    func providerDisplayName(for provider: String) -> String {
        return PROVIDER_NAMES[provider] ?? provider
    }

    /// Short, display-friendly plan tier for the header chip. Antigravity
    /// reports the full marketing name ("Google AI Pro"); strip the brand prefix
    /// so its chip stays as compact as the "Pro"/"Plus"/"Max" chips of the other
    /// providers. The raw `account_label` is left untouched for price matching.
    func planTierLabel(_ raw: String) -> String {
        var tier = raw.trimmingCharacters(in: .whitespaces)
        for prefix in ["Google AI ", "Google "] where tier.lowercased().hasPrefix(prefix.lowercased()) {
            tier = String(tier.dropFirst(prefix.count))
            break
        }
        return tier.capitalized
    }
    
    // Dynamic animatable properties
    var targetWidth: CGFloat {
        let count = notchLayoutCount(model.providers)
        switch model.notchState {
        case .collapsed:
            return model.collapsedWidth
        case .peek:
            if model.hasNotch {
                // Keep the +80 breathing room only where the inline content
                // actually needs it (3 providers, single-line timers). Every
                // other case hugs the collapsed width so narrow content isn't
                // lost in a notch-wide tray — but never goes narrower than it.
                let bonus: CGFloat = (count == 3 && model.peekReset != "both") ? 80 : 0
                return model.collapsedWidth + bonus
            } else {
                return model.peekWidthNoNotch(count: count)
            }
        case .expanded:
            return notchExpandedWidth(providers: model.providers)
        }
    }

    var targetHeight: CGFloat {
        switch model.notchState {
        case .collapsed:
            return model.collapsedHeight
        case .peek:
            return model.peekHeight
        case .expanded:
            return model.expandedCardHeight
        }
    }
    
    var targetCornerRadius: CGFloat {
        switch model.notchState {
        case .collapsed:
            return 16
        case .peek:
            return 20
        case .expanded:
            return 30
        }
    }
    
    var backgroundCard: some View {
        ZStack {
            // Frosted glass background
            VisualEffectView(material: .hudWindow, blendingMode: .withinWindow)
                .opacity(model.notchState == .expanded ? 1.0 : 0.0)
            
            // Black color layer
            Color.black
                .opacity(model.notchState == .expanded ? 0.76 : 1.0)
        }
        .frame(width: targetWidth, height: targetHeight)
        .clipShape(BottomRoundedRectangle(cornerRadius: targetCornerRadius))
        .overlay(
            BottomRoundedRectangle(cornerRadius: targetCornerRadius)
                .stroke(Color.white.opacity(model.notchState == .expanded ? 0.16 : 0.0), lineWidth: 0.5)
        )
    }
    
    var body: some View {
        ZStack(alignment: .top) {
            if model.isUpgradingBackend {
                // Every upgrade-glow layer sits BEHIND the opaque card, so only
                // the part outside the silhouette ever shows. Drawing any of
                // them on top would expose the seam between the physical notch
                // and the drawn strip: a centered stroke keeps its inner half
                // on the drawn wings (real pixels) but loses it over the
                // hardware cutout (no pixels), so the light appears to switch
                // Z-layers as it crosses the boundary. Line widths are doubled
                // where a crisp edge is wanted, since only the outer half is
                // visible. Needs the halo window margin — see
                // NotchWindowController.setUpgradingBackend.
                ZStack {
                    // Soft outer halo.
                    LoadingSweep(
                        cornerRadius: targetCornerRadius,
                        width: targetWidth,
                        height: targetHeight,
                        tint: Self.upgradeTint
                    )
                    // Constant faint rim so the whole outline reads as "active"
                    // even where the comet currently isn't (visible: ~0.8pt).
                    BottomRoundedBorder(cornerRadius: targetCornerRadius)
                        .stroke(Self.upgradeTint.opacity(0.3), lineWidth: 1.6)
                        .frame(width: targetWidth, height: targetHeight)
                    // Crisp comet line hugging the edge (visible: ~1.6pt).
                    LoadingSweep(
                        cornerRadius: targetCornerRadius,
                        width: targetWidth,
                        height: targetHeight,
                        tint: Self.upgradeTint,
                        lineWidth: 3.2,
                        blur: 0
                    )
                }
            }

            // Animatable background
            backgroundCard
            
            // UI Content Layers
            VStack(spacing: 0) {
                switch model.notchState {
                case .collapsed:
                    collapsedView
                        .transition(.opacity)
                case .peek:
                    peekView
                        .transition(.opacity)
                case .expanded:
                    expandedView
                        .transition(.asymmetric(
                            insertion: .scale(scale: 0.93, anchor: .top).combined(with: .opacity),
                            removal: .opacity
                        ))
                }
            }
            .frame(width: targetWidth, height: targetHeight, alignment: .top)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        // Collapsed/peek: only the visible card is tappable so taps on the
        // transparent overshoot halo don't expand the notch (uses the live
        // target size so it tracks the spring, including overshoot). Expanded:
        // the whole window is tappable so a tap on the panel's transparent
        // margin still collapses it (relying on resign-key passthrough there is
        // unreliable — the click doesn't always hand key focus to another app).
        .contentShape(
            model.notchState == .expanded
                ? CardHitShape(width: .infinity, height: .infinity)
                : CardHitShape(width: targetWidth, height: targetHeight)
        )
        .ignoresSafeArea()
        .onTapGesture {
            guard !model.isUpgradingBackend else { return }
            controller?.toggleExpand()
        }
        .onChange(of: model.page) { _ in
            if model.notchState == .expanded {
                controller?.repositionWindow()
            }
        }
        // Tick `now` every second while the notch is visible. Because `now` is
        // a @State dependency, SwiftUI re-evaluates body — and every downstream
        // computed property — on each tick, so countdown displays update live.
        .onReceive(timer) { date in
            guard model.notchState != .collapsed else { return }
            now = date
        }
    }
    
    // MARK: - Quota Utility Helpers (delegated to NotchQuotaPresenter)
    // The view keeps these names so the rendering code reads naturally; the
    // actual math is SwiftUI-independent and unit-testable — see
    // NotchQuotaPresenter.swift. A fresh presenter is created per render keyed
    // off the ticking `now`.
    var quota: NotchQuotaPresenter { NotchQuotaPresenter(now: now) }

    func windowKind(for window: QuotaWindow) -> QuotaWindowKind? { quota.windowKind(for: window) }
    func windowTag(for window: QuotaWindow) -> String? { quota.windowTag(for: window) }
    func snapshot(for window: QuotaWindow, kind: QuotaWindowKind, fetchedAt: String?) -> QuotaWindowSnapshot? {
        quota.snapshot(for: window, kind: kind, fetchedAt: fetchedAt)
    }
    func activeWindow(for provider: ProviderUsage, kind: QuotaWindowKind) -> QuotaWindowSnapshot? {
        quota.activeWindow(for: provider, kind: kind)
    }
    func getActive5hWindow(for provider: ProviderUsage) -> QuotaWindowSnapshot? { quota.getActive5hWindow(for: provider) }
    func getActiveWeeklyWindow(for provider: ProviderUsage) -> QuotaWindowSnapshot? { quota.getActiveWeeklyWindow(for: provider) }
    func scopedWindows(for provider: ProviderUsage) -> [QuotaWindowSnapshot] { quota.scopedWindows(for: provider) }
    func getCredits(for provider: ProviderUsage) -> Double? { quota.getCredits(for: provider) }
    func quotaState(for provider: ProviderUsage) -> ProviderQuotaState { quota.quotaState(for: provider) }
    func isSubscription(for provider: ProviderUsage) -> Bool { quota.isSubscription(for: provider) }
    func timerString(for snapshot: QuotaWindowSnapshot?) -> String { quota.timerString(for: snapshot) }
    func get5hResetTimer(for provider: ProviderUsage) -> String { quota.get5hResetTimer(for: provider) }
    func getWeeklyResetTimer(for provider: ProviderUsage) -> String { quota.getWeeklyResetTimer(for: provider) }
    func resetsAtDate(from isoString: String?) -> Date? { quota.resetsAtDate(from: isoString) }
    func windowLengthText(_ snapshot: QuotaWindowSnapshot) -> String { quota.windowLengthText(snapshot) }
    func formatDuration(_ seconds: TimeInterval) -> (primary: String, secondary: String) { quota.formatDuration(seconds) }
    func formatTokenCount(_ val: Int) -> String { quota.formatTokenCount(val) }
}

/// A comet-like light running along the notch border. The phase is derived
/// from the wall clock (not view state), so multiple instances — the blurred
/// halo behind the card and the crisp edge stroke on top of it — animate in
/// lockstep for free.
struct LoadingSweep: View {
    let cornerRadius: CGFloat
    let width: CGFloat
    let height: CGFloat
    let tint: Color
    var lineWidth: CGFloat = 5.5
    var blur: CGFloat = 3.0

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            let rotation = (t * 90).truncatingRemainder(dividingBy: 360)
            BottomRoundedBorder(cornerRadius: cornerRadius)
                .stroke(
                    AngularGradient(
                        gradient: Gradient(stops: [
                            .init(color: .clear, location: 0.00),
                            .init(color: tint.opacity(0.0), location: 0.45),
                            .init(color: tint.opacity(1.0), location: 0.72),
                            .init(color: .white.opacity(1.0), location: 0.90),
                            .init(color: tint.opacity(0.0), location: 1.00),
                        ]),
                        center: .center,
                        angle: .degrees(rotation)
                    ),
                    lineWidth: lineWidth
                )
                .blur(radius: blur)
                .frame(width: width, height: height)
        }
    }
}


