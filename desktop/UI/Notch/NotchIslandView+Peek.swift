import AppKit
import Foundation
import SwiftUI

/// One half of the perimeter quota rail used by the two-agent peek. The path
/// starts on the outside shoulder, rounds the island's lower corner, then
/// travels toward the centre. Trimming it therefore leaves low quota close to
/// its owning agent instead of producing an ambiguous bar across the island.
private struct PeekCornerRailShape: Shape {
    let leading: Bool
    var sideInset: CGFloat = 6
    var bottomInset: CGFloat = 6
    var cornerRadius: CGFloat = 14
    var centerGap: CGFloat = 12

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let bottom = rect.maxY - bottomInset
        let radius = min(cornerRadius, max(0, rect.height - bottomInset - sideInset))

        if leading {
            path.move(to: CGPoint(x: sideInset, y: bottom - radius))
            path.addQuadCurve(
                to: CGPoint(x: sideInset + radius, y: bottom),
                control: CGPoint(x: sideInset, y: bottom)
            )
            path.addLine(to: CGPoint(x: rect.midX - centerGap, y: bottom))
        } else {
            path.move(to: CGPoint(x: rect.maxX - sideInset, y: bottom - radius))
            path.addQuadCurve(
                to: CGPoint(x: rect.maxX - sideInset - radius, y: bottom),
                control: CGPoint(x: rect.maxX - sideInset, y: bottom)
            )
            path.addLine(to: CGPoint(x: rect.midX + centerGap, y: bottom))
        }
        return path
    }
}

// NotchIslandView — peek tray.
// Split out of NotchWindow.swift for navigability; same type via extension.
extension NotchIslandView {
    // MARK: - 2. Peek View

    // The provider's coloured glance gauge (dot / ring / liquid per the
    // gauge-style preference). The numbers-inside placement carries into the
    // peek: callers pass the number as `label` with a larger size, and drop
    // their beside-% so the value isn't shown twice.
    func peekGauge(
        mode: ProviderDisplayMode,
        remaining: Double,
        brandColor: NSColor,
        label: String? = nil,
        size: CGFloat = 13
    ) -> some View {
        glanceGauge(mode: mode, remaining: remaining, brandColor: brandColor, label: label, size: size)
    }

