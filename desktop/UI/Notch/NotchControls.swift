import AppKit
import Foundation
import SwiftUI

// Reusable leaf views and button styles for the notch UI, split out of
// NotchWindow.swift. No state of their own beyond hover/pulse animation.

// MARK: - UI Helpers

/// Hit-test shape covering only the visible island card — horizontally centered
/// and top-anchored within the (larger) window. The window reserves a
/// transparent overshoot halo around the card so the peek Q-bounce isn't
/// clipped; restricting the tap to this shape (using the live, animating
/// `targetWidth`/`targetHeight`) keeps hover/clicks on the visible card and off
/// the invisible halo, without the AppKit hit-test coordinate guesswork.
struct CardHitShape: Shape {
    var width: CGFloat
    var height: CGFloat

    func path(in rect: CGRect) -> Path {
        let x = rect.minX + max(0, (rect.width - width) / 2)
        return Path(CGRect(x: x, y: rect.minY, width: min(width, rect.width), height: min(height, rect.height)))
    }
}

struct BottomRoundedRectangle: Shape {
    var cornerRadius: CGFloat
    
    var animatableData: CGFloat {
        get { cornerRadius }
        set { cornerRadius = newValue }
    }
    
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let r = min(cornerRadius, rect.height)
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - r))
        path.addQuadCurve(to: CGPoint(x: rect.maxX - r, y: rect.maxY),
                          control: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX + r, y: rect.maxY))
        path.addQuadCurve(to: CGPoint(x: rect.minX, y: rect.maxY - r),
                          control: CGPoint(x: rect.minX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}

struct BottomRoundedBorder: Shape {
    var cornerRadius: CGFloat
    
    var animatableData: CGFloat {
        get { cornerRadius }
        set { cornerRadius = newValue }
    }
    
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let r = min(cornerRadius, rect.height)
        path.move(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - r))
        path.addQuadCurve(to: CGPoint(x: rect.maxX - r, y: rect.maxY),
                          control: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX + r, y: rect.maxY))
        path.addQuadCurve(to: CGPoint(x: rect.minX, y: rect.maxY - r),
                          control: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY))
        return path
    }
}

struct VisualEffectView: NSViewRepresentable {
    var material: NSVisualEffectView.Material
    var blendingMode: NSVisualEffectView.BlendingMode
    
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = material
        view.blendingMode = blendingMode
        view.state = .active
        return view
    }
    
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {
        nsView.material = material
        nsView.blendingMode = blendingMode
    }
}

struct CoinIcon: View {
    var size: CGFloat = 10
    var body: some View {
        ZStack {
            Circle()
                .fill(LinearGradient(colors: [Color(NSColor(hex: "#4ad07e")), Color(NSColor(hex: "#27a35c"))], startPoint: .topLeading, endPoint: .bottomTrailing))
                .frame(width: size, height: size)
                .shadow(color: Color(NSColor.systemGreen).opacity(0.4), radius: 2)
            Circle()
                .stroke(Color.white.opacity(0.5), lineWidth: 0.7)
                .frame(width: size / 2, height: size / 2)
        }
    }
}

// A day bar for the usage page's week chart: rounded on top only, so the
// bar sits flush on the chart's baseline instead of lifting off it with a
// bottom radius. (UnevenRoundedRectangle needs macOS 13; this app targets 12.)
struct TopRoundedBar: Shape {
    var radius: CGFloat = 2

    func path(in rect: CGRect) -> Path {
        let r = min(radius, min(rect.width, rect.height) / 2)
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.minY + r))
        p.addQuadCurve(
            to: CGPoint(x: rect.minX + r, y: rect.minY),
            control: CGPoint(x: rect.minX, y: rect.minY)
        )
        p.addLine(to: CGPoint(x: rect.maxX - r, y: rect.minY))
        p.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.minY + r),
            control: CGPoint(x: rect.maxX, y: rect.minY)
        )
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}

// One pool's 5-hour arc in a dual-pool ring (Antigravity GEM/EXT). A blocked
// pool renders as a muted red track with a lock badge instead of a fill.
struct RingPoolArc {
    let remaining: Double
    let color: Color
    let blocked: Bool
}

// The pool a dual ring's headline is NOT bound to, rendered as a faint line
// under the subtitle. Without it that pool's 5-hour number appears nowhere on
// the card — the detail band below the ring meters the weekly windows, so the
// arc's length was the only clue.
struct RingSecondaryPool {
    let tag: String
    /// Remaining percent while usable, or the unlock countdown when blocked.
    let value: String
    let color: Color
    let blocked: Bool
}

