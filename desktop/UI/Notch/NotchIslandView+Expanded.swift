import AppKit
import Foundation
import SwiftUI

// NotchIslandView — expanded panel (rings, rows, solo split, token usage).
// Split out of NotchWindow.swift for navigability; same type via extension.
extension NotchIslandView {
    // Provider header (brand icon + name + plan-tier chip) shared by all three
    // expanded surfaces: the regular ring column, the solo-split panel, and the
    // token-usage page. `centered` flanks it with Spacers (solo split centers a
    // single header over both pool columns); the column surfaces leave it leading.
    @ViewBuilder
    func providerHeaderRow(for p: ProviderUsage, visual: ProviderVisual, centered: Bool = false) -> some View {
        HStack(spacing: 8) {
            if centered { Spacer(minLength: 0) }
            Circle()
                .fill(Color(visual.brandColor))
                .shadow(color: Color(visual.brandColor).opacity(0.8), radius: 3)
                .frame(width: 10, height: 10)
                .frame(width: 20, height: 20)

            Text(providerDisplayName(for: p.provider))
                .font(.system(size: 13.5, weight: .bold))
                .foregroundColor(.white)
                .lineLimit(1)
                .minimumScaleFactor(0.7)

            if let label = p.accountLabel,
               !label.trimmingCharacters(in: .whitespaces).isEmpty {
                Text(planTierLabel(label))
                    .font(.system(size: 10, weight: .semibold))
                    .tracking(0.3)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background(Color(visual.brandColor).opacity(0.16))
                    .cornerRadius(5)
                    .foregroundColor(.white.opacity(0.86))
                    .overlay(
                        RoundedRectangle(cornerRadius: 5)
                            .stroke(Color(visual.brandColor).opacity(0.3), lineWidth: 0.5)
                    )
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            if centered { Spacer(minLength: 0) }
        }
        .frame(height: 38, alignment: .center)
    }

    // MARK: - 3. Expanded View
    var expandedView: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                HStack(spacing: 8) {
                    Text(model.page == 0 ? L10n.tr("notch.page.remaining") : L10n.tr("notch.page.used_today"))
                        .font(.system(size: 12.5, weight: .bold))
                        .foregroundColor(.white.opacity(0.92))
                }
                Spacer()
                HStack(spacing: 8) {
                    HStack(spacing: 5) {
                        Circle()
                            .fill(Color(NSColor.systemGreen))
                            .frame(width: 5, height: 5)
                            .shadow(color: Color(NSColor.systemGreen).opacity(0.8), radius: 3)
                        Text(notchUpdatedText())
                            .font(.system(size: 11))
                            .foregroundColor(.white.opacity(0.5))
                    }
                    
                    Rectangle()
                        .fill(Color.white.opacity(0.13))
                        .frame(width: 0.5, height: 13)
                        .padding(.leading, 1)
                    
                    NotchHeaderSettingsButton(controller: controller)
                }
            }
            .padding(.horizontal, 16)
            // 1–2 providers: drop the header below the physical notch band so
            // the title isn't hidden behind it (mirrors expandedCardHeight's
            // inset). 3 providers route around the notch, so no push-down.
            .padding(.top, 11 + model.expandedHeaderNotchInset)
            .padding(.bottom, 7)

            // Paged views. Both pages stay in the same layout cell so the
            // container naturally sizes to the taller page, matching the
            // prototype's grid-stacked page model.
            ZStack(alignment: .top) {
                quotaRemainingView
                    .opacity(model.page == 0 ? 1 : 0)
                    .offset(x: model.page == 0 ? 0 : -16)
                    .allowsHitTesting(model.page == 0)
                tokenUsageView
                    .opacity(model.page == 1 ? 1 : 0)
                    .offset(x: model.page == 1 ? 0 : 16)
                    .allowsHitTesting(model.page == 1)
            }
            .animation(.easeInOut(duration: 0.24), value: model.page)
            .fixedSize(horizontal: false, vertical: true)
            .background(
                GeometryReader { proxy in
                    Color.clear.preference(key: ExpandedPageHeightKey.self, value: proxy.size.height)
                }
            )
            .onPreferenceChange(ExpandedPageHeightKey.self) { height in
                let rounded = ceil(height)
                guard rounded > 1 else { return }
                guard abs(model.expandedPageHeight - rounded) > 0.5 else { return }
                model.expandedPageHeight = rounded
                if model.notchState == .expanded {
                    controller?.repositionWindow()
                }
            }
            
            // Page Indicator Dots
            HStack(spacing: 4) {
                NotchPageDotButton(page: model.page, targetPage: 0, action: { model.page = 0 })
                NotchPageDotButton(page: model.page, targetPage: 1, action: { model.page = 1 })
            }
            .padding(.vertical, 2)
            
            // Footer separator
            Rectangle()
                .fill(Color.white.opacity(0.09))
                .frame(height: 0.5)
                .padding(.horizontal, 16)
                .padding(.top, 7)
            
