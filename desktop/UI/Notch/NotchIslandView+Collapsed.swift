import AppKit
import Foundation
import SwiftUI

// The glance gauge primitive shared by the collapsed strip and the peek tray.
// One swappable rendering per FLUXION_NOTCH_GAUGE_STYLE: "dot" keeps the classic
// glowing dot, "ring" draws a progress ring whose arc sweeps with remaining
// quota, with an optional window label (5H/WK) in its center. Every glance
// layout routes through this so a new style lands everywhere at once.
struct QuotaGauge: View {
    let style: String
    let color: NSColor
    /// Remaining fraction in 0...1; nil draws only the dim track (loading /
    /// credits, where there is no meaningful arc to sweep).
    let progress: Double?
    var label: String? = nil
    var size: CGFloat = 12

    var body: some View {
        if style.hasPrefix("ring") {
            ring
        } else if style == "liquid" {
            liquid
        } else {
            Circle()
                .fill(Color(color))
                .frame(width: 7, height: 7)
                .shadow(color: Color(color).opacity(0.8), radius: 3)
        }
    }

    // Liquid style: a thin-rimmed circle filled to `progress` with a gently
    // bowed static surface — no wave animation, the always-on strip shouldn't
    // have perpetual motion beside the camera. Empty (nil progress) leaves
    // just the rim, like the ring's bare track.
    private var liquid: some View {
        let lineWidth: CGFloat = size >= 16 ? 1.5 : 1.2
        let clamped = progress.map { CGFloat(min(max($0, 0), 1)) }
        return ZStack {
            if let clamped = clamped, clamped > 0.001 {
                LiquidFillShape(level: clamped)
                    .fill(
                        LinearGradient(
                            gradient: Gradient(colors: [Color(color), Color(color).opacity(0.72)]),
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .clipShape(Circle())
            }
            Circle()
                .stroke(Color(color).opacity(0.55), lineWidth: lineWidth)
            if let label = label {
                Text(label)
                    .font(.system(size: max(6, size * 0.38), weight: .bold))
                    .foregroundColor(.white.opacity(0.95))
                    .shadow(color: .black.opacity(0.35), radius: 1)
                    .minimumScaleFactor(0.5)
                    .lineLimit(1)
                    .padding(.horizontal, lineWidth + 1)
            }
        }
        .padding(lineWidth / 2)
        .frame(width: size, height: size)
    }

    private var ring: some View {
        let lineWidth: CGFloat = size >= 16 ? 2 : 1.5
        let clamped = progress.map { CGFloat(min(max($0, 0), 1)) }
        return ZStack {
            Circle()
                .stroke(Color(color).opacity(0.28), lineWidth: lineWidth)
            if let clamped = clamped, clamped > 0.001 {
                Circle()
                    .trim(from: 0, to: clamped)
                    .stroke(Color(color), style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            }
            if let label = label {
                Text(label)
                    .font(.system(size: max(6, size * 0.38), weight: .bold))
                    .foregroundColor(.white.opacity(0.92))
                    .minimumScaleFactor(0.5)
                    .lineLimit(1)
                    .padding(.horizontal, lineWidth + 1)
            }
        }
        .padding(lineWidth / 2)
        .frame(width: size, height: size)
    }
}

// Everything below a gently bowed surface line at `level` (remaining
// fraction); the caller clips it to the gauge circle. 0 yields an empty path,
// ~1 fills the whole rect so the bow never clips against the top edge.
struct LiquidFillShape: Shape {
    let level: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        guard level > 0.001 else { return path }
        if level >= 0.97 {
            path.addRect(rect)
            return path
        }
        let surfaceY = rect.minY + rect.height * (1 - level)
        let bow = min(1.6, rect.height * 0.08)
        path.move(to: CGPoint(x: rect.minX, y: surfaceY + bow))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: surfaceY + bow),
            control: CGPoint(x: rect.midX, y: surfaceY - bow)
        )
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}

// NotchIslandView — collapsed strip + shared solo-pool glance units (collapsed/peek).
// Split out of NotchWindow.swift for navigability; same type via extension.
extension NotchIslandView {
    // Maps a provider/pool quota reading onto the gauge: healthy sweeps the
    // brand-colored arc by remaining (stepping to red at the shared critical
    // threshold, like the quota bars), locked/error shows a full red ring (an
    // alert badge, per the design mock), recovering uses the same full-ring
    // treatment in amber, and loading/credits leave just the dim track. The dot
    // style ignores all of it and stays the classic brand dot.
    func glanceGauge(
        mode: ProviderDisplayMode,
        remaining: Double,
        brandColor: NSColor,
        label: String? = nil,
        size: CGFloat = 12
    ) -> some View {
        let color: NSColor
        let progress: Double?
        if !usesShapedGauge {
            // Classic dot: always the brand color, no arc — unchanged visuals.
            color = brandColor
            progress = nil
        } else {
            switch mode {
            case .locked, .error:
                (color, progress) = (.systemRed, 1)
            case .recovering:
                (color, progress) = (.systemYellow, 1)
            case .healthy:
                color = remaining <= QuotaLevel.criticalRemaining ? .systemRed : brandColor
                progress = remaining / 100
            case .credits, .loading:
                (color, progress) = (brandColor, nil)
            }
        }
        return QuotaGauge(style: model.gaugeStyle, color: color, progress: progress, label: label, size: size)
    }