// MARK: - SwiftUI Progress Ring
// Internal (not file-private) so NotchIslandView+Expanded can build the rings.
struct CircularProgressRing: View {
    let percentage: Double
    let mode: ProviderDisplayMode
    let credits: Double?
    let creditsIsDollar: Bool
    let color: Color
    let glowColor: Color
    let subtitle: String
    let lockCountdown: String?
    // Two entries (outer, inner) switch the healthy ring to concentric
    // per-pool arcs; empty keeps the classic single ring.
    var poolArcs: [RingPoolArc] = []
    // Dual rings only: the other pool's 5-hour standing, shown beneath the
    // subtitle. Nil keeps the two-line center.
    var secondaryPool: RingSecondaryPool? = nil

    @State private var pulseOpacity = 0.9

    private var isDual: Bool { mode == .healthy && poolArcs.count >= 2 }

    var body: some View {
        ZStack {
            if isDual {
                dualPoolArcs
            } else {
                Circle()
                    .stroke(Color.white.opacity(0.10), lineWidth: 8)
                // Loading keeps just the neutral track — no colored arc, and
                // especially not the red full-circle treatment, which would
                // make a provider that merely hasn't been fetched yet look broken.
                if mode != .loading {
                    Circle()
                        .trim(from: 0.0, to: CGFloat(mode == .healthy ? percentage / 100.0 : 1.0))
                        .stroke(
                            (mode == .error || mode == .locked) ? Color(NSColor.systemRed)
                                : mode == .recovering ? Color(NSColor.systemYellow)
                                : (mode == .credits ? Color(NSColor.systemGreen) : color),
                            style: StrokeStyle(lineWidth: 8, lineCap: .round)
                        )
                        .rotationEffect(Angle(degrees: -90))
                        .shadow(
                            color: (mode == .error || mode == .locked) ? Color(NSColor.systemRed).opacity(0.35) :
                                mode == .recovering ? Color(NSColor.systemYellow).opacity(0.5) :
                                (mode == .credits ? Color(NSColor.systemGreen).opacity(0.7) : glowColor),
                            radius: 4
                        )
                        .opacity(mode == .healthy ? 1.0 : pulseOpacity)
                }
            }

            VStack(spacing: 2) {
                if mode == .loading {
                    VStack(spacing: 9) {
                        ProgressView()
                            .controlSize(.small)
                            // Force the dark rendering so the spinner stays
                            // white on the notch card even under a light
                            // system appearance.
                            .environment(\.colorScheme, .dark)
                        Text(subtitle.uppercased())
                            .font(.system(size: 9, weight: .bold))
                            .tracking(0.8)
                            .foregroundColor(Color.white.opacity(0.42))
                    }
                } else if mode == .error {
                    VStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.system(size: 15))
                            .foregroundColor(Color(NSColor.systemRed))
                        Text(L10n.tr("notch.error.upper"))
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(Color(NSColor.systemRed))
                            .tracking(0.8)
                    }
                } else if mode == .credits, let creds = credits {
                    let balanceText = QuotaFormatter.formatCreditBalance(
                        creds,
                        currency: creditsIsDollar ? "USD" : nil
                    )
                    Text(balanceText)
                        .font(.system(size: balanceText.count > 6 ? 17 : 20, weight: .bold, design: .default))
                        .foregroundColor(.white)
                        .tracking(-0.5)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .padding(.horizontal, 8)
                    HStack(spacing: 3) {
                        CoinIcon(size: 7)
                        Text(L10n.tr("notch.on_credits"))
                            .font(.system(size: 9, weight: .bold))
                            .foregroundColor(Color(NSColor.systemGreen))
                            .tracking(0.8)
                    }
                    .padding(.top, 3)
                } else if mode == .locked {
                    VStack(spacing: 4) {
                        Image(systemName: "lock.fill")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(Color(NSColor.systemRed))
                        lockCountdownView
                        Text(subtitle.uppercased())
                            .font(.system(size: 9, weight: .bold))
                            .foregroundColor(Color(NSColor.systemRed))
                            .tracking(0.8)
                    }
                } else if mode == .recovering {
                    // Predicted reset elapsed; confirming with the provider. A
                    // neutral amber "confirming…" replaces the red lock + frozen
                    // countdown until a fresh poll flips the card back to healthy.
                    VStack(spacing: 6) {
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .font(.system(size: 13, weight: .bold))
                            .foregroundColor(Color(NSColor.systemYellow))
                        Text(subtitle.uppercased())
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(Color(NSColor.systemYellow))
                            .tracking(0.8)
                    }
                } else {
                    // The inner pool arc shrinks the center hole, so the dual
                    // ring steps the headline down a notch to keep clear of it.
                    // A dual ring also tints the headline with the bound pool's
                    // color: two arcs share one number, and the tint is what
                    // says which arc it reads. Single rings keep the number
                    // white — there is only one arc, so white costs no clarity
                    // and buys contrast.
                    HStack(alignment: .firstTextBaseline, spacing: 0.5) {
                        Text("\(Int(percentage))")
                            .font(.system(size: isDual ? 22 : 27, weight: .bold, design: .default))
                            .tracking(-0.6)
                        Text("%")
                            .font(.system(size: isDual ? 11 : 13, weight: .semibold))
                            .opacity(0.55)
                    }
                    .foregroundColor(isDual ? color : .white)
                    Text(subtitle.uppercased())
                        .font(.system(size: 9, weight: .bold))
                        .tracking(0.8)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .frame(maxWidth: isDual ? 56 : 72)
                        .foregroundColor(isDual ? color.opacity(0.75) : Color.white.opacity(0.42))
                        .padding(.top, isDual && secondaryPool != nil ? 2 : 3)
                    if isDual, let other = secondaryPool {
                        // Sits ~24pt below center, where the inner arc leaves
                        // only ~59pt of clear width — hence the demoted tag
                        // size, near-zero tracking, and space-stripped
                        // countdown. The scale factor is the backstop for a
                        // longer countdown ("1d 2h 14m") rather than the plan.
                        HStack(alignment: .firstTextBaseline, spacing: 3) {
                            Text(other.tag)
                                .font(.system(size: 8, weight: .bold))
                                .tracking(0.2)
                            Text(other.value.replacingOccurrences(of: " ", with: ""))
                                .font(.system(size: 9, weight: .bold))
                                .monospacedDigit()
                        }
                        .foregroundColor(
                            other.blocked
                                ? Color(NSColor.systemRed).opacity(0.8)
                                : other.color.opacity(0.62)
                        )
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .frame(maxWidth: 56)
                    }
                }
            }
        }
        .frame(width: 104, height: 104)
        .onAppear {
            if mode != .healthy && mode != .loading {
                startPulseAnimation()
            }
        }
        .onChange(of: mode) { _ in
            if mode != .healthy && mode != .loading {
                startPulseAnimation()
            } else {
                pulseOpacity = 1.0
            }
        }
    }
    
    // Concentric per-pool arcs: outer = first pool (GEM), inner = second
    // (EXT), matching the weekly rows' top-to-bottom order. A blocked pool
    // keeps a muted red track with a lock badge at 12 o'clock while the live
    // pool keeps its color — one glance tells which side is still usable.
    @ViewBuilder
    private var dualPoolArcs: some View {
        ForEach(Array(poolArcs.prefix(2).enumerated()), id: \.offset) { idx, arc in
            let diameter: CGFloat = idx == 0 ? 104 : 82
            let stroke: CGFloat = idx == 0 ? 8 : 6
            ZStack {
                if arc.blocked {
                    Circle()
                        .stroke(Color(NSColor.systemRed).opacity(0.30), lineWidth: stroke)
                    ZStack {
                        Circle()
                            .fill(Color(NSColor.systemRed))
                            .frame(width: 13, height: 13)
                        Image(systemName: "lock.fill")
                            .font(.system(size: 7, weight: .bold))
                            .foregroundColor(.white)
                    }
                    .offset(y: -diameter / 2)
                } else {
                    Circle()
                        .stroke(Color.white.opacity(0.10), lineWidth: stroke)
                    Circle()
                        .trim(from: 0.0, to: CGFloat(max(0.0, min(100.0, arc.remaining)) / 100.0))
                        .stroke(arc.color, style: StrokeStyle(lineWidth: stroke, lineCap: .round))
                        .rotationEffect(Angle(degrees: -90))
                        .shadow(color: arc.color.opacity(0.5), radius: 4)
                }
            }
            .frame(width: diameter, height: diameter)
        }
    }

    private func startPulseAnimation() {
        pulseOpacity = 0.9
        withAnimation(Animation.easeInOut(duration: 1.3).repeatForever(autoreverses: true)) {
            pulseOpacity = 0.46
        }
    }

    @ViewBuilder
    private var lockCountdownView: some View {
        let raw = lockCountdown ?? "now"
        let display = raw == "now" ? L10n.tr("notch.now") : raw
        let parts = display.split(separator: " ", maxSplits: 1).map(String.init)
        HStack(alignment: .firstTextBaseline, spacing: 2) {
            Text(parts.first ?? L10n.tr("notch.now"))
                .font(.system(size: 20, weight: .bold, design: .default))
                .tracking(-0.4)
            if parts.count > 1 {
                Text(parts[1])
                    .font(.system(size: 12, weight: .semibold, design: .default))
                    .opacity(0.5)
            }
        }
        .foregroundColor(.white)
        .monospacedDigit()
    }
}

