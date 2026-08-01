import AppKit
import SwiftUI

/// One day's column in the compact seven-day trend, resolved by the caller so
/// the bar row needs neither the locale nor the token formatter.
struct CompactTrendDay {
    let value: Int
    /// Terse label under the bar — a single character in narrow columns.
    let axis: String
    /// Spelled-out day for the hover chip.
    let full: String
    /// Preformatted token total for the hover chip.
    let amount: String
}

/// The seven bars of the compact trend, split out of `compactUsageTrend` so
/// they can own hover state: a `@ViewBuilder` method on the view extension has
/// nowhere to keep `@State`.
///
/// The hovered day's total arrives as an overlay rather than a row of its own.
/// Overlays don't participate in layout, so the module's height is identical
/// hovered or not — the trend block must never resize the island under the
/// cursor (see the placeholder note at its call site). The chip stays centered
/// over the row instead of tracking the hovered bar: pinned to the bar, the
/// first and last day would push it into the neighbouring provider's column.
/// The lit bar carries the association, and the chip names its day regardless.
struct CompactTrendBars: View {
    let days: [CompactTrendDay]
    let brandColor: Color
    let narrow: Bool
    let placeholder: Bool

    @State private var hovered: Int?

    var body: some View {
        let peak = max(1, days.map(\.value).max() ?? 1)
        HStack(alignment: .bottom, spacing: narrow ? 2 : 4) {
            ForEach(Array(days.enumerated()), id: \.offset) { idx, day in
                let isToday = idx == days.count - 1
                let isHovered = hovered == idx
                VStack(spacing: 4) {
                    ZStack(alignment: .bottom) {
                        Rectangle()
                            .fill(Color.white.opacity(0.08))
                            .frame(height: 0.5)
                        TopRoundedBar(radius: 2)
                            .fill(barFill(isToday: isToday, isHovered: isHovered))
                            .frame(
                                width: narrow ? 8 : 11,
                                height: placeholder
                                    ? 5
                                    : (day.value == 0
                                        ? 1
                                        : max(isToday ? 5 : 3, 25 * CGFloat(day.value) / CGFloat(peak)))
                            )
                            .shadow(
                                color: !placeholder && isToday ? brandColor.opacity(0.45) : .clear,
                                radius: 3
                            )
                    }
                    .frame(height: 25, alignment: .bottom)

                    Text(day.axis)
                        .font(.system(size: narrow ? 9 : 10, weight: isToday ? .bold : .medium))
                        .foregroundColor(axisColor(isToday: isToday, isHovered: isHovered))
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                }
                .frame(maxWidth: .infinity)
                // A narrow bar is 8pt wide; the whole column — bar, baseline
                // and weekday label — is the hover target.
                .contentShape(Rectangle())
                .onHover { hovering in
                    guard !placeholder else { return }
                    if hovering {
                        hovered = idx
                    } else if hovered == idx {
                        hovered = nil
                    }
                }
            }
        }
        .overlay(alignment: .top) {
            if let idx = hovered, days.indices.contains(idx) {
                chip(for: days[idx])
                    // Lifted into the gap above the trend header rather than
                    // onto it: the chip is opaque, and covering the header
                    // would take away the week total just as the reader gains
                    // a day to compare against it. Measured up from the top of
                    // the bar row — 6 (stack spacing) + ~13 (header) puts the
                    // chip's foot at the header's top edge, and the ~23pt of
                    // padding above the header (see the module's call site)
                    // swallows the rest, so only the hairline divider is hidden.
                    //
                    // Drawing outside this module is safe: nothing clips until
                    // the card silhouette, and the trend block is a later
                    // sibling in the column stack, so it composites on top.
                    .offset(y: -40)
                    .allowsHitTesting(false)
                    .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.12), value: hovered)
    }

    private func barFill(isToday: Bool, isHovered: Bool) -> Color {
        if placeholder { return Color.white.opacity(0.08) }
        if isToday { return brandColor }
        return Color.white.opacity(isHovered ? 0.42 : 0.22)
    }

    private func axisColor(isToday: Bool, isHovered: Bool) -> Color {
        if placeholder { return .white.opacity(0.18) }
        if isToday { return brandColor.opacity(0.9) }
        return .white.opacity(isHovered ? 0.7 : 0.45)
    }

    @ViewBuilder
    private func chip(for day: CompactTrendDay) -> some View {
        HStack(spacing: 4) {
            Text(day.full)
                .foregroundColor(.white.opacity(0.6))
            Text(day.amount)
                .monospacedDigit()
                .foregroundColor(.white.opacity(0.95))
        }
        .font(.system(size: narrow ? 9.5 : 10.5, weight: .bold))
        .lineLimit(1)
        // The overlay proposes the row's width; without this the chip would
        // stretch to fill it instead of hugging its text.
        .fixedSize()
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.black.opacity(0.82))
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color.white.opacity(0.12), lineWidth: 0.5)
                )
        )
    }
}

