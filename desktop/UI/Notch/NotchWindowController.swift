import AppKit
import Foundation
import SwiftUI

// MARK: - Private SkyLight (CGS) Space APIs
// Used to read the current Space type per display (type 4 == native fullscreen).
// This is the only reliable signal for "another app is in native fullscreen":
// window geometry alone can't tell native fullscreen from a maximized window on
// a notched display. Same private APIs used by AltTab, Ice, etc.; fine for a
// directly-distributed (non-App-Store) app.
private typealias CGSConnectionID = UInt32
@_silgen_name("CGSMainConnectionID")
private func CGSMainConnectionID() -> CGSConnectionID
@_silgen_name("CGSCopyManagedDisplaySpaces")
private func CGSCopyManagedDisplaySpaces(_ cid: CGSConnectionID) -> CFArray

// MARK: - Notch States
enum NotchState {
    case collapsed
    case peek
    case expanded
}

struct ProviderHistoryStats: Equatable {
    let tokens: Int
    let input: Int
    let output: Int
    let cacheCreation: Int
    let cacheRead: Int
    let cost: Double
}

/// One day of one provider's usage in the trailing 14-day series.
///
/// `total` is the measure the bars are drawn from and the one the headline
/// figure uses, so a bar and the big number above it always agree. `cost` is
/// what that day was worth at API rates — on a subscription it is notional,
/// the same "API value" the today tiles report, not money spent.
struct ProviderDayUsage: Equatable {
    let total: Int
    let cost: Double

    static let empty = ProviderDayUsage(total: 0, cost: 0)
}

// MARK: - Notch Data Model
class NotchDataModel: ObservableObject {
    @Published var isUpgradingBackend: Bool = false
    @Published var providers: [ProviderUsage] = []
    @Published var justGranted: Int = 0
    @Published var todayStats: [String: ProviderHistoryStats] = [:]
    // Per-provider daily usage for the trailing 14 local days (oldest → today),
    // keyed by lowercased provider: the last 7 draw the usage page's week
    // chart, the 7 before them anchor its week-over-week delta. Empty when the
    // backend predates by_provider_day, and the chart simply doesn't render.
    @Published var dailyTokens: [String: [ProviderDayUsage]] = [:]
    // Provider-specific peak local hour across the trailing seven days. The
    // backend counts turns rather than tokens so one unusually large request
    // cannot distort the user's habitual busy time.
    @Published var peakHours: [String: Int] = [:]
    // False until the first successful history fetch: the pages use this to
    // show a loading placeholder instead of misreading "not fetched yet" as
    // a real zero-usage day.
    @Published var historyLoaded: Bool = false
    @Published var pendingUpdateVersion: String?
    @Published var notchState: NotchState = .collapsed
    @Published var page: Int = 0
    // Natural height of each expanded page (keyed by page index), reported by
    // the pages' measurement backgrounds. expandedPageHeight tracks the entry
    // for the CURRENT page so the island grows/shrinks per page.
    @Published var pageHeights: [Int: CGFloat] = [:]
    @Published var hasNotch: Bool = false
    @Published var safeAreaTop: CGFloat = 0
    @Published var silentStyle: String = "all"
    // The gauge SHAPE drawn in the collapsed strip and peek tray: "ring"
    // (progress ring, default), "liquid" (liquid-filled circle) or "dot" (the
    // classic glowing dot). Orthogonal to silentStyle, which decides WHAT is
    // shown; this decides HOW.
    @Published var gaugeStyle: String = "ring"
    // Where each quota's number lives: "beside" the gauge (default), "inside"
    // the ring/liquid circle, or "hidden". The dot can't hold a number, so it
    // ignores "inside"; exhausted quotas always surface their reset countdown.
    @Published var gaugeValue: String = "beside"
    // Single-provider expanded quota layout: "compact" reuses the same
    // one-ring column used by multi-provider panels; "detailed" uses the
    // richer full-width solo card. Multi-provider panels ignore this setting.
    @Published var expandedStyle: String = "detailed"
    // Which provider slot the peek's callout bubble is pointing at, as an
    // index into `providers`. Set on entry (see peekFocusIndexUnderPointer) and
    // then by whichever slot the pointer is nearest, so the bubble is never
    // absent while peek is open — nil only outside peek.
    @Published var peekFocusIndex: Int? = nil
    /// Gates the bubble tail's slide. False while peek opens, so the callout
    /// appears already pointing at its slot; the first pointer-driven change
    /// turns it on. Without the gate the tail travels in on every open, which
    /// reads as the bubble hunting for its target.
    @Published var peekBubbleTailAnimates: Bool = false
    /// Centre x of each provider's slot in the strip, in the card's coordinate
    /// space, reported by the collapsed row (see NotchProviderAnchorsKey).
    @Published var providerAnchors: [Int: CGFloat] = [:]
    /// Height of the callout actually on screen, reported by the bubble layer
    /// (see PeekBubbleHeightKey). Zero before the first one has been laid out.
    @Published var peekBubbleVisibleHeight: CGFloat = 0
    @Published var notchWidth: CGFloat = 0
    @Published var notchLeft: CGFloat = 0
    @Published var notchRight: CGFloat = 0
    @Published var collapsedWidth: CGFloat = 180
    @Published var expandedPageHeight: CGFloat = 232
    // Natural width of the collapsed row, measured the same way (see
    // CollapsedContentWidthKey / NotchIslandView+Collapsed).
    @Published var collapsedContentWidth: CGFloat = 0



    // Collapsed pill width on a non-notched display: hugs the measured row
    // (which carries its own 17pt side paddings), floored so the pill keeps a
    // sensible silhouette against its 16pt corner radius.
    var collapsedWidthNoNotch: CGFloat {
        guard collapsedContentWidth > 1 else { return 180 }
        return max(120, collapsedContentWidth)
    }

    // Narrow panels (1–2 providers) can't route the header around the physical
    // notch the way the wide 3-provider panel does — there isn't room beside it.
    // So the header drops below the notch band instead. The push-down is derived
    // from the real notch height (safeAreaInsets.top, ~32pt) rather than a fixed
    // constant, so it tracks the actual hardware: it clears the notch and leaves
    // the header's base top padding (11) as the gap beneath it. Applied only when
    // a physical notch is present.
    var expandedHeaderNotchInset: CGFloat {
        guard hasNotch, max(1, providers.count) <= 2 else { return 0 }
        return max(0, safeAreaTop - 8)
    }

    var expandedCardHeight: CGFloat {
        // Header, page dots, separator, and footer are shell chrome. The page
        // itself reports intrinsic height so different pages do not need
        // hand-maintained height tables. The notch inset (1–2 providers) is
        // added on top so the header push-down never clips the footer.
        max(320, expandedPageHeight + 124) + expandedHeaderNotchInset
    }

    // Collapsed strip height. On a notched display it tracks the real notch
    // height (safeAreaInsets.top) so the black strip covers the notch flush; on
    // a non-notched display it's a free-floating pill at the fixed design height.
    var collapsedHeight: CGFloat {
        hasNotch ? safeAreaTop : 32
    }


    // MARK: Bubble peek geometry
    // With more than one provider the peek tray stays exactly the collapsed
    // strip and the detail moves into a callout bubble hanging below it,
    // pointing at the provider slot nearest the pointer.
    //
    // The bubble's WIDTH is fixed and its HEIGHT follows its rows; what must
    // never change mid-hover is the window, which is why the constants below
    // reserve room for the tallest callout rather than the current one (see
    // peekBubbleBandHeight).
    // The callout is sized by its content, not by the strip it hangs from: the
    // stacked window rows read best in a column this wide, and a bubble
    // stretched to a 400pt strip pushes each row's label and value to opposite
    // ends of a line.
    static let peekBubbleWidth: CGFloat = 236

    // Height follows the rows the focused provider actually has. A provider's
    // quota is not always "5h + weekly": Claude Max meters a model-scoped
    // weekly (Fable) alongside them, and Antigravity meters two independent
    // pools with a 5h and a weekly each. Anything fixed at two rows either
    // hides real limits or reserves dead space.
    // These describe the RESERVE only — the callout itself is sized by its
    // content (see peekBubbleLayer). Reserving too much costs a taller strip of
    // transparent window; reserving too little clips the callout outright, so
    // they are deliberately biased high and carry explicit slack.
    static let peekBubbleHeaderHeight: CGFloat = 23
    static let peekBubbleRowHeight: CGFloat = 37
    static let peekBubbleRowSpacing: CGFloat = 9
    static let peekBubbleGroupHeaderHeight: CGFloat = 20
    static let peekBubbleVerticalPadding: CGFloat = 23
    /// A hairline between a split-pool provider's two pool groups, plus the
    /// breathing room around it beyond the normal row spacing.
    static let peekBubbleDividerHeight: CGFloat = 11
    static let peekBubbleReserveSlack: CGFloat = 16
    /// Four covers every provider we meter today (Antigravity's two pools x two
    /// windows). Beyond it the callout stops being glanceable, so extra rows are
    /// dropped least-urgent-first and stay in the expanded panel.
    static let peekBubbleMaxRows: Int = 4

    static func peekBubbleHeight(rows: Int, groups: Int = 0) -> CGFloat {
        let n = max(1, min(rows, peekBubbleMaxRows))
        let g = max(0, groups)
        return peekBubbleHeaderHeight
            + CGFloat(n) * peekBubbleRowHeight
            + CGFloat(n - 1) * peekBubbleRowSpacing
            + CGFloat(g) * peekBubbleGroupHeaderHeight
            + CGFloat(max(0, g - 1)) * peekBubbleDividerHeight
            + peekBubbleVerticalPadding
            + peekBubbleReserveSlack
    }
    /// Transparent gap between the strip's bottom edge and the tail's tip.
    ///
    /// Close enough that the callout reads as growing out of the island rather
    /// than floating under it, but not so close that the gap becomes a hairline
    /// of desktop showing between two black shapes — at 3-4pt that reads as a
    /// seam rather than as deliberate separation.
    ///
    /// It is also live hit-test area: peekIslandContainsPointer extends the
    /// callout's rect up through this gap to meet the strip, so travelling down
    /// into the callout is never sampled as having left the island.
    static let peekBubbleGap: CGFloat = 5
    /// Tall and narrow: a tail is a pointer, and a squat wide triangle points
    /// at a region rather than at a slot. Paired with peekBubbleTailHalfWidth
    /// this is roughly a 62° tip.
    static let peekBubbleTailHeight: CGFloat = 10
    static let peekBubbleTailHalfWidth: CGFloat = 6

