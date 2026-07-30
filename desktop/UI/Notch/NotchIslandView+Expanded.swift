import AppKit
import Foundation
import SwiftUI

// NotchIslandView — expanded panel (rings, rows, solo split, token usage).
// Split out of NotchWindow.swift for navigability; same type via extension.

/// Every input the two expanded pages read while rendering.
///
/// Flipping the pager mutates `model.page`, which no page's *content* reads —
/// only the shell around them does (title, opacity, offset, dots). But because
/// the pages are computed properties on one big view, that write used to
/// re-evaluate both hierarchies for all providers. Profiling attributed ~77% of
/// the notch's main-thread time during paging to exactly that rebuild.
///
/// Gating each page on this value lets SwiftUI reuse the hierarchy it already
/// built whenever these inputs are untouched, which is the case throughout a
/// flip. Correctness depends on this covering everything the pages read — and
/// `now` is a member that ticks once a second, so even a missed dependency
/// resolves itself within a second instead of sticking indefinitely.
struct ExpandedPageInputs: Equatable {
    let providers: [ProviderUsage]
    let todayStats: [String: ProviderHistoryStats]
    let dailyTokens: [String: [Int]]
    let peakHours: [String: Int]
    let historyLoaded: Bool
    let expandedStyle: String
    let justGranted: Int
    let now: Date
    /// Date and number text is formatted in the in-app language, which is
    /// switchable at runtime; without it a switch would not show until the
    /// next tick.
    let language: String
}

/// Wraps a page so SwiftUI can skip re-evaluating it when `inputs` is unchanged.
///
/// `content` captures the parent view, so it may only be consulted when the
/// inputs actually differ — which is precisely what `==` decides here. Wrapping
/// does not change the identity of anything inside, so `@State` held by the page
/// (ring pulses, placeholder shimmer) survives a flip rather than restarting.
struct EquatablePage<Content: View>: View, Equatable {
    let inputs: ExpandedPageInputs
    @ViewBuilder var content: () -> Content

    var body: some View { content() }

    static func == (lhs: EquatablePage<Content>, rhs: EquatablePage<Content>) -> Bool {
        lhs.inputs == rhs.inputs
    }
}

extension NotchIslandView {
    var expandedPageInputs: ExpandedPageInputs {
        ExpandedPageInputs(
            providers: model.providers,
            todayStats: model.todayStats,
            dailyTokens: model.dailyTokens,
            peakHours: model.peakHours,
            historyLoaded: model.historyLoaded,
            expandedStyle: model.expandedStyle,
            justGranted: model.justGranted,
            now: now,
            language: L10n.resolvedAppLanguage
        )
    }

    // Provider header (brand icon + name + plan-tier chip) shared by all three
    // expanded surfaces: the regular ring column, the solo-split panel, and the
    // token-usage page. `centered` flanks it with Spacers (solo split centers a
    // single header over both pool columns); the column surfaces leave it leading.
    // `subtle` lowers the identity one visual level in detailed solo dashboards,
    // where it provides context but must not compete with the primary metrics.
    @ViewBuilder
    func providerHeaderRow(
        for p: ProviderUsage,
        visual: ProviderVisual,
        centered: Bool = false,
        subtle: Bool = false
    ) -> some View {
        HStack(spacing: subtle ? 6 : 8) {
            if centered { Spacer(minLength: 0) }
            Circle()
                .fill(Color(visual.brandColor))
                .shadow(color: Color(visual.brandColor).opacity(subtle ? 0.55 : 0.8), radius: subtle ? 2 : 3)
                .frame(width: subtle ? 6 : 10, height: subtle ? 6 : 10)
                .frame(width: subtle ? 14 : 20, height: subtle ? 14 : 20)

            Text(providerDisplayName(for: p.provider))
                .font(.system(size: subtle ? 11.5 : 13.5, weight: subtle ? .semibold : .bold))
                .foregroundColor(.white.opacity(subtle ? 0.82 : 1))
                .lineLimit(1)
                .minimumScaleFactor(0.7)

            if let label = p.accountLabel,
               !label.trimmingCharacters(in: .whitespaces).isEmpty {
                Text(planTierLabel(label))
                    .font(.system(size: subtle ? 8.5 : 10, weight: .semibold))
                    .tracking(subtle ? 0.2 : 0.3)
                    .padding(.horizontal, subtle ? 5 : 7)
                    .padding(.vertical, subtle ? 1 : 2)
                    .background(Color(visual.brandColor).opacity(subtle ? 0.11 : 0.16))
                    .cornerRadius(subtle ? 4 : 5)
                    .foregroundColor(.white.opacity(subtle ? 0.68 : 0.86))
                    .overlay(
                        RoundedRectangle(cornerRadius: subtle ? 4 : 5)
                            .stroke(Color(visual.brandColor).opacity(subtle ? 0.2 : 0.3), lineWidth: 0.5)
                    )
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            if centered { Spacer(minLength: 0) }
        }
        .frame(height: subtle ? 28 : 38, alignment: .center)
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

                    if let version = model.pendingUpdateVersion {
                        NotchUpdateDotButton(version: version)
                    }

                    NotchHeaderSettingsButton(controller: controller)
                }
            }
            .padding(.horizontal, 16)
            // 1–2 providers: drop the header below the physical notch band so
            // the title isn't hidden behind it (mirrors expandedCardHeight's
            // inset). 3 providers route around the notch, so no push-down.
            .padding(.top, 11 + model.expandedHeaderNotchInset)
            .padding(.bottom, 7)

