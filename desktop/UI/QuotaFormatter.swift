import Foundation

/// Shared date formatters for the render path.
///
/// Constructing a `DateFormatter`/`ISO8601DateFormatter`/`DateIntervalFormatter` is
/// expensive — each one builds a CFDateFormatter and resolves a locale, and
/// `setLocalizedDateFormatFromTemplate` is the priciest of the lot. The notch rebuilds
/// its entire SwiftUI tree once a second (and again on every page flip), re-parsing
/// every provider's reset timestamps along the way, so allocating formatters per call
/// added up to hundreds of constructions per render pass.
///
/// Each cached instance is fully configured before it is stored and only read
/// afterwards, which is what makes sharing it safe. The cache dictionaries themselves
/// are guarded by `lock` — callers are main-thread today, but an uncontended lock costs
/// orders of magnitude less than the construction it avoids, so this stays correct if
/// that ever changes.
///
/// Locale-dependent formatters key on the *resolved app language*, not just on the
/// format string: the in-app language is user-switchable at runtime, and a
/// language-blind cache would pin date text to the language in effect at first use
/// until the app relaunched.
enum SharedDateFormatters {
    private static let lock = NSLock()

    private static let iso: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let isoFractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    /// Memo for `parseISO`. Sharing the formatter removed the construction cost
    /// but not the parse itself, which profiling still put at ~6% of the notch's
    /// main-thread time while paging: the same handful of reset timestamps are
    /// re-parsed for every provider, every window, on every render pass.
    ///
    /// Safe to cache without any invalidation because the mapping is pure — an
    /// ISO8601 string always denotes the same instant. Misses are cached too:
    /// a string that parses under neither option set is the case that costs two
    /// full attempts, so it is the one most worth remembering.
    private static var parsedISOCache: [String: Date?] = [:]

    /// The live working set is a few dozen strings (one reset instant per window,
    /// plus a `fetchedAt` per provider), but those rotate as windows reset and
    /// polls land, so the table would creep upward forever. Past the cap, drop
    /// everything rather than tracking recency — refilling costs one parse per
    /// string still in use, and the cap is high enough that this is rare.
    private static let parsedISOCacheCap = 512

    /// Plain first, then fractional. The two option sets are mutually exclusive —
    /// a timestamp parses under exactly one of them — so the order only decides
    /// which attempt is wasted, never the result.
    static func parseISO(_ isoString: String) -> Date? {
        lock.lock()
        if let cached = parsedISOCache[isoString] {
            lock.unlock()
            return cached
        }
        lock.unlock()

        // Parsed outside the lock: this is the expensive part, and a duplicate
        // parse under contention is cheaper than serialising every caller on it.
        let parsed = iso.date(from: isoString) ?? isoFractional.date(from: isoString)

        lock.lock()
        if parsedISOCache.count >= parsedISOCacheCap {
            parsedISOCache.removeAll(keepingCapacity: true)
        }
        parsedISOCache[isoString] = parsed
        lock.unlock()
        return parsed
    }

    private static var templatedCache: [String: DateFormatter] = [:]

    /// Formatter for a localized *template* (`"jmm"`, `"Ejmm"`, `"EEE"`, ...), whose
    /// concrete pattern is chosen by the locale.
    static func templated(_ template: String, language: String) -> DateFormatter {
        lock.lock()
        defer { lock.unlock() }
        let key = "\(language)|\(template)"
        if let cached = templatedCache[key] { return cached }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: language)
        formatter.setLocalizedDateFormatFromTemplate(template)
        templatedCache[key] = formatter
        return formatter
    }

    private static var fixedPatternCache: [String: DateFormatter] = [:]

    /// Formatter for a fixed pattern (`"MMM d"`), where the locale only affects
    /// symbol rendering rather than field order.
    static func fixedPattern(_ pattern: String, locale: Locale) -> DateFormatter {
        lock.lock()
        defer { lock.unlock() }
        let key = "\(locale.identifier)|\(pattern)"
        if let cached = fixedPatternCache[key] { return cached }
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.dateFormat = pattern
        fixedPatternCache[key] = formatter
        return formatter
    }

    private static var currencyCache: [String: NumberFormatter] = [:]

    static func currency(code: String, language: String) -> NumberFormatter {
        lock.lock()
        defer { lock.unlock() }
        let key = "\(language)|\(code)"
        if let cached = currencyCache[key] { return cached }
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: language)
        formatter.numberStyle = .currency
        formatter.currencyCode = code
        formatter.minimumFractionDigits = 2
        formatter.maximumFractionDigits = 2
        currencyCache[key] = formatter
        return formatter
    }

    /// A whole-hour range for the peak-hour tile.
    ///
    /// Both ends are rendered separately rather than through DateIntervalFormatter.
    /// The tile's hours are built on a sentinel date, and the 23:00 bucket ends
    /// on the following day — which is enough for the interval formatter to
    /// decide the range needs disambiguating and print the sentinel outright
    /// ("1/1/2001, 11 PM – 1/2/2001, 12 AM").
    ///
    /// The bucket is always a full hour, so the minutes are only worth printing
    /// where they read as part of the notation: 24-hour locales look bare
    /// without them ("19 – 20"), while 12-hour locales already carry the day
    /// period and would just repeat it ("7:00 PM–8:00 PM").
    static func hourRangeText(from start: Date, to end: Date, language: String) -> String {
        let locale = Locale(identifier: language)
        let hourPattern = DateFormatter.dateFormat(fromTemplate: "j", options: 0, locale: locale) ?? ""
        let formatter = templated(hourPattern.contains("a") ? "j" : "jmm", language: language)
        return "\(formatter.string(from: start))–\(formatter.string(from: end))"
    }
}

