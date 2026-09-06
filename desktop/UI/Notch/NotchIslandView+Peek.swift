import AppKit
import Foundation
import SwiftUI

// NotchIslandView — peek callout.
// Split out of NotchWindow.swift for navigability; same type via extension.

// MARK: - Bubble peek
//
// With two or more providers the tray stops trying to be a table. It stays
// exactly the collapsed strip, and every countdown, bar and label moves into a
// callout bubble that hangs below it, pointing at one provider's slot.
//
// The bubble is a persistent object, not a tooltip: it is present for the whole
// of peek, pointing at the most urgent provider until the pointer picks another.
//
// The body never moves: it is centred on the strip and the TAIL does the
// pointing. An earlier version slid a narrower bubble to the focused slot, but
// the bubble was ~70% of the strip's width, so it could only shuffle a few
// points before clamping — a half-finished slide that read worse than no
// movement at all.
//
// The tray is the collapsed strip verbatim. An earlier version replaced it with
// a row of equal lanes, which reflowed the strip on hover and, worse, moved the
// provider the pointer was aiming at: slots that sit 6pt apart beside the
// camera became 130pt lanes, so aiming at the middle provider opened the right
// one. Keeping the strip put makes the target and the tail agree by
// construction. Slot centres are measured by the strip itself
// (NotchProviderAnchorsKey), and the pointer is assigned to the nearest one, so
// even a bare 12pt gauge owns a target a hundred points wide.
//
// Targeting lives in the controller (peekPointerMoved), which owns the
// hysteresis and commit delay that keep the tail from chasing a pointer merely
// passing through.

/// Rounded callout with an upward tail whose horizontal position is a free
/// parameter, so the body can stay put while the tail travels between anchors.
struct PeekBubbleShape: Shape {
    var tailX: CGFloat
    var cornerRadius: CGFloat = 13
    var tailHeight: CGFloat = 10
    var tailHalfWidth: CGFloat = 6

    var animatableData: CGFloat {
        get { tailX }
        set { tailX = newValue }
    }

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let bodyTop = rect.minY + tailHeight
        path.addRoundedRect(
            in: CGRect(x: rect.minX, y: bodyTop, width: rect.width, height: max(0, rect.height - tailHeight)),
            cornerSize: CGSize(width: cornerRadius, height: cornerRadius)
        )
        let minX = cornerRadius + tailHalfWidth
        let maxX = max(minX, rect.width - cornerRadius - tailHalfWidth)
        let anchor = rect.minX + min(max(tailX, minX), maxX)
        path.move(to: CGPoint(x: anchor - tailHalfWidth, y: bodyTop))
        path.addLine(to: CGPoint(x: anchor, y: rect.minY))
        path.addLine(to: CGPoint(x: anchor + tailHalfWidth, y: bodyTop))
        path.closeSubpath()
        return path
    }
}

extension NotchIslandView {

    /// Where the callout's entrance scales from: its own tail, in unit space.
    /// Growing from the tip is what makes the bubble read as coming OUT of the
    /// slot it points at, rather than fading in over the desktop.
    var peekBubbleTailAnchor: UnitPoint {
        guard !model.providers.isEmpty else { return .top }
        let anchor = model.providerAnchors[peekFocusIndex] ?? targetWidth / 2
        let placement = model.peekBubblePlacement(trayWidth: targetWidth, anchor: anchor)
        let unit = placement.tailX / NotchDataModel.peekBubbleWidth
        return UnitPoint(x: min(max(unit, 0), 1), y: 0)
    }

    var peekFocusIndex: Int {
        let count = model.providers.count
        guard count > 0 else { return 0 }
        return min(max(model.peekFocusIndex ?? 0, 0), count - 1)
    }

    // MARK: Callout bubble
    /// The bubble's rect relative to the tray's top-left, for hit-testing.
    /// Nil unless a bubble is actually on screen.
    var peekBubbleHitRect: CGRect? {
        guard model.notchState == .peek, model.usesBubblePeek, !model.providers.isEmpty else {
            return nil
        }
        let anchor = model.providerAnchors[peekFocusIndex] ?? targetWidth / 2
        let placement = model.peekBubblePlacement(trayWidth: targetWidth, anchor: anchor)
        return CGRect(
            x: placement.x,
            y: model.peekHeight + NotchDataModel.peekBubbleGap,
            width: NotchDataModel.peekBubbleWidth,
            // The visible bubble only, not the band reserved for the tallest
            // one — a click in the empty part of the band is a click on the
            // desktop, not on the island. Reported by the bubble itself, since
            // its height is now whatever its rows come to.
            height: max(0, model.peekBubbleVisibleHeight)
        )
    }

