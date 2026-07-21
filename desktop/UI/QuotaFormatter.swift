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

    /// Plain first, then fractional. The two option sets are mutually exclusive —
    /// a timestamp parses under exactly one of them — so the order only decides
    /// which attempt is wasted, never the result.
    static func parseISO(_ isoString: String) -> Date? {
        iso.date(from: isoString) ?? isoFractional.date(from: isoString)
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

    private static var shortTimeRangeCache: [String: DateIntervalFormatter] = [:]

    /// Time-only range formatter ("2 – 3 PM"), used for peak-hour labels.
    static func shortTimeRange(language: String) -> DateIntervalFormatter {
        lock.lock()
        defer { lock.unlock() }
        if let cached = shortTimeRangeCache[language] { return cached }
        let formatter = DateIntervalFormatter()
        formatter.locale = Locale(identifier: language)
        formatter.dateStyle = .none
        formatter.timeStyle = .short
        shortTimeRangeCache[language] = formatter
        return formatter
    }
}

struct QuotaFormatter {
    /// Strict ISO8601 parse that returns nil on failure.
    static func parseISODate(_ isoString: String?) -> Date? {
        guard let isoString = isoString else { return nil }
        return SharedDateFormatters.parseISO(isoString)
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
