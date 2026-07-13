import AppKit
import Foundation
import SwiftUI

// NotchIslandView — peek tray.
// Split out of NotchWindow.swift for navigability; same type via extension.
extension NotchIslandView {
    // MARK: - 2. Peek View

    // The provider's coloured glance dot.
    func peekDot(_ color: NSColor) -> some View {
        Circle()
            .fill(Color(color))
            .frame(width: 7, height: 7)
            .shadow(color: Color(color).opacity(0.8), radius: 3)
    }

    // Remaining % (+ optional pool tag), the headline glance metric.
    @ViewBuilder
    func peekPercent(remaining: Double, tag: String?) -> some View {
        Text("\(Int(remaining))%")
            .font(.system(size: 12.5, weight: .bold))
            .monospacedDigit()
            .foregroundColor(.white)
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
        if let tag = tag {
            compactTag(tag, color: Color.white.opacity(0.82))
        }
    }

    // Styled reset countdown: clock glyph + time, muted — mirrors the design
    // mockup (replaces the old parenthesised "(2h 11m)"). An optional window
    // label (5H/WK) precedes it in "both" mode so the two timers are unambiguous.
    @ViewBuilder
    func peekTimer(_ text: String, label: String? = nil, locked: Bool = false) -> some View {
        HStack(spacing: 3) {
            if let label = label {
                Text(label)
                    .font(.system(size: 8.5, weight: .bold))
                    .foregroundColor(locked ? Color(NSColor.systemRed).opacity(0.7) : .white.opacity(0.34))
                    .frame(width: 16, alignment: .trailing)
            }
            Image(systemName: "clock")
                .font(.system(size: 8.5, weight: .semibold))
            Text(text)
                .font(.system(size: 11, weight: .medium))
                .monospacedDigit()
                .fixedSize(horizontal: true, vertical: false)
        }
        .foregroundColor(locked ? Color(NSColor.systemRed).opacity(0.8) : .white.opacity(0.5))
        .lineLimit(1)
    }

