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

struct ProviderHistoryStats {
    let tokens: Int
    let input: Int
    let output: Int
    let cacheCreation: Int
    let cacheRead: Int
    let cost: Double
}

// MARK: - Notch Data Model
class NotchDataModel: ObservableObject {
    @Published var isUpgradingBackend: Bool = false
    @Published var providers: [ProviderUsage] = []
    @Published var justGranted: Int = 0
    @Published var todayStats: [String: ProviderHistoryStats] = [:]
    @Published var notchState: NotchState = .collapsed
    @Published var page: Int = 0
    @Published var hasNotch: Bool = false
    @Published var safeAreaTop: CGFloat = 0
    @Published var silentStyle: String = "all"
    // Which reset window(s) the peek status line counts down: "5h" (rolling
    // window, default), "week" (weekly cap), or "both" (two labeled timers
    // stacked vertically, which makes the peek tray taller).
    @Published var peekReset: String = "5h"
    @Published var notchWidth: CGFloat = 0
    @Published var notchLeft: CGFloat = 0
    @Published var notchRight: CGFloat = 0
    @Published var collapsedWidth: CGFloat = 180
    @Published var expandedPageHeight: CGFloat = 232
    // Natural width of the peek segment row, measured by the hidden twin in
    // NotchIslandView+Peek (see PeekContentWidthKey). Only meaningful on
    // non-notched displays; 0 until the first peek layout has run.
    @Published var peekContentWidth: CGFloat = 0
    // Natural width of the collapsed row, measured the same way (see
    // CollapsedContentWidthKey / NotchIslandView+Collapsed).
    @Published var collapsedContentWidth: CGFloat = 0

    // Peek tray width on a non-notched display. The tray wraps the measured
    // content with the 16pt side paddings plus slack so a countdown that gains
    // a digit between measurements doesn't clip. Until the first measurement
    // lands (first hover), fall back to per-count guesses close to the likely
    // result so the resize on first layout is small.
    func peekWidthNoNotch(count: Int) -> CGFloat {
        guard peekContentWidth > 1 else {
            return count == 1 ? 200 : (count == 3 ? 380 : 280)
        }
        return max(180, peekContentWidth + 32 + 8)
    }

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

    // Extra peek-tray height in "both" mode, where each segment stacks a header
    // row over two labeled countdowns instead of a single inline timer.
    static let peekBothExtraHeight: CGFloat = 34

    // Extra tray height for the one-line "updating components" caption shown
    // under the peek segments during a backend upgrade. Without it the
    // bottom-anchored content grows upward into the notch band and the
    // segments slide behind the physical notch.
    static let upgradeCaptionHeight: CGFloat = 15

    var peekHeight: CGFloat {
        // With a notch the tray keeps the notch band above the content row; a
        // non-notched pill has no band to mirror, so it hugs the content
        // (which the peek view centers vertically instead of bottom-anchoring).
        let base: CGFloat = hasNotch ? safeAreaTop + 36 : 44
        let both: CGFloat = peekReset == "both" ? Self.peekBothExtraHeight : 0
        let upgrade: CGFloat = isUpgradingBackend ? Self.upgradeCaptionHeight : 0
        return base + both + upgrade
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
        let options: NSTrackingArea.Options = [.mouseEnteredAndExited, .activeAlways]
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
    func visibleContentRect(in bounds: CGRect) -> CGRect {
        guard windowHasPeekHalo else { return bounds }
        return CGRect(
            x: bounds.minX + Self.peekMarginW / 2,
            y: bounds.minY + Self.peekMarginH,
            width: bounds.width - Self.peekMarginW,
            height: bounds.height - Self.peekMarginH
        )
    }
    var allowsKeyWindow: Bool = false
    var isCollapsing: Bool = false
    private var webViewTimer: Timer?
    private var screenTimer: Timer?
    private var collapseWorkItem: DispatchWorkItem?
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

    // History fetch retry: the web server may not be listening yet right after
    // launch, so a single attempt would leave "0" until the 30s poll. Each
    // fetchHistory() starts a fresh attempt chain (newer chains supersede older
    // ones via the generation token) that backs off until it succeeds.
    private var fetchGeneration = 0
    private let fetchRetryDelays: [TimeInterval] = [0.5, 1.0, 2.0, 4.0]
    
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
        // Intentionally NO .fullScreenAuxiliary: that flag is what makes an
        // overlay follow into — and draw on top of — another app's native
        // fullscreen Space (fullscreen video, games, Keynote). Without it the
        // notch shows over the regular desktop and maximized windows, but
        // politely gets out of the way when an app goes truly fullscreen.
        // .stationary keeps it from drifting during Spaces transitions;
        // .ignoresCycle keeps it out of the ⌘Tab window cycle.
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
        
        // Start screen tracking timer (every 1.0 second) to follow the mouse
        // cursor screen and to hide the notch while another app is fullscreen.
        // The timer catches borderless "fake" fullscreen (video players, games,
        // in-browser video) which does NOT change Spaces and so emits no
        // notification; native fullscreen is handled instantly below.
        screenTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
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
                // Headline volume uses `generated_tokens` (input + output +
                // cache_creation), NOT `total_tokens` — i.e. cache *reads* are
                // excluded. This keeps the notch's big number consistent with
                // the web console's "Generated" hero; total_tokens would be
                // inflated by repeated cached context and confuse the input →
                // output breakdown shown below it.
                let generated_tokens: Int
                let input_tokens: Int
                let output_tokens: Int
                let cache_creation_tokens: Int?
                let cache_read_tokens: Int?
            }

