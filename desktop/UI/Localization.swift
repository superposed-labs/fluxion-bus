import Foundation

enum L10n {
    static let languageDefaultsKey = "FluxionAppLanguage"
    static let supportedLanguageCodes = ["system", "zh-Hans", "en", "ja"]

    static let appLanguage: String = normalizedLanguage(
        UserDefaults.standard.string(forKey: languageDefaultsKey) ?? "system"
    )

    static var savedAppLanguage: String {
        return normalizedLanguage(UserDefaults.standard.string(forKey: languageDefaultsKey) ?? "system")
    }

    private static func normalizedLanguage(_ raw: String) -> String {
        return supportedLanguageCodes.contains(raw) ? raw : "system"
    }

    static var resolvedAppLanguage: String {
        return resolvedLanguage(appLanguage)
    }

    static var pythonLocale: String {
        return pythonLocale(for: appLanguage)
    }

    /// Backend locale (`zh`/`en`/`ja`) for a given app-language selection,
    /// resolving "system" against the current macOS preferred languages.
    static func pythonLocale(for language: String) -> String {
        switch resolvedLanguage(language) {
        case "zh-Hans": return "zh"
        case "ja": return "ja"
        default: return "en"
        }
    }

    private static func resolvedLanguage(_ raw: String) -> String {
        let language = normalizedLanguage(raw)
        guard language == "system" else { return language }
        return systemPreferredLanguage()
    }

    private static func systemPreferredLanguage() -> String {
        for raw in Locale.preferredLanguages {
            let language = raw.lowercased().replacingOccurrences(of: "_", with: "-")
            if language.hasPrefix("zh") {
                return "zh-Hans"
            }
            if language.hasPrefix("ja") {
                return "ja"
            }
            if language.hasPrefix("en") {
                return "en"
            }
        }
        return "en"
    }

    private static var localizedBundle: Bundle {
        let language = resolvedAppLanguage
        guard let path = Bundle.main.path(forResource: language, ofType: "lproj"),
              let bundle = Bundle(path: path) else {
            return Bundle.main
        }
        return bundle
    }

    private static var formatLocale: Locale {
        return Locale(identifier: resolvedAppLanguage)
    }

    /// The macOS-wide key every framework reads to decide what language to
    /// draw itself in.
    private static let appleLanguagesKey = "AppleLanguages"

    /// Tell the frameworks which language the app is in.
    ///
    /// `tr` only swaps the strings we own. Everything macOS draws for us —
    /// Sparkle's update dialogs, the open panel's Cancel button and sidebar,
    /// the Dictation and Emoji items AppKit adds to the Edit menu, an NSAlert's
    /// default button — resolves its own localization from `AppleLanguages`,
    /// which knows nothing about our menu. On a Japanese system an app set to
    /// 简体中文 therefore drew Chinese text beside Japanese buttons inside the
    /// same window.
    ///
    /// Writing `AppleLanguages` into our own defaults domain is how an app with
    /// its own language menu answers that question; it is the same key System
    /// Settings writes for a per-app language, so ours becomes the one source
    /// of truth for this app. It must be set before AppKit resolves its first
    /// localized resource, so the entry point calls it ahead of NSApplication —
    /// which is also why changing the language needs a relaunch (the
    /// preferences pane already says so).
    ///
    /// Records the value we last wrote to `AppleLanguages`, so switching back to
    /// "follow macOS" can tell our own pin from one System Settings put there.
    /// Language & Region's per-app override writes the very same key, and
    /// deleting it would silently undo a setting the user made outside this app.
    private static let pinMarkerKey = "FluxionAppleLanguagesPin"

    /// Pass the newly picked language when applying a change; the launch path
    /// reads the stored one.
    static func applyLanguageToFrameworks(_ language: String? = nil) {
        let defaults = UserDefaults.standard
        let selection = normalizedLanguage(language ?? savedAppLanguage)
        guard selection != "system" else {
            // "Follow macOS" must leave no pin of OURS behind: a stale one would
            // outrank a later system-language change, and systemPreferredLanguage()
            // would read it back as if it were the system's own answer. A pin we
            // did not write is someone else's setting and stays.
            if let marker = defaults.string(forKey: pinMarkerKey) {
                if defaults.stringArray(forKey: appleLanguagesKey) == [marker] {
                    defaults.removeObject(forKey: appleLanguagesKey)
                }
                defaults.removeObject(forKey: pinMarkerKey)
            }
            return
        }
        let pinned = [selection]
        guard defaults.stringArray(forKey: appleLanguagesKey) != pinned
            || defaults.string(forKey: pinMarkerKey) != selection
        else { return }
        defaults.set(pinned, forKey: appleLanguagesKey)
        defaults.set(selection, forKey: pinMarkerKey)
    }

    static func tr(_ key: String, _ args: CVarArg...) -> String {
        let format = localizedBundle.localizedString(forKey: key, value: nil, table: nil)
        guard !args.isEmpty else { return format }
        return String(format: format, locale: formatLocale, arguments: args)
    }
}