    // Gauge for a single quota window (the solo 5H | WK glance): the ring's
    // center holds the window label — or, in the numeral style, the remaining
    // number itself (callers that already show the number elsewhere, like the
    // peek columns, pass numeralAllowed: false to keep the label). An absent
    // window or Codex's temporarily uncapped 5h renders as a full brand ring
    // rather than an alarming void.
    @ViewBuilder
    func windowGauge(
        label: String,
        snapshot: QuotaWindowSnapshot?,
        brandColor: NSColor,
        uncapped: Bool = false,
        size: CGFloat = 17,
        numeralAllowed: Bool = true
    ) -> some View {
        let center: String = {
            guard numeralAllowed, gaugeNumeralInside else { return label }
            if uncapped { return "∞" }
            guard let snapshot = snapshot else { return label }
            return snapshot.remainingText
        }()
        if uncapped || snapshot == nil {
            QuotaGauge(style: model.gaugeStyle, color: brandColor, progress: 1, label: center, size: size)
        } else {
            glanceGauge(
                mode: snapshot?.depleted == true ? .locked : .healthy,
                remaining: snapshot?.remaining ?? 0,
                brandColor: brandColor,
                label: center,
                size: size
            )
        }
    }

    // A "shaped" gauge (ring or liquid) draws its own identity, so window
    // labels move inside it; the classic dot keeps its text label beside.
    var usesShapedGauge: Bool { model.gaugeStyle != "dot" }

    // Number placement (the gaugeValue axis): "inside" moves the remaining
    // number into the ring/liquid center, "hidden" drops numbers entirely.
    // Both suppress the % text beside the gauge in the collapsed strip —
    // except for exhausted quotas, whose red reset countdown always surfaces
    // (the one thing that matters then has to live somewhere). The dot can't
    // hold a number, so "inside" degrades to beside for it. Peek keeps full
    // detail regardless: hovering is an explicit request for the numbers.
    var gaugeNumeralInside: Bool { usesShapedGauge && model.gaugeValue == "inside" }
    var gaugeHidesSideValue: Bool {
        usesShapedGauge && (model.gaugeValue == "inside" || model.gaugeValue == "hidden")
    }

    // MARK: - 1. Collapsed View
    var collapsedView: some View {
        HStack {
            collapsedContent
        }
        .frame(height: model.collapsedHeight)
        .background {
            if !model.hasNotch {
                // Measures width only: its copy of the row must not report
                // anchors, or the tail would point at the twin's layout.
                collapsedWidthMeasurer
                    .environment(\.notchReportsAnchors, false)
            }
        }
        .onPreferenceChange(CollapsedContentWidthKey.self) { width in
            let rounded = ceil(width)
            guard rounded > 1 else { return }
            guard abs(model.collapsedContentWidth - rounded) > 0.5 else { return }
            model.collapsedContentWidth = rounded
            if model.notchState == .collapsed {
                controller?.repositionWindow()
            }
        }
    }