// MARK: - Premium Interactive Notch Buttons

struct NotchHeaderSettingsButtonStyle: ButtonStyle {
    let isHovered: Bool
    
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundColor(.white.opacity(configuration.isPressed ? 0.7 : (isHovered ? 0.95 : 0.55)))
            .frame(width: 26, height: 26)
            .background(
                Circle()
                    .fill(Color.white.opacity(isHovered ? 0.12 : 0.0))
            )
            .scaleEffect(configuration.isPressed ? 0.92 : (isHovered ? 1.05 : 1.0))
            .contentShape(Circle())
    }
}

struct NotchHeaderSettingsButton: View {
    let controller: NotchWindowController?
    @State private var isHovered = false
    
    var body: some View {
        Button(action: {
            controller?.collapse {
                PreferencesWindow.shared.show()
            }
        }) {
            Image(systemName: "slider.horizontal.3")
                .font(.system(size: 13))
        }
        .buttonStyle(NotchHeaderSettingsButtonStyle(isHovered: isHovered))
        .onHover { hovering in
            withAnimation(.easeOut(duration: 0.15)) {
                isHovered = hovering
            }
        }
    }
}

struct NotchUpdateDotButton: View {
    let version: String
    @State private var isHovered = false

    var body: some View {
        Button {
            (NSApp.delegate as? AppDelegate)?.presentAvailableUpdate()
        } label: {
            Circle()
                .fill(Color(red: 0.58, green: 0.38, blue: 0.95))
                .frame(width: 6, height: 6)
                .shadow(
                    color: Color(red: 0.58, green: 0.38, blue: 0.95)
                        .opacity(isHovered ? 0.9 : 0.55),
                    radius: isHovered ? 4 : 2
                )
                .frame(width: 24, height: 24)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .help(L10n.tr("update.available.hint", version))
        .onHover { hovering in
            withAnimation(.easeOut(duration: 0.15)) {
                isHovered = hovering
            }
        }
    }
}

struct NotchPageDotButtonStyle: ButtonStyle {
    let isSelected: Bool
    let isHovered: Bool
    
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.85 : 1.0)
    }
}