    // F-style status caption under an exhausted segment ("5H · Exhausted"):
    // the sentence treatment the design mock reserves for abnormal states.
    func exhaustedCaption(_ windowLabel: String) -> some View {
        Text("\(windowLabel) · \(L10n.tr("notch.peek.exhausted"))")
            .font(.system(size: 9.5, weight: .medium))
            .foregroundColor(Color(NSColor.systemRed).opacity(0.72))
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
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
    func peekTimer(
        _ text: String,
        label: String? = nil,
        locked: Bool = false,
        emphasized: Bool = false
    ) -> some View {
        HStack(spacing: 3) {
            if let label = label {
                Text(label)
                    .font(.system(size: 8.5, weight: .bold))
                    .foregroundColor(
                        locked
                            ? Color(NSColor.systemRed).opacity(0.7)
                            : .white.opacity(emphasized ? 0.54 : 0.34)
                    )
                    .frame(width: 16, alignment: .trailing)
            }
            Image(systemName: "arrow.triangle.2.circlepath")
                .font(.system(size: 8.5, weight: .semibold))
            Text(text)
                .font(.system(size: 11, weight: .medium))
                .monospacedDigit()
                .fixedSize(horizontal: true, vertical: false)
        }
        .foregroundColor(
            locked
                ? Color(NSColor.systemRed).opacity(0.8)
                : .white.opacity(emphasized ? 0.6 : 0.5)
        )
        .lineLimit(1)
    }

    // Transitional glance for the recovering state: the predicted reset has
    // elapsed and we're confirming with the provider. Replaces the vanished
    // countdown + stuck red "0%" with a neutral amber "confirming…" so the
    // peek never freezes on a finished timer.
    @ViewBuilder
    func peekConfirming() -> some View {
        HStack(spacing: 3) {
            Image(systemName: "arrow.triangle.2.circlepath")
                .font(.system(size: 8.5, weight: .semibold))
            Text(L10n.tr("notch.confirming"))
                .font(.system(size: 11, weight: .medium))
                .fixedSize(horizontal: true, vertical: false)
        }
        .foregroundColor(Color(NSColor.systemYellow).opacity(0.9))
        .lineLimit(1)
    }

    @ViewBuilder
    func dualAgentTimer(
        _ snapshot: QuotaWindowSnapshot?,
        locked: Bool = false,
        emphasized: Bool = false
    ) -> some View {
        let timer = snapshot.map { timerString(for: $0) } ?? ""
        if !timer.isEmpty && timer != "now" {
            HStack(spacing: 2.5) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: 7.5, weight: .semibold))
                Text(timer)
                    .font(.system(size: 9.5, weight: emphasized ? .semibold : .medium))
                    .monospacedDigit()
            }
            .foregroundColor(
                locked
                    ? Color(NSColor.systemRed).opacity(0.8)
                    : .white.opacity(emphasized ? 0.62 : 0.46)
            )
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
        }
    }

    /// Two compact rows for one provider: 5H owns the ring and first-row
    /// countdown; WK owns the second row and the perimeter rail drawn by the
    /// parent. This keeps both reset times without returning to the old
    /// three-line stack.
    @ViewBuilder
    func dualAgentArcSegment(for provider: ProviderUsage) -> some View {
        let visual = providerVisual(for: provider.provider)
        let state = quotaState(for: provider)
        let five = state.fiveHour
        let weekly = state.weekly
        let fiveUncapped = isCodexFiveHourTemporarilyUncapped(provider)
        let fiveRemaining = five?.remaining ?? 100
        let weeklyRemaining = weekly?.remaining ?? 0
        let fiveLocked = !fiveUncapped && five != nil && fiveRemaining <= 0
        let weeklyCritical = weekly != nil && weeklyRemaining <= QuotaLevel.criticalRemaining

        HStack(alignment: .center, spacing: 8) {
            if usesShapedGauge {
                windowGauge(
                    label: "5H",
                    snapshot: five,
                    brandColor: visual.brandColor,
                    uncapped: fiveUncapped,
                    size: 27,
                    numeralAllowed: false
                )
            } else {
                HStack(spacing: 4) {
                    peekGauge(
                        mode: fiveLocked ? .locked : .healthy,
                        remaining: fiveRemaining,
                        brandColor: visual.brandColor
                    )
                    Text("5H")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(Color(visual.brandColor).opacity(0.9))
                }
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(fiveUncapped ? "∞" : "\(Int(fiveRemaining))%")
                        .font(.system(size: 13.5, weight: .bold))
                        .monospacedDigit()
                        .foregroundColor(
                            fiveUncapped
                                ? Color(NSColor.systemGreen)
                                : (fiveLocked ? Color(NSColor.systemRed) : .white)
                        )
                        .lineLimit(1)
                    if fiveUncapped {
                        Text(L10n.tr("notch.five_hour_uncapped"))
                            .font(.system(size: 8.5, weight: .medium))
                            // The green infinity is the semantic signal; the
                            // descriptor stays neutral so it does not outweigh
                            // the other provider's percentage and timer.
                            .foregroundColor(.white.opacity(0.44))
                            .lineLimit(1)
                    } else {
                        dualAgentTimer(five, locked: fiveLocked, emphasized: true)
                    }
                }

                HStack(spacing: 5) {
                    Text("WK")
                        .font(.system(size: 9.5, weight: .bold))
                        .foregroundColor(weeklyCritical
                            ? Color(NSColor.systemRed).opacity(0.76)
                            : Color(visual.brandColor).opacity(0.86))
                    Text("\(Int(weeklyRemaining))%")
                        .font(.system(size: 11, weight: .bold))
                        .monospacedDigit()
                        .foregroundColor(weeklyCritical ? Color(NSColor.systemRed) : .white.opacity(0.88))
                        .lineLimit(1)
                    dualAgentTimer(weekly, locked: weeklyCritical)
                }
            }
        }
        .fixedSize(horizontal: true, vertical: false)
        .accessibilityElement(children: .combine)
    }

    func peekCornerRails(
        leftRemaining: Double,
        rightRemaining: Double,
        leftBrand: NSColor,
        rightBrand: NSColor
    ) -> some View {
        let leftColor = leftRemaining <= QuotaLevel.criticalRemaining ? NSColor.systemRed : leftBrand
        let rightColor = rightRemaining <= QuotaLevel.criticalRemaining ? NSColor.systemRed : rightBrand
        let railStroke = StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round)

        return ZStack {
            PeekCornerRailShape(leading: true)
                .stroke(Color.white.opacity(0.10), style: railStroke)
            PeekCornerRailShape(leading: false)
                .stroke(Color.white.opacity(0.10), style: railStroke)
            PeekCornerRailShape(leading: true)
                .trim(from: 0, to: min(max(leftRemaining / 100, 0), 1))
                .stroke(Color(leftColor).opacity(0.92), style: railStroke)
                .shadow(color: Color(leftColor).opacity(0.18), radius: 1.5)
            PeekCornerRailShape(leading: false)
                .trim(from: 0, to: min(max(rightRemaining / 100, 0), 1))
                .stroke(Color(rightColor).opacity(0.92), style: railStroke)
                .shadow(color: Color(rightColor).opacity(0.18), radius: 1.5)
        }
        // Only real quota updates interpolate. Entering Peek itself is static
        // so the rail remains an ambient indicator rather than an attraction.
        .animation(.easeOut(duration: 0.35), value: leftRemaining)
        .animation(.easeOut(duration: 0.35), value: rightRemaining)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    @ViewBuilder
    func dualAgentWeeklyRails() -> some View {
        if model.providers.count == 2 {
            let leftState = quotaState(for: model.providers[0])
            let rightState = quotaState(for: model.providers[1])
            peekCornerRails(
                leftRemaining: leftState.weekly?.remaining ?? 0,
                rightRemaining: rightState.weekly?.remaining ?? 0,
                leftBrand: providerVisual(for: model.providers[0].provider).brandColor,
                rightBrand: providerVisual(for: model.providers[1].provider).brandColor
            )
        }
    }

    func peekSeg(for p: ProviderUsage) -> AnyView {
        let visual = providerVisual(for: p.provider)
        let state = quotaState(for: p)

        if state.mode == .loading {
            // Not fetched yet — a quiet ellipsis, not a fake 100% or an error.
            return AnyView(HStack(spacing: 6) {
                peekGauge(mode: .loading, remaining: 0, brandColor: visual.brandColor)
                Text("…")
                    .font(.system(size: 12.5, weight: .bold))
                    .foregroundColor(.white.opacity(0.5))
                    .fixedSize(horizontal: true, vertical: false)
            })
        } else if state.mode == .credits, let creds = state.credits {
            // On reserve credits — show the balance, no countdown.
            return AnyView(HStack(spacing: 6) {
                peekGauge(mode: .credits, remaining: 0, brandColor: visual.brandColor)
                HStack(spacing: 2) {
                    Circle()
                        .fill(RadialGradient(colors: [Color(NSColor(hex: "#ffd700")), Color(NSColor(hex: "#daa520"))], center: .center, startRadius: 0, endRadius: 4))
                        .frame(width: 7, height: 7)
                    Text(
                        QuotaFormatter.formatCreditBalance(
                            creds,
                            currency: p.windows.first(where: { $0.key == "ai_credits" })?.currency
                        )
                    )
                        .font(.system(size: 12.5, weight: .bold))
                        .monospacedDigit()
                        .foregroundColor(Color(NSColor.systemGreen))
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
            })
        } else if state.mode == .locked {
            // Locked — the reset countdown IS the headline (the design mock's
            // "28m / Exhausted" treatment); a dead "0%" says nothing the user
            // needs right now. In Both mode the F-style status caption sits
            // under it; inline mode tags the blocking window beside it.
            // lockSnapshot already points at the blocking window — and for a
            // split-pool provider, at the earliest-recovering pool's window,
            // which the merged per-kind getters can't express.
            let lockTimer = timerString(for: state.lockSnapshot)
            let lockLabel = state.lockResetKind == .weekly ? "WK" : "5H"
            let hasTimer = !lockTimer.isEmpty && lockTimer != "now"
            let headline = Text(hasTimer ? lockTimer : "0%")
                .font(.system(size: 12.5, weight: .bold))
                .monospacedDigit()
                .foregroundColor(Color(NSColor.systemRed))
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
            if model.peekReset == "both" {
                return AnyView(VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        peekGauge(mode: .locked, remaining: 0, brandColor: visual.brandColor)
                        headline
                    }
                    exhaustedCaption(lockLabel)
                })
            } else {
                return AnyView(HStack(spacing: 6) {
                    peekGauge(mode: .locked, remaining: 0, brandColor: visual.brandColor)
                    headline
                    compactTag(lockLabel, color: Color(NSColor.systemRed).opacity(0.85))
                })
            }
        } else if state.mode == .recovering {
            return AnyView(HStack(spacing: 6) {
                peekGauge(mode: .recovering, remaining: 0, brandColor: visual.brandColor)
                peekConfirming()
            })
        } else if model.peekReset == "both" {
            // Both timers: header row over two labeled countdowns, stacked so
            // the tray grows DOWN, not sideways. Numbers-inside placement puts
            // the % in the gauge and drops the beside-%, mirroring collapsed.
            let t5 = get5hResetTimer(for: p)
            let tw = getWeeklyResetTimer(for: p)
            let isThreeProviderPeek = model.providers.count == 3
            let fiveUncapped = isCodexFiveHourTemporarilyUncapped(p)
            return AnyView(VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    peekGauge(
                        mode: .healthy,
                        remaining: state.bindingRemaining,
                        brandColor: visual.brandColor,
                        label: gaugeNumeralInside ? "\(Int(state.bindingRemaining))" : nil,
                        size: gaugeNumeralInside ? 18 : 13
                    )
                    if !gaugeNumeralInside {
                        peekPercent(
                            remaining: state.bindingRemaining,
                            tag: isThreeProviderPeek ? nil : state.bindingTag
                        )
                    } else if !isThreeProviderPeek, let tag = state.bindingTag {
                        compactTag(tag, color: Color.white.opacity(0.82))
                    }
                    if isThreeProviderPeek {
                        // The numeral ring may bind to 5H for one provider and
                        // WK for another. Always name that window in the same
                        // position; a pool tag is subordinate plain text, not
                        // the lone heavy badge in the three-column header.
                        HStack(spacing: 2.5) {
                            Text(state.bindingLabel.rawValue.uppercased())
                                .foregroundColor(Color(visual.brandColor).opacity(0.82))
                            if let tag = state.bindingTag {
                                Text("·")
                                    .foregroundColor(.white.opacity(0.28))
                                Text(tag)
                                    .foregroundColor(.white.opacity(0.42))
                            }
                        }
                        .font(.system(size: 8.5, weight: .bold))
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                    }
                }
                if isThreeProviderPeek && fiveUncapped {
                    HStack(spacing: 3) {
                        Text("5H")
                            .font(.system(size: 8.5, weight: .bold))
                            .foregroundColor(.white.opacity(0.34))
                            .frame(width: 16, alignment: .trailing)
                        Text("∞")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(Color(NSColor.systemGreen))
                    }
                    .lineLimit(1)
                    .accessibilityLabel("5H \(L10n.tr("notch.five_hour_uncapped"))")
                } else if !t5.isEmpty && t5 != "now" {
                    peekTimer(t5, label: "5H")
                }
                if !tw.isEmpty && tw != "now" { peekTimer(tw, label: "WK") }
            })
        } else {
            // Single window — the one the user chose (5-hour or weekly).
            // If that window is absent, fall back to the real window that is
            // available. Keep percent and countdown sourced from the same
            // snapshot so a weekly percentage never accompanies a 5h timer.
            let preferred = model.peekReset == "weekly"
                ? (state.weekly ?? state.fiveHour)
                : (state.fiveHour ?? state.weekly)
            let remaining = preferred?.remaining ?? state.bindingRemaining
            let timer = timerString(for: preferred)
            return AnyView(HStack(spacing: 6) {
                peekGauge(
                    mode: .healthy,
                    remaining: remaining,
                    brandColor: visual.brandColor,
                    label: gaugeNumeralInside ? "\(Int(remaining))" : nil,
                    size: gaugeNumeralInside ? 18 : 13
                )
                if !gaugeNumeralInside {
                    peekPercent(remaining: remaining, tag: preferred?.tag ?? state.bindingTag)
                } else if let tag = preferred?.tag ?? state.bindingTag {
                    compactTag(tag, color: Color.white.opacity(0.82))
                }
                if !timer.isEmpty && timer != "now" {
                    peekTimer(timer)
                }
            })
        }
    }

    // Single-provider peek preserves the collapsed pair of rings, but drops
    // them below the physical notch and attaches each ring directly to its
    // label/countdown. This is a progressive reveal of the same two anchors,
    // not the old shoulder layout enlarged into the camera housing.
    @ViewBuilder
    func peekSoloDualWindowSeg(for provider: ProviderUsage) -> some View {
        let state = quotaState(for: provider)
        let visual = providerVisual(for: provider.provider)
        let five = peekWindowInline(
            label: "5H",
            snapshot: state.fiveHour,
            brandColor: visual.brandColor,
            uncapped: isCodexFiveHourTemporarilyUncapped(provider)
        )
        let week = peekWindowInline(
            label: "WK",
            snapshot: state.weekly,
            brandColor: visual.brandColor
        )
        HStack(alignment: .center, spacing: model.hasNotch ? 34 : 18) {
            five
            if !model.hasNotch {
                Rectangle()
                    .fill(Color.white.opacity(0.14))
                    .frame(width: 0.5, height: 26)
            }
            week
        }
    }

    @ViewBuilder
    func peekWindowInline(
        label: String,
        snapshot: QuotaWindowSnapshot?,
        brandColor: NSColor,
        uncapped: Bool = false
    ) -> some View {
        let locked = !uncapped && snapshot != nil && (snapshot?.remaining ?? 100) <= 0
        let timer = snapshot.map { timerString(for: $0) } ?? ""
        let hasTimer = !timer.isEmpty && timer != "now"
        HStack(alignment: .center, spacing: 7) {
            if usesShapedGauge {
                windowGauge(
                    label: label,
                    snapshot: snapshot,
                    brandColor: brandColor,
                    uncapped: uncapped,
                    size: 25
                )
            } else {
                peekGauge(
                    mode: locked ? .locked : .healthy,
                    remaining: snapshot?.remaining ?? 0,
                    brandColor: brandColor
                )
            }

            VStack(alignment: .leading, spacing: 2.5) {
                HStack(spacing: 4) {
                    // Ring/liquid gauges already carry 5H/WK in their center.
                    // Keep the outside label only when the center is occupied
                    // by the numeral, or when a dot cannot carry text at all.
                    if !usesShapedGauge || gaugeNumeralInside {
                        Text(label)
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(
                                locked
                                    ? Color(NSColor.systemRed).opacity(0.78)
                                    : Color(brandColor).opacity(0.88)
                            )
                    }
                    if !gaugeNumeralInside {
                        Text(uncapped ? "∞" : "\(Int(snapshot?.remaining ?? 0))%")
                            .font(.system(size: 11.5, weight: .bold))
                            .monospacedDigit()
                            .foregroundColor(
                                uncapped
                                    ? Color(NSColor.systemGreen)
                                    : (locked ? Color(NSColor.systemRed) : .white.opacity(0.92))
                            )
                    }
                }
                .lineLimit(1)

                if uncapped {
                    Text(L10n.tr("notch.five_hour_uncapped"))
                        .font(.system(size: 8.5, weight: .medium))
                        .foregroundColor(Color(NSColor.systemGreen).opacity(0.62))
                        .lineLimit(1)
                } else if hasTimer {
                    dualAgentTimer(snapshot, locked: locked, emphasized: true)
                } else if locked {
                    Text(L10n.tr("notch.peek.exhausted"))
                        .font(.system(size: 8.5, weight: .medium))
                        .foregroundColor(Color(NSColor.systemRed).opacity(0.72))
                        .lineLimit(1)
                }
            }
        }
        .fixedSize(horizontal: true, vertical: false)
        .accessibilityElement(children: .combine)
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
                peekGauge(mode: .credits, remaining: 0, brandColor: u.color)
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
            // Locked pool — countdown-as-headline, matching peekSeg's
            // exhausted treatment.
            let lockTimer = u.weekZero ? timerString(for: u.weekly) : timerString(for: u.five)
            let lockLabel = u.weekZero ? "WK" : "5H"
            let hasTimer = !lockTimer.isEmpty && lockTimer != "now"
            let headline = Text(hasTimer ? lockTimer : "0%")
                .font(.system(size: 12.5, weight: .bold))
                .monospacedDigit()
                .foregroundColor(Color(NSColor.systemRed))
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
            if model.peekReset == "both" {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        peekGauge(mode: .locked, remaining: 0, brandColor: u.color)
                        headline
                    }
                    exhaustedCaption(lockLabel)
                }
            } else {
                HStack(spacing: 6) {
                    peekGauge(mode: .locked, remaining: 0, brandColor: u.color)
                    headline
                    compactTag(lockLabel, color: Color(NSColor.systemRed).opacity(0.85))
                }
            }
        } else if u.mode == .recovering {
            HStack(spacing: 6) {
                peekGauge(mode: .recovering, remaining: 0, brandColor: u.color)
                peekConfirming()
            }
        } else if model.peekReset == "both" {
            let t5 = timerString(for: u.five)
            let tw = timerString(for: u.weekly)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    // No GEM/EXT tag — the gauge color identifies the pool (same
                    // as collapsed). Two tagged segments overflow the tray.
                    peekGauge(
                        mode: .healthy,
                        remaining: u.remaining,
                        brandColor: u.color,
                        label: gaugeNumeralInside ? "\(Int(u.remaining))" : nil,
                        size: gaugeNumeralInside ? 18 : 13
                    )
                    if !gaugeNumeralInside {
                        peekPercent(remaining: u.remaining, tag: nil)
                    }
                }
                if !t5.isEmpty && t5 != "now" { peekTimer(t5, label: "5H") }
                if !tw.isEmpty && tw != "now" { peekTimer(tw, label: "WK") }
            }
        } else {
            let timer = model.peekReset == "weekly" ? timerString(for: u.weekly) : timerString(for: u.five)
            HStack(spacing: 6) {
                // No GEM/EXT tag — the gauge color identifies the pool (same as
                // collapsed). Two tagged segments overflow the tray.
                peekGauge(
                    mode: .healthy,
                    remaining: u.remaining,
                    brandColor: u.color,
                    label: gaugeNumeralInside ? "\(Int(u.remaining))" : nil,
                    size: gaugeNumeralInside ? 18 : 13
                )
                if !gaugeNumeralInside {
                    peekPercent(remaining: u.remaining, tag: nil)
                }
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
        if model.usesDualAgentArcPeek {
            HStack(spacing: 0) {
                dualAgentArcSegment(for: model.providers[0])
                    .frame(maxWidth: .infinity, alignment: .leading)
                Spacer(minLength: 28)
                dualAgentArcSegment(for: model.providers[1])
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        } else if notchUsesSoloDualWindowGlance(model.providers),
           let provider = model.providers.first {
            peekSoloDualWindowSeg(for: provider)
        } else if soloUnits.isEmpty {
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
        let isBoth = model.usesTallPeekLayout
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
        .padding(.bottom, model.hasNotch ? (model.usesDualAgentArcPeek ? 15 : 11) : 0)
        .frame(height: model.peekHeight, alignment: model.hasNotch ? .bottom : .center)
        .overlay {
            if model.usesDualAgentArcPeek {
                dualAgentWeeklyRails()
            }
        }
        .background(alignment: .bottom) {
            // Measured on notched displays too: the tray's collapsed-derived
            // width is only a floor, and wide content (three segments, pool
            // tags, stacked timers) must grow it rather than clip (see
            // peekWidthWithNotch).
            peekWidthMeasurer(isBoth: isBoth)
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
