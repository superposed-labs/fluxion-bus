import AppKit
import SwiftUI

/// One pool's 5-hour reset, as the hover panel reports it.
struct PoolResetRow {
    let tag: String
    /// Countdown, or the window's own length when the window is unanchored —
    /// `timerString` already makes that substitution, and `idle` is what tells
    /// the reader which of the two they are looking at.
    let value: String
    let color: Color
    /// An untouched window has no reset moment to count down to; it reads as a
    /// static "5h" and is dimmed so it is not mistaken for a live timer.
    let idle: Bool
    let blocked: Bool
}

/// Reveals both pools' 5-hour reset times while the cursor is on the ring.
///
/// The dual-pool card meters two pools against two windows, which is four
/// numbers; the column is 161pt wide and the ring is 104 of them, so only the
/// binding pool's percent fits permanently. The 5h resets are the part that
/// gets dropped — the two rows under the ring both meter the week.
///
/// They are revealed rather than laid out because a permanent row could only
/// carry one pool. Showing both on demand is what makes the pair symmetric:
/// the idle pool's "no reset yet" is worth a line when asked for and worth
/// nothing when it is always on screen.
///
/// Cost: an overlay, so the card's height is the same hovered or not.
struct PoolResetHover<Content: View>: View {
    let rows: [PoolResetRow]
    @ViewBuilder let content: () -> Content

    @State private var hovering = false

    var body: some View {
        content()
            // Pushed clear of the ring rather than laid over it: the percent
            // and pool name inside the arc are what the panel is elaborating,
            // so covering them answers a question while hiding what prompted
            // it. Below the ring it lands on the weekly rows instead, which
            // stay readable the moment the cursor leaves.
            .overlay(alignment: .bottom) {
                if hovering, !rows.isEmpty {
                    panel
                        .offset(y: 30)
                        .transition(.opacity)
                        .allowsHitTesting(false)
                }
            }
            .contentShape(Rectangle())
            .onHover { hovering = $0 }
            .animation(.easeOut(duration: 0.12), value: hovering)
            // The ring is an earlier sibling than the detail band, so without
            // this the panel is painted first and the weekly rows show through
            // it. Raising the whole ring is harmless: nothing else overlaps it.
            .zIndex(1)
    }

    private var panel: some View {
        VStack(spacing: 3) {
            // One caption for the panel rather than a unit on every row: the
            // rows are already tight, and repeating "5H" twice cost the
            // countdown the width it needed to render in full.
            HStack(spacing: 0) {
                Text("5H")
                    .font(.system(size: 7.5, weight: .bold))
                    .tracking(0.5)
                    .foregroundColor(.white.opacity(0.34))
                Spacer(minLength: 0)
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                HStack(spacing: 6) {
                    Text(row.tag)
                        .font(.system(size: 8.5, weight: .bold))
                        .tracking(0.4)
                        .foregroundColor(row.color.opacity(0.85))
                    Spacer(minLength: 6)
                    if row.blocked {
                        Image(systemName: "lock.fill")
                            .font(.system(size: 7.5))
                            .foregroundColor(Color(NSColor.systemRed).opacity(0.9))
                    } else if row.idle {
                        Image(systemName: "moon.zzz.fill")
                            .font(.system(size: 7.5))
                            .foregroundColor(.white.opacity(0.35))
                    } else {
                        Image(systemName: "clock")
                            .font(.system(size: 7.5))
                            .foregroundColor(.white.opacity(0.5))
                    }
                    Text(row.value)
                        .font(.system(size: 9.5, weight: .bold))
                        .monospacedDigit()
                        .foregroundColor(
                            row.blocked
                                ? Color(NSColor.systemRed)
                                : .white.opacity(row.idle ? 0.42 : 0.92)
                        )
                }
                .lineLimit(1)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .frame(width: 128)
        .background(
            RoundedRectangle(cornerRadius: 7)
                .fill(Color.black.opacity(0.92))
                .overlay(
                    RoundedRectangle(cornerRadius: 7)
                        .stroke(Color.white.opacity(0.14), lineWidth: 0.5)
                )
                .shadow(color: .black.opacity(0.55), radius: 8, y: 2)
        )
    }
}
