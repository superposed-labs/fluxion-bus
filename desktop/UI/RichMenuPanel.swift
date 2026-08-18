import AppKit
import Foundation

private struct RichActionHit {
    let rect: NSRect
    let action: () -> Void
}

private struct RichHoverHit {
    let id: String
    let rect: NSRect
}

final class RichMenuContainerView: NSView {
    let panelView: RichMenuPanelView
    private let effectView = NSVisualEffectView()

    init(panelView: RichMenuPanelView, frame: NSRect) {
        self.panelView = panelView
        super.init(frame: frame)
        wantsLayer = true
        layer?.cornerRadius = 13
        layer?.masksToBounds = true

        effectView.material = .popover
        effectView.blendingMode = .behindWindow
        effectView.state = .active
        effectView.frame = bounds
        effectView.autoresizingMask = [.width, .height]
        addSubview(effectView)

        panelView.frame = bounds
        panelView.autoresizingMask = [.width, .height]
        addSubview(panelView)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func layout() {
        super.layout()
        effectView.frame = bounds
        panelView.frame = bounds
    }
}

final class RichMenuPanelView: NSView {
    var providers: [ProviderUsage]
    var todayStats: [String: ProviderHistoryStats]
    var isFetchingHistory: Bool
    var historyMessage: String?
    var pendingUpdateVersion: String?
    var onRefresh: (() -> Void)?
    var onConsole: (() -> Void)?
    var onPreferences: (() -> Void)?
    var onUpdate: (() -> Void)?
    var onQuit: (() -> Void)?
    var onClose: (() -> Void)?

    private var actionHits: [RichActionHit] = []
    private var hoverHits: [RichHoverHit] = []
    private var hoverID: String?
    private var trackingAreaRef: NSTrackingArea?
    private var countdownTimer: Timer?
    private var resetsChipRect: NSRect?
    private let outerPad: CGFloat = 7
    private let innerPad: CGFloat = 10
    private let rowH: CGFloat = 30
    private let sectionPadTop: CGFloat = 4
    private let sectionPadBottom: CGFloat = 5
    private let separatorMarginX: CGFloat = 10
    private let sectionLine = NSColor.black.withAlphaComponent(0.09)
    private let text = NSColor(hex: "#202126")
    private let muted = NSColor(hex: "#8D8B94")
    private let blue = NSColor(hex: "#2F6FAE")
    private let green = NSColor(hex: "#2DBE68")
    private let red = NSColor(hex: "#FF3B3B")
    private let amber = NSColor(hex: "#D08A00")

    init(
        providers: [ProviderUsage],
        todayStats: [String: ProviderHistoryStats] = [:],
        isFetchingHistory: Bool = false,
        historyMessage: String? = nil,
        pendingUpdateVersion: String? = nil
    ) {
        self.providers = providers
        self.todayStats = todayStats
        self.isFetchingHistory = isFetchingHistory
        self.historyMessage = historyMessage
        self.pendingUpdateVersion = pendingUpdateVersion
        super.init(frame: NSRect(x: 0, y: 0, width: 486, height: 780))
        wantsLayer = true
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override var isFlipped: Bool { true }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window != nil {
            startCountdownTimer()
        } else {
            stopCountdownTimer()
        }
    }

    private func startCountdownTimer() {
        guard countdownTimer == nil else { return }
        countdownTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.needsDisplay = true
        }
    }

    private func stopCountdownTimer() {
        countdownTimer?.invalidate()
        countdownTimer = nil
    }

    func preferredHeight() -> CGFloat {
        var y: CGFloat = outerPad + 1
        y += 41

        for (idx, p) in providers.enumerated() {
            y += measuredProviderHeight(p)
            if idx < providers.count - 1 {
                y += 12
            }
        }

        y += 1
        y += 12
        y += measuredActionsHeight()
        y += outerPad + 1
        return min(ceil(y), 800)
    }

    override func draw(_ dirtyRect: NSRect) {
        actionHits.removeAll()
        hoverHits.removeAll()
        resetsChipRect = nil
        drawBackground()

        var y: CGFloat = outerPad + 1
        drawSummary(y: y)
        y += 41

        for (idx, p) in providers.enumerated() {
            drawProvider(p, y: &y)
            if idx < providers.count - 1 {
                y += 1
                drawLine(y)
                y += 11
            }
        }

        y += 1
        drawLine(y)
        y += 12
        drawActions(y: y)
        drawInfoTooltipIfNeeded()
        drawUpdateTooltipIfNeeded()
        drawResetsTooltipIfNeeded()
    }

    override func mouseDown(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        for hit in actionHits where hit.rect.contains(point) {
            hit.action()
            return
        }
    }

    override var acceptsFirstResponder: Bool { true }