    @ViewBuilder
    var peekBubbleLayer: some View {
        let count = model.providers.count
        if count > 0 {
            let focus = peekFocusIndex
            let anchor = model.providerAnchors[focus] ?? targetWidth / 2
            let placement = model.peekBubblePlacement(trayWidth: targetWidth, anchor: anchor)
            let tailX = placement.tailX

            // The identity switch stays INSIDE a stable container. Hanging
            // .id() off the root of the chain gives the whole thing — paddings,
            // background shape, offset, and the callout's own transition — an
            // identity that changes with the focus, so every re-point and even
            // the open/close transition became a teardown and rebuild with no
            // animation at all.
            ZStack(alignment: .top) {
                peekBubbleBody(for: model.providers[focus])
                    // Swapping identity crossfades the contents while the tail
                    // slides, so the bubble reads as one object being
                    // re-pointed rather than two bubbles trading places.
                    .id(focus)
                    .transition(.opacity)
            }
                .padding(.top, NotchDataModel.peekBubbleTailHeight + 10)
                .padding(.horizontal, 14)
                .padding(.bottom, 11)
                // Width is fixed; height is whatever the rows come to. Sizing
                // the callout from a table of per-row constants was wrong three
                // times running — every mismatch landed as dead space along the
                // bottom edge, because the content is top-aligned.
                .frame(width: NotchDataModel.peekBubbleWidth, alignment: .topLeading)
                .background(
                    PeekBubbleShape(
                        tailX: tailX,
                        tailHeight: NotchDataModel.peekBubbleTailHeight,
                        tailHalfWidth: NotchDataModel.peekBubbleTailHalfWidth
                    )
                    .fill(Color.black)
                    // The tail slides only once the pointer has actually
                    // re-pointed it. Peek opens inside the island's spring
                    // transaction, and a value-scoped animation here overrides
                    // that ambient one for the tail's position without touching
                    // the callout's own entrance.
                    .animation(
                        model.peekBubbleTailAnimates
                            ? .spring(response: 0.34, dampingFraction: 0.82)
                            : nil,
                        value: tailX
                    )
                )
                // Report what was actually laid out. Everything that asks "is
                // the pointer still on the island" reads this, so it must be
                // the rendered height, not a predicted one.
                .background(
                    GeometryReader { proxy in
                        Color.clear.preference(
                            key: PeekBubbleHeightKey.self,
                            value: proxy.size.height
                        )
                    }
                )
                .offset(x: placement.x)
                // Same gate as the tail: the callout is placed, not flown in,
                // on the frame peek opens. Re-pointing afterwards slides it.
                .animation(
                    model.peekBubbleTailAnimates
                        ? .spring(response: 0.34, dampingFraction: 0.82)
                        : nil,
                    value: placement.x
                )
        }
    }

    @ViewBuilder
    func peekBubbleBody(for provider: ProviderUsage) -> some View {
        let visual = providerVisual(for: provider.provider)
        let state = quotaState(for: provider)
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 5) {
                Text(providerDisplayName(for: provider.provider))
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundColor(Color(visual.brandColor))
                    .lineLimit(1)
                Spacer(minLength: 0)
                // No pool tag here: a split-pool provider names its pool in
                // every row, and a lone "GEM" beside the provider's name reads
                // as a category rather than as "the other pool is hidden".
            }