            struct HistoryPayload: Codable {
                let by_model: [HistoryModelStat]?
            }

            // A valid payload (even an empty one = no usage today) is a success;
            // anything else — connection refused, timeout, non-200, undecodable —
            // is a miss worth retrying while the server comes up.
            var parsed: [String: ProviderHistoryStats]? = nil
            if let data = data, error == nil,
               (response as? HTTPURLResponse)?.statusCode == 200,
               let payload = try? JSONDecoder().decode(HistoryPayload.self, from: data),
               let byModel = payload.by_model {
                var stats: [String: ProviderHistoryStats] = [:]
                for m in byModel {
                    let pKey = m.provider.lowercased()
                    let existing = stats[pKey] ?? ProviderHistoryStats(tokens: 0, input: 0, output: 0, cacheCreation: 0, cacheRead: 0, cost: 0.0)
                    stats[pKey] = ProviderHistoryStats(
                        tokens: existing.tokens + m.generated_tokens,
                        input: existing.input + m.input_tokens,
                        output: existing.output + m.output_tokens,
                        cacheCreation: existing.cacheCreation + (m.cache_creation_tokens ?? 0),
                        cacheRead: existing.cacheRead + (m.cache_read_tokens ?? 0),
                        cost: existing.cost + m.cost
                    )
                }
                parsed = stats
            }

            DispatchQueue.main.async {
                // A newer fetchHistory() started; abandon this stale chain.
                guard generation == self.fetchGeneration else { return }
                if let stats = parsed {
                    self.model.todayStats = stats
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
        
        switch model.silentStyle {
        case "ambient":
            leftW = (count >= 2) ? 7 : 0
            rightW = (count >= 3) ? 20 : 7
        case "lowest":
            leftW = 0
            rightW = 48
        default: // "all"
            if count == 1 {
                leftW = 0
                rightW = 48
            } else if count == 2 {
                leftW = 48
                rightW = 48
            } else {
                leftW = 48
                rightW = 102
            }
        }
        
        let gOuter: CGFloat = 13
        let gNotch: CGFloat = 10
        
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
            cardWidth = hasNotch ? (model.collapsedWidth + 80) : model.peekWidthNoNotch(count: count)
        case .expanded:
            cardWidth = notchExpandedWidth(providers: model.providers)
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
            return NSSize(width: w, height: h)
        case .peek:
            let w: CGFloat
            // "both" mode stacks two labeled timers per segment, so the tray is
            // taller. peekReset is a stable preference (set before reposition),
            // so reading it from the model here is safe. Mirrors peekHeight,
            // including the upgrade caption row.
            var extra: CGFloat = model.peekReset == "both" ? NotchDataModel.peekBothExtraHeight : 0
            if model.isUpgradingBackend {
                extra += NotchDataModel.upgradeCaptionHeight
            }
            // Transparent margin so the spring's overshoot can extend past the
            // tray edges instead of being clipped by the window border — same
            // trick the expanded panel uses (see below). Without this the peek
            // Q-bounce is invisible (the width/height overshoot is cut off at
            // the window edge), so it reads as flat compared to the design. The
            // halo is excluded from hit-testing via visibleContentRect(in:).
            let peekMarginW = Self.peekMarginW
            let peekMarginH = Self.peekMarginH
            if hasNotch {
                // Mirror targetWidth's peek rule: only 3-provider non-Both keeps
                // the +80; all others hug the collapsed width.
                let bonus: CGFloat = (count == 3 && model.peekReset != "both") ? 80 : 0
                w = getCollapsedWidth(screen: screen, count: count) + bonus
                return NSSize(width: w + peekMarginW, height: safeAreaTop + 36 + extra + peekMarginH)
            } else {
                w = model.peekWidthNoNotch(count: count)
                return NSSize(width: w + peekMarginW, height: 44 + extra + peekMarginH)
            }
        case .expanded:
            let w: CGFloat = notchExpandedWidth(providers: model.providers)
            // Add a transparent window margin (width + 60, height + 40) so the SwiftUI
            // card can bounce and overshoot freely without getting clipped by window borders.
            return NSSize(width: w + 60, height: model.expandedCardHeight + 40)
        }
    }
    