struct QuotaFormatter {
    /// Strict ISO8601 parse that returns nil on failure.
    static func parseISODate(_ isoString: String?) -> Date? {
        guard let isoString = isoString else { return nil }
        return SharedDateFormatters.parseISO(isoString)
    }

    static func formatCreditBalance(_ value: Double, currency: String?) -> String {
        guard let code = currency?.trimmingCharacters(in: .whitespacesAndNewlines),
              !code.isEmpty else {
            return value.rounded() == value ? "\(Int(value))" : "\(value)"
        }
        let formatter = SharedDateFormatters.currency(code: code, language: L10n.resolvedAppLanguage)
        return formatter.string(from: NSNumber(value: value)) ?? String(format: "$%.2f", value)
    }

    static func formatExpiryDate(_ isoString: String?) -> String? {
        guard let date = parseISODate(isoString) else { return nil }
        return SharedDateFormatters.templated("yMMMd", language: L10n.resolvedAppLanguage).string(from: date)
    }

    /// A window is "idle" (unanchored) when its reported reset is essentially a
    /// full window-length past the moment we observed it — i.e. the provider
    /// keeps saying "resets <window> from now" instead of holding a fixed reset
    /// instant.
    static func isWindowIdle(_ window: QuotaWindow, fetchedAt: String?) -> Bool {
        if (window.resetsAt ?? "").trimmingCharacters(in: .whitespaces).isEmpty {
            return (window.usedPercent ?? 0) <= 5.0
        }
        guard let windowMinutes = window.windowMinutes, windowMinutes > 0,
              let resetDate = parseISODate(window.resetsAt),
              let fetchedDate = parseISODate(fetchedAt) else {
            return false
        }
        let windowSeconds = Double(windowMinutes) * 60.0
        let span = resetDate.timeIntervalSince(fetchedDate)
        // Tolerance absorbs the few seconds of timestamp jitter in a "resets a
        // full window from now" report; 15s gives ~6× margin.
        let tolerance: TimeInterval = 15
        return span >= windowSeconds - tolerance && span <= windowSeconds + tolerance
    }

    /// Static label for a window's full length, shown in place of a live
    /// countdown when the window is idle.
    static func windowLengthText(windowMinutes: Int?, fallbackLabel: String?) -> String {
        if let m = windowMinutes, m > 0 {
            if m % 1440 == 0 { return "\(m / 1440)d" }
            if m % 60 == 0 { return "\(m / 60)h" }
            if m < 60 { return "\(m)m" }
            return "\(m / 60)h\(String(format: "%02d", m % 60))m"
        }
        let label = (fallbackLabel ?? "").lowercased()
        if label.contains("5") || label.contains("hour") || label.contains("rolling") {
            return "5h"
        }
        return "7d"
    }

    /// Format a duration in seconds to primary and secondary segments.
    static func formatDuration(_ seconds: TimeInterval) -> (primary: String, secondary: String) {
        if seconds <= 0 {
            return ("now", "")
        }
        let d = Int(seconds) / 86400
        let h = (Int(seconds) % 86400) / 3600
        let m = (Int(seconds) % 3600) / 60
        let s = Int(seconds) % 60

        let pad = { (n: Int) -> String in String(format: "%02d", n) }

        if d > 0 {
            return ("\(d)d", "\(pad(h))h")
        }
        if h > 0 {
            return ("\(h)h", "\(pad(m))m")
        }
        return ("\(pad(m))m", "\(pad(s))s")
    }
}