    @ViewBuilder
    var collapsedContent: some View {
        if model.providers.isEmpty {
            EmptyView()
        } else {
            switch model.silentStyle {
            case "ambient":
                ambientDotsView
            case "lowest":
                lowestProviderView
            default:
                allProvidersView
            }
        }
    }

    // Invisible twin of the collapsed row, laid out at its natural (fixedSize)
    // width so the pill can hug the content on non-notched displays (see the
    // model's collapsedWidthNoNotch). Inert: transparent, not hit-testable.
    var collapsedWidthMeasurer: some View {
        HStack {
            collapsedContent
        }
        .fixedSize()
        .opacity(0)
        .allowsHitTesting(false)
        .background(
            GeometryReader { proxy in
                Color.clear.preference(key: CollapsedContentWidthKey.self, value: proxy.size.width)
            }
        )
    }

    var ambientDotsView: some View {
        Group {
            if notchIsSoloSplit(model.providers) {
                soloAmbientDotsView
            } else if usesShapedGauge,
                      notchUsesSoloDualWindowGlance(model.providers),
                      let provider = model.providers.first {
                // Minimal density + ring gauge for a single dual-window
                // provider: the bare labeled 5H | WK rings flank the camera,
                // mirroring the full glance's layout without any numbers. The
                // dot style keeps its classic single dot — two identical
                // unlabeled dots would say nothing.
                soloDualWindowMinimalView(for: provider)
            } else if model.hasNotch {
                ZStack(alignment: .top) {
                    // Left side of notch
                    HStack {
                        if model.providers.count >= 2 {
                            breatheDot(for: model.providers[0])
                                .notchProviderSlot(index: 0, focused: peekSlotFocused(0))
                                .padding(.leading, 13)
                        }
                        Spacer()
                    }
                    .frame(width: max(0, model.notchLeft), height: model.collapsedHeight)
                    .position(x: max(0, model.notchLeft) / 2, y: model.collapsedHeight / 2)

                    // Right side of notch
                    HStack {
                        Spacer()
                        if model.providers.count == 1 {
                            breatheDot(for: model.providers[0])
                                .notchProviderSlot(index: 0, focused: peekSlotFocused(0))
                                .padding(.trailing, 13)
                        } else if model.providers.count == 2 {
                            breatheDot(for: model.providers[1])
                                .notchProviderSlot(index: 1, focused: peekSlotFocused(1))
                                .padding(.trailing, 13)
                        } else {
                            HStack(spacing: 6) {
                                breatheDot(for: model.providers[1])
                                    .notchProviderSlot(index: 1, focused: peekSlotFocused(1))
                                breatheDot(for: model.providers[2])
                                    .notchProviderSlot(index: 2, focused: peekSlotFocused(2))
                            }
                            .padding(.trailing, 13)
                        }
                    }
                    .frame(width: max(0, targetWidth - model.notchRight), height: model.collapsedHeight)
                    .position(x: model.notchRight + max(0, targetWidth - model.notchRight) / 2, y: model.collapsedHeight / 2)
                }
            } else {
                // No notch to flank: a plain centered row. The pill hugs this
                // row's width (via the measurer), so edge-pinning would be a
                // no-op anyway.
                HStack(spacing: 9) {
                    ForEach(0..<model.providers.count, id: \.self) { idx in
                        breatheDot(for: model.providers[idx])
                            .notchProviderSlot(index: idx, focused: peekSlotFocused(idx))
                    }
                }
                .padding(.horizontal, 17)
            }
        }
    }