            // Both pages share one measured cell. Async usage modules reserve
            // their completed geometry behind a loading layer, so history can
            // fade in without changing the natural page height. Real quota
            // structure (lock notes, credits, reset rows) remains free to grow.
            //
            // Both pages also stay mounted; the gate only decides whether their
            // contents are rebuilt (see ExpandedPageInputs). The animated
            // modifiers sit outside it, so a flip animates the hierarchy that is
            // already on screen instead of building a new one.
            let pageInputs = expandedPageInputs
            ZStack(alignment: .top) {
                EquatablePage(inputs: pageInputs) { quotaRemainingView }
                    .equatable()
                    .background(pageHeightReader(page: 0))
                    .opacity(model.page == 0 ? 1 : 0)
                    .offset(x: model.page == 0 ? 0 : -16)
                    .allowsHitTesting(model.page == 0)
                EquatablePage(inputs: pageInputs) { tokenUsageView }
                    .equatable()
                    .background(pageHeightReader(page: 1))
                    .opacity(model.page == 1 ? 1 : 0)
                    .offset(x: model.page == 1 ? 0 : 16)
                    .allowsHitTesting(model.page == 1)
            }
            .fixedSize(horizontal: false, vertical: true)
            .frame(height: resolvedExpandedPageHeight(), alignment: .top)
            .animation(.easeInOut(duration: 0.24), value: model.page)
            .onPreferenceChange(ExpandedPageHeightsKey.self) { heights in
                for (idx, height) in heights where ceil(height) > 1 {
                    model.pageHeights[idx] = ceil(height)
                }
                syncExpandedHeight()
            }
            .onChange(of: model.page) { _ in
                syncExpandedHeight()
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
                        // The notch is a quota surface, so its console link
                        // lands on the usage page, not the default task view.
                        MainWindow.shared.show(view: "stats")
                    }
                })
            }
            .frame(maxWidth: .infinity)
            .frame(height: 46)
        }
    }

    func pageHeightReader(page: Int) -> some View {
        GeometryReader { proxy in
            Color.clear.preference(key: ExpandedPageHeightsKey.self, value: [page: proxy.size.height])
        }
    }

    // Keep the window-sizing height in step with the current page's measured
    // height (both when a page re-measures and when the user switches pages).
    // Both pages use the taller measured height. Even carefully balanced
    // detailed pages can differ by a fractional point after localization;
    // locking their shared frame prevents a visible bump during page flips.
    func resolvedExpandedPageHeight() -> CGFloat? {
        if let remaining = model.pageHeights[0], remaining > 1,
           let used = model.pageHeights[1], used > 1 {
            return max(remaining, used)
        }
        return model.pageHeights[model.page]
    }

    func syncExpandedHeight() {
        guard let height = resolvedExpandedPageHeight(), height > 1 else { return }
        guard abs(model.expandedPageHeight - height) > 0.5 else { return }
        model.expandedPageHeight = height
        if model.notchState == .expanded {
            controller?.repositionWindow()
        }
    }

    func notchUpdatedText() -> String {
        let latest = model.providers.compactMap { provider -> Date? in
            QuotaFormatter.parseISODate(provider.fetchedAt)
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

    /// Ring subtitle for a dual-pool card: pool tag + the window the ring
    /// meters. Naming the pool tells the reader which of the two arcs the
    /// headline belongs to; naming the window keeps it from being confused
    /// with that pool's weekly percent on the bar rows right below.
    func splitRingSubtitle(for snapshot: QuotaWindowSnapshot) -> String {
        let tag = snapshot.tag ?? splitQuotaName(for: snapshot).uppercased()
        return L10n.tr("notch.ring.pool_five_hour", tag)
    }

    /// The 5h pool the ring headline is not bound to, for the faint line under
    /// the subtitle. A blocked pool shows its unlock countdown instead of a
    /// percent — its arc is a red lock track, and this is the only place the
    /// card says when it comes back.
    func splitRingSecondaryPool(
        for provider: ProviderUsage,
        state: ProviderQuotaState,
        bound: QuotaWindowSnapshot?,
        visual: ProviderVisual
    ) -> RingSecondaryPool? {
        guard let bound else { return nil }
        let pools = antigravityFiveHourPools(for: provider).prefix(2)
        guard pools.count >= 2, let other = pools.first(where: { $0.tag != bound.tag }) else { return nil }
        let tag = other.tag ?? splitQuotaName(for: other).uppercased()
        if let lock = state.blockedPools.first(where: { $0.tag == tag }) {
            return RingSecondaryPool(
                tag: tag,
                value: timerString(for: lock.snapshot),
                color: Color(NSColor.systemRed),
                blocked: true
            )
        }
        return RingSecondaryPool(
            tag: tag,
            value: "\(Int(other.remaining))%",
            color: splitQuotaColor(for: other, visual: visual),
            blocked: false
        )
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
        footerVisible: Bool
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
                regularQuotaRows(state: state, visual: visual, provider: provider)
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
                footerVisible: footerVisible
            )
        }
        .padding(.top, 4)
        .padding(.bottom, 6)
        // While loading the rows carry no real data (empty snapshots render
        // as "now"), so dim the whole band to read as pending, not live.
        .opacity(state.mode == .loading ? 0.35 : 1.0)
    }

    @ViewBuilder
    func regularQuotaRows(state: ProviderQuotaState, visual: ProviderVisual, provider: ProviderUsage) -> some View {
        VStack(spacing: 6) {
            if state.fiveHour != nil {
                quotaInfoLine(
                    title: L10n.tr("notch.row.5h"),
                    snapshot: state.fiveHour,
                    showPercent: false,
                    color: state.fiveHour?.remaining ?? 100 <= 0 ? Color(NSColor.systemRed) : Color.white.opacity(0.48)
                )
            } else if isCodexFiveHourTemporarilyUncapped(provider) {
                quotaStatusLine(
                    title: L10n.tr("notch.row.5h"),
                    status: L10n.tr("notch.five_hour_uncapped")
                )
            }
            if state.weekly != nil {
                quotaInfoLine(
                    title: L10n.tr("notch.row.weekly"),
                    snapshot: state.weekly,
                    showPercent: true,
                    color: state.weekly?.remaining ?? 100 <= 0 ? Color(NSColor.systemRed) : Color.white.opacity(0.5)
                )
                // The weekly bar visualizes the *long* window as a complement to
                // the ring's *short* (5h) window. When 5h is absent/uncapped
                // (e.g. Codex), the ring itself falls back to headlining weekly,
                // so the bar would just redraw the same percentage — skip it.
                // The weekly row above still carries the percent and countdown.
                // Any non-healthy mode shows a lock reason in the ring, not
                // weekly, so the bar stays as weekly's only visualization there.
                let ringHeadlinesWeekly = state.mode == .healthy && state.fiveHour == nil
                if !ringHeadlinesWeekly {
                    quotaProgressBar(remaining: state.weekly?.remaining ?? 0, color: quotaBarColor(remaining: state.weekly?.remaining ?? 0, brand: Color(visual.brandColor)))
                }
            }
        }
    }

    @ViewBuilder
    func quotaStatusLine(title: String, status: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .font(.system(size: 9.5, weight: .semibold))
                .tracking(0.8)
                .lineLimit(1)
            Spacer(minLength: 8)
            // Match the timer rows' right column: leading-aligned in the same
            // 56pt box so a short status ("Uncapped" / "一時解除") starts at the
            // same x as the "6d 04h" timer in the weekly row directly below,
            // instead of hugging the panel's right edge. There's no clock glyph
            // (nothing counts down for an uncapped window), so that column is
            // simply left empty. A status wider than 56pt would grow past the
            // box and still land against the trailing edge, unchanged.
            Text(status)
                .font(.system(size: 9.5, weight: .semibold))
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
                .frame(minWidth: 56, alignment: .leading)
        }
        .foregroundColor(Color.white.opacity(0.38))
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
        // Critical-low (or spent) → solid red. Locked-but-still-has-room (the
        // pool's other window is what's spent) → dimmed red, keeping the real
        // weekly level visible. Otherwise the pool's split hue.
        let barColor = snapshot.remaining < QuotaLevel.criticalRemaining
            ? Color(NSColor.systemRed)
            : (lock != nil ? Color(NSColor.systemRed).opacity(0.55) : splitQuotaColor(for: snapshot, visual: visual))
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
        let barColor = quotaBarColor(remaining: snapshot.remaining, brand: Color(visual.brandColor))
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

    // Fill color for a single-hue quota bar: the provider's brand color while
    // healthy, red once it drops below the shared critical threshold. No amber
    // tier — amber would collide with brand hues like Claude's coral, so the
    // notch steps straight brand → red. (The rich menu, whose bars are semantic
    // green rather than brand-colored, keeps an amber caution band.)
    func quotaBarColor(remaining: Double, brand: Color) -> Color {
        remaining < QuotaLevel.criticalRemaining ? Color(NSColor.systemRed) : brand
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
        footerVisible: Bool
    ) -> some View {
        let hasResets = provider.provider == "codex" && provider.resets != nil && (provider.resets?.count ?? 0) > 0
        let creditsWindow = provider.windows.first(where: { $0.key == "ai_credits" })
        let showFooter = footerVisible || hasResets || creditsWindow != nil

        if showFooter {
            // Content-driven: sizes to the note's natural 1 or 2 lines instead of
            // a fixed reserve, so short notes don't leave dead space below.
            VStack(spacing: 0) {
                Rectangle()
                    .fill(Color.white.opacity(0.08))
                    .frame(height: 0.5)
                    .padding(.top, provider.provider == "antigravity" ? 4 : 10)
                    .padding(.bottom, provider.provider == "antigravity" ? 8 : 10)

                // Keep the blocking reason primary, but do not hide a real
                // balance just because a quota window is exhausted. Disabled
                // Claude extra usage remains neutral rather than implying that
                // the balance can take over automatically.
                if let note = state.note {
                    Text(provider.provider == "antigravity" ? note.replacingOccurrences(of: " — ", with: "\n") : note)
                        .font(.system(size: 10, weight: .bold))
                        .multilineTextAlignment(.center)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity)
                        .foregroundColor(state.mode == .credits ? Color(NSColor.systemGreen)
                            : state.mode == .recovering ? Color(NSColor.systemYellow)
                            : Color(NSColor.systemRed))
                }
                if let creditsWindow {
                    UsageCreditsView(window: creditsWindow, enabled: state.creditsEnabled)
                        .padding(.top, state.note == nil ? 0 : 8)
                }
                if hasResets, let resets = provider.resets {
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
        } else if notchIsSoloDualWindow(model.providers), model.expandedStyle == "detailed" {
            soloCardQuotaView
        } else {
            // Compact solo mode intentionally falls through to the exact same
            // one-ring column used by 2+ providers (and by the pre-solo-card
            // implementation), so the layouts stay visually and structurally
            // identical instead of drifting as two copies evolve.
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
                let ringSubtitle = state.mode == .healthy
                    ? (splitRing.map { splitRingSubtitle(for: $0) }
                        ?? (state.fiveHour.map { $0.idle ? L10n.tr("notch.five_hour_window") : L10n.tr("notch.five_hour_left") }
                            ?? (state.weekly != nil ? L10n.tr("notch.weekly_left") : L10n.tr("notch.unavailable"))))
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
                let secondaryPool = poolArcs.isEmpty
                    ? nil
                    : splitRingSecondaryPool(for: p, state: state, bound: splitRing, visual: visual)
                let footerVisible = state.note != nil

                VStack(spacing: 0) {
                    if !(model.providers.count == 1 && model.expandedStyle == "compact") {
                        providerHeaderRow(for: p, visual: visual)
                    }

                    CircularProgressRing(
                        percentage: ringPercentage,
                        mode: state.mode,
                        credits: credits,
                        creditsIsDollar: p.provider != "antigravity",
                        color: ringColor,
                        glowColor: ringColor.opacity(0.5),
                        subtitle: ringSubtitle,
                        lockCountdown: timerString(for: state.lockSnapshot),
                        poolArcs: poolArcs,
                        secondaryPool: secondaryPool
                    )
                    .frame(height: 128, alignment: .center)
                    
                    quotaDetailBand(
                        for: p,
                        state: state,
                        visual: visual,
                        footerVisible: footerVisible
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
                        .padding(.vertical, 20)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .top)
        // Three-provider cards can route the shell header around the physical
        // notch, while one- and two-provider cards must push their whole body
        // below it. Reinvest a little of that saved vertical room here so the
        // quota content does not look pinned to the top with all of the shared
        // page-height slack collected above the pager.
        .padding(.top, model.providers.count == 3 ? 10 : 0)
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
            // Provider identity is redundant in Compact solo mode. Detailed
            // keeps it as quiet context over the two pool columns.
            if model.expandedStyle != "compact" {
                providerHeaderRow(for: p, visual: visual, centered: true, subtle: true)
                    .padding(.horizontal, 14)
            }

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
        let subtitle = (u.mode == .locked || u.mode == .recovering)
            ? (u.mode == .recovering ? L10n.tr("notch.recovering.upper") : lockReason)
            : (u.five.idle ? L10n.tr("notch.five_hour_window") : L10n.tr("notch.five_hour_left"))
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

    // MARK: - Solo card (single Claude/Codex provider, 5h + weekly)
    // One provider metering a 5-hour and a weekly window gets the full panel
    // width as one ring + a real info column, so the freed width carries
    // information instead of a second ring: the ring keeps the regular
    // column's metric (5-hour remaining while healthy, lock treatment bound
    // to the blocking window), the info column headlines the 5-hour reset
    // countdown, carries weekly as a labelled bar row, and closes with a
    // Today / Cache / Reserve foot strip. Depletion stays inside the same
    // shell: the hero swaps to the blocking window's countdown and the
    // state note sits above the foot. Scoped model caps (e.g. Claude's
    // Fable weekly) keep their rows under the weekly bar — they are
    // weekly-window sub-limits.
    var soloCardQuotaView: some View {
        let p = model.providers[0]
        let visual = providerVisual(for: p.provider)
        let state = quotaState(for: p)
        // Same ring metric as regularQuotaRemainingView's single-pool path.
        let ringPercentage = state.mode == .healthy
            ? (state.fiveHour?.remaining ?? state.bindingRemaining)
            : state.bindingRemaining
        let ringSubtitle = state.mode == .healthy
            ? (state.fiveHour.map { $0.idle ? L10n.tr("notch.five_hour_window") : L10n.tr("notch.five_hour_left") }
                ?? (state.weekly != nil ? L10n.tr("notch.weekly_left") : L10n.tr("notch.unavailable")))
            : state.lockReason
        return VStack(spacing: 0) {
            providerHeaderRow(for: p, visual: visual, centered: true, subtle: true)

            HStack(alignment: .center, spacing: 16) {
                CircularProgressRing(
                    percentage: ringPercentage,
                    mode: state.mode,
                    credits: state.credits,
                    creditsIsDollar: p.provider != "antigravity",
                    color: Color(visual.brandColor),
                    glowColor: Color(visual.brandColor).opacity(0.5),
                    subtitle: ringSubtitle,
                    lockCountdown: timerString(for: state.lockSnapshot)
                )
                .frame(width: 104)

                // Faint boundary between the graphic zone and the data zone —
                // the design's .card-info::before (0.075 vs the multi-column
                // dividers' 0.13, longer fade, deeper inset: it ties two halves
                // of ONE card together rather than separating two providers).
                // Deliberately in-flow and height-flexible: it soaks up the
                // height the paging ZStack proposes from the taller usage page,
                // stretching this page to match it — see the paging comment.
                Rectangle()
                    .fill(
                        LinearGradient(
                            gradient: Gradient(stops: [
                                .init(color: .clear, location: 0.0),
                                .init(color: Color.white.opacity(0.075), location: 0.24),
                                .init(color: Color.white.opacity(0.075), location: 0.76),
                                .init(color: .clear, location: 1.0)
                            ]),
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .frame(width: 0.5)
                    .padding(.vertical, 14)

                VStack(alignment: .leading, spacing: 12) {
                    soloCardHero(state: state)
                    soloCardWeeklyBlock(for: p, state: state, visual: visual)
                    if let note = state.note,
                       note != L10n.tr("notch.five_hour_spent") {
                        Text(note)
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(state.mode == .credits
                                ? Color(NSColor.systemGreen).opacity(0.85)
                                : Color(NSColor.systemRed))
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.top, 4)
            .padding(.bottom, 10)

            // A full-width bottom summary anchors the detailed quota page and
            // balances the ring/ledger composition above. Until history loads,
            // dashes reserve the final band without presenting fake zeros.
            soloCardFootRow(for: p, placeholder: !model.historyLoaded)
                .padding(.bottom, 12)
        }
        .frame(maxWidth: .infinity, alignment: .top)
        .padding(.horizontal, 24)
    }

    // Hero stat: while healthy, the binding window's reset countdown ("when do
    // I get quota back") headlines the column — the 5-hour window when it's
    // metered, else weekly (Codex drops its 5-hour window while temporarily
    // uncapped). A depleted provider swaps in the blocking window's countdown
    // so the big number is never a stale healthy figure. The weekly outlasts
    // the 5h, so weekly-blocked states count the weekly reset.
    @ViewBuilder
    func soloCardHero(state: ProviderQuotaState) -> some View {
        let depleted = state.mode == .credits || state.mode == .locked
        let snapshot = depleted ? state.lockSnapshot : (state.fiveHour ?? state.weekly)
        let idle = snapshot?.idle ?? false
        let fiveZero = (state.fiveHour?.remaining ?? 100) <= 0
        let weekZero = (state.weekly?.remaining ?? 100) <= 0
        let title: String = depleted
            ? (fiveZero && weekZero
                ? L10n.tr("notch.card.all_spent")
                : (weekZero ? L10n.tr("notch.card.weekly_reached") : L10n.tr("notch.five_hour_spent")))
            : (state.fiveHour == nil
                ? L10n.tr("notch.card.week_resets_in")
                : (idle ? L10n.tr("notch.five_hour_window") : L10n.tr("notch.card.five_resets_in")))
        let titleColor: Color = depleted
            ? (state.mode == .credits ? Color(NSColor.systemGreen).opacity(0.85) : Color(NSColor.systemRed))
            : Color.white.opacity(0.5)
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 5) {
                Image(systemName: idle ? "moon.zzz.fill" : "clock")
                    .font(.system(size: 10))
                    .opacity(0.8)
                Text(title.uppercased())
                    .font(.system(size: 11, weight: .semibold))
                    .tracking(0.5)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            .foregroundColor(titleColor)

            soloCardHeroCountdown(for: snapshot)

            if let abs = absoluteResetText(for: snapshot) {
                Text(L10n.tr("notch.card.resets_at", abs))
                    .font(.system(size: 9.5, weight: .semibold))
                    .foregroundColor(.white.opacity(0.42))
            }
        }
    }

    @ViewBuilder
    func soloCardHeroCountdown(for snapshot: QuotaWindowSnapshot?) -> some View {
        if let snapshot = snapshot, snapshot.idle {
            // Unanchored window: static window length, not a sawtooth timer.
            Text(windowLengthText(snapshot))
                .font(.system(size: 27, weight: .bold))
                .foregroundColor(.white.opacity(0.55))
                .monospacedDigit()
        } else if let snapshot = snapshot,
                  let date = resetsAtDate(from: snapshot.window.resetsAt) {
            let dur = formatDuration(date.timeIntervalSince(now))
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(dur.primary)
                    .font(.system(size: 27, weight: .bold))
                    .tracking(-0.5)
                    .foregroundColor(.white)
                if !dur.secondary.isEmpty {
                    Text(dur.secondary)
                        .font(.system(size: 15, weight: .bold))
                        .foregroundColor(.white.opacity(0.55))
                }
            }
            .monospacedDigit()
        } else {
            Text(L10n.tr("notch.now"))
                .font(.system(size: 27, weight: .bold))
                .foregroundColor(.white)
        }
    }

    // Weekly as a labelled bar row: caption + remaining % (same "left"
    // semantics as every other WEEKLY row) + countdown, the bar underneath,
    // and the absolute reset time as a sub-line. Scoped weekly sub-caps keep
    // their own rows below the bar.
    @ViewBuilder
    func soloCardWeeklyBlock(for provider: ProviderUsage, state: ProviderQuotaState, visual: ProviderVisual) -> some View {
        let scopedRows = scopedWindows(for: provider)
        // No 5-hour window while healthy → the ring already shows weekly-left
        // and the hero counts the weekly reset, so a weekly row + bar here
        // would repeat both numbers (mirrors regularQuotaRows' bar skip). The
        // slot instead reports why the 5-hour window is absent.
        let ringHeadlinesWeekly = state.mode == .healthy && state.fiveHour == nil
        VStack(alignment: .leading, spacing: 5) {
            if ringHeadlinesWeekly {
                if isCodexFiveHourTemporarilyUncapped(provider) {
                    quotaStatusLine(
                        title: L10n.tr("notch.row.5h"),
                        status: L10n.tr("notch.five_hour_uncapped")
                    )
                }
            } else if let weekly = state.weekly {
                let depleted = weekly.remaining <= 0
                quotaInfoLine(
                    title: L10n.tr("notch.row.weekly"),
                    snapshot: weekly,
                    showPercent: true,
                    color: depleted ? Color(NSColor.systemRed) : Color.white.opacity(0.5)
                )
                quotaProgressBar(
                    remaining: weekly.remaining,
                    color: quotaBarColor(remaining: weekly.remaining, brand: Color(visual.brandColor))
                )
                if let abs = absoluteResetText(for: weekly) {
                    Text(L10n.tr("notch.card.resets_at", abs))
                        .font(.system(size: 9.5, weight: .semibold))
                        .foregroundColor(.white.opacity(0.42))
                }
            }
            if !scopedRows.isEmpty {
                VStack(spacing: 7) {
                    ForEach(Array(scopedRows.enumerated()), id: \.offset) { _, row in
                        scopedQuotaRow(row, visual: visual)
                    }
                }
                .padding(.top, 3)
            }
            if let creditsWindow = provider.windows.first(where: { $0.key == "ai_credits" }),
               creditsWindow.remaining != nil {
                UsageCreditsView(
                    window: creditsWindow,
                    enabled: quota.creditsEnabled(for: provider),
                    ledgerStyle: true
                )
                .padding(.top, 3)
            }
            // Codex reset credits as a full ledger row (the same chip the
            // multi-column layout uses, hover schedule and granted flash
            // included) — a single "RESETS …… 2 available" line matches the
            // column's label-…-value language, where a stat cell in the foot
            // squeezed it into a box and lost the chip's affordances. Keeps
            // the foot trio (Today/Cache/This week) identical across
            // subscription providers.
            if provider.provider == "codex", let resets = provider.resets, resets.count > 0 {
                ResetChipView(
                    resets: resets,
                    brandColor: Color(visual.brandColor),
                    justGranted: model.justGranted
                )
                .padding(.top, 4)
            }
        }
    }

    // Foot strip: three same-period figures as one muted row — today's token
    // volume, cache hit rate, and API-equivalent value. Keeping every item on
    // today's time scale avoids mixing a rolling seven-day total into an
    // otherwise immediate quota snapshot.
    // `placeholder` (first history fetch still in flight) keeps the strip's
    // frame with dashes so the card doesn't jump when the data lands.
    @ViewBuilder
    func soloCardFootRow(for provider: ProviderUsage, placeholder: Bool = false) -> some View {
        let stats = model.todayStats[provider.provider.lowercased()]
        let denom = stats.map { $0.cacheRead + $0.input + $0.cacheCreation } ?? 0
        let cachePct: Int? = (stats != nil && denom > 0)
            ? Int((Double(stats!.cacheRead) / Double(denom) * 100).rounded())
            : nil
        let isSub = isSubscription(for: provider)
        let items: [(label: String, value: String)] = {
            if placeholder {
                // The loaded strip's usual trio, with dashes for the numbers.
                return [
                    (L10n.tr("notch.tokens_today.upper"), "–"),
                    (L10n.tr("notch.card.cache_hit"), "–"),
                    (L10n.tr("notch.card.api_value"), "–")
                ]
            }
            let value: String = {
                guard let stats = stats, denom > 0 else { return "—" }
                if stats.cost > 0 {
                    return String(format: isSub ? "≈$%.1f" : "~$%.1f", stats.cost)
                }
                return isSub ? L10n.tr("notch.in_plan") : "~$0.0"
            }()
            return [
                (L10n.tr("notch.tokens_today.upper"), formatTokenCount(stats?.tokens ?? 0)),
                (L10n.tr("notch.card.cache_hit"), cachePct.map { "\($0)%" } ?? "—"),
                (L10n.tr("notch.card.api_value"), value)
            ]
        }()
        // Equal thirds inside one quiet surface: this is a summary band, not
        // three buttons, so the shared border stays deliberately understated.
        HStack(spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.offset) { idx, item in
                if idx > 0 {
                    Rectangle()
                        .fill(Color.white.opacity(0.075))
                        .frame(width: 0.5, height: 24)
                        .padding(.horizontal, 10)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.label.uppercased())
                        .font(.system(size: 8, weight: .semibold))
                        .tracking(0.4)
                        .foregroundColor(.white.opacity(0.4))
                    Text(item.value)
                        .font(.system(size: 12.5, weight: .bold))
                        .monospacedDigit()
                        .foregroundColor(.white.opacity(placeholder ? 0.35 : 0.9))
                        .placeholderPulse(placeholder)
                }
                .lineLimit(1)
                .minimumScaleFactor(0.72)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(Color.white.opacity(0.02))
        .cornerRadius(9)
        .overlay(
            RoundedRectangle(cornerRadius: 9)
                .stroke(Color.white.opacity(0.065), lineWidth: 0.5)
        )
    }

    // Absolute reset moment ("resets 2:09 AM" / "resets Mon 5 AM") backing a
    // relative countdown: within 24h a bare clock time is unambiguous, beyond
    // that the weekday disambiguates. Nil for idle/unanchored windows, whose
    // reset moment would drift with every poll.
    func absoluteResetText(for snapshot: QuotaWindowSnapshot?) -> String? {
        guard let snapshot = snapshot, !snapshot.idle,
              let date = resetsAtDate(from: snapshot.window.resetsAt) else { return nil }
        let within24h = date.timeIntervalSince(now) < 24 * 3600
        // Follow the app language (which may differ from the system locale).
        let formatter = SharedDateFormatters.templated(
            within24h ? "jmm" : "Ejmm",
            language: L10n.resolvedAppLanguage
        )
        return formatter.string(from: date)
    }

    // The backend returns a rolling 14-day series. The compact page displays
    // its trailing seven days; unlike the old decorative sparkline, an empty
    // week still has useful meaning and therefore keeps its labelled frame.
    func tokenSparklineSeries(for provider: ProviderUsage) -> [Int]? {
        guard let full = model.dailyTokens[provider.provider.lowercased()] else { return nil }
        let series = Array(full.suffix(7))
        return series.count > 1 ? series : nil
    }

    func compactTrendLabels(count: Int, narrow: Bool) -> [String] {
        let formatter = SharedDateFormatters.templated(
            narrow ? "EEEEE" : "EEE",
            language: L10n.resolvedAppLanguage
        )
        let calendar = Calendar.current
        return (0..<count).map { index in
            if index == count - 1, !narrow {
                return L10n.tr("notch.card.today")
            }
            let offset = index - (count - 1)
            let date = calendar.date(byAdding: .day, value: offset, to: now) ?? now
            return formatter.string(from: date)
        }
    }

    func peakHourText(for provider: String) -> String {
        guard let hour = model.peakHours[provider.lowercased()] else { return "—" }
        var components = DateComponents()
        components.calendar = Calendar.current
        components.timeZone = TimeZone.current
        components.year = 2001
        components.month = 1
        components.day = 1
        components.hour = hour
        guard let start = components.date,
              let end = Calendar.current.date(byAdding: .hour, value: 1, to: start)
        else { return "—" }

        return SharedDateFormatters.hourRangeText(
            from: start,
            to: end,
            language: L10n.resolvedAppLanguage
        )
    }

    @ViewBuilder
    func compactUsageTrend(
        _ series: [Int],
        visual: ProviderVisual,
        narrow: Bool,
        placeholder: Bool = false
    ) -> some View {
        let peak = max(1, series.max() ?? 1)
        let labels = compactTrendLabels(count: series.count, narrow: narrow)
        VStack(spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(L10n.tr("notch.card.last_7_days").uppercased())
                    .font(.system(size: narrow ? 7.2 : 8, weight: .semibold))
                    .tracking(narrow ? 0.35 : 0.65)
                    .foregroundColor(.white.opacity(0.4))
                Spacer(minLength: 4)
                Text(placeholder ? "—" : formatTokenCount(series.reduce(0, +)))
                    .font(.system(size: narrow ? 10 : 11, weight: .bold))
                    .monospacedDigit()
                    .foregroundColor(.white.opacity(placeholder ? 0.25 : 0.76))
            }

            HStack(alignment: .bottom, spacing: narrow ? 2 : 4) {
                ForEach(Array(series.enumerated()), id: \.offset) { idx, value in
                    let isToday = idx == series.count - 1
                    VStack(spacing: 4) {
                        ZStack(alignment: .bottom) {
                            Rectangle()
                                .fill(Color.white.opacity(0.08))
                                .frame(height: 0.5)
                            TopRoundedBar(radius: 2)
                                .fill(placeholder
                                    ? Color.white.opacity(0.08)
                                    : (isToday ? Color(visual.brandColor) : Color.white.opacity(0.22)))
                                .frame(
                                    width: narrow ? 8 : 11,
                                    height: placeholder
                                        ? 5
                                        : (value == 0 ? 1 : max(isToday ? 5 : 3, 25 * CGFloat(value) / CGFloat(peak)))
                                )
                                .shadow(
                                    color: !placeholder && isToday
                                        ? Color(visual.brandColor).opacity(0.45)
                                        : .clear,
                                    radius: 3
                                )
                        }
                        .frame(height: 25, alignment: .bottom)

                        Text(labels[idx])
                            .font(.system(size: narrow ? 6.5 : 7.5, weight: isToday ? .bold : .medium))
                            .foregroundColor(placeholder
                                ? .white.opacity(0.18)
                                : (isToday ? Color(visual.brandColor).opacity(0.9) : .white.opacity(0.34)))
                            .lineLimit(1)
                            .minimumScaleFactor(0.75)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
        }
    }

    @ViewBuilder
    func compactUsagePair(input: Int, output: Int, inline: Bool) -> some View {
        HStack(spacing: 0) {
            compactUsageFigure(
                label: L10n.tr("notch.card.input"),
                value: formatTokenCount(input),
                inline: inline
            )
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(width: 0.5, height: inline ? 15 : 24)
                .padding(.horizontal, inline ? 6 : 8)
            compactUsageFigure(
                label: L10n.tr("notch.card.output"),
                value: formatTokenCount(output),
                inline: inline
            )
        }
    }

    @ViewBuilder
    func detailedUsageFlow(input: Int, cacheHit: Int?, output: Int) -> some View {
        HStack(spacing: 0) {
            detailedUsageFlowFigure(
                label: L10n.tr("notch.card.fresh_input"),
                value: formatTokenCount(input)
            )
            detailedUsageFlowDivider
            detailedUsageFlowFigure(
                label: L10n.tr("notch.card.cache_hit"),
                value: cacheHit.map { "\($0)%" } ?? "—",
                secondary: true
            )
            detailedUsageFlowDivider
            detailedUsageFlowFigure(
                label: L10n.tr("notch.card.output"),
                value: formatTokenCount(output)
            )
        }
    }

    var detailedUsageFlowDivider: some View {
        Rectangle()
            .fill(Color.white.opacity(0.075))
            .frame(width: 0.5, height: 18)
            .padding(.horizontal, 5)
    }

    @ViewBuilder
    func detailedUsageFlowFigure(label: String, value: String, secondary: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Text(value)
                .font(.system(size: secondary ? 12.5 : 14, weight: secondary ? .semibold : .bold))
                .monospacedDigit()
                .foregroundColor(.white.opacity(secondary ? 0.68 : 0.9))
            Text(label.uppercased())
                .font(.system(size: secondary ? 8 : 8.5, weight: .semibold))
                .tracking(secondary ? 0.35 : 0.5)
                .foregroundColor(.white.opacity(secondary ? 0.32 : 0.38))
        }
        .lineLimit(1)
        .minimumScaleFactor(0.68)
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    func compactUsageFigure(label: String, value: String, inline: Bool) -> some View {
        if inline {
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text(value)
                    .font(.system(size: 13, weight: .bold))
                    .monospacedDigit()
                    .foregroundColor(.white.opacity(0.9))
                Text(label.uppercased())
                    .font(.system(size: 7.5, weight: .semibold))
                    .tracking(0.5)
                    .foregroundColor(.white.opacity(0.38))
            }
            .lineLimit(1)
            .minimumScaleFactor(0.72)
            .frame(maxWidth: .infinity)
        } else {
            VStack(spacing: 2) {
                Text(value)
                    .font(.system(size: 13, weight: .bold))
                    .monospacedDigit()
                    .foregroundColor(.white.opacity(0.9))
                Text(label.uppercased())
                    .font(.system(size: 7.5, weight: .semibold))
                    .tracking(0.55)
                    .foregroundColor(.white.opacity(0.38))
            }
            .lineLimit(1)
            .minimumScaleFactor(0.75)
            .frame(maxWidth: .infinity)
        }
    }

    @ViewBuilder
    func compactUsageMetric(
        label: String,
        value: String,
        accent: Color? = nil,
        inline: Bool,
        condensed: Bool = false
    ) -> some View {
        Group {
            if inline {
                HStack(alignment: .firstTextBaseline, spacing: condensed ? 3 : 5) {
                    Text(label.uppercased())
                        .font(.system(size: condensed ? 7 : 7.2, weight: .semibold))
                        .tracking(condensed ? 0.25 : 0.35)
                        .foregroundColor(.white.opacity(0.38))
                    Spacer(minLength: 3)
                    Text(value)
                        .font(.system(size: 11.5, weight: .bold))
                        .monospacedDigit()
                        .foregroundColor(accent ?? .white.opacity(0.86))
                        .layoutPriority(1)
                }
            } else {
                VStack(alignment: .leading, spacing: 3) {
                    Text(label.uppercased())
                        .font(.system(size: 7.5, weight: .semibold))
                        .tracking(0.45)
                        .foregroundColor(.white.opacity(0.38))
                    Text(value)
                        .font(.system(size: 11.5, weight: .bold))
                        .monospacedDigit()
                        .foregroundColor(accent ?? .white.opacity(0.86))
                }
            }
        }
        .lineLimit(1)
        .minimumScaleFactor(0.68)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, condensed ? 6 : 8)
        .padding(.vertical, 7)
        .background(Color.white.opacity(0.045))
        .cornerRadius(8)
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.white.opacity(0.075), lineWidth: 0.5)
        )
    }

    @ViewBuilder
    var tokenUsageView: some View {
        // Detailed solo mode gets the richer full-width usage dashboard.
        // Compact solo mode deliberately falls through to the exact same
        // usage column as 2+ providers, matching page 1's component reuse so
        // both pages keep one coherent density and hierarchy.
        if model.providers.count == 1, model.expandedStyle == "detailed" {
            soloTokenUsageView
        } else {
            multiTokenUsageView
        }
    }

    // MARK: - Solo usage page (single provider: hero + This-week + tiles)
    var soloTokenUsageView: some View {
        let p = model.providers[0]
        let visual = providerVisual(for: p.provider)
        let stats = model.todayStats[p.provider.lowercased()] ?? ProviderHistoryStats(tokens: 0, input: 0, output: 0, cacheCreation: 0, cacheRead: 0, cost: 0.0)
        let state = quotaState(for: p)
        // 14-day series: the trailing 7 are displayed, and the prior 7 form
        // the comparison baseline. An old backend sends 7 → no baseline.
        let fullSeries = model.dailyTokens[p.provider.lowercased()] ?? []
        let series = Array(fullSeries.suffix(7))
        // The trend and analytics are permanent layout modules. Before
        // history arrives (or for a genuinely empty history), seven zero days
        // preserve their final geometry instead of removing the whole block.
        let displaySeries = series.count > 1 ? series : Array(repeating: 0, count: 7)
        let prevTotal = fullSeries.count >= 14 ? fullSeries.prefix(7).reduce(0, +) : 0
        let weekTotal = displaySeries.reduce(0, +)
        let denom = stats.cacheRead + stats.input + stats.cacheCreation
        let cachePct = denom > 0
            ? Int((Double(stats.cacheRead) / Double(denom) * 100).rounded())
            : nil
        return VStack(spacing: 0) {
            providerHeaderRow(for: p, visual: visual, centered: true, subtle: true)

            ZStack {
                soloTokenUsageContent(
                    provider: p,
                    visual: visual,
                    state: state,
                    stats: stats,
                    cachePct: cachePct,
                    denom: denom,
                    series: displaySeries,
                    weekTotal: weekTotal,
                    prevTotal: prevTotal
                )
                .opacity(model.historyLoaded ? 1 : 0)
                .allowsHitTesting(model.historyLoaded)
                .accessibilityHidden(!model.historyLoaded)

                if !model.historyLoaded {
                    // The complete dashboard above remains in layout while
                    // transparent; this centered layer is only the visible
                    // loading treatment and therefore cannot affect height.
                    VStack(spacing: 9) {
                        ProgressView()
                            .controlSize(.small)
                            .environment(\.colorScheme, .dark)
                        Text(L10n.tr("notch.loading.upper"))
                            .font(.system(size: 9, weight: .bold))
                            .tracking(0.8)
                            .foregroundColor(Color.white.opacity(0.42))
                    }
                    .transition(.opacity)
                    .accessibilityElement(children: .combine)
                }
            }
            .animation(.easeOut(duration: 0.18), value: model.historyLoaded)
        }
        .frame(maxWidth: .infinity, alignment: .top)
        .padding(.horizontal, 24)
        .padding(.bottom, 8)
    }

    @ViewBuilder
    func soloTokenUsageContent(
        provider p: ProviderUsage,
        visual: ProviderVisual,
        state: ProviderQuotaState,
        stats: ProviderHistoryStats,
        cachePct: Int?,
        denom: Int,
        series: [Int],
        weekTotal: Int,
        prevTotal: Int
    ) -> some View {
        VStack(spacing: 0) {
            VStack(spacing: 4) {
                Text(formatTokenCount(stats.tokens))
                    .font(.system(size: 32, weight: .bold, design: .default))
                    .foregroundColor(Color(visual.brandColor))
                Text(L10n.tr("notch.tokens_today.upper"))
                    .font(.system(size: 8.5, weight: .bold))
                    .tracking(1.2)
                    .foregroundColor(Color.white.opacity(0.36))
            }
            .padding(.top, 10)

            // Empty usage and the regular Input/Cache/Output flow occupy one
            // identical slot. A true zero state changes its message, not the
            // page's geometry.
            Group {
                if denom == 0 {
                    Text(L10n.tr("notch.no_consumption"))
                        .font(.system(size: 10, weight: .bold))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 3)
                        .background(Color.white.opacity(0.08))
                        .cornerRadius(8)
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(Color.white.opacity(0.12), lineWidth: 0.5)
                        )
                        .foregroundColor(.white.opacity(0.48))
                } else {
                    detailedUsageFlow(input: stats.input, cacheHit: cachePct, output: stats.output)
                }
            }
            .frame(maxWidth: 310)
            .frame(height: 20)
            .padding(.top, 10)

            soloWeekSection(series: series, weekTotal: weekTotal, prevTotal: prevTotal, visual: visual)
                .padding(.top, 12)

            soloUsageTiles(provider: p.provider, series: series, weekTotal: weekTotal)
                .padding(.top, 10)

            if state.mode == .credits, let creds = state.credits {
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
                .padding(.top, 12)
            }
        }
    }

    // Rolling seven-day trend: seven equal-width bars with localized weekday
    // labels. The explicit time range matches the backend's trailing window;
    // calling it "this week" would incorrectly imply a calendar-week total.
    @ViewBuilder
    func soloWeekSection(series: [Int], weekTotal: Int, prevTotal: Int, visual: ProviderVisual) -> some View {
        let peak = max(1, series.max() ?? 1)
        let labels = compactTrendLabels(count: series.count, narrow: false)
        // Week-over-week: this week's total against the 7 days before it.
        // Consumption up reads warm (spending faster), down reads green.
        let delta: Int? = prevTotal > 0
            ? Int((Double(weekTotal - prevTotal) / Double(prevTotal) * 100).rounded())
            : nil
        VStack(spacing: 0) {
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(height: 0.5)
                .padding(.bottom, 10)

            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text(L10n.tr("notch.card.last_7_days").uppercased())
                    .font(.system(size: 9, weight: .semibold))
                    .tracking(0.8)
                    .foregroundColor(.white.opacity(0.42))
                Spacer()
                if let delta = delta {
                    let up = delta >= 0
                    Text("\(up ? "▲" : "▼")\(abs(delta))%")
                        .font(.system(size: 9.5, weight: .bold))
                        .monospacedDigit()
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background((up ? Color(NSColor.systemOrange) : Color(NSColor.systemGreen)).opacity(0.13))
                        .cornerRadius(5)
                        .foregroundColor(up
                            ? Color(NSColor.systemOrange).opacity(0.95)
                            : Color(NSColor.systemGreen).opacity(0.92))
                }
                Text(formatTokenCount(weekTotal))
                    .font(.system(size: 13, weight: .bold))
                    .monospacedDigit()
                    .foregroundColor(.white)
            }

            ZStack(alignment: .bottom) {
                Rectangle()
                    .fill(Color.white.opacity(0.09))
                    .frame(height: 0.5)
                HStack(alignment: .bottom, spacing: 4) {
                    ForEach(Array(series.enumerated()), id: \.offset) { idx, value in
                        let isToday = idx == series.count - 1
                        TopRoundedBar(radius: 2)
                            .fill(isToday ? Color(visual.brandColor) : Color.white.opacity(0.12))
                            .frame(height: max(isToday ? 6 : 3, 35 * CGFloat(value) / CGFloat(peak)))
                            .frame(maxWidth: .infinity)
                            .shadow(color: isToday ? Color(visual.brandColor).opacity(0.5) : .clear, radius: 3)
                    }
                }
            }
            .frame(height: 35, alignment: .bottom)
            .padding(.top, 8)

            HStack(spacing: 4) {
                ForEach(Array(labels.enumerated()), id: \.offset) { idx, label in
                    let isToday = idx == labels.count - 1
                    Text(label)
                        .font(.system(size: 10, weight: isToday ? .bold : .medium))
                        .foregroundColor(isToday
                            ? Color(visual.brandColor).opacity(0.9)
                            : .white.opacity(0.46))
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .frame(maxWidth: .infinity)
                }
            }
            .padding(.top, 4)
        }
    }

    // Three compact analytics derived from the same rolling seven-day series.
    // The quota page already summarizes today's cache and API value, so these
    // tiles add depth instead of repeating it: calendar-day average, peak day,
    // and the provider's habitual peak local hour.
    @ViewBuilder
    func soloUsageTiles(provider: String, series: [Int], weekTotal: Int) -> some View {
        let labels = compactTrendLabels(count: series.count, narrow: false)
        let average = series.isEmpty
            ? 0
            : Int((Double(weekTotal) / Double(series.count)).rounded())
        let peakIndex = series.indices.max(by: { series[$0] < series[$1] })
        let peakValue = peakIndex.map { series[$0] } ?? 0
        let peakLabel = weekTotal > 0
            ? (peakIndex.flatMap { labels.indices.contains($0) ? labels[$0] : nil } ?? "—")
            : "—"
        let peakDisplay = weekTotal > 0
            ? "\(peakLabel) · \(formatTokenCount(peakValue))"
            : "—"
        let tiles: [(label: String, value: String)] = [
            (L10n.tr("notch.card.daily_avg"), formatTokenCount(average)),
            (L10n.tr("notch.card.peak_day"), peakDisplay),
            (L10n.tr("notch.card.peak_hour"), peakHourText(for: provider))
        ]
        if !series.isEmpty {
            HStack(spacing: 8) {
                ForEach(Array(tiles.enumerated()), id: \.offset) { _, tile in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(tile.label.uppercased())
                            .font(.system(size: 9, weight: .semibold))
                            .tracking(0.4)
                            .foregroundColor(.white.opacity(0.42))
                        // Held at 13: the peak-hour range ("19:00–20:00") is the
                        // widest value any tile carries and overflows at 14.
                        Text(tile.value)
                            .font(.system(size: 13, weight: .bold))
                            .monospacedDigit()
                            .foregroundColor(.white)
                    }
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 6)
                    .background(Color.white.opacity(0.045))
                    .cornerRadius(9)
                    .overlay(
                        RoundedRectangle(cornerRadius: 9)
                            .stroke(Color.white.opacity(0.08), lineWidth: 0.5)
                    )
                }
            }
        }
    }

    var multiTokenUsageView: some View {
        ZStack {
            // Always lay out the complete dashboard, even while it is hidden
            // behind the loading state. This invisible sizing twin includes
            // the trend block, so the real content can fade in without ever
            // changing the page or window height.
            HStack(alignment: .top, spacing: 0) {
                let padding: CGFloat = model.providers.count == 3 ? 8 : 14
                ForEach(0..<model.providers.count, id: \.self) { idx in
                let p = model.providers[idx]
                let visual = providerVisual(for: p.provider)
                let stats = model.todayStats[p.provider.lowercased()] ?? ProviderHistoryStats(tokens: 0, input: 0, output: 0, cacheCreation: 0, cacheRead: 0, cost: 0.0)
                let state = quotaState(for: p)
                let credits = state.credits
                let isSub = isSubscription(for: p)
                let sparkline = tokenSparklineSeries(for: p)
                let denom = stats.cacheRead + stats.input + stats.cacheCreation
                let cacheValue = denom > 0
                    ? "\(Int(round(Double(stats.cacheRead) / Double(denom) * 100)))%"
                    : "—"
                // Three provider columns are too narrow for translated labels
                // and values to stay legible on one line. One and two columns
                // use the denser inline treatment; three retain two-line rows.
                let inlineMetrics = model.providers.count < 3
                let condensedMetrics = model.providers.count == 2
                let condensedCacheLabel = L10n.resolvedAppLanguage.hasPrefix("zh")
                    ? L10n.tr("notch.card.cache_hit")
                    : L10n.tr("notch.card.cache")
                let valueMetric: (label: String, value: String, accent: Color?) = {
                    if state.mode == .credits, let creds = credits {
                        let value = p.provider != "antigravity"
                            ? String(format: condensedMetrics ? "$%.1f" : "$%.2f", creds)
                            : "\(Int(creds))"
                        return (L10n.tr("notch.card.reserve"), value, Color(NSColor.systemGreen))
                    }
                    if denom == 0 {
                        return (L10n.tr("notch.card.api_value"), "—", nil)
                    }
                    if isSub, stats.cost == 0 {
                        return (L10n.tr("notch.card.api_value"), L10n.tr("notch.in_plan"), nil)
                    }
                    let value = isSub
                        ? String(format: condensedMetrics ? "≈$%.1f" : "≈$%.2f", stats.cost)
                        : String(format: condensedMetrics ? "~$%.1f" : "~$%.2f", stats.cost)
                    return (
                        L10n.tr("notch.card.api_value"),
                        value,
                        isSub ? nil : Color(NSColor.systemGreen)
                    )
                }()

                VStack(spacing: 0) {
                    if !(model.providers.count == 1 && model.expandedStyle == "compact") {
                        providerHeaderRow(for: p, visual: visual)
                    }

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
                    .padding(.top, model.providers.count == 1 ? 16 : 18)

                    compactUsagePair(
                        input: stats.input,
                        output: stats.output,
                        inline: inlineMetrics
                    )
                        .padding(.top, 12)

                    HStack(spacing: 6) {
                        compactUsageMetric(
                            label: condensedMetrics
                                ? condensedCacheLabel
                                : L10n.tr("notch.card.cache_hit"),
                            value: cacheValue,
                            inline: inlineMetrics,
                            condensed: condensedMetrics
                        )
                        compactUsageMetric(
                            label: condensedMetrics && valueMetric.label == L10n.tr("notch.card.api_value")
                                ? L10n.tr("notch.card.value_short")
                                : valueMetric.label,
                            value: valueMetric.value,
                            accent: valueMetric.accent,
                            inline: inlineMetrics,
                            condensed: condensedMetrics
                        )
                    }
                    .padding(.top, 10)

                    // History arrives a few seconds after quota data. Keep the
                    // complete trend module in the layout from the first frame
                    // so loading only replaces values and bars — it never adds
                    // a new block that can increase the island's height.
                    VStack(spacing: 0) {
                        Rectangle()
                            .fill(Color.white.opacity(0.075))
                            .frame(height: 0.5)
                            .padding(.bottom, 10)
                        compactUsageTrend(
                            sparkline ?? Array(repeating: 0, count: 7),
                            visual: visual,
                            narrow: model.providers.count > 1,
                            placeholder: !model.historyLoaded
                        )
                    }
                    .padding(.top, 13)
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
                        .padding(.vertical, 20)
                }
                }
            }
            .frame(maxWidth: .infinity, alignment: .top)
            .padding(.horizontal, 16)
            .opacity(model.historyLoaded ? 1 : 0)
            .allowsHitTesting(model.historyLoaded)
            .accessibilityHidden(!model.historyLoaded)

            if !model.historyLoaded {
                VStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                        .environment(\.colorScheme, .dark)
                    Text(L10n.tr("notch.loading.upper"))
                        .font(.system(size: 9, weight: .bold))
                        .tracking(0.8)
                        .foregroundColor(Color.white.opacity(0.42))
                }
                .transition(.opacity)
                .accessibilityElement(children: .combine)
            }
        }
        .animation(.easeOut(duration: 0.18), value: model.historyLoaded)
    }
    
}