    @ViewBuilder
    func peekSeg(for p: ProviderUsage) -> some View {
        let visual = providerVisual(for: p.provider)
        let state = quotaState(for: p)

        if state.mode == .loading {
            // Not fetched yet — a quiet ellipsis, not a fake 100% or an error.
            HStack(spacing: 6) {
                peekDot(visual.brandColor)
                Text("…")
                    .font(.system(size: 12.5, weight: .bold))
                    .foregroundColor(.white.opacity(0.5))
                    .fixedSize(horizontal: true, vertical: false)
            }
        } else if state.mode == .credits, let creds = state.credits {
            // On reserve credits — show the balance, no countdown.
            HStack(spacing: 6) {
                peekDot(visual.brandColor)
                HStack(spacing: 2) {
                    Circle()
                        .fill(RadialGradient(colors: [Color(NSColor(hex: "#ffd700")), Color(NSColor(hex: "#daa520"))], center: .center, startRadius: 0, endRadius: 4))
                        .frame(width: 7, height: 7)
                    Text(p.provider != "antigravity" ? String(format: "$%.2f", creds) : "\(Int(creds))")
                        .font(.system(size: 12.5, weight: .bold))
                        .monospacedDigit()
                        .foregroundColor(Color(NSColor.systemGreen))
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
            }
        } else if state.mode == .locked {
            // Locked — 0% + the blocking window's countdown (WK/5H folded into the
            // timer as a light label, not a boxed tag). In Both mode it stacks like
            // the healthy columns so the segment stays narrow and the tray doesn't
            // overflow; otherwise it sits inline.
            // lockSnapshot already points at the blocking window — and for a
            // split-pool provider, at the earliest-recovering pool's window,
            // which the merged per-kind getters can't express.
            let lockTimer = timerString(for: state.lockSnapshot)
            let lockLabel = state.lockResetKind == .weekly ? "WK" : "5H"
            let hasTimer = !lockTimer.isEmpty && lockTimer != "now"
            let zero = Text("0%")
                .font(.system(size: 12.5, weight: .bold))
                .monospacedDigit()
                .foregroundColor(Color(NSColor.systemRed))
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
            if model.peekReset == "both" {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        peekDot(visual.brandColor)
                        zero
                    }
                    if hasTimer {
                        peekTimer(lockTimer, label: lockLabel, locked: true)
                    }
                }
            } else {
                HStack(spacing: 6) {
                    peekDot(visual.brandColor)
                    zero
                    if hasTimer {
                        peekTimer(lockTimer, label: lockLabel, locked: true)
                    }
                }
            }
        } else if model.peekReset == "both" {
            // Both timers: header row over two labeled countdowns, stacked so
            // the tray grows DOWN, not sideways.
            let t5 = get5hResetTimer(for: p)
            let tw = getWeeklyResetTimer(for: p)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    peekDot(visual.brandColor)
                    peekPercent(remaining: state.bindingRemaining, tag: state.bindingTag)
                }
                if !t5.isEmpty && t5 != "now" { peekTimer(t5, label: "5H") }
                if !tw.isEmpty && tw != "now" { peekTimer(tw, label: "WK") }
            }
        } else {
            // Single window — the one the user chose (5-hour or weekly).
            // If that window is absent, fall back to the real window that is
            // available. Keep percent and countdown sourced from the same
            // snapshot so a weekly percentage never accompanies a 5h timer.
            let preferred = model.peekReset == "week"
                ? (state.weekly ?? state.fiveHour)
                : (state.fiveHour ?? state.weekly)
            let timer = timerString(for: preferred)
            HStack(spacing: 6) {
                peekDot(visual.brandColor)
                peekPercent(
                    remaining: preferred?.remaining ?? state.bindingRemaining,
                    tag: preferred?.tag ?? state.bindingTag
                )
                if !timer.isEmpty && timer != "now" {
                    peekTimer(timer)
                }
            }
        }
    }
    
    // Solo split peek: one segment per pool (GEM | EXT), each headlining its own
    // 5-hour remaining + countdown — the pool color and tag identify it. Mirrors
    // peekSeg's healthy/locked/both shapes so the two pools read like two
    // providers.
    @ViewBuilder
    func peekPoolSeg(_ u: SoloPoolUnit) -> some View {
        if u.mode == .credits, let creds = u.credits {
            // On reserve credits — show the shared balance, no countdown.
            HStack(spacing: 6) {
                peekDot(u.color)
                HStack(spacing: 2) {
                    Circle()
                        .fill(RadialGradient(colors: [Color(NSColor(hex: "#ffd700")), Color(NSColor(hex: "#daa520"))], center: .center, startRadius: 0, endRadius: 4))
                        .frame(width: 7, height: 7)
                    Text("\(Int(creds))")
                        .font(.system(size: 12.5, weight: .bold))
                        .monospacedDigit()
                        .foregroundColor(Color(NSColor.systemGreen))
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
            }
        } else if u.mode == .locked {
            let lockTimer = u.weekZero ? timerString(for: u.weekly) : timerString(for: u.five)
            let lockLabel = u.weekZero ? "WK" : "5H"
            let hasTimer = !lockTimer.isEmpty && lockTimer != "now"
            let zero = Text("0%")
                .font(.system(size: 12.5, weight: .bold))
                .monospacedDigit()
                .foregroundColor(Color(NSColor.systemRed))
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
            if model.peekReset == "both" {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) { peekDot(u.color); zero }
                    if hasTimer { peekTimer(lockTimer, label: lockLabel, locked: true) }
                }
            } else {
                HStack(spacing: 6) {
                    peekDot(u.color)
                    zero
                    if hasTimer { peekTimer(lockTimer, label: lockLabel, locked: true) }
                }
            }
        } else if model.peekReset == "both" {
            let t5 = timerString(for: u.five)
            let tw = timerString(for: u.weekly)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    peekDot(u.color)
                    // No GEM/EXT tag — the dot color identifies the pool (same as
                    // collapsed). Two tagged segments overflow the tray.
                    peekPercent(remaining: u.remaining, tag: nil)
                }
                if !t5.isEmpty && t5 != "now" { peekTimer(t5, label: "5H") }
                if !tw.isEmpty && tw != "now" { peekTimer(tw, label: "WK") }
            }
        } else {
            let timer = model.peekReset == "week" ? timerString(for: u.weekly) : timerString(for: u.five)
            HStack(spacing: 6) {
                peekDot(u.color)
                // No GEM/EXT tag — the dot color identifies the pool (same as
                // collapsed). Two tagged segments overflow the tray.
                peekPercent(remaining: u.remaining, tag: nil)
                if !timer.isEmpty && timer != "now" { peekTimer(timer) }
            }
        }
    }

    func peekDivider(tall: Bool) -> some View {
        Rectangle()
            .fill(Color.white.opacity(0.18))
            .frame(width: 0.5, height: tall ? 44 : 13)
    }

    // Segments + dividers only (no outer Spacers/padding). Shared by the
    // visible tray row and the hidden width measurer so the two can never
    // drift apart. The divider-flanking Spacers carry an 11pt floor, which is
    // what they collapse to under the measurer's fixedSize.
    @ViewBuilder
    func peekSegments(isBoth: Bool) -> some View {
        let soloUnits = notchIsSoloSplit(model.providers) ? soloPoolUnits() : []
        if soloUnits.isEmpty {
            ForEach(0..<model.providers.count, id: \.self) { idx in
                if idx > 0 {
                    Spacer(minLength: 11)
                    peekDivider(tall: isBoth)
                    Spacer(minLength: 11)
                }
                peekSeg(for: model.providers[idx])
            }
        } else {
            ForEach(0..<soloUnits.count, id: \.self) { idx in
                if idx > 0 {
                    Spacer(minLength: 11)
                    peekDivider(tall: isBoth)
                    Spacer(minLength: 11)
                }
                peekPoolSeg(soloUnits[idx])
            }
        }
    }

    // Invisible twin of the segment row, laid out at its natural (fixedSize)
    // width so the tray can be sized to fit on non-notched displays, where
    // there is no physical notch width to derive the tray from. Fixed
    // per-count widths clipped long content there (see the model's
    // peekWidthNoNotch). Inert: transparent and not hit-testable.
    func peekWidthMeasurer(isBoth: Bool) -> some View {
        HStack(alignment: isBoth ? .top : .bottom, spacing: 0) {
            peekSegments(isBoth: isBoth)
        }
        .fixedSize()
        .opacity(0)
        .allowsHitTesting(false)
        .background(
            GeometryReader { proxy in
                Color.clear.preference(key: PeekContentWidthKey.self, value: proxy.size.width)
            }
        )
    }

    @ViewBuilder
    var peekView: some View {
        let isBoth = model.peekReset == "both"
        // Fill the tray width and distribute the side slack across Spacers: one
        // at each outer edge plus two flanking every divider. Equal Spacers
        // split the slack evenly, so each inter-column gap (two Spacers) gets
        // ~twice an outer margin — a balanced look that pools spare room in the
        // middle rather than the edges. The divider-flanking Spacers carry an
        // 11pt floor (the old fixed gap), so when there's little slack (3
        // providers) the layout matches the previous spacing instead of
        // cramping the dividers or overflowing.
        VStack(spacing: 3) {
            HStack(alignment: isBoth ? .top : .bottom, spacing: 0) {
                Spacer(minLength: 0)
                peekSegments(isBoth: isBoth)
                Spacer(minLength: 0)
            }
            if model.isUpgradingBackend {
                Text(L10n.tr("menu.updating_components"))
                    .font(.system(size: 9, weight: .medium))
                    .foregroundColor(.white.opacity(0.6))
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 16)
        // With a notch, the content row bottom-anchors under the notch band;
        // a non-notched pill has no band, so the row centers vertically in
        // the (correspondingly shorter — see peekHeight) tray.
        .padding(.bottom, model.hasNotch ? 11 : 0)
        .frame(height: model.peekHeight, alignment: model.hasNotch ? .bottom : .center)
        .background(alignment: .bottom) {
            if !model.hasNotch {
                peekWidthMeasurer(isBoth: isBoth)
            }
        }
        .onPreferenceChange(PeekContentWidthKey.self) { width in
            let rounded = ceil(width)
            guard rounded > 1 else { return }
            guard abs(model.peekContentWidth - rounded) > 0.5 else { return }
            model.peekContentWidth = rounded
            if model.notchState == .peek {
                controller?.repositionWindow()
            }
        }
    }
    
}