    var lowestProviderView: some View {
        Group {
            // This style renders one provider, so peek can only ever detail
            // that one — the bubble points at the slot the strip actually
            // shows, and there is nothing else on the strip to target.
            if let idx = model.providers.indices.min(by: {
                compactRank(for: model.providers[$0]) < compactRank(for: model.providers[$1])
            }) {
                let lowest = model.providers[idx]
                if model.hasNotch {
                    // Positioned safely to the right of the notch
                    HStack {
                        Spacer()
                        providerStatusLabel(for: lowest, showName: false)
                            .notchProviderSlot(index: idx, focused: peekSlotFocused(idx))
                            .padding(.trailing, 13)
                    }
                    .frame(width: max(0, targetWidth - model.notchRight), height: model.collapsedHeight)
                    .position(x: model.notchRight + max(0, targetWidth - model.notchRight) / 2, y: model.collapsedHeight / 2)
                } else {
                    providerStatusLabel(for: lowest, showName: false)
                        .notchProviderSlot(index: idx, focused: peekSlotFocused(idx))
                        .padding(.horizontal, 17)
                }
            }
        }
    }

    var allProvidersView: some View {
        Group {
            if notchIsSoloSplit(model.providers) {
                soloAllProvidersView
            } else if notchUsesSoloDualWindowGlance(model.providers),
                      let provider = model.providers.first {
                soloDualWindowCollapsedView(for: provider)
            } else if model.hasNotch {
                ZStack(alignment: .top) {
                    // Left side of notch
                    HStack {
                        if model.providers.count >= 2 {
                            providerStatusLabel(for: model.providers[0], showName: false)
                                .notchProviderSlot(index: 0, focused: peekSlotFocused(0))
                                .padding(.leading, 13)
                        }
                        Spacer()
                    }
                    .frame(width: max(0, model.notchLeft), height: model.collapsedHeight)
                    .position(x: max(0, model.notchLeft) / 2, y: model.collapsedHeight / 2)
                    
                    // Right side of notch
                    HStack {
                        Spacer()
                        if model.providers.count == 1 {
                            providerStatusLabel(for: model.providers[0], showName: false)
                                .notchProviderSlot(index: 0, focused: peekSlotFocused(0))
                                .padding(.trailing, 13)
                        } else if model.providers.count == 2 {
                            providerStatusLabel(for: model.providers[1], showName: false)
                                .notchProviderSlot(index: 1, focused: peekSlotFocused(1))
                                .padding(.trailing, 13)
                        } else {
                            HStack(spacing: 6) {
                                providerStatusLabel(for: model.providers[1], showName: false)
                                    .notchProviderSlot(index: 1, focused: peekSlotFocused(1))
                                providerStatusLabel(for: model.providers[2], showName: false)
                                    .notchProviderSlot(index: 2, focused: peekSlotFocused(2))
                            }
                            .padding(.trailing, 13)
                        }
                    }
                    .frame(width: max(0, targetWidth - model.notchRight), height: model.collapsedHeight)
                    .position(x: model.notchRight + max(0, targetWidth - model.notchRight) / 2, y: model.collapsedHeight / 2)
                }
            } else {
                // No notch to flank: a plain centered row (see ambientDotsView).
                HStack(spacing: 14) {
                    ForEach(0..<model.providers.count, id: \.self) { idx in
                        providerStatusLabel(for: model.providers[idx], showName: false)
                            .notchProviderSlot(index: idx, focused: peekSlotFocused(idx))
                    }
                }
                .padding(.horizontal, 17)
            }
        }
    }

