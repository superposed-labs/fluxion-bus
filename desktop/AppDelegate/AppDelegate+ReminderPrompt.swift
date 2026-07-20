import AppKit
import Foundation
import UserNotifications

// MARK: - Reminder Coverage Prompt
//
// Weekly reset reminders are stored as per-provider AutoPing rules, and the
// welcome window only ever writes rules for the providers that existed at
// first run. An agent installed later therefore reports quota in the menu bar
// while silently having no reminder — the user believes reminders are "on"
// because they switched them on once.
//
// Rather than enabling it for them (some people deliberately watch a single
// provider), this offers a one-click prompt the first time a newly watchable
// provider shows up, and never asks about that provider again.
extension AppDelegate {

    static let reminderPromptCategoryId = "FLUXION_REMINDER_COVERAGE"
    static let reminderPromptEnableActionId = "FLUXION_REMINDER_ENABLE"
    /// Providers already offered a reminder, so a declined offer stays declined.
    /// App-local bookkeeping rather than a .env key: it is not configuration and
    /// does not belong in a file users hand-edit.
    static let reminderPromptedDefaultsKey = "FluxionReminderPromptedProviders"

    var reminderPromptedProviders: Set<String> {
        get {
            Set(UserDefaults.standard.stringArray(forKey: AppDelegate.reminderPromptedDefaultsKey) ?? [])
        }
        set {
            UserDefaults.standard.set(
                newValue.sorted(), forKey: AppDelegate.reminderPromptedDefaultsKey)
        }
    }

    /// Offers weekly reminders for providers that became watchable after the
    /// user had already asked for reminders elsewhere. Safe to call after any
    /// availability detection; it decides on its own whether to say anything.
    ///
    /// Runs its scheduler probe off the main thread — runAutoPingCommand()
    /// blocks on a subprocess.
    func promptForUnwatchedProvidersIfNeeded() {
        guard (envVals["FLUXION_MENU_MACOS_NOTIFY_REFRESH"] ?? "true").lowercased() != "false" else {
            return
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self = self else { return }
            guard let modes = self.runAutoPingCommand(["--get-autoping"]) else { return }

            // Only speak up for a user who already wanted reminders somewhere.
            // With none configured, silence is the setting they chose.
            guard modes.values.contains(where: { $0 != "off" }) else { return }

            guard let snapshot = self.loadAvailabilitySnapshot() else { return }
            let watchable = snapshot.usage
                .filter { $0.value.status == "ok" }
                .map { $0.key }
            let alreadyPrompted = self.reminderPromptedProviders
            let uncovered = watchable
                .filter { (modes[$0] ?? "off") == "off" }
                .filter { !alreadyPrompted.contains($0) }
                .sorted()
            guard !uncovered.isEmpty else { return }

            DispatchQueue.main.async {
                self.deliverReminderCoveragePrompt(for: uncovered)
            }
        }
    }

    private func deliverReminderCoveragePrompt(for providers: [String]) {
        // Asked once per provider whether or not the user acts on it: a prompt
        // they ignored is an answer too, and repeating it every launch would
        // be worse than the gap it reports.
        reminderPromptedProviders = reminderPromptedProviders.union(providers)

        let names = providers.map { PROVIDER_NAMES[$0] ?? $0.capitalized }
        let body = names.count == 1
            ? L10n.tr("notification.reminder_coverage.body", names[0])
            : L10n.tr("notification.reminder_coverage.body_many", names.joined(separator: ", "))

        deliverLocalNotification(
            title: L10n.tr("notification.reminder_coverage.title"),
            body: body,
            userInfo: ["reminder_providers": providers],
            categoryIdentifier: AppDelegate.reminderPromptCategoryId
        )
    }

    /// Turns on weekly reminders for the providers the prompt named. Called
    /// from the notification's action button.
    func enableWeeklyReminders(for providers: [String]) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            for provider in providers {
                _ = self.setAutoPingMode(provider: provider, mode: "7d")
            }
            self.loadAutoPingModes()
        }
    }
}