    /// True when peek is the strip plus a callout bubble — which is now every
    /// configuration that has anything to show.
    ///
    /// The solo trays this replaced were structurally capped at two windows,
    /// which is no longer what a provider has: Claude Max meters a model-scoped
    /// weekly alongside its 5h and weekly, and a lone Antigravity meters two
    /// pools with a 5h and a weekly each. They were also a second peek engine,
    /// and the mechanisms added for the bubble kept leaking into them wrongly.
    var usesBubblePeek: Bool {
        !providers.isEmpty
    }

    /// True when the callout has a choice of slots to point at.
    ///
    /// Deliberately separate from `usesBubblePeek`: one flag used to mean both
    /// "peek is a bubble" and "the pointer is picking between providers", and
    /// the camera-housing dead zone keyed off it walled off most of a solo
    /// island's strip. What varies with provider count is TARGETING — the
    /// housing dead zone, the slot highlight, move-driven opening, and which
    /// slot has focus.
    ///
    /// The tail is not one of them. It was, briefly, on the reasoning that with
    /// one subject there is nothing to disambiguate — but a callout's tail is
    /// an attachment cue before it is a pointer, and dropping it left a 236pt
    /// rounded rect butted against a 283pt rounded strip, which reads as two
    /// shapes colliding rather than one island extending. Popovers keep their
    /// arrow even when only one thing could have opened them.
    var peekTargetsSlots: Bool {
        providers.count > 1
    }


    /// Vertical band the bubble occupies below the tray. Reserved in the peek
    /// window's height whenever the bubble peek is in use — the bubble is
    /// present for the whole of peek, and growing the window mid-hover would
    /// both flicker and force a tracking-area recompute.
    /// Reserved for the TALLEST callout, not the current one.
    ///
    /// The bubble's own black block follows its content, but the window it
    /// lives in must not: resizing an NSWindow mid-hover rebuilds its tracking
    /// area (the mechanism behind peek getting stuck open), makes
    /// `windowHasPeekHalo` — and so every hover and exit judgement derived from
    /// it — depend on which provider is focused, and shrinks unanimated, which
    /// would clip the bubble halfway through its own animation. What is
    /// reserved is transparent, and only while peek is open.
    var peekBubbleBandHeight: CGFloat {
        usesBubblePeek
            ? Self.peekBubbleGap
                + Self.peekBubbleTailHeight
                + Self.peekBubbleHeight(rows: Self.peekBubbleMaxRows, groups: 2)
            : 0
    }

    /// Peek tray width. The bubble peek is the collapsed strip verbatim, so it
    /// takes that width and never measures content — the tray cannot resize
    /// while the pointer moves across it. `collapsedBase` is passed in because the controller
    /// computes it fresh (before `collapsedWidth` has been published) while the
    /// view reads the published value.
    func peekTrayWidth(collapsedBase: CGFloat, hasNotch: Bool) -> CGFloat {
        // Peek IS the collapsed strip — same width, same content, same slot
        // positions — with a callout hung underneath. Nothing about the strip
        // may move on hover, or the provider the pointer was aiming at slides
        // out from under it.
        hasNotch ? collapsedBase : collapsedWidthNoNotch
    }

    /// Where the callout sits for a given slot, in the strip's coordinates.
    ///
    /// Centred on the slot, then kept inside the strip. Clamping is why the
    /// tail's position is a free parameter: the body stops at the strip's edge
    /// while the tail carries on to the slot's real centre. On a strip narrower
    /// than the bubble there is nothing to clamp against, so it simply
    /// overhangs evenly.
    func peekBubblePlacement(trayWidth: CGFloat, anchor: CGFloat) -> (x: CGFloat, tailX: CGFloat) {
        let width = Self.peekBubbleWidth
        let x: CGFloat
        if trayWidth <= width {
            x = (trayWidth - width) / 2
        } else {
            x = min(max(anchor - width / 2, 0), trayWidth - width)
        }
        return (x, anchor - x)
    }

    /// Provider whose slot owns `x`, measured from the card's leading edge.
    /// Boundaries are the midpoints between neighbouring anchors, so the strip
    /// is partitioned with no dead space and a 12pt gauge still gets a target
    /// a hundred points wide.
    func providerIndex(atCardX x: CGFloat) -> Int? {
        let sorted = providerAnchors.sorted { $0.value < $1.value }
        guard let last = sorted.last else { return nil }
        for (current, next) in zip(sorted, sorted.dropFirst()) {
            if x < (current.value + next.value) / 2 { return current.key }
        }
        return last.key
    }


    var peekTrayWidth: CGFloat {
        peekTrayWidth(collapsedBase: collapsedWidth, hasNotch: hasNotch)
    }

    // Extra tray height for the one-line "updating components" caption shown
    // under the peek segments during a backend upgrade. Without it the
    // bottom-anchored content grows upward into the notch band and the
    // segments slide behind the physical notch.
    static let upgradeCaptionHeight: CGFloat = 15

    /// Peek's tray is the collapsed strip verbatim.
    var peekHeight: CGFloat {
        collapsedHeight + (isUpgradingBackend ? Self.upgradeCaptionHeight : 0)
    }

}

// MARK: - Notch Container View (AppKit)
class NotchContainerView: NSView {
    private var trackingArea: NSTrackingArea?
    weak var controller: NotchWindowController?

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        return true
    }

    override var acceptsFirstResponder: Bool { true }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 { // Escape
            if let controller = controller, controller.model.notchState == .expanded {
                controller.collapse()
                return
            }
        }
        super.keyDown(with: event)
    }

    override func updateTrackingAreas() {
        if let trackingArea = trackingArea {
            removeTrackingArea(trackingArea)
        }
        // .mouseMoved drives the peek bubble's slot targeting. It goes through
        // AppKit rather than SwiftUI's .onHover because the peek window never
        // becomes key (see NotchWindow.canBecomeKey), and .activeAlways
        // tracking is the reliable way to keep receiving moves there.
        let options: NSTrackingArea.Options = [.mouseEnteredAndExited, .mouseMoved, .activeAlways]
        // Track only the visible card, not the transparent overshoot halo, so
        // moving into the halo counts as leaving peek.
        let rect = controller?.visibleContentRect(in: bounds) ?? bounds
        trackingArea = NSTrackingArea(rect: rect, options: options, owner: self, userInfo: nil)
        addTrackingArea(trackingArea!)
        super.updateTrackingAreas()
    }

    override func mouseEntered(with event: NSEvent) {
        controller?.mouseEntered()
    }

    override func mouseMoved(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        controller?.peekPointerMoved(to: point, in: bounds)
    }

    override func mouseExited(with event: NSEvent) {
        controller?.mouseExited()
    }
}

// MARK: - Notch Window (AppKit)
class NotchWindow: NSWindow {
    override var canBecomeKey: Bool {
        let appDelegate = NSApp.delegate as! AppDelegate
        let allows = appDelegate.notchWindowController?.allowsKeyWindow ?? false
        NSLog("FluxionNotch: canBecomeKey checked: \(allows)")
        return allows
    }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if event.keyCode == 53 { // Escape
            let appDelegate = NSApp.delegate as! AppDelegate
            if let controller = appDelegate.notchWindowController, controller.model.notchState == .expanded {
                controller.collapse()
                return true
            }
        }
        return super.performKeyEquivalent(with: event)
    }
}

// MARK: - Notch Hosting View (AppKit override)
class NotchHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        return true
    }
}

// MARK: - Notch Window Controller (AppKit)
class NotchWindowController: NSWindowController, NSWindowDelegate {
    let model = NotchDataModel()

    // Transparent overshoot halo reserved around the peek tray so the spring's
    // Q-bounce isn't clipped by the window border (see getWindowSize). Kept as
    // constants so hit-testing can subtract the halo and stay on the visible
    // tray — otherwise hovering/clicking the invisible halo would keep peek
    // open and expand the notch.
    static let peekMarginW: CGFloat = 48
    static let peekMarginH: CGFloat = 32
    /// The island hangs from the screen's top edge, so its hit rects end
    /// exactly at screen.maxY — and CGRect.contains excludes its own max edge.
    /// A pointer shoved all the way up reports y == maxY and reads as OUTSIDE
    /// the island it is sitting on, which made peek open on the way up and shut
    /// again on arrival. Every island hit rect is grown past the top edge by
    /// this much so the edge itself counts as inside.
    static let screenEdgeSlop: CGFloat = 4
    /// How far past the boundary the pointer must travel before the bubble
    /// re-points — an upper bound, not a fixed distance. See peekTargetIndex:
    /// neighbouring slots can sit as little as 24pt apart, and a flat ±12pt
    /// dead band then covers the whole gap between them.
    static let peekFocusHysteresis: CGFloat = 12
    /// How long a new slot must hold the pointer before the bubble commits to
    /// it, so slots crossed in transit don't drag the callout along.
    static let peekFocusCommitDelay: TimeInterval = 0.09
    /// How long the pointer must stay on the strip before peek opens.
    ///
    /// The island lives on the screen's top edge — the one line the pointer
    /// crosses all day on its way to the menu bar, a title bar, or the corner,
    /// and the one users deliberately slam into as a wall. Every such crossing
    /// used to throw a large callout over whatever they were reading. The costs
    /// are lopsided: a false open covers content and takes attention, while the
    /// delay is paid once against something the user is about to read for
    /// seconds. Crossings clear the strip well inside 200ms; deliberate aim
    /// does not notice it.
    ///
    /// Deliberately NOT applied to re-pointing: once the callout is open the
    /// user is reading, and there latency reads as sluggishness rather than
    /// composure (see peekFocusCommitDelay).
    static let peekOpenDelay: TimeInterval = 0.2

    /// True while the window is physically peek-sized and therefore carries the
    /// transparent overshoot halo. Tracked explicitly rather than derived from
    /// `model.notchState` because the state flips to `.collapsed` ~0.35s before
    /// the window actually shrinks back — during that gap the halo still exists
    /// and must stay inert to hover/clicks.
    private var windowHasPeekHalo = false