// Gentle opacity breathing for loading placeholders — signals "pending"
// without adding a spinner to a page whose other content is already live.
private struct PlaceholderPulse: ViewModifier {
    @State private var dim = false
    func body(content: Content) -> some View {
        content
            .opacity(dim ? 0.45 : 1.0)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) {
                    dim = true
                }
            }
    }
}

extension View {
    // Conditional wrapper: the placeholder branch gets its own view identity,
    // so the repeat-forever animation dies with it when real data lands.
    @ViewBuilder
    func placeholderPulse(_ active: Bool) -> some View {
        if active {
            modifier(PlaceholderPulse())
        } else {
            self
        }
    }
}

struct UsageCreditsView: View {
    let window: QuotaWindow
    let enabled: Bool
    var ledgerStyle = false
    @State private var isHovering = false
    @State private var hoverWorkItem: DispatchWorkItem? = nil

    private var amount: String {
        guard let balance = window.remaining else { return "—" }
        return QuotaFormatter.formatCreditBalance(balance, currency: window.currency)
    }

    private var expiry: String? {
        QuotaFormatter.formatExpiryDate(window.expiresAt)
    }

    private var tint: Color {
        enabled ? Color(NSColor.systemGreen) : Color.white.opacity(0.5)
    }

    var body: some View {
        Group {
            if ledgerStyle {
                HStack(spacing: 6) {
                    Image(systemName: "creditcard.fill")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(tint)
                    Text(L10n.tr("notch.credits_title"))
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.white.opacity(0.5))
                    Spacer(minLength: 8)
                    Text(amount)
                        .font(.system(size: 11, weight: .bold, design: .monospaced))
                        .foregroundColor(.white.opacity(0.88))
                }
            } else {
                HStack(spacing: 5) {
                    Image(systemName: "creditcard.fill")
                        .font(.system(size: 9, weight: .semibold))
                    Text(L10n.tr(
                        enabled ? "notch.credits_ready" : "notch.credits_balance",
                        amount
                    ))
                }
                .font(.system(size: 9.5, weight: .bold))
                .foregroundColor(tint)
            }
        }
        .contentShape(Rectangle())
        .onHover { hovering in
            guard expiry != nil else { return }
            if hovering {
                hoverWorkItem?.cancel()
                let item = DispatchWorkItem { isHovering = true }
                hoverWorkItem = item
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: item)
            } else {
                hoverWorkItem?.cancel()
                isHovering = false
            }
        }
        .popover(isPresented: $isHovering, arrowEdge: .top) {
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 7) {
                    Image(systemName: "creditcard.fill")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(tint)
                    Text(L10n.tr("notch.credits_title"))
                        .font(.system(size: 11.5, weight: .bold))
                    Spacer(minLength: 14)
                    Text(amount)
                        .font(.system(size: 11.5, weight: .bold, design: .monospaced))
                }
                Divider()
                    .background(Color.white.opacity(0.12))
                if let expiry {
                    Text(L10n.tr("menu.credits.expires", expiry))
                        .font(.system(size: 10.5, weight: .semibold))
                        .foregroundColor(.white.opacity(0.58))
                }
            }
            .padding(12)
            .frame(width: 220)
            .background(Color.black.opacity(0.25))
            .preferredColorScheme(.dark)
        }
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
        let formatter = SharedDateFormatters.fixedPattern("MMM d", locale: Locale.current)
        return formatter.string(from: date)
    }
}
