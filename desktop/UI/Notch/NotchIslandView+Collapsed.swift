import AppKit
import Foundation
import SwiftUI

// NotchIslandView — collapsed strip + shared solo-pool glance units (collapsed/peek).
// Split out of NotchWindow.swift for navigability; same type via extension.
extension NotchIslandView {
    // MARK: - 1. Collapsed View
    var collapsedView: some View {
        HStack {
            collapsedContent
        }
        .frame(height: model.collapsedHeight)
        .background {
            if !model.hasNotch {
                collapsedWidthMeasurer
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
            } else if model.hasNotch {
                ZStack(alignment: .top) {
                    // Left side of notch
                    HStack {
                        if model.providers.count >= 2 {
                            breatheDot(for: model.providers[0].provider)
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
                            breatheDot(for: model.providers[0].provider)
                                .padding(.trailing, 13)
                        } else if model.providers.count == 2 {
                            breatheDot(for: model.providers[1].provider)
                                .padding(.trailing, 13)
                        } else {
                            HStack(spacing: 6) {
                                breatheDot(for: model.providers[1].provider)
                                breatheDot(for: model.providers[2].provider)
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
                    ForEach(model.providers, id: \.provider) { p in
                        breatheDot(for: p.provider)
                    }
                }
                .padding(.horizontal, 17)
            }
        }
    }

    var lowestProviderView: some View {
        Group {
            if let lowest = model.providers.min(by: { compactRank(for: $0) < compactRank(for: $1) }) {
                if model.hasNotch {
                    // Positioned safely to the right of the notch
                    HStack {
                        Spacer()
                        providerStatusLabel(for: lowest, showName: false)
                            .padding(.trailing, 13)
                    }
                    .frame(width: max(0, targetWidth - model.notchRight), height: model.collapsedHeight)
                    .position(x: model.notchRight + max(0, targetWidth - model.notchRight) / 2, y: model.collapsedHeight / 2)
                } else {
                    providerStatusLabel(for: lowest, showName: false)
                        .padding(.horizontal, 17)
                }
            }
        }
    }

    var allProvidersView: some View {
        Group {
            if notchIsSoloSplit(model.providers) {
                soloAllProvidersView
            } else if model.hasNotch {
                ZStack(alignment: .top) {
                    // Left side of notch
                    HStack {
                        if model.providers.count >= 2 {
                            providerStatusLabel(for: model.providers[0], showName: false)
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
                                .padding(.trailing, 13)
                        } else if model.providers.count == 2 {
                            providerStatusLabel(for: model.providers[1], showName: false)
                                .padding(.trailing, 13)
                        } else {
                            HStack(spacing: 6) {
                                providerStatusLabel(for: model.providers[1], showName: false)
                                providerStatusLabel(for: model.providers[2], showName: false)
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
                    ForEach(model.providers, id: \.provider) { p in
                        providerStatusLabel(for: p, showName: false)
                    }
                }
                .padding(.horizontal, 17)
            }
        }
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

    func compactRank(for provider: ProviderUsage) -> Double {
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

    func providerStatusLabel(for p: ProviderUsage, showName: Bool) -> some View {
        let visual = providerVisual(for: p.provider)
        let state = quotaState(for: p)
        return HStack(spacing: 5) {
            Circle()
                .fill(Color(visual.brandColor))
                .frame(width: 7, height: 7)
                .shadow(color: Color(visual.brandColor).opacity(0.8), radius: 3)
            
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
                    CoinIcon(size: 7)
                    Text(p.provider != "antigravity" ? String(format: "$%.2f", credits) : "\(Int(credits))")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(Color(NSColor.systemGreen))
                        .fixedSize(horizontal: true, vertical: false)
                }
            } else {
                // Collapsed glance stays lean: just the % (red when locked).
                // The pool tag (EXT) and the locked window tag (WK/5H) are detail
                // for the wider peek/expanded surfaces — in the narrow strip beside
                // the notch they overflowed into the camera and wrapped vertically.
                let depletedGlance = state.mode == .locked || state.mode == .recovering
                Text("\(Int(depletedGlance ? 0 : state.bindingRemaining))%")
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

    func breatheDot(for provider: String) -> some View {
        let visual = providerVisual(for: provider)
        return Circle()
            .fill(Color(visual.brandColor))
            .frame(width: 7, height: 7)
            .shadow(color: Color(visual.brandColor).opacity(0.8), radius: 3)
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
        let hasCredits = (credits ?? 0) > 0
        return fivePools.prefix(2).map { five in
            let weekly = weeklyPools.first(where: { $0.tag == five.tag })
            let fiveZero = five.remaining <= 0
            let weekZero = (weekly?.remaining ?? 100) <= 0
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
        Circle()
            .fill(Color(u.color))
            .frame(width: 7, height: 7)
            .shadow(color: Color(u.color).opacity(0.8), radius: 3)
    }

    @ViewBuilder
    func poolStatusLabel(_ u: SoloPoolUnit) -> some View {
        HStack(spacing: 5) {
            Circle()
                .fill(Color(u.color))
                .frame(width: 7, height: 7)
                .shadow(color: Color(u.color).opacity(0.8), radius: 3)
            if u.mode == .credits, let credits = u.credits {
                HStack(spacing: 3) {
                    CoinIcon(size: 7)
                    Text("\(Int(credits))")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundColor(Color(NSColor.systemGreen))
                        .fixedSize(horizontal: true, vertical: false)
                }
            } else {
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