    /// The hit-testable card rect inside the content view, excluding the
    /// transparent overshoot halo. Only peek reserves a halo; collapsed and
    /// expanded use the whole window. `bounds` is in the (non-flipped) content
    /// view coordinate space, so the card hugs the top and the halo sits at the
    /// bottom and on both sides.
    /// The visible card in screen coordinates, grown past the screen's top edge
    /// (see screenEdgeSlop). The single answer to "is the pointer still on the
    /// island", shared by the exit debounce and the watchdog so they cannot
    /// disagree about it.
    /// Is the pointer on the island? The island in peek is a T, not a box: the
    /// strip spans the full tray width, the callout below it is narrower and
    /// sits wherever its slot put it. Testing one bounding rectangle kept peek
    /// alive with the pointer well to the side of anything drawn.
    func peekIslandContainsPointer(_ point: CGPoint) -> Bool {
        guard model.usesBubblePeek, let win = window else {
            return visibleCardScreenRect()?.contains(point) ?? false
        }
        let frame = win.frame
        let trayWidth = model.peekTrayWidth
        let cardLeft = frame.midX - trayWidth / 2
        let stripBottom = frame.maxY - model.peekHeight
        let strip = CGRect(
            x: cardLeft,
            y: stripBottom,
            width: trayWidth,
            height: model.peekHeight + Self.screenEdgeSlop
        )
        if strip.contains(point) { return true }

        let bubbleHeight = model.peekBubbleVisibleHeight
        guard bubbleHeight > 0 else { return false }
        let anchor = model.providerAnchors[model.peekFocusIndex ?? 0] ?? trayWidth / 2
        let placement = model.peekBubblePlacement(trayWidth: trayWidth, anchor: anchor)
        // Reaches up through the gap to meet the strip, so travelling down into
        // the callout is never sampled as having left the island.
        let bubble = CGRect(
            x: cardLeft + placement.x,
            y: stripBottom - NotchDataModel.peekBubbleGap - bubbleHeight,
            width: NotchDataModel.peekBubbleWidth,
            height: NotchDataModel.peekBubbleGap + bubbleHeight
        )
        return bubble.contains(point)
    }

    func visibleCardScreenRect() -> CGRect? {
        guard let win = window else { return nil }
        var rect = visibleContentRect(in: CGRect(origin: .zero, size: win.frame.size))
            .offsetBy(dx: win.frame.minX, dy: win.frame.minY)
        rect.size.height += Self.screenEdgeSlop
        return rect
    }

    func visibleContentRect(in bounds: CGRect) -> CGRect {
        guard windowHasPeekHalo else { return bounds }
        var rect = CGRect(
            x: bounds.minX + Self.peekMarginW / 2,
            y: bounds.minY + Self.peekMarginH,
            width: bounds.width - Self.peekMarginW,
            height: bounds.height - Self.peekMarginH
        )
        // Trim the band reserved for a taller callout than the one showing.
        // The reservation exists so the window never resizes mid-hover; it must
        // not also make the island seem to extend a hundred points below
        // anything the user can see.
        if model.notchState == .peek, model.usesBubblePeek, model.peekBubbleVisibleHeight > 0 {
            let content = model.peekHeight
                + NotchDataModel.peekBubbleGap
                + model.peekBubbleVisibleHeight
            if content < rect.height {
                rect = CGRect(
                    x: rect.minX,
                    y: rect.maxY - content,
                    width: rect.width,
                    height: content
                )
            }
        }
        return rect
    }
    var allowsKeyWindow: Bool = false
    var isCollapsing: Bool = false
    private var webViewTimer: Timer?
    private var screenTimer: Timer?
    private var collapseWorkItem: DispatchWorkItem?
    /// Debounce state for the peek bubble's slot targeting (see
    /// peekPointerMoved): the slot waiting out its commit delay, and the work
    /// item that will apply it.
    private var peekFocusWorkItem: DispatchWorkItem?
    private var pendingPeekFocusIndex: Int?
    /// Backstop for a peek that never gets its mouseExited. See
    /// startPeekWatchdog.
    private var peekWatchdog: Timer?
    /// True between a deliberate dismissal of the expanded panel and the
    /// pointer next leaving the island. See suppressPeekUntilPointerLeaves.
    private var peekSuppressed = false
    private var peekSuppressionWatchdog: Timer?
    /// Pending hover-intent open. See schedulePeekOpen.
    private var peekOpenWorkItem: DispatchWorkItem?
    /// Debounces the flurry of didChangeScreenParametersNotification callbacks
    /// that fire while a display is being (un)plugged — we only want to
    /// reposition once the geometry has settled.
    private var screenParamsWorkItem: DispatchWorkItem?
    /// Last screen frame we positioned against, in global coordinates. Plugging
    /// or unplugging an external display can shift the built-in (notched)
    /// display's origin/maxY without changing its NSScreen identity, so
    /// checkActiveScreen() also compares this to catch the "same screen, moved
    /// coordinates" case that would otherwise strand the window until hover.
    private var lastTargetScreenFrame: CGRect = .zero
    /// True only while WE have hidden the notch because another app is
    /// fullscreen. Gates the auto-restore so we never re-show a notch the user
    /// (or other app logic) hid for a different reason. See updateFullscreenVisibility().
    private var hiddenForFullscreen = false

    /// Number of native-fullscreen (type 4) Spaces on the target display at the
    /// last visibility check. A fresh fullscreen Space is created ~500ms BEFORE
    /// the display's Current Space flips to it (measured on macOS 26), so a jump
    /// in this count is the earliest signal that a fullscreen transition has
    /// begun — earlier, and independent of whether the incoming window covers
    /// the notch strip, which is what lets us pre-empt the flash for video apps
    /// (their fullscreen content sits *below* the notch, so the full-frame-cover
    /// signal never fires for them). See updateFullscreenVisibility().
    private var lastFullscreenSpaceCount = 0

    /// While set and not yet elapsed, a fullscreen entry transition is in
    /// flight: keep the notch hidden even though Current Space hasn't flipped to
    /// type 4 yet, bridging the ~500ms gap between "fullscreen Space created"
    /// and "Current Space == 4" so the notch never reappears mid-transition.
    /// Cleared once the flip completes, or on timeout if the transition was for
    /// some other display and never reaches us.
    private var fullscreenEntryDeadline: Date?

    /// Target display the fullscreen count above was read from. The notch
    /// follows the mouse across displays, so the count must be re-baselined
    /// (not read as a jump) whenever this changes. See updateFullscreenVisibility.
    private var lastFullscreenDisplayID: CGDirectDisplayID?

    // History fetch retry: the web server may not be listening yet right after
    // launch, so a single attempt would leave "0" until the 30s poll. Each
    // fetchHistory() starts a fresh attempt chain (newer chains supersede older
    // ones via the generation token) that backs off until it succeeds. The
    // front is dense because the common case is fast: the app restarts the web
    // server on its own relaunch and a warm-store boot serves in ~0.6s
    // (measured), so quarter/half-second probes pick that up almost as soon as
    // it lands. The ~12s tail covers the slow cases — a first-ever ingest or a
    // store-format migration — instead of giving up and stranding the
    // placeholders until the 30s poll.
    private var fetchGeneration = 0
    private let fetchRetryDelays: [TimeInterval] = [0.25, 0.5, 1.0, 2.0, 4.0, 4.0, 4.0]
    
    private var appDelegate: AppDelegate {
        return NSApp.delegate as! AppDelegate
    }
    
    init() {
        // Initial frame: collapsed state for 3 models is 180x32
        let initialFrame = NSRect(x: 0, y: 0, width: 180, height: 32)
        let win = NotchWindow(
            contentRect: initialFrame,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        
        win.isOpaque = false
        win.backgroundColor = .clear
        win.hasShadow = false
        win.level = .statusBar + 1
        win.ignoresMouseEvents = false
        // The peek bubble's slot targeting is driven by mouseMoved (see
        // NotchContainerView). The tracking area asks for it, but a window that
        // never becomes key needs this flag for the moves to be delivered.
        win.acceptsMouseMovedEvents = true
        // Note: omitting .fullScreenAuxiliary does NOT keep the notch off
        // native fullscreen Spaces — measured on macOS 26, a .canJoinAllSpaces
        // window at this level is composited over fullscreen Spaces regardless
        // (the window stays in CGWindowList with the fullscreen Space active).
        // Getting out of the way of fullscreen apps is therefore handled
        // explicitly by updateFullscreenVisibility() below, not by collection
        // behavior. .stationary keeps it from drifting during Spaces
        // transitions; .ignoresCycle keeps it out of the ⌘Tab window cycle.
        win.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        win.isReleasedWhenClosed = false
        
        super.init(window: win)
        win.delegate = self
        
        let container = NotchContainerView(frame: win.contentView!.bounds)
        container.controller = self
        container.autoresizingMask = [.width, .height]
        
        let hostingView = NotchHostingView(rootView: NotchIslandView(model: model, controller: self))
        hostingView.frame = container.bounds
        hostingView.autoresizingMask = [.width, .height]
        container.addSubview(hostingView)
        
        win.contentView = container
        
        // Initial positioning
        repositionWindow()
        
        // Start history polling (every 30 seconds)
        webViewTimer = Timer.scheduledTimer(withTimeInterval: 30.0, repeats: true) { [weak self] _ in
            self?.fetchHistory()
        }
        RunLoop.main.add(webViewTimer!, forMode: .common)
        
        // Start screen tracking timer (every 0.25 seconds) to follow the mouse
        // cursor screen and to hide the notch while another app is fullscreen.
        // The interval is load-bearing for flicker-free native fullscreen: the
        // transitioning window covers the screen's full frame ~450ms BEFORE the
        // Space flips to fullscreen type / the Space-change notification fires /
        // the system re-composites the notch over the new Space (measured on
        // macOS 26). Polling faster than that lead window guarantees a tick
        // hides the notch during the transition animation — before it could
        // ever flash over the fullscreen content. It also catches borderless
        // "fake" fullscreen (video players, games), which never changes Spaces
        // and so emits no notification at all.
        screenTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            self?.checkActiveScreen()
            self?.updateFullscreenVisibility()
        }
        RunLoop.main.add(screenTimer!, forMode: .common)

        // Native fullscreen moves the app to its own Space, which fires this
        // notification — react immediately instead of waiting up to 1s.
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(activeSpaceChanged),
            name: NSWorkspace.activeSpaceDidChangeNotification,
            object: nil
        )