    // A single subscription provider uses the camera as a semantic divider:
    // the short rolling window stays on the left and the weekly cap on the
    // right. Healthy windows show one compact remaining value; only a depleted
    // window swaps that value for its reset timer. This keeps the always-on
    // strip glanceable while peek/expanded retain the full detail.
    @ViewBuilder
    func soloDualWindowCollapsedView(for provider: ProviderUsage) -> some View {
        let state = quotaState(for: provider)
        let visual = providerVisual(for: provider.provider)
        let uncappedFiveHour = isCodexFiveHourTemporarilyUncapped(provider)

        if model.hasNotch {
            ZStack(alignment: .top) {
                HStack {
                    soloWindowGlance(
                        label: "5H",
                        snapshot: state.fiveHour,
                        brandColor: visual.brandColor,
                        uncapped: uncappedFiveHour
                    )
                    .padding(.leading, 13)
                    Spacer()
                }
                .frame(width: max(0, model.notchLeft), height: model.collapsedHeight)
                .position(x: max(0, model.notchLeft) / 2, y: model.collapsedHeight / 2)

                HStack {
                    Spacer()
                    soloWindowGlance(
                        label: notchWindowShortTag(state.weekly, defaultTag: "WK"),
                        snapshot: state.weekly,
                        brandColor: visual.brandColor
                    )
                    .padding(.trailing, 13)
                }
                .frame(width: max(0, targetWidth - model.notchRight), height: model.collapsedHeight)
                .position(
                    x: model.notchRight + max(0, targetWidth - model.notchRight) / 2,
                    y: model.collapsedHeight / 2
                )
            }
        } else {
            HStack(spacing: 10) {
                soloWindowGlance(
                    label: "5H",
                    snapshot: state.fiveHour,
                    brandColor: visual.brandColor,
                    uncapped: uncappedFiveHour
                )
                Rectangle()
                    .fill(Color.white.opacity(0.16))
                    .frame(width: 0.5, height: 12)
                soloWindowGlance(
                    label: notchWindowShortTag(state.weekly, defaultTag: "WK"),
                    snapshot: state.weekly,
                    brandColor: visual.brandColor
                )
            }
            .padding(.horizontal, 17)
        }
    }

    // Minimal (ambient) rendering of the solo 5H | WK glance: just the two
    // labeled window gauges, no numbers. The labels stay even in the numeral
    // style — Minimal's contract is "no numbers", and an anonymous pair of
    // rings would be unreadable to a new user.
    @ViewBuilder
    func soloDualWindowMinimalView(for provider: ProviderUsage) -> some View {
        let state = quotaState(for: provider)
        let visual = providerVisual(for: provider.provider)
        let uncappedFiveHour = isCodexFiveHourTemporarilyUncapped(provider)
        let left = windowGauge(
            label: "5H",
            snapshot: state.fiveHour,
            brandColor: visual.brandColor,
            uncapped: uncappedFiveHour,
            size: 17,
            numeralAllowed: false
        )
        let right = windowGauge(
            label: notchWindowShortTag(state.weekly, defaultTag: "WK"),
            snapshot: state.weekly,
            brandColor: visual.brandColor,
            size: 17,
            numeralAllowed: false
        )
        if model.hasNotch {
            ZStack(alignment: .top) {
                HStack {
                    left.padding(.leading, 13)
                    Spacer()
                }
                .frame(width: max(0, model.notchLeft), height: model.collapsedHeight)
                .position(x: max(0, model.notchLeft) / 2, y: model.collapsedHeight / 2)

                HStack {
                    Spacer()
                    right.padding(.trailing, 13)
                }
                .frame(width: max(0, targetWidth - model.notchRight), height: model.collapsedHeight)
                .position(
                    x: model.notchRight + max(0, targetWidth - model.notchRight) / 2,
                    y: model.collapsedHeight / 2
                )
            }
        } else {
            HStack(spacing: 10) {
                left
                Rectangle()
                    .fill(Color.white.opacity(0.16))
                    .frame(width: 0.5, height: 12)
                right
            }
            .padding(.horizontal, 17)
        }
    }