struct NotchPageDotButton: View {
    let page: Int
    let targetPage: Int
    let action: () -> Void
    @State private var isHovered = false
    
    var body: some View {
        Button(action: action) {
            ZStack {
                Capsule()
                    .fill(Color.white.opacity(page == targetPage ? 0.9 : (isHovered ? 0.55 : 0.26)))
                    .frame(width: page == targetPage ? 17 : 6, height: 6)
            }
            .frame(width: 24, height: 22)
            .contentShape(Rectangle())
        }
        .buttonStyle(NotchPageDotButtonStyle(isSelected: page == targetPage, isHovered: isHovered))
        .onHover { hovering in
            withAnimation(.easeOut(duration: 0.15)) {
                isHovered = hovering
            }
        }
    }
}

struct NotchFooterLinkButtonStyle: ButtonStyle {
    let isHovered: Bool
    
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundColor(.white.opacity(configuration.isPressed ? 0.42 : (isHovered ? 0.92 : 0.72)))
            .scaleEffect(configuration.isPressed ? 0.985 : 1.0)
    }
}

struct NotchFooterLinkButton: View {
    let text: String
    let systemImage: String
    let action: () -> Void
    @State private var isHovered = false
    
    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                Text(text)
                Image(systemName: systemImage)
                    .font(.system(size: 10, weight: .semibold))
                    .offset(x: isHovered ? 1 : 0, y: isHovered ? -1 : 0)
            }
            .font(.system(size: 12, weight: .semibold))
            .contentShape(Rectangle())
        }
        .buttonStyle(NotchFooterLinkButtonStyle(isHovered: isHovered))
        .onHover { hovering in
            withAnimation(.easeInOut(duration: 0.18)) {
                isHovered = hovering
            }
        }
    }
}
