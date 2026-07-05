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
        switch resolvedAppLanguage {
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

    static func tr(_ key: String, _ args: CVarArg...) -> String {
        let format = localizedBundle.localizedString(forKey: key, value: nil, table: nil)
        guard !args.isEmpty else { return format }
        return String(format: format, locale: formatLocale, arguments: args)
    }
}