    @ViewBuilder
    func soloWindowGlance(
        label: String,
        snapshot: QuotaWindowSnapshot?,
        brandColor: NSColor,
        uncapped: Bool = false
    ) -> some View {
        HStack(alignment: usesShapedGauge ? .center : .firstTextBaseline, spacing: usesShapedGauge ? 5 : 3) {
            if usesShapedGauge {
                // Ring style: the window label (or, in the numeral style, the
                // remaining number) moves inside the ring.
                windowGauge(
                    label: label,
                    snapshot: snapshot,
                    brandColor: brandColor,
                    uncapped: uncapped,
                    size: gaugeNumeralInside ? 18 : 17
                )
            } else {
                Text(label)
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(Color(brandColor).opacity(0.86))
                    .lineLimit(1)
            }

            if uncapped {
                // The numeral style already carries the ∞ inside the ring.
                if !gaugeNumeralInside {
                    Text("∞")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundColor(Color(NSColor.systemGreen).opacity(0.88))
                }
            } else if let snapshot = snapshot, snapshot.depleted {
                // Exhausted: the reset countdown always surfaces, whatever the
                // gauge style — it's the one number that matters right now.
                let timer = compactTimerString(for: snapshot)
                Text(timer.isEmpty || timer == "now" ? "0%" : timer)
                    .font(.system(size: 11, weight: .bold))
                    .monospacedDigit()
                    .foregroundColor(Color(NSColor.systemRed))
                    .minimumScaleFactor(0.85)
            } else if !gaugeHidesSideValue {
                Text("\((snapshot?.remainingText ?? "0"))%")
                    .font(.system(size: 11, weight: .bold))
                    .monospacedDigit()
                    .foregroundColor(.white)
            }
        }
        .lineLimit(1)
        .fixedSize(horizontal: true, vertical: false)
        .accessibilityElement(children: .combine)
    }

    // Solo split collapsed: flank the two pools around the camera like a
    // 2-provider glance (GEM left shoulder, EXT right), pool color telling them
    // apart. Mirrors the expanded panel's left-GEM / right-EXT placement.
    var soloAllProvidersView: some View {
        let units = soloPoolUnits()
        return Group {
            if model.hasNotch {
                ZStack(alignment: .top) {
                    HStack {
                        if units.count >= 1 {
                            poolStatusLabel(units[0]).padding(.leading, 13)
                        }
                        Spacer()
                    }
                    .frame(width: max(0, model.notchLeft), height: model.collapsedHeight)
                    .position(x: max(0, model.notchLeft) / 2, y: model.collapsedHeight / 2)

                    HStack {
                        Spacer()
                        if units.count >= 2 {
                            poolStatusLabel(units[1]).padding(.trailing, 13)
                        }
                    }
                    .frame(width: max(0, targetWidth - model.notchRight), height: model.collapsedHeight)
                    .position(x: model.notchRight + max(0, targetWidth - model.notchRight) / 2, y: model.collapsedHeight / 2)
                }
            } else {
                // No notch to flank: a plain centered row (see ambientDotsView).
                HStack(spacing: 14) {
                    ForEach(0..<units.count, id: \.self) { idx in
                        poolStatusLabel(units[idx])
                    }
                }
                .padding(.horizontal, 17)
            }
        }
    }

    var soloAmbientDotsView: some View {
        let units = soloPoolUnits()
        return Group {
            if model.hasNotch {
                ZStack(alignment: .top) {
                    HStack {
                        if units.count >= 1 {
                            poolBreatheDot(units[0]).padding(.leading, 13)
                        }
                        Spacer()
                    }
                    .frame(width: max(0, model.notchLeft), height: model.collapsedHeight)
                    .position(x: max(0, model.notchLeft) / 2, y: model.collapsedHeight / 2)

                    HStack {
                        Spacer()
                        if units.count >= 2 {
                            poolBreatheDot(units[1]).padding(.trailing, 13)
                        }
                    }
                    .frame(width: max(0, targetWidth - model.notchRight), height: model.collapsedHeight)
                    .position(x: model.notchRight + max(0, targetWidth - model.notchRight) / 2, y: model.collapsedHeight / 2)
                }
            } else {
                // No notch to flank: a plain centered row (see ambientDotsView).
                HStack(spacing: 9) {
                    ForEach(0..<units.count, id: \.self) { idx in
                        poolBreatheDot(units[idx])
                    }
                }
                .padding(.horizontal, 17)
            }
        }
    }

