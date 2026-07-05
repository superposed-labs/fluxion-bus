import Foundation

struct QuotaFormatter {
    /// Strict ISO8601 parse that returns nil on failure.
    static func parseISODate(_ isoString: String?) -> Date? {
        guard let isoString = isoString else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        if let parsed = formatter.date(from: isoString) {
            return parsed
        }
        let formatterFraction = ISO8601DateFormatter()
        formatterFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatterFraction.date(from: isoString)
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