        // Plugging/unplugging an external display re-lays-out the global
        // coordinate space, shifting the notched display's absolute frame. The
        // window keeps its stale absolute frame until something recomputes it,
        // so it strands itself off the notch (e.g. down in a browser's content
        // area) until the next hover. React to the geometry change directly.
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(screenParametersChanged),
            name: NSApplication.didChangeScreenParametersNotification,
            object: nil
        )

        fetchHistory()
    }
    
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
    
    func show() {
        // Reset any fullscreen-hide leftovers (alpha faded to 0, hidden flag)
        // so an explicit show — e.g. re-enabling the notch in Preferences —
        // always lands visible. If a fullscreen app still owns the screen, the
        // next visibility tick will fade it back out.
        hiddenForFullscreen = false
        fadeGeneration += 1
        window?.alphaValue = 1
        window?.orderFrontRegardless()
        repositionWindow()
        fetchHistory()
    }
    
    func hide() {
        window?.orderOut(nil)
    }
    
    func triggerCreditGrantFlash(delta: Int) {
        DispatchQueue.main.async {
            self.model.justGranted = delta
            // Reset after 8.0 seconds
            DispatchQueue.main.asyncAfter(deadline: .now() + 8.0) { [weak self] in
                guard let self = self else { return }
                if self.model.justGranted == delta {
                    self.model.justGranted = 0
                }
            }
        }
    }
    
    func fetchHistory() {
        fetchGeneration += 1
        performFetchHistory(attempt: 0, generation: fetchGeneration)
    }

    private func performFetchHistory(attempt: Int, generation: Int) {
        let port = appDelegate.envVals["FLUXION_UI_PORT"] ?? "8765"
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/usage/history?window=1d") else { return }
        var request = URLRequest(url: url)
        // Cap a single attempt so a stalled/half-restarting server fails fast and
        // we retry, rather than hanging on the 60s URLSession default.
        request.timeoutInterval = 5
        if let token = appDelegate.envVals["FLUXION_UI_TOKEN"] {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            guard let self = self else { return }

            struct HistoryModelStat: Codable {
                let provider: String
                let model: String
                let cost: Double
                // Headline volume uses `total_tokens` (input + output +
                // cache_creation + cache_read) — the same measure the web
                // console's hero, the menu bar and ccusage report, and the one
                // that reconciles with what Codex itself prints. Cache reads
                // dominate it at a healthy hit rate; the breakdown below the
                // number is what separates them out.
                let total_tokens: Int
                let input_tokens: Int
                let output_tokens: Int
                let cache_creation_tokens: Int?
                let cache_read_tokens: Int?
            }

            struct ProviderDayStat: Codable {
                let date: String
                let provider: String
                // Same measure as the headline (see HistoryModelStat), so
                // today's bar always matches the big number above it.
                let total_tokens: Int
                // Optional: a backend that predates per-day cost still decodes,
                // and the hover chip falls back to the day's total alone.
                let cost: Double?
            }

            struct ProviderHourStat: Codable {
                let provider: String
                let hour: Int
                let messages: Int
                let total_tokens: Int
            }

            struct HistoryPayload: Codable {
                let by_model: [HistoryModelStat]?
                // Absent on backends that predate the field; the sparkline
                // just stays hidden.
                let by_provider_day: [ProviderDayStat]?
                let by_provider_hour: [ProviderHourStat]?
            }

            // A valid payload (even an empty one = no usage today) is a success;
            // anything else — connection refused, timeout, non-200, undecodable —
            // is a miss worth retrying while the server comes up.
            var parsed: [String: ProviderHistoryStats]? = nil
            var parsedDaily: [String: [ProviderDayUsage]] = [:]
            var parsedPeakHours: [String: Int] = [:]
            if let data = data, error == nil,
               (response as? HTTPURLResponse)?.statusCode == 200,
               let payload = try? JSONDecoder().decode(HistoryPayload.self, from: data),
               let byModel = payload.by_model {
                var stats: [String: ProviderHistoryStats] = [:]
                for m in byModel {
                    let pKey = m.provider.lowercased()
                    let existing = stats[pKey] ?? ProviderHistoryStats(tokens: 0, input: 0, output: 0, cacheCreation: 0, cacheRead: 0, cost: 0.0)
                    stats[pKey] = ProviderHistoryStats(
                        tokens: existing.tokens + m.total_tokens,
                        input: existing.input + m.input_tokens,
                        output: existing.output + m.output_tokens,
                        cacheCreation: existing.cacheCreation + (m.cache_creation_tokens ?? 0),
                        cacheRead: existing.cacheRead + (m.cache_read_tokens ?? 0),
                        cost: existing.cost + m.cost
                    )
                }
                parsed = stats

                if let rows = payload.by_provider_day {
                    // Re-key the server's sparse (day, provider) rows into a
                    // dense trailing-7-local-days series per provider so bar
                    // positions line up across providers and zero days show
                    // as gaps. The server buckets days in the same local
                    // timezone this formatter uses.
                    let formatter = DateFormatter()
                    formatter.dateFormat = "yyyy-MM-dd"
                    let dayKeys: [String] = (0..<14).reversed().map { offset in
                        formatter.string(from: Calendar.current.date(byAdding: .day, value: -offset, to: Date()) ?? Date())
                    }
                    // The server emits one row per (day, provider); summing
                    // rather than assigning keeps the merge correct if that
                    // ever loosens to a finer grain.
                    var byProviderDay: [String: [String: ProviderDayUsage]] = [:]
                    for row in rows {
                        let pKey = row.provider.lowercased()
                        let prior = byProviderDay[pKey]?[row.date] ?? .empty
                        byProviderDay[pKey, default: [:]][row.date] = ProviderDayUsage(
                            total: prior.total + row.total_tokens,
                            cost: prior.cost + (row.cost ?? 0)
                        )
                    }
                    for (pKey, days) in byProviderDay {
                        parsedDaily[pKey] = dayKeys.map { days[$0] ?? .empty }
                    }
                }

                if let rows = payload.by_provider_hour {
                    var grouped: [String: [ProviderHourStat]] = [:]
                    for row in rows where (0..<24).contains(row.hour) {
                        grouped[row.provider.lowercased(), default: []].append(row)
                    }
                    for (provider, hours) in grouped {
                        // Avoid presenting a habit inferred from only one or
                        // two turns. Ties prefer token volume, then the earlier
                        // local hour for deterministic rendering.
                        guard hours.reduce(0, { $0 + $1.messages }) >= 3 else { continue }
                        let peak = hours.max { lhs, rhs in
                            if lhs.messages != rhs.messages { return lhs.messages < rhs.messages }
                            if lhs.total_tokens != rhs.total_tokens {
                                return lhs.total_tokens < rhs.total_tokens
                            }
                            return lhs.hour > rhs.hour
                        }
                        if let peak = peak, peak.messages > 0 {
                            parsedPeakHours[provider] = peak.hour
                        }
                    }
                }
            }

            DispatchQueue.main.async {
                // A newer fetchHistory() started; abandon this stale chain.
                guard generation == self.fetchGeneration else { return }
                if let stats = parsed {
                    self.model.todayStats = stats
                    self.model.dailyTokens = parsedDaily
                    self.model.peakHours = parsedPeakHours
                    self.model.historyLoaded = true
                } else if attempt < self.fetchRetryDelays.count {
                    let delay = self.fetchRetryDelays[attempt]
                    DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                        guard let self = self, generation == self.fetchGeneration else { return }
                        self.performFetchHistory(attempt: attempt + 1, generation: generation)
                    }
                }
            }
        }.resume()
    }
    
    func repositionWindow() {
        let size = getWindowSize(for: model.notchState)
        updateWindowFrame(to: size)
    }

    /// Toggle the backend-upgrade indicator. The sweep's outer glow needs the
    /// halo margin around the collapsed strip, so the window must be reframed
    /// right away — a bare `model.isUpgradingBackend` write only re-renders the
    /// SwiftUI content inside the existing tight window, and the glow stays
    /// clipped until some other transition happens to resize it.
    func setUpgradingBackend(_ upgrading: Bool) {
        guard model.isUpgradingBackend != upgrading else { return }
        model.isUpgradingBackend = upgrading
        repositionWindow()
    }
    
    private func getLeftAndRightMargins(count: Int) -> (left: CGFloat, right: CGFloat) {
        let leftW: CGFloat
        let rightW: CGFloat

        // The number-suppressing ring styles collapse each unit's lane to its
        // bare ring, so the strip hugs the notch instead of carrying the dead
        // width of numbers it no longer shows. Any state whose side text
        // returns (the exhausted countdown, credits, ∞, loading) gets the full
        // text lane back — the strip resizes on those transitions, trading the
        // old fixed-width stability for a tight silhouette.
        let shapedGaugeStyle = model.gaugeStyle != "dot"
        let hidesSide = shapedGaugeStyle
            && (model.gaugeValue == "inside" || model.gaugeValue == "hidden")
        let bareRing: CGFloat = model.gaugeValue == "inside" ? 18 : 12
        let presenter = NotchQuotaPresenter(now: Date())
        func statusUnitW(_ p: ProviderUsage) -> CGFloat {
            let mode = presenter.quotaState(for: p).mode
            if mode == .credits {
                return 56
            }
            guard hidesSide else { return 48 }
            return mode == .healthy ? bareRing : 48
        }

        switch model.silentStyle {
        case "ambient":
            // Lane widths track the gauge style: the classic dot is 7pt, a
            // bare ring/liquid circle is 12pt, and the Minimal solo 5H | WK
            // glance flanks a 17pt labeled gauge on each shoulder
            // (soloDualWindowMinimalView). Undersized lanes push the gauges
            // under the physical notch.
            if shapedGaugeStyle && notchUsesSoloDualWindowGlance(model.providers) {
                leftW = 17
                rightW = 17
            } else {
                let unit: CGFloat = shapedGaugeStyle ? 12 : 7
                leftW = (count >= 2) ? unit : 0
                rightW = (count >= 3) ? (unit * 2 + 6) : unit
            }
        case "lowest":
            leftW = 0
            let maxUnitW = model.providers.map(statusUnitW).max() ?? 48
            if hidesSide {
                let allHealthy = !model.providers.isEmpty
                    && model.providers.allSatisfy { presenter.quotaState(for: $0).mode == .healthy }
                rightW = allHealthy ? bareRing : maxUnitW
            } else {
                rightW = maxUnitW
            }
        default: // "all"
            if notchUsesSoloDualWindowGlance(model.providers), let provider = model.providers.first {
                if hidesSide {
                    // Sides size independently: a depleted window's countdown
                    // (or the hidden placement's side ∞) restores that side's
                    // text lane.
                    let state = presenter.quotaState(for: provider)
                    let ring: CGFloat = model.gaugeValue == "inside" ? 18 : 17
                    if state.fiveHour?.depleted == true {
                        leftW = 58
                    } else if isCodexFiveHourTemporarilyUncapped(provider) && model.gaugeValue == "hidden" {
                        leftW = 34
                    } else {
                        leftW = ring
                    }
                    rightW = state.weekly?.depleted == true ? 58 : ring
                } else {
                    // `5H 100%` / `WK 100%` normally fit well inside 48pt, but a
                    // depleted window swaps the value for a timer (`4d 17h`). Give
                    // both shoulders a stable 58pt content lane so state changes
                    // never resize the strip or crowd the camera.
                    leftW = 58
                    rightW = 58
                }
            } else if notchIsSoloSplit(model.providers), let provider = model.providers.first {
                // One provider's two pools flank the camera. Pool trouble
                // (a blocked pool's countdown or credits) restores the lanes.
                let state = presenter.quotaState(for: provider)
                let bad = state.mode != .healthy || !state.blockedPools.isEmpty
                let unit: CGFloat = state.mode == .credits ? 56 : ((hidesSide && !bad) ? bareRing : 48)
                leftW = unit
                rightW = unit
            } else if count == 1 {
                leftW = 0
                rightW = model.providers.first.map(statusUnitW) ?? 48
            } else if count == 2 {
                leftW = statusUnitW(model.providers[0])
                rightW = statusUnitW(model.providers[1])
            } else {
                leftW = statusUnitW(model.providers[0])
                rightW = statusUnitW(model.providers[1]) + 6 + statusUnitW(model.providers[2])
            }
        }
        
        let gOuter: CGFloat = 13
        // AppKit's auxiliary top areas stop slightly inside the visible camera
        // housing on some panels. Text layouts have spare lane width, but a
        // gauge that occupies its entire lane (ambient's 7pt dot, or the
        // number-suppressing ring styles' bare rings) would sit partly under
        // the physical notch with the old 10pt allowance. Expand those tight
        // layouts symmetrically; roomy text layouts keep their established
        // geometry.
        let gNotch: CGFloat = (model.silentStyle == "ambient" || hidesSide) ? 20 : 10
        
        let leftMargin = leftW > 0 ? (leftW + gOuter + gNotch) : (gOuter + gNotch)
        let rightMargin = rightW > 0 ? (rightW + gOuter + gNotch) : (gOuter + gNotch)
        return (leftMargin, rightMargin)
    }

    private struct PhysicalNotchInfo {
        let left: CGFloat
        let right: CGFloat
        let width: CGFloat
    }
    
    private func getPhysicalNotchInfo(screen: NSScreen) -> PhysicalNotchInfo? {
        guard screen.safeAreaInsets.top > 25 else { return nil }
        
        if #available(macOS 12.0, *),
           let topLeft = screen.auxiliaryTopLeftArea,
           let topRight = screen.auxiliaryTopRightArea {
            let pLeft = topLeft.maxX + 14
            let pRight = topRight.minX - 14
            return PhysicalNotchInfo(left: pLeft, right: pRight, width: pRight - pLeft)
        }
        
        // Fallback: screen-centered 210pt when AppKit APIs are unavailable
        let screenFrame = screen.frame
        let screenCenterX = screenFrame.minX + screenFrame.width / 2
        return PhysicalNotchInfo(left: screenCenterX - 105, right: screenCenterX + 105, width: 210)
    }

    private func getCapsuleCenterX(screen: NSScreen, state: NotchState, count: Int) -> CGFloat {
        let screenFrame = screen.frame
        let margins = getLeftAndRightMargins(count: count)
        let screenCenterX = screenFrame.minX + screenFrame.width / 2
        
        guard let physicalNotch = getPhysicalNotchInfo(screen: screen) else {
            return screenCenterX
        }
        
        let physicalNotchCenter = (physicalNotch.left + physicalNotch.right) / 2
        return physicalNotchCenter + (margins.right - margins.left) / 2
    }

    func updateNotchCoordinates(for state: NotchState) {
        let screen = findTargetScreen()
        let count = notchLayoutCount(model.providers)
        let physicalNotch = getPhysicalNotchInfo(screen: screen)
        let hasNotch = physicalNotch != nil
        
        let cardWidth: CGFloat
        switch state {
        case .collapsed:
            cardWidth = model.collapsedWidth
        case .peek:
            cardWidth = model.peekTrayWidth(
                collapsedBase: model.collapsedWidth,
                hasNotch: hasNotch
            )
        case .expanded:
            cardWidth = notchExpandedWidth(providers: model.providers, expandedStyle: model.expandedStyle)
        }
        
        let screenFrame = screen.frame
        let cardX: CGFloat
        if hasNotch {
            let center = getCapsuleCenterX(screen: screen, state: state, count: count)
            cardX = center - cardWidth / 2
        } else {
            cardX = screenFrame.minX + (screenFrame.width - cardWidth) / 2
        }
        
        var notchWidth: CGFloat = 0
        var notchLeft: CGFloat = cardWidth / 2
        var notchRight: CGFloat = cardWidth / 2
        
        if let physicalNotch = physicalNotch {
            notchWidth = physicalNotch.width
            notchLeft = physicalNotch.left - cardX
            notchRight = physicalNotch.right - cardX
        }
        
        model.notchWidth = notchWidth
        model.notchLeft = notchLeft
        model.notchRight = notchRight
    }

    func updateWindowFrame(to size: NSSize) {
        guard let win = window else { return }
        let screen = findTargetScreen()
        let screenFrame = screen.frame
        let physicalNotch = getPhysicalNotchInfo(screen: screen)
        let hasNotch = physicalNotch != nil
        let count = notchLayoutCount(model.providers)
        
        let x: CGFloat
        if hasNotch {
            let center = getCapsuleCenterX(screen: screen, state: model.notchState, count: count)
            x = center - size.width / 2
        } else {
            x = screenFrame.minX + (screenFrame.width - size.width) / 2
        }
        let y = screenFrame.maxY - size.height
        
        updateNotchCoordinates(for: model.notchState)

        model.hasNotch = hasNotch
        model.safeAreaTop = screen.safeAreaInsets.top
        // Remember the frame we positioned against so checkActiveScreen() can
        // detect a coordinate shift on the same display (see there).
        lastTargetScreenFrame = screenFrame
        
        let newFrame = NSRect(x: x, y: y, width: size.width, height: size.height)
        win.hasShadow = (model.notchState == .expanded)
        if model.notchState == .expanded {
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.16
                context.allowsImplicitAnimation = true
                win.animator().setFrame(newFrame, display: true)
            }
        } else {
            win.setFrame(newFrame, display: true, animate: false)
        }

        // Derive the peek-halo flag from the size we actually applied. This is
        // the single resize choke point, so the flag can never get stuck true
        // after the window has shrunk back — a stale flag would inset the small
        // collapsed window and swallow clicks (intermittent "click doesn't
        // expand"). Peek is always strictly larger than collapsed/expanded by
        // the margin, so the size match is unambiguous.
        if model.isUpgradingBackend {
            windowHasPeekHalo = true
        } else {
            let peekSize = getWindowSize(for: .peek)
            windowHasPeekHalo = abs(size.width - peekSize.width) < 0.5
                && abs(size.height - peekSize.height) < 0.5
        }
    }
    
    private func getCollapsedWidth(screen: NSScreen, count: Int) -> CGFloat {
        guard let physicalNotch = getPhysicalNotchInfo(screen: screen) else {
            // No physical notch: hug the measured content width so the
            // floating pill carries no dead side margins.
            return model.collapsedWidthNoNotch
        }
        
        let margins = getLeftAndRightMargins(count: count)
        return physicalNotch.width + margins.left + margins.right
    }
    
    private func getWindowSize(for state: NotchState) -> NSSize {
        let screen = findTargetScreen()
        let safeAreaTop = screen.safeAreaInsets.top
        let hasNotch = safeAreaTop > 25
        
        let count = notchLayoutCount(model.providers)
        
        switch state {
        case .collapsed:
            let w = getCollapsedWidth(screen: screen, count: count)
            model.collapsedWidth = w
            // Match the physical notch height when present (see collapsedHeight);
            // local hasNotch/safeAreaTop are used because model.safeAreaTop is not
            // updated until updateWindowFrame, which runs after this.
            let h = hasNotch ? safeAreaTop : 32
            if model.isUpgradingBackend {
                // Halo margin so the upgrade sweep's outer glow isn't clipped
                // by the window while collapsed.
                return NSSize(width: w + Self.peekMarginW, height: h + Self.peekMarginH)
            }
            // Room for the shoulder flare, which grows outward from the body.
            // Peek and expanded already carry a far larger overshoot halo, so
            // only the tight collapsed frame has to make room for it.
            return NSSize(width: w + 2 * notchFlareRadius(forWidth: w), height: h)
        case .peek:
            // Transparent margin so the spring's overshoot can extend past the
            // tray edges instead of being clipped by the window border — same
            // trick the expanded panel uses (see below). The halo is excluded
            // from hit-testing via visibleContentRect(in:).
            //
            // The callout hangs below the strip in its own band, reserved for
            // the tallest callout so hovering never resizes the window. Tray
            // height is the strip's, computed from the local screen values
            // because model.safeAreaTop/hasNotch are not published until
            // updateWindowFrame, which runs after this.
            let w = model.peekTrayWidth(
                collapsedBase: hasNotch ? getCollapsedWidth(screen: screen, count: count) : 0,
                hasNotch: hasNotch
            )
            let upgrade = model.isUpgradingBackend ? NotchDataModel.upgradeCaptionHeight : 0
            let trayHeight = (hasNotch ? safeAreaTop : 32) + upgrade
            return NSSize(
                width: w + Self.peekMarginW,
                height: trayHeight + model.peekBubbleBandHeight + Self.peekMarginH
            )
        case .expanded:
            let w: CGFloat = notchExpandedWidth(providers: model.providers, expandedStyle: model.expandedStyle)
            // Add a transparent window margin (width + 60, height + 40) so the SwiftUI
            // card can bounce and overshoot freely without getting clipped by window borders.
            return NSSize(width: w + 60, height: model.expandedCardHeight + 40)
        }
    }
    
    deinit {
        peekOpenWorkItem?.cancel()
        peekWatchdog?.invalidate()
        peekSuppressionWatchdog?.invalidate()
        webViewTimer?.invalidate()
        screenTimer?.invalidate()
        screenParamsWorkItem?.cancel()
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        NotificationCenter.default.removeObserver(self)
    }

    private func checkActiveScreen() {
        guard model.notchState == .collapsed else { return }
        let currentScreen = window?.screen
        let targetScreen = findTargetScreen()
        // Reposition when the target screen changed identity (mouse moved to
        // another display) OR when the target screen kept its identity but its
        // frame shifted in global coordinates (a display was (un)plugged and the
        // change notification was missed/coalesced). The latter is what otherwise
        // strands the notch off-position until the user hovers.
        if currentScreen != targetScreen || targetScreen.frame != lastTargetScreenFrame {
            repositionWindow()
        }
    }

    @objc private func activeSpaceChanged() {
        // Native fullscreen enter/exit changes the active Space. The 0.25s poll
        // is what actually hides the notch in time on ENTER (it sees the
        // full-frame cover ~450ms before this notification even fires); this
        // handler's job is mainly the EXIT direction — bring the notch back the
        // instant fullscreen ends instead of up to a poll-interval later. The
        // short burst re-checks absorb the CGS "Current Space" record lagging
        // the notification during the transition, so the first check doesn't
        // read a stale space type and no-op.
        updateFullscreenVisibility()
        for delay in [0.1, 0.3, 0.7] {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                self?.updateFullscreenVisibility()
            }
        }
    }

    @objc private func screenParametersChanged() {
        // Reposition immediately so the window tracks the geometry live instead
        // of sitting at its stale absolute frame — that stale frame is exactly
        // what briefly strands the notch off-position. NSScreen state is already
        // updated by the time this fires, so each synchronous reposition lands
        // correctly for the current arrangement; there is no visible gap to wait
        // out.
        repositionWindow()

        // The notification still fires several times in quick succession while a
        // display is being (un)plugged, and the geometry isn't final until the
        // last one. Keep a trailing debounced reposition so we also settle on
        // that final frame, without ever leaving the window stranded in between.
        screenParamsWorkItem?.cancel()
        let workItem = DispatchWorkItem { [weak self] in
            self?.repositionWindow()
        }
        screenParamsWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: workItem)
    }

    // MARK: - Fullscreen Auto-Hide
    //
    // On by default (FLUXION_NOTCH_HIDE_ON_FULLSCREEN=false opts out): while a
    // fullscreen app owns the notch's screen the island hides, and it fades back
    // in when fullscreen ends. This is the ONLY thing keeping the island off
    // fullscreen content: the system composites this window over native
    // fullscreen Spaces too (see the collectionBehavior note in init).
    //
    // Three signals, in priority order, cover the cases (all measured on macOS
    // 26 with a 0.25s poll + the Space-change notification):
    //   1. A newly created fullscreen Space (type-4 count jumped) — fires ~500ms
    //      BEFORE Current Space flips, and regardless of whether the incoming
    //      window covers the notch strip. This is what pre-empts the flash for
    //      *video* fullscreen, whose content sits below the notch.
    //   2. Current Space == 4 — the steady-state "we are on a native fullscreen
    //      Space" truth; maintains the hide after the transition completes.
    //   3. Another app's borderless window covering the full frame — the "fake"
    //      fullscreen of some players/games that never creates a Space at all.

    /// Supersedes any in-flight fade so an interrupted hide can't order the
    /// window out after a restore has already begun (and vice versa).
    private var fadeGeneration = 0
    private static let fadeDuration: TimeInterval = 0.25
    /// How long the entry pre-empt (signal 1) keeps the notch hidden while
    /// waiting for Current Space to flip. Comfortably longer than the measured
    /// ~500ms gap, so a slow transition still bridges; short enough that a
    /// fullscreen Space created on a *different* display (which never flips us
    /// to type 4) un-strands the notch quickly.
    private static let fullscreenEntryGrace: TimeInterval = 2.0

    private func updateFullscreenVisibility() {
        guard let win = window else { return }
        let hideEnabled = (appDelegate.envVals["FLUXION_NOTCH_HIDE_ON_FULLSCREEN"] ?? "true")
            .lowercased() == "true"

        // Opted out: never auto-hide, and undo any hide left over from when
        // the preference was previously on.
        guard hideEnabled else {
            if hiddenForFullscreen {
                restoreAfterFullscreen(win)
            }
            fullscreenEntryDeadline = nil
            return
        }

        let screen = findTargetScreen()
        let spaces = fullscreenSpaces(for: screen)

        // The notch follows the mouse across displays, so the target display can
        // change between ticks. When it does, re-baseline the count instead of
        // reading a jump: moving onto a display that merely has a fullscreen app
        // parked in another of its Spaces must not be mistaken for a fresh
        // fullscreen transition and hide the notch.
        let screenChanged = spaces.displayID != lastFullscreenDisplayID
        lastFullscreenDisplayID = spaces.displayID

        // Signal 1: a fullscreen Space just appeared — arm the entry grace so we
        // stay hidden across the pre-flip gap. Only a genuine *increase* on the
        // *same* display arms it, so a fullscreen app merely parked in another
        // Space doesn't keep the notch hidden while you work on the desktop.
        if !screenChanged, spaces.count > lastFullscreenSpaceCount {
            fullscreenEntryDeadline = Date().addingTimeInterval(Self.fullscreenEntryGrace)
        }
        lastFullscreenSpaceCount = spaces.count

        let onFullscreenSpace = spaces.currentIsFullscreen
        // The flip completed (signal 2 now holds), so the entry pre-empt has
        // done its job — retire it and let the steady-state signal carry the
        // hide, rather than letting a stale deadline linger.
        if onFullscreenSpace {
            fullscreenEntryDeadline = nil
        }
        let entering = fullscreenEntryDeadline.map { Date() < $0 } ?? false

        let shouldHide = onFullscreenSpace || entering || otherAppCoversFullFrame(on: screen)

        if shouldHide {
            // Only record that *we* hid it if it was actually visible, so a
            // notch hidden for some other reason is left alone on restore.
            if win.isVisible, !hiddenForFullscreen {
                hideForFullscreen(win)
            }
        } else if hiddenForFullscreen {
            restoreAfterFullscreen(win)
        }
    }

    // Hiding is deliberately INSTANT — no fade-out. For native fullscreen the
    // clock is the whole point: the entry signal (new fullscreen Space) leads
    // the moment the system re-composites the notch over the new Space by only
    // ~500ms, so a hide that lands inside the transition animation must take
    // effect immediately; a fade would spend that entire budget melting out and
    // hand back the "reappears for an instant" glitch. During the transition the
    // screen is covered anyway, so nobody sees an instant vanish. Only the
    // restore direction fades (fadeDuration), where the notch comes back over a
    // calm desktop.
    private func hideForFullscreen(_ win: NSWindow) {
        hiddenForFullscreen = true
        fadeGeneration += 1
        // Drop any open tray/panel so the island comes back collapsed instead
        // of reappearing mid-peek or as the full expanded card.
        if model.notchState != .collapsed {
            collapse()
        }
        win.alphaValue = 0
        win.orderOut(nil)
    }

    private func restoreAfterFullscreen(_ win: NSWindow) {
        hiddenForFullscreen = false
        fadeGeneration += 1
        repositionWindow()
        win.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { context in
            context.duration = Self.fadeDuration
            win.animator().alphaValue = 1
        }
    }

    /// The fullscreen picture for `screen`, read from the private SkyLight
    /// managed-display-spaces list in a single pass: whether the Current Space
    /// is a native-fullscreen Space (type 4), and how many type-4 Spaces exist
    /// on the display right now (a jump in that count is the early entry signal —
    /// see updateFullscreenVisibility). Scoped per display by UUID so a
    /// fullscreen app on another monitor never hides the notch on this one.
    private struct FullscreenSpaces {
        let currentIsFullscreen: Bool
        let count: Int
        let displayID: CGDirectDisplayID?
    }

    private func fullscreenSpaces(for screen: NSScreen) -> FullscreenSpaces {
        guard let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else {
            return FullscreenSpaces(currentIsFullscreen: false, count: 0, displayID: nil)
        }
        let displayID = CGDirectDisplayID(number.uint32Value)
        guard let uuidRef = CGDisplayCreateUUIDFromDisplayID(displayID)?.takeRetainedValue(),
              let uuid = CFUUIDCreateString(nil, uuidRef) as String?,
              let displays = CGSCopyManagedDisplaySpaces(CGSMainConnectionID()) as? [[String: Any]] else {
            return FullscreenSpaces(currentIsFullscreen: false, count: 0, displayID: displayID)
        }
        for display in displays where display["Display Identifier"] as? String == uuid {
            let currentIsFullscreen = (display["Current Space"] as? [String: Any])?["type"] as? Int == 4
            let count = (display["Spaces"] as? [[String: Any]])?
                .filter { $0["type"] as? Int == 4 }.count ?? 0
            return FullscreenSpaces(currentIsFullscreen: currentIsFullscreen, count: count, displayID: displayID)
        }
        return FullscreenSpaces(currentIsFullscreen: false, count: 0, displayID: displayID)
    }

    /// CGWindowList reports bounds in a top-left origin space anchored to the
    /// primary display; NSScreen uses a bottom-left origin. Flip Y so a window's
    /// reported bounds can be compared against this screen's full frame.
    private func cgFrame(for screen: NSScreen) -> CGRect {
        let primaryHeight = NSScreen.screens.first(where: { $0.frame.origin == .zero })?.frame.height
            ?? NSScreen.main?.frame.height
            ?? screen.frame.height
        let f = screen.frame
        return CGRect(x: f.minX, y: primaryHeight - f.maxY, width: f.width, height: f.height)
    }

    /// True if some other app has an on-screen, normal-layer window whose bounds
    /// match `screen`'s full frame. Matching the *full* frame (including the
    /// menu-bar row) is what distinguishes a borderless fullscreen window from a
    /// merely maximized/zoomed one, whose top stops below the menu bar.
    private func otherAppCoversFullFrame(on screen: NSScreen) -> Bool {
        let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
        guard let list = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
            return false
        }
        let target = cgFrame(for: screen)
        let myPID = Int(ProcessInfo.processInfo.processIdentifier)
        let tol: CGFloat = 2
        for info in list {
            guard let layer = info[kCGWindowLayer as String] as? Int, layer == 0,
                  let pid = info[kCGWindowOwnerPID as String] as? Int, pid != myPID,
                  let boundsDict = info[kCGWindowBounds as String] as? NSDictionary,
                  let bounds = CGRect(dictionaryRepresentation: boundsDict as CFDictionary) else {
                continue
            }
            if abs(bounds.minX - target.minX) <= tol,
               abs(bounds.minY - target.minY) <= tol,
               abs(bounds.width - target.width) <= tol,
               abs(bounds.height - target.height) <= tol {
                return true
            }
        }
        return false
    }

    func windowDidBecomeKey(_ notification: Notification) {
        NSLog("FluxionNotch: windowDidBecomeKey")
    }
    
    func windowDidResignKey(_ notification: Notification) {
        // Automatically collapse back when user clicks outside!
        NSLog("FluxionNotch: windowDidResignKey (clicks outside detected). Collapsing...")
        collapse()
    }
    
    private func findTargetScreen() -> NSScreen {
        let mouseLoc = NSEvent.mouseLocation
        if let screen = NSScreen.screens.first(where: { $0.frame.contains(mouseLoc) }) {
            return screen
        }
        if let mainScreen = NSScreen.main {
            return mainScreen
        }
        if let notchedScreen = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) {
            return notchedScreen
        }
        return NSScreen.screens.first ?? NSScreen()
    }
    
    // MARK: - Peek Bubble Focus

    /// Lane the bubble points at before the pointer has chosen one: the
    /// provider that most wants attention, so entering peek already answers
    /// "what's about to run out" without a second movement.
    /// The lane the pointer is already over as peek opens, mapped through the
    /// tray geometry the tray is about to have.
    ///
    /// Without this the bubble opened on the most urgent provider and then
    /// re-pointed lane by lane as the pointer's own position was read — so
    /// hovering the rightmost provider played Claude, then Codex, then
    /// Antigravity before settling. The pointer is by definition on the island
    /// when peek opens, so it, not urgency, is the honest initial answer.
    private func peekFocusIndexUnderPointer() -> Int? {
        guard model.providers.count > 1 else { return nil }
        let screen = findTargetScreen()
        let layoutCount = notchLayoutCount(model.providers)
        let hasNotch = getPhysicalNotchInfo(screen: screen) != nil
        let trayWidth = model.peekTrayWidth(
            collapsedBase: hasNotch ? getCollapsedWidth(screen: screen, count: layoutCount) : 0,
            hasNotch: hasNotch
        )
        guard trayWidth > 0 else { return nil }
        // Peek keeps the strip's geometry, so the anchors measured while
        // collapsed are already the right ones — the provider under the pointer
        // now is the provider under the pointer a moment later.
        let left = getCapsuleCenterX(screen: screen, state: .peek, count: layoutCount) - trayWidth / 2
        return model.providerIndex(atCardX: NSEvent.mouseLocation.x - left)
    }

    /// Fallback when the pointer can't be mapped to a lane: the provider that
    /// most wants attention.
    private func defaultPeekFocusIndex() -> Int? {
        guard model.usesBubblePeek else { return nil }
        let presenter = NotchQuotaPresenter(now: Date())
        let ranked = model.providers.enumerated().min { lhs, rhs in
            presenter.attentionRank(for: lhs.element) < presenter.attentionRank(for: rhs.element)
        }
        return ranked?.offset
    }

    /// Stop tracking lanes, but leave `peekFocusIndex` where it is.
    ///
    /// Clearing it here used to reset the bubble to lane 0 while it was still
    /// on screen: collapse only *starts* the removal transition, so the fading
    /// bubble kept rendering, read the nil back as lane 0, and visibly walked
    /// its tail and contents back across the tray on the way out. The stale
    /// index costs nothing — it is read only during peek, and mouseEntered
    /// always sets a fresh one.
    private func stopPeekFocusTracking() {
        cancelPeekOpen()
        peekFocusWorkItem?.cancel()
        peekFocusWorkItem = nil
        pendingPeekFocusIndex = nil
        model.peekBubbleTailAnimates = false
        stopPeekWatchdog()
    }

    /// Peek closes on mouseExited — except when that exit is never delivered.
    ///
    /// A tracking area rebuilt while the pointer is already inside it leaves
    /// AppKit believing the pointer is outside, so the matching exit never
    /// comes and the callout stays on screen long after the pointer has gone.
    /// That happens for real now that peek can open mid-strip (the pointer
    /// crosses off the camera housing onto a shoulder) rather than only at the
    /// moment the pointer crosses into the window.
    ///
    /// Rather than fight the tracking area's inside/outside bookkeeping — where
    /// assumeInside would only move the failure to the cases where the pointer
    /// really is outside — this checks the pointer's actual position a few
    /// times a second and closes exactly the way an exit would.
    private func startPeekWatchdog() {
        stopPeekWatchdog()
        let timer = Timer(timeInterval: 0.2, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            guard self.model.notchState == .peek, !self.isCollapsing else { return }
            guard !self.peekIslandContainsPointer(NSEvent.mouseLocation) else { return }
            self.mouseExited()
        }
        // .common so it keeps firing while the user is dragging or scrolling
        // elsewhere, which is exactly when a stuck callout is most obvious.
        RunLoop.main.add(timer, forMode: .common)
        peekWatchdog = timer
    }

    private func stopPeekWatchdog() {
        peekWatchdog?.invalidate()
        peekWatchdog = nil
    }

    /// Hold peek back until the pointer has left the island once.
    ///
    /// Closing the expanded panel is the one moment the user has given an
    /// explicit "not now": the same click also resigns key and deactivates the
    /// app, so they are on their way back to their own window. Re-asserting a
    /// callout over it honours half of that dismissal. Everywhere else the
    /// pointer resting on a slot is reason enough to show peek, so this is the
    /// only suppression there is.
    ///
    /// It lifts on the pointer leaving, never on a timer: a callout that
    /// reappears on its own after a delay is worse than either behaviour, and
    /// "move off and back" is a gesture the user already makes constantly.
    /// The poll is there because the release condition must not depend on an
    /// exit event being delivered — if one were missed, hovering the island
    /// would stay dead with no way back.
    private func suppressPeekUntilPointerLeaves() {
        peekSuppressed = true
        peekSuppressionWatchdog?.invalidate()
        let timer = Timer(timeInterval: 0.2, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            guard self.peekSuppressed else {
                self.endPeekSuppression()
                return
            }
            if !self.pointerIsOverCollapsedStrip() {
                self.endPeekSuppression()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        peekSuppressionWatchdog = timer
    }

    private func endPeekSuppression() {
        peekSuppressed = false
        peekSuppressionWatchdog?.invalidate()
        peekSuppressionWatchdog = nil
    }

    /// Lane hit-testing for the peek bubble. Coordinates are in the container
    /// view's (unflipped) space, so the card hangs from `bounds.maxY`.
    ///
    /// Returns nil to mean "keep the current lane" — the pointer is over the
    /// bubble itself, in the transparent overshoot halo, or inside the
    /// hysteresis band around a lane boundary. It never returns "no lane": once
    /// peek is open the bubble always points somewhere.
    private func peekTargetIndex(at point: CGPoint, in bounds: CGRect) -> Int? {
        guard model.providers.count > 1 else { return nil }

        let trayTop = bounds.maxY
        let trayBottom = trayTop - model.peekHeight
        // Below the strip is the bubble's own band: while the pointer is in
        // there it belongs to whichever provider the bubble is already showing,
        // so travelling down into the callout can never re-target it.
        guard point.y <= trayTop, point.y >= trayBottom else { return nil }

        let cardX = point.x - (bounds.midX - model.peekTrayWidth / 2)
        // The camera housing is hardware, not a slot. Crossing it on the way
        // from one shoulder to the other must not hand the bubble to whichever
        // provider happens to be nearer the middle.
        if model.hasNotch, cardX > model.notchLeft, cardX < model.notchRight {
            return nil
        }
        guard let candidate = model.providerIndex(atCardX: cardX) else { return nil }

        // Hysteresis: the slot that already owns the bubble keeps it until the
        // pointer is clearly past the boundary, so a hand resting on the divide
        // doesn't flip the callout back and forth.
        //
        // Scaled to the distance between the two slots. The number-inside gauge
        // styles shrink each slot to a bare 18pt ring, and the two that share a
        // shoulder then sit 24pt apart — against which a flat 12pt dead band
        // spans the entire gap, so the bubble could never change hands however
        // long the pointer waited on its target.
        if let current = model.peekFocusIndex, current != candidate,
           let here = model.providerAnchors[current],
           let there = model.providerAnchors[candidate] {
            let boundary = (here + there) / 2
            let slack = min(Self.peekFocusHysteresis, abs(here - there) * 0.2)
            if abs(cardX - boundary) < slack { return nil }
        }
        return candidate
    }

    /// True when the pointer is over the collapsed strip itself.
    ///
    /// The tracking area covers the WINDOW, and the window is only resized back
    /// to strip size ~0.3s after the state flips to collapsed — so between an
    /// expanded panel closing and its frame shrinking, the whole ~624x370
    /// region still delivers events. Answering "is the pointer on the island"
    /// from the window's current bounds therefore says yes hundreds of points
    /// away. This computes the strip's real rect from the screen instead, the
    /// same way updateWindowFrame places it, so it is immune to a frame that
    /// has not caught up. The flare's wings are included: they are visible
    /// black, and the tracking area reaches them.
    private func pointerIsOverCollapsedStrip() -> Bool {
        let screen = findTargetScreen()
        let count = notchLayoutCount(model.providers)
        let bodyWidth = getCollapsedWidth(screen: screen, count: count)
        let width = bodyWidth + 2 * notchFlareRadius(forWidth: bodyWidth)
        let height = getPhysicalNotchInfo(screen: screen) != nil ? screen.safeAreaInsets.top : 32
        let strip = CGRect(
            x: getCapsuleCenterX(screen: screen, state: .collapsed, count: count) - width / 2,
            y: screen.frame.maxY - height,
            width: width,
            // Screen coordinates grow upward, so this reaches past the top edge.
            height: height + Self.screenEdgeSlop
        )
        return strip.contains(NSEvent.mouseLocation)
    }

    /// True while the pointer sits over the physical camera housing. Nothing
    /// of ours is drawn there — it is the display's own cutout — so it neither
    /// opens peek nor claims a provider.
    private func pointerIsOverPhysicalNotch() -> Bool {
        guard let notch = getPhysicalNotchInfo(screen: findTargetScreen()) else { return false }
        let x = NSEvent.mouseLocation.x
        return x > notch.left && x < notch.right
    }

    func peekPointerMoved(to point: CGPoint, in bounds: CGRect) {
        // Entering the strip over the camera housing doesn't open peek, so pick
        // it up here the moment the pointer reaches a shoulder that has content.
        if model.notchState == .collapsed {
            // Only a slot-targeting peek opens from a move: it is how the
            // pointer crossing off the camera housing onto a shoulder gets
            // picked up. Without a dead zone there is nothing to recover from,
            // and mouseEntered has already started the clock.
            guard model.peekTargetsSlots else { return }
            // Stepping off the strip — or onto the housing — stops the clock,
            // so a crossing never banks progress toward opening.
            guard peekMayOpenNow() else {
                cancelPeekOpen()
                return
            }
            schedulePeekOpen()
            return
        }
        guard model.notchState == .peek, model.peekTargetsSlots else { return }
        guard let candidate = peekTargetIndex(at: point, in: bounds) else { return }
        guard candidate != model.peekFocusIndex else {
            // Back on the lane already showing — drop any pending switch.
            peekFocusWorkItem?.cancel()
            peekFocusWorkItem = nil
            pendingPeekFocusIndex = nil
            return
        }
        // Already scheduled for this lane; let the pending commit run.
        guard candidate != pendingPeekFocusIndex else { return }

        // Commit delay: crossing a lane on the way somewhere else shouldn't
        // drag the bubble along with the pointer.
        peekFocusWorkItem?.cancel()
        pendingPeekFocusIndex = candidate
        let workItem = DispatchWorkItem { [weak self] in
            guard let self = self else { return }
            self.pendingPeekFocusIndex = nil
            guard self.model.notchState == .peek else { return }
            // Opt the tail into animating from here on: this is a real
            // re-point, not the initial placement.
            self.model.peekBubbleTailAnimates = true
            withAnimation(.spring(response: 0.34, dampingFraction: 0.82)) {
                self.model.peekFocusIndex = candidate
            }
        }
        peekFocusWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.peekFocusCommitDelay, execute: workItem)
    }

    // MARK: - Hover Handlers
    func mouseEntered() {
        collapseWorkItem?.cancel()

        // Landing on the camera housing opens nothing; peekPointerMoved picks
        // it up once the pointer reaches a shoulder that has content. The state,
        // collapse-in-progress and post-dismissal-suppression checks live in
        // peekMayOpenNow with the rest.
        guard peekMayOpenNow() else { return }
        schedulePeekOpen()
    }

    /// The single answer to "may peek open for the pointer where it is right
    /// now?" — asked when the pointer arrives, again on every move while
    /// collapsed, and once more when the hover-intent clock fires.
    ///
    /// It is one function because it was three. Each caller re-derived the same
    /// set of conditions, and the copy inside the timer was missing the
    /// usesBubblePeek qualifier on the camera-housing check — which, since the
    /// housing is most of a single provider's strip, meant a solo island could
    /// never be opened at all. Conditions that must agree across call sites do
    /// not get to live in three places.
    private func peekMayOpenNow() -> Bool {
        guard model.notchState == .collapsed, !isCollapsing, !peekSuppressed else { return false }
        // An entry into a window that has not finished shrinking back from
        // expanded is not an entry into the strip.
        guard pointerIsOverCollapsedStrip() else { return false }
        // The camera housing holds nothing, so it opens nothing — but only
        // where slots are being targeted. One provider has no slots to
        // disambiguate, and gating it there would wall off most of its strip.
        if model.peekTargetsSlots, pointerIsOverPhysicalNotch() { return false }
        return true
    }

    /// Start the hover-intent clock, unless it is already running. Continued
    /// movement inside the strip must not restart it: the clock measures how
    /// long the pointer has been on the island, not how long it has been still.
    private func schedulePeekOpen() {
        guard peekOpenWorkItem == nil else { return }
        let workItem = DispatchWorkItem { [weak self] in
            guard let self = self else { return }
            self.peekOpenWorkItem = nil
            // Re-check at fire time rather than trusting that no exit event was
            // missed on the way here. AppKit has dropped those on us twice
            // already; this way a missed exit costs an open that doesn't
            // happen, never a callout that will not go away.
            guard self.peekMayOpenNow() else { return }
            self.openPeek()
        }
        peekOpenWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.peekOpenDelay, execute: workItem)
    }

    private func cancelPeekOpen() {
        peekOpenWorkItem?.cancel()
        peekOpenWorkItem = nil
    }

    private func openPeek() {
        cancelPeekOpen()
        // Opening from a pointer move (see peekPointerMoved) skips mouseEntered
        // entirely, so cancel any pending collapse here rather than there.
        collapseWorkItem?.cancel()
        endPeekSuppression()
        model.peekBubbleTailAnimates = false
        model.peekFocusIndex = peekFocusIndexUnderPointer() ?? defaultPeekFocusIndex()
        let targetSize = getWindowSize(for: .peek)
        updateWindowFrame(to: targetSize)  // sets windowHasPeekHalo = true

        // Match the design draft's island curve — cubic-bezier(.32,1.42,.5,1)
        // over .52s — which gives peek the same Q-bounce overshoot as expand
        // instead of the old over-damped (0.82) settle.
        withAnimation(.spring(response: 0.45, dampingFraction: 0.56)) {
            model.notchState = .peek
        }
        // The window was resized to peek size *before* the state flipped, so the
        // tracking area was last computed as collapsed (whole window). Recompute
        // now that we're in peek so it excludes the overshoot halo.
        window?.contentView?.updateTrackingAreas()
        startPeekWatchdog()
    }
    
    func mouseExited() {
        cancelPeekOpen()
        collapseWorkItem?.cancel()
        
        guard model.notchState == .peek else { return }
        
        let workItem = DispatchWorkItem { [weak self] in
            guard let self = self else { return }
            // Re-check against what is actually drawn, not the full window
            // frame, so neither the transparent overshoot halo nor the band
            // reserved for a taller callout keeps peek alive.
            if self.peekIslandContainsPointer(NSEvent.mouseLocation) {
                return
            }
            
            self.isCollapsing = true
            self.stopPeekFocusTracking()
            self.updateNotchCoordinates(for: .collapsed)
            withAnimation(.spring(response: 0.35, dampingFraction: 0.82)) {
                self.model.notchState = .collapsed
            }
            
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
                guard let self = self else { return }
                self.isCollapsing = false
                guard self.model.notchState == .collapsed else { return }
                let targetSize = self.getWindowSize(for: .collapsed)
                self.updateWindowFrame(to: targetSize)  // clears windowHasPeekHalo
            }
        }
        collapseWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15, execute: workItem)
    }
    
    func toggleExpand() {
        collapseWorkItem?.cancel()
        stopPeekFocusTracking()
        NSLog("FluxionNotch: toggleExpand called. Current state: \(model.notchState)")

        if model.notchState == .expanded {
            allowsKeyWindow = false
            isCollapsing = true
            suppressPeekUntilPointerLeaves()
            NSLog("FluxionNotch: Collapsing from expanded state.")
            updateNotchCoordinates(for: .collapsed)
            withAnimation(.spring(response: 0.32, dampingFraction: 0.88)) {
                model.notchState = .collapsed
            }
            if let win = window, win.isKeyWindow {
                win.resignKey()
            }
            NSApp.deactivate()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.32) { [weak self] in
                guard let self = self else { return }
                self.isCollapsing = false
                guard self.model.notchState == .collapsed else { return }
                let targetSize = self.getWindowSize(for: .collapsed)
                self.updateWindowFrame(to: targetSize)
            }
        } else {
            allowsKeyWindow = true
            // Refresh history the moment the user opens the panel: the
            // launch-time retry chain gives up after ~8s (e.g. when the web
            // server is still cold-starting), and waiting out the 30s poll
            // with placeholders showing reads as "stuck". The endpoint is
            // local and ~40ms warm, so an extra fetch per open is free.
            fetchHistory()
            appDelegate.refreshCodexQuotaForPanelIfNeeded()
            let targetSize = getWindowSize(for: .expanded)
            updateWindowFrame(to: targetSize)  // clears windowHasPeekHalo

            NSLog("FluxionNotch: Activating app and making window key.")
            NSApp.activate(ignoringOtherApps: true)
            window?.makeKeyAndOrderFront(nil)
            
            withAnimation(.spring(response: 0.35, dampingFraction: 0.56)) {
                model.notchState = .expanded
            }
        }
    }
    
    func collapse(completion: (() -> Void)? = nil) {
        collapseWorkItem?.cancel()
        stopPeekFocusTracking()
        guard model.notchState != .collapsed else {
            completion?()
            return
        }
        NSLog("FluxionNotch: collapse called. Current state: \(model.notchState)")

        // Escape and the other deliberate dismissals of the panel get the same
        // treatment as clicking it shut.
        if model.notchState == .expanded {
            suppressPeekUntilPointerLeaves()
        }
        allowsKeyWindow = false
        isCollapsing = true
        updateNotchCoordinates(for: .collapsed)
        withAnimation(.spring(response: 0.32, dampingFraction: 0.88)) {
            model.notchState = .collapsed
        }
        if let win = window, win.isKeyWindow {
            win.resignKey()
        }
        NSApp.deactivate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.32) { [weak self] in
            guard let self = self else { return }
            self.isCollapsing = false
            if self.model.notchState == .collapsed {
                let targetSize = self.getWindowSize(for: .collapsed)
                self.updateWindowFrame(to: targetSize)
            }
            completion?()
        }
    }
}