            switch state.mode {
            case .loading:
                peekBubbleNote(L10n.tr("notch.loading.upper"), color: .white.opacity(0.5))
            case .recovering:
                peekBubbleNote(L10n.tr("notch.recovering.note"), color: Color(NSColor.systemYellow).opacity(0.9))
            case .error:
                peekBubbleNote(L10n.tr("notch.unavailable"), color: Color(NSColor.systemRed).opacity(0.85))
            case .credits:
                peekBubbleCredits(for: provider, credits: state.credits)
            case .locked, .healthy:
                peekBubbleWindows(sections: peekBubbleSections(for: provider))
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    @ViewBuilder
    func peekBubbleNote(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(color)
            .lineLimit(2)
            .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder
    func peekBubbleCredits(for provider: ProviderUsage, credits: Double?) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 5) {
                Circle()
                    .fill(RadialGradient(
                        colors: [Color(NSColor(hex: "#ffd700")), Color(NSColor(hex: "#daa520"))],
                        center: .center, startRadius: 0, endRadius: 4
                    ))
                    .frame(width: 8, height: 8)
                Text(QuotaFormatter.formatCreditBalance(
                    credits ?? 0,
                    currency: provider.windows.first(where: { $0.key == "ai_credits" })?.currency
                ))
                .font(.system(size: 17, weight: .bold))
                .monospacedDigit()
                .foregroundColor(Color(NSColor.systemGreen))
            }
            peekBubbleNote(L10n.tr("notch.on_credits"), color: .white.opacity(0.5))
        }
    }

    /// One quota window as the callout renders it.
    struct PeekBubbleRow: Identifiable {
        let id: String
        let label: String
        let snapshot: QuotaWindowSnapshot?
        let color: NSColor
        var uncapped: Bool = false

        /// Least-remaining sorts first. Used only to decide what to DROP past
        /// the row cap — the rows themselves stay in their canonical order, so
        /// a changing percentage never makes them swap places under the pointer.
        var urgency: Double {
            if uncapped { return .greatestFiniteMagnitude }
            guard let snapshot = snapshot else { return .greatestFiniteMagnitude }
            return snapshot.depleted ? -1 : snapshot.remaining
        }
    }

    /// A titled group of rows. Split-pool providers get one per pool, so the
    /// pool is named once above its windows instead of being repeated as a
    /// prefix on every row — the treatment a menu uses for sections.
    struct PeekBubbleSection: Identifiable {
        let id: String
        let title: String?
        let color: NSColor
        let rows: [PeekBubbleRow]
    }

    /// Canonical order: 5h, weekly, then any model-scoped limits — or, for a
    /// split-pool provider, one section per pool. Trimmed to the row cap by
    /// dropping the least urgent.
    func peekBubbleSections(for provider: ProviderUsage) -> [PeekBubbleSection] {
        let visual = providerVisual(for: provider.provider)
        let state = quotaState(for: provider)
        var sections: [PeekBubbleSection] = []

        let pools = quota.taggedPools(for: provider)
        if pools.count >= 2 {
            // Two independent budgets with their own limits and resets.
            for pool in pools {
                let color = pool.five.map { splitQuotaNSColor(for: $0, visual: visual) }
                    ?? visual.brandColor
                var rows: [PeekBubbleRow] = []
                if let five = pool.five {
                    rows.append(PeekBubbleRow(
                        id: "\(pool.tag).5h",
                        label: notchWindowRowTitle(five),
                        snapshot: five,
                        color: color
                    ))
                }
                if let weekly = pool.weekly {
                    rows.append(PeekBubbleRow(
                        id: "\(pool.tag).wk",
                        label: notchWindowRowTitle(weekly),
                        snapshot: weekly,
                        color: color
                    ))
                }
                guard !rows.isEmpty else { continue }
                sections.append(PeekBubbleSection(
                    id: pool.tag,
                    title: pool.tag,
                    color: color,
                    rows: rows
                ))
            }
        } else {
            var rows: [PeekBubbleRow] = []
            let uncapped = isCodexFiveHourTemporarilyUncapped(provider)
            if state.fiveHour != nil || uncapped {
                rows.append(PeekBubbleRow(
                    id: "5h",
                    label: state.fiveHour.map { notchWindowRowTitle($0) } ?? L10n.tr("notch.row.5h"),
                    snapshot: state.fiveHour,
                    color: visual.brandColor,
                    uncapped: uncapped
                ))
            }
            if let weekly = state.weekly {
                rows.append(PeekBubbleRow(
                    id: "wk",
                    label: notchWindowRowTitle(weekly),
                    snapshot: weekly,
                    color: visual.brandColor
                ))
            }
            // Model-scoped weeklies (Claude Max meters one for Fable). They are
            // real windows with their own resets, not a footnote on the weekly.
            for scoped in scopedWindows(for: provider) {
                rows.append(PeekBubbleRow(
                    id: "scoped.\(scoped.window.key ?? scoped.window.label ?? "model")",
                    label: (scoped.window.label ?? "MODEL"),
                    snapshot: scoped,
                    color: visual.brandColor
                ))
            }
            guard !rows.isEmpty else { return [] }
            sections.append(PeekBubbleSection(
                id: "windows",
                title: nil,
                color: visual.brandColor,
                rows: rows
            ))
        }

        return trimmed(sections)
    }

    /// Keeps the callout glanceable: past the row cap the least urgent rows are
    /// dropped (and a section emptied by that goes with them). Order is never
    /// touched — a changing percentage must not make rows swap places.
    private func trimmed(_ sections: [PeekBubbleSection]) -> [PeekBubbleSection] {
        let all = sections.flatMap(\.rows)
        guard all.count > NotchDataModel.peekBubbleMaxRows else { return sections }
        let keep = Set(
            all.sorted { $0.urgency < $1.urgency }
                .prefix(NotchDataModel.peekBubbleMaxRows)
                .map(\.id)
        )
        return sections.compactMap { section in
            let rows = section.rows.filter { keep.contains($0.id) }
            guard !rows.isEmpty else { return nil }
            return PeekBubbleSection(
                id: section.id,
                title: section.title,
                color: section.color,
                rows: rows
            )
        }
    }

    @ViewBuilder
    func peekBubbleWindows(sections: [PeekBubbleSection]) -> some View {
        VStack(alignment: .leading, spacing: NotchDataModel.peekBubbleRowSpacing) {
            ForEach(Array(sections.enumerated()), id: \.element.id) { index, section in
                if let title = section.title {
                    if index > 0 {
                        Rectangle()
                            .fill(Color.white.opacity(0.09))
                            .frame(height: 0.5)
                            .padding(.top, 1)
                    }
                    Text(title)
                        .font(.system(size: 9, weight: .bold))
                        .kerning(0.4)
                        .foregroundColor(Color(section.color).opacity(0.85))
                        .lineLimit(1)
                }
                ForEach(section.rows) { row in
                    peekBubbleWindowRow(row)
                }
            }
        }
    }

    /// Label and value on one line, the bar under them, the reset on its own
    /// line beneath that.
    @ViewBuilder
    func peekBubbleWindowRow(_ row: PeekBubbleRow) -> some View {
        let snapshot = row.snapshot
        let remaining = snapshot?.remaining ?? 0
        let locked = !row.uncapped && snapshot?.depleted == true
        let critical = !row.uncapped && remaining <= QuotaLevel.criticalRemaining
        let barColor = (locked || critical) ? Color(NSColor.systemRed) : Color(row.color)
        let timer = snapshot.map { timerString(for: $0) } ?? ""
        let hasTimer = !timer.isEmpty && timer != "now"

        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text(row.label)
                    .font(.system(size: 9.5, weight: .semibold))
                    .foregroundColor(.white.opacity(0.5))
                    .lineLimit(1)
                    .layoutPriority(-1)
                Spacer(minLength: 6)
                Text(row.uncapped ? "∞" : "\(snapshot?.remainingText ?? "0")%")
                    .font(.system(size: 12.5, weight: .bold))
                    .monospacedDigit()
                    .foregroundColor(
                        row.uncapped
                            ? Color(NSColor.systemGreen)
                            : (locked ? Color(NSColor.systemRed) : .white)
                    )
                    .fixedSize(horizontal: true, vertical: false)
            }

            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.16))
                    if row.uncapped {
                        Capsule().fill(Color(NSColor.systemGreen).opacity(0.5))
                    } else {
                        let fill = max(0, min(1, remaining / 100)) * proxy.size.width
                        if fill > 0 {
                            Capsule().fill(barColor).frame(width: max(2, fill))
                        }
                    }
                }
            }
            .frame(height: 3)
            .animation(.easeOut(duration: 0.35), value: remaining)

            Group {
                if row.uncapped {
                    Text(L10n.tr("notch.five_hour_uncapped"))
                        .foregroundColor(Color(NSColor.systemGreen).opacity(0.7))
                } else if hasTimer {
                    Text(String(format: L10n.tr("notch.peek.resets_in"), timer))
                        .foregroundColor(locked ? Color(NSColor.systemRed).opacity(0.8) : .white.opacity(0.5))
                } else if locked {
                    Text(L10n.tr("notch.peek.exhausted"))
                        .foregroundColor(Color(NSColor.systemRed).opacity(0.8))
                } else {
                    Text(" ")
                }
            }
            .font(.system(size: 9.5, weight: .medium))
            .monospacedDigit()
            .lineLimit(1)
        }
    }
}