            // Footer
            HStack(spacing: 0) {
                NotchFooterLinkButton(text: L10n.tr("menu.open_console"), systemImage: "arrow.up.right", action: {
                    controller?.collapse {
                        MainWindow.shared.show()
                    }
                })
            }
            .frame(maxWidth: .infinity)
            .frame(height: 46)
        }
    }

    func notchUpdatedText() -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let fallbackFormatter = ISO8601DateFormatter()
        fallbackFormatter.formatOptions = [.withInternetDateTime]

        let latest = model.providers.compactMap { provider -> Date? in
            guard let fetchedAt = provider.fetchedAt else { return nil }
            return formatter.date(from: fetchedAt) ?? fallbackFormatter.date(from: fetchedAt)
        }.max()

        guard let latest else {
            return L10n.tr("notch.updated_unknown")
        }

        let elapsed = max(0, Int(now.timeIntervalSince(latest)))
        if elapsed < 90 {
            return L10n.tr("notch.updated_just_now")
        }
        let minutes = elapsed / 60
        if minutes < 60 {
            return L10n.tr("notch.updated_minutes_ago", max(1, minutes))
        }
        return L10n.tr("notch.updated_hours_ago", max(1, minutes / 60))
    }
    
    @ViewBuilder
    func durationTextView(for provider: ProviderUsage, isWeekly: Bool) -> some View {
        let window = isWeekly ? getActiveWeeklyWindow(for: provider) : getActive5hWindow(for: provider)
        if let window = window, let date = resetsAtDate(from: window.window.resetsAt) {
            let diff = date.timeIntervalSince(now)
            let dur = formatDuration(diff)
            HStack(spacing: 3) {
                Text(dur.primary)
                    .fontWeight(.semibold)
                if !dur.secondary.isEmpty {
                    Text(dur.secondary)
                        .opacity(0.5)
                }
            }
            .font(.system(size: 11))
            .monospacedDigit()
        } else {
            Text(L10n.tr("notch.now"))
                .fontWeight(.semibold)
                .font(.system(size: 11))
                .monospacedDigit()
        }
    }

    @ViewBuilder
    func timerTextView(for snapshot: QuotaWindowSnapshot?) -> some View {
        if let snapshot = snapshot, snapshot.idle {
            // Unanchored window: show the static window length, not a live
            // countdown (which would tick down between polls and snap back).
            Text(windowLengthText(snapshot))
                .font(.system(size: 11, weight: .semibold))
                .monospacedDigit()
                .opacity(0.45)
        } else if let snapshot = snapshot,
           let date = resetsAtDate(from: snapshot.window.resetsAt) {
            let diff = date.timeIntervalSince(now)
            let dur = formatDuration(diff)
            HStack(spacing: 3) {
                Text(dur.primary)
                    .fontWeight(.semibold)
                if !dur.secondary.isEmpty {
                    Text(dur.secondary)
                        .fontWeight(.semibold)
                        .opacity(0.5)
                }
            }
            .font(.system(size: 11))
            .monospacedDigit()
        } else {
            Text(L10n.tr("notch.now"))
                .font(.system(size: 11, weight: .semibold))
                .monospacedDigit()
        }
    }

    // Pool extraction lives in the presenter (taggedPools) so per-pool lock
    // state and these render lists can never disagree; these are flat views
    // of its 5h / weekly sides, in GEM-then-EXT order.
    func antigravityFiveHourPools(for provider: ProviderUsage) -> [QuotaWindowSnapshot] {
        quota.taggedPools(for: provider).compactMap { $0.five }
    }

    func antigravityWeeklyPools(for provider: ProviderUsage) -> [QuotaWindowSnapshot] {
        quota.taggedPools(for: provider).compactMap { $0.weekly }
    }

    func splitQuotaName(for snapshot: QuotaWindowSnapshot) -> String {
        switch snapshot.tag {
        case "GEM": return "Gemini"
        case "EXT": return L10n.tr("notch.group.external")
        default:
            return snapshot.window.label ?? "Quota"
        }
    }

    func splitQuotaColor(for snapshot: QuotaWindowSnapshot, visual: ProviderVisual) -> Color {
        switch snapshot.tag {
        case "GEM": return Color(visual.brandColor)
        case "EXT": return Color(NSColor(hex: "#35D6C8"))
        default: return Color(visual.brandColor)
        }
    }

    func splitRingSnapshot(for provider: ProviderUsage, state: ProviderQuotaState) -> QuotaWindowSnapshot? {
        guard state.mode == .healthy else { return nil }
        let rows = antigravityFiveHourPools(for: provider)
        guard rows.count >= 2 else { return nil }
        // Bind to the tightest pool the user can still draw from — a blocked
        // pool is out of the running (it's marked on its own row instead), so
        // the ring headline always reflects usable quota.
        let blockedTags = Set(state.blockedPools.map { $0.tag })
        return rows
            .filter { !blockedTags.contains($0.tag ?? "") }
            .min(by: { $0.remaining < $1.remaining })
    }

    @ViewBuilder
    func quotaDetailBand(
        for provider: ProviderUsage,
        state: ProviderQuotaState,
        visual: ProviderVisual,
        footerVisible: Bool,
        reserveVisible: Bool
    ) -> some View {
        let weeklyPools = antigravityWeeklyPools(for: provider)
        let useAntigravityRows = provider.provider == "antigravity" && weeklyPools.count >= 2
        // Content-driven: the band sizes to its rows (+ footer when present)
        // rather than a fixed per-state reserve, so 1-line notes / no-footer
        // states don't leave dead space below the bars. The ring above stays in
        // its own fixed frame, so this never shifts ring/bar alignment.
        let scopedRows = scopedWindows(for: provider)
        VStack(spacing: 0) {
            if useAntigravityRows {
                antigravityWeeklyRows(weeklyPools, state: state, visual: visual)
            } else {
                regularQuotaRows(state: state, visual: visual)
            }

            if !scopedRows.isEmpty {
                VStack(spacing: 7) {
                    ForEach(Array(scopedRows.enumerated()), id: \.offset) { _, row in
                        scopedQuotaRow(row, visual: visual)
                    }
                }
                .padding(.top, 7)
            }

            quotaFooterBand(
                state: state,
                visual: visual,
                provider: provider,
                footerVisible: footerVisible,
                reserveVisible: reserveVisible
            )
        }
        .padding(.top, 4)
        .padding(.bottom, 6)
        // While loading the rows carry no real data (empty snapshots render
        // as "now"), so dim the whole band to read as pending, not live.
        .opacity(state.mode == .loading ? 0.35 : 1.0)
    }

    @ViewBuilder
    func regularQuotaRows(state: ProviderQuotaState, visual: ProviderVisual) -> some View {
        VStack(spacing: 6) {
            quotaInfoLine(
                title: L10n.tr("notch.row.5h"),
                snapshot: state.fiveHour,
                showPercent: false,
                color: state.fiveHour?.remaining ?? 100 <= 0 ? Color(NSColor.systemRed) : Color.white.opacity(0.48)
            )
            quotaInfoLine(
                title: L10n.tr("notch.row.weekly"),
                snapshot: state.weekly,
                showPercent: true,
                color: state.weekly?.remaining ?? 100 <= 0 ? Color(NSColor.systemRed) : Color.white.opacity(0.5)
            )
            quotaProgressBar(remaining: state.weekly?.remaining ?? 0, color: state.weekly?.remaining ?? 100 <= 0 ? Color(NSColor.systemRed) : Color(visual.brandColor))
        }
    }

    @ViewBuilder
    func antigravityWeeklyRows(_ rows: [QuotaWindowSnapshot], state: ProviderQuotaState, visual: ProviderVisual) -> some View {
        VStack(spacing: 7) {
            ForEach(Array(rows.prefix(2).enumerated()), id: \.offset) { _, row in
                antigravityWeeklyRow(
                    row,
                    lock: state.blockedPools.first(where: { $0.tag == row.tag }),
                    visual: visual
                )
            }
        }
    }

    @ViewBuilder
    func quotaInfoLine(title: String, snapshot: QuotaWindowSnapshot?, showPercent: Bool, color: Color) -> some View {
        let depleted = (snapshot?.remaining ?? 100) <= 0
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                // 9.5pt / semibold matches the design's muted row caption
                // (.semibold ≈ the mockup's 650, the nearest standard weight).
                .font(.system(size: 9.5, weight: .semibold))
                .tracking(0.8)
                // Keep the caption on one line — without this it loses the
                // width race against the percent and wraps ("WEEKL\nY").
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
            if showPercent, let snapshot = snapshot {
                Text("\(Int(snapshot.remaining))%")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(depleted ? Color(NSColor.systemRed) : .white)
                    // Keep its intrinsic single-line width so the fixed-width
                    // timer box can't squeeze it into a wrap; lets a 3-digit
                    // "100%" sit on one line and the Spacer absorb the slack.
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            }
            Spacer(minLength: 8)
            // Clock + timer kept as one cluster at the design's 4pt gap
            // (`.wk-rs{gap:4px}`) instead of the HStack's looser ~8pt default.
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Image(systemName: (snapshot?.idle ?? false) ? "moon.zzz.fill" : "clock")
                    .font(.system(size: 9))
                    .opacity((snapshot?.idle ?? false) ? 0.5 : 0.75)
                // Fixed-width, LEADING-aligned timer box. The fixed 56pt width
                // (enough for the widest value, "59m 59s" / "23h 59m") still pins
                // the clock column so rows line up; leading alignment then keeps
                // the time hard against the clock at a constant 4pt gap instead of
                // floating it to the right edge — so a short value like the idle
                // "5h" or "now" no longer leaves a big gap after the clock. The
                // trade-off is the time's right edge is ragged, not columnar.
                timerTextView(for: snapshot)
                    .frame(minWidth: 56, alignment: .leading)
            }
        }
        .foregroundColor(color)
    }

    @ViewBuilder
    func antigravityWeeklyRow(_ snapshot: QuotaWindowSnapshot, lock: PoolLockInfo?, visual: ProviderVisual) -> some View {
        let depleted = snapshot.remaining <= 0
        // `lock` marks the pool unusable (its 5h OR weekly is spent) even when
        // this weekly bar itself still has room: the row reads red and the
        // timer counts down the blocking window's reset behind a lock glyph,
        // while the bar keeps showing the real weekly level (dimmed) so the
        // remaining weekly quota isn't hidden.
        let blocked = depleted || lock != nil
        let barColor = blocked
            ? Color(NSColor.systemRed).opacity(depleted ? 1.0 : 0.55)
            : splitQuotaColor(for: snapshot, visual: visual)
        let chipColor = blocked ? Color(NSColor.systemRed) : splitQuotaColor(for: snapshot, visual: visual)
        let textColor = blocked ? Color(NSColor.systemRed) : Color.white.opacity(0.58)
        VStack(spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                HStack(spacing: 5) {
                    Text(snapshot.tag ?? splitQuotaName(for: snapshot).uppercased())
                        .font(.system(size: 10.5, weight: .bold))
                        .tracking(0.8)
                        .foregroundColor(blocked ? Color(NSColor.systemRed) : Color.white.opacity(0.54))
                    Text("WK")
                        .font(.system(size: 7.5, weight: .bold))
                        .tracking(0.4)
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(chipColor.opacity(0.16))
                        .cornerRadius(4)
                        .foregroundColor(chipColor.opacity(0.92))
                }
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                Text("\(Int(snapshot.remaining))%")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(blocked ? Color(NSColor.systemRed) : Color.white.opacity(0.9))
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                Spacer(minLength: 8)
                // Match the regular rows: clock + fixed-width LEADING-aligned
                // timer box at the design's 4pt gap. The fixed width keeps the
                // clock column aligned across the two pools; leading alignment
                // keeps the time hard against the clock instead of drifting right.
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    if let lock = lock {
                        Image(systemName: "lock.fill")
                            .font(.system(size: 9))
                            .opacity(0.9)
                        timerTextView(for: lock.snapshot)
                            .frame(minWidth: 56, alignment: .leading)
                    } else {
                        Image(systemName: snapshot.idle ? "moon.zzz.fill" : "clock")
                            .font(.system(size: 9))
                            .opacity(snapshot.idle ? 0.5 : 0.75)
                        timerTextView(for: snapshot)
                            .frame(minWidth: 56, alignment: .leading)
                    }
                }
            }
            .foregroundColor(textColor)

            quotaProgressBar(remaining: snapshot.remaining, color: barColor)
        }
    }

    /// A model-scoped sub-limit (e.g. Claude's Fable weekly cap), in the same
    /// visual language as antigravityWeeklyRow: model name + WK chip + percent
    /// + timer + its own bar. When spent, the row reads red with a lock glyph
    /// counting down the weekly reset — the card itself stays healthy because
    /// scoped windows never enter the 5h/weekly classification.
    @ViewBuilder
    func scopedQuotaRow(_ snapshot: QuotaWindowSnapshot, visual: ProviderVisual) -> some View {
        let depleted = snapshot.remaining <= 0
        let barColor = depleted ? Color(NSColor.systemRed) : Color(visual.brandColor)
        let chipColor = depleted ? Color(NSColor.systemRed) : Color.white.opacity(0.54)
        VStack(spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                HStack(spacing: 5) {
                    Text((snapshot.window.label ?? "MODEL").uppercased())
                        .font(.system(size: 10.5, weight: .bold))
                        .tracking(0.8)
                        .foregroundColor(depleted ? Color(NSColor.systemRed) : Color.white.opacity(0.54))
                    Text("WK")
                        .font(.system(size: 7.5, weight: .bold))
                        .tracking(0.4)
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(chipColor.opacity(0.16))
                        .cornerRadius(4)
                        .foregroundColor(chipColor.opacity(0.92))
                }
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                Text("\(Int(snapshot.remaining))%")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(depleted ? Color(NSColor.systemRed) : Color.white.opacity(0.9))
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
                Spacer(minLength: 8)
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Image(systemName: depleted ? "lock.fill" : (snapshot.idle ? "moon.zzz.fill" : "clock"))
                        .font(.system(size: 9))
                        .opacity(depleted ? 0.9 : (snapshot.idle ? 0.5 : 0.75))
                    timerTextView(for: snapshot)
                        .frame(minWidth: 56, alignment: .leading)
                }
            }
            .foregroundColor(depleted ? Color(NSColor.systemRed) : Color.white.opacity(0.58))

            quotaProgressBar(remaining: snapshot.remaining, color: barColor)
        }
    }

    @ViewBuilder
    func quotaProgressBar(remaining: Double, color: Color) -> some View {
        let depleted = remaining <= 0
        GeometryReader { geo in
            let fillWidth = geo.size.width * CGFloat(max(0, min(100, remaining)) / 100)
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(depleted ? Color(NSColor.systemRed).opacity(0.16) : Color.white.opacity(0.10))
                if fillWidth > 0 {
                    Capsule()
                        .fill(color)
                        .frame(width: max(2, fillWidth))
                        .shadow(color: color.opacity(0.45), radius: 3)
                }
            }
        }
        .frame(height: 3)
    }

    @ViewBuilder
    func quotaFooterBand(
        state: ProviderQuotaState,
        visual: ProviderVisual,
        provider: ProviderUsage,
        footerVisible: Bool,
        reserveVisible: Bool
    ) -> some View {
        let hasResets = provider.provider == "codex" && provider.resets != nil && (provider.resets?.count ?? 0) > 0
        let showFooter = footerVisible || hasResets

        if showFooter {
            // Content-driven: sizes to the note's natural 1 or 2 lines instead of
            // a fixed reserve, so short notes don't leave dead space below.
            VStack(spacing: 0) {
                Rectangle()
                    .fill(Color.white.opacity(0.08))
                    .frame(height: 0.5)
                    .padding(.top, provider.provider == "antigravity" ? 4 : 10)
                    .padding(.bottom, provider.provider == "antigravity" ? 8 : 10)

                // A note outranks the ambient "credits ready" line: it only
                // coexists with reserveVisible in the partial-block state,
                // where "GEM 5h spent — EXT available" is the actionable fact.
                if reserveVisible, state.note == nil, let credits = state.credits {
                    HStack(spacing: 5) {
                        CoinIcon(size: 9)
                        Text(L10n.tr("notch.credits_ready", provider.provider != "antigravity" ? String(format: "$%.2f", credits) : "\(Int(credits))"))
                    }
                    .font(.system(size: 9.5, weight: .bold))
                    .foregroundColor(Color(NSColor.systemGreen))
                } else if let note = state.note {
                    Text(provider.provider == "antigravity" ? note.replacingOccurrences(of: " — ", with: "\n") : note)
                        .font(.system(size: 10, weight: .bold))
                        .multilineTextAlignment(.center)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity)
                        .foregroundColor(state.mode == .credits ? Color(NSColor.systemGreen) : Color(NSColor.systemRed))
                } else if hasResets, let resets = provider.resets {
                    ResetChipView(resets: resets, brandColor: Color(visual.brandColor), justGranted: model.justGranted)
                }
            }
        }
    }

    // Page 1: Circular rings
    @ViewBuilder
    var quotaRemainingView: some View {
        if notchIsSoloSplit(model.providers) {
            soloSplitQuotaView
        } else {
            regularQuotaRemainingView
        }
    }

    var regularQuotaRemainingView: some View {
        HStack(alignment: .top, spacing: 0) {
            let padding: CGFloat = model.providers.count == 3 ? 8 : 14
            ForEach(0..<model.providers.count, id: \.self) { idx in
                let p = model.providers[idx]
                let visual = providerVisual(for: p.provider)
                let state = quotaState(for: p)
                let credits = state.credits
                let splitRing = splitRingSnapshot(for: p, state: state)
                let ringPercentage = splitRing?.remaining ?? (state.mode == .healthy ? (state.fiveHour?.remaining ?? state.bindingRemaining) : state.bindingRemaining)
                let ringColor = splitRing.map { splitQuotaColor(for: $0, visual: visual) } ?? Color(visual.brandColor)
                let fiveHourIdle = state.fiveHour?.idle ?? false
                let ringSubtitle = state.mode == .healthy
                    ? (splitRing.map { splitQuotaName(for: $0) } ?? (fiveHourIdle ? L10n.tr("notch.five_hour_window") : L10n.tr("notch.five_hour_left")))
                    : state.lockReason
                // Dual-pool providers get concentric per-pool 5h arcs so both
                // pools stay visible at once; a blocked pool's arc renders as
                // a red lock track while the ring headline binds to the
                // surviving pool (splitRingSnapshot skips blocked pools).
                let fivePools = antigravityFiveHourPools(for: p)
                let blockedTags = Set(state.blockedPools.map { $0.tag })
                let poolArcs: [RingPoolArc] = (state.mode == .healthy && fivePools.count >= 2)
                    ? fivePools.prefix(2).map {
                        RingPoolArc(
                            remaining: $0.remaining,
                            color: splitQuotaColor(for: $0, visual: visual),
                            blocked: blockedTags.contains($0.tag ?? "")
                        )
                    }
                    : []
                let reserveVisible = state.mode == .healthy && credits != nil
                let footerVisible = reserveVisible || state.note != nil

                VStack(spacing: 0) {
                    providerHeaderRow(for: p, visual: visual)

                    CircularProgressRing(
                        percentage: ringPercentage,
                        mode: state.mode,
                        credits: credits,
                        creditsIsDollar: p.provider != "antigravity",
                        color: ringColor,
                        glowColor: ringColor.opacity(0.5),
                        subtitle: ringSubtitle,
                        lockCountdown: timerString(for: state.lockSnapshot),
                        poolArcs: poolArcs
                    )
                    .frame(height: 128, alignment: .center)
                    
                    quotaDetailBand(
                        for: p,
                        state: state,
                        visual: visual,
                        footerVisible: footerVisible,
                        reserveVisible: reserveVisible
                    )
                }
                .frame(maxWidth: .infinity, alignment: .top)
                .padding(.horizontal, padding)
                
                if idx < model.providers.count - 1 {
                    Rectangle()
                        .fill(
                            LinearGradient(
                                gradient: Gradient(stops: [
                                    .init(color: .clear, location: 0.0),
                                    .init(color: Color.white.opacity(0.13), location: 0.18),
                                    .init(color: Color.white.opacity(0.13), location: 0.82),
                                    .init(color: .clear, location: 1.0)
                                ]),
                                startPoint: .top,
                                endPoint: .bottom
                            )
                        )
                        .frame(width: 0.5)
                        .padding(.vertical, 8)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .top)
        .padding(.horizontal, 16)
    }

    // MARK: - Solo split (single Antigravity provider, two pools)
    // One provider metering two pools (GEM + EXT) gets the full panel width, so
    // each pool earns its own ring side-by-side instead of one ring + stacked
    // rows. Each ring headlines its 5-hour remaining (matching Codex/Claude);
    // weekly stays in the row below. No "runs out first" marker — both numbers
    // are shown side-by-side so the comparison is self-evident, and a 5h-based
    // marker would flap as the rolling window resets.
    var soloSplitQuotaView: some View {
        let p = model.providers[0]
        let visual = providerVisual(for: p.provider)
        let units = soloPoolUnits()
        return VStack(spacing: 0) {
            // Provider header — once, centered over both pool columns.
            providerHeaderRow(for: p, visual: visual, centered: true)
                .padding(.horizontal, 14)

            HStack(alignment: .top, spacing: 0) {
                ForEach(Array(units.enumerated()), id: \.offset) { idx, unit in
                    soloSplitPoolColumn(unit)
                        .frame(maxWidth: .infinity, alignment: .top)
                        .padding(.horizontal, 14)

                    if idx < units.count - 1 {
                        Rectangle()
                            .fill(
                                LinearGradient(
                                    gradient: Gradient(stops: [
                                        .init(color: .clear, location: 0.0),
                                        .init(color: Color.white.opacity(0.13), location: 0.18),
                                        .init(color: Color.white.opacity(0.13), location: 0.82),
                                        .init(color: .clear, location: 1.0)
                                    ]),
                                    startPoint: .top,
                                    endPoint: .bottom
                                )
                            )
                            .frame(width: 0.5)
                            .padding(.vertical, 8)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .top)
        .padding(.horizontal, 16)
    }

    @ViewBuilder
    func soloSplitPoolColumn(_ u: SoloPoolUnit) -> some View {
        let poolColor = Color(u.color)
        let rw = u.weekly?.remaining ?? 100.0
        // Headline the 5-hour window (like Codex/Claude). A depleted pool falls
        // back to the shared reserve when available, else locks; weekly otherwise
        // stays in the row below.
        let lockReason = (u.weekZero && u.fiveZero) ? L10n.tr("notch.all_spent") : (u.weekZero ? L10n.tr("notch.weekly_cap") : L10n.tr("notch.five_hour_empty"))
        let subtitle = u.mode == .locked ? lockReason : (u.five.idle ? L10n.tr("notch.five_hour_window") : L10n.tr("notch.five_hour_left"))
        // When locked, headline the blocking window's countdown (weekly outlasts 5h).
        let lockTimer = u.weekZero ? timerString(for: u.weekly) : timerString(for: u.five)
        VStack(spacing: 0) {
            // Just the pool's series name — the full label ("Gemini" / "External")
            // is self-explanatory, so no GEM/EXT chip (that's for compressed
            // peek/glance surfaces, redundant under a full column header).
            Text(splitQuotaName(for: u.five))
                .font(.system(size: 12, weight: .bold))
                .foregroundColor(.white.opacity(0.92))
                .frame(height: 38, alignment: .center)

            CircularProgressRing(
                percentage: u.remaining,
                mode: u.mode,
                credits: u.credits,
                creditsIsDollar: false,
                color: poolColor,
                glowColor: poolColor.opacity(0.5),
                subtitle: subtitle,
                lockCountdown: lockTimer
            )
            .frame(height: 128, alignment: .center)

            VStack(spacing: 6) {
                quotaInfoLine(
                    title: L10n.tr("notch.row.5h"),
                    snapshot: u.five,
                    showPercent: false,
                    color: u.fiveZero ? Color(NSColor.systemRed) : Color.white.opacity(0.48)
                )
                quotaInfoLine(
                    title: L10n.tr("notch.row.weekly"),
                    snapshot: u.weekly,
                    showPercent: true,
                    color: u.weekZero ? Color(NSColor.systemRed) : Color.white.opacity(0.5)
                )
                quotaProgressBar(remaining: rw, color: u.weekZero ? Color(NSColor.systemRed) : poolColor)
            }
            .padding(.top, 4)
            .padding(.bottom, 6)
        }
        .frame(maxWidth: .infinity, alignment: .top)
    }

    var tokenUsageView: some View {
        HStack(alignment: .top, spacing: 0) {
            let padding: CGFloat = model.providers.count == 3 ? 8 : 14
            ForEach(0..<model.providers.count, id: \.self) { idx in
                let p = model.providers[idx]
                let visual = providerVisual(for: p.provider)
                let stats = model.todayStats[p.provider.lowercased()] ?? ProviderHistoryStats(tokens: 0, input: 0, output: 0, cacheCreation: 0, cacheRead: 0, cost: 0.0)
                let state = quotaState(for: p)
                let credits = state.credits
                let isSub = isSubscription(for: p)

                VStack(spacing: 0) {
                    providerHeaderRow(for: p, visual: visual)

                    VStack(spacing: 4) {
                        Text(formatTokenCount(stats.tokens))
                            .font(.system(size: 32, weight: .bold, design: .default))
                            .foregroundColor(Color(visual.brandColor))
                        Text(L10n.tr("notch.tokens_today.upper"))
                            .font(.system(size: 8.5, weight: .bold))
                            .tracking(1.2)
                            .foregroundColor(Color.white.opacity(0.36))
                    }
                    .frame(height: 54)
                    .padding(.top, 28)
                    
                    HStack(spacing: 6) {
                        Text(formatTokenCount(stats.input))
                            .fontWeight(.bold)
                        Text("→")
                            .foregroundColor(.white.opacity(0.3))
                        Text(formatTokenCount(stats.output))
                            .fontWeight(.bold)
                    }
                    .font(.system(size: 13, design: .default))
                    .foregroundColor(.white.opacity(0.85))
                    .padding(.top, 22)
                    
                    let denom = stats.cacheRead + stats.input + stats.cacheCreation
                    if denom > 0 {
                        let hitPct = Int(round(Double(stats.cacheRead) / Double(denom) * 100))
                        Text(L10n.tr("notch.cache_hit", hitPct))
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(.white.opacity(0.48))
                            .padding(.top, 3)
                    }
                    
                    VStack(spacing: 3) {
                        if denom == 0 {
                            Text(L10n.tr("notch.no_consumption"))
                                .font(.system(size: 10, weight: .bold))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 4)
                                .background(Color.white.opacity(0.08))
                                .cornerRadius(8)
                                .overlay(
                                    RoundedRectangle(cornerRadius: 8)
                                        .stroke(Color.white.opacity(0.12), lineWidth: 0.5)
                                )
                                .foregroundColor(.white.opacity(0.48))
                        } else if state.mode == .credits, let creds = credits {
                            HStack(spacing: 5) {
                                CoinIcon(size: 9)
                                Text("\(L10n.tr("notch.on_credits")) · \(p.provider != "antigravity" ? String(format: "$%.2f", creds) : "\(Int(creds))")")
                            }
                            .font(.system(size: 10, weight: .bold))
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(Color(NSColor.systemGreen).opacity(0.12))
                            .cornerRadius(8)
                            .overlay(
                                RoundedRectangle(cornerRadius: 8)
                                    .stroke(Color(NSColor.systemGreen).opacity(0.26), lineWidth: 0.5)
                            )
                            .foregroundColor(Color(NSColor.systemGreen))
                        } else if isSub {
                            if stats.cost > 0 {
                                Text(L10n.tr("notch.api_value", stats.cost))
                                    .font(.system(size: 10, weight: .medium))
                                    .foregroundColor(.white.opacity(0.4))
                            } else {
                                Text(L10n.tr("notch.in_plan"))
                                    .font(.system(size: 10, weight: .bold))
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 4)
                                    .background(Color.white.opacity(0.08))
                                    .cornerRadius(8)
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 8)
                                            .stroke(Color.white.opacity(0.12), lineWidth: 0.5)
                                    )
                                    .foregroundColor(.white.opacity(0.6))
                            }
                        } else {
                            Text(L10n.tr("notch.api_value_est", stats.cost))
                                .font(.system(size: 10, weight: .bold))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 4)
                                .background(Color(NSColor.systemGreen).opacity(0.12))
                                .cornerRadius(8)
                                .overlay(
                                    RoundedRectangle(cornerRadius: 8)
                                        .stroke(Color(NSColor.systemGreen).opacity(0.26), lineWidth: 0.5)
                                )
                                .foregroundColor(Color(NSColor.systemGreen))
                        }
                    }
                    .padding(.top, 24)
                }
                .frame(maxWidth: .infinity, alignment: .top)
                .padding(.horizontal, padding)
                
                if idx < model.providers.count - 1 {
                    Rectangle()
                        .fill(
                            LinearGradient(
                                gradient: Gradient(stops: [
                                    .init(color: .clear, location: 0.0),
                                    .init(color: Color.white.opacity(0.13), location: 0.18),
                                    .init(color: Color.white.opacity(0.13), location: 0.82),
                                    .init(color: .clear, location: 1.0)
                                ]),
                                startPoint: .top,
                                endPoint: .bottom
                            )
                        )
                        .frame(width: 0.5)
                        .padding(.vertical, 8)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .top)
        .padding(.horizontal, 16)
    }
    
}

struct ResetChipView: View {
    let resets: ResetCredits
    let brandColor: Color
    let justGranted: Int
    @State private var isHovering = false
    @State private var hoverWorkItem: DispatchWorkItem? = nil

    var body: some View {
        let exp = resets.expiries.sorted()
        let nextMs = exp.first ?? 0.0
        let nextDays = Int(max(0.0, round(nextMs / 86400000.0)))
        let soon = nextDays <= 7 && !exp.isEmpty
        
        VStack(spacing: 4) {
            HStack(alignment: .lastTextBaseline, spacing: 0) {
                HStack(alignment: .center, spacing: 5) {
                    Image(systemName: "arrow.counterclockwise")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundColor(soon ? Color(NSColor.systemOrange) : brandColor)
                        .offset(y: -0.5) // Visually center the icon with the cap-height of the text
                        .shadow(color: justGranted > 0 ? brandColor : Color.clear, radius: justGranted > 0 ? 3 : 0)
                    
                    Text(L10n.tr("menu.resets.upper"))
                        .font(.system(size: 9.5, weight: .semibold))
                        .tracking(0.5)
                        .foregroundColor(.white.opacity(0.5))
                }
                
                Spacer()
                
                Text(L10n.tr("menu.resets.available.compact", resets.count))
                    .font(.system(size: 9.5, weight: .semibold))
                    .tracking(0.2)
                    .foregroundColor(.white.opacity(0.55))
                    .lineLimit(1)
            }
            
            if justGranted > 0 {
                HStack(spacing: 4) {
                    Spacer()
                    Text("+\(justGranted)")
                        .font(.system(size: 8, weight: .black))
                        .foregroundColor(.black)
                        .padding(.horizontal, 4)
                        .padding(.vertical, 0.5)
                        .background(brandColor)
                        .cornerRadius(4)
                    
                    Text(L10n.tr("menu.resets.just_granted"))
                        .font(.system(size: 8, weight: .bold))
                        .foregroundColor(brandColor)
                        .textCase(.uppercase)
                }
            }
            
            if soon {
                HStack {
                    Spacer()
                    Text(L10n.tr("menu.resets.next_expires_in", nextDays))
                        .font(.system(size: 9.5, weight: .bold))
                        .foregroundColor(Color(NSColor.systemOrange))
                }
            }
        }
        .padding(.horizontal, 2)
        .onHover { hovering in
            if hovering {
                hoverWorkItem?.cancel()
                let item = DispatchWorkItem {
                    isHovering = true
                }
                hoverWorkItem = item
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: item)
            } else {
                hoverWorkItem?.cancel()
                isHovering = false
            }
        }
        .popover(isPresented: $isHovering, arrowEdge: .top) {
            ResetTooltipView(resets: resets, soon: soon)
                .preferredColorScheme(.dark)
        }
    }
}