    /// Whether this slot is the one the peek bubble is pointing at. Always
    /// false outside peek, so the always-on strip carries no highlight.
    func peekSlotFocused(_ index: Int) -> Bool {
        model.notchState == .peek && model.usesBubblePeek && peekFocusIndex == index
    }

    func compactRank(for provider: ProviderUsage) -> Double {
        quota.attentionRank(for: provider)
    }

    func providerStatusLabel(for p: ProviderUsage, showName: Bool) -> some View {
        let visual = providerVisual(for: p.provider)
        let state = quotaState(for: p)
        return HStack(spacing: 5) {
            if state.mode != .credits {
                glanceGauge(
                    mode: state.mode,
                    remaining: state.bindingRemaining,
                    brandColor: visual.brandColor,
                    label: gaugeNumeralInside && state.mode == .healthy
                        ? QuotaFormatter.remainingPercentText(state.bindingRemaining) : nil,
                    size: gaugeNumeralInside ? 18 : 12
                )
            }

            if showName {
                Text(providerDisplayName(for: p.provider))
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(.white.opacity(0.85))
                    .lineLimit(1)
            }

            if state.mode == .loading {
                Text("…")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(.white.opacity(0.5))
                    .fixedSize(horizontal: true, vertical: false)
            } else if state.mode == .credits, let credits = state.credits {
                HStack(spacing: 3) {
                    CoinIcon(size: 8)
                    Text(
                        QuotaFormatter.formatCompactCreditBalance(
                            credits,
                            currency: p.windows.first(where: { $0.key == "ai_credits" })?.currency
                        )
                    )
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(Color(NSColor.systemGreen))
                        .fixedSize(horizontal: true, vertical: false)
                }
            } else if state.mode == .locked && gaugeHidesSideValue {
                // The number-suppressing ring styles trade "0%" for the reset
                // countdown — the one number that matters while exhausted.
                let timer = compactTimerString(for: state.lockSnapshot)
                Text(timer.isEmpty || timer == "now" ? "0%" : timer)
                    .font(.system(size: 11, weight: .bold))
                    .monospacedDigit()
                    .foregroundColor(Color(NSColor.systemRed))
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            } else if !gaugeHidesSideValue {
                // Collapsed glance stays lean: just the % (red when locked).
                // The pool tag (EXT) and the locked window tag (WK/5H) are detail
                // for the wider peek/expanded surfaces — in the narrow strip beside
                // the notch they overflowed into the camera and wrapped vertically.
                let depletedGlance = state.mode == .locked || state.mode == .recovering
                Text("\(depletedGlance ? "0" : QuotaFormatter.remainingPercentText(state.bindingRemaining))%")
                    .font(.system(size: 11, weight: .bold))
                    // Recovering = predicted reset elapsed, confirming: amber, not
                    // the red of a hard lock, so the glance reads "coming back".
                    .foregroundColor(state.mode == .locked ? Color(NSColor.systemRed)
                        : state.mode == .recovering ? Color(NSColor.systemYellow)
                        : .white)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            }
        }
    }

    func compactTag(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.system(size: 8, weight: .bold))
            // Never let the tag compress/wrap — under the peek spread layout the
            // Spacers would otherwise squeeze it into a deformed empty box.
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
            .padding(.horizontal, 4)
            .padding(.vertical, 1)
            .background(color.opacity(0.16))
            .cornerRadius(4)
            .foregroundColor(color)
    }

    func breatheDot(for p: ProviderUsage) -> some View {
        let visual = providerVisual(for: p.provider)
        let state = quotaState(for: p)
        return glanceGauge(
            mode: state.mode,
            remaining: state.bindingRemaining,
            brandColor: visual.brandColor,
            size: 12
        )
    }

