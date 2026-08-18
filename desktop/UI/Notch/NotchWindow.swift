import AppKit
import Foundation
import SwiftUI

// Internal (not file-private) so NotchIslandView+Expanded can read the page
// heights it reports. Keyed by page index: the expanded island sizes to the
// CURRENT page's natural height (like the design, whose hidden page is
// display:none and contributes nothing), not the taller of the two.
struct ExpandedPageHeightsKey: PreferenceKey {
    static var defaultValue: [Int: CGFloat] = [:]

    static func reduce(value: inout [Int: CGFloat], nextValue: () -> [Int: CGFloat]) {
        value.merge(nextValue(), uniquingKeysWith: max)
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
    // Duration-based fallback: a 30-day free-tier window carries key "30d"
    // which the text checks above don't recognise.
    if let m = window.windowMinutes, m > 0 {
        return m <= 720 ? .fiveHour : .weekly
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

func notchWindowShortTag(_ window: QuotaWindow?, defaultTag: String = "WK") -> String {
    guard let w = window else { return defaultTag }
    let m = w.windowMinutes
    let raw = ((w.label ?? "") + " " + (w.key ?? "")).lowercased()
    if m == 300 || raw.contains("5h") || raw.contains("5-hour") {
        return "5H"
    }
    if m == 10080 || raw.contains("7d") || raw.contains("weekly") || raw.contains("week") {
        return "WK"
    }
    if let m = m, m > 0 {
        return QuotaFormatter.windowLengthText(windowMinutes: m, fallbackLabel: nil).uppercased()
    }
    return defaultTag
}

func notchWindowShortTag(_ snapshot: QuotaWindowSnapshot?, defaultTag: String = "WK") -> String {
    notchWindowShortTag(snapshot?.window, defaultTag: defaultTag)
}

func notchWindowRowTitle(_ snapshot: QuotaWindowSnapshot?) -> String {
    guard let snap = snapshot else { return "" }
    let m = snap.window.windowMinutes
    let raw = ((snap.window.label ?? "") + " " + (snap.window.key ?? "")).lowercased()
    if m == 300 || raw.contains("5h") || raw.contains("5-hour") {
        return L10n.tr("notch.row.5h")
    }
    if m == 10080 || raw.contains("7d") || raw.contains("weekly") || raw.contains("week") {
        return L10n.tr("notch.row.weekly")
    }
    if let m = m, m > 0 {
        if m % 1440 == 0 {
            return L10n.tr("notch.row.days", m / 1440)
        }
        if m % 60 == 0 {
            return L10n.tr("notch.row.hours", m / 60)
        }
        return "\(m)m"
    }
    return snap.kind == .fiveHour ? L10n.tr("notch.row.5h") : L10n.tr("notch.row.weekly")
}

func notchRingSubtitle(for snapshot: QuotaWindowSnapshot) -> String {
    let m = snapshot.window.windowMinutes
    let raw = ((snapshot.window.label ?? "") + " " + (snapshot.window.key ?? "")).lowercased()
    let is5h = m == 300 || raw.contains("5h") || raw.contains("5-hour")
    let isWeekly = m == 10080 || raw.contains("7d") || raw.contains("weekly") || raw.contains("week")

    if snapshot.idle {
        if is5h {
            return L10n.tr("notch.five_hour_window")
        }
        if isWeekly {
            return "WEEKLY WINDOW"
        }
        let length = QuotaFormatter.windowLengthText(windowMinutes: snapshot.window.windowMinutes, fallbackLabel: nil)
        return "\(length.uppercased()) WINDOW"
    } else {
        if is5h {
            return L10n.tr("notch.five_hour_left")
        }
        if isWeekly {
            return L10n.tr("notch.weekly_left")
        }
        let length = QuotaFormatter.windowLengthText(windowMinutes: snapshot.window.windowMinutes, fallbackLabel: nil)
        return "\(length.uppercased()) LEFT"
    }
}

func notchSoloCardNonFiveHourTitle(for snapshot: QuotaWindowSnapshot?) -> String {
    guard let snapshot = snapshot else { return L10n.tr("notch.card.week_resets_in") }
    let m = snapshot.window.windowMinutes
    let raw = ((snapshot.window.label ?? "") + " " + (snapshot.window.key ?? "")).lowercased()
    let isWeekly = m == 10080 || raw.contains("7d") || raw.contains("weekly") || raw.contains("week")
    if isWeekly {
        return snapshot.idle ? "WEEKLY WINDOW" : L10n.tr("notch.card.week_resets_in")
    }
    if snapshot.idle {
        let len = QuotaFormatter.windowLengthText(windowMinutes: snapshot.window.windowMinutes, fallbackLabel: nil).uppercased()
        return "\(len) WINDOW"
    }
    if let m = m, m > 0, m % 1440 == 0 {
        return L10n.tr("notch.card.days_resets_in", m / 1440)
    }
    return L10n.tr("notch.card.week_resets_in")
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

/// True when exactly one healthy provider is connected and it meters a weekly
/// window (single Claude/Codex). With the panel to itself, the lone centred
/// ring gives way to the solo card: ring on the left, an info column (binding
/// window's reset hero, weekly bar, Today/Cache/Reserve foot) spending the
/// freed width on the right. A 5-hour window is NOT required: Codex drops its
/// 5-hour window while temporarily uncapped, and the card then headlines the
/// weekly reset instead — falling back to the narrow single-ring column just
/// for that state would flip the whole layout style whenever the cap toggles.
/// Requires status "ok": error/loading keep the narrow single card, whose
/// full-ring red/pending treatment reads clearer than a card wrapped around
/// broken data.
func notchIsSoloDualWindow(_ providers: [ProviderUsage]) -> Bool {
    guard providers.count == 1, let p = providers.first, p.status == "ok" else { return false }
    if notchIsSoloSplit(providers) { return false }
    return p.windows.contains { window in
        window.usedPercent != nil && notchWindowKind(window) == .weekly
    }
}

/// The collapsed default for one subscription provider can use the physical
/// notch as a natural divider: 5-hour on the left, weekly on the right. Keep
/// this separate from the expanded-card predicate because the compact glance
/// also represents Codex's temporarily uncapped 5-hour window as `5H ∞`.
func notchUsesSoloDualWindowGlance(_ providers: [ProviderUsage]) -> Bool {
    guard providers.count == 1, let provider = providers.first, provider.status == "ok" else {
        return false
    }
    if notchIsSoloSplit(providers) { return false }
    let hasFiveHour = provider.windows.contains {
        $0.usedPercent != nil && notchWindowKind($0) == .fiveHour
    }
    let hasWeekly = provider.windows.contains {
        $0.usedPercent != nil && notchWindowKind($0) == .weekly
    }
    return hasWeekly && (hasFiveHour || isCodexFiveHourTemporarilyUncapped(provider))
}

/// The two-provider peek can give the short and long quota windows distinct
/// visual jobs: a 5H ring in the content row and a WK rail following the
/// island's lower corner. Keep the richer treatment to healthy, comparable
/// provider data; loading/error cards and providers without a weekly window
/// continue through the generic peek renderer.
func notchUsesDualAgentArcPeek(_ providers: [ProviderUsage]) -> Bool {
    guard providers.count == 2 else { return false }
    return providers.allSatisfy { provider in
        guard provider.status == "ok" else { return false }
        let hasFiveHour = provider.windows.contains {
            $0.usedPercent != nil && notchWindowKind($0) == .fiveHour
        }
        let hasWeekly = provider.windows.contains {
            $0.usedPercent != nil && notchWindowKind($0) == .weekly
        }
        return hasWeekly && (hasFiveHour || isCodexFiveHourTemporarilyUncapped(provider))
    }
}

/// Expanded panel width. Solo split needs the 2-provider width so each pool
/// column has room for a full ring + detail band. A detailed solo dual-window
/// card needs extra width for its ring + info column, while Compact deliberately
/// restores the pre-card 300pt single-column width used by the multi-provider
/// component. Everything with 2+ providers keeps its existing width.
func notchExpandedWidth(providers: [ProviderUsage], expandedStyle: String = "detailed") -> CGFloat {
    let count = max(1, providers.count)
    if count == 1 {
        if notchIsSoloSplit(providers) { return 436 }
        return notchIsSoloDualWindow(providers) && expandedStyle == "detailed" ? 384 : 300
    }
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

    // Geometry uses a spring, but opacity must not inherit that spring: a
    // spring can keep a removing layer alive after the card has visually
    // collapsed. Content enters just behind the growing silhouette and exits
    // quickly enough to stay inside the shrinking one.
    static let contentInsertionAnimation = Animation.easeOut(duration: 0.13).delay(0.045)
    static let contentRemovalAnimation = Animation.linear(duration: 0.055)

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
                // Collapsed width (+ the 3-provider bonus) is the floor;
                // content that measures wider grows the tray instead of
                // clipping at the window edges (peekWidthWithNotch).
                return model.peekWidthWithNotch(collapsedBase: model.collapsedWidth, count: count)
            } else {
                return model.peekWidthNoNotch(count: count)
            }
        case .expanded:
            return notchExpandedWidth(providers: model.providers, expandedStyle: model.expandedStyle)
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
                        .transition(.asymmetric(
                            insertion: .opacity.animation(Self.contentInsertionAnimation),
                            removal: .opacity.animation(Self.contentRemovalAnimation)
                        ))
                case .peek:
                    peekView
                        .transition(.asymmetric(
                            insertion: .opacity.animation(Self.contentInsertionAnimation),
                            removal: .opacity.animation(Self.contentRemovalAnimation)
                        ))
                case .expanded:
                    expandedView
                        .transition(.asymmetric(
                            insertion: .scale(scale: 0.93, anchor: .top)
                                .combined(with: .opacity)
                                .animation(Self.contentInsertionAnimation),
                            removal: .opacity.animation(Self.contentRemovalAnimation)
                        ))
                }
            }
            .frame(width: targetWidth, height: targetHeight, alignment: .top)
            // The state-specific view is replaced immediately while the card
            // geometry animates. Without this live silhouette clip, the old
            // content can draw for a frame outside a card that has already
            // shrunk — perceived as a text/rail afterimage.
            .clipShape(BottomRoundedRectangle(cornerRadius: targetCornerRadius))
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
        // No reposition on page flips: the window frame does not depend on the
        // page. `expandedCardHeight` is derived from `expandedPageHeight`, whose
        // only writer (syncExpandedHeight) already repositions when it actually
        // changes — and both pages share one locked height anyway, so a flip
        // normally resizes nothing. Repositioning here spent a 0.16s animated
        // setFrame(display:) redrawing the blur and window shadow of an
        // unchanged frame, on top of the page cross-fade.
        //
        // Tick `now` every second. The collapsed island can itself show an
        // exhausted quota's reset countdown, so skipping ticks while collapsed
        // leaves that always-visible value frozen until another model update.
        // Because `now` is a @State dependency, SwiftUI re-evaluates body — and
        // every downstream computed property — on each tick.
        .onReceive(timer) { date in
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
    func awaitingReset(_ snapshot: QuotaWindowSnapshot?) -> Bool { quota.awaitingReset(snapshot) }
    func isSubscription(for provider: ProviderUsage) -> Bool { quota.isSubscription(for: provider) }
    func timerString(for snapshot: QuotaWindowSnapshot?) -> String { quota.timerString(for: snapshot) }
    func compactTimerString(for snapshot: QuotaWindowSnapshot?) -> String { quota.compactTimerString(for: snapshot) }
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