struct ResetTooltipView: View {
    let resets: ResetCredits
    let soon: Bool
    
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L10n.tr("menu.resets.available.count", resets.count))
                .font(.system(size: 11.5, weight: .bold))
                .foregroundColor(.white)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
            
            Text(L10n.tr("menu.resets.full_reset"))
                .font(.system(size: 9, weight: .semibold))
                .foregroundColor(.white.opacity(0.44))
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
            
            Divider()
                .background(Color.white.opacity(0.12))
                .padding(.vertical, 2)
            
            let exp = resets.expiries.sorted()
            ForEach(Array(exp.enumerated()), id: \.offset) { i, ms in
                let d = Int(max(0.0, round(ms / 86400000.0)))
                let dateStr = formatDate(offsetMs: ms)
                HStack(spacing: 14) {
                    Text(i == 0 ? L10n.tr("menu.resets.next_expires") : L10n.tr("menu.resets.then"))
                        .font(.system(size: 10.5, weight: .medium))
                        .foregroundColor(.white.opacity(0.55))
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                    
                    Spacer()
                    
                    HStack(spacing: 7) {
                        Text(dateStr)
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(.white)
                            .lineLimit(1)
                            .fixedSize(horizontal: true, vertical: false)
                        
                        Text(L10n.tr("menu.resets.in_days", d))
                            .font(.system(size: 9.5, weight: .semibold))
                            .foregroundColor(i == 0 && soon ? Color(NSColor.systemOrange) : .white.opacity(0.4))
                            .lineLimit(1)
                            .fixedSize(horizontal: true, vertical: false)
                    }
                }
            }
        }
        .padding(12)
        .frame(width: 220)
        .background(Color.black.opacity(0.25)) // Semi-translucent black to preserve frosted glass blur while dimming background elements
    }
    
    private func formatDate(offsetMs: Double) -> String {
        let date = Date().addingTimeInterval(offsetMs / 1000.0)
        let formatter = DateFormatter()
        formatter.locale = Locale.current
        formatter.dateFormat = "MMM d"
        return formatter.string(from: date)
    }
}