    /// Mirrors the ⌘R / ⌘D / ⌘, / ⌘Q shortcuts that NSMenu provides for free in
    /// the native menu. performKeyEquivalent propagates through the key window's
    /// content view hierarchy, so this fires regardless of first-responder state.
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        guard event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command else {
            return false
        }
        switch event.charactersIgnoringModifiers?.lowercased() {
        case "r": onRefresh?(); return true
        case "d": onConsole?(); return true
        case ",": onPreferences?(); return true
        case "q": onQuit?(); return true
        default: return false
        }
    }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 { // Escape
            onClose?()
            return
        }
        super.keyDown(with: event)
    }

    override func updateTrackingAreas() {
        if let trackingAreaRef {
            removeTrackingArea(trackingAreaRef)
        }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.activeAlways, .inVisibleRect, .mouseEnteredAndExited, .mouseMoved],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
        trackingAreaRef = area
        super.updateTrackingAreas()
    }

    override func mouseMoved(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        let nextHover = hoverHits.first { $0.rect.contains(point) }?.id
        if nextHover != hoverID {
            hoverID = nextHover
            needsDisplay = true
        }
    }

    override func mouseExited(with event: NSEvent) {
        if hoverID != nil {
            hoverID = nil
            needsDisplay = true
        }
    }

    private func drawBackground() {
        let path = NSBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5), xRadius: 13, yRadius: 13)
        NSColor.white.withAlphaComponent(0.70).setFill()
        path.fill()
        NSColor.white.withAlphaComponent(0.70).setStroke()
        path.lineWidth = 0.5
        path.stroke()
    }

    private func drawSummary(y: CGFloat) {
        let rect = NSRect(x: outerPad + 1, y: y, width: bounds.width - (outerPad + 1) * 2, height: 37)
        let path = NSBezierPath(roundedRect: rect, xRadius: 8, yRadius: 8)
        NSColor(hex: "#EDF2FF").withAlphaComponent(0.88).setFill()
        path.fill()
        NSColor(hex: "#0A7CFF").withAlphaComponent(0.20).setStroke()
        path.lineWidth = 0.5
        path.stroke()

        draw(L10n.tr("menu.today"), x: rect.minX + 11, y: rect.minY + 9, size: 12.5, weight: .bold, color: text)
        let loadingCount = providers.filter { $0.status == "loading" }.count
        let okCount = providers.filter { $0.status == "ok" }.count
        let statusLabel = loadingCount > 0 ? L10n.tr("menu.status.running", loadingCount) : L10n.tr("menu.status.active", okCount)
        let statusWidth = pillWidth(statusLabel, size: 11, weight: .semibold, horizontalPadding: 34, minimum: 82)
        let runningRect = NSRect(x: rect.minX + 62, y: rect.minY + 8, width: statusWidth, height: 19)
        drawPill(runningRect, fill: NSColor(hex: "#DDEBE1").withAlphaComponent(0.85), stroke: .clear, text: "● \(statusLabel)", color: NSColor(hex: "#46484C"), size: 11, weight: .semibold, dotColor: green)

        let infoRect = summaryInfoRect()
        hoverHits.append(RichHoverHit(id: "summary-info", rect: infoRect))
        let infoColor = hoverID == "summary-info" ? blue : muted
        drawInfoGlyph(x: infoRect.minX + 3, y: infoRect.minY + 3, size: 15, color: infoColor)
        var accessoryX = infoRect.minX + 3
        if pendingUpdateVersion != nil {
            let updateRect = summaryUpdateRect()
            hoverHits.append(RichHoverHit(id: "summary-update", rect: updateRect))
            actionHits.append(RichActionHit(rect: updateRect, action: { [weak self] in
                self?.onUpdate?()
            }))
            let dotRect = NSRect(
                x: updateRect.midX - 3,
                y: updateRect.midY - 3,
                width: 6,
                height: 6
            )
            let dot = NSBezierPath(ovalIn: dotRect)
            NSColor(hex: "#9461F2").setFill()
            dot.fill()
            if hoverID == "summary-update" {
                let halo = NSBezierPath(ovalIn: dotRect.insetBy(dx: -3, dy: -3))
                NSColor(hex: "#9461F2").withAlphaComponent(0.20).setFill()
                halo.fill()
            }
            accessoryX = updateRect.minX
        }
        let infoX = accessoryX
        let totalStats = aggregateTodayStats()
        if totalStats.tokens > 0 {
            if totalStats.cost > 0 {
                let apiValueText = L10n.tr("menu.api_value")
                let costText = String(format: "~$%.2f", totalStats.cost)
                let dotText = "·"
                let tokensText = L10n.tr("menu.tokens")
                let tokenCountText = formatTokenCount(totalStats.tokens)
                let gap: CGFloat = 6
                let apiValueW = width(apiValueText, size: 10, weight: .semibold)
                let costW = width(costText, size: 12.5, weight: .bold)
                let dotW = width(dotText, size: 12, weight: .regular)
                let tokensW = width(tokensText, size: 12.5, weight: .regular)
                var cx = infoX - 8
                cx -= apiValueW; draw(apiValueText, x: cx, y: rect.minY + 10, size: 10, weight: .semibold, color: muted)
                cx -= gap + costW; draw(costText, x: cx, y: rect.minY + 8, size: 12.5, weight: .bold, color: amber)
                cx -= gap + dotW; draw(dotText, x: cx, y: rect.minY + 8, size: 12, weight: .regular, color: muted)
                cx -= gap + tokensW; draw(tokensText, x: cx, y: rect.minY + 8, size: 12.5, weight: .regular, color: muted)
                cx -= gap + width(tokenCountText, size: 12.5, weight: .semibold); draw(tokenCountText, x: cx, y: rect.minY + 8, size: 12.5, weight: .semibold, color: text)
            } else {
                let tokensText = L10n.tr("menu.tokens")
                let tokenCountText = formatTokenCount(totalStats.tokens)
                let gap: CGFloat = 6
                let tokensW = width(tokensText, size: 12.5, weight: .regular)
                var cx = infoX - 8
                cx -= tokensW; draw(tokensText, x: cx, y: rect.minY + 8, size: 12.5, weight: .regular, color: muted)
                cx -= gap + width(tokenCountText, size: 12.5, weight: .semibold); draw(tokenCountText, x: cx, y: rect.minY + 8, size: 12.5, weight: .semibold, color: text)
            }
        } else if let message = currentHistoryMessage() {
            let messageWidth = width(message, size: 11.5, weight: .regular)
            draw(message, x: infoX - messageWidth - 8, y: rect.minY + 9, size: 11.5, weight: .regular, color: muted)
        } else {
            let liveQuota = L10n.tr("menu.live_quota")
            draw(liveQuota, x: infoX - width(liveQuota, size: 11.5, weight: .regular) - 8, y: rect.minY + 9, size: 11.5, weight: .regular, color: muted)
        }
    }

    private func drawProvider(_ p: ProviderUsage, y: inout CGFloat) {
        y += sectionPadTop
        let visual = providerVisual(for: p.provider)
        drawProviderIcon(provider: p.provider, color: visual.brandColor, x: outerPad + innerPad, y: y + 5)

        let name = PROVIDER_NAMES[p.provider] ?? p.provider.capitalized
        let nameX = outerPad + innerPad + 29
        draw(name, x: nameX, y: y + 7, size: 13.5, weight: .semibold, color: text)
        let nameW = width(name, size: 13.5, weight: .semibold)
        let tier = planLabel(for: p)
        let tierW = tier.map { pillWidth($0, size: 10.5, weight: .semibold, horizontalPadding: 18, minimum: 36) } ?? 0
        if let tier {
            let brandColor = visual.brandColor
            drawPill(NSRect(x: nameX + nameW + 9, y: y + 6, width: tierW, height: 18), fill: brandColor.withAlphaComponent(0.10), stroke: brandColor.withAlphaComponent(0.18), text: tier, color: brandColor.withAlphaComponent(0.70), size: 10.5, weight: .semibold)
        }
        if p.provider == "antigravity", let credits = antigravityCredits(p) {
            drawCreditsChip(credits: credits, y: y)
        }
        if p.provider == "codex", let resets = p.resets, resets.count > 0 {
            drawResetsChip(resets: resets, y: y)
        }
        y += 32

        if p.status != "ok" {
            draw(p.detail ?? p.status, x: outerPad + innerPad + 38, y: y, size: 12, weight: .regular, color: red)
            y += rowH
            return
        }

        if p.provider == "antigravity" {
            let visibleWindows = p.windows.filter { $0.usedPercent != nil || $0.resetsAt != nil }
            let geminiWindows = visibleWindows.filter { !isExternalWindow($0) }
            let externalWindows = visibleWindows.filter { isExternalWindow($0) }
            if !geminiWindows.isEmpty {
                drawGroupHeader(title: "GEMINI", note: L10n.tr("menu.group.native"), color: NSColor(hex: "#3B82D9"), y: y)
                y += 18
                for w in geminiWindows {
                    drawQuotaRow(w, fetchedAt: p.fetchedAt, limitReached: p.limitReached, y: y, hoverID: "quota-\(p.provider)-gemini-\(w.key ?? w.label ?? "\(y)")", labelOverride: richWindowLabel(w))
                    y += rowH
                }
            }
            if !externalWindows.isEmpty {
                drawGroupHeader(title: "EXT", note: "Claude / GPT", color: muted, y: y)
                y += 18
                for w in externalWindows {
                    drawQuotaRow(w, fetchedAt: p.fetchedAt, limitReached: p.limitReached, y: y, hoverID: "quota-\(p.provider)-ext-\(w.key ?? w.label ?? "\(y)")", labelOverride: richWindowLabel(w))
                    y += rowH
                }
            }
        } else {
            if isCodexFiveHourTemporarilyUncapped(p) {
                drawTemporarilyUncappedRow(y: y)
                y += rowH
            }
            for w in p.windows {
                drawQuotaRow(w, fetchedAt: p.fetchedAt, limitReached: p.limitReached, y: y, hoverID: "quota-\(p.provider)-\(w.key ?? w.label ?? "\(y)")", labelOverride: compactQuotaWindowLabel(w))
                y += rowH
            }
        }

        if let stats = todayStats[p.provider.lowercased()] {
            drawUsageLine(stats: stats, y: y, hoverID: "usage-\(p.provider)")
            y += 33
        } else if shouldDrawUsageStatusLine(for: p) {
            let message = isFetchingHistory ? L10n.tr("menu.usage.loading") : (historyMessage ?? L10n.tr("menu.usage.no_today"))
            drawUsageStatusLine(message: message, y: y, hoverID: "usage-\(p.provider)-status")
            y += 33
        }

        y += sectionPadBottom
    }

    private func drawQuotaRow(_ w: QuotaWindow, fetchedAt: String?, limitReached: Bool?, y: CGFloat, hoverID: String, labelOverride: String? = nil) {
        let label = labelOverride ?? richWindowLabel(w)
        let rowX = outerPad + innerPad
        let rowRect = NSRect(x: outerPad + 2, y: y + 1, width: bounds.width - (outerPad + 2) * 2, height: rowH - 2)
        drawHover(id: hoverID, rect: rowRect)
        let barX = bounds.width - outerPad - innerPad - 68 - 48 - 130 - 24
        let pctX = barX + 130 + 12
        let resetX = pctX + 48 + 12
        let isIdle = QuotaFormatter.isWindowIdle(w, fetchedAt: fetchedAt)
        let sym = w.key == "ai_credits" ? "creditcard.fill" : (isIdle ? "moon.zzz.fill" : "clock")
        drawSymbol(sym, x: rowX + 5, y: y + 8, size: 14, color: usageColor(used: w.usedPercent))
        draw(label, x: rowX + 36, y: y + 6, size: 13.0, weight: .regular, color: text)

        guard let used = w.usedPercent else {
            if let remaining = w.remaining {
                let amount: String
                if let total = w.total {
                    amount = "\(Int(remaining)) / \(Int(total))"
                } else {
                    amount = QuotaFormatter.formatCreditBalance(remaining, currency: w.currency)
                }
                let amountWidth = width(amount, size: 13.0, weight: .semibold)
                let rightEdge = resetX + 68
                if let expiryDate = QuotaFormatter.formatExpiryDate(w.expiresAt) {
                    let expiry = L10n.tr("menu.credits.expires", expiryDate)
                    let expiryWidth = width(expiry, size: 11.5, weight: .regular)
                    let expiryX = rightEdge - expiryWidth
                    let separator = "·"
                    let separatorWidth = width(separator, size: 11.5, weight: .regular)
                    let separatorX = expiryX - 10 - separatorWidth
                    draw(amount, x: separatorX - 10 - amountWidth, y: y + 6, size: 13.0, weight: .semibold, color: text)
                    draw(separator, x: separatorX, y: y + 7, size: 11.5, weight: .regular, color: muted)
                    draw(expiry, x: expiryX, y: y + 7, size: 11.5, weight: .regular, color: muted)
                } else {
                    draw(amount, x: rightEdge - amountWidth, y: y + 6, size: 13.0, weight: .semibold, color: text)
                }
            } else {
                draw("—", x: pctX + 34, y: y + 6, size: 13.0, weight: .semibold, color: muted)
                let reset = resetDuration(window: w, fetchedAt: fetchedAt)
                if !reset.isEmpty {
                    let resetWidth = width(reset, size: 12.5, weight: .regular, monospaced: true)
                    draw(reset, x: resetX + 68 - resetWidth, y: y + 6, size: 12.5, weight: .regular, color: muted, monospaced: true)
                }
            }
            return
        }

        let remaining = max(0, 100 - used)
        // Semantic three-tier bar (green → amber → red). The red line matches the
        // notch's `QuotaLevel.criticalRemaining`; the amber caution band fills the
        // gap up to `cautionRemaining`. Amber is at home here because these bars
        // aren't brand-colored (unlike the notch, where amber would clash).
        let barColor: NSColor = remaining < QuotaLevel.criticalRemaining
            ? red
            : (remaining < QuotaLevel.cautionRemaining ? amber : green)
        let barRect = NSRect(x: barX, y: y + 12, width: 130, height: 6)
        drawBar(rect: barRect, percent: remaining, color: barColor)
        let pct = remaining <= 0 && limitReached == false
            ? "<1"
            : QuotaFormatter.remainingPercentText(remaining)
        let pctWidth = width(pct, size: 13.0, weight: .semibold, monospaced: true)
        draw(pct, x: pctX + 34 - pctWidth, y: y + 6, size: 13.0, weight: .semibold, color: remaining < QuotaLevel.criticalRemaining ? red : text, monospaced: true)
        draw("%", x: pctX + 37, y: y + 7, size: 11, weight: .regular, color: muted)
        let reset = resetDuration(window: w, fetchedAt: fetchedAt)
        let resetWidth = width(reset, size: 12.5, weight: .regular, monospaced: true)
        draw(reset, x: resetX + 68 - resetWidth, y: y + 6, size: 12.5, weight: .regular, color: remaining <= 15 ? red : muted, monospaced: true)
    }

    private func drawTemporarilyUncappedRow(y: CGFloat) {
        let rowX = outerPad + innerPad
        let status = L10n.tr("menu.five_hour_temporarily_uncapped")
        drawSymbol("infinity", x: rowX + 5, y: y + 8, size: 14, color: muted)
        draw(L10n.tr("preferences.window.5h"), x: rowX + 36, y: y + 6, size: 13.0, weight: .regular, color: text)
        let statusWidth = width(status, size: 12.5, weight: .medium)
        draw(status, x: bounds.width - outerPad - innerPad - statusWidth, y: y + 6, size: 12.5, weight: .medium, color: muted)
    }

    private func drawUsageLine(stats: ProviderHistoryStats, y: CGFloat, hoverID: String) {
        let rowX = outerPad + innerPad
        let textY = y + 8
        drawLine(y - 3, inset: 11)
        let rowRect = NSRect(x: outerPad + 2, y: y + 1, width: bounds.width - (outerPad + 2) * 2, height: 29)
        drawHover(id: hoverID, rect: rowRect)
        drawSymbol("calendar", x: rowX + 5, y: y + 9, size: 12, color: muted)
        let gap: CGFloat = 6
        let dotColor = NSColor.black.withAlphaComponent(0.14)
        let textColor = NSColor(hex: "#414147")
        var cx = rowX + 34
        let todayText = L10n.tr("menu.today")
        draw(todayText, x: cx, y: textY, size: 11.5, weight: .semibold, color: textColor)
        cx += width(todayText, size: 11.5, weight: .semibold) + gap
        draw("·", x: cx, y: textY, size: 11.5, weight: .regular, color: dotColor)
        cx += width("·", size: 11.5, weight: .regular) + gap
        let tokenCountText = formatTokenCount(stats.tokens)
        draw(tokenCountText, x: cx, y: textY, size: 11.5, weight: .semibold, color: textColor)
        cx += width(tokenCountText, size: 11.5, weight: .semibold) + gap
        let tokensText = L10n.tr("menu.tokens")
        draw(tokensText, x: cx, y: textY, size: 11.5, weight: .regular, color: muted)
        cx += width(tokensText, size: 11.5, weight: .regular) + gap
        draw("·", x: cx, y: textY, size: 11.5, weight: .regular, color: dotColor)
        cx += width("·", size: 11.5, weight: .regular) + gap
        let ioText = "\(formatTokenCount(stats.input))→\(formatTokenCount(stats.output))"
        draw(ioText, x: cx, y: textY + 0.9, size: 10.5, weight: .regular, color: muted)
        cx += width(ioText, size: 10.5, weight: .regular)
        
        let denom = stats.cacheRead + stats.input + stats.cacheCreation
        if denom > 0 {
            let hitPct = Int(round(Double(stats.cacheRead) / Double(denom) * 100))
            cx += gap
            draw("·", x: cx, y: textY, size: 11.5, weight: .regular, color: dotColor)
            cx += width("·", size: 11.5, weight: .regular) + gap
            let pctText = "\(hitPct)%"
            draw(pctText, x: cx, y: textY, size: 11.5, weight: .semibold, color: textColor)
            cx += width(pctText, size: 11.5, weight: .semibold) + 3
            let cacheText = L10n.tr("menu.cache")
            draw(cacheText, x: cx, y: textY, size: 11.5, weight: .regular, color: muted)
            cx += width(cacheText, size: 11.5, weight: .regular)
        }

        if stats.cost > 0 {
            cx += gap
            draw("·", x: cx, y: textY, size: 11.5, weight: .regular, color: dotColor)
            cx += width("·", size: 11.5, weight: .regular) + gap
            draw(String(format: "~$%.2f %@", stats.cost, L10n.tr("menu.api_value")), x: cx, y: textY + 0.9, size: 10.5, weight: .regular, color: muted)
        }
    }

    private func drawUsageStatusLine(message: String, y: CGFloat, hoverID: String) {
        let rowX = outerPad + innerPad
        let textY = y + 8
        drawLine(y - 3, inset: 11)
        let rowRect = NSRect(x: outerPad + 2, y: y + 1, width: bounds.width - (outerPad + 2) * 2, height: 29)
        drawHover(id: hoverID, rect: rowRect)
        drawSymbol("calendar", x: rowX + 5, y: y + 9, size: 12, color: muted)
        let todayText = L10n.tr("menu.today")
        draw(todayText, x: rowX + 34, y: textY, size: 11.5, weight: .semibold, color: NSColor(hex: "#414147"))
        let dotX = rowX + 34 + width(todayText, size: 11.5, weight: .semibold) + 8
        draw("·", x: dotX, y: textY, size: 11.5, weight: .regular, color: NSColor.black.withAlphaComponent(0.14))
        draw(message, x: dotX + 18, y: textY, size: 11.5, weight: .regular, color: muted)
    }

    private func drawActions(y: CGFloat) {
        let actions: [(String?, String, String, () -> Void)] = [
            ("arrow.clockwise", L10n.tr("menu.refresh_now"), "⌘R", { [weak self] in self?.onRefresh?() }),
            ("macwindow", L10n.tr("menu.open_console"), "⌘D", { [weak self] in self?.onConsole?() }),
            ("slider.horizontal.3", L10n.tr("menu.preferences"), "⌘,", { [weak self] in self?.onPreferences?() }),
            (nil, L10n.tr("app.quit"), "⌘Q", { [weak self] in self?.onQuit?() })
        ]
        var yy = y
        for (idx, action) in actions.enumerated() {
            if idx == 3 {
                drawLine(yy)
                yy += 6
            }
            let rect = NSRect(x: outerPad + 1, y: yy, width: bounds.width - (outerPad + 1) * 2, height: 30)
            drawHover(id: "action-\(idx)", rect: rect)
            actionHits.append(RichActionHit(rect: rect, action: action.3))
            if let symbol = action.0 {
                drawSymbol(symbol, x: rect.minX + 12, y: rect.minY + 6, size: 17, color: NSColor(hex: "#3A3A3C"))
            }
            draw(action.1, x: rect.minX + 41, y: rect.minY + 7, size: 13.5, weight: .regular, color: text)
            let hint = action.2
            let hintW = width(hint, size: 12.5, weight: .regular)
            draw(hint, x: rect.maxX - 14 - hintW, y: rect.minY + 7, size: 12.5, weight: .regular, color: muted)
            yy += 30
        }
    }

    private func drawHover(id: String, rect: NSRect) {
        hoverHits.append(RichHoverHit(id: id, rect: rect))
        guard hoverID == id else { return }
        let path = NSBezierPath(roundedRect: rect.insetBy(dx: 1, dy: 1), xRadius: 6, yRadius: 6)
        NSColor(hex: "#DCEBFF").withAlphaComponent(0.62).setFill()
        path.fill()
        NSColor(hex: "#7AA7E8").withAlphaComponent(0.22).setStroke()
        path.lineWidth = 0.5
        path.stroke()
    }

    private func drawInfoTooltipIfNeeded() {
        guard hoverID == "summary-info" else { return }
        let lines = [
            L10n.tr("menu.usage.note.api_value"),
            L10n.tr("menu.usage.note.subscription")
        ]
        let infoRect = summaryInfoRect()
        let rect = NSRect(x: bounds.width - outerPad - 258, y: outerPad + 36, width: 250, height: 58)
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.16)
        shadow.shadowBlurRadius = 12
        shadow.shadowOffset = NSSize(width: 0, height: -3)
        let arrowTipX = infoRect.midX
        let path = tooltipBubblePath(rect: rect, arrowTipX: arrowTipX)

        NSGraphicsContext.saveGraphicsState()
        shadow.set()
        NSColor.white.withAlphaComponent(0.94).setFill()
        path.fill()
        NSGraphicsContext.restoreGraphicsState()

        NSColor(hex: "#CBD7EA").withAlphaComponent(0.75).setStroke()
        path.lineWidth = 0.5
        path.stroke()

        draw(L10n.tr("menu.usage.notes"), x: rect.minX + 11, y: rect.minY + 8, size: 10.5, weight: .semibold, color: NSColor(hex: "#2E3440"))
        for (idx, line) in lines.enumerated() {
            draw(line, x: rect.minX + 11, y: rect.minY + 25 + CGFloat(idx) * 14, size: 9.8, weight: .regular, color: NSColor(hex: "#60636B"))
        }
    }

    private func drawUpdateTooltipIfNeeded() {
        guard hoverID == "summary-update", let version = pendingUpdateVersion else { return }
        let updateRect = summaryUpdateRect()
        let label = L10n.tr("update.available.hint", version)
        let labelWidth = width(label, size: 10.5, weight: .semibold)
        let bubbleWidth = min(bounds.width - outerPad * 2, labelWidth + 22)
        let rect = NSRect(
            x: max(outerPad, min(updateRect.midX - bubbleWidth / 2, bounds.width - outerPad - bubbleWidth)),
            y: outerPad + 36,
            width: bubbleWidth,
            height: 30
        )
        let path = tooltipBubblePath(rect: rect, arrowTipX: updateRect.midX)
        NSColor.white.withAlphaComponent(0.96).setFill()
        path.fill()
        NSColor(hex: "#CBD7EA").withAlphaComponent(0.75).setStroke()
        path.lineWidth = 0.5
        path.stroke()
        draw(
            label,
            x: rect.midX - labelWidth / 2,
            y: rect.minY + 8,
            size: 10.5,
            weight: .semibold,
            color: NSColor(hex: "#2E3440")
        )
    }
 
    private func drawResetsTooltipIfNeeded() {
        guard hoverID == "resets-tooltip", let chipRect = resetsChipRect else { return }
        guard let codex = providers.first(where: { $0.provider == "codex" }),
              let resets = codex.resets, resets.count > 0 else { return }
        
        let exp = resets.expiries.sorted()
        let nextMs = exp.first ?? 0.0
        let soon = nextMs <= 7 * 86_400_000.0 && !exp.isEmpty
        
        let headerH: CGFloat = 20
        let dividerH: CGFloat = 8
        let rowH: CGFloat = 16
        let padBottom: CGFloat = 8
        
        let count = resets.count
        let bubbleW: CGFloat = 220
        let bubbleH: CGFloat = headerH + dividerH + CGFloat(count) * rowH + padBottom
        
        let rect = NSRect(
            x: max(outerPad, min(chipRect.midX - bubbleW / 2, bounds.width - outerPad - bubbleW)),
            y: chipRect.maxY + 6,
            width: bubbleW,
            height: bubbleH
        )
        
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.16)
        shadow.shadowBlurRadius = 12
        shadow.shadowOffset = NSSize(width: 0, height: -3)
        let arrowTipX = chipRect.midX
        let path = tooltipBubblePath(rect: rect, arrowTipX: arrowTipX)
        
        NSGraphicsContext.saveGraphicsState()
        shadow.set()
        NSColor.white.withAlphaComponent(0.94).setFill()
        path.fill()
        NSGraphicsContext.restoreGraphicsState()
        
        NSColor(hex: "#CBD7EA").withAlphaComponent(0.75).setStroke()
        path.lineWidth = 0.5
        path.stroke()
        
        draw(L10n.tr("menu.resets.rate_limit_available"), x: rect.minX + 11, y: rect.minY + 6, size: 10.5, weight: .semibold, color: NSColor(hex: "#2E3440"))
        
        let divY = rect.minY + headerH + 3
        let divPath = NSBezierPath()
        divPath.move(to: NSPoint(x: rect.minX + 11, y: divY))
        divPath.line(to: NSPoint(x: rect.maxX - 11, y: divY))
        NSColor(hex: "#E5E7EB").setStroke()
        divPath.lineWidth = 0.5
        divPath.stroke()
        
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US")
        formatter.dateFormat = "MMM d"
        
        for (i, ms) in exp.enumerated() {
            let label = i == 0 ? L10n.tr("menu.resets.next_expires") : L10n.tr("menu.resets.then")
            let date = Date().addingTimeInterval(ms / 1000.0)
            let dateStr = formatter.string(from: date)
            let countdown = QuotaFormatter.resetExpiryCountdown(ms, includesExpiry: false)
            
            let rowY = divY + 4 + CGFloat(i) * rowH
            
            draw(label, x: rect.minX + 11, y: rowY, size: 10, weight: .medium, color: NSColor(hex: "#60636B"))
            
            let countdownW = width(countdown, size: 9, weight: .semibold)
            let dateW = width(dateStr, size: 10.5, weight: .bold)
            
            let dateX = rect.maxX - 11 - countdownW - 6 - dateW
            let countdownX = rect.maxX - 11 - countdownW
            
            draw(dateStr, x: dateX, y: rowY - 0.5, size: 10.5, weight: .bold, color: NSColor(hex: "#2E3440"))
            
            let badgeColor = (i == 0 && soon) ? NSColor(hex: "#D08A00") : NSColor(hex: "#8D8B94")
            draw(countdown, x: countdownX, y: rowY + 0.5, size: 9, weight: .semibold, color: badgeColor)
        }
    }

    private func tooltipBubblePath(rect: NSRect, arrowTipX: CGFloat) -> NSBezierPath {
        let radius: CGFloat = 8
        let arrowHalfWidth: CGFloat = 8
        let arrowHeight: CGFloat = 8
        let tipX = max(rect.minX + radius + arrowHalfWidth, min(arrowTipX, rect.maxX - radius - arrowHalfWidth))
        let path = NSBezierPath()

        path.move(to: NSPoint(x: rect.minX + radius, y: rect.minY))
        path.line(to: NSPoint(x: tipX - arrowHalfWidth, y: rect.minY))
        path.line(to: NSPoint(x: tipX, y: rect.minY - arrowHeight))
        path.line(to: NSPoint(x: tipX + arrowHalfWidth, y: rect.minY))
        path.line(to: NSPoint(x: rect.maxX - radius, y: rect.minY))
        path.curve(
            to: NSPoint(x: rect.maxX, y: rect.minY + radius),
            controlPoint1: NSPoint(x: rect.maxX - radius * 0.45, y: rect.minY),
            controlPoint2: NSPoint(x: rect.maxX, y: rect.minY + radius * 0.45)
        )
        path.line(to: NSPoint(x: rect.maxX, y: rect.maxY - radius))
        path.curve(
            to: NSPoint(x: rect.maxX - radius, y: rect.maxY),
            controlPoint1: NSPoint(x: rect.maxX, y: rect.maxY - radius * 0.45),
            controlPoint2: NSPoint(x: rect.maxX - radius * 0.45, y: rect.maxY)
        )
        path.line(to: NSPoint(x: rect.minX + radius, y: rect.maxY))
        path.curve(
            to: NSPoint(x: rect.minX, y: rect.maxY - radius),
            controlPoint1: NSPoint(x: rect.minX + radius * 0.45, y: rect.maxY),
            controlPoint2: NSPoint(x: rect.minX, y: rect.maxY - radius * 0.45)
        )
        path.line(to: NSPoint(x: rect.minX, y: rect.minY + radius))
        path.curve(
            to: NSPoint(x: rect.minX + radius, y: rect.minY),
            controlPoint1: NSPoint(x: rect.minX, y: rect.minY + radius * 0.45),
            controlPoint2: NSPoint(x: rect.minX + radius * 0.45, y: rect.minY)
        )
        path.close()
        return path
    }

    private func measuredProviderHeight(_ p: ProviderUsage) -> CGFloat {
        var h: CGFloat = sectionPadTop + 32
        if p.status != "ok" {
            return h + rowH
        }

        if p.provider == "antigravity" {
            let visibleWindows = p.windows.filter { $0.usedPercent != nil || $0.resetsAt != nil }
            let geminiWindows = visibleWindows.filter { !isExternalWindow($0) }
            let externalWindows = visibleWindows.filter { isExternalWindow($0) }
            if !geminiWindows.isEmpty {
                h += 18 + CGFloat(geminiWindows.count) * rowH
            }
            if !externalWindows.isEmpty {
                h += 18 + CGFloat(externalWindows.count) * rowH
            }
        } else {
            let visibleWindows = p.windows.filter { isDisplayedQuotaWindow($0, provider: p.provider) }
            h += CGFloat(visibleWindows.count) * rowH
            if isCodexFiveHourTemporarilyUncapped(p) {
                h += rowH
            }
        }

        if shouldDrawUsageStatusLine(for: p) {
            h += 33
        }
        h += sectionPadBottom
        return h
    }

    private func measuredActionsHeight() -> CGFloat {
        return 30 * 3 + 6 + 30
    }

    private func drawProviderIcon(provider: String, color: NSColor, x: CGFloat, y: CGFloat) {
        let rect = NSRect(x: x + 5, y: y + 5, width: 10, height: 10)
        let path = NSBezierPath(ovalIn: rect)
        color.setFill()
        path.fill()
    }

    private func drawCreditsChip(credits: Double, y: CGFloat) {
        let creditsText = formatInteger(credits)
        let creditsW = width(creditsText, size: 11.5, weight: .bold)
        let creditsLabel = L10n.tr("menu.credits.upper")
        let labelW = width(creditsLabel, size: 9.5, weight: .semibold)
        let chipW = ceil(8 + 11 + 8 + creditsW + 10 + labelW + 8)
        let rect = NSRect(x: bounds.width - outerPad - innerPad - chipW, y: y + 6, width: chipW, height: 18)
        let path = NSBezierPath(roundedRect: rect, xRadius: 5, yRadius: 5)
        NSColor.black.withAlphaComponent(0.05).setFill()
        path.fill()
        drawCoin(x: rect.minX + 8, y: rect.minY + 4, size: 11, color: muted)
        draw(creditsText, x: rect.minX + 27, y: rect.minY + 2, size: 11.5, weight: .bold, color: text)
        draw(creditsLabel, x: rect.minX + 27 + creditsW + 10, y: rect.minY + 4, size: 9.5, weight: .semibold, color: muted)
    }

    private func drawResetsChip(resets: ResetCredits, y: CGFloat) {
        let availableText = L10n.tr("menu.resets.available.compact", resets.count)
        let availableW = width(availableText, size: 9.5, weight: .semibold)
        let labelText = L10n.tr("menu.resets.upper")
        let labelW = width(labelText, size: 9.5, weight: .semibold)
        let chipW = ceil(8 + 11 + 8 + availableW + 8 + labelW + 8)
        let rect = NSRect(x: bounds.width - outerPad - innerPad - chipW, y: y + 6, width: chipW, height: 18)
        
        resetsChipRect = rect
        
        // 1. Draw hover background behind text (if hovered)
        drawHover(id: "resets-tooltip", rect: rect)
        
        // 2. Draw default background only if not hovered
        if hoverID != "resets-tooltip" {
            let path = NSBezierPath(roundedRect: rect, xRadius: 5, yRadius: 5)
            NSColor.black.withAlphaComponent(0.05).setFill()
            path.fill()
        }
        
        let exp = resets.expiries.sorted()
        let nextMs = exp.first ?? 0.0
        let soon = nextMs <= 7 * 86_400_000.0 && !exp.isEmpty
        
        let iconColor = soon ? amber : NSColor(hex: "#8B5CF6")
        
        // 3. Draw symbol and text on top
        drawSymbol("arrow.counterclockwise", x: rect.minX + 8, y: rect.minY + 4, size: 11, color: iconColor)
        draw(availableText, x: rect.minX + 27, y: rect.minY + 4, size: 9.5, weight: .semibold, color: text)
        draw(labelText, x: rect.minX + 27 + availableW + 8, y: rect.minY + 4, size: 9.5, weight: .semibold, color: soon ? amber : muted)
    }

    private func drawGroupHeader(title: String, note: String, color: NSColor, y: CGFloat) {
        let x = outerPad + 46
        let dot = NSBezierPath(ovalIn: NSRect(x: x, y: y + 7, width: 6, height: 6))
        color.setFill()
        dot.fill()
        draw(title, x: x + 12, y: y + 2, size: 10, weight: .bold, color: NSColor(hex: "#3A3A3C"))
        let titleW = width(title, size: 10, weight: .bold)
        draw(note, x: x + 19 + titleW, y: y + 2, size: 10.5, weight: .regular, color: muted)
    }

    private func drawCoin(x: CGFloat, y: CGFloat, size: CGFloat, color: NSColor) {
        color.setStroke()
        let outer = NSBezierPath(ovalIn: NSRect(x: x, y: y, width: size, height: size))
        outer.lineWidth = 1.1
        outer.stroke()
        let inner = NSBezierPath(ovalIn: NSRect(x: x + 2.5, y: y + 2.5, width: size - 5, height: size - 5))
        inner.lineWidth = 0.9
        inner.stroke()
    }

    private func drawPill(_ rect: NSRect, fill: NSColor, stroke: NSColor? = nil, text label: String, color: NSColor, size: CGFloat, weight: NSFont.Weight, dotColor: NSColor? = nil) {
        let path = NSBezierPath(roundedRect: rect, xRadius: rect.height / 2, yRadius: rect.height / 2)
        fill.setFill()
        path.fill()
        if let stroke = stroke {
            stroke.setStroke()
            path.stroke()
        }
        if let dotColor = dotColor {
            draw("●", x: rect.minX + 9, y: rect.minY + 4, size: 8, weight: .regular, color: dotColor)
            draw(label.replacingOccurrences(of: "● ", with: ""), x: rect.minX + 22, y: rect.minY + 3, size: size, weight: weight, color: color)
        } else {
            let w = width(label, size: size, weight: weight)
            draw(label, x: rect.midX - w / 2, y: rect.minY + 3, size: size, weight: weight, color: color)
        }
    }

    private func drawBar(rect: NSRect, percent: Double, color: NSColor) {
        let track = NSBezierPath(roundedRect: rect, xRadius: rect.height / 2, yRadius: rect.height / 2)
        (percent <= 0 ? NSColor.systemRed.withAlphaComponent(0.16) : NSColor(hex: "#D1CBD2")).setFill()
        track.fill()
        if percent > 0 {
            let fillWidth = max(rect.height, rect.width * CGFloat(min(100, percent) / 100.0))
            let fillRect = NSRect(x: rect.minX, y: rect.minY, width: fillWidth, height: rect.height)
            let fill = NSBezierPath(roundedRect: fillRect, xRadius: rect.height / 2, yRadius: rect.height / 2)
            color.setFill()
            fill.fill()
        }
    }

    private func drawLine(_ y: CGFloat, inset: CGFloat? = nil) {
        let xInset = inset ?? separatorMarginX
        sectionLine.setStroke()
        let path = NSBezierPath()
        path.move(to: NSPoint(x: outerPad + xInset, y: y))
        path.line(to: NSPoint(x: bounds.width - outerPad - xInset, y: y))
        path.lineWidth = 0.5
        path.stroke()
    }

    private func summaryInfoRect() -> NSRect {
        let summaryY = outerPad + 1
        let summaryRect = NSRect(x: outerPad + 1, y: summaryY, width: bounds.width - (outerPad + 1) * 2, height: 37)
        return NSRect(x: summaryRect.maxX - 24, y: summaryRect.minY + 6, width: 21, height: 21)
    }

    private func summaryUpdateRect() -> NSRect {
        let infoRect = summaryInfoRect()
        return NSRect(x: infoRect.minX - 24, y: infoRect.minY - 1.5, width: 24, height: 24)
    }

    private func drawInfoGlyph(x: CGFloat, y: CGFloat, size: CGFloat, color: NSColor) {
        drawSymbol("info.circle", x: x, y: y, size: size, color: color)
    }

    private func drawBolt(x: CGFloat, y: CGFloat, color: NSColor) {
        let path = NSBezierPath()
        path.move(to: NSPoint(x: x + 6, y: y))
        path.line(to: NSPoint(x: x, y: y + 7))
        path.line(to: NSPoint(x: x + 4, y: y + 7))
        path.line(to: NSPoint(x: x + 2, y: y + 12))
        path.line(to: NSPoint(x: x + 10, y: y + 4))
        path.line(to: NSPoint(x: x + 6, y: y + 4))
        path.close()
        color.setFill()
        path.fill()
    }

    private func drawSymbol(_ name: String, x: CGFloat, y: CGFloat, size: CGFloat, color: NSColor) {
        if let image = NSImage(systemSymbolName: name, accessibilityDescription: nil) {
            image.isTemplate = true
            
            // Calculate aspect-ratio preserving dimensions that fit inside the size x size bounding box
            let imgSize = image.size
            let scale = min(size / imgSize.width, size / imgSize.height)
            let drawWidth = imgSize.width * scale
            let drawHeight = imgSize.height * scale
            
            // Center the image within the target size x size bounding box
            let drawX = x + (size - drawWidth) / 2.0
            let drawY = y + (size - drawHeight) / 2.0
            let rect = NSRect(x: drawX, y: drawY, width: drawWidth, height: drawHeight)
            
            let tinted = NSImage(size: rect.size, flipped: false) { dstRect in
                image.draw(in: dstRect, from: .zero, operation: .copy, fraction: 1.0)
                color.set()
                dstRect.fill(using: .sourceIn)
                return true
            }
            tinted.draw(in: rect)
            return
        }

        // Fallback drawing if SF Symbols fail
        color.setStroke()
        let r = NSRect(x: x + 2, y: y + 2, width: size - 4, height: size - 4)
        let fallback = NSBezierPath(ovalIn: r)
        fallback.lineWidth = 1.2
        fallback.stroke()
    }

    private func draw(_ string: String, x: CGFloat, y: CGFloat, size: CGFloat, weight: NSFont.Weight, color: NSColor, monospaced: Bool = false) {
        let font = monospaced ? NSFont.monospacedDigitSystemFont(ofSize: size, weight: weight) : NSFont.systemFont(ofSize: size, weight: weight)
        let attrs: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: color
        ]
        string.draw(at: NSPoint(x: x, y: y), withAttributes: attrs)
    }

    private func drawWrapped(_ string: String, rect: NSRect, size: CGFloat, weight: NSFont.Weight, color: NSColor) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byWordWrapping
        paragraph.lineSpacing = 1.0
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: size, weight: weight),
            .foregroundColor: color,
            .paragraphStyle: paragraph
        ]
        string.draw(in: rect, withAttributes: attrs)
    }

    private func width(_ string: String, size: CGFloat, weight: NSFont.Weight, monospaced: Bool = false) -> CGFloat {
        let font = monospaced ? NSFont.monospacedDigitSystemFont(ofSize: size, weight: weight) : NSFont.systemFont(ofSize: size, weight: weight)
        let attrs: [NSAttributedString.Key: Any] = [.font: font]
        return string.size(withAttributes: attrs).width
    }

    private func pillWidth(_ string: String, size: CGFloat, weight: NSFont.Weight, horizontalPadding: CGFloat, minimum: CGFloat) -> CGFloat {
        return max(minimum, ceil(width(string, size: size, weight: weight) + horizontalPadding))
    }

    private func planLabel(for provider: ProviderUsage) -> String? {
        let raw = provider.accountLabel?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !raw.isEmpty else { return nil }
        var tier = raw
        for prefix in ["Google AI ", "Google "] where tier.lowercased().hasPrefix(prefix.lowercased()) {
            tier = String(tier.dropFirst(prefix.count))
            break
        }
        if provider.provider == "antigravity" {
            if tier.lowercased().hasPrefix("antigravity ") {
                tier = String(tier.dropFirst("Antigravity ".count))
            }
            if tier.lowercased().hasSuffix(" quota") {
                tier = String(tier.dropLast(" Quota".count))
            }
        }
        return tier.uppercased()
    }

    private func richWindowLabel(_ w: QuotaWindow) -> String {
        // Scoped sub-limits reuse Antigravity's "<Model> · wk" language.
        if w.isScoped {
            return "\(w.label ?? "Model") · wk"
        }
        let raw = w.label ?? w.key ?? "Quota"
        let lower = raw.lowercased()
        if lower.contains("gemini") && (lower.contains("weekly") || lower.contains("week")) {
            return "Gemini · wk"
        }
        if (lower.contains("claude") || lower.contains("external") || lower.contains("gpt")) && (lower.contains("weekly") || lower.contains("week")) {
            return "Claude / GPT · wk"
        }
        if lower.contains("gemini") {
            return "Gemini · 5h"
        }
        if lower.contains("claude") || lower.contains("external") || lower.contains("gpt") {
            return "Claude / GPT · 5h"
        }
        return raw
    }

    private func compactQuotaWindowLabel(_ w: QuotaWindow) -> String {
        if w.isScoped {
            return richWindowLabel(w)
        }
        let raw = ((w.label ?? "") + " " + (w.key ?? "")).lowercased()
        if raw.contains("weekly") || raw.contains("week") || raw.contains("wk") {
            return L10n.tr("preferences.window.weekly")
        }
        if raw.contains("5h") || raw.contains("5-hour") || raw.contains("rolling") {
            return L10n.tr("preferences.window.5h")
        }
        // Duration-based fallback for windows like 30-day free tier
        if let m = w.windowMinutes, m > 0 {
            if m % 1440 == 0 {
                return L10n.tr("preferences.window.days", m / 1440)
            }
            if m % 60 == 0 {
                return L10n.tr("preferences.window.hours", m / 60)
            }
            return QuotaFormatter.windowLengthText(windowMinutes: m, fallbackLabel: nil)
        }
        return richWindowLabel(w)
    }

    private func isExternalWindow(_ w: QuotaWindow) -> Bool {
        let raw = ((w.label ?? "") + " " + (w.key ?? "")).lowercased()
        return raw.contains("external") || raw.contains("claude") || raw.contains("gpt")
    }

    private func resetDuration(window: QuotaWindow, fetchedAt: String?) -> String {
        guard let resetsAt = window.resetsAt else { return "" }
        if QuotaFormatter.isWindowIdle(window, fetchedAt: fetchedAt) {
            let fallback = richWindowLabel(window)
            return QuotaFormatter.windowLengthText(windowMinutes: window.windowMinutes, fallbackLabel: fallback)
        }
        guard let targetDate = QuotaFormatter.parseISODate(resetsAt) else { return "" }
        let totalSecs = Int(targetDate.timeIntervalSinceNow)
        if totalSecs <= 0 { return L10n.tr("menu.reset.now") }
        let dur = QuotaFormatter.formatDuration(TimeInterval(totalSecs))
        return dur.secondary.isEmpty ? dur.primary : "\(dur.primary)\(dur.secondary)"
    }

    private func antigravityCredits(_ provider: ProviderUsage) -> Double? {
        guard provider.provider == "antigravity" else { return nil }
        return provider.windows.first { ($0.key ?? "").lowercased() == "ai_credits" }?.remaining
    }

    private func isDisplayedQuotaWindow(_ window: QuotaWindow, provider: String) -> Bool {
        if provider == "antigravity" && window.key == "ai_credits" { return false }
        return window.usedPercent != nil || window.resetsAt != nil || window.remaining != nil
    }

    private func formatInteger(_ value: Double) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.maximumFractionDigits = 0
        return formatter.string(from: NSNumber(value: value)) ?? "\(Int(value))"
    }

    private func formatTokenCount(_ value: Int) -> String {
        let absValue = abs(value)
        if absValue >= 1_000_000 {
            return String(format: "%.1fM", Double(value) / 1_000_000.0)
        }
        if absValue >= 1_000 {
            return "\(Int(round(Double(value) / 1_000.0)))k"
        }
        return "\(value)"
    }

    private func aggregateTodayStats() -> ProviderHistoryStats {
        todayStats.values.reduce(ProviderHistoryStats(tokens: 0, input: 0, output: 0, cacheCreation: 0, cacheRead: 0, cost: 0.0)) { partial, stats in
            ProviderHistoryStats(
                tokens: partial.tokens + stats.tokens,
                input: partial.input + stats.input,
                output: partial.output + stats.output,
                cacheCreation: partial.cacheCreation + stats.cacheCreation,
                cacheRead: partial.cacheRead + stats.cacheRead,
                cost: partial.cost + stats.cost
            )
        }
    }

    private func currentHistoryMessage() -> String? {
        if isFetchingHistory { return L10n.tr("menu.usage.loading") }
        if let historyMessage { return historyMessage }
        return nil
    }

    private func shouldDrawUsageStatusLine(for provider: ProviderUsage) -> Bool {
        guard provider.status == "ok" else { return false }
        return true
    }
}