    // MARK: - Solo split glance units (collapsed / peek)
    // A single Antigravity provider's two pools, distilled into lightweight
    // display units for the compressed surfaces. The pool COLOR (GEM brand /
    // EXT teal) does the disambiguating — no text tag in the narrow strip beside
    // the camera, where it would overflow.
    struct SoloPoolUnit {
        let color: NSColor
        let remaining: Double
        let mode: ProviderDisplayMode
        let credits: Double?
        let weekZero: Bool
        let fiveZero: Bool
        let tag: String
        let five: QuotaWindowSnapshot
        let weekly: QuotaWindowSnapshot?
    }

    func splitQuotaNSColor(for snapshot: QuotaWindowSnapshot, visual: ProviderVisual) -> NSColor {
        switch snapshot.tag {
        case "EXT": return NSColor(hex: "#35D6C8")
        default: return visual.brandColor
        }
    }

    func soloPoolUnits() -> [SoloPoolUnit] {
        guard let p = model.providers.first else { return [] }
        let visual = providerVisual(for: p.provider)
        let fivePools = antigravityFiveHourPools(for: p)
        let weeklyPools = antigravityWeeklyPools(for: p)
        // Reserve credits are provider-level (shared across both pools), so a
        // depleted pool runs on the same reserve rather than locking outright.
        let credits = getCredits(for: p)
        let hasCredits = (credits ?? 0) > 0 && quota.creditsEnabled(for: p)
        return fivePools.prefix(2).map { five in
            let weekly = weeklyPools.first(where: { $0.tag == five.tag })
            let fiveZero = five.depleted
            let weekZero = weekly?.depleted == true
            let depleted = fiveZero || weekZero
            let blocking: QuotaWindowSnapshot? = weekZero ? weekly : five
            let mode: ProviderDisplayMode
            if depleted {
                mode = hasCredits ? .credits : (awaitingReset(blocking) ? .recovering : .locked)
            } else {
                mode = .healthy
            }
            return SoloPoolUnit(
                color: splitQuotaNSColor(for: five, visual: visual),
                remaining: five.remaining,
                mode: mode,
                credits: credits,
                weekZero: weekZero,
                fiveZero: fiveZero,
                tag: five.tag ?? "",
                five: five,
                weekly: weekly
            )
        }
    }

    func poolBreatheDot(_ u: SoloPoolUnit) -> some View {
        glanceGauge(mode: u.mode, remaining: u.remaining, brandColor: u.color, size: 12)
    }

    @ViewBuilder
    func poolStatusLabel(_ u: SoloPoolUnit) -> some View {
        HStack(spacing: 5) {
            if u.mode != .credits {
                glanceGauge(
                    mode: u.mode,
                    remaining: u.remaining,
                    brandColor: u.color,
                    label: gaugeNumeralInside && u.mode == .healthy ? "\(Int(u.remaining))" : nil,
                    size: gaugeNumeralInside ? 18 : 12
                )
            }
            if u.mode == .credits, let credits = u.credits {
                HStack(spacing: 3) {
                    CoinIcon(size: 8)
                    Text("\(Int(credits))")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(Color(NSColor.systemGreen))
                        .fixedSize(horizontal: true, vertical: false)
                }
            } else if u.mode == .locked && gaugeHidesSideValue {
                // Exhausted pool under a number-suppressing style: surface the
                // blocking window's reset countdown instead of "0%".
                let timer = compactTimerString(for: u.weekZero ? u.weekly : u.five)
                Text(timer.isEmpty || timer == "now" ? "0%" : timer)
                    .font(.system(size: 11, weight: .bold))
                    .monospacedDigit()
                    .foregroundColor(Color(NSColor.systemRed))
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            } else if !gaugeHidesSideValue || u.mode == .recovering {
                // A recovering pool restores its side lane even for a
                // number-suppressing gauge: the amber 0% distinguishes the
                // post-reset confirmation gap from a hard red lock.
                let depletedGlance = u.mode == .locked || u.mode == .recovering
                Text("\(Int(depletedGlance ? 0 : u.remaining))%")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(u.mode == .locked ? Color(NSColor.systemRed)
                        : u.mode == .recovering ? Color(NSColor.systemYellow)
                        : .white)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            }
        }
    }
    
}