    deinit {
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
        // Native fullscreen enter/exit changes the active Space — re-evaluate
        // immediately rather than waiting for the next 1s tick.
        updateFullscreenVisibility()
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

    // MARK: - Fullscreen Auto-Hide (opt-in)
    //
    // Off by default: in fullscreen the notch strip is otherwise just black, and
    // native-fullscreen content sits *below* it, so the notch fills dead space
    // rather than covering anything. Users who prefer the stock all-black look
    // can enable FLUXION_NOTCH_HIDE_ON_FULLSCREEN, which orders the notch out
    // while a fullscreen app owns the notch's screen and restores it afterward.

    private func updateFullscreenVisibility() {
        guard let win = window else { return }
        let hideEnabled = (appDelegate.envVals["FLUXION_NOTCH_HIDE_ON_FULLSCREEN"] ?? "false")
            .lowercased() == "true"

        // Not opted in: never auto-hide, and undo any hide left over from when
        // the preference was previously on.
        guard hideEnabled else {
            if hiddenForFullscreen {
                hiddenForFullscreen = false
                win.orderFrontRegardless()
                repositionWindow()
            }
            return
        }

        let screen = findTargetScreen()
        if isFullscreenActive(on: screen) {
            // Only record that *we* hid it if it was actually visible, so a
            // notch hidden for some other reason is left alone on restore.
            if win.isVisible {
                hiddenForFullscreen = true
                win.orderOut(nil)
            }
        } else if hiddenForFullscreen {
            hiddenForFullscreen = false
            win.orderFrontRegardless()
            repositionWindow()
        }
    }

    /// A fullscreen app owns `screen` when either its current Space is a native
    /// fullscreen Space (the reliable signal — Space type 4) or some other app
    /// has a borderless window covering the screen's full frame (the "fake"
    /// fullscreen of some video players/games, which creates no Space). The Space
    /// check stays steady through the brief moment a native-fullscreen window
    /// resizes to clear the notch, so it never flickers.
    private func isFullscreenActive(on screen: NSScreen) -> Bool {
        if currentSpaceType(for: screen) == 4 { return true }
        return otherAppCoversFullFrame(on: screen)
    }

    /// The macOS Space type for `screen`'s currently visible Space, read from the
    /// private SkyLight managed-display-spaces list. 4 == native fullscreen.
    private func currentSpaceType(for screen: NSScreen) -> Int {
        guard let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber,
              let uuidRef = CGDisplayCreateUUIDFromDisplayID(CGDirectDisplayID(number.uint32Value))?.takeRetainedValue(),
              let uuid = CFUUIDCreateString(nil, uuidRef) as String?,
              let spaces = CGSCopyManagedDisplaySpaces(CGSMainConnectionID()) as? [[String: Any]] else {
            return -1
        }
        for display in spaces {
            if display["Display Identifier"] as? String == uuid,
               let current = display["Current Space"] as? [String: Any] {
                return current["type"] as? Int ?? -1
            }
        }
        return -1
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
    
    // MARK: - Hover Handlers
    func mouseEntered() {
        collapseWorkItem?.cancel()
        
        guard !isCollapsing else { return }
        guard model.notchState == .collapsed else { return }
        
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
    }
    
    func mouseExited() {
        collapseWorkItem?.cancel()
        
        guard model.notchState == .peek else { return }
        
        let workItem = DispatchWorkItem { [weak self] in
            guard let self = self else { return }
            if let win = self.window {
                let mouseLoc = NSEvent.mouseLocation
                // Re-check against the visible card, not the full window frame,
                // so the transparent overshoot halo doesn't keep peek alive.
                let card = self.visibleContentRect(in: CGRect(origin: .zero, size: win.frame.size))
                    .offsetBy(dx: win.frame.minX, dy: win.frame.minY)
                if card.contains(mouseLoc) {
                    return
                }
            }
            
            self.isCollapsing = true
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
        NSLog("FluxionNotch: toggleExpand called. Current state: \(model.notchState)")
        
        if model.notchState == .expanded {
            allowsKeyWindow = false
            isCollapsing = true
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
        guard model.notchState != .collapsed else {
            completion?()
            return
        }
        NSLog("FluxionNotch: collapse called. Current state: \(model.notchState)")
        
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
